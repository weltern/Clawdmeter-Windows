"""Unit tests for plan_pricing: the live refresh of subscription-plan prices
(the other half of ROI, alongside pricing/pricing_refresh's per-token rates).

No real network is touched -- parse_plan_prices() is always fed a small,
hand-built HTML snippet mirroring claude.com/pricing's actual data-plan
anchors, never fetch_plan_page().
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import plan_pricing  # noqa: E402

# A trimmed stand-in for claude.com/pricing's real markup: same data-plan
# anchors and attribute soup, none of the surrounding marketing copy.
SAMPLE_HTML = """
<div data-plan="free" class="card_pricing_price_amount">$0</div>
<div data-plan="pro_annual" data-plan-field="amount_per_month" class="card_pricing_price_amount">$17</div>
<span data-plan="pro_annual" data-plan-field="amount_total">$200</span>
<div data-plan="pro_monthly" data-tier-price="" class="card_pricing_price_amount">$20</div>
<div data-plan="max_5x_monthly" data-tier-price="" class="card_pricing_price_amount">From $100</div>
"""


def test_parse_plan_prices_happy_path():
    prices = plan_pricing.parse_plan_prices(SAMPLE_HTML)
    assert prices["default_claude_pro"] == {"amount": 20.0, "source": "live"}
    assert prices["default_claude_max_5x"] == {"amount": 100.0, "source": "live"}


def test_parse_plan_prices_derives_max_20x():
    prices = plan_pricing.parse_plan_prices(SAMPLE_HTML)
    assert prices["default_claude_max_20x"] == {
        "amount": 100.0 * plan_pricing.MAX_20X_MULTIPLIER, "source": "derived"}


def test_parse_plan_prices_missing_anchor_raises():
    html = '<div data-plan="pro_monthly">$20</div>'   # max_5x_monthly absent
    with pytest.raises(ValueError, match="max_5x_monthly"):
        plan_pricing.parse_plan_prices(html)


def test_parse_plan_prices_non_positive_raises():
    html = ('<div data-plan="pro_monthly">$0</div>'
            '<div data-plan="max_5x_monthly">From $100</div>')
    with pytest.raises(ValueError, match="not a positive number"):
        plan_pricing.parse_plan_prices(html)


def test_parse_plan_prices_zero_models_style_page_change_raises():
    # A total page redesign (no recognizable anchors at all) must refuse
    # rather than silently produce an empty/wrong result.
    with pytest.raises(ValueError):
        plan_pricing.parse_plan_prices("<html><body>redesigned page</body></html>")


# --- file I/O + loader/accessor --------------------------------------------

def test_write_and_load_existing_round_trip(tmp_path):
    path = tmp_path / "plan_prices.json"
    prices = plan_pricing.parse_plan_prices(SAMPLE_HTML)
    plan_pricing.write_plan_prices(prices, path)
    assert plan_pricing.load_existing(path) == prices


def test_load_existing_missing_file_returns_empty(tmp_path):
    assert plan_pricing.load_existing(tmp_path / "does_not_exist.json") == {}


def test_plan_amount_uses_override_then_falls_back(tmp_path):
    assert plan_pricing.plan_amount("default_claude_pro") is None   # no override yet

    path = tmp_path / "plan_prices.json"
    plan_pricing.write_plan_prices(
        {"default_claude_pro": {"amount": 25.0, "source": "live"}}, path)
    plan_pricing.set_override_path(path)
    try:
        assert plan_pricing.plan_amount("default_claude_pro") == 25.0
        assert plan_pricing.plan_amount("default_claude_max_5x") is None   # not in override
    finally:
        plan_pricing.set_override_path(None)

    assert plan_pricing.plan_amount("default_claude_pro") is None   # cleared


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
