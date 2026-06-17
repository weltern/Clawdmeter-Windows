"""Unit tests for the pricing package: the rate-card markdown parser, the
name->id mapping, validation refusing malformed/empty parses, the diff logic, and
the loader lookup.

Run with `python -m pytest tests/ -q`, or directly:
`python tests/test_pricing.py`. No real network is touched — the parser is fed a
committed fixture (tests/fixtures/pricing_sample.md), never fetch_rate_card().
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pricing  # noqa: E402
from pricing import updater  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pricing_sample.md"


@pytest.fixture(scope="module")
def sample_md() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed(sample_md: str) -> dict:
    return updater.parse_rate_card(sample_md)


# --- parser ---------------------------------------------------------------

def test_parse_finds_all_models(parsed: dict):
    # Model-pricing rows in the fixture (by cleaned display name).
    expected = {
        "Claude Fable 5", "Claude Mythos 5", "Claude Opus 4.8", "Claude Opus 4.5",
        "Claude Opus 4.1", "Claude Opus 4", "Claude Haiku 3.5",
        # Fast mode introduces these two with no base-pricing row in the fixture:
        "Claude Opus 4.6", "Claude Opus 4.7",
    }
    assert expected.issubset(set(parsed))


def test_parse_prices_strip_dollar_and_mtok(parsed: dict):
    opus = parsed["Claude Opus 4.8"]
    assert opus["input"] == 5.0
    assert opus["output"] == 25.0
    assert opus["cache_write_5m"] == 6.25
    assert opus["cache_write_1h"] == 10.0
    assert opus["cache_read"] == 0.5


def test_parse_decimal_and_sub_dollar_values(parsed: dict):
    haiku = parsed["Claude Haiku 3.5"]
    assert haiku["input"] == 0.8       # "$0.80 / MTok"
    assert haiku["cache_read"] == 0.08
    assert haiku["output"] == 4.0


def test_parse_batch_columns(parsed: dict):
    assert parsed["Claude Fable 5"]["batch_input"] == 5.0
    assert parsed["Claude Fable 5"]["batch_output"] == 25.0
    assert parsed["Claude Opus 4.8"]["batch_input"] == 2.5
    assert parsed["Claude Opus 4.8"]["batch_output"] == 12.5


def test_parse_status_from_annotation(parsed: dict):
    assert parsed["Claude Opus 4.8"]["status"] == "active"
    assert parsed["Claude Opus 4.1"]["status"] == "deprecated"
    assert parsed["Claude Opus 4"]["status"] == "retired"
    assert parsed["Claude Haiku 3.5"]["status"] == "retired"


def test_parse_cleans_display_name_links_and_annotations(parsed: dict):
    # "Claude Mythos 5 ([limited availability](...))" -> "Claude Mythos 5"
    assert parsed["Claude Mythos 5"]["display_name"] == "Claude Mythos 5"
    assert "(" not in parsed["Claude Opus 4.1"]["display_name"]


def test_parse_fast_mode_combined_row_fans_out(parsed: dict):
    # "Claude Opus 4.6 / Claude Opus 4.7" -> both get the same fast-mode rates.
    for name in ("Claude Opus 4.6", "Claude Opus 4.7"):
        assert parsed[name]["fast_mode_input"] == 30.0
        assert parsed[name]["fast_mode_output"] == 150.0
    assert parsed["Claude Opus 4.8"]["fast_mode_input"] == 10.0
    assert parsed["Claude Opus 4.8"]["fast_mode_output"] == 50.0


def test_parse_fast_mode_only_on_expected_models(parsed: dict):
    assert "fast_mode_input" not in parsed["Claude Opus 4.5"]
    assert "fast_mode_input" not in parsed["Claude Haiku 3.5"]


# --- name -> id mapping ---------------------------------------------------

def test_map_known_names():
    assert updater.map_name_to_id("Claude Opus 4.8") == "claude-opus-4-8"
    assert updater.map_name_to_id("Claude Haiku 3.5") == "claude-3-5-haiku-20241022"
    assert updater.map_name_to_id("Claude Opus 4") == "claude-opus-4-0"


def test_map_unknown_name_is_slugified_and_visible(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        key = updater.map_name_to_id("Claude Imaginary 9")
    assert key == "unmapped-claude-imaginary-9"
    assert any("unmapped" in rec.message.lower() for rec in caplog.records)


# --- validation -----------------------------------------------------------

def test_build_rejects_empty_parse():
    with pytest.raises(ValueError, match="zero models"):
        updater.build_price_map({})


def test_build_rejects_non_numeric_required_field():
    bad = {
        "Claude Opus 4.8": {
            "display_name": "Claude Opus 4.8", "status": "active",
            "input": None, "output": 25.0, "cache_write_5m": 6.25,
            "cache_write_1h": 10.0, "cache_read": 0.5,
            "batch_input": 2.5, "batch_output": 12.5,
        }
    }
    with pytest.raises(ValueError, match="not numeric"):
        updater.build_price_map(bad)


def test_build_rejects_non_positive_value():
    bad = {
        "Claude Opus 4.8": {
            "display_name": "Claude Opus 4.8", "status": "active",
            "input": 0, "output": 25.0, "cache_write_5m": 6.25,
            "cache_write_1h": 10.0, "cache_read": 0.5,
            "batch_input": 2.5, "batch_output": 12.5,
        }
    }
    with pytest.raises(ValueError, match="not positive"):
        updater.build_price_map(bad)


def test_build_full_map_from_fixture(parsed: dict):
    pm = updater.build_price_map(parsed, fetched_at="2026-06-16")
    assert pm["currency"] == "USD"
    assert pm["unit"] == "per_mtok"
    assert pm["fetched_at"] == "2026-06-16"
    assert pm["multipliers"]["batch"] == 0.5
    assert pm["surcharges"]["web_search_per_1k_searches"] == 10.0
    # keyed by API model id, joins with usage data
    assert "claude-opus-4-8" in pm["models"]
    assert pm["models"]["claude-opus-4-8"]["input"] == 5.0
    assert pm["models"]["claude-opus-4-8"]["fast_mode_output"] == 50.0


def test_build_preserves_seed_key_order(parsed: dict):
    pm = updater.build_price_map(parsed, fetched_at="2026-06-16")
    ids = list(pm["models"])
    # fable before opus-4-8 before haiku-3.5, matching the seed order
    assert ids.index("claude-fable-5") < ids.index("claude-opus-4-8")
    assert ids.index("claude-opus-4-8") < ids.index("claude-3-5-haiku-20241022")


# --- diff -----------------------------------------------------------------

def test_diff_detects_added_removed_changed():
    old = {"models": {
        "claude-opus-4-8": {"input": 5.0, "output": 25.0},
        "claude-old": {"input": 1.0},
    }}
    new = {"models": {
        "claude-opus-4-8": {"input": 6.0, "output": 25.0},  # input changed
        "claude-new": {"input": 2.0},                        # added
    }}
    diff = updater.diff_maps(old, new)
    assert diff["added"] == ["claude-new"]
    assert diff["removed"] == ["claude-old"]
    assert diff["changed"] == {"claude-opus-4-8": {"input": (5.0, 6.0)}}
    assert updater.has_changes(diff) is True


def test_diff_ignores_fetched_at_and_identical_models():
    old = {"fetched_at": "2026-01-01", "models": {"x": {"input": 1.0}}}
    new = {"fetched_at": "2026-06-16", "models": {"x": {"input": 1.0}}}
    diff = updater.diff_maps(old, new)
    assert updater.has_changes(diff) is False
    assert "No pricing changes" in updater.format_diff(diff)


# --- loader / accessor ----------------------------------------------------

def test_loader_reads_bundled_map():
    pricing.load_price_map.cache_clear()
    pm = pricing.load_price_map()
    assert pm["currency"] == "USD"
    assert "claude-opus-4-8" in pm["models"]


def test_model_rates_known_and_unknown():
    rates = pricing.model_rates("claude-opus-4-8")
    assert rates is not None
    assert rates["input"] == 5
    assert rates["output"] == 25
    assert pricing.model_rates("claude-does-not-exist") is None


def test_bundled_map_matches_validator_rules():
    # The committed seed must itself pass validation (positive numeric fields).
    pricing.load_price_map.cache_clear()
    pm = pricing.load_price_map()
    for api_id, fields in pm["models"].items():
        assert updater._validate_model(api_id, fields) == [], api_id


def test_bundled_map_is_stable_json_format():
    # 2-space indent + trailing newline, so the updater's writes are no-ops on
    # an already-current file (keeps git diffs clean).
    text = pricing.price_map_path().read_text(encoding="utf-8")
    data = json.loads(text)
    assert text == json.dumps(data, indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
