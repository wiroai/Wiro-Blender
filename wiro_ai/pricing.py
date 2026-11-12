# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""The price shown before a run, worked out the way Wiro AI bills it.

- The row: Wiro bills with the first price row whose values all match the
  run's settings. When a row counts files (QUANTITY:n), it bills with the valid
  row that has the highest total count at or below what the run sends.
- The charge: cpr is the price of the run, cpo the price per output,
  cp-promptlength per prompt character, cp-outputVideoLength per second of
  output video. A run that matches no row is billed for its run time; the
  model's average cost is shown as an estimate.

The same rules as the Wiro AI browser extensions. This module does not import bpy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schema import ModelSchema

_QUANTITY = re.compile(r"^QUANTITY:(\d+)$")
COUNT_KEYS = ("n", "numberOfImages", "numImages", "numOutputs", "numberOfOutputs", "count", "batchSize", "samples")
DURATION_KEYS = ("duration", "durationSeconds", "length", "seconds", "videoLength", "videoDuration")


@dataclass
class Quote:
    amount: float | None
    approximate: bool
    label: str
    note: str = ""


def format_usd(amount: float) -> str:
    if amount == 0:
        return "$0"
    if amount >= 1:
        return f"${amount:.2f}"
    if amount >= 0.01:
        return f"${_trim(f'{amount:.4f}')}"
    return f"${_trim(f'{amount:.3g}')}" if "e" not in f"{amount:.3g}" else f"${amount:.6f}".rstrip("0")


def _trim(text: str) -> str:
    if "." not in text:
        return text
    text = text.rstrip("0").rstrip(".")
    # Keep cents readable: 0.2 -> 0.20.
    if re.search(r"\.\d$", text):
        text += "0"
    return text


def _quantity(value: Any) -> int | None:
    match = _QUANTITY.match(str(value))
    return int(match.group(1)) if match else None


def _count(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (list, tuple)):
        return len([item for item in value if item not in (None, "")])
    return 1


def _same(expected: Any, actual: Any) -> bool:
    if actual is None:
        return False
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    if isinstance(actual, bool):
        actual = "true" if actual else "false"
    if isinstance(actual, float) and actual.is_integer():
        actual = int(actual)
    return str(actual) == str(expected)


def _has_quantity(row: dict) -> bool:
    return any(_quantity(value) is not None for value in (row.get("inputs") or {}).values())


def matching_row(rows: list, params: dict) -> dict | None:
    """The row Wiro bills these settings with, or None when no row applies."""
    if not any(_has_quantity(row) for row in rows):
        for row in rows:
            if all(_same(expected, params.get(key)) for key, expected in (row.get("inputs") or {}).items()):
                return row
        return None
    best, best_score = None, -1
    for row in rows:
        score, valid = 0, True
        for key, expected in (row.get("inputs") or {}).items():
            quantity = _quantity(expected)
            if quantity is None:
                if not _same(expected, params.get(key)):
                    valid = False
                    break
            elif quantity > _count(params.get(key)):
                valid = False
                break
            else:
                score += quantity
        if valid and score > best_score:
            best, best_score = row, score
    return best


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _first_number(params: dict, keys: tuple) -> float | None:
    for key in keys:
        number = _positive(params.get(key))
        if number is not None:
            return number
    return None


def _note(row: dict) -> str:
    return " ".join(str(row.get("note") or "").split())


def _run_time(schema: ModelSchema, scale: float) -> Quote:
    estimate = schema.estimated_cost
    if estimate:
        amount = estimate * scale
        return Quote(amount, True, f"about {format_usd(amount)}", "Billed by run time; past runs averaged this.")
    if schema.cost_per_second:
        rate = format_usd(schema.cost_per_second * scale)
        return Quote(None, True, f"{rate} / second", "Billed by run time.")
    return Quote(None, True, "by run time", "Billed by run time.")


def _row_quote(row: dict, params: dict, prompt_chars: int) -> Quote:
    price = _positive(row.get("price")) or 0.0
    method = str(row.get("priceMethod") or "")
    note = _note(row)
    if method == "cpr":
        if price == 0:
            return Quote(0.0, False, "Free", note)
        return Quote(price, False, format_usd(price), note)
    if method == "cpo":
        if price == 0:
            return Quote(0.0, False, "Free", note)
        count = _first_number(params, COUNT_KEYS) or 1
        amount = price * count
        return Quote(amount, False, format_usd(amount), note)
    if method == "cp-promptlength":
        if not prompt_chars:
            return Quote(None, True, f"{format_usd(price)} / character", note)
        amount = price * prompt_chars
        return Quote(amount, False, format_usd(amount), note)
    if method == "cp-outputVideoLength":
        seconds = _first_number(params, DURATION_KEYS)
        if seconds is None:
            return Quote(None, True, f"{format_usd(price)} / video second", note)
        amount = price * seconds
        return Quote(amount, False, format_usd(amount), note)
    if method == "cp-audiosecondslength":
        return Quote(None, True, f"{format_usd(price)} / audio second", note)
    if method == "cp-pixel":
        return Quote(price, True, f"from {format_usd(price)}", note)
    if method == "cps":
        return Quote(None, True, f"{format_usd(price)} / second", note)
    if method == "cpt":
        return Quote(None, True, "by tokens", note or "Set by the tokens the run uses.")
    if method == "cp-readoutput":
        if price > 0:
            return Quote(price, True, f"about {format_usd(price)}", note)
        return Quote(None, True, "set when it ends", note or "The charge is known when the run ends.")
    if method == "cp-realtimeturn":
        return Quote(None, True, "live, per turn", note)
    return Quote(None, True, "by run time", note)


def quote(schema: ModelSchema, params: dict | None = None, *, prompt_chars: int = 0) -> Quote:
    """What a run with these settings costs, after the caller's discount."""
    params = params or {}
    scale = 1.0
    if schema.discount_percent:
        scale = 1.0 - schema.discount_percent / 100.0
    rows = [row for row in schema.price_rows if isinstance(row, dict)]
    if not rows:
        return _run_time(schema, scale)
    live = next((row for row in rows if row.get("priceMethod") == "cp-realtimeturn"), None)
    if live is not None:
        return _row_quote(live, params, prompt_chars)
    row = matching_row(rows, params)
    if row is None:
        return _run_time(schema, scale)
    scaled = dict(row)
    scaled["price"] = (_positive(row.get("price")) or 0.0) * scale
    return _row_quote(scaled, params, prompt_chars)


def total(quotes: list) -> Quote:
    """Several runs added up, for a pipeline such as picture plus 3D model."""
    known = [q.amount for q in quotes if q.amount is not None]
    unknown = [q for q in quotes if q.amount is None]
    approximate = any(q.approximate for q in quotes if q.amount is not None)
    amount = sum(known) if known else None
    if amount is None:
        label = " + ".join(q.label for q in quotes) or "by run time"
        return Quote(None, True, label)
    label = format_usd(amount)
    if approximate:
        label = f"about {label}"
    if unknown:
        label += " + " + " + ".join(q.label for q in unknown)
    return Quote(amount, approximate, label)


def summary_label(row: dict) -> str:
    """A short price for a model list row, from its Tool/List record."""
    rows = [r for r in row.get("price_rows") or [] if isinstance(r, dict)]
    prices = sorted({float(r.get("price") or 0) for r in rows if r.get("priceMethod") in ("cpr", "cpo")})
    if prices:
        unit = "run" if all(r.get("priceMethod") == "cpr" for r in rows) else "output"
        if len(prices) == 1:
            return "Free" if prices[0] == 0 else f"{format_usd(prices[0])} / {unit}"
        return f"from {format_usd(prices[0])} / {unit}"
    if rows:
        method = str(rows[0].get("priceMethod") or "")
        labels = {"cpt": "by tokens", "cp-readoutput": "set when it ends", "cp-realtimeturn": "live"}
        return labels.get(method, "varies")
    estimate = _positive(row.get("estimated_cost"))
    return f"about {format_usd(estimate)}" if estimate else "by run time"
