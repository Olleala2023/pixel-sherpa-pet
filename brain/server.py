"""HTTP-сервис мозга на 127.0.0.1. Протокол — docs/голосовая-справка-ТЗ.md."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import keystore
from . import llm as llm_mod
from . import stt as stt_mod
from .config import AUDIO_DIR, DB_PATH, PROVIDERS, Config, save_settings
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


class SettingsIn(BaseModel):
    provider: str = "openrouter"
    model: str
    api_key: str | None = None  # пусто — ключ не меняем
    clear_key: bool = False  # удалить сохранённый в Windows ключ


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
            raise ApiError(500, "no_api_key", "Нет ключа API — открой настройки (шестерёнка в окне Шерпы) и вставь ключ OpenRouter.")
        except Exception as e:  # noqa: BLE001
            log.exception("ошибка нейронки")
            brain.db.insert(**base, status="error", error=f"{type(e).__name__}: {e}"[:500])
            name = type(e).__name__
            if name == "OutputTokenLimitExceeded":
                msg = f"Ответ не влез в {cfg.max_answer_tokens} токенов — спроси уже или подними max_answer_tokens."
            elif "Connection" in name or "Timeout" in name:
                msg = "Нет связи с OpenRouter — проверь интернет."
            elif "401" in str(e) or "Unauthorized" in str(e):
                msg = "OpenRouter не принял ключ — проверь OPENROUTER_API_KEY."
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

    def settings_view() -> dict:
        key, src = keystore.get_key(cfg.provider)
        hint = ("…" + key[-4:]) if key and len(key) >= 12 else ("•••" if key else None)  # ключ целиком не отдаём
        return {"provider": cfg.provider, "providers": PROVIDERS, "model": cfg.model, "key_source": src,
                "key_hint": hint}

    @app.get("/settings")
    async def get_settings():
        return settings_view()

    def _validated(body: SettingsIn) -> tuple[str, str, str | None, str | None]:
        """(провайдер, модель, новый ключ, ключ для проверки) или ApiError."""
        provider, model = body.provider.strip(), body.model.strip()
        new_key = (body.api_key or "").strip() or None
        if provider not in PROVIDERS:
            raise ApiError(400, "bad_provider", f"Неизвестный провайдер: {provider}.")
        if not model:
            raise ApiError(400, "bad_model", "Название модели пустое.")
        if new_key:
            check_key = new_key
        elif body.clear_key:  # после удаления останется только переменная окружения
            check_key = os.environ.get(keystore.ENV.get(provider, ""), "") or None
        else:
            check_key = keystore.get_key(provider)[0]
        return provider, model, new_key, check_key

    @app.post("/settings/check")
    async def check_settings(body: SettingsIn):
        """Проверка подключения без сохранения: есть ли модель и принят ли ключ (введённый или сохранённый)."""
        brain.touch()
        _provider, model, _new, check_key = _validated(body)
        res = await asyncio.to_thread(brain.llm.check, model, check_key)
        res["has_key"] = bool(check_key)
        return res

    @app.post("/settings")
    async def post_settings(body: SettingsIn):
        """Проверяет модель и ключ (бесплатно, без вопроса к модели) и сохраняет. Ключ — только в Windows."""
        brain.touch()
        provider, model, new_key, check_key = _validated(body)
        res = await asyncio.to_thread(brain.llm.check, model, check_key)
        if res["key_ok"] is False:
            raise ApiError(400, "bad_key", "OpenRouter не принял ключ — проверь, что вставил его целиком.")
        if res["model_ok"] is False:
            raise ApiError(400, "bad_model", f"В OpenRouter нет модели «{model}». Точное имя — на openrouter.ai/models.")
        if body.clear_key:
            keystore.delete_key(provider)
        if new_key:
            keystore.set_key(provider, new_key)
        save_settings(provider, model)
        cfg.provider, cfg.model = provider, model
        brain.llm.reset()
        log.info("настройки: провайдер %s, модель %s, ключ %s", provider, model,
                 "новый" if new_key else ("удалён" if body.clear_key else "прежний"))
        out = settings_view()
        out["checked"] = res
        return out

    @app.get("/history")
    async def history(limit: int = 50):
        return brain.db.history(max(1, min(limit, 500)))

    def stats_view() -> dict:
        today = date.today()
        t = brain.db.stats_since(today.isoformat())
        t["limit_usd"] = cfg.daily_limit_usd
        return {
            "today": t,
            "week": brain.db.stats_since((today - timedelta(days=6)).isoformat()),  # 7 дней, считая сегодня
            "days30": brain.db.stats_since((today - timedelta(days=29)).isoformat()),
            "all": brain.db.stats_since(""),
            "reset_at": brain.db.get_meta("stats_reset_at"),
            "model": cfg.model,
        }

    @app.get("/stats")
    async def stats():
        return stats_view()

    @app.post("/stats/reset")
    async def stats_reset():
        brain.db.reset_stats()
        return stats_view()

    return app
