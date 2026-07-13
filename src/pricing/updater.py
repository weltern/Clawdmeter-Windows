"""Keep ``price_map.json`` current from Anthropic's published rate card.

Fetches the markdown rate card, parses the three pricing tables (Model pricing,
Fast mode, Batch processing), maps the page's human display names back to Claude
API model IDs, validates the result, diffs it against the bundled map, and writes
the updated JSON. Runnable as a CLI:

    python -m pricing.updater            # fetch live, diff, write if changed
    python -m pricing.updater --check    # exit 1 if live differs (no write; for CI)

(Run from ``src/`` or with ``src`` on PYTHONPATH, matching the project's flat module
layout — same as the test imports. From the repo root: ``cd src && python -m
pricing.updater``.)

Network + parsing are split into small pure functions (``fetch_rate_card``,
``parse_rate_card``, ``build_price_map``, ``diff_maps``) so they're unit-testable
without touching the network — mirroring ``poller``/``update_check``. The parser is
deliberately strict: ``build_price_map`` refuses to return a map that's empty or
has a non-numeric required field, so a page reformat can't silently blank the map.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from . import price_map_path

log = logging.getLogger("clawdmeter.pricing.updater")

# The .md variant returns raw markdown (text/markdown); confirmed in use. If it
# ever 404s the page itself would need HTML parsing — flagged but not built, since
# .md is the supported path.
RATE_CARD_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"

_HEADERS = {
    "Accept": "text/markdown, text/plain, */*",
    "User-Agent": "Clawdmeter-pricing-updater",
}

# Static metadata copied through to the written map.
CURRENCY = "USD"
UNIT = "per_mtok"

# Maintained display-name -> API-model-id map, seeded from the bundled table. The
# page prints display names; usage data is keyed by API ID, so this is the join.
# When the page introduces a name not listed here, the model is NOT dropped — it's
# emitted under a slugified key and a warning is logged for a human to fix here.
NAME_TO_ID: dict[str, str] = {
    "Claude Fable 5": "claude-fable-5",
    "Claude Mythos 5": "claude-mythos-5",
    "Claude Sonnet 5": "claude-sonnet-5",
    "Claude Opus 4.8": "claude-opus-4-8",
    "Claude Opus 4.7": "claude-opus-4-7",
    "Claude Opus 4.6": "claude-opus-4-6",
    "Claude Opus 4.5": "claude-opus-4-5",
    "Claude Opus 4.1": "claude-opus-4-1",
    "Claude Opus 4": "claude-opus-4-0",
    "Claude Sonnet 4.6": "claude-sonnet-4-6",
    "Claude Sonnet 4.5": "claude-sonnet-4-5",
    "Claude Sonnet 4": "claude-sonnet-4-0",
    "Claude Haiku 4.5": "claude-haiku-4-5",
    "Claude Haiku 3.5": "claude-3-5-haiku-20241022",
}

# Derivable feature rates and non-token surcharges are not on the parsed tables in
# a per-model form; they're stable policy values carried straight through. Kept
# here (not re-parsed) so a prose reword can't blank them; revisit if the rate card
# changes these numbers.
MULTIPLIERS: dict[str, float] = {
    "cache_write_5m": 1.25,
    "cache_write_1h": 2.0,
    "cache_read": 0.1,
    "batch": 0.5,
    "inference_geo_us": 1.1,
}
SURCHARGES: dict[str, Any] = {
    "_note": ("Usage-based, non-token charges. NOT priced per MTok; do not apply "
              "the token multipliers to these."),
    "web_search_per_1k_searches": 10.0,
    "code_execution_per_container_hour": 0.05,
    "code_execution_note": "1550 free container-hours per organization per month.",
    "managed_agent_session_per_hour": 0.08,
}

# Stable ordering for model keys in the written JSON: follow the bundled order so
# diffs stay small, then append any genuinely new IDs in first-seen order.
_SEED_ORDER = list(NAME_TO_ID.values())

# Required per-model numeric fields the validator insists on (fast_mode_* are
# optional — only some models have them).
_REQUIRED_FIELDS = (
    "input", "output", "cache_write_5m", "cache_write_1h", "cache_read",
    "batch_input", "batch_output",
)


# --- fetching --------------------------------------------------------------

def fetch_rate_card(url: str = RATE_CARD_URL, timeout: float = 30.0) -> str:
    """Fetch the markdown rate card. Raises ``httpx.HTTPError`` on failure (the
    CLI catches it) — unlike the poller we want a loud failure here, since a
    silent empty fetch must never overwrite the bundled map."""
    with httpx.Client(timeout=timeout, headers=_HEADERS,
                      follow_redirects=True) as http:
        resp = http.get(url)
        resp.raise_for_status()
        return resp.text


# --- markdown parsing ------------------------------------------------------

_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")   # [text](url) -> text
_ANNOT_RE = re.compile(r"\s*\(\s*\)")             # leftover empty "( )" after link strip
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _strip_links(text: str) -> str:
    """Replace markdown links with their visible text."""
    return _LINK_RE.sub(r"\1", text)


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into trimmed cells (drops the outer pipes)."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    """True for the |---|:--|---| header-separator row."""
    return all(re.fullmatch(r":?-{2,}:?", c) is not None for c in cells if c)


def _iter_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Collect contiguous table rows starting at the first ``|`` line at/after
    ``start``. Returns (data_rows_without_header_or_separator, next_index)."""
    i = start
    n = len(lines)
    while i < n and not lines[i].lstrip().startswith("|"):
        i += 1
    rows: list[list[str]] = []
    while i < n and lines[i].lstrip().startswith("|"):
        cells = _split_row(lines[i])
        if not _is_separator_row(cells):
            rows.append(cells)
        i += 1
    # Drop the header row (first non-separator row).
    return rows[1:] if rows else [], i


def _find_table_after(lines: list[str], heading_substr: str) -> list[list[str]]:
    """Find the heading line containing ``heading_substr`` and return the first
    table after it. Returns [] if the heading or table is absent."""
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("#") and heading_substr.lower() in line.lower():
            rows, _ = _iter_table(lines, idx + 1)
            return rows
    return []


def _parse_price(cell: str) -> float | None:
    """Pull the first number out of a price cell ('$12.50 / MTok' -> 12.5).
    Returns None if there's no number (so the validator can reject it)."""
    cell = _strip_links(cell).replace("$", "")
    m = _NUM_RE.search(cell)
    return float(m.group()) if m else None


def _clean_model_name(cell: str) -> str:
    """Recover a base display name from a model cell, stripping link/annotation
    noise: 'Claude Opus 4.1 ([deprecated](...))' -> 'Claude Opus 4.1'."""
    text = _strip_links(cell)
    text = _ANNOT_RE.sub("", text)          # drop the now-empty "( )"
    # Drop any remaining parenthetical annotation, e.g. "(deprecated)".
    text = re.sub(r"\s*\([^)]*\)", "", text)
    return text.strip()


def _status_from_cell(cell: str) -> str:
    """Infer status from the model cell's annotation text. Defaults to 'active'."""
    low = cell.lower()
    if "retired" in low:
        return "retired"
    if "deprecated" in low:
        return "deprecated"
    return "active"


def parse_rate_card(markdown: str) -> dict[str, dict[str, Any]]:
    """Parse the rate card markdown into a {display_name: {fields...}} dict.

    Combines the Model pricing, Fast mode, and Batch processing tables, keyed by
    cleaned display name. ``status`` is inferred from the model-cell annotation.
    Combined fast-mode rows ('A / B') fan out to both names. No validation here —
    that's ``build_price_map``'s job — so partial/garbage input parses without
    raising and is rejected downstream.
    """
    lines = markdown.splitlines()
    out: dict[str, dict[str, Any]] = {}

    # Model pricing: Model | Base Input | 5m CW | 1h CW | Cache Hits | Output
    for row in _find_table_after(lines, "Model pricing"):
        if len(row) < 6:
            continue
        name = _clean_model_name(row[0])
        if not name:
            continue
        out.setdefault(name, {})
        out[name].update(
            display_name=name,
            status=_status_from_cell(row[0]),
            input=_parse_price(row[1]),
            cache_write_5m=_parse_price(row[2]),
            cache_write_1h=_parse_price(row[3]),
            cache_read=_parse_price(row[4]),
            output=_parse_price(row[5]),
        )

    # Batch processing: Model | Batch input | Batch output
    for row in _find_table_after(lines, "Batch processing"):
        if len(row) < 3:
            continue
        name = _clean_model_name(row[0])
        if not name:
            continue
        entry = out.setdefault(name, {"display_name": name,
                                      "status": _status_from_cell(row[0])})
        entry["batch_input"] = _parse_price(row[1])
        entry["batch_output"] = _parse_price(row[2])

    # Fast mode: Model | Input | Output. Rows may combine two models with " / ".
    for row in _find_table_after(lines, "Fast mode"):
        if len(row) < 3:
            continue
        fast_in = _parse_price(row[1])
        fast_out = _parse_price(row[2])
        for name in (_clean_model_name(part) for part in row[0].split(" / ")):
            if not name:
                continue
            entry = out.setdefault(name, {"display_name": name, "status": "active"})
            entry["fast_mode_input"] = fast_in
            entry["fast_mode_output"] = fast_out

    return out


# --- time-boxed variant resolution ------------------------------------------
#
# Anthropic sometimes announces a scheduled repricing by listing the SAME model
# twice under qualified names -- e.g. "Claude Sonnet 5 through August 31, 2026"
# and "Claude Sonnet 5 starting September 1, 2026" -- rather than one row. Left
# alone, both would map to the same API id and the second one parsed would
# silently clobber the first in `by_id`. This section collapses such pairs into
# one base-name row: whichever variant is in effect as of `today` becomes that
# row's own fields, and any not-yet-effective variant is attached under
# `rate_changes` so `pricing.model_rates()` can promote it automatically once
# its date arrives -- no code change and no human mapping needed when that day
# comes, even offline.

_TIME_BOX_RE = re.compile(
    r"^(?P<base>.+?)\s+(?:through|starting|beginning|from)\s+(?P<date>.+?)\.?$",
    re.IGNORECASE,
)
_TIME_BOX_DATE_FORMATS = ("%B %d, %Y", "%B %d %Y", "%Y-%m-%d")


def _parse_time_box_date(phrase: str) -> str | None:
    """'August 31, 2026' -> '2026-08-31'. None if the phrase isn't a date --
    callers must NOT guess in that case, since a wrong effective date would
    silently apply the wrong rate on the wrong day."""
    phrase = phrase.strip().rstrip(".")
    for fmt in _TIME_BOX_DATE_FORMATS:
        try:
            return _dt.datetime.strptime(phrase, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def resolve_time_boxed_variants(parsed: dict[str, dict[str, Any]], *,
                                today: str | None = None) -> dict[str, dict[str, Any]]:
    """Collapse '<Model> through/starting <date>' rows into one entry per base
    display name. Rows with no time-box qualifier, or with a qualifier whose
    date phrase doesn't parse, pass through unchanged under their original
    name (an unparseable date is treated as "not a time-box" rather than
    dropped, so it still surfaces via the normal unmapped-name path instead of
    vanishing silently).
    """
    today = today or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    out: dict[str, dict[str, Any]] = {}

    for name, fields in parsed.items():
        m = _TIME_BOX_RE.match(name)
        eff = _parse_time_box_date(m.group("date")) if m else None
        if not m or eff is None:
            out[name] = fields
            continue
        groups.setdefault(m.group("base").strip(), []).append((eff, fields))

    for base, variants in groups.items():
        variants.sort(key=lambda v: v[0])
        current = next((v for v in reversed(variants) if v[0] <= today), None)
        upcoming = [v for v in variants if v[0] > today]
        if current is None:            # every variant is still in the future
            current = variants[0]
            upcoming = variants[1:]

        row = dict(current[1])
        row["display_name"] = base
        if upcoming:
            row["rate_changes"] = [
                {"effective_from": eff, **fields} for eff, fields in upcoming
            ]
        out[base] = row   # a same-named unqualified row, if any, loses to this

    return out


# --- name -> id, validation, assembly --------------------------------------

def _slugify(name: str) -> str:
    """Fallback key for an unmapped display name. Visible & obviously-temporary
    so a human notices and adds it to NAME_TO_ID."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"unmapped-{slug}" if slug else "unmapped-model"


def map_name_to_id(display_name: str) -> str:
    """Map a display name to its API model ID, or a slugified fallback (logged)."""
    api_id = NAME_TO_ID.get(display_name)
    if api_id is None:
        api_id = _slugify(display_name)
        log.warning(
            "Pricing page lists an unmapped model %r; emitting under key %r. "
            "Add it to NAME_TO_ID in updater.py.", display_name, api_id)
    return api_id


def _validate_model(api_id: str, fields: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems with one model's fields."""
    problems: list[str] = []
    for key in _REQUIRED_FIELDS:
        val = fields.get(key)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            problems.append(f"{api_id}.{key} is not numeric ({val!r})")
        elif val <= 0:
            problems.append(f"{api_id}.{key} is not positive ({val!r})")
    for key in ("fast_mode_input", "fast_mode_output"):
        if key in fields:
            val = fields[key]
            if not isinstance(val, (int, float)) or isinstance(val, bool) or val <= 0:
                problems.append(f"{api_id}.{key} is not a positive number ({val!r})")
    for i, change in enumerate(fields.get("rate_changes") or []):
        eff = change.get("effective_from")
        if not isinstance(eff, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", eff):
            problems.append(f"{api_id}.rate_changes[{i}].effective_from is not a "
                             f"YYYY-MM-DD date ({eff!r})")
        for key in _REQUIRED_FIELDS:
            val = change.get(key)
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                problems.append(f"{api_id}.rate_changes[{i}].{key} is not numeric ({val!r})")
            elif val <= 0:
                problems.append(f"{api_id}.rate_changes[{i}].{key} is not positive ({val!r})")
    return problems


def _ordered_model_fields(api_id: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """Emit one model's fields in a stable, readable key order."""
    out: dict[str, Any] = {
        "display_name": parsed.get("display_name", api_id),
        "status": parsed.get("status", "active"),
        "input": parsed["input"],
        "output": parsed["output"],
        "cache_write_5m": parsed["cache_write_5m"],
        "cache_write_1h": parsed["cache_write_1h"],
        "cache_read": parsed["cache_read"],
        "batch_input": parsed["batch_input"],
        "batch_output": parsed["batch_output"],
    }
    if "fast_mode_input" in parsed:
        out["fast_mode_input"] = parsed["fast_mode_input"]
    if "fast_mode_output" in parsed:
        out["fast_mode_output"] = parsed["fast_mode_output"]
    if "rate_changes" in parsed:
        out["rate_changes"] = parsed["rate_changes"]
    return out


def build_price_map(parsed: dict[str, dict[str, Any]], *,
                    fetched_at: str | None = None) -> dict[str, Any]:
    """Assemble a complete, validated price map from parsed rows.

    Raises ``ValueError`` if the parse yields zero models or any required field is
    non-numeric/non-positive — so a page-format change can never blank or corrupt
    the bundled map. ``fetched_at`` defaults to today's UTC date, and also serves
    as "today" for resolving any time-boxed variants (see
    ``resolve_time_boxed_variants``) so a fixed ``fetched_at`` in tests makes the
    whole resolution deterministic too.
    """
    if fetched_at is None:
        fetched_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

    parsed = resolve_time_boxed_variants(parsed, today=fetched_at)

    by_id: dict[str, dict[str, Any]] = {}
    for display_name, fields in parsed.items():
        by_id[map_name_to_id(display_name)] = fields

    if not by_id:
        raise ValueError("parsed zero models from the rate card — refusing to write")

    problems: list[str] = []
    for api_id, fields in by_id.items():
        problems.extend(_validate_model(api_id, fields))
    if problems:
        raise ValueError("rate-card parse failed validation:\n  "
                         + "\n  ".join(problems))

    # Stable key order: seed order first, then any new IDs (sorted for determinism).
    new_ids = sorted(k for k in by_id if k not in _SEED_ORDER)
    ordered_ids = [k for k in _SEED_ORDER if k in by_id] + new_ids

    models = {api_id: _ordered_model_fields(api_id, by_id[api_id])
              for api_id in ordered_ids}

    return {
        "currency": CURRENCY,
        "unit": UNIT,
        "source": SOURCE_URL,
        "fetched_at": fetched_at,
        "models": models,
        "multipliers": dict(MULTIPLIERS),
        "surcharges": dict(SURCHARGES),
    }


# --- diffing ---------------------------------------------------------------

def diff_maps(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Diff two price maps' model fields. Returns a dict with ``added`` (ids),
    ``removed`` (ids), and ``changed`` ({id: {field: (old, new)}}). Ignores the
    ``fetched_at`` timestamp so a re-run that only restamps the date is a no-op."""
    old_models = old.get("models", {})
    new_models = new.get("models", {})
    old_ids, new_ids = set(old_models), set(new_models)

    changed: dict[str, dict[str, tuple[Any, Any]]] = {}
    for api_id in old_ids & new_ids:
        o, n = old_models[api_id], new_models[api_id]
        field_changes = {
            key: (o.get(key), n.get(key))
            for key in set(o) | set(n)
            if o.get(key) != n.get(key)
        }
        if field_changes:
            changed[api_id] = field_changes

    return {
        "added": sorted(new_ids - old_ids),
        "removed": sorted(old_ids - new_ids),
        "changed": changed,
    }


def has_changes(diff: dict[str, Any]) -> bool:
    return bool(diff["added"] or diff["removed"] or diff["changed"])


def format_diff(diff: dict[str, Any]) -> str:
    """Render a diff for stdout/log. Empty string when there are no changes."""
    if not has_changes(diff):
        return "No pricing changes."
    out: list[str] = []
    if diff["added"]:
        out.append("Added models: " + ", ".join(diff["added"]))
    if diff["removed"]:
        out.append("Removed models: " + ", ".join(diff["removed"]))
    for api_id, fields in sorted(diff["changed"].items()):
        out.append(f"Changed {api_id}:")
        for key, (old_v, new_v) in sorted(fields.items()):
            out.append(f"    {key}: {old_v} -> {new_v}")
    return "\n".join(out)


# --- file I/O --------------------------------------------------------------

def load_existing(path: Path | None = None) -> dict[str, Any]:
    """Load the bundled price map, or {} if it doesn't exist yet."""
    path = path or price_map_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def write_price_map(price_map: dict[str, Any], path: Path | None = None) -> None:
    """Write the map as stable, 2-space-indented JSON with a trailing newline."""
    path = path or price_map_path()
    text = json.dumps(price_map, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


# --- CLI -------------------------------------------------------------------

def run(check_only: bool = False) -> int:
    """Fetch, parse, validate, diff, and (unless ``check_only``) write.

    Returns a process exit code: 0 = no changes (or written ok); 1 = differs
    (--check) or an error. Designed so ``--check`` is CI-suitable.
    """
    try:
        markdown = fetch_rate_card()
    except httpx.HTTPError as exc:
        log.error("Failed to fetch rate card: %s", exc)
        return 1

    try:
        parsed = parse_rate_card(markdown)
        new_map = build_price_map(parsed)
    except ValueError as exc:
        log.error("Refusing to update price map: %s", exc)
        return 1

    existing = load_existing()
    diff = diff_maps(existing, new_map)
    print(format_diff(diff))

    if check_only:
        if has_changes(diff):
            print("\nLive pricing differs from the bundled map (run without "
                  "--check to update).")
            return 1
        return 0

    if not has_changes(diff) and existing:
        print("Bundled price map already current; nothing to write.")
        return 0

    write_price_map(new_map)
    print(f"\nWrote {price_map_path()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pricing.updater",
        description="Update price_map.json from Anthropic's published rate card.")
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if the live page differs from the bundled map; "
             "do not write. Suitable for CI.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return run(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
