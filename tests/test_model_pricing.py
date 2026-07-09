"""Phase 8.2: model pricing / cost estimate."""
from src.models import TokenUsage
from src.services import model_pricing as mp


def test_known_model_cost_math():
    # Opus 4.8: input $5, output $25, cache-write 1.25x input, cache-read 0.1x input, per 1M.
    usage = TokenUsage(input=1_000_000, output=1_000_000,
                       cache_creation=1_000_000, cache_read=1_000_000)
    cost = mp.cost_for_model("claude-opus-4-8", usage)
    # 5 + 25 + (5 * 1.25) + (5 * 0.10) = 5 + 25 + 6.25 + 0.5
    assert cost == 36.75


def test_multi_model_sum():
    usage = {
        "claude-opus-4-8": TokenUsage(output=1_000_000),      # $25
        "claude-haiku-4-5": TokenUsage(output=1_000_000),     # $5
    }
    est = mp.estimate_cost(usage)
    assert est.dollars == 30.0
    assert est.is_partial is False


def test_unknown_model_counted_partial():
    usage = {
        "claude-opus-4-8": TokenUsage(output=1_000_000),      # $25
        "some-future-model": TokenUsage(output=1_000_000),    # unpriced
    }
    est = mp.estimate_cost(usage)
    assert est.dollars == 25.0       # priced portion still counted
    assert est.is_partial is True    # unknown model flags the estimate as partial


def test_family_fallback_prices_unlisted_variant():
    # A dated/unlisted opus id still prices via the family fallback.
    assert mp.cost_for_model("claude-opus-9-9-20990101",
                             TokenUsage(output=1_000_000)) == 25.0
    assert mp._rate("totally-unknown") is None


def test_format_cost_labels_estimate():
    assert mp.format_cost(mp.CostEstimate(0.1234, False)) == "~$0.12 (est.)"
    assert mp.format_cost(mp.CostEstimate(0.0, False)) == "~$0.00 (est.)"
    assert mp.format_cost(mp.CostEstimate(0.004, False)) == "<$0.01 (est.)"
    # partial estimates get a trailing '+'
    assert mp.format_cost(mp.CostEstimate(1.5, True)) == "~$1.50+ (est.)"
