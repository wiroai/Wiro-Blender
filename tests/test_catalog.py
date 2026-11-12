# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later

import json

from tests.fakes import BUNDLED, FakeTransport, bundled_catalog
from wiro_ai import api, catalog


def client(transport):
    return api.Client(None, user_agent="Wiro-Blender/test", transport=transport)


def tool(owner, project, **extra):
    return {"cleanslugowner": owner, "cleanslugproject": project, "title": project.title(), "categories": [], **extra}


def test_bundled_lists_preferred_first():
    shipped = bundled_catalog()
    assert [row["slug"] for row in shipped.models("3d-generation")][:3] == list(catalog.PREFERRED["3d-generation"])
    assert shipped.models("text-to-image")[0]["slug"] == "google/nano-banana-2-1"
    assert shipped.models("background-removal")[0]["slug"] == "wiro/remove-background"
    for slug in catalog.BUNDLED_SCHEMAS:
        assert shipped.has_schema(slug), slug
    for category, slug in catalog.DEFAULTS.items():
        assert slug in [row["slug"] for row in shipped.models(category)], (category, slug)


def test_hidden_models():
    assert catalog.is_hidden({"slug": "a/qwen-uncensored", "title": "x"})
    assert catalog.is_hidden({"slug": "a/b", "title": "NSFW Image Maker"})
    assert not catalog.is_hidden({"slug": "a/nsfw-detector", "title": "NSFW Detector"})
    assert not catalog.is_hidden({"slug": "microsoft/trellis-2", "title": "Trellis-2"})


def test_summary_and_trim():
    row = catalog.summary_row(
        tool(
            "Microsoft",
            "Trellis-2",
            dynamicprice='[{"inputs": {}, "price": 0.3, "priceMethod": "cpr"}]',
            approximatelycost="0",
            taskstat={"runcount": 7},
            readme="long readme",
        )
    )
    assert row["slug"] == "microsoft/trellis-2" and row["runs"] == 7 and row["price_rows"][0]["price"] == 0.3
    assert catalog.summary_row({"title": "no slug"}) == {}
    trimmed = catalog.trim_tool(
        tool("a", "b", readme="x", parameters=json.dumps([{"items": [{"id": "p", "type": "text", "class": "is-12"}]}]))
    )
    assert "readme" not in trimmed
    assert trimmed["parameters"] == [{"items": [{"id": "p", "type": "text"}]}]


def test_refresh_store_and_cache(tmp_path):
    cache = tmp_path / "cache" / "catalog.json"
    live = catalog.Catalog(str(BUNDLED), str(cache))
    version = live.version
    transport = FakeTransport(tools={"3d-generation": [tool("meshy", "meshy-6"), tool("microsoft", "trellis-2")]})
    live.refresh(client(transport), ["3d-generation"])
    request = transport.requests[0]
    assert "x-api-key" not in request["headers"], "the catalog is read without the key"
    assert json.loads(request["body"])["categories"] == ["3d-generation"]
    assert live.version > version
    slugs = [row["slug"] for row in live.models("3d-generation")]
    assert slugs[0] == "microsoft/trellis-2" and "meshy/meshy-6" in slugs
    assert not live.stale("3d-generation") and live.stale("text-to-image")
    reloaded = catalog.Catalog(str(BUNDLED), str(cache))
    assert "meshy/meshy-6" in [row["slug"] for row in reloaded.models("3d-generation")]


def test_fetch_schema(tmp_path):
    detail = tool("meshy", "meshy-6", parameters=[{"items": [{"id": "inputImage", "type": "combinefileinput"}]}])
    live = catalog.Catalog("", str(tmp_path / "c.json"))
    parsed = live.fetch_schema(client(FakeTransport(tools={"meshy/meshy-6": detail})), "meshy/meshy-6")
    assert parsed.image_param().id == "inputImage" and live.has_schema("meshy/meshy-6")
    try:
        live.fetch_schema(client(FakeTransport()), "nobody/nothing")
    except ValueError as exc:
        assert "no model" in str(exc)
    else:
        raise AssertionError("expected an error")


def test_search_skips_live_models():
    rows = [
        tool("a", "image-model", categories=["text-to-image"]),
        tool("b", "voice", dynamicprice=[{"inputs": {}, "price": 0, "priceMethod": "cp-realtimeturn"}]),
        tool("c", "chat-uncensored"),
    ]
    transport = FakeTransport(tools={"": rows})
    found = bundled_catalog().search(client(transport), "model")
    assert [row["slug"] for row in found] == ["a/image-model"]
    assert json.loads(transport.requests[0]["body"])["search"] == "model"


def test_context_values_and_framing():
    shipped = bundled_catalog()
    nano = shipped.schema("google/nano-banana-2-1")
    assert catalog.context_values(nano, "3d") == {"aspectRatio": "1:1", "outputFormat": "png"}
    flare = shipped.schema("openai/gpt-image-2-6-flare")
    values = catalog.context_values(flare, "3d")
    assert values["background"] == "transparent" and values["ratio"] == "1:1" and values["quality"] == "medium"
    assert "background" not in catalog.context_values(flare, "texture")
    assert catalog.framed_prompt("mossy stone", "texture").startswith("mossy stone. A seamless, tileable texture")
    assert "transparent background" in catalog.framed_prompt("a lamp", "3d", transparent=True)
    assert catalog.short_name("microsoft/trellis-2") == "Trellis-2"
    assert catalog.short_name("meshy/meshy-6") == "meshy-6"
