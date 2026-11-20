# -*- coding: utf-8 -*-
"""Голосовая справка Шерпы: окно «Спросить Шерпу», запись с микрофона, клиент мозга, окно «История».

Мозг — отдельный процесс (python -m brain) на 127.0.0.1, см. docs/голосовая-справка-ТЗ.md.
Tk трогается только из главного потока: сеть и запись работают в фоне, результаты идут через queue.
"""
from __future__ import annotations

import io
import json
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import wave
from pathlib import Path
from tkinter import ttk

PROJECT_DIR = Path(__file__).resolve().parent.parent  # корень проекта
BRAIN_CONFIG = PROJECT_DIR / "brain" / "config.toml"
SR = 16000
MAX_REC_S = 60
FONT = ("Segoe UI", 10)


def brain_url() -> str:
    port = 8765
    try:
        import tomllib

        port = int(tomllib.loads(BRAIN_CONFIG.read_text(encoding="utf-8")).get("port", port))
    except Exception:  # noqa: BLE001 — нет конфига: порт по умолчанию
        pass
    return f"http://127.0.0.1:{port}"


class BrainError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class BrainClient:
    def __init__(self):
        self.url = brain_url()
        self._start_lock = threading.Lock()

    def _req(self, path: str, data: bytes | None = None, ctype: str = "application/json", timeout: float = 120):
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                body = json.load(e)
                raise BrainError(body.get("error", "http"), body.get("message", f"HTTP {e.code}")) from None
            except (ValueError, AttributeError):
                raise BrainError("http", f"Мозг ответил HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise BrainError("brain_down", f"Мозг не отвечает: {e}") from None

    def health(self, timeout: float = 0.5) -> dict | None:
        try:
            return self._req("/health", timeout=timeout)
        except BrainError:
            return None

    def ensure_running(self) -> None:
        """Если мозг молчит — запускает его фоном (pythonw -m brain) и ждёт до 15 с."""
        with self._start_lock:
            if self.health():
                return
            exe = Path(sys.executable)
            pyw = exe.with_name("pythonw.exe")
            proc = subprocess.Popen(
                [str(pyw if pyw.exists() else exe), "-m", "brain"],
                cwd=str(PROJECT_DIR),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            t_end = time.monotonic() + 30  # холодный старт с диска бывает ~11 с
            while time.monotonic() < t_end:
                time.sleep(0.3)
                if self.health():
                    return
                if proc.poll() is not None:  # упал при старте (занят порт, нет пакета…) — не ждём зря
                    raise BrainError("brain_down", "Мозг упал при запуске, смотри brain/data/brain.log")
            raise BrainError("brain_down", "Мозг не запустился за 30 с, смотри brain/data/brain.log")

    def transcribe(self, wav: bytes) -> dict:
        self.ensure_running()
        return self._req("/transcribe", wav, "audio/wav")

    def ask(self, payload: dict) -> dict:
        self.ensure_running()
        return self._req("/ask", json.dumps(payload).encode("utf-8"))

    # --- живая запись (/listen): звук порциями, текст по кускам между паузами ---
    def listen_start(self) -> str:
        self.ensure_running()
        return self._req("/listen", b"{}", timeout=15)["live_id"]

    def listen_chunk(self, lid: str, pcm: bytes) -> dict:
        return self._req(f"/listen/{lid}", pcm, "application/octet-stream", timeout=60)

    def listen_finish(self, lid: str, pcm: bytes) -> dict:
        return self._req(f"/listen/{lid}/finish", pcm, "application/octet-stream", timeout=120)

    def ask_stream(self, payload: dict, on_delta) -> dict:
        """Как ask, но куски ответа приходят в on_delta(текст) по мере генерации (вызывается в фоновом потоке).
        Мозг старой версии без /ask/stream — обычный ask одним куском."""
        self.ensure_running()
        req = urllib.request.Request(self.url + "/ask/stream", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code in (404, 405):
                res = self.ask(payload)
                on_delta(res.get("answer", ""))
                return res
            raise BrainError("http", f"Мозг ответил HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise BrainError("brain_down", f"Мозг не отвечает: {e}") from None
        with r:
            for raw in r:
                if not raw.strip():
                    continue
                item = json.loads(raw)
                if "delta" in item:
                    on_delta(item["delta"])
                elif "error" in item:
                    raise BrainError(item["error"], item.get("message", "Ошибка мозга"))
                elif item.get("done"):
                    return item
        raise BrainError("brain_down", "Мозг оборвал ответ на полуслове")

    def restart(self) -> None:
        """Перезапуск мозга (новый конфиг и код): гасим и поднимаем заново."""
        self.stop()
        self.url = brain_url()  # порт мог поменяться в config.toml
        self.ensure_running()

    def stop(self) -> None:
        """Гасит мозг: /shutdown, ждём, пока замолчит. Старый мозг без /shutdown завершаем по pid из /health —
        это наш же процесс. Мозга нет — ничего не делаем."""
        h = self.health()
        if h:
            try:
                self._req("/shutdown", b"{}", timeout=3)
            except BrainError as e:
                if e.code != "http" or not h.get("pid"):
                    raise
                import os
                import signal

                os.kill(int(h["pid"]), signal.SIGTERM)
            t_end = time.monotonic() + 10
            while self.health(timeout=0.3) and time.monotonic() < t_end:
                time.sleep(0.2)
            if self.health(timeout=0.3):
                raise BrainError("brain_down", "Мозг не завершился за 10 с — закройте pythonw.exe в диспетчере задач")

    def history(self, limit: int = 50) -> list[dict]:
        self.ensure_running()
        return self._req(f"/history?limit={limit}")

    def stats(self) -> dict:
        self.ensure_running()
        return self._req("/stats")

    def settings(self) -> dict:
        self.ensure_running()
        return self._req("/settings")

    def reset_stats(self) -> dict:
        self.ensure_running()
        return self._req("/stats/reset", b"{}")

    def check_settings(self, payload: dict) -> dict:
        """Проверка модели и ключа без сохранения."""
        self.ensure_running()
        return self._req("/settings/check", json.dumps(payload).encode("utf-8"), timeout=40)

    def save_settings(self, payload: dict) -> dict:
        """Ключ уходит только мозгу на 127.0.0.1, тот кладёт его в диспетчер учётных данных Windows."""
        self.ensure_running()
        return self._req("/settings", json.dumps(payload).encode("utf-8"), timeout=40)


class Recorder:
    """Запись 16 кГц моно int16. Колбэк PortAudio только складывает куски в список."""

    def __init__(self):
        self.stream = None
        self.chunks: list = []
        self.t0 = 0.0

    @property
    def active(self) -> bool:
        return self.stream is not None

    def elapsed(self) -> float:
        return time.monotonic() - self.t0 if self.stream else 0.0

    def start(self) -> None:
        import sounddevice as sd  # лениво: пёс стартует и без пакета

        self.chunks = []
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="int16", callback=self._cb)
        self.stream.start()
        self.t0 = time.monotonic()

    def _cb(self, indata, frames, t, status) -> None:  # noqa: ARG002
        self.chunks.append(bytes(indata))

    def stop(self) -> bytes:
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop()
            finally:
                s.close()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(b"".join(self.chunks))
        self.chunks = []
        return buf.getvalue()


class LiveUpload:
    """Пока идёт запись, раз в SEND_S отправляет мозгу новый звук; когда мозг распознал очередной кусок
    (после паузы в речи), зовёт on_text(весь текст на сейчас) — из фонового потока.

    finish(wav) дожидается отправки и возвращает итог как у /transcribe. Мозг старой версии без /listen
    или потерянная запись — распознаём готовый WAV по-старому, одним запросом."""

    SEND_S = 0.4

    def __init__(self, client: BrainClient, rec: Recorder, on_text):
        self.client, self.rec, self.on_text = client, rec, on_text
        self.lid: str | None = None
        self.sent = 0  # сколько байт PCM уже у мозга
        self.error: Exception | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="live-upload")
        self._thread.start()

    def _run(self) -> None:
        try:
            self.lid = self.client.listen_start()
            while not self._stop.wait(self.SEND_S):
                self._send(b"".join(list(self.rec.chunks)))
        except Exception as e:  # noqa: BLE001 — запись идёт дальше, в конце распознаем целиком
            self.error = e

    def _send(self, pcm: bytes) -> None:
        new = pcm[self.sent :]
        if not new or self.lid is None:
            return
        r = self.client.listen_chunk(self.lid, new)
        self.sent += len(new)
        if r.get("new"):
            self.on_text(r.get("text", ""))

    def cancel(self) -> None:
        self._stop.set()

    def finish(self, wav: bytes) -> dict:
        """Звать в фоновом потоке после Recorder.stop(): wav — вся запись."""
        self._stop.set()
        self._thread.join(timeout=60)
        if self.lid is None or self.error is not None:
            return self.client.transcribe(wav)
        with wave.open(io.BytesIO(wav), "rb") as w:
            pcm = w.readframes(w.getnframes())
        try:
            return self.client.listen_finish(self.lid, pcm[self.sent :])
        except BrainError as e:
            if e.code in ("no_live", "http"):  # мозг перезапускался или старой версии
                return self.client.transcribe(wav)
            raise


class _Worker:
    """Фоновые задачи с ответом в главный поток через queue + after(50)."""

    def __init__(self, widget: tk.Misc):
        self.widget = widget
        self.q: queue.Queue = queue.Queue()
        self.alive = True
        self._poll()

    def run(self, fn, on_ok, on_err) -> None:
        def body():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001
                self.q.put((on_err, e))
            else:
                self.q.put((on_ok, res))

        threading.Thread(target=body, daemon=True).start()

    def post(self, cb, arg) -> None:
        """Из фонового потока: вызвать cb(arg) в главном (куски потокового ответа)."""
        self.q.put((cb, arg))

    def _poll(self) -> None:
        if not self.alive:
            return
        while True:
            try:
                cb, arg = self.q.get_nowait()
            except queue.Empty:
                break
            cb(arg)
        self.widget.after(50, self._poll)


def err_text(e: Exception) -> str:
    if isinstance(e, BrainError):
        return e.message
    name = type(e).__name__
    if name == "PortAudioError" or "sounddevice" in type(e).__module__:
        return f"Микрофон недоступен: {e}"
    if isinstance(e, ModuleNotFoundError):
        return f"Не установлен пакет: {e.name} (pip install -r requirements.txt)"
    return f"{name}: {e}"


