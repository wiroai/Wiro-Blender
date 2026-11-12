# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks the manifest against the rules Blender's own validator and the
extensions platform apply, so a mistake shows up before a release."""

import pathlib
import tomllib

from wiro_ai import version

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = tomllib.loads((ROOT / "wiro_ai" / "blender_manifest.toml").read_text())

# From Blender's _bpy_internal/extensions/tags.py (add-ons).
ADDON_TAGS = {
    "3D View", "Add Curve", "Add Mesh", "Animation", "Bake", "Camera", "Compositing", "Development",
    "Game Engine", "Geometry Nodes", "Grease Pencil", "Import-Export", "Lighting", "Material", "Mesh",
    "Modeling", "Node", "Object", "Paint", "Physics", "Pipeline", "Render", "Rigging", "Scene", "Sculpt",
    "Sequencer", "System", "Text Editor", "Tracking", "User Interface", "UV",
}  # fmt: skip


def terse(text: str) -> bool:
    return 0 < len(text) <= 64 and text == text.strip() and (text[-1].isalnum() or text[-1] in ")]}")


def test_version_matches():
    assert MANIFEST["version"] == version.VERSION


def test_required_fields():
    assert MANIFEST["schema_version"] == "1.0.0"
    assert MANIFEST["id"] == "wiro_ai" and MANIFEST["type"] == "add-on"
    assert MANIFEST["blender_version_min"] == "4.2.0"
    assert MANIFEST["license"] == ["SPDX:GPL-3.0-or-later"]
    assert "Blender" not in MANIFEST["name"]  # extensions platform terms, 2.1


def test_tagline_and_permissions_are_terse():
    assert terse(MANIFEST["tagline"]), MANIFEST["tagline"]
    assert set(MANIFEST["permissions"]) == {"network", "files"}
    for reason in MANIFEST["permissions"].values():
        assert terse(reason), reason


def test_tags_are_known():
    assert set(MANIFEST["tags"]) <= ADDON_TAGS


def test_user_agent():
    assert version.user_agent("5.3.0") == f"Wiro-Blender/{version.VERSION} Blender/5.3.0"
    assert version.user_agent("4.2.0 LTS") == f"Wiro-Blender/{version.VERSION} Blender/4.2.0-LTS"
