"""What went wrong, said the way an artist needs to hear it.

The model call, the MCP servers and background helpers all fail in different, technical ways when the network is
down. This maps them to one message and one `action` the pane knows how to offer (a button, a hint).
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import httpx
from pydantic_ai import exceptions as ai
from pydantic_ai import exceptions as ai_exceptions

from urllib.parse import urlsplit

KNOWN_ENDPOINTS = {
    "gateway": "https://gateway-us.pydantic.dev/proxy",  # the real region comes from the key; a failed request names its own host
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com",
    "ollama": "http://localhost:11434/v1",
}


KEY_ENVS = {
    "gateway": "PYDANTIC_AI_GATEWAY_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
}


def key_env(provider: str | None) -> str:
    """The environment variable holding this provider's API key."""
    return KEY_ENVS.get(provider or "", f"{(provider or 'PROVIDER').upper().replace('-', '_')}_API_KEY")


def where_to_fix_key(provider: str | None) -> str:
    which = f"a valid {key_env(provider)}" if provider else "the model provider's API key"
    base = f"Set {which} in the agent core's environment (on NixOS: services.tech-art-agent.environmentFile)"
    if provider == "gateway":
        return base + ", or create a new key in Logfire under the organization's Gateway settings"
    return base


def endpoint_for(model_key: str | None, providers: dict | None = None) -> str | None:
    """Where this model's server lives: a configured provider's baseUrl, else the well-known address of a hosted one."""
    provider = (model_key or "").partition("/")[0]
    configured = (providers or {}).get(provider)
    return (configured.base_url if configured is not None else None) or KNOWN_ENDPOINTS.get(provider)


def is_local(url: str | None) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    return host in ("localhost", "::1") or host.endswith(".local") or host.startswith(("127.", "10.", "192.168.")) or host == "host.docker.internal"


def unreachable_message(url: str | None) -> str:
    where = urlsplit(url or "").netloc or "the model server"
    if is_local(url):
        return (
            f"Can't reach your local model server at {where}. Is it running? "
            "(Ollama: `ollama serve`; LM Studio / llama.cpp: start the server.) Nothing in your scene was changed by this; "
            "try again once it's up."
        )
    return (
        f"Can't reach the model provider ({where}). Check your internet connection or the provider's status, or pick a local "
        "model from the model menu. Nothing in your scene was changed by this; try again when it's reachable."
    )


def leaves(error: BaseException) -> Iterator[BaseException]:
    """The real errors inside any (nested) ExceptionGroup, as anyio and the MCP client raise them."""
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            yield from leaves(inner)
    else:
        yield error
        if error.__cause__ is not None and error.__cause__ is not error:
            yield from leaves(error.__cause__)


def is_unreachable(error: BaseException) -> bool:
    network = (ai.ModelConnectionError, ai.ModelTimeoutError, httpx.TransportError, ConnectionError, TimeoutError, socket.gaierror)
    return any(isinstance(e, network) for e in leaves(error))


def request_url(error: BaseException) -> str | None:
    """The URL a failed HTTP call was heading for, when the exception chain remembers it."""
    for e in leaves(error):
        try:
            return str(e.request.url) if isinstance(e, httpx.RequestError) else None
        except RuntimeError:  # httpx raises if no request was attached
            continue
    return None


def _body_excerpt(body: object, limit: int = 200) -> str:
    """The provider's own explanation, flattened to one short line."""
    text = " ".join(str(body).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def gateway_route_of(model_key: str | None) -> str | None:
    """The custom gateway endpoint slug in `gateway/<format>@<slug>/<model>`, else None."""
    parts = (model_key or "").split("/", 2)
    return parts[1].partition("@")[2] or None if len(parts) > 1 and parts[0] == "gateway" else None


def describe(error: BaseException, endpoint: str | None = None, provider: str | None = None, model_key: str | None = None) -> tuple[str, str | None]:
    """(message for the pane, action) — action is "unreachable", "login", "retry" or None."""
    if is_unreachable(error):
        return unreachable_message(request_url(error) or endpoint), "unreachable"
    # pydantic-ai refuses to even build a model when its key is not set: say which key, for which provider.
    if any(isinstance(e, ai_exceptions.UserError) and "API_KEY" in str(e).upper() and "environment variable" in str(e) for e in leaves(error)):
        return f"No API key for {provider or 'the model provider'}. {where_to_fix_key(provider)}, then try again.", "login"
    found = list(leaves(error))
    if any(isinstance(e, ai.ModelAuthenticationError) for e in found) or any(isinstance(e, ai.ModelHTTPError) and e.status_code in (401, 403) for e in found):
        return f"The model provider rejected the API key. {where_to_fix_key(provider)}, then try again.", "login"
    if any(isinstance(e, (ai.ModelRateLimitError, ai.ModelQuotaExceededError)) or (isinstance(e, ai.ModelHTTPError) and e.status_code == 429) for e in found):
        if provider == "gateway":
            return (
                "The gateway turned this request down: this key has probably reached a spend limit (or is being rate limited). "
                "Check the key's limits in Logfire under the organization's Gateway settings, or wait for the period to roll over, "
                "then try again.",
                "retry",
            )
        return "The model provider is rate limiting or out of credit for this key. Wait a moment, or check the account, then try again.", "retry"
    if any(isinstance(e, (ai.ModelOverloadedError, ai.ModelUnavailableError, ai.ModelServerError)) or (isinstance(e, ai.ModelHTTPError) and e.status_code >= 500) for e in found):
        return "The model provider is having trouble right now. Try again in a moment, or pick another model.", "retry"
    http = next((e for e in found if isinstance(e, ai.ModelHTTPError) and e.status_code in (400, 404)), None)
    refused = http is not None or any(isinstance(e, ai.ModelNotFoundError) for e in found)
    if provider == "gateway" and refused:
        route = gateway_route_of(model_key)
        status = http.status_code if http is not None else 404
        detail = _body_excerpt(http.body) if http is not None else ""
        if route and status == 404:
            return (
                f"The gateway has no endpoint with the slug '{route}'. Check the slug in Logfire's Gateway settings (it must match "
                f"exactly), or that the endpoint is active. Or pick another model.",
                None,
            )
        if route:  # a custom provider answers 400 when it has no pricing data for the model
            return (
                f"The custom endpoint '{route}' refused this model (HTTP {status}{': ' + detail if detail else ''}). Most often the "
                "gateway has no prices for it: switch 'Require pricing data' off for that provider in Logfire's Gateway settings "
                "(its cost then does not count toward the gateway's spend limits). Otherwise check the provider's key. Or pick another model.",
                None,
            )
        if status == 404:
            return "The gateway has no pricing data for this model, or does not offer it. Pick another model from the menu.", None
        return f"The gateway or the provider rejected the request (HTTP {status}{': ' + detail if detail else ''}).", None
    if any(isinstance(e, ai.ModelNotFoundError) for e in found):
        return "That model isn't available from the provider. Pick another one from the model menu.", None
    return str(error), None


_NETWORK_HOSTS = (("pypi.org", 443), ("github.com", 443))


def network_up(timeout: float = 1.0, hosts: tuple[tuple[str, int], ...] = _NETWORK_HOSTS) -> bool:
    """Whether PyPI/GitHub answer. Only decides if the tool servers (uvx) may update or must use their cache; turns never wait on it."""
    for host, port in hosts:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False
