# -*- coding: utf-8 -*-
"""Скачивает модель распознавания речи GigaAM v3 e2e (русский, с заглавными и знаками препинания).

Запуск из корня проекта:  python tools/download_model.py
Файлы (около 225 МБ) кладутся в brain/models/gigaam-v3-e2e/ — туда же смотрит brain/config.toml.
Источник: репозиторий csukuangfj/sherpa-onnx-nemo-ctc-punct-giga-am-v3-russian-2025-12-16 на Hugging Face.
Уже скачанные файлы нужного размера пропускаются, так что запускать повторно безопасно.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

REPO = "csukuangfj/sherpa-onnx-nemo-ctc-punct-giga-am-v3-russian-2025-12-16"
BASE = f"https://huggingface.co/{REPO}/resolve/main/"
FILES = ["model.int8.onnx", "tokens.txt", "LICENSE"]
DEST = Path(__file__).resolve().parent.parent / "brain" / "models" / "gigaam-v3-e2e"


def remote_size(url: str) -> int | None:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        n = r.headers.get("Content-Length")
        return int(n) if n else None


def fetch(name: str) -> None:
    url, target = BASE + name, DEST / name
    want = remote_size(url)
    if target.exists() and (want is None or target.stat().st_size == want):
        print(f"{name}: уже есть, пропускаю")
        return
    part = target.with_name(target.name + ".part")
    print(f"{name}: скачиваю" + (f" ({want / 1_000_000:.0f} МБ)" if want else ""))
    with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
        got = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            got += len(chunk)
            if want:
                print(f"\r  {got * 100 // want}%  ", end="", flush=True)
    print()
    if want and part.stat().st_size != want:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{name}: скачалось {got} байт из {want}")
    part.replace(target)


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    try:
        for name in FILES:
            fetch(name)
    except Exception as e:  # noqa: BLE001 — любая сетевая ошибка → понятный текст
        print(f"\nНе получилось скачать: {e}\n"
              f"Можно скачать три файла вручную со страницы https://huggingface.co/{REPO}/tree/main\n"
              f"и положить их в {DEST}")
        return 1
    print(f"Готово: {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
