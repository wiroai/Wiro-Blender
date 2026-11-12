# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later

import re

import pytest

from wiro_ai import catalog


def settings(**changes):
    return catalog.MeshSettings(**changes)


def test_slugs_and_defaults():
    for model in (*catalog.IMAGE_MODELS, *catalog.MESH_MODELS):
        assert re.fullmatch(r"[a-z0-9-]+/[a-z0-9.-]+", model.slug), model.slug
        assert model.page == f"https://wiro.ai/models/{model.slug}"
    for model in catalog.MESH_MODELS:
        assert model.default_detail in [row[0] for row in model.details]


def test_trellis_params():
    model = catalog.mesh_model("TRELLIS_2")
    params = catalog.mesh_params(model, settings(detail="1536_cascade", texture_size="4096", face_limit=5, seed=42))
    assert params == {
        "pipeline_type": "1536_cascade",
        "texture_size": "4096",
        "decimation_target": "10000",
        "seed": "42",
    }


def test_unknown_values_fall_back_to_defaults():
    model = catalog.mesh_model("PIXAL3D")
    params = catalog.mesh_params(model, settings(detail="512", texture_size="999", face_limit=9_000_000, seed=-3))
    assert params == {
        "pipeline_type": "1024_cascade",
        "texture_size": "2048",
        "decimation_target": "2000000",
        "seed": "0",
    }
    assert catalog.mesh_model("NOPE").key == "TRELLIS_2"
    assert catalog.image_model("NOPE").key == "NANO_BANANA_2_1"


def test_hunyuan_params():
    model = catalog.mesh_model("HUNYUAN3D_2_1")
    params = catalog.mesh_params(
        model, settings(detail="384", face_limit=500_000, textured=False, remove_background=False)
    )
    assert params == {
        "octree_resolution": "384",
        "max_facenum": "200000",
        "generate_texture": "false",
        "remove_background": "false",
        "seed": "0",
    }


@pytest.mark.parametrize(
    ("key", "changes", "price"),
    [
        ("TRELLIS_2", {"detail": "512"}, 0.25),
        ("TRELLIS_2", {"detail": "1024_cascade"}, 0.30),
        ("TRELLIS_2", {"detail": "1536_cascade"}, 0.35),
        ("PIXAL3D", {"detail": "1024_cascade"}, 0.30),
        ("PIXAL3D", {"detail": "1536_cascade"}, 0.42),
        ("HUNYUAN3D_2_1", {"textured": True}, 0.90),
        ("HUNYUAN3D_2_1", {"textured": False}, 0.30),
    ],
)
def test_mesh_prices(key, changes, price):
    assert catalog.mesh_price(catalog.mesh_model(key), settings(**changes)) == pytest.approx(price)


def test_expected_seconds_are_positive():
    for model in catalog.MESH_MODELS:
        for row in model.details:
            assert catalog.mesh_seconds(model, settings(detail=row[0], textured=True)) > 0


def test_reference_prompt():
    nano = catalog.image_model("NANO_BANANA_2_1")
    gpt = catalog.image_model("GPT_IMAGE_2_6_FLARE")
    assert catalog.reference_prompt("  a red   fox ", nano, False) == "a red fox"
    framed = catalog.reference_prompt("a red fox", nano, True)
    assert framed.startswith("a red fox. Show one whole object") and "plain white background" in framed
    assert "transparent background" in catalog.reference_prompt("a red fox!", gpt, True)
    assert catalog.reference_prompt("a red fox!", gpt, True).startswith("a red fox! Show")


def test_image_params_keep_model_settings():
    gpt = catalog.image_model("GPT_IMAGE_2_6_FLARE")
    params = catalog.image_params(gpt, "a lamp")
    assert params["prompt"] == "a lamp" and params["background"] == "transparent" and params["samples"] == "1"
    assert "prompt" not in gpt.params


@pytest.mark.parametrize(
    ("value", "text"),
    [(0.0336, "$0.0336"), (0.3, "$0.30"), (0.25, "$0.25"), (0.3336, "$0.3336"), (1.0, "$1.00"), (None, "")],
)
def test_format_price(value, text):
    assert catalog.format_price(value) == text
