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


def test_map_claude_sonnet_5():
    assert updater.map_name_to_id("Claude Sonnet 5") == "claude-sonnet-5"


def test_map_prefers_registry_over_name_to_id():
    # NAME_TO_ID's guess is the bare, undated form; Anthropic's own registry
    # (from the Models API) knows the real, dated ID actual usage is billed
    # under -- the registry must win.
    registry = {"Claude Opus 4.5": "claude-opus-4-5-20251101"}
    assert updater.map_name_to_id("Claude Opus 4.5", registry) == "claude-opus-4-5-20251101"
    assert updater.map_name_to_id("Claude Opus 4.5") == "claude-opus-4-5"   # unchanged w/o one


def test_map_falls_back_past_registry_miss():
    # A name absent from the registry (e.g. announced on the rate card before
    # the Models API lists it) still falls through to NAME_TO_ID/slugify.
    registry = {"Claude Opus 4.5": "claude-opus-4-5-20251101"}
    assert updater.map_name_to_id("Claude Opus 4.8", registry) == "claude-opus-4-8"


# --- time-boxed variant resolution -----------------------------------------

def _rates(input_: float) -> dict:
    return {"display_name": "x", "status": "active", "input": input_, "output": input_ * 5,
            "cache_write_5m": input_ * 1.25, "cache_write_1h": input_ * 2,
            "cache_read": input_ * 0.1, "batch_input": input_ * 0.5, "batch_output": input_ * 2.5}


def test_resolve_collapses_through_and_starting_variants_before_transition():
    parsed = {
        "Claude Sonnet 5 through August 31, 2026": _rates(2.0),
        "Claude Sonnet 5 starting September 1, 2026": _rates(3.0),
    }
    resolved = updater.resolve_time_boxed_variants(parsed, today="2026-07-12")
    assert list(resolved.keys()) == ["Claude Sonnet 5"]
    row = resolved["Claude Sonnet 5"]
    assert row["input"] == 2.0                       # today's rate stays active
    expected = _rates(3.0)
    del expected["display_name"], expected["status"]   # parent-level fields, not per-change
    assert row["rate_changes"] == [{"effective_from": "2026-09-01", **expected}]


def test_resolve_promotes_variant_once_its_date_arrives():
    parsed = {
        "Claude Sonnet 5 through August 31, 2026": _rates(2.0),
        "Claude Sonnet 5 starting September 1, 2026": _rates(3.0),
    }
    resolved = updater.resolve_time_boxed_variants(parsed, today="2026-09-15")
    row = resolved["Claude Sonnet 5"]
    assert row["input"] == 3.0                       # the later variant is now current
    assert "rate_changes" not in row


def test_resolve_leaves_unqualified_names_untouched():
    parsed = {"Claude Opus 4.8": _rates(5.0)}
    resolved = updater.resolve_time_boxed_variants(parsed, today="2026-07-12")
    assert resolved == parsed


def test_resolve_keeps_unparseable_date_phrase_as_its_own_row():
    # Not a real date -> must not be silently merged/guessed; it should surface
    # via the normal unmapped-name path like any other unrecognized row.
    parsed = {"Claude Sonnet 5 starting the next ice age": _rates(2.0)}
    resolved = updater.resolve_time_boxed_variants(parsed, today="2026-07-12")
    assert resolved == parsed


def test_build_price_map_carries_rate_changes_through(monkeypatch):
    parsed = {
        "Claude Sonnet 5 through August 31, 2026": _rates(2.0),
        "Claude Sonnet 5 starting September 1, 2026": _rates(3.0),
    }
    pm = updater.build_price_map(parsed, fetched_at="2026-07-12")
    model = pm["models"]["claude-sonnet-5"]
    assert model["input"] == 2.0
    assert model["rate_changes"][0]["effective_from"] == "2026-09-01"
    assert updater._validate_model("claude-sonnet-5", model) == []


def test_rate_changes_entries_never_carry_display_name_or_status():
    # A scheduled change's own raw, still-qualified name ("... starting
    # September 1, 2026") must never ride along -- it belongs to the parent
    # model, not to an individual future rate (regression test for the bug
    # where model_rates() promoting a change clobbered the clean model name).
    parsed = {
        "Claude Sonnet 5 through August 31, 2026": _rates(2.0),
        "Claude Sonnet 5 starting September 1, 2026": _rates(3.0),
    }
    pm = updater.build_price_map(parsed, fetched_at="2026-07-12")
    change = pm["models"]["claude-sonnet-5"]["rate_changes"][0]
    assert "display_name" not in change
    assert "status" not in change


def test_build_price_map_uses_registry_id_when_given():
    parsed = {"Claude Opus 4.5": _rates(5.0)}
    pm_no_registry = updater.build_price_map(parsed, fetched_at="2026-07-12")
    assert "claude-opus-4-5" in pm_no_registry["models"]          # unchanged guess

    pm_with_registry = updater.build_price_map(
        parsed, fetched_at="2026-07-12",
        registry={"Claude Opus 4.5": "claude-opus-4-5-20251101"})
    assert "claude-opus-4-5-20251101" in pm_with_registry["models"]
    assert "claude-opus-4-5" not in pm_with_registry["models"]


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


def _price_map_with_rate_changes(*changes: dict) -> dict:
    return {
        "currency": "USD", "unit": "per_mtok", "source": "test", "fetched_at": "2026-01-01",
        "models": {"claude-sonnet-5": {
            "display_name": "Claude Sonnet 5", "status": "active",
            "input": 2.0, "output": 10.0, "cache_write_5m": 2.5, "cache_write_1h": 4.0,
            "cache_read": 0.2, "batch_input": 1.0, "batch_output": 5.0,
            "rate_changes": list(changes),
        }},
        "multipliers": {}, "surcharges": {},
    }


def test_model_rates_applies_already_effective_rate_change(tmp_path):
    cache = tmp_path / "price_map.json"
    updater.write_price_map(_price_map_with_rate_changes(
        {"effective_from": "2020-01-01", "input": 1.0, "output": 5.0, "cache_write_5m": 1.25,
         "cache_write_1h": 2.0, "cache_read": 0.1, "batch_input": 0.5, "batch_output": 2.5},
    ), cache)
    pricing.set_override_path(cache)
    try:
        rates = pricing.model_rates("claude-sonnet-5")
        assert rates["input"] == 1.0        # 2020-01-01 has long since passed
        assert "rate_changes" not in rates  # merged entry doesn't leak the schedule
    finally:
        pricing.set_override_path(None)


def test_model_rates_ignores_not_yet_effective_rate_change(tmp_path):
    cache = tmp_path / "price_map.json"
    updater.write_price_map(_price_map_with_rate_changes(
        {"effective_from": "2099-01-01", "input": 9.0, "output": 45.0, "cache_write_5m": 11.25,
         "cache_write_1h": 18.0, "cache_read": 0.9, "batch_input": 4.5, "batch_output": 22.5},
    ), cache)
    pricing.set_override_path(cache)
    try:
        rates = pricing.model_rates("claude-sonnet-5")
        assert rates["input"] == 2.0        # 2099-01-01 hasn't happened -> unchanged
    finally:
        pricing.set_override_path(None)


def test_model_rates_picks_latest_of_multiple_effective_changes(tmp_path):
    cache = tmp_path / "price_map.json"
    updater.write_price_map(_price_map_with_rate_changes(
        {"effective_from": "2020-01-01", "input": 1.0, "output": 5.0, "cache_write_5m": 1.25,
         "cache_write_1h": 2.0, "cache_read": 0.1, "batch_input": 0.5, "batch_output": 2.5},
        {"effective_from": "2021-01-01", "input": 1.5, "output": 7.5, "cache_write_5m": 1.875,
         "cache_write_1h": 3.0, "cache_read": 0.15, "batch_input": 0.75, "batch_output": 3.75},
    ), cache)
    pricing.set_override_path(cache)
    try:
        assert pricing.model_rates("claude-sonnet-5")["input"] == 1.5
    finally:
        pricing.set_override_path(None)


def test_model_rates_promotion_never_overrides_display_name_or_status(tmp_path):
    # Defense in depth: even if a rate_changes entry somehow carries a stray
    # display_name/status (malformed data, a future regression upstream),
    # promoting it must not let those fields override the model's own.
    cache = tmp_path / "price_map.json"
    updater.write_price_map(_price_map_with_rate_changes(
        {"effective_from": "2020-01-01", "display_name": "should not win",
         "status": "should not win either", "input": 1.0, "output": 5.0,
         "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1,
         "batch_input": 0.5, "batch_output": 2.5},
    ), cache)
    pricing.set_override_path(cache)
    try:
        rates = pricing.model_rates("claude-sonnet-5")
        assert rates["display_name"] == "Claude Sonnet 5"
        assert rates["status"] == "active"
        assert rates["input"] == 1.0   # the rate itself still promotes correctly
    finally:
        pricing.set_override_path(None)


def test_load_existing_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "price_map.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert updater.load_existing(path) == {}


def test_load_price_map_falls_back_when_override_is_corrupt(tmp_path):
    corrupt = tmp_path / "price_map.json"
    corrupt.write_text("{not valid json", encoding="utf-8")
    pricing.set_override_path(corrupt)
    try:
        pm = pricing.load_price_map()
        assert pm["currency"] == "USD"
        assert "claude-opus-4-8" in pm["models"]   # the bundled map, not the corrupt override
    finally:
        pricing.set_override_path(None)


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
