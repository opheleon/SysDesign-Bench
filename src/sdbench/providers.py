"""Provider adapters using official SDKs (no litellm).

Model specs are `provider/model-id`:
    anthropic/claude-fable-5
    openai/gpt-5.6-sol
    xai/grok-4.5                     (OpenAI-compatible endpoint)
    baseten/zai-org/GLM-5.2          (OpenAI-compatible endpoint)

Two SDKs cover everything: `anthropic` for Anthropic, `openai` for OpenAI and
every OpenAI-compatible endpoint (xAI, Baseten, or a custom --api-base).

Reasoning effort: `high` maps to Anthropic extended thinking (budget tokens)
and OpenAI-style `reasoning_effort`; OpenAI-compatible servers that reject the
parameter get one retry without it. Every request carries a hard timeout — a
hung HTTPS call must never stall a run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

REQUEST_TIMEOUT_SECONDS = 600

# Sampling caps. Anthropic non-streaming requests are limited to ~21K
# max_tokens, so 20000 with a 12000-token thinking budget stays inside it.
MAX_OUTPUT_TOKENS = 20000
ANTHROPIC_THINKING_BUDGETS = {"low": 2048, "medium": 8192, "high": 12000}

# USD per million tokens (input, output) — mirrors prodgrade config/config.yaml.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.5": (5.0, 30.0),
    "zai-org/GLM-5.2": (1.5, 4.5),
    "grok-4.5": (2.0, 6.0),
}

OPENAI_COMPAT_ENDPOINTS = {
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "baseten": ("https://inference.baseten.co/v1", "BASETEN_API_KEY"),
}


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cost_usd + other.cost_usd,
        )


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage


# complete(messages) -> Completion; model/provider/effort are bound at build time
CompleteFn = Callable[[list[dict]], Completion]


def compute_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = PRICING_USD_PER_MTOK.get(model_id)
    if rates is None:
        return 0.0
    return (prompt_tokens * rates[0] + completion_tokens * rates[1]) / 1_000_000


def estimate_cost(model_id: str, prompt_tokens: int, assumed_completion_tokens: int) -> float:
    return compute_cost(model_id, prompt_tokens, assumed_completion_tokens)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"environment variable {name} is not set")
    return value


def parse_model_spec(spec: str) -> tuple[str, str]:
    """'provider/model-id' -> (provider, model-id); model-id may contain '/'."""
    provider, sep, model_id = spec.partition("/")
    if not sep or not model_id:
        raise ValueError(f"model spec must be 'provider/model-id', got '{spec}'")
    return provider, model_id


def _anthropic_complete(model_id: str, reasoning: str | None) -> CompleteFn:
    import anthropic  # noqa: PLC0415 — optional [run] dependency

    client = anthropic.Anthropic(
        api_key=_require_env("ANTHROPIC_API_KEY"), timeout=REQUEST_TIMEOUT_SECONDS
    )

    def complete(messages: list[dict]) -> Completion:
        kwargs: dict = {}
        if reasoning:
            # Claude 5 family / Opus 4.8+: adaptive thinking with effort control.
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["extra_body"] = {"output_config": {"effort": reasoning}}
            # thinking requires default temperature
        else:
            kwargs["temperature"] = 0.0
        try:
            response = client.messages.create(
                model=model_id,
                max_tokens=MAX_OUTPUT_TOKENS,
                messages=messages,
                **kwargs,
            )
        except anthropic.BadRequestError as exc:
            # Older Anthropic models predate adaptive thinking and want the
            # budget-based form instead.
            if not reasoning or "adaptive" not in str(exc):
                raise
            response = client.messages.create(
                model=model_id,
                max_tokens=MAX_OUTPUT_TOKENS,
                messages=messages,
                thinking={
                    "type": "enabled",
                    "budget_tokens": ANTHROPIC_THINKING_BUDGETS[reasoning],
                },
            )
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = Usage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            cost_usd=compute_cost(
                model_id, response.usage.input_tokens, response.usage.output_tokens
            ),
        )
        return Completion(text=text, usage=usage)

    return complete


def _openai_complete(
    model_id: str,
    reasoning: str | None,
    base_url: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
) -> CompleteFn:
    import openai  # noqa: PLC0415 — optional [run] dependency

    client = openai.OpenAI(
        api_key=_require_env(api_key_env),
        base_url=base_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    official = base_url is None

    def complete(messages: list[dict]) -> Completion:
        kwargs: dict = {}
        if official:
            kwargs["max_completion_tokens"] = MAX_OUTPUT_TOKENS
        else:
            kwargs["max_tokens"] = MAX_OUTPUT_TOKENS
        if reasoning:
            kwargs["reasoning_effort"] = reasoning
        else:
            kwargs["temperature"] = 0.0

        # Progressive parameter stripping: OpenAI-compatible servers and newer
        # official models reject different sampling params. One retry per
        # offender, in the order providers actually complain about them.
        for _ in range(3):
            try:
                response = client.chat.completions.create(
                    model=model_id, messages=messages, **kwargs
                )
                break
            except openai.BadRequestError as exc:
                message = str(exc)
                stripped = False
                for param in ("reasoning_effort", "temperature", "max_completion_tokens", "max_tokens"):
                    if param in kwargs and param in message:
                        kwargs.pop(param)
                        stripped = True
                        break
                if not stripped:
                    raise
        else:  # pragma: no cover
            raise RuntimeError("exhausted parameter-stripping retries")

        text = response.choices[0].message.content or ""
        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        completion_tokens = response.usage.completion_tokens if response.usage else 0
        # Some OpenAI-compatible providers (verified: xAI) report reasoning
        # tokens SEPARATELY from completion_tokens even though they bill as
        # output; official OpenAI includes them in completion_tokens. Fold
        # them in on compat endpoints so cost accounting is honest.
        if not official and response.usage is not None:
            details = getattr(response.usage, "completion_tokens_details", None)
            reasoning_tokens = getattr(details, "reasoning_tokens", None) or 0
            completion_tokens += reasoning_tokens
        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=compute_cost(model_id, prompt_tokens, completion_tokens),
        )
        return Completion(text=text, usage=usage)

    return complete


def build_complete_fn(
    model_spec: str,
    reasoning: str | None = None,
    api_base: str | None = None,
    api_key_env: str | None = None,
) -> CompleteFn:
    """Build a completion function for a `provider/model-id` spec.

    `api_base`/`api_key_env` override the endpoint for custom OpenAI-compatible
    servers (provider is then treated as openai-compatible).
    """
    provider, model_id = parse_model_spec(model_spec)

    if api_base:
        return _openai_complete(
            model_id, reasoning, base_url=api_base, api_key_env=api_key_env or "OPENAI_API_KEY"
        )
    if provider == "anthropic":
        return _anthropic_complete(model_id, reasoning)
    if provider == "openai":
        return _openai_complete(model_id, reasoning)
    if provider in OPENAI_COMPAT_ENDPOINTS:
        base_url, key_env = OPENAI_COMPAT_ENDPOINTS[provider]
        return _openai_complete(model_id, reasoning, base_url=base_url, api_key_env=key_env)
    raise ValueError(
        f"unknown provider '{provider}' — use anthropic/, openai/, xai/, baseten/, "
        "or pass --api-base for a custom OpenAI-compatible endpoint"
    )
