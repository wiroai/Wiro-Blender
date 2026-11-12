# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wiro AI 3D Generator: 3D models, textures and any Wiro AI model in the
3D Viewport sidebar.

The bpy modules are imported in register(), so api, catalog, schema, pricing,
pipeline and history can be imported (and tested) without Blender.
"""


def _startup():
    import bpy

    from . import runtime

    for scene in bpy.data.scenes:
        settings = getattr(scene, "wiro_ai", None)
        if settings is not None:
            settings.ensure_forms()
    runtime.refresh_catalog()
    return None


def _on_load(*_args):
    import bpy

    bpy.app.timers.register(_startup, first_interval=0.1)


# What bpy.app.handlers.persistent does: keep the handler when a file loads.
_on_load._bpy_persistent = True


def register():
    import bpy

    from . import operators, preferences, properties, runtime, ui

    preferences.register()
    properties.register()
    runtime.register()
    operators.register()
    ui.register()
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if not bpy.app.background:
        bpy.app.timers.register(_startup, first_interval=0.2)


def unregister():
    import bpy

    from . import operators, preferences, properties, runtime, ui

    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_startup):
        bpy.app.timers.unregister(_startup)
    ui.unregister()
    operators.unregister()
    runtime.unregister()
    properties.unregister()
    preferences.unregister()
