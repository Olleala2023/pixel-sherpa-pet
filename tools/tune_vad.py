"""Подбор нарезки для живого распознавания: пауза (min_silence_s) и предел куска (max_speech_s).

Запись подаётся в LiveSession порциями по 0,4 с, как с микрофона, и для каждой пары настроек считается:
- ошибки в словах к эталону (без регистра и знаков препинания);
- сколько кусков и сколько ждать текста во время речи (самая долгая пауза между появлениями текста);
- сколько ждать итога после конца записи.

Запуск из корня проекта (нужны ffmpeg для m4a/mp3 и модель GigaAM из brain/config.toml):
    python tools/tune_vad.py "tools/test-audio/Запись (2).m4a" tools/test-audio/ru-65s.txt
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain.config import load_config  # noqa: E402
from brain.stt import SR, LiveSession, Stt, decode_wav  # noqa: E402

PAUSES = (0.5, 0.6, 0.8, 1.0)
MAX_SPEECH = (10.0, 15.0, 20.0)
CHUNK_S = 0.4


def read_audio(path: Path):
    data = path.read_bytes()
    if path.suffix.lower() != ".wav":
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "a.wav"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(SR),
                            "-c:a", "pcm_s16le", str(out)], check=True)
            data = out.read_bytes()
    return decode_wav(data)


def words(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9]+", text.lower().replace("ё", "е"))


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1] / max(1, len(ref))


def run(stt: Stt, x, pause: float, max_s: float) -> dict:
    stt.cfg.vad.min_silence_s, stt.cfg.vad.max_speech_s = pause, max_s
    s = LiveSession(stt)
    step = int(CHUNK_S * SR)
    shown = [0.0]  # моменты записи (с), когда появлялся новый текст
    work = 0.0
    for i in range(0, len(x), step):
        t0 = time.perf_counter()
        new = s.accept(x[i : i + step])
        work = max(work, time.perf_counter() - t0)
        if new:
            shown.append((i + step) / SR)
    res, _ = s.finish()
    gaps = [b - a for a, b in zip(shown, shown[1:] + [len(x) / SR])]
    return {"text": res.text, "segments": res.segments, "max_gap": max(gaps), "tail_ms": res.stt_ms,
            "worst_chunk_ms": int(work * 1000)}


def main() -> None:
    audio, ref_path = Path(sys.argv[1]), Path(sys.argv[2])
    ref = words(ref_path.read_text(encoding="utf-8"))
    x = read_audio(audio)
    stt = Stt(load_config())
    stt.ensure_loaded()
    print(f"запись {len(x) / SR:.1f} с, в эталоне {len(ref)} слов, порог VAD {stt.cfg.vad.threshold}")
    print(f"{'пауза':>6} {'предел':>7} {'кусков':>7} {'ошибок':>7} {'ждать текст':>12} {'итог после':>11} {'худшая порция':>14}")
    for max_s in MAX_SPEECH:
        for pause in PAUSES:
            r = run(stt, x, pause, max_s)
            print(f"{pause:>5.1f}с {max_s:>6.0f}с {r['segments']:>7} {wer(ref, words(r['text'])):>6.1%} "
                  f"{r['max_gap']:>10.1f} с {r['tail_ms']:>8} мс {r['worst_chunk_ms']:>11} мс")


if __name__ == "__main__":
    main()
