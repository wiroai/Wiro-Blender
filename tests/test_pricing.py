# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Price cases shared with the Wiro AI browser extensions (Wiro-Extension-Core)."""

import pytest

from tests.fakes import bundled_catalog
from wiro_ai import pricing, schema


def model(rows, **extra):
    return schema.ModelSchema(slug="a/b", title="A", price_rows=rows, **extra)


def test_picks_the_row_for_the_settings():
    rows = model(
        [
            {"inputs": {"resolution": "480P", "duration": "5"}, "price": 0.125, "priceMethod": "cpr"},
            {"inputs": {"resolution": "768P", "duration": "5"}, "price": 0.2, "priceMethod": "cpr"},
        ]
    )
    assert pricing.quote(rows, {"resolution": "768P", "duration": "5"}).label == "$0.20"
    assert pricing.quote(rows, {"resolution": "480P", "duration": 5}).amount == 0.125


def test_per_character_and_per_output():
    tts = model([{"inputs": {}, "price": 0.00004, "priceMethod": "cp-promptlength"}])
    assert pricing.quote(tts, {}, prompt_chars=66).label == "$0.00264"
    assert pricing.quote(tts, {}).label == "$0.00004 / character"
    nb = model([{"inputs": {"resolution": "1K"}, "price": 0.067, "priceMethod": "cpo"}])
    assert pricing.quote(nb, {"resolution": "1K", "n": "2"}).label == "$0.134"


def test_highest_valid_file_count_wins():
    edit = model(
        [
            {"inputs": {"inputImage": "QUANTITY:0"}, "price": 0.03, "priceMethod": "cpr"},
            {"inputs": {"inputImage": "QUANTITY:1"}, "price": 0.04, "priceMethod": "cpr"},
            {"inputs": {"inputImage": "QUANTITY:3"}, "price": 0.05, "priceMethod": "cpr"},
        ]
    )
    assert pricing.quote(edit, {}).amount == 0.03
    assert pricing.quote(edit, {"inputImage": ["a", "b"]}).amount == 0.04
    assert pricing.quote(edit, {"inputImage": ["a", "b", "c", "d"]}).amount == 0.05


def test_first_matching_row_and_notes():
    rows = model(
        [
            {"inputs": {"quality": "high"}, "price": 0.78, "priceMethod": "cpr"},
            {"inputs": {}, "price": 0.52, "priceMethod": "cpr"},
        ]
    )
    assert pricing.quote(rows, {"quality": "high"}).amount == 0.78
    assert pricing.quote(rows, {"quality": "standard"}).amount == 0.52
    video = model(
        [
            {
                "inputs": {"resolution": "1080P"},
                "price": 0.06,
                "priceMethod": "cp-outputVideoLength",
                "note": "Per second",
            }
        ]
    )
    result = pricing.quote(video, {"resolution": "1080P", "duration": "5"})
    assert (result.label, result.note) == ("$0.30", "Per second")
    free = model([{"inputs": {}, "price": 0, "priceMethod": "cpr", "note": "Free for now."}])
    assert (pricing.quote(free, {}).label, pricing.quote(free, {}).note) == ("Free", "Free for now.")


def test_run_time_and_discount():
    rows = model([{"inputs": {"resolution": "1K"}, "price": 0.1, "priceMethod": "cpr"}], estimated_cost=0.05)
    assert pricing.quote(rows, {"resolution": "4K"}).label == "about $0.05"
    assert pricing.quote(model([], estimated_cost=0.0012)).label == "about $0.0012"
    assert pricing.quote(model([], cost_per_second=0.0006)).label == "$0.0006 / second"
    offer = model([{"inputs": {}, "price": 0.52, "priceMethod": "cpr"}], discount_percent=50)
    assert pricing.quote(offer, {}).amount == pytest.approx(0.26)


def test_real_models():
    catalog = bundled_catalog()
    trellis = catalog.schema("microsoft/trellis-2")
    assert pricing.quote(trellis, {"pipeline_type": "512"}).label == "$0.25"
    assert pricing.quote(trellis, {"pipeline_type": "1536_cascade"}).label == "$0.35"
    hunyuan = catalog.schema("tencent/hunyuan3d-2-1")
    assert pricing.quote(hunyuan, {"generate_texture": "false"}).label == "$0.30"
    assert pricing.quote(hunyuan, {"generate_texture": True}).label == "$0.90"
    nano = catalog.schema("google/nano-banana-2-1")
    assert pricing.quote(nano, {"resolution": "1K"}).label == "$0.0336"
    cutout = catalog.schema("wiro/remove-background")
    assert pricing.quote(cutout, {}).approximate


def test_total():
    total = pricing.total([pricing.Quote(0.30, False, "$0.30"), pricing.Quote(0.0336, False, "$0.0336")])
    assert (total.amount, total.label) == (pytest.approx(0.3336), "$0.3336")
    rough = pricing.total([pricing.Quote(0.30, False, "$0.30"), pricing.Quote(0.0012, True, "about $0.0012")])
    assert rough.label == "about $0.3012"
    open_ended = pricing.total([pricing.Quote(0.30, False, "$0.30"), pricing.Quote(None, True, "by tokens")])
    assert open_ended.label == "$0.30 + by tokens"


@pytest.mark.parametrize(
    ("amount", "text"),
    [(0.2, "$0.20"), (1.5, "$1.50"), (0.01, "$0.01"), (0.0336, "$0.0336"), (0.00121, "$0.00121"), (0, "$0")],
)
def test_format_usd(amount, text):
    assert pricing.format_usd(amount) == text


def test_summary_label():
    assert pricing.summary_label({"price_rows": [{"price": 0.25, "priceMethod": "cpr"}]}) == "$0.25 / run"
    two = [{"price": 0.3, "priceMethod": "cpr"}, {"price": 0.42, "priceMethod": "cpr"}]
    assert pricing.summary_label({"price_rows": two}) == "from $0.30 / run"
    assert pricing.summary_label({"price_rows": [], "estimated_cost": 0.0012}) == "about $0.0012"
    assert pricing.summary_label({"price_rows": [{"price": 0, "priceMethod": "cpt"}]}) == "by tokens"
