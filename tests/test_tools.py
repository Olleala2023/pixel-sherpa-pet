"""Инструменты нейронки (brain/tools.py) и то, как llm.ask отдаёт напоминания и поток текста."""
import json
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta, ToolCallPart

from brain import tools
from brain.config import _toml_str, load_config, save_settings


# ---------- calc ----------

@pytest.mark.parametrize("expr,expected", [
    ("2+2*2", "6"), ("(1250*0.13)", "162.5"), ("10/4", "2.5"), ("2^10", "1024"), ("sqrt(16)", "4"),
    ("1,5*2", "3"), ("-3+abs(-5)", "2"), ("round(pi, 2)", "3.14"), ("7//2", "3"), ("7 % 3", "1"),
])
def test_calc_ok(expr, expected):
    assert tools.calc(expr) == expected


@pytest.mark.parametrize("expr", [
    "__import__('os')", "open('x')", "a+1", "1/0", "2**100000", "().__class__", "lambda: 1", "sqrt(x=4)",
])
def test_calc_refuses(expr):
    assert tools.calc(expr).startswith("Ошибка")


def test_now_has_weekday():
    out = tools.now()
    assert any(d in out for d in tools.WEEKDAYS) and "." in out and ":" in out


# ---------- напоминание ----------

def test_set_reminder_adds_action():
    deps = tools.Deps()
    out = tools.set_reminder(SimpleNamespace(deps=deps), 1.5, "  чайник  ")
    assert out.startswith("Напоминание поставлено")
    (a,) = deps.actions
    assert a["type"] == "reminder" and a["in_s"] == 90 and a["text"] == "чайник" and "T" in a["at"]


@pytest.mark.parametrize("minutes", [0, -5, 24 * 60 + 1])
def test_set_reminder_bad_range(minutes):
    deps = tools.Deps()
    assert tools.set_reminder(SimpleNamespace(deps=deps), minutes, "x").startswith("Ошибка")
    assert deps.actions == []


# ---------- буфер обмена (настоящий буфер не читаем) ----------

def test_clipboard_empty(monkeypatch):
    monkeypatch.setattr(tools, "_clipboard_text", lambda: None)
    assert "пуст" in tools.read_clipboard()


def test_clipboard_long_is_cut(monkeypatch):
    monkeypatch.setattr(tools, "_clipboard_text", lambda: "я" * 5000)
    out = tools.read_clipboard()
    assert out.startswith("я" * tools.CLIP_MAX) and "обрезано" in out and "5000" in out


def test_tools_registered():
    names = {t.name for t in tools.TOOLS}
    assert names == {"now", "calc", "set_reminder", "read_clipboard"}


# ---------- llm.ask: поток и напоминания ----------

class _Usage:
    input_tokens, output_tokens = 3, 4


class _Result:
    usage = _Usage()
    output = "Привет, мир"
    response = SimpleNamespace(model_name="m/x")

    def new_messages(self):
        return []

    def new_messages_json(self):
        return b"[]"


class StreamingAgent:
    """Отдаёт события как настоящий агент: начало текста, куски, вызов инструмента (его в поток не пускаем)."""

    async def run(self, question, message_history=None, deps=None, event_stream_handler=None):
        deps.actions.append({"type": "reminder", "in_s": 60, "text": "x"})
        if event_stream_handler:
            async def events():
                yield PartStartEvent(index=0, part=ToolCallPart(tool_name="now", args="{}"))
                yield PartStartEvent(index=0, part=TextPart(content="Привет"))
                yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=", мир"))
            await event_stream_handler(None, events())
        return _Result()


@pytest.fixture
def llm(cfg, monkeypatch):
    from brain.llm import Llm

    m = Llm(cfg)
    monkeypatch.setattr(m, "_get_agent", lambda: StreamingAgent())
    monkeypatch.setattr(m, "_price_cost", lambda a, b: None)
    return m


async def test_ask_streams_text_only(llm):
    got = []
    a = await llm.ask("q", [], on_delta=got.append)
    assert got == ["Привет", ", мир"] and a.text == "Привет, мир"
    assert a.actions == [{"type": "reminder", "in_s": 60, "text": "x"}]


async def test_ask_without_stream(llm):
    a = await llm.ask("q", [])
    assert a.text == "Привет, мир" and len(a.actions) == 1


# ---------- характер (system_prompt) в config.toml ----------

@pytest.mark.parametrize("v", ['обычный', 'с "кавычками"', "две\nстроки", "путь C:\\x\tтаб", "юникод ✓"])
def test_toml_str_roundtrip(v):
    import tomllib

    assert tomllib.loads("x = " + _toml_str(v))["x"] == v


def test_save_prompt_roundtrip(paths):
    load_config(paths.config)
    save_settings("openrouter", "a/b", paths.config, system_prompt='Ты "кот".\nМяукай.')
    c = load_config(paths.config)
    assert c.system_prompt == 'Ты "кот".\nМяукай.' and c.model == "a/b"
    assert c.gigaam_model.name == "model.int8.onnx"  # секции не задеты


def test_save_prompt_replaces_multiline(paths):
    paths.config.write_text('model = "a/b"\nsystem_prompt = """\nстарый\nпромпт\n"""\nport = 9000\n\n[vad]\nthreshold = 0.5\n',
                            encoding="utf-8")
    save_settings("openrouter", "a/b", paths.config, system_prompt="новый")
    c = load_config(paths.config)
    assert c.system_prompt == "новый" and c.port == 9000 and c.vad.threshold == 0.5


def test_prompt_unchanged_when_none(paths):
    load_config(paths.config)
    before = load_config(paths.config).system_prompt
    save_settings("openrouter", "x/y", paths.config)
    assert load_config(paths.config).system_prompt == before


def test_default_config_has_tools(paths):
    assert load_config(paths.config).tools is True
    assert "tools = true" in paths.config.read_text(encoding="utf-8")


# ---------- сервер: /ask/stream, /shutdown, характер в /settings ----------

def stream_lines(r):
    return [json.loads(x) for x in r.text.splitlines() if x.strip()]


def test_ask_stream_ok(client, llm_calls):
    from tests.conftest import make_answer

    llm_calls["answer"] = make_answer(text="абвгд")
    r = client.post("/ask/stream", json={"question": "q", "session_id": "s"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    lines = stream_lines(r)
    assert "".join(x["delta"] for x in lines if "delta" in x) == "абвгд"
    last = lines[-1]
    assert last["done"] is True and last["answer"] == "абвгд" and last["session_id"] == "s" and last["turn"] == 1
    assert last["actions"] == []
    assert client.get("/history").json()[0]["status"] == "ok"


def test_ask_stream_error_line(client, llm_calls):
    llm_calls["ask_exc"] = RuntimeError("бум")
    lines = stream_lines(client.post("/ask/stream", json={"question": "q"}))
    assert lines[-1]["error"] == "llm_failed" and "done" not in lines[-1]


def test_ask_stream_empty_question(client, llm_calls):
    lines = stream_lines(client.post("/ask/stream", json={"question": "  "}))
    assert lines == [{"error": "empty_question", "message": "Вопрос пустой."}]


def test_ask_returns_actions(client, llm_calls):
    from tests.conftest import make_answer

    a = make_answer()
    a.actions = [{"type": "reminder", "in_s": 60, "text": "чай"}]
    llm_calls["answer"] = a
    assert client.post("/ask", json={"question": "q"}).json()["actions"] == a.actions


def test_shutdown_calls_exit(client):
    called = []
    client.brain.should_exit = lambda: called.append(1)
    r = client.post("/shutdown")
    assert r.status_code == 200 and r.json()["ok"] is True
    import time

    time.sleep(0.4)
    client.get("/health")  # дать циклу отработать call_later
    assert called == [1]


def test_settings_shows_prompt(client):
    s = client.get("/settings").json()
    assert s["system_prompt"] == client.cfg.system_prompt and s["default_system_prompt"]


def test_settings_saves_prompt(client, paths, llm_calls):
    r = client.post("/settings", json={"model": "a/b", "system_prompt": "  Ты кот.  "})
    assert r.status_code == 200 and r.json()["system_prompt"] == "Ты кот."
    assert load_config(paths.config).system_prompt == "Ты кот."


def test_settings_empty_prompt_restores_default(client, paths, llm_calls):
    from brain.config import DEFAULT_SYSTEM_PROMPT

    client.post("/settings", json={"model": "a/b", "system_prompt": "Ты кот."})
    client.post("/settings", json={"model": "a/b", "system_prompt": ""})
    assert load_config(paths.config).system_prompt == DEFAULT_SYSTEM_PROMPT


def test_settings_prompt_too_long(client, llm_calls):
    r = client.post("/settings", json={"model": "a/b", "system_prompt": "я" * 4001})
    assert r.status_code == 400 and r.json()["error"] == "bad_prompt"
