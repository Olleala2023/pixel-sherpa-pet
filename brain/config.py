"""Конфиг мозга: brain/config.toml. Если файла нет — создаётся со значениями по умолчанию."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BRAIN_DIR / "config.toml"
DATA_DIR = BRAIN_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
DB_PATH = DATA_DIR / "brain.db"
LOG_PATH = DATA_DIR / "brain.log"

DEFAULT_SYSTEM_PROMPT = (
    "Ты — Шерпа, робопёс-помощник. Отвечай по-русски, коротко, как справка: "
    "2–6 предложений или короткий список. Вопрос получен распознаванием речи: "
    "он без пунктуации и может содержать ошибки в словах — пойми по смыслу. "
    "Если вопрос неясен — задай один уточняющий вопрос."
)

DEFAULT_TOML = f'''# Настройки мозга Шерпы. Ключ OpenRouter сюда НЕ пишется — только переменная окружения OPENROUTER_API_KEY.
port = 8765
model = "deepseek/deepseek-v4-flash"
daily_limit_usd = 0.5
max_answer_tokens = 1000
max_dialog_pairs = 6
stt_idle_unload_s = 300
brain_idle_exit_s = 3600
audio_keep = 50
max_audio_s = 60
system_prompt = "{DEFAULT_SYSTEM_PROMPT}"

[gigaam]
model = '{Path.home() / ".dsh" / "speech-to-text" / "gigaam" / "gigaam_multilingual_600m_int8.onnx"}'
tokens = '{Path.home() / ".dsh" / "speech-to-text" / "gigaam" / "gigaam_multilingual_600m_tokens.txt"}'
num_threads = 4

[vad]
model = "models/silero_vad.onnx"   # относительно папки brain/
threshold = 0.5
min_silence_s = 0.5
min_speech_s = 0.25
max_speech_s = 20
'''


@dataclass
class VadCfg:
    model: Path
    threshold: float = 0.5
    min_silence_s: float = 0.5
    min_speech_s: float = 0.25
    max_speech_s: float = 20.0


@dataclass
class Config:
    port: int = 8765
    model: str = "deepseek/deepseek-v4-flash"
    daily_limit_usd: float = 0.5
    max_answer_tokens: int = 1000
    max_dialog_pairs: int = 6
    stt_idle_unload_s: float = 300
    brain_idle_exit_s: float = 3600
    audio_keep: int = 50
    max_audio_s: float = 60
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    gigaam_model: Path = field(default_factory=Path)
    gigaam_tokens: Path = field(default_factory=Path)
    gigaam_threads: int = 4
    vad: VadCfg = field(default_factory=lambda: VadCfg(BRAIN_DIR / "models" / "silero_vad.onnx"))


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        path.write_text(DEFAULT_TOML, encoding="utf-8")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    g = raw.get("gigaam", {})
    v = raw.get("vad", {})
    vad_model = Path(v.get("model", "models/silero_vad.onnx"))
    if not vad_model.is_absolute():
        vad_model = BRAIN_DIR / vad_model
    top = {k: raw[k] for k in Config.__dataclass_fields__ if k in raw and not isinstance(raw[k], dict)}
    cfg = Config(**top)
    cfg.gigaam_model = Path(g["model"])
    cfg.gigaam_tokens = Path(g["tokens"])
    cfg.gigaam_threads = int(g.get("num_threads", 4))
    cfg.vad = VadCfg(
        model=vad_model,
        threshold=float(v.get("threshold", 0.5)),
        min_silence_s=float(v.get("min_silence_s", 0.5)),
        min_speech_s=float(v.get("min_speech_s", 0.25)),
        max_speech_s=float(v.get("max_speech_s", 20)),
    )
    DATA_DIR.mkdir(exist_ok=True)
    AUDIO_DIR.mkdir(exist_ok=True)
    return cfg
