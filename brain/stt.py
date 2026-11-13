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

    def _vad_segments(self, x: np.ndarray) -> list[np.ndarray] | None:
        """Куски речи по Silero VAD; None — VAD недоступен."""
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
        spans: list[tuple[int, int]] = []

        def drain() -> None:
            while not vad.empty():
                spans.append((vad.front.start, vad.front.start + len(vad.front.samples)))
                vad.pop()

        for i in range(0, len(x), VAD_WINDOW):
            vad.accept_waveform(x[i : i + VAD_WINDOW])
            drain()
        vad.flush()
        drain()
        # Silero режет вплотную к речи и съедает тихие первые слова («ну») — берём поля из исходной записи
        out, prev_end = [], 0
        for a, b in sorted(spans):
            a = max(prev_end, a - SEG_PAD, 0)
            b = min(len(x), b + SEG_PAD)
            if b > a:
                out.append(x[a:b])
                prev_end = b
        return out

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
        if self.rec is None:
            self._load()
        texts = []
        for s in segs:
            st = self.rec.create_stream()
            st.accept_waveform(SR, s)
            self.rec.decode_stream(st)
            t = st.result.text.strip()
            if t:
                texts.append(t)
        self.last_used = time.monotonic()
        ms = int((time.perf_counter() - t0) * 1000)
        log.info("распознано %.1f с аудио, %d кусков (%s), %d мс", audio_sec, len(segs), method, ms)
        return SttResult(" ".join(texts), audio_sec, ms, len(segs), method)
