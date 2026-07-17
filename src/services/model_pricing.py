"""Approximate model pricing for the session cost estimate (Phase 8.2).

Prices are **estimates**, per 1M tokens, in USD. They drift over time — tokens
don't — so every UI label built on this marks the dollar figure as an estimate.
Maintained by hand: update ``_BASE_RATES`` when providers change list prices.

Each entry carries its own cache multipliers because providers differ:
    Anthropic: cache-write (5-minute TTL) = 1.25 x input, cache-read = 0.10 x input
    OpenAI:    cache-read = 0.10 x input; cache-write free before gpt-5.6,
               1.25 x input from gpt-5.6 on (moot for Codex history today —
               its usage events report no write bucket, so cache_creation = 0)

Sources: the ``claude-api`` skill's model/pricing tables (Opus 4.8 $5/$25,
Sonnet 5 $3/$15, Haiku 4.5 $1/$5, Fable 5 $10/$50, Opus 4.x $5/$25, older
Opus $15/$75; Sonnet 5's intro rate through 2026-08-31 deliberately ignored),
and OpenAI list prices as of 2026-07 (GPT-5.6 Sol $5/$30, Terra $2.50/$15,
Luna $1/$6; GPT-5.5 $5/$30; GPT-5.4 $2.50/$15, mini $0.75/$4.50,
nano $0.20/$1.25).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import TokenUsage

_ANTHROPIC_CACHE = (1.25, 0.10)  # (write mult, read mult) off the input rate
_OPENAI_CACHE = (1.25, 0.10)     # gpt-5.6+: writes billed like Anthropic
_OPENAI_CACHE_FREE_WRITE = (0.0, 0.10)  # pre-5.6 gpt: cache writes not billed

# model id -> (input $/1M, output $/1M, cache-write mult, cache-read mult)
_BASE_RATES: dict[str, tuple[float, float, float, float]] = {
    "claude-fable-5": (10.0, 50.0, *_ANTHROPIC_CACHE),
    "claude-mythos-5": (10.0, 50.0, *_ANTHROPIC_CACHE),
    "claude-opus-4-8": (5.0, 25.0, *_ANTHROPIC_CACHE),
    "claude-opus-4-7": (5.0, 25.0, *_ANTHROPIC_CACHE),
    "claude-opus-4-6": (5.0, 25.0, *_ANTHROPIC_CACHE),
    "claude-opus-4-5": (5.0, 25.0, *_ANTHROPIC_CACHE),
    "claude-opus-4-1": (15.0, 75.0, *_ANTHROPIC_CACHE),
    "claude-opus-4-0": (15.0, 75.0, *_ANTHROPIC_CACHE),
    "claude-sonnet-5": (3.0, 15.0, *_ANTHROPIC_CACHE),
    "claude-sonnet-4-6": (3.0, 15.0, *_ANTHROPIC_CACHE),
    "claude-sonnet-4-5": (3.0, 15.0, *_ANTHROPIC_CACHE),
    "claude-sonnet-4-0": (3.0, 15.0, *_ANTHROPIC_CACHE),
    "claude-haiku-4-5": (1.0, 5.0, *_ANTHROPIC_CACHE),
    "gpt-5.6-sol": (5.0, 30.0, *_OPENAI_CACHE),
    "gpt-5.6-terra": (2.5, 15.0, *_OPENAI_CACHE),
    "gpt-5.6-luna": (1.0, 6.0, *_OPENAI_CACHE),
    "gpt-5.5": (5.0, 30.0, *_OPENAI_CACHE_FREE_WRITE),
    "gpt-5.4": (2.5, 15.0, *_OPENAI_CACHE_FREE_WRITE),
    "gpt-5.4-mini": (0.75, 4.5, *_OPENAI_CACHE_FREE_WRITE),
    "gpt-5.4-nano": (0.2, 1.25, *_OPENAI_CACHE_FREE_WRITE),
}

# Fallback rates by model family, for ids not listed above (e.g. a newer dated
# variant): keyed by a substring tested against the model id, **in order** —
# more specific needles must precede their prefixes (mini before gpt-5.4,
# codex before any generic gpt needle).
_FAMILY_RATES: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("fable", (10.0, 50.0, *_ANTHROPIC_CACHE)),
    ("mythos", (10.0, 50.0, *_ANTHROPIC_CACHE)),
    ("opus", (5.0, 25.0, *_ANTHROPIC_CACHE)),
    ("sonnet", (3.0, 15.0, *_ANTHROPIC_CACHE)),
    ("haiku", (1.0, 5.0, *_ANTHROPIC_CACHE)),
    # Codex-tuned models (gpt-5.3-codex, ...) have no published per-token list
    # price (subscription usage); assume the mid tier so the estimate stays an
    # estimate, not zero. The needle is "-codex" so the reader's synthetic
    # "codex-unknown" id does NOT match and stays honestly unpriced (partial).
    ("-codex", (2.5, 15.0, *_OPENAI_CACHE)),
    ("gpt-5.6-sol", (5.0, 30.0, *_OPENAI_CACHE)),
    ("gpt-5.6-luna", (1.0, 6.0, *_OPENAI_CACHE)),
    ("gpt-5.6", (2.5, 15.0, *_OPENAI_CACHE)),  # terra is the default tier
    ("gpt-5.5", (5.0, 30.0, *_OPENAI_CACHE_FREE_WRITE)),
    ("gpt-5.4-mini", (0.75, 4.5, *_OPENAI_CACHE_FREE_WRITE)),
    ("gpt-5.4-nano", (0.2, 1.25, *_OPENAI_CACHE_FREE_WRITE)),
    ("gpt-5.4", (2.5, 15.0, *_OPENAI_CACHE_FREE_WRITE)),
)


@dataclass
class CostEstimate:
    """A dollar estimate plus whether any model went unpriced."""

    dollars: float
    is_partial: bool  # True if at least one model had no known price


def _rate(model_id: str) -> tuple[float, float, float, float] | None:
    """(input, output, cache-write mult, cache-read mult) or None if unknown."""
    rate = _BASE_RATES.get(model_id)
    if rate is not None:
        return rate
    for needle, family_rate in _FAMILY_RATES:
        if needle in model_id:
            return family_rate
    return None


def cost_for_model(model_id: str, usage: TokenUsage) -> float | None:
    """Estimated USD for one model's usage, or None if the model is unpriced."""
    rate = _rate(model_id)
    if rate is None:
        return None
    in_rate, out_rate, write_mult, read_mult = rate
    return (
        usage.input * in_rate
        + usage.output * out_rate
        + usage.cache_creation * in_rate * write_mult
        + usage.cache_read * in_rate * read_mult
    ) / 1_000_000


def estimate_cost(usage_by_model: dict[str, TokenUsage]) -> CostEstimate:
    """Sum the per-model estimates; flag partial if any model is unpriced."""
    total = 0.0
    is_partial = False
    for model_id, usage in usage_by_model.items():
        cost = cost_for_model(model_id, usage)
        if cost is None:
            is_partial = True
            continue
        total += cost
    return CostEstimate(dollars=total, is_partial=is_partial)


def format_cost(estimate: CostEstimate) -> str:
    """A compact, clearly-approximate label, e.g. ``~$0.12 (est.)``."""
    prefix = "~$"
    suffix = "+ (est.)" if estimate.is_partial else " (est.)"
    dollars = estimate.dollars
    if dollars and dollars < 0.01:
        return f"<$0.01{suffix}"
    return f"{prefix}{dollars:,.2f}{suffix}"
