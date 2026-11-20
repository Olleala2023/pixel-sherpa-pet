"""Общие фикстуры. Боевые brain.db, audio/, config.toml и диспетчер учётных данных Windows не трогаем."""
import io
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import brain.config as bconf
import brain.keystore as keystore
import brain.server as server
from brain.config import DEFAULT_TOML, load_config
from brain.llm import Answer, Llm


class FakeKeyring:
    """Диспетчер учётных данных в памяти."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        if (service, name) not in self.store:
            raise KeyError(name)
        del self.store[(service, name)]


@pytest.fixture(autouse=True)
def fake_keystore(monkeypatch):
    """Всегда: фейковый keyring и без переменной окружения."""
    kr = FakeKeyring()
    monkeypatch.setattr(keystore, "_kr", lambda: kr)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return kr


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Подмена всех путей данных на tmp_path (до load_config / create_app)."""
    data = tmp_path / "data"
    audio = data / "audio"
    audio.mkdir(parents=True)
    cfg_path = tmp_path / "config.toml"
    for mod in (bconf, server):
        monkeypatch.setattr(mod, "AUDIO_DIR", audio)
    monkeypatch.setattr(bconf, "DATA_DIR", data)
    monkeypatch.setattr(bconf, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(server, "DB_PATH", data / "brain.db")
    # save_settings без пути берёт bconf.CONFIG_PATH в момент вызова — подмены выше достаточно
    return type("P", (), {"data": data, "audio": audio, "config": cfg_path, "root": tmp_path})


@pytest.fixture
def cfg(paths):
    c = load_config(paths.config)
    c.brain_idle_exit_s = 0
    # модель распознавания: несуществующие файлы
    c.gigaam_model = paths.root / "no" / "model.onnx"
    c.gigaam_tokens = paths.root / "no" / "tokens.txt"
    return c


def make_answer(text="ответ", cost=0.01, messages_json='["m"]', model="m/x"):
    return Answer(text=text, model=model, tokens_in=10, tokens_out=5, cost_usd=cost, llm_ms=7,
                  messages_json=messages_json)


@pytest.fixture
def llm_calls(monkeypatch):
    """Фейковый Llm.ask / Llm.check; в списке — вызовы. Поведение задаётся через state."""
    state = {"ask_calls": [], "check_calls": [], "ask_exc": None,
             "check_res": {"model_ok": True, "key_ok": True}, "answer": make_answer()}

    async def fake_ask(self, question, history_jsons, on_delta=None):
        state["ask_calls"].append((question, list(history_jsons)))
        if on_delta:  # поток: ответ двумя кусками (до ошибки — тоже, как у настоящей нейронки)
            text = state["answer"].text
            on_delta(text[: len(text) // 2])
            on_delta(text[len(text) // 2 :])
        if state["ask_exc"]:
            raise state["ask_exc"]
        return state["answer"]

    def fake_check(self, model, key):
        state["check_calls"].append((model, key))
        return dict(state["check_res"])

    monkeypatch.setattr(Llm, "ask", fake_ask)
    monkeypatch.setattr(Llm, "check", fake_check)
    return state


@pytest.fixture
def client(cfg, llm_calls):
    app = server.create_app(cfg)
    with TestClient(app) as c:
        c.brain = app.state.brain
        c.cfg = cfg
        yield c


def make_wav(seconds=0.1, sr=16000, ch=1, sw=2, value=1000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(sr)
        n = int(seconds * sr)
        if sw == 2:
            w.writeframes(np.full(n * ch, value, dtype=np.int16).tobytes())
        else:
            w.writeframes(b"\x80" * (n * ch * sw))
    return buf.getvalue()
