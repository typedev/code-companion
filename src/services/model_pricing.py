"""Approximate Claude model pricing for the session cost estimate (Phase 8.2).

Prices are **estimates**, per 1M tokens, in USD. They drift over time — tokens
don't — so every UI label built on this marks the dollar figure as an estimate.
Maintained by hand: update ``_BASE_RATES`` when Anthropic changes list prices.

Only the two list rates (input, output) are stored per model. The two cache
rates follow Anthropic's fixed multipliers off the input rate:
    cache-write (5-minute TTL) = 1.25 x input
    cache-read                 = 0.10 x input
so they never drift out of sync with the input rate.

Source: the ``claude-api`` skill's model/pricing tables (Opus 4.8 $5/$25,
Sonnet 5 $3/$15, Haiku 4.5 $1/$5, Fable 5 $10/$50, Opus 4.x $5/$25, older
Opus $15/$75). Sonnet 5 has an intro rate ($2/$10 through 2026-08-31); the
durable $3/$15 is used here deliberately.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import TokenUsage

# model id -> (input $/1M, output $/1M)
_BASE_RATES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4-0": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4-0": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Fallback rates by model family, for ids not listed above (e.g. a newer dated
# variant): keyed by a substring tested against the model id.
_FAMILY_RATES: tuple[tuple[str, tuple[float, float]], ...] = (
    ("fable", (10.0, 50.0)),
    ("mythos", (10.0, 50.0)),
    ("opus", (5.0, 25.0)),
    ("sonnet", (3.0, 15.0)),
    ("haiku", (1.0, 5.0)),
)

_CACHE_WRITE_MULT = 1.25  # 5-minute TTL
_CACHE_READ_MULT = 0.10


@dataclass
class CostEstimate:
    """A dollar estimate plus whether any model went unpriced."""

    dollars: float
    is_partial: bool  # True if at least one model had no known price


def _rate(model_id: str) -> tuple[float, float] | None:
    """Input/output $/1M for a model id, or None if unknown."""
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
    in_rate, out_rate = rate
    return (
        usage.input * in_rate
        + usage.output * out_rate
        + usage.cache_creation * in_rate * _CACHE_WRITE_MULT
        + usage.cache_read * in_rate * _CACHE_READ_MULT
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
