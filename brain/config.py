"""Конфиг мозга: brain/config.toml. Если файла нет — создаётся со значениями по умолчанию."""
from __future__ import annotations

import json
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
    "знаки препинания в нём расставлены автоматически и не всегда верно, а в словах бывают ошибки — пойми по смыслу. "
    "Если вопрос неясен — задай один уточняющий вопрос."
)

DEFAULT_TOML = f'''# Настройки мозга Шерпы. Ключ API сюда НЕ пишется: он в диспетчере учётных данных Windows (окно «Настройки»)
# или в переменной окружения OPENROUTER_API_KEY. Провайдер и модель тоже меняются в окне «Настройки».
port = 8765
provider = "openrouter"
model = "deepseek/deepseek-v4-flash"
daily_limit_usd = 0.5
max_answer_tokens = 1000
max_dialog_pairs = 6
stt_idle_unload_s = 300
brain_idle_exit_s = 3600
audio_keep = 50
max_audio_s = 60
tools = true   # нейронке можно звать инструменты: время, счёт, напоминание, буфер обмена
system_prompt = "{DEFAULT_SYSTEM_PROMPT}"

[gigaam]
# Модель распознавания речи. Пути без диска — относительно папки brain/. Скачать: python tools/download_model.py
model = "models/gigaam-v3-e2e/model.int8.onnx"
tokens = "models/gigaam-v3-e2e/tokens.txt"
num_threads = 4

[vad]
model = "models/silero_vad.onnx"   # относительно папки brain/
threshold = 0.4
min_silence_s = 0.8
min_speech_s = 0.25
max_speech_s = 20
'''


@dataclass
class VadCfg:
    model: Path
    threshold: float = 0.4
    min_silence_s: float = 0.8
    min_speech_s: float = 0.25
    max_speech_s: float = 20.0


@dataclass
class Config:
    port: int = 8765
    provider: str = "openrouter"
    model: str = "deepseek/deepseek-v4-flash"
    daily_limit_usd: float = 0.5
    max_answer_tokens: int = 1000
    max_dialog_pairs: int = 6
    stt_idle_unload_s: float = 300
    brain_idle_exit_s: float = 3600
    audio_keep: int = 50
    max_audio_s: float = 60
    tools: bool = True
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    gigaam_model: Path = field(default_factory=Path)
    gigaam_tokens: Path = field(default_factory=Path)
    gigaam_threads: int = 4
    vad: VadCfg = field(default_factory=lambda: VadCfg(BRAIN_DIR / "models" / "silero_vad.onnx"))


PROVIDERS = {"openrouter": "OpenRouter"}  # на будущее: сюда добавляются другие


def save_settings(provider: str, model: str, path: Path | None = None, system_prompt: str | None = None) -> None:
    """Меняет provider, model (и system_prompt, если задан) в config.toml, не трогая комментарии и секции
    ([gigaam] тоже имеет model). Многострочное значение в тройных кавычках заменяется целиком."""
    path = path or CONFIG_PATH  # берём в момент вызова: тесты подменяют CONFIG_PATH
    lines = path.read_text(encoding="utf-8").splitlines()
    vals = {"provider": provider, "model": model}
    if system_prompt is not None:
        vals["system_prompt"] = system_prompt
    i = 0
    while i < len(lines) and not lines[i].lstrip().startswith("["):
        key, eq, rest = lines[i].partition("=")
        key = key.strip()
        if eq and key in vals:
            rest = rest.strip()
            q = rest[:3]
            if q in ('"""', "'''") and rest.count(q) < 2:  # многострочное: убираем строки до закрывающих кавычек
                j = i + 1
                while j < len(lines) and q not in lines[j]:
                    j += 1
                del lines[i + 1 : j + 1]
            lines[i] = f"{key} = {_toml_str(vals.pop(key))}"
        i += 1
    at = next((i for i, ln in enumerate(lines) if not ln.lstrip().startswith("#")), len(lines))
    for key, v in vals.items():  # строки не было (старый конфиг) — добавляем после шапки-комментария
        lines.insert(at, f"{key} = {_toml_str(v)}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def _abs(p: str | Path) -> Path:
    """Путь из конфига: относительный считается от папки brain/."""
    p = Path(p)
    return p if p.is_absolute() else BRAIN_DIR / p


def _toml_str(v: str) -> str:
    """Однострочная строка TOML. Экраны JSON (кавычки, обратный слеш, перевод строки, табуляция, uXXXX)
    TOML понимает так же, поэтому берём json.dumps; CR выкидываем."""
    return json.dumps(v.replace(chr(13), ""), ensure_ascii=False)


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        path.write_text(DEFAULT_TOML, encoding="utf-8")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    g = raw.get("gigaam", {})
    v = raw.get("vad", {})
    vad_model = _abs(v.get("model", "models/silero_vad.onnx"))
    top = {k: raw[k] for k in Config.__dataclass_fields__ if k in raw and not isinstance(raw[k], dict)}
    cfg = Config(**top)
    cfg.gigaam_model = _abs(g.get("model", "models/gigaam-v3-e2e/model.int8.onnx"))
    cfg.gigaam_tokens = _abs(g.get("tokens", "models/gigaam-v3-e2e/tokens.txt"))
    cfg.gigaam_threads = int(g.get("num_threads", 4))
    cfg.vad = VadCfg(
        model=vad_model,
        threshold=float(v.get("threshold", 0.4)),
        min_silence_s=float(v.get("min_silence_s", 0.8)),
        min_speech_s=float(v.get("min_speech_s", 0.25)),
        max_speech_s=float(v.get("max_speech_s", 20)),
    )
    DATA_DIR.mkdir(exist_ok=True)
    AUDIO_DIR.mkdir(exist_ok=True)
    return cfg
