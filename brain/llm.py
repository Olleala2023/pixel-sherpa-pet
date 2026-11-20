"""Нейронка: агент Pydantic AI, модель через OpenRouter. Ключ — из keystore (Windows или переменная окружения)."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from . import keystore, tools
from .config import Config

log = logging.getLogger("brain.llm")
PRICES_URL = "https://openrouter.ai/api/v1/models"
KEY_URL = "https://openrouter.ai/api/v1/key"


class NoApiKey(Exception):
    pass


@dataclass
class Answer:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    llm_ms: int
    messages_json: str
    actions: list[dict] = field(default_factory=list)  # что сделать псу: напоминания (tools.set_reminder)


class Llm:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._agent: Agent | None = None
        self._prices: dict[str, tuple[float, float]] | None = None

    def _get_agent(self) -> Agent:
        if self._agent is None:
            key, _src = keystore.get_key(self.cfg.provider)
            if not key:
                raise NoApiKey()
            model = OpenRouterModel(self.cfg.model, provider=OpenRouterProvider(api_key=key))
            settings = OpenRouterModelSettings(
                openrouter_usage={"include": True},
                openrouter_reasoning={"enabled": False},  # справке рассуждения не нужны: дешевле и предсказуемее
                max_tokens=self.cfg.max_answer_tokens,
            )
            # instructions (а не system_prompt): подставляются в каждый запуск, даже с message_history
            self._agent = Agent(model, model_settings=settings, instructions=self.cfg.system_prompt,
                                deps_type=tools.Deps, tools=tools.TOOLS if self.cfg.tools else ())
        return self._agent

    def reset(self) -> None:
        """Сменились провайдер, модель или ключ — агент соберётся заново при следующем вопросе."""
        self._agent = None

    def _load_prices(self) -> dict[str, tuple[float, float]]:
        if self._prices is None:
            with urllib.request.urlopen(PRICES_URL, timeout=10) as r:
                data = json.load(r)["data"]
            self._prices = {m["id"]: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])) for m in data}
        return self._prices

    def check(self, model: str, key: str | None) -> dict:
        """Бесплатная проверка без вопроса к модели: есть ли такая модель в OpenRouter и принят ли ключ.
        Значения: True / False / None (не проверил — нет связи)."""
        out = {"model_ok": None, "key_ok": None}
        try:
            self._prices = None
            out["model_ok"] = model in self._load_prices()
        except Exception as e:  # noqa: BLE001
            log.warning("не проверил модель: %s", type(e).__name__)
        if key:
            req = urllib.request.Request(KEY_URL, headers={"Authorization": f"Bearer {key}"})
            try:
                with urllib.request.urlopen(req, timeout=10):
                    out["key_ok"] = True
            except urllib.error.HTTPError as e:
                out["key_ok"] = False if e.code in (401, 403) else None
                log.warning("проверка ключа: HTTP %s", e.code)
            except Exception as e:  # noqa: BLE001
                log.warning("не проверил ключ: %s", type(e).__name__)
        return out

    def _price_cost(self, tokens_in: int, tokens_out: int) -> float | None:
        """Запасной расчёт стоимости по прайсу OpenRouter, если cost не пришёл в ответе."""
        try:
            p = self._load_prices().get(self.cfg.model)
            return None if p is None else tokens_in * p[0] + tokens_out * p[1]
        except Exception as e:  # noqa: BLE001
            log.warning("не получил прайс OpenRouter: %s", e)
            return None

    async def ask(self, question: str, history_jsons: list[str],
                  on_delta: Callable[[str], None] | None = None) -> Answer:
        """Вопрос нейронке. on_delta — получает куски текста ответа по мере генерации (для живого вывода)."""
        agent = self._get_agent()
        history = []
        for j in history_jsons:
            history.extend(ModelMessagesTypeAdapter.validate_json(j))
        deps = tools.Deps()

        async def stream(_ctx, events) -> None:
            async for ev in events:
                if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart) and ev.part.content:
                    on_delta(ev.part.content)
                elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta) and ev.delta.content_delta:
                    on_delta(ev.delta.content_delta)

        t0 = time.perf_counter()
        result = await agent.run(question, message_history=history or None, deps=deps,
                                 event_stream_handler=stream if on_delta else None)
        llm_ms = int((time.perf_counter() - t0) * 1000)
        usage = result.usage  # в pydantic-ai 2.x — свойство (проверено на 2.68.0 в brain/spike.py)
        tin, tout = int(usage.input_tokens or 0), int(usage.output_tokens or 0)
        costs = [
            m.provider_details.get("cost")
            for m in result.new_messages()
            if isinstance(m, ModelResponse) and m.provider_details
        ]
        costs = [float(c) for c in costs if c is not None]
        cost = sum(costs) if costs else self._price_cost(tin, tout)
        return Answer(
            text=str(result.output),
            model=result.response.model_name or self.cfg.model,
            tokens_in=tin,
            tokens_out=tout,
            cost_usd=cost,
            llm_ms=llm_ms,
            messages_json=result.new_messages_json().decode("utf-8"),
            actions=deps.actions,
        )
