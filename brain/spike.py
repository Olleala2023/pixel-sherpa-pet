"""Этап 0: разведка. GigaAM через sherpa-onnx (Python) и один вопрос через Pydantic AI + OpenRouter.

Запуск: D:\\Program\\python.exe brain\\spike.py [путь.wav]
"""
import asyncio
import sys
import time
import wave
from pathlib import Path

import numpy as np

GIGAAM_DIR = Path.home() / ".dsh" / "speech-to-text" / "gigaam"
MODEL = GIGAAM_DIR / "gigaam_multilingual_600m_int8.onnx"
TOKENS = GIGAAM_DIR / "gigaam_multilingual_600m_tokens.txt"
DEFAULT_WAV = Path(r"C:\Users\Oleg\Documents\deepseek-harness\default-workspace\ru-test.wav")


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    assert sw == 2, f"ожидаю PCM16, а sampwidth={sw}"
    x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def stt(path: Path) -> None:
    import sherpa_onnx

    t0 = time.perf_counter()
    rec = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
        model=str(MODEL), tokens=str(TOKENS), num_threads=4, decoding_method="greedy_search"
    )
    t_load = time.perf_counter() - t0
    samples, sr = read_wav(path)
    t1 = time.perf_counter()
    s = rec.create_stream()
    s.accept_waveform(sr, samples)
    rec.decode_stream(s)
    t_dec = time.perf_counter() - t1
    print(f"[stt] аудио {len(samples) / sr:.1f} с, sr={sr}; загрузка {t_load:.2f} с, распознавание {t_dec:.2f} с")
    print(f"[stt] текст: {s.result.text}")


async def llm() -> None:
    import pydantic_ai
    from pydantic_ai import Agent
    from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings

    print(f"[llm] pydantic_ai {pydantic_ai.__version__}")
    agent = Agent(
        OpenRouterModel("deepseek/deepseek-v4-flash"),
        model_settings=OpenRouterModelSettings(openrouter_usage={"include": True}, max_tokens=200),
        instructions="Отвечай по-русски, коротко, 1–2 предложения.",
    )
    t0 = time.perf_counter()
    result = await agent.run("сколько лап у собаки")
    print(f"[llm] {time.perf_counter() - t0:.2f} с, ответ: {result.output}")
    u = result.usage
    print(f"[llm] type(result.usage) = {type(u).__name__}, callable={callable(u)}")
    print(f"[llm] input_tokens={u.input_tokens} output_tokens={u.output_tokens} requests={u.requests}")
    print(f"[llm] response.model_name={result.response.model_name}")
    print(f"[llm] provider_details={result.response.provider_details}")


if __name__ == "__main__":
    wav = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WAV
    stt(wav)
    asyncio.run(llm())
