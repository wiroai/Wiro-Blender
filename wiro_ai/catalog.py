# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which Wiro AI models each panel lists, and the settings it starts them with.

Model lists come from Wiro AI categories (Tool/List), so a model that joins
the catalog shows up without an add-on update. A copy shipped with the add-on
(data/catalog.json, made by scripts/update_catalog.py) covers the first start
and offline use. Live lists and schemas are cached on disk.

This module does not import bpy.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

from . import pricing, schema

# Category -> how many models the shipped copy keeps.
BUNDLED_CATEGORIES = {
    "3d-generation": 100,
    "text-to-image": 40,
    "image-editing": 40,
    "background-removal": 20,
}

# Models whose settings ship with the add-on, so their forms work offline.
BUNDLED_SCHEMAS = (
    "microsoft/trellis-2",
    "tencentarc/pixal3d",
    "tencent/hunyuan3d-2-1",
    "tencent/hy-world-2-0-world-reconstruction",
    "google/nano-banana-2-1",
    "openai/gpt-image-2-6-flare",
    "google/nano-banana-2-lite",
    "bytedance/seedream-v5-lite",
    "wiro/remove-background",
    "briaai/rmbg-2-0",
)

CATEGORY_LABELS = {
    "3d-generation": "3D generation",
    "text-to-image": "Text to image",
    "image-editing": "Image editing",
    "image-to-image": "Image to image",
    "background-removal": "Background removal",
    "upscale": "Upscale",
    "text-to-video": "Text to video",
    "image-to-video": "Image to video",
    "text-to-speech": "Text to speech",
    "text-to-music": "Text to music",
    "speech-to-text": "Speech to text",
    "llm": "LLM",
}

# The model a panel starts with, and the ones it lists first.
DEFAULTS = {
    "3d-generation": "microsoft/trellis-2",
    "text-to-image": "google/nano-banana-2-1",
    "image-editing": "google/nano-banana-2-1",
    "background-removal": "wiro/remove-background",
}
PREFERRED = {
    "3d-generation": ("microsoft/trellis-2", "tencentarc/pixal3d", "tencent/hunyuan3d-2-1"),
    "text-to-image": ("google/nano-banana-2-1", "openai/gpt-image-2-6-flare", "google/nano-banana-2-lite"),
    "image-editing": ("google/nano-banana-2-1", "openai/gpt-image-2-6-flare"),
    "background-removal": ("wiro/remove-background", "briaai/rmbg-2-0"),
}

# Settings that suit Blender better than the model's own default.
OVERRIDES = {
    "microsoft/trellis-2": {"texture_size": "2048", "decimation_target": 200000},
    "tencentarc/pixal3d": {"texture_size": "2048", "decimation_target": 200000},
    "tencent/hunyuan3d-2-1": {"max_facenum": 200000},
}

# Short names for panels, objects and history.
SHORT_NAMES = {
    "microsoft/trellis-2": "Trellis-2",
    "tencentarc/pixal3d": "Pixal3D",
    "tencent/hunyuan3d-2-1": "Hunyuan3D-2.1",
    "tencent/hy-world-2-0-world-reconstruction": "HY-World 2.0",
    "google/nano-banana-2-1": "Nano Banana 2.1",
    "google/nano-banana-2-lite": "Nano Banana 2 Lite",
    "openai/gpt-image-2-6-flare": "GPT Image 2.6 Flare",
    "bytedance/seedream-v5-lite": "Seedream V5 Lite",
    "wiro/remove-background": "Remove Background",
    "briaai/rmbg-2-0": "RMBG 2.0",
}


def short_name(slug: str, title: str = "") -> str:
    """A short display name: a known one, else the model part of the slug."""
    if slug in SHORT_NAMES:
        return SHORT_NAMES[slug]
    return slug.partition("/")[2] or title or slug


# Clearer names for settings the 3D models share.
LABELS = {
    "decimation_target": "Face limit",
    "max_facenum": "Face limit",
    "pipeline_type": "Resolution",
    "texture_size": "Texture size",
    "generate_texture": "Textures",
    "octree_resolution": "Octree resolution",
    "remove_background": "Remove background",
    "targetSize": "Target size",
}

# Advanced settings worth showing up front in Blender.
PROMOTED = {"texture_size", "decimation_target", "max_facenum", "generate_texture"}

FRAMING_3D = (
    "Show one whole object, centered, in a three-quarter view, on a plain white background "
    "with soft studio lighting. No text, no other objects."
)
FRAMING_3D_TRANSPARENT = (
    "Show one whole object, centered, in a three-quarter view, on a transparent background "
    "with soft studio lighting. No text, no other objects."
)
FRAMING_TEXTURE = (
    "A seamless, tileable texture that fills the whole square frame, seen straight from above, "
    "flat even lighting, no shadows, no perspective, no objects, no text, no border."
)

_UNCENSORED = re.compile(r"uncensored|decensored|abliterated|obliterated|heretic|refusal[- ]reduced", re.I)
_NSFW = re.compile(r"nsfw", re.I)
_MODERATION = re.compile(r"detect|classif|moderat|filter|checker", re.I)

CACHE_SECONDS = 6 * 3600


def is_hidden(row: dict) -> bool:
    """Models the add-on never lists, the same rule as the Wiro AI browser extensions."""
    text = f"{row.get('slug', '')} {row.get('title', '')} {row.get('description', '')}"
    if _UNCENSORED.search(text):
        return True
    name = f"{row.get('slug', '')} {row.get('title', '')}"
    return bool(_NSFW.search(name)) and not _MODERATION.search(name)


def _price_rows(tool: dict) -> list:
    value = tool.get("dynamicprice")
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else []
        except ValueError:
            value = []
    return [row for row in value or [] if isinstance(row, dict)]


def summary_row(tool: dict) -> dict:
    """The few fields a model list needs, from a Tool/List record."""
    owner = str(tool.get("cleanslugowner") or tool.get("slugowner") or "").strip().lower()
    project = str(tool.get("cleanslugproject") or tool.get("slugproject") or "").strip().lower()
    if not owner or not project:
        return {}
    stats = tool.get("taskstat") if isinstance(tool.get("taskstat"), dict) else {}
    try:
        estimate = float(tool.get("approximatelycost") or 0) or None
    except (TypeError, ValueError):
        estimate = None
    return {
        "slug": f"{owner}/{project}",
        "title": " ".join(str(tool.get("title") or project).split()),
        "description": " ".join(str(tool.get("description") or "").split())[:300],
        "categories": [str(c) for c in tool.get("categories") or []],
        "price_rows": _price_rows(tool),
        "estimated_cost": estimate,
        "runs": int(stats.get("runcount") or 0) if str(stats.get("runcount") or "0").isdigit() else 0,
    }


_TOOL_KEYS = (
    "cleanslugowner",
    "cleanslugproject",
    "slugowner",
    "slugproject",
    "title",
    "description",
    "categories",
    "dynamicprice",
    "approximatelycost",
    "cps",
)
_ITEM_KEYS = (
    "id",
    "type",
    "label",
    "placeholder",
    "note",
    "required",
    "advanced",
    "hidden",
    "defaultvalue",
    "options",
    "minvalue",
    "maxvalue",
    "incrementby",
    "maxinputlenght",
)


def trim_tool(tool: dict) -> dict:
    """A Tool/Detail record with only what the add-on reads, for the cache."""
    out = {key: tool.get(key) for key in _TOOL_KEYS if key in tool}
    groups = tool.get("parameters")
    if isinstance(groups, str):
        try:
            groups = json.loads(groups)
        except ValueError:
            groups = []
    out["parameters"] = [
        {
            "items": [
                {
                    key: (
                        [{"value": o.get("value"), "text": o.get("text")} for o in item.get("options") or []]
                        if key == "options"
                        else item.get(key)
                    )
                    for key in _ITEM_KEYS
                    if key in item
                }
                for item in (group or {}).get("items") or []
                if isinstance(item, dict)
            ]
        }
        for group in groups or []
    ]
    return out


def overrides(slug: str) -> dict:
    return dict(OVERRIDES.get(slug, {}))


def context_values(model: schema.ModelSchema, purpose: str) -> dict:
    """Settings an image model gets when it draws a 3D reference or a texture."""
    values = {}
    ratio = model.find(schema.RATIO_IDS)
    if ratio and any(value == "1:1" for value, _label in ratio.options):
        values[ratio.id] = "1:1"
    count = model.find(schema.COUNT_IDS)
    if count and count.value_kind in ("int", "float", "text", "enum"):
        values[count.id] = "1" if count.value_kind == "enum" else 1
    fmt = model.find(schema.FORMAT_IDS)
    if fmt and any(value == "png" for value, _label in fmt.options):
        values[fmt.id] = "png"
    background = model.param("background")
    if purpose == "3d" and background and any(value == "transparent" for value, _label in background.options):
        values["background"] = "transparent"
    quality = model.param("quality")
    if quality and any(value == "medium" for value, _label in quality.options):
        values["quality"] = "medium"
    return values


def framed_prompt(prompt: str, purpose: str, *, transparent: bool = False) -> str:
    """The user's prompt with the framing hint for a 3D reference or a texture."""
    text = " ".join((prompt or "").split())
    hint = FRAMING_TEXTURE if purpose == "texture" else (FRAMING_3D_TRANSPARENT if transparent else FRAMING_3D)
    if text and text[-1] not in ".!?":
        text += "."
    return f"{text} {hint}".strip()


class Catalog:
    """Model lists and schemas: shipped copy, disk cache and live refreshes."""

    def __init__(self, bundled_path: str = "", cache_path: str = "") -> None:
        self._lock = threading.Lock()
        self.cache_path = cache_path
        self.version = 0
        self._lists: dict = {}
        self._listed_at: dict = {}
        self._tools: dict = {}
        self._parsed: dict = {}
        if bundled_path and os.path.isfile(bundled_path):
            self._load(bundled_path, live=False)
        if cache_path and os.path.isfile(cache_path):
            self._load(cache_path, live=True)

    def _load(self, path: str, *, live: bool) -> None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        for category, rows in (data.get("categories") or {}).items():
            self._lists[category] = [row for row in rows if isinstance(row, dict) and row.get("slug")]
            if live:
                self._listed_at[category] = float((data.get("listed_at") or {}).get(category) or 0)
        for slug, tool in (data.get("schemas") or {}).items():
            if isinstance(tool, dict):
                self._tools[slug] = tool
        self.version += 1

    def save(self) -> None:
        if not self.cache_path:
            return
        with self._lock:
            payload = {
                "categories": {k: v for k, v in self._lists.items() if k in self._listed_at},
                "listed_at": dict(self._listed_at),
                "schemas": dict(self._tools),
            }
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        partial = self.cache_path + ".part"
        with open(partial, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(partial, self.cache_path)

    # -- reading ------------------------------------------------------------

    def models(self, category: str) -> list:
        """The category's models: the preferred ones first, then Wiro's order."""
        rows = list(self._lists.get(category) or [])
        order = {slug: index for index, slug in enumerate(PREFERRED.get(category, ()))}
        rows.sort(key=lambda row: (order.get(row["slug"], len(order)), 0))
        return [row for row in rows if not is_hidden(row)]

    def row(self, slug: str) -> dict | None:
        for rows in self._lists.values():
            for row in rows:
                if row.get("slug") == slug:
                    return row
        return None

    def title(self, slug: str) -> str:
        tool = self._tools.get(slug)
        if tool and tool.get("title"):
            return " ".join(str(tool["title"]).split())
        row = self.row(slug)
        return row["title"] if row else slug

    def schema(self, slug: str) -> schema.ModelSchema | None:
        if slug in self._parsed:
            return self._parsed[slug]
        tool = self._tools.get(slug)
        if tool is None:
            return None
        parsed = schema.parse_tool(tool)
        self._parsed[slug] = parsed
        return parsed

    def has_schema(self, slug: str) -> bool:
        return slug in self._tools

    def stale(self, category: str) -> bool:
        return time.time() - self._listed_at.get(category, 0) > CACHE_SECONDS

    def price_label(self, slug: str) -> str:
        parsed = self.schema(slug)
        if parsed is not None and parsed.price_rows:
            return pricing.summary_label({"price_rows": parsed.price_rows, "estimated_cost": parsed.estimated_cost})
        row = self.row(slug)
        return pricing.summary_label(row) if row else ""

    # -- writing (any thread) -----------------------------------------------

    def store_list(self, category: str, tools: list) -> None:
        rows = [summary_row(tool) for tool in tools]
        rows = [row for row in rows if row]
        with self._lock:
            self._lists[category] = rows
            self._listed_at[category] = time.time()
            self.version += 1

    def store_tool(self, tool: dict) -> schema.ModelSchema:
        trimmed = trim_tool(tool)
        parsed = schema.parse_tool(trimmed)
        with self._lock:
            self._tools[parsed.slug] = trimmed
            self._parsed[parsed.slug] = parsed
            self.version += 1
        return parsed

    # -- network (call from a worker thread) ----------------------------------

    def refresh(self, client, categories, *, limit: int = 100) -> None:
        for category in categories:
            data = client.post(
                "Tool/List",
                json_body={
                    "start": "0",
                    "limit": str(limit),
                    "categories": [category],
                    "sort": "ratedusercount",
                    "hideworkflows": True,
                    "summary": True,
                },
                auth=False,
            )
            self.store_list(category, data.get("tool") or [])
        self.save()

    def fetch_schema(self, client, slug: str) -> schema.ModelSchema:
        owner, _, project = slug.partition("/")
        data = client.post("Tool/Detail", json_body={"slugowner": owner, "slugproject": project}, auth=False)
        tools = data.get("tool") or []
        if not tools or not isinstance(tools[0], dict):
            raise ValueError(f'Wiro AI has no model "{slug}".')
        parsed = self.store_tool(tools[0])
        self.save()
        return parsed

    def search(self, client, text: str, category: str = "", *, limit: int = 40) -> list:
        body = {"start": "0", "limit": str(limit), "hideworkflows": True, "summary": True}
        if text.strip():
            body["search"] = text.strip()
        if category:
            body["categories"] = [category]
            body["sort"] = "ratedusercount"
        data = client.post("Tool/List", json_body=body, auth=False)
        rows = [summary_row(tool) for tool in data.get("tool") or []]
        live = re.compile(r"realtime|live", re.I)
        return [
            row
            for row in rows
            if row
            and not is_hidden(row)
            and not any(r.get("priceMethod") == "cp-realtimeturn" for r in row.get("price_rows") or [])
            and not any(live.search(c) for c in row.get("categories") or [] if c == "realtime-conversation")
        ]
