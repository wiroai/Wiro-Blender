# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences: the Wiro AI API key and where generated files go.

No "from __future__ import annotations" here: Blender reads the property
annotations at registration and needs real objects.
"""

import os
import threading

import bpy
from bpy.props import StringProperty

from . import api, version
from .properties import path_options

PACKAGE = __package__
KEY_PAGE = "https://wiro.ai/panel/project/new"


def get(context=None) -> "WiroAIPreferences | None":
    context = context or bpy.context
    addon = context.preferences.addons.get(PACKAGE)
    return addon.preferences if addon else None


def credentials(context=None) -> api.Credentials:
    """Credentials from the preferences, else from WIRO_API_KEY / WIRO_API_SECRET."""
    prefs = get(context)
    key = prefs.api_key.strip() if prefs else ""
    if key:
        return api.Credentials(key, prefs.api_secret)
    return api.Credentials(os.environ.get("WIRO_API_KEY", ""), os.environ.get("WIRO_API_SECRET", ""))


def has_key(context=None) -> bool:
    prefs = get(context)
    return bool((prefs and prefs.api_key.strip()) or os.environ.get("WIRO_API_KEY", "").strip())


def output_dir(context=None) -> str:
    prefs = get(context)
    folder = bpy.path.abspath(prefs.output_dir) if prefs and prefs.output_dir.strip() else ""
    if not folder:
        folder = bpy.utils.extension_path_user(PACKAGE, path="generated", create=True)
    os.makedirs(folder, exist_ok=True)
    return folder


def online() -> bool:
    return bool(getattr(bpy.app, "online_access", True))


def client(context=None) -> api.Client:
    return api.Client(credentials(context), user_agent=version.user_agent(bpy.app.version_string))


# Result of the last key check, shown in the preferences.
_check = {"running": False, "message": "", "ok": False}


class WIRO_AI_OT_check_key(bpy.types.Operator):
    bl_idname = "wiro_ai.check_key"
    bl_label = "Check API Key"
    bl_description = "Ask Wiro AI whether the API key works. This is free"

    @classmethod
    def poll(cls, context):
        return not _check["running"]

    def execute(self, context):
        if not online():
            self.report({"ERROR"}, "Turn on Allow Online Access in Preferences > System > Network first.")
            return {"CANCELLED"}
        try:
            wiro = client(context)
        except api.WiroError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _check.update(running=True, message="Checking...", ok=False)

        def work():
            try:
                wiro.check_key()
                _check.update(message="The API key works.", ok=True)
            except api.WiroError as exc:
                _check.update(message=str(exc), ok=False)
            finally:
                _check["running"] = False

        threading.Thread(target=work, name="wiro-ai-key-check", daemon=True).start()
        bpy.app.timers.register(_redraw_preferences, first_interval=0.5)
        return {"FINISHED"}


def _redraw_preferences():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "PREFERENCES":
                area.tag_redraw()
    return 0.5 if _check["running"] else None


class WiroAIPreferences(bpy.types.AddonPreferences):
    bl_idname = PACKAGE

    api_key: StringProperty(
        name="API key",
        description="The API key of a Wiro AI project",
        subtype="PASSWORD",
        default="",
    )
    api_secret: StringProperty(
        name="API secret",
        description="Only for projects that use signature-based authentication. Leave empty for API-key-only projects",
        subtype="PASSWORD",
        default="",
    )
    output_dir: StringProperty(
        name="Save files to",
        description=(
            "Folder for the GLB files and reference images. A path starting with // is relative to the "
            "open .blend file. Leave empty to use the add-on's own folder in your Blender user directory"
        ),
        subtype="DIR_PATH",
        options=path_options(),
        default="",
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.prop(self, "api_key")
        col.prop(self, "api_secret")
        row = col.row(align=True)
        row.operator("wiro_ai.check_key", icon="CHECKMARK")
        op = row.operator("wm.url_open", text="Create an API Key (opens the browser)", icon="URL")
        op.url = KEY_PAGE
        if _check["message"]:
            col.label(text=_check["message"], icon="CHECKMARK" if _check["ok"] else "INFO")
        if not self.api_key.strip() and os.environ.get("WIRO_API_KEY", "").strip():
            col.label(text="Using the WIRO_API_KEY environment variable.", icon="INFO")
        layout.separator()
        layout.prop(self, "output_dir")
        if not online():
            box = layout.box()
            box.label(text="Online access is off. Wiro AI needs it to run models.", icon="ERROR")
            box.label(text="Turn on Allow Online Access in Preferences > System > Network.")


classes = (WIRO_AI_OT_check_key, WiroAIPreferences)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
