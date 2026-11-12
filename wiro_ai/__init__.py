# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wiro AI 3D Generator: text to 3D and image to 3D in the 3D Viewport sidebar.

The bpy modules are imported in register(), so api, catalog and pipeline can
be imported (and tested) without Blender.
"""


def register():
    from . import preferences, properties, runtime, ui

    preferences.register()
    properties.register()
    runtime.register()
    ui.register()


def unregister():
    from . import preferences, properties, runtime, ui

    ui.unregister()
    runtime.unregister()
    properties.unregister()
    preferences.unregister()
