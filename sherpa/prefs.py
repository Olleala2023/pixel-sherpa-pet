# -*- coding: utf-8 -*-
"""Настройки пса, которые он помнит между запусками: размер и галочки меню.

Файл sherpa-settings.json в корне проекта (в git не попадает, обновление его не трогает).
Нет файла или он испорчен — берутся значения по умолчанию; неизвестные и неверные поля отбрасываются.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "sherpa-settings.json"
DEFAULTS = {"scale": 3, "roaming": True, "sound": True, "bubble": True}
SCALES = (2, 3, 4, 5)


def load(path: Path | None = None) -> dict:
    out = dict(DEFAULTS)
    try:
        raw = json.loads((path or PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict):
        return out
    if raw.get("scale") in SCALES:
        out["scale"] = raw["scale"]
    for k in ("roaming", "sound", "bubble"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    return out


def save(values: dict, path: Path | None = None) -> None:
    """Сохраняет известные поля. Не вышло (папка только для чтения) — молча живём без памяти."""
    data = {k: values[k] for k in DEFAULTS if k in values}
    path = path or PATH
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
