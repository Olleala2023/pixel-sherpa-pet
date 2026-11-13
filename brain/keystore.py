"""Где лежит ключ API: диспетчер учётных данных Windows (через keyring) или переменная окружения.

Ключ не пишется ни в config.toml, ни в базу, ни в лог. Сохранённый из окна «Настройки» важнее переменной:
так другой человек может подставить свой ключ, не трогая переменные окружения.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("brain.keystore")
SERVICE = "Sherpa brain"
ENV = {"openrouter": "OPENROUTER_API_KEY"}


def _kr():
    import keyring  # лениво: без пакета работает только переменная окружения

    return keyring


def get_key(provider: str) -> tuple[str | None, str | None]:
    """(ключ, откуда): откуда — 'windows', 'env' или None."""
    try:
        k = _kr().get_password(SERVICE, provider)
        if k:
            return k, "windows"
    except Exception as e:  # noqa: BLE001
        log.warning("не прочитал ключ из диспетчера учётных данных: %s", type(e).__name__)
    k = os.environ.get(ENV.get(provider, ""), "")
    return (k, "env") if k else (None, None)


def set_key(provider: str, key: str) -> None:
    _kr().set_password(SERVICE, provider, key)


def delete_key(provider: str) -> None:
    try:
        _kr().delete_password(SERVICE, provider)
    except Exception as e:  # noqa: BLE001 — ключа и не было
        log.info("удалять нечего: %s", type(e).__name__)
