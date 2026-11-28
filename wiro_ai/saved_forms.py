# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Forms saved in a .blend file, brought up to date with their model's schema.

A built form keeps the file limits it was built with, and Blender saves it with
the scene. So when a file loads, and when the add-on starts with a file already
open, every built form takes its file fields' limits from the model's cached or
bundled schema again. A run does the same first (forms.ensure). Nothing is
fetched here: a form whose model has no schema on this computer, or a setting
its model no longer has, keeps what it has.

The handler can take its scenes and schemas as arguments, so it is tested
without Blender. Outside persistent, bpy is imported only inside the functions.
"""

from __future__ import annotations

try:
    from bpy.app.handlers import persistent
except ImportError:  # the unit tests run without Blender

    def persistent(function):
        function._bpy_persistent = True  # what Blender's decorator does
        return function


FORMS = ("mesh_form", "reference_form", "cutout_form", "texture_form", "texture_edit_form", "run_form")


def refresh_file_limits(form, model) -> None:
    """Give a built form's file fields the limits of its model's schema. The form stores no cap as 0."""
    for item in form.params:
        param = model.param(item.pid) if item.kind == "file" else None
        if param is None or not param.is_file:
            continue
        limit = param.max_files or 0
        if item.max_files != limit:
            item.max_files = limit


@persistent
def on_load(*_args, scenes=None, schemas=None) -> None:
    """The load_post handler: refresh_file_limits for every built form, from known schemas only."""
    if scenes is None:
        import bpy

        scenes = bpy.data.scenes
    if schemas is None:
        from . import runtime

        schemas = runtime.catalog.schema
    for scene in scenes:
        settings = getattr(scene, "wiro_ai", None)
        if settings is None:
            continue
        for attr in FORMS:
            form = getattr(settings, attr, None)
            if form is None or not form.slug or form.built_slug != form.slug:
                continue  # an unbuilt form is built from the schema when it arrives
            model = schemas(form.slug)
            if model is not None:
                refresh_file_limits(form, model)


def register() -> None:
    import bpy

    if on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load)
    # No load_post comes for the file that is open when the add-on is enabled, and register()
    # cannot read it: Blender's addon_utils.enable runs it with bpy.data restricted. So a timer
    # runs the handler once right after. (Timers do not run in background mode; there a run
    # corrects the form first, and a file load does too.)
    if not bpy.app.timers.is_registered(on_load):
        bpy.app.timers.register(on_load, first_interval=0.0)


def unregister() -> None:
    import bpy

    if bpy.app.timers.is_registered(on_load):
        bpy.app.timers.unregister(on_load)
    if on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load)
