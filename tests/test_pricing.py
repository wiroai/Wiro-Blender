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
            {"inputs": {"resolution": "480P", "duration": "5"}, "price": 0.125, "priceMethod": "per-run"},
            {"inputs": {"resolution": "768P", "duration": "5"}, "price": 0.2, "priceMethod": "per-run"},
        ]
    )
    assert pricing.quote(rows, {"resolution": "768P", "duration": "5"}).label == "$0.20"
    assert pricing.quote(rows, {"resolution": "480P", "duration": 5}).amount == 0.125


def test_per_character_and_per_output():
    tts = model([{"inputs": {}, "price": 0.00004, "priceMethod": "per-input-character"}])
    assert pricing.quote(tts, {}, prompt_chars=66).label == "$0.00264"
    assert pricing.quote(tts, {}).label == "$0.00004 per prompt character"
    nb = model([{"inputs": {"resolution": "1K"}, "price": 0.067, "priceMethod": "per-output-file"}])
    assert pricing.quote(nb, {"resolution": "1K", "n": "2"}).label == "$0.134"


def test_highest_valid_file_count_wins():
    edit = model(
        [
            {"inputs": {"inputImage": "QUANTITY:0"}, "price": 0.03, "priceMethod": "per-run"},
            {"inputs": {"inputImage": "QUANTITY:1"}, "price": 0.04, "priceMethod": "per-run"},
            {"inputs": {"inputImage": "QUANTITY:3"}, "price": 0.05, "priceMethod": "per-run"},
        ]
    )
    assert pricing.quote(edit, {}).amount == 0.03
    assert pricing.quote(edit, {"inputImage": ["a", "b"]}).amount == 0.04
    assert pricing.quote(edit, {"inputImage": ["a", "b", "c", "d"]}).amount == 0.05


def test_first_matching_row_and_notes():
    rows = model(
        [
            {"inputs": {"quality": "high"}, "price": 0.78, "priceMethod": "per-run"},
            {"inputs": {}, "price": 0.52, "priceMethod": "per-run"},
        ]
    )
    assert pricing.quote(rows, {"quality": "high"}).amount == 0.78
    assert pricing.quote(rows, {"quality": "standard"}).amount == 0.52
    video = model(
        [
            {
                "inputs": {"resolution": "1080P"},
                "price": 0.06,
                "priceMethod": "per-output-video-second",
                "note": "Per second",
            }
        ]
    )
    result = pricing.quote(video, {"resolution": "1080P", "duration": "5"})
    assert (result.label, result.note) == ("$0.30", "Per second")
    free = model([{"inputs": {}, "price": 0, "priceMethod": "per-run", "note": "Free for now."}])
    assert (pricing.quote(free, {}).label, pricing.quote(free, {}).note) == ("Free", "Free for now.")


def test_unit_prices_read_in_plain_words():
    def label(row, params=None):
        return pricing.quote(model([{"inputs": {}, **row}]), params or {}).label

    assert label({"price": 0.06, "priceMethod": "per-output-video-second"}) == "$0.06 per second of output video"
    assert label({"price": 0.0006, "priceMethod": "per-input-audio-second"}) == "$0.0006 per second of input audio"
    video = {"price": 0.025, "priceMethod": "per-input-video-second", "maxSeconds": 60}
    assert label(video) == "$0.025 per second of input video"
    assert label({"price": 0.0006, "priceMethod": "per-run-second"}) == "$0.0006 per second of run time"
    assert label({"price": 0.00001, "priceMethod": "per-total-token"}) == "$10.00 per 1M input and output tokens"
    pixel = {"price": 0.03, "priceMethod": "per-output-megapixel", "priceExtra": 0.015, "priceInput": 0}
    assert label(pixel) == "from $0.03"
    assert label({"price": 0, "priceMethod": "reported-price", "note": "rates"}) == "set when the run ends"
    assert label({"price": 0, "priceMethod": "per-realtime-turn", "note": "rates"}) == "per conversation turn"
    assert label({"price": 0.05, "priceMethod": "unknown-method"}) == "by run time"


def test_run_time_and_discount():
    rows = model([{"inputs": {"resolution": "1K"}, "price": 0.1, "priceMethod": "per-run"}], estimated_cost=0.05)
    assert pricing.quote(rows, {"resolution": "4K"}).label == "about $0.05"
    assert pricing.quote(model([], estimated_cost=0.0012)).label == "about $0.0012"
    assert pricing.quote(model([], cost_per_second=0.0006)).label == "$0.0006 per second of run time"
    offer = model([{"inputs": {}, "price": 0.52, "priceMethod": "per-run"}], discount_percent=50)
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
    reported = pricing.Quote(None, True, "set when the run ends")
    open_ended = pricing.total([pricing.Quote(0.30, False, "$0.30"), reported])
    assert open_ended.label == "$0.30 + set when the run ends"


@pytest.mark.parametrize(
    ("amount", "text"),
    [(0.2, "$0.20"), (1.5, "$1.50"), (0.01, "$0.01"), (0.0336, "$0.0336"), (0.00121, "$0.00121"), (0, "$0")],
)
def test_format_usd(amount, text):
    assert pricing.format_usd(amount) == text


def test_summary_label():
    assert pricing.summary_label({"price_rows": [{"price": 0.25, "priceMethod": "per-run"}]}) == "$0.25 per run"
    two = [{"price": 0.3, "priceMethod": "per-run"}, {"price": 0.42, "priceMethod": "per-run"}]
    assert pricing.summary_label({"price_rows": two}) == "from $0.30 per run"
    files = [{"price": 0.067, "priceMethod": "per-output-file"}]
    assert pricing.summary_label({"price_rows": files}) == "$0.067 per output file"
    assert pricing.summary_label({"price_rows": [], "estimated_cost": 0.0012}) == "about $0.0012"
    tokens = [{"price": 0.00001, "priceMethod": "per-total-token"}]
    assert pricing.summary_label({"price_rows": tokens}) == "per input and output token"
    reported = [{"price": 0, "priceMethod": "reported-price"}]
    assert pricing.summary_label({"price_rows": reported}) == "set when the run ends"
    voice = [{"price": 0, "priceMethod": "per-realtime-turn"}]
    assert pricing.summary_label({"price_rows": voice}) == "per conversation turn"
    audio = [{"price": 0.0006, "priceMethod": "per-input-audio-second"}]
    assert pricing.summary_label({"price_rows": audio}) == "varies"
