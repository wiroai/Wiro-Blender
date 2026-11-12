# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refresh wiro_ai/data/catalog.json from the public Wiro AI catalog.

The add-on ships this file so its model lists and settings work before the
first live refresh, and offline. Inside Blender the lists refresh from Wiro AI
by category, so new models appear without an add-on update; this file only
sets the starting point.

    python scripts/update_catalog.py

Tool/List and Tool/Detail are public endpoints, so no API key is needed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wiro_ai import catalog  # noqa: E402

API = "https://api.wiro.ai/v1"
OUT = os.path.join(os.path.dirname(__file__), "..", "wiro_ai", "data", "catalog.json")
USER_AGENT = "Wiro-Blender-catalog-script"


def post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode())
    if data.get("result") is not True:
        raise SystemExit(f"{path} failed: {data.get('errors')}")
    return data


def main() -> None:
    lists: dict[str, list] = {}
    for category, limit in catalog.BUNDLED_CATEGORIES.items():
        data = post(
            "Tool/List",
            {
                "start": "0",
                "limit": str(limit),
                "categories": [category],
                "sort": "ratedusercount",
                "hideworkflows": True,
                "summary": True,
            },
        )
        rows = [catalog.summary_row(tool) for tool in data.get("tool") or []]
        lists[category] = [row for row in rows if row and not catalog.is_hidden(row)]
        print(f"{category}: {len(lists[category])} models")
    schemas: dict[str, dict] = {}
    for slug in catalog.BUNDLED_SCHEMAS:
        owner, project = slug.split("/", 1)
        tool = post("Tool/Detail", {"slugowner": owner, "slugproject": project})["tool"][0]
        schemas[slug] = catalog.trim_tool(tool)
        print(f"schema: {slug}")
    payload = {"source": "https://api.wiro.ai/v1 Tool/List and Tool/Detail", "categories": lists, "schemas": schemas}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    print(f"wrote {os.path.relpath(OUT)} ({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    main()
