# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on version. Keep it equal to "version" in blender_manifest.toml (a test checks)."""

VERSION = "1.1.0"


def user_agent(blender_version: str = "") -> str:
    agent = f"Wiro-Blender/{VERSION}"
    blender_version = "-".join(str(blender_version).split())
    if blender_version:
        agent += f" Blender/{blender_version}"
    return agent
