"""The model picker's list: OpenRouter's catalog (prices, image support), cached on disk for a day."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from .wire import ModelCost, ModelKey, ModelRef

CATALOG_URL = "https://openrouter.ai/api/v1/models"
MAX_AGE_S = 24 * 3600


def parse_catalog(payload: dict) -> list[ModelRef]:
    models = []
    for item in payload.get("data", []):
        pricing = item.get("pricing") or {}
        try:
            cost = ModelCost(input=float(pricing["prompt"]) * 1e6, output=float(pricing["completion"]) * 1e6)
        except (KeyError, TypeError, ValueError):
            cost = None
        arch = item.get("architecture") or {}
        params = item.get("supported_parameters") or []
        models.append(ModelRef(
            provider="openrouter", id=item["id"], name=item.get("name") or item["id"],
            reasoning="reasoning" in params, vision="image" in (arch.get("input_modalities") or []), cost=cost,
        ))
    return sorted(models, key=lambda m: m.name.lower())


async def load_catalog(cache: Path, client: httpx.AsyncClient | None = None) -> list[ModelRef]:
    """The cached catalog if fresh, else a fetch; an old cache beats nothing when offline."""
    try:
        if time.time() - cache.stat().st_mtime < MAX_AGE_S:
            return parse_catalog(json.loads(cache.read_text()))
    except (OSError, ValueError):
        pass
    try:
        async with (client or httpx.AsyncClient(timeout=10)) as http:
            response = await http.get(CATALOG_URL)
            response.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(response.text)
        return parse_catalog(response.json())
    except (httpx.HTTPError, ValueError):
        try:
            return parse_catalog(json.loads(cache.read_text()))
        except (OSError, ValueError):
            return []


def has_credentials() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def recommended(models: list[ModelRef], wanted: list[str]) -> list[ModelKey]:
    """The configured defaults that the catalog actually lists, in order."""
    have = {f"{m.provider}/{m.id}" for m in models}
    return [ModelKey(provider=w.split("/", 1)[0], id=w.split("/", 1)[1]) for w in wanted if w in have]


def cheapest(models: list[ModelRef]) -> ModelRef | None:
    """The lowest-priced *priced* model; free and unpriced ones are skipped (usually rate-limited or unknown)."""
    priced = [m for m in models if m.cost and m.cost.input + m.cost.output > 0]
    return min(priced, key=lambda m: m.cost.input + m.cost.output, default=None)


async def local_models(providers: dict, client: httpx.AsyncClient | None = None) -> list[ModelRef]:
    """Models offered by the configured OpenAI-compatible servers. A server that doesn't answer is skipped: it may simply not be running."""
    found: list[ModelRef] = []
    async with (client or httpx.AsyncClient(timeout=2)) as http:
        for name, provider in providers.items():
            ids = list(provider.models)
            if not ids:
                try:
                    response = await http.get(provider.base_url.rstrip("/") + "/models")
                    response.raise_for_status()
                    ids = [m["id"] for m in response.json().get("data", [])]
                except (httpx.HTTPError, ValueError, KeyError):
                    continue
            found += [ModelRef(provider=name, id=i, name=i, vision=provider.vision) for i in ids]
    return found


GATEWAY_KEY_ENVS = ("PYDANTIC_AI_GATEWAY_API_KEY", "PAIG_API_KEY")


def has_gateway_key(env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return any(env.get(name) for name in GATEWAY_KEY_ENVS)


def gateway_models(gateway, env: dict[str, str] | None = None) -> list[ModelRef]:
    """The configured gateway models, offered only when there is a gateway key to use them with."""
    if not has_gateway_key(env):
        return []
    return [ModelRef(provider="gateway", id=m, name=f"{m.partition('/')[2] or m} (gateway)", vision=gateway.vision) for m in gateway.models]
