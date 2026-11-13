"""HTTP-сервис мозга на 127.0.0.1. Протокол — docs/голосовая-справка-ТЗ.md."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import stt as stt_mod
from .config import AUDIO_DIR, DB_PATH, Config
from .db import Db

log = logging.getLogger("brain.server")
MAX_BODY = 4 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


class Brain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = Db(DB_PATH)
        self.stt = stt_mod.Stt(cfg)
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

    return app
