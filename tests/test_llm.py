"""Llm: check, _price_cost, ask — только с фейками, без сети."""
import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart,
                                  UserPromptPart)

from brain import llm as llm_mod
from brain.config import Config
from brain.llm import Llm, NoApiKey

PRICES = {"data": [{"id": "a/b", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_urlopen(prices=PRICES, key_code=200, models_exc=None):
    calls = []

    def _open(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        calls.append(url)
        if url == llm_mod.PRICES_URL:
            if models_exc:
                raise models_exc
            return Resp(json.dumps(prices).encode())
        if url == llm_mod.KEY_URL:
            if key_code != 200:
                raise urllib.error.HTTPError(url, key_code, "x", {}, None)
            return Resp(b"{}")
        raise AssertionError(url)

    _open.calls = calls
    return _open


@pytest.fixture
def llm():
    return Llm(Config(model="a/b"))


def test_price_cost(llm, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen())
    assert llm._price_cost(1000, 500) == pytest.approx(0.002)


def test_price_cost_unknown_model(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen())
    assert Llm(Config(model="zzz"))._price_cost(1, 1) is None


def test_price_cost_no_network(llm, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(models_exc=OSError("net")))
    assert llm._price_cost(1, 1) is None


def test_check_all_ok(llm, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen())
    assert llm.check("a/b", "key") == {"model_ok": True, "key_ok": True}


def test_check_unknown_model_no_key(llm, monkeypatch):
    f = fake_urlopen()
    monkeypatch.setattr(urllib.request, "urlopen", f)
    assert llm.check("x/y", None) == {"model_ok": False, "key_ok": None}
    assert llm_mod.KEY_URL not in f.calls


@pytest.mark.parametrize("code,expected", [(401, False), (403, False), (500, None), (429, None)])
def test_check_key_codes(llm, monkeypatch, code, expected):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(key_code=code))
    assert llm.check("a/b", "key")["key_ok"] is expected


def test_check_offline(llm, monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert llm.check("a/b", "key") == {"model_ok": None, "key_ok": None}


def test_check_sends_bearer(llm, monkeypatch):
    seen = []

    def _open(req, timeout=None):
        if not isinstance(req, str) and req.full_url == llm_mod.KEY_URL:
            seen.append(req.get_header("Authorization"))
            return Resp(b"{}")
        return Resp(json.dumps(PRICES).encode())

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    llm.check("a/b", "sekret")
    assert seen == ["Bearer sekret"]


# ---------- ask с фейковым агентом ----------

class FakeResult:
    def __init__(self, cost):
        self.usage = SimpleNamespace(input_tokens=100, output_tokens=40)
        self.response = SimpleNamespace(model_name="a/b-served")
        self.output = "готово"
        pd = {"cost": cost} if cost is not None else None
        self._msgs = [ModelRequest(parts=[UserPromptPart(content="в")]),
                      ModelResponse(parts=[TextPart(content="готово")], provider_details=pd)]

    def new_messages(self):
        return self._msgs

    def new_messages_json(self):
        return ModelMessagesTypeAdapter.dump_json(self._msgs)


class FakeAgent:
    def __init__(self, cost):
        self.cost, self.calls = cost, []

    async def run(self, question, message_history=None, deps=None, event_stream_handler=None):
        self.calls.append((question, message_history))
        self.deps, self.handler = deps, event_stream_handler
        return FakeResult(self.cost)


async def test_ask_uses_cost_from_response(llm, monkeypatch):
    agent = FakeAgent(0.0123)
    monkeypatch.setattr(llm, "_get_agent", lambda: agent)
    a = await llm.ask("вопрос", [])
    assert (a.text, a.model, a.tokens_in, a.tokens_out, a.cost_usd) == ("готово", "a/b-served", 100, 40, 0.0123)
    assert agent.calls == [("вопрос", None)]
    json.loads(a.messages_json)


async def test_ask_falls_back_to_price(llm, monkeypatch):
    monkeypatch.setattr(llm, "_get_agent", lambda: FakeAgent(None))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen())
    a = await llm.ask("в", [])
    assert a.cost_usd == pytest.approx(100 * 1e-6 + 40 * 2e-6)


async def test_ask_cost_none_when_no_price(llm, monkeypatch):
    monkeypatch.setattr(llm, "_get_agent", lambda: FakeAgent(None))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(models_exc=OSError()))
    assert (await llm.ask("в", [])).cost_usd is None


async def test_ask_history_roundtrip(llm, monkeypatch):
    agent = FakeAgent(0.01)
    monkeypatch.setattr(llm, "_get_agent", lambda: agent)
    first = await llm.ask("один", [])
    await llm.ask("два", [first.messages_json])
    hist = agent.calls[1][1]
    assert len(hist) == 2 and isinstance(hist[1], ModelResponse)


async def test_ask_no_key(llm):
    with pytest.raises(NoApiKey):  # keystore подменён autouse-фикстурой, ключа нет
        await llm.ask("в", [])


def test_reset_drops_agent(llm):
    llm._agent = object()
    llm.reset()
    assert llm._agent is None
