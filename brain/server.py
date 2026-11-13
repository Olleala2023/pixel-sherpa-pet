"""HTTP-сервис мозга на 127.0.0.1. Протокол — docs/голосовая-справка-ТЗ.md."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import llm as llm_mod
from . import stt as stt_mod
from .config import AUDIO_DIR, DB_PATH, Config
from .db import Db

log = logging.getLogger("brain.server")
MAX_BODY = 4 * 1024 * 1024


class AskIn(BaseModel):
    session_id: str | None = None
    question: str
    transcript_raw: str | None = None
    audio_id: str | None = None
    audio_sec: float | None = None
    stt_ms: int | None = None
    source: str = "pet"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


class Brain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = Db(DB_PATH)
        self.stt = stt_mod.Stt(cfg)
        self.llm = llm_mod.Llm(cfg)
        self.stt_lock = asyncio.Lock()
        self.last_request = time.monotonic()
        self.should_exit = None  # колбэк из __main__: остановить uvicorn

    def touch(self) -> None:
        self.last_request = time.monotonic()

    def rotate_audio(self) -> None:
        files = sorted(AUDIO_DIR.glob("*.wav"), key=lambda p: p.name)
        for p in files[: max(0, len(files) - self.cfg.audio_keep)]:
            try:
                p.unlink()
                self.db.clear_audio(p.name)
            except OSError as e:
                log.warning("не удалил %s: %s", p.name, e)

    async def housekeeping(self) -> None:
        while True:
            await asyncio.sleep(2)
            if not self.stt_lock.locked():
                self.stt.maybe_unload()
            idle = time.monotonic() - self.last_request
            if self.cfg.brain_idle_exit_s > 0 and idle > self.cfg.brain_idle_exit_s and self.should_exit:
                log.info("нет запросов %.0f с — выхожу", idle)
                self.should_exit()
                return


def create_app(cfg: Config) -> FastAPI:
    brain = Brain(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(brain.housekeeping())
        log.info("мозг запущен: pid %d, порт %d, модель %s", os.getpid(), cfg.port, cfg.model)
        yield
        task.cancel()

    app = FastAPI(title="Sherpa brain", lifespan=lifespan)
    app.state.brain = brain

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse({"error": e.code, "message": e.message}, status_code=e.status)

    @app.get("/health")
    async def health():
        return {
            "ok": True,
            "stt_loaded": brain.stt.loaded,
            "vad": brain.stt.vad_ok,
            "model": cfg.model,
            "pid": os.getpid(),
        }

    @app.post("/transcribe")
    async def transcribe(request: Request):
        brain.touch()
        body = await request.body()
        if len(body) > MAX_BODY:
            raise ApiError(413, "audio_too_long", "Запись слишком большая (больше 4 МБ).")
        try:
            x = stt_mod.decode_wav(body)
        except stt_mod.BadAudio as e:
            raise ApiError(400, "bad_audio", f"Не получилось прочитать запись: {e}")
        sec = len(x) / stt_mod.SR
        if sec > cfg.max_audio_s + 0.5:
            raise ApiError(413, "audio_too_long", f"Запись {sec:.0f} с, а можно не больше {cfg.max_audio_s:.0f} с.")
        audio_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        (AUDIO_DIR / f"{audio_id}.wav").write_bytes(body)
        brain.rotate_audio()
        try:
            async with brain.stt_lock:
                r = await asyncio.to_thread(brain.stt.transcribe, x)
        except Exception as e:  # noqa: BLE001
            log.exception("ошибка распознавания")
            raise ApiError(500, "stt_failed", f"Распознавание сломалось: {e}")
        brain.touch()
        return {
            "audio_id": audio_id,
            "text": r.text,
            "audio_sec": round(r.audio_sec, 2),
            "stt_ms": r.stt_ms,
            "segments": r.segments,
            "method": r.method,
        }

    @app.post("/ask")
    async def ask(body: AskIn):
        brain.touch()
        question = body.question.strip()
        if not question:
            raise ApiError(400, "empty_question", "Вопрос пустой.")
        session_id = body.session_id or secrets.token_hex(6)
        audio_file = f"{body.audio_id}.wav" if body.audio_id and (AUDIO_DIR / f"{body.audio_id}.wav").exists() else None
        base = dict(
            session_id=session_id,
            turn=brain.db.next_turn(session_id),
            source=body.source,
            audio_file=audio_file,
            audio_sec=body.audio_sec,
            transcript_raw=body.transcript_raw,
            question=question,
            stt_ms=body.stt_ms,
            model=cfg.model,
        )
        spent = brain.db.cost_since(date.today().isoformat())
        if spent >= cfg.daily_limit_usd:
            brain.db.insert(**base, status="limit", error=f"потрачено ${spent:.4f} из ${cfg.daily_limit_usd}")
            raise ApiError(
                429, "daily_limit", f"Лимит на сегодня исчерпан: ${spent:.2f} из ${cfg.daily_limit_usd:.2f}."
            )
        history = brain.db.session_messages(session_id, cfg.max_dialog_pairs)
        try:
            a = await brain.llm.ask(question, history)
        except llm_mod.NoApiKey:
            brain.db.insert(**base, status="error", error="no_api_key")
            raise ApiError(500, "no_api_key", "Нет ключа: задай переменную окружения OPENROUTER_API_KEY.")
        except Exception as e:  # noqa: BLE001
            log.exception("ошибка нейронки")
            brain.db.insert(**base, status="error", error=f"{type(e).__name__}: {e}"[:500])
            if type(e).__name__ == "OutputTokenLimitExceeded":
                msg = f"Ответ не влез в {cfg.max_answer_tokens} токенов — спроси уже или подними max_answer_tokens."
            else:
                msg = f"Нейронка не ответила: {type(e).__name__}."
            raise ApiError(502, "llm_failed", msg)
        base.update(
            answer=a.text,
            model=a.model,
            tokens_in=a.tokens_in,
            tokens_out=a.tokens_out,
            cost_usd=a.cost_usd,
            llm_ms=a.llm_ms,
            messages_json=a.messages_json,
        )
        brain.db.insert(**base, status="ok")
        brain.touch()
        return {
            "session_id": session_id,
            "turn": base["turn"],
            "answer": a.text,
            "model": a.model,
            "tokens_in": a.tokens_in,
            "tokens_out": a.tokens_out,
            "cost_usd": a.cost_usd,
            "llm_ms": a.llm_ms,
        }

    @app.get("/history")
    async def history(limit: int = 50):
        return brain.db.history(max(1, min(limit, 500)))

    @app.get("/stats")
    async def stats():
        today = date.today()
        t = brain.db.stats_since(today.isoformat())
        t["limit_usd"] = cfg.daily_limit_usd
        m = brain.db.stats_since(today.replace(day=1).isoformat())
        return {"today": t, "month": m, "model": cfg.model}

    return app
