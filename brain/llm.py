"""Нейронка: Pydantic AI + OpenRouter. Ключ — только из OPENROUTER_API_KEY."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from .config import Config

log = logging.getLogger("brain.llm")
PRICES_URL = "https://openrouter.ai/api/v1/models"


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


class Llm:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._agent: Agent | None = None
        self._prices: dict[str, tuple[float, float]] | None = None

    def _get_agent(self) -> Agent:
        if self._agent is None:
            key = os.environ.get("OPENROUTER_API_KEY")
            if not key:
                raise NoApiKey()
            model = OpenRouterModel(self.cfg.model, provider=OpenRouterProvider(api_key=key))
            settings = OpenRouterModelSettings(
                openrouter_usage={"include": True},
                openrouter_reasoning={"enabled": False},  # справке рассуждения не нужны: дешевле и предсказуемее
                max_tokens=self.cfg.max_answer_tokens,
            )
            # instructions (а не system_prompt): подставляются в каждый запуск, даже с message_history
            self._agent = Agent(model, model_settings=settings, instructions=self.cfg.system_prompt)
        return self._agent

    def _price_cost(self, tokens_in: int, tokens_out: int) -> float | None:
        """Запасной расчёт стоимости по прайсу OpenRouter, если cost не пришёл в ответе."""
        try:
            if self._prices is None:
                with urllib.request.urlopen(PRICES_URL, timeout=10) as r:
                    data = json.load(r)["data"]
                self._prices = {
                    m["id"]: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])) for m in data
                }
            p = self._prices.get(self.cfg.model)
            return None if p is None else tokens_in * p[0] + tokens_out * p[1]
        except Exception as e:  # noqa: BLE001
            log.warning("не получил прайс OpenRouter: %s", e)
            return None

    async def ask(self, question: str, history_jsons: list[str]) -> Answer:
        agent = self._get_agent()
        history = []
        for j in history_jsons:
            history.extend(ModelMessagesTypeAdapter.validate_json(j))
        t0 = time.perf_counter()
        result = await agent.run(question, message_history=history or None)
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
        )
