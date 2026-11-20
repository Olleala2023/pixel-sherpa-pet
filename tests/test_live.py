"""Живое распознавание по кускам: brain/stt.py (Cutter, LiveSession), адреса /listen и клиент LiveUpload.
VAD и распознаватель поддельные: модель речи не грузим."""
import io
import threading
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from brain import stt as stt_mod
from brain.stt import SR, VAD_WINDOW, Cutter, LiveSession, Stt

SILENCE = int(0.8 * SR)


class FakeVad:
    """Отдаёт кусок (start, end), когда звука накопилось до end + паузы; остальное — по flush()."""

    def __init__(self, spans):
        self.spans, self.pos, self.ready = list(spans), 0, []
        self.windows = []

    def accept_waveform(self, x):
        self.windows.append(len(x))
        self.pos += len(x)
        while self.spans and self.pos >= self.spans[0][1] + SILENCE:
            self._emit(self.spans.pop(0))

    def _emit(self, span):
        a, b = span
        self.ready.append(SimpleNamespace(start=a, samples=np.zeros(b - a, dtype=np.float32)))

    def flush(self):
        while self.spans and self.spans[0][0] < self.pos:
            a, b = self.spans.pop(0)
            self._emit((a, min(b, self.pos)))

    def empty(self):
        return not self.ready

    @property
    def front(self):
        return self.ready[0]

    def pop(self):
        self.ready.pop(0)


SPANS = [(SR * 1, SR * 3), (SR * 5, SR * 8), (SR * 10, SR * 12)]  # три фразы в 13-секундной записи


@pytest.fixture
def fake_stt(cfg, monkeypatch):
    s = Stt(cfg)
    decoded = []
    monkeypatch.setattr(s, "make_vad", lambda: FakeVad(SPANS))
    monkeypatch.setattr(s, "ensure_loaded", lambda: None)

    def decode(seg):
        decoded.append(len(seg))
        return f"фраза{len(decoded)}"

    monkeypatch.setattr(s, "decode", decode)
    s.decoded = decoded
    return s


def audio(sec=13.0):
    return np.zeros(int(sec * SR), dtype=np.float32)


# ---------- Cutter ----------

def test_cutter_feeds_whole_windows_in_any_portions():
    vad = FakeVad([])
    c = Cutter(vad)
    for n in (100, 700, 1500, 3):  # порции не кратны окну
        c.feed(np.zeros(n, dtype=np.float32))
    assert all(w == VAD_WINDOW for w in vad.windows) and sum(vad.windows) + len(c.tail) == 2303
    c.flush()
    assert sum(vad.windows) == 2303 and len(c.tail) == 0


# ---------- LiveSession ----------

def test_live_texts_appear_after_each_pause(fake_stt):
    s = LiveSession(fake_stt)
    x, step, seen = audio(), int(0.4 * SR), []
    for i in range(0, len(x), step):
        if s.accept(x[i : i + step]):
            seen.append(round((i + step) / SR, 1))
    # фраза кончилась на 3, 8 и 12 с; текст — после паузы 0,8 с с точностью до порции 0,4 с
    assert seen == [4.0, 8.8, 12.8]
    r, new = s.finish()
    assert r.text == "фраза1 фраза2 фраза3" and r.segments == 3 and new == [] and r.method == "vad-live"


def test_live_matches_offline_transcribe(fake_stt):
    """Итог живой сессии = transcribe() той же записи: те же куски с теми же полями."""
    x = audio()
    s = LiveSession(fake_stt)
    for i in range(0, len(x), 6000):
        s.accept(x[i : i + 6000])
    live_r, _ = s.finish()
    live_lens = list(fake_stt.decoded)
    fake_stt.decoded.clear()
    off = fake_stt.transcribe(x)
    assert off.text == live_r.text and fake_stt.decoded == live_lens
    pad = stt_mod.SEG_PAD
    assert live_lens[0] == (SPANS[0][1] - SPANS[0][0]) + 2 * pad  # поля 0,25 с с двух сторон


def test_live_last_phrase_comes_with_finish(fake_stt):
    s = LiveSession(fake_stt)
    s.accept(audio(12.2))  # остановили сразу после третьей фразы — паузы после неё нет
    assert s.text == "фраза1 фраза2"
    r, new = s.finish()
    assert new == ["фраза3"] and r.text.endswith("фраза3")


def test_live_without_vad_falls_back_to_transcribe(cfg, monkeypatch):
    s = Stt(cfg)
    monkeypatch.setattr(s, "make_vad", lambda: None)
    monkeypatch.setattr(s, "transcribe", lambda x: stt_mod.SttResult("целиком", len(x) / SR, 5, 1, "quiet-split"))
    live = LiveSession(s)
    assert live.accept(audio(2)) == []
    r, new = live.finish()
    assert r.text == "целиком" and new == ["целиком"]


def test_live_silence_only(cfg, monkeypatch):
    s = Stt(cfg)
    monkeypatch.setattr(s, "make_vad", lambda: FakeVad([]))
    live = LiveSession(s)
    live.accept(audio(3))
    r, new = live.finish()
    assert r.text == "" and r.segments == 0 and r.method == "empty"


# ---------- адреса /listen ----------

@pytest.fixture
def live_client(client, monkeypatch, tmp_path):
    st = client.brain.stt
    monkeypatch.setattr(st, "make_vad", lambda: FakeVad(SPANS))
    loads = []
    monkeypatch.setattr(st, "ensure_loaded", lambda: loads.append(1))
    monkeypatch.setattr(st, "decode", lambda seg: f"кусок{len(seg)}")
    for p in (client.cfg.gigaam_model, client.cfg.gigaam_tokens):  # «модель есть»
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    client.loads = loads
    return client


def pcm16(x):
    return (x * 32767).astype(np.int16).tobytes()


def test_listen_flow(live_client, paths):
    c = live_client
    lid = c.post("/listen").json()["live_id"]
    time.sleep(0.1)
    assert c.loads  # модель начали грузить сразу, ещё до первой фразы
    x = audio()
    texts = []
    for i in range(0, int(12.2 * SR), int(0.4 * SR)):
        r = c.post(f"/listen/{lid}", content=pcm16(x[i : i + int(0.4 * SR)])).json()
        if r["new"]:
            texts.append(r["text"])
    assert len(texts) == 2 and texts[1].count("кусок") == 2
    done = c.post(f"/listen/{lid}/finish", content=pcm16(x[int(12.4 * SR):])).json()
    assert done["text"].count("кусок") == 3 and len(done["new"]) == 1 and done["audio_sec"] == pytest.approx(13.0, abs=0.01)
    wav = paths.audio / f"{done['audio_id']}.wav"
    with wave.open(str(wav)) as w:
        assert w.getframerate() == SR and w.getnframes() == pytest.approx(13 * SR, abs=2)
    assert c.post(f"/listen/{lid}", content=b"\0\0").status_code == 404  # сессия закрыта


def test_listen_unknown_session(live_client):
    r = live_client.post("/listen/нет", content=b"\0\0")
    assert r.status_code == 404 and r.json()["error"] == "no_live"


def test_listen_odd_bytes(live_client):
    lid = live_client.post("/listen").json()["live_id"]
    assert live_client.post(f"/listen/{lid}", content=b"\0\0\0").json()["error"] == "bad_audio"


def test_listen_too_long(live_client):
    c = live_client
    c.cfg.max_audio_s = 1
    lid = c.post("/listen").json()["live_id"]
    r = c.post(f"/listen/{lid}", content=pcm16(audio(2)))
    assert r.status_code == 413 and r.json()["error"] == "audio_too_long"


def test_listen_no_model(client):
    r = client.post("/listen")
    assert r.status_code == 503 and r.json()["error"] == "no_model"


def test_abandoned_session_expires(live_client, monkeypatch):
    from brain import server

    lid = live_client.post("/listen").json()["live_id"]
    live_client.brain.live[lid].last_used -= server.LIVE_TTL_S + 1
    time.sleep(2.3)  # housekeeping раз в 2 с
    assert lid not in live_client.brain.live


# ---------- клиент пса: LiveUpload ----------

class FakeRec:
    def __init__(self):
        self.chunks = []


class FakeClient:
    def __init__(self, start_error=None):
        self.sent, self.calls, self.start_error = [], [], start_error

    def listen_start(self):
        if self.start_error:
            raise self.start_error
        return "L1"

    def listen_chunk(self, lid, pcm):
        self.sent.append(pcm)
        n = len(self.sent)
        return {"new": ["т"] if n == 2 else [], "text": f"текст{n}"}

    def listen_finish(self, lid, pcm):
        self.calls.append(("finish", pcm))
        return {"text": "итог", "audio_id": "a"}

    def transcribe(self, wav):
        self.calls.append(("transcribe", len(wav)))
        return {"text": "целиком"}


def make_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue()


def test_live_upload_sends_new_audio_and_rest(monkeypatch):
    from sherpa import voice

    monkeypatch.setattr(voice.LiveUpload, "SEND_S", 0.05)
    rec, cl, texts = FakeRec(), FakeClient(), []
    up = voice.LiveUpload(cl, rec, texts.append)
    for k in range(3):
        rec.chunks.append(bytes([k]) * 100)
        time.sleep(0.15)
    pcm = b"".join(rec.chunks) + b"\x09" * 50  # хвост, который не успели отправить
    r = up.finish(make_wav(pcm))
    assert b"".join(cl.sent) + cl.calls[-1][1] == pcm  # всё ушло ровно один раз, по порядку
    assert r["text"] == "итог" and texts == ["текст2"]


def test_live_upload_old_brain_falls_back(monkeypatch):
    from sherpa import voice

    cl = FakeClient(start_error=voice.BrainError("http", "Мозг ответил HTTP 404"))
    up = voice.LiveUpload(cl, FakeRec(), lambda t: None)
    r = up.finish(make_wav(b"\0\0" * 10))
    assert r["text"] == "целиком" and cl.calls[0][0] == "transcribe"


def test_live_upload_lost_session_falls_back(monkeypatch):
    from sherpa import voice

    class Lost(FakeClient):
        def listen_finish(self, lid, pcm):
            raise voice.BrainError("no_live", "потерялась")

    cl = Lost()
    r = voice.LiveUpload(cl, FakeRec(), lambda t: None).finish(make_wav(b"\0\0"))
    assert r["text"] == "целиком"
