"""Сервер мозга через TestClient (без uvicorn, без сети, без модели распознавания)."""
from datetime import datetime

import pytest

import brain.keystore as keystore
from brain import server
from brain.llm import NoApiKey

from conftest import make_answer, make_wav


def today_early() -> str:
    return datetime.now().replace(hour=0, minute=0, second=1, microsecond=0).isoformat(timespec="seconds")


def rows(client):
    return client.brain.db.history(100)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["stt_loaded"] is False and j["model"] == client.cfg.model and "pid" in j


# ---------- /ask ----------

@pytest.mark.parametrize("q", ["", "   \n"])
def test_ask_empty_400(client, q):
    r = client.post("/ask", json={"question": q})
    assert r.status_code == 400 and r.json()["error"] == "empty_question"
    assert rows(client) == []


def test_ask_ok_writes_row(client, llm_calls):
    r = client.post("/ask", json={"question": "  привет  ", "transcript_raw": "привет ", "audio_sec": 1.5})
    assert r.status_code == 200
    j = r.json()
    assert j["turn"] == 1 and j["session_id"] and j["answer"] == "ответ" and j["cost_usd"] == 0.01
    row = rows(client)[0]
    assert row["status"] == "ok" and row["question"] == "привет" and row["transcript_raw"] == "привет "
    assert row["answer"] == "ответ" and row["session_id"] == j["session_id"] and row["audio_sec"] == 1.5
    assert llm_calls["ask_calls"] == [("привет", [])]


def test_ask_second_turn_passes_history(client, llm_calls):
    sid = client.post("/ask", json={"question": "один"}).json()["session_id"]
    llm_calls["answer"] = make_answer(messages_json='["второй"]')
    j = client.post("/ask", json={"question": "два", "session_id": sid}).json()
    assert j["turn"] == 2 and j["session_id"] == sid
    assert llm_calls["ask_calls"][1] == ("два", ['["m"]'])
    j = client.post("/ask", json={"question": "три", "session_id": sid}).json()
    assert j["turn"] == 3
    assert llm_calls["ask_calls"][2][1] == ['["m"]', '["второй"]']


def test_ask_history_trimmed_to_max_pairs(client, llm_calls):
    client.cfg.max_dialog_pairs = 2
    for i in range(4):
        llm_calls["answer"] = make_answer(messages_json=f'["m{i}"]')
        client.post("/ask", json={"question": f"q{i}", "session_id": "sess"})
    assert llm_calls["ask_calls"][3][1] == ['["m1"]', '["m2"]']


def test_ask_links_existing_audio(client, paths):
    (paths.audio / "a1.wav").write_bytes(b"x")
    client.post("/ask", json={"question": "q", "audio_id": "a1"})
    client.post("/ask", json={"question": "q", "audio_id": "missing"})
    files = sorted((x["audio_file"] or "") for x in rows(client))
    assert files == ["", "a1.wav"]


def test_daily_limit_429(client, llm_calls):
    client.brain.db.insert(session_id="old", turn=1, question="q", status="ok", cost_usd=1.0)
    r = client.post("/ask", json={"question": "дорого", "session_id": "new"})
    assert r.status_code == 429 and r.json()["error"] == "daily_limit"
    assert llm_calls["ask_calls"] == []
    row = [x for x in rows(client) if x["session_id"] == "new"][0]
    assert row["status"] == "limit" and row["answer"] is None


def test_daily_limit_ignores_yesterday(client):
    client.brain.db.insert(session_id="old", turn=1, question="q", status="ok", cost_usd=5.0,
                           created_at="2000-01-01T10:00:00")
    assert client.post("/ask", json={"question": "q"}).status_code == 200


def test_no_api_key_500(client, llm_calls):
    llm_calls["ask_exc"] = NoApiKey()
    r = client.post("/ask", json={"question": "q"})
    assert r.status_code == 500 and r.json()["error"] == "no_api_key"
    row = rows(client)[0]
    assert row["status"] == "error" and row["error"] == "no_api_key"


def test_llm_exception_502(client, llm_calls):
    llm_calls["ask_exc"] = RuntimeError("бум")
    r = client.post("/ask", json={"question": "q"})
    assert r.status_code == 502 and r.json()["error"] == "llm_failed"
    row = rows(client)[0]
    assert row["status"] == "error" and "RuntimeError" in row["error"] and "бум" in row["error"]


def test_failed_turn_not_in_history(client, llm_calls):
    llm_calls["ask_exc"] = RuntimeError("x")
    client.post("/ask", json={"question": "q", "session_id": "s"})
    llm_calls["ask_exc"] = None
    assert client.post("/ask", json={"question": "q2", "session_id": "s"}).json()["turn"] == 2
    assert llm_calls["ask_calls"][-1][1] == []


def test_llm_timeout_message(client, llm_calls):
    class ConnectTimeout(Exception):
        pass

    llm_calls["ask_exc"] = ConnectTimeout()
    r = client.post("/ask", json={"question": "q"})
    assert r.status_code == 502 and "связи" in r.json()["message"]


def test_llm_bad_key_points_to_settings(client, llm_calls):
    """Ключ не принят — подсказка ведёт в окно настроек, а не к переменной окружения."""
    llm_calls["ask_exc"] = RuntimeError("status_code: 401, Unauthorized")
    msg = client.post("/ask", json={"question": "q"}).json()["message"]
    assert "настройк" in msg and "OPENROUTER_API_KEY" not in msg


# ---------- /transcribe ----------

def test_transcribe_bad_wav_400(client):
    r = client.post("/transcribe", content=b"not a wav")
    assert r.status_code == 400 and r.json()["error"] == "bad_audio"


def test_transcribe_empty_body_400(client):
    assert client.post("/transcribe", content=b"").status_code == 400


def test_transcribe_8bit_400(client):
    r = client.post("/transcribe", content=make_wav(sw=1))
    assert r.status_code == 400 and r.json()["error"] == "bad_audio"


def test_transcribe_too_long_413(client):
    client.cfg.max_audio_s = 1
    r = client.post("/transcribe", content=make_wav(seconds=2))
    assert r.status_code == 413 and r.json()["error"] == "audio_too_long"


def test_transcribe_body_over_4mb_413(client):
    r = client.post("/transcribe", content=b"\0" * (server.MAX_BODY + 1))
    assert r.status_code == 413


def test_transcribe_no_model_503(client, paths):
    r = client.post("/transcribe", content=make_wav())
    assert r.status_code == 503 and r.json()["error"] == "no_model"
    assert list(paths.audio.glob("*.wav")) == []  # аудио не сохранилось


def _fake_stt(client, monkeypatch, tmp):
    from brain.stt import SttResult

    client.cfg.gigaam_model = tmp / "m.onnx"
    client.cfg.gigaam_tokens = tmp / "t.txt"
    client.cfg.gigaam_model.write_bytes(b"")
    client.cfg.gigaam_tokens.write_bytes(b"")
    monkeypatch.setattr(client.brain.stt, "transcribe",
                        lambda x: SttResult("привет мир", len(x) / 16000, 5, 1, "vad"))


def test_transcribe_ok_saves_audio(client, paths, monkeypatch):
    _fake_stt(client, monkeypatch, paths.root)
    r = client.post("/transcribe", content=make_wav(seconds=0.5))
    assert r.status_code == 200
    j = r.json()
    assert j["text"] == "привет мир" and j["audio_sec"] == 0.5 and j["method"] == "vad"
    assert (paths.audio / f"{j['audio_id']}.wav").exists()


def test_transcribe_stt_failure_500(client, paths, monkeypatch):
    _fake_stt(client, monkeypatch, paths.root)

    def boom(x):
        raise RuntimeError("упала")

    monkeypatch.setattr(client.brain.stt, "transcribe", boom)
    r = client.post("/transcribe", content=make_wav())
    assert r.status_code == 500 and r.json()["error"] == "stt_failed"


def test_transcribe_truncated_wav_is_400(client):
    """Обрезанный посреди сэмпла файл: ждём понятную 400, а не падение."""
    r = client.post("/transcribe", content=make_wav(seconds=0.1)[:-1])
    assert r.status_code == 400


# ---------- rotate_audio ----------

def test_rotate_audio_keeps_last(client, paths):
    client.cfg.audio_keep = 3
    for i in range(6):
        name = f"2026010{i}-000000-aaaa.wav"
        (paths.audio / name).write_bytes(b"x")
        client.brain.db.insert(session_id="s", turn=i + 1, question="q", status="ok", audio_file=name)
    client.brain.rotate_audio()
    left = sorted(p.name for p in paths.audio.glob("*.wav"))
    assert left == [f"2026010{i}-000000-aaaa.wav" for i in (3, 4, 5)]
    refs = {r["turn"]: r["audio_file"] for r in rows(client)}
    assert [refs[t] for t in (1, 2, 3)] == [None, None, None] and refs[6] is not None


def test_rotate_audio_fewer_than_keep(client, paths):
    (paths.audio / "a.wav").write_bytes(b"x")
    client.brain.rotate_audio()
    assert (paths.audio / "a.wav").exists()


# ---------- /settings ----------

def test_get_settings_masks_key(client, fake_keystore):
    keystore.set_key("openrouter", "sk-or-v1-abcdefghijklmnop1234")
    r = client.get("/settings")
    j = r.json()
    assert j["key_hint"] == "…1234" and j["key_source"] == "windows"
    assert "abcdefghijkl" not in r.text and "sk-or" not in r.text
    assert j["model"] == client.cfg.model and "openrouter" in j["providers"]


def test_get_settings_short_key_and_no_key(client):
    assert client.get("/settings").json()["key_hint"] is None
    keystore.set_key("openrouter", "short")
    r = client.get("/settings")
    assert r.json()["key_hint"] == "•••" and "short" not in r.text


def test_get_settings_env_key(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "envkey-envkey-envkey-9999")
    j = client.get("/settings").json()
    assert j["key_source"] == "env" and j["key_hint"] == "…9999"


def test_post_settings_bad_provider(client):
    r = client.post("/settings", json={"provider": "nope", "model": "a/b"})
    assert r.status_code == 400 and r.json()["error"] == "bad_provider"


def test_post_settings_empty_model(client):
    r = client.post("/settings", json={"model": "  "})
    assert r.status_code == 400 and r.json()["error"] == "bad_model"


def test_post_settings_bad_key(client, llm_calls, fake_keystore, paths):
    llm_calls["check_res"] = {"model_ok": True, "key_ok": False}
    r = client.post("/settings", json={"model": "a/b", "api_key": "k" * 20})
    assert r.status_code == 400 and r.json()["error"] == "bad_key"
    assert fake_keystore.store == {}
    assert "a/b" not in paths.config.read_text(encoding="utf-8")


def test_post_settings_bad_model(client, llm_calls, fake_keystore):
    llm_calls["check_res"] = {"model_ok": False, "key_ok": True}
    r = client.post("/settings", json={"model": "no/such", "api_key": "k" * 20})
    assert r.status_code == 400 and r.json()["error"] == "bad_model"
    assert fake_keystore.store == {} and client.cfg.model != "no/such"


def test_post_settings_ok(client, llm_calls, fake_keystore, paths):
    key = "sk-or-v1-0123456789abcdef7777"
    r = client.post("/settings", json={"model": " vendor/model ", "api_key": f" {key} "})
    assert r.status_code == 200
    j = r.json()
    assert j["model"] == "vendor/model" and j["key_hint"] == "…7777" and j["checked"]["key_ok"] is True
    assert key not in r.text
    assert fake_keystore.store[("Sherpa brain", "openrouter")] == key
    assert llm_calls["check_calls"] == [("vendor/model", key)]
    assert 'model = "vendor/model"' in paths.config.read_text(encoding="utf-8")
    assert 'provider = "openrouter"' in paths.config.read_text(encoding="utf-8")
    assert client.cfg.model == "vendor/model"
    assert client.get("/health").json()["model"] == "vendor/model"


def test_post_settings_resets_llm_agent(client):
    client.brain.llm._agent = object()
    client.post("/settings", json={"model": "a/b"})
    assert client.brain.llm._agent is None


def test_post_settings_without_key_keeps_old_and_checks_it(client, llm_calls, fake_keystore):
    keystore.set_key("openrouter", "old-key-old-key-1")
    client.post("/settings", json={"model": "a/b"})
    assert llm_calls["check_calls"] == [("a/b", "old-key-old-key-1")]
    assert fake_keystore.store[("Sherpa brain", "openrouter")] == "old-key-old-key-1"


def test_post_settings_clear_key(client, fake_keystore, llm_calls):
    keystore.set_key("openrouter", "old-key-old-key-1")
    r = client.post("/settings", json={"model": "a/b", "clear_key": True})
    assert r.status_code == 200 and fake_keystore.store == {}
    assert llm_calls["check_calls"] == [("a/b", None)]


def test_settings_check_does_not_save(client, fake_keystore, paths):
    before = paths.config.read_text(encoding="utf-8")
    r = client.post("/settings/check", json={"model": "x/y", "api_key": "k" * 20})
    assert r.status_code == 200
    assert r.json() == {"model_ok": True, "key_ok": True, "has_key": True}
    assert fake_keystore.store == {} and paths.config.read_text(encoding="utf-8") == before
    assert client.post("/settings/check", json={"model": "x/y"}).json()["has_key"] is False


# ---------- /history, /stats ----------

def test_history_limit_clamped(client):
    for i in range(3):
        client.post("/ask", json={"question": f"q{i}"})
    assert len(client.get("/history?limit=2").json()) == 2
    assert len(client.get("/history?limit=0").json()) == 1
    assert len(client.get("/history").json()) == 3
    assert "messages_json" not in client.get("/history").json()[0]


def test_stats_counts_today(client):
    client.post("/ask", json={"question": "q"})
    j = client.get("/stats").json()
    assert j["today"]["requests"] == 1 and j["today"]["limit_usd"] == client.cfg.daily_limit_usd
    assert j["all"]["requests"] == 1 and j["week"]["requests"] == 1 and j["days30"]["requests"] == 1
    assert j["reset_at"] is None and j["model"] == client.cfg.model


def test_stats_reset_keeps_history_and_daily_limit(client, llm_calls):
    client.cfg.daily_limit_usd = 0.5
    client.brain.db.insert(session_id="a", turn=1, question="q", status="ok", cost_usd=0.6, tokens_in=1,
                           tokens_out=1, created_at=today_early())
    assert client.get("/stats").json()["today"]["requests"] == 1
    j = client.post("/stats/reset").json()
    assert j["reset_at"] and j["today"]["requests"] == 0 and j["all"]["requests"] == 0
    assert j["today"]["cost_usd"] == 0 and j["week"]["requests"] == 0 and j["days30"]["requests"] == 0
    assert len(client.get("/history").json()) == 1  # история осталась
    # дневной лимит по-прежнему видит реальные траты за сегодня
    r = client.post("/ask", json={"question": "ещё"})
    assert r.status_code == 429 and r.json()["error"] == "daily_limit"
    assert llm_calls["ask_calls"] == []


def test_stats_after_reset_counts_new(client):
    client.brain.db.set_meta("stats_reset_at", "2000-01-01T00:00:00")
    client.post("/ask", json={"question": "q"})
    assert client.get("/stats").json()["all"]["requests"] == 1
