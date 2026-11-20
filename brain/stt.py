"""Распознавание речи: GigaAM (sherpa-onnx) + нарезка по паузам Silero VAD.

Модель грузится при первом запросе и выгружается после простоя (см. maybe_unload).
Вызовы распознавателя последовательные — их сериализует сервер через asyncio.Lock.
"""
from __future__ import annotations

import gc
import io
import logging
import time
import wave
from dataclasses import dataclass

import numpy as np

from .config import Config

log = logging.getLogger("brain.stt")
SR = 16000
VAD_WINDOW = 512  # 32 мс при 16 кГц
SEG_PAD = int(0.25 * SR)  # поля вокруг куска речи


class BadAudio(Exception):
    pass


@dataclass
class SttResult:
    text: str
    audio_sec: float
    stt_ms: int
    segments: int
    method: str  # vad / quiet-split / empty


def decode_wav(data: bytes) -> np.ndarray:
    """WAV PCM16 любой частоты и числа каналов → float32 моно 16 кГц."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            frames = w.readframes(n)
    except (wave.Error, EOFError) as e:
        raise BadAudio(f"не WAV: {e}") from e
    if sw != 2:
        raise BadAudio(f"нужен PCM16, а тут {sw * 8} бит")
    if len(frames) % (sw * max(1, ch)):  # оборвана посреди сэмпла (недокачанный файл)
        raise BadAudio("запись оборвана посреди сэмпла")
    x = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR and len(x):
        t_new = np.arange(int(len(x) * SR / sr)) / SR
        x = np.interp(t_new, np.arange(len(x)) / sr, x).astype(np.float32)
    return x


def quiet_split(x: np.ndarray, lo_s: float = 12.0, hi_s: float = 20.0) -> list[np.ndarray]:
    """Запасной путь без VAD: куски до hi_s, разрез в самом тихом месте окна [lo_s, hi_s]."""
    out, start = [], 0
    win = int(0.1 * SR)
    while len(x) - start > hi_s * SR:
        a, b = start + int(lo_s * SR), start + int(hi_s * SR)
        seg = x[a:b]
        n = len(seg) // win
        energy = (seg[: n * win].reshape(n, win) ** 2).mean(axis=1)
        cut = a + int(np.argmin(energy)) * win + win // 2
        out.append(x[start:cut])
        start = cut
    out.append(x[start:])
    return [s for s in out if len(s) > 0]


class Stt:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rec = None
        self.vad_ok: bool | None = None  # None — ещё не пробовали
        self.last_used = 0.0

    @property
    def loaded(self) -> bool:
        return self.rec is not None

    def _load(self) -> None:
        import sherpa_onnx

        t0 = time.perf_counter()
        self.rec = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(self.cfg.gigaam_model),
            tokens=str(self.cfg.gigaam_tokens),
            num_threads=self.cfg.gigaam_threads,
            decoding_method="greedy_search",
        )
        log.info("GigaAM загружена за %.2f с", time.perf_counter() - t0)

    def unload(self) -> None:
        if self.rec is not None:
            self.rec = None
            gc.collect()
            log.info("GigaAM выгружена после простоя")

    def maybe_unload(self) -> None:
        if self.rec is not None and time.monotonic() - self.last_used > self.cfg.stt_idle_unload_s:
            self.unload()

    def make_vad(self):
        """Потоковый Silero VAD (sherpa-onnx) с настройками из [vad]; None — VAD недоступен."""
        v = self.cfg.vad
        if self.vad_ok is False:
            return None
        try:
            import sherpa_onnx

            if not v.model.exists():
                raise FileNotFoundError(v.model)
            c = sherpa_onnx.VadModelConfig()
            c.silero_vad.model = str(v.model)
            c.silero_vad.threshold = v.threshold
            c.silero_vad.min_silence_duration = v.min_silence_s
            c.silero_vad.min_speech_duration = v.min_speech_s
            c.silero_vad.max_speech_duration = v.max_speech_s
            c.silero_vad.window_size = VAD_WINDOW
            c.sample_rate = SR
            c.num_threads = 1
            vad = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=int(self.cfg.max_audio_s) + 30)
        except Exception as e:  # noqa: BLE001 — любая поломка VAD → запасной путь
            log.warning("VAD недоступен, режу по тихим местам: %s", e)
            self.vad_ok = False
            return None
        self.vad_ok = True
        return vad

    def _vad_segments(self, x: np.ndarray) -> list[np.ndarray] | None:
        """Куски речи по Silero VAD для готовой записи; None — VAD недоступен.
        Тот же разрезчик, что у живой сессии: запись подаётся окнами по порядку."""
        vad = self.make_vad()
        if vad is None:
            return None
        cut = Cutter(vad)
        for i in range(0, len(x), VAD_WINDOW):
            cut.feed(x[i : i + VAD_WINDOW])
        spans = cut.spans() + cut.flush()
        return pad_spans(x, spans, [0])

    def ensure_loaded(self) -> None:
        if self.rec is None:
            self._load()
        self.last_used = time.monotonic()

    def decode(self, seg: np.ndarray) -> str:
        """Один кусок речи → текст. Модель должна быть загружена (ensure_loaded)."""
        st = self.rec.create_stream()
        st.accept_waveform(SR, seg)
        self.rec.decode_stream(st)
        self.last_used = time.monotonic()
        return st.result.text.strip()

    def transcribe(self, x: np.ndarray) -> SttResult:
        """Блокирующий вызов: звать из пула потоков под замком."""
        t0 = time.perf_counter()
        self.last_used = time.monotonic()
        audio_sec = len(x) / SR
        segs = self._vad_segments(x)
        method = "vad"
        if segs is None:
            segs, method = quiet_split(x), "quiet-split"
        if not segs:
            return SttResult("", audio_sec, int((time.perf_counter() - t0) * 1000), 0, "empty")
        self.ensure_loaded()
        texts = [t for t in (self.decode(s) for s in segs) if t]
        ms = int((time.perf_counter() - t0) * 1000)
        log.info("распознано %.1f с аудио, %d кусков (%s), %d мс", audio_sec, len(segs), method, ms)
        return SttResult(" ".join(texts), audio_sec, ms, len(segs), method)


class Cutter:
    """Обёртка потокового VAD: принимает звук любыми порциями, отдаёт границы готовых кусков речи
    (start, end) в отсчётах от начала записи. Кусок готов, когда после него была пауза min_silence_s
    или он дорос до max_speech_s."""

    def __init__(self, vad):
        self.vad = vad
        self.tail = np.zeros(0, dtype=np.float32)  # неполное окно VAD ждёт продолжения

    def feed(self, x: np.ndarray) -> None:
        x = np.concatenate([self.tail, x]) if len(self.tail) else x
        n = len(x) - len(x) % VAD_WINDOW
        for i in range(0, n, VAD_WINDOW):
            self.vad.accept_waveform(x[i : i + VAD_WINDOW])
        self.tail = x[n:].copy()

    def spans(self) -> list[tuple[int, int]]:
        out = []
        while not self.vad.empty():
            out.append((self.vad.front.start, self.vad.front.start + len(self.vad.front.samples)))
            self.vad.pop()
        return out

    def flush(self) -> list[tuple[int, int]]:
        """Конец записи: дописываем неполное окно и забираем последний кусок."""
        if len(self.tail):
            self.vad.accept_waveform(self.tail)
            self.tail = np.zeros(0, dtype=np.float32)
        self.vad.flush()
        return self.spans()


def pad_spans(x: np.ndarray, spans: list[tuple[int, int]], prev_end: list[int]) -> list[np.ndarray]:
    """Silero режет вплотную к речи и съедает тихие первые слова («ну») — берём поля из исходной записи.
    prev_end — [конец прошлого куска]: куски не перекрываются (список, чтобы живая сессия его помнила)."""
    out = []
    for a, b in sorted(spans):
        a = max(prev_end[0], a - SEG_PAD, 0)
        b = min(len(x), b + SEG_PAD)
        if b > a:
            out.append(x[a:b])
            prev_end[0] = b
    return out


class LiveSession:
    """Живое распознавание: звук приходит порциями во время записи, каждый кусок речи распознаётся,
    как только VAD увидел после него паузу. Итог совпадает с transcribe() той же записи: те же окна VAD,
    те же куски. Без VAD — копим звук и распознаём всё в конце (transcribe).

    Методы блокирующие: сервер зовёт их из пула потоков под stt_lock."""

    def __init__(self, stt: Stt):
        self.stt = stt
        self.vad = stt.make_vad()
        self.cut = Cutter(self.vad) if self.vad is not None else None
        self.parts: list[np.ndarray] = []
        self.n = 0
        self.prev_end = [0]
        self.texts: list[str] = []
        self.segments = 0
        self.stt_ms = 0
        self.last_used = time.monotonic()

    def _audio(self) -> np.ndarray:
        if len(self.parts) > 1:
            self.parts = [np.concatenate(self.parts)]
        return self.parts[0] if self.parts else np.zeros(0, dtype=np.float32)

    def _decode_spans(self, spans) -> list[str]:
        if not spans:
            return []
        t0 = time.perf_counter()
        segs = pad_spans(self._audio(), spans, self.prev_end)
        self.stt.ensure_loaded()
        new = [t for t in (self.stt.decode(s) for s in segs) if t]
        self.segments += len(segs)
        self.stt_ms += int((time.perf_counter() - t0) * 1000)
        self.texts.extend(new)
        return new

    def accept(self, x: np.ndarray) -> list[str]:
        """Новая порция звука; возвращает тексты кусков, которые стали готовы."""
        self.last_used = time.monotonic()
        if len(x) == 0:
            return []
        self.parts.append(x.astype(np.float32, copy=False))
        self.n += len(x)
        if self.cut is None:
            return []
        self.cut.feed(x)
        return self._decode_spans(self.cut.spans())

    def finish(self, x: np.ndarray | None = None) -> tuple[SttResult, list[str]]:
        """Остаток звука и конец записи → (итог как у transcribe, тексты последних кусков)."""
        new = self.accept(x) if x is not None and len(x) else []
        audio = self._audio()
        sec = len(audio) / SR
        if self.cut is None:  # без VAD: как раньше, целиком
            r = self.stt.transcribe(audio)
            return r, [r.text] if r.text else []
        t0 = time.perf_counter()
        new += self._decode_spans(self.cut.flush())
        tail_ms = int((time.perf_counter() - t0) * 1000)
        log.info("живое распознавание: %.1f с аудио, %d кусков, %d мс на распознавание, после записи — %d мс",
                 sec, self.segments, self.stt_ms, tail_ms)
        method = "vad-live" if self.segments else "empty"
        return SttResult(" ".join(self.texts), sec, tail_ms, self.segments, method), new

    @property
    def text(self) -> str:
        return " ".join(self.texts)
