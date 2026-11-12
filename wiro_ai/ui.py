# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Wiro AI tab in the 3D Viewport sidebar, and its operators.

No "from __future__ import annotations" here: Blender reads operator
property annotations at registration and needs real objects.
"""

import os
import textwrap

import bpy

from . import api, catalog, pipeline, preferences, runtime


def _settings(context):
    return context.scene.wiro_ai


def _build_request(context, settings) -> pipeline.JobRequest:
    mode = settings.mode
    request = pipeline.JobRequest(
        mode=mode,
        mesh_model=catalog.mesh_model(settings.mesh_model),
        mesh_settings=settings.mesh_settings(),
        output_dir=preferences.output_dir(context),
        prompt=settings.prompt.strip(),
        image_path=bpy.path.abspath(settings.image_path) if settings.image_path else "",
        image_model=catalog.image_model(settings.image_model) if mode == pipeline.TEXT else None,
        framing=settings.framing,
    )
    return request


def _check_inputs(settings) -> str:
    if settings.mode == pipeline.TEXT:
        if not settings.prompt.strip():
            return "Write a prompt first."
        return ""
    path = bpy.path.abspath(settings.image_path) if settings.image_path else ""
    if not path:
        return "Choose an image first."
    if not os.path.isfile(path):
        return f"Image not found: {path}"
    if not path.lower().endswith(api.IMAGE_EXTENSIONS):
        return "Use a PNG, JPEG or WebP image."
    return ""


class WIRO_AI_OT_generate(bpy.types.Operator):
    bl_idname = "wiro_ai.generate"
    bl_label = "Generate 3D Model"
    bl_description = (
        "Send the prompt or image to Wiro AI, then import the GLB it returns. The run is billed to your Wiro AI wallet"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return not runtime.running()

    def execute(self, context):
        settings = _settings(context)
        if not preferences.online():
            self.report({"ERROR"}, "Turn on Allow Online Access in Preferences > System > Network first.")
            return {"CANCELLED"}
        problem = _check_inputs(settings)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        try:
            client = preferences.client(context)
            request = _build_request(context, settings)
        except api.WiroError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        model = request.mesh_model
        meta = {
            "mesh_slug": model.slug,
            "object_name": f"Wiro {model.label}",
            "place_at_cursor": settings.place_at_cursor,
            "prompt": request.prompt if request.mode == pipeline.TEXT else "",
            "mode": request.mode,
        }
        result = runtime.start(pipeline.Job(client, request), meta)
        if result is None:
            self.report({"INFO"}, f"Wiro AI: generating with {model.label}.")
            return {"FINISHED"}
        # Background mode: the job already ran.
        if result.get("phase") == "done":
            self.report(
                {"INFO"},
                f"Wiro AI: imported {', '.join(result['objects'])} in {result['seconds']:.0f} s "
                f"({catalog.format_price(result['cost'])}).",
            )
            return {"FINISHED"}
        self.report({"ERROR"}, result.get("error") or f"Wiro AI: {result.get('phase')}.")
        return {"CANCELLED"}


class WIRO_AI_OT_cancel(bpy.types.Operator):
    bl_idname = "wiro_ai.cancel"
    bl_label = "Cancel"
    bl_description = "Stop the running generation. A queued task is cancelled, a running one is stopped"

    @classmethod
    def poll(cls, context):
        return runtime.running()

    def execute(self, context):
        runtime.cancel()
        return {"FINISHED"}


class WIRO_AI_OT_reimport(bpy.types.Operator):
    bl_idname = "wiro_ai.reimport"
    bl_label = "Import Again"
    bl_description = "Import the last generated GLB again, without a new run"

    @classmethod
    def poll(cls, context):
        path = runtime.last_result().get("glb_path") or ""
        return bool(path) and os.path.isfile(path) and not runtime.running()

    def execute(self, context):
        try:
            roots = runtime.reimport_last(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {', '.join(obj.name for obj in roots)}.")
        return {"FINISHED"}


class WIRO_AI_OT_use_reference(bpy.types.Operator):
    bl_idname = "wiro_ai.use_reference"
    bl_label = "Use in Image to 3D"
    bl_description = (
        "Switch to Image to 3D with this reference image, to try another 3D model without drawing a new picture"
    )

    @classmethod
    def poll(cls, context):
        path = runtime.last_result().get("reference_path") or ""
        return bool(path) and os.path.isfile(path) and not runtime.running()

    def execute(self, context):
        settings = _settings(context)
        settings.image_path = runtime.last_result()["reference_path"]
        settings.mode = pipeline.IMAGE
        return {"FINISHED"}


class WIRO_AI_OT_open_folder(bpy.types.Operator):
    bl_idname = "wiro_ai.open_folder"
    bl_label = "Open Folder"
    bl_description = "Open the folder with the generated GLB files and reference images"

    def execute(self, context):
        folder = preferences.output_dir(context)
        bpy.ops.wm.path_open(filepath=folder)
        return {"FINISHED"}


class WIRO_AI_OT_open_preferences(bpy.types.Operator):
    bl_idname = "wiro_ai.open_preferences"
    bl_label = "Add API Key"
    bl_description = "Open the Wiro AI add-on preferences"

    def execute(self, context):
        bpy.ops.preferences.addon_show(module=preferences.PACKAGE)
        return {"FINISHED"}


def _wrapped(layout, text: str, context, icon: str = "NONE") -> None:
    width = context.region.width if context.region else 300
    scale = max(0.5, context.preferences.view.ui_scale)
    chars = max(20, int(width / (7.0 * scale)))
    for index, line in enumerate(textwrap.wrap(text, chars) or [""]):
        layout.label(text=line, icon=icon if index == 0 else "BLANK1" if icon != "NONE" else "NONE")


def _model_options(layout, settings) -> None:
    model = catalog.mesh_model(settings.mesh_model)
    if model.key == "TRELLIS_2":
        layout.prop(settings, "trellis_resolution")
    elif model.key == "PIXAL3D":
        layout.prop(settings, "pixal_resolution")
    else:
        layout.prop(settings, "hunyuan_texture")
    if model.texture_sizes:
        layout.prop(settings, "texture_size")
    layout.prop(settings, "face_limit")


class WIRO_AI_PT_generate(bpy.types.Panel):
    bl_idname = "WIRO_AI_PT_generate"
    bl_label = "Wiro AI"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Wiro AI"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = _settings(context)
        job = runtime.current()

        if not preferences.online():
            box = layout.box()
            box.label(text="Online access is off", icon="ERROR")
            _wrapped(
                box, "Wiro AI runs the models online. Turn on Allow Online Access in Preferences > System.", context
            )
            box.operator("screen.userpref_show", text="Open Preferences", icon="PREFERENCES")
        elif not preferences.has_key(context):
            box = layout.box()
            box.label(text="No API key yet", icon="INFO")
            box.operator("wiro_ai.open_preferences", icon="PREFERENCES")

        inputs = layout.column()
        inputs.enabled = job is None
        # Mode, prompt and image path use the full width, so they are not cut short.
        wide = inputs.column()
        wide.use_property_split = False
        wide.row().prop(settings, "mode", expand=True)
        if settings.mode == pipeline.TEXT:
            wide.label(text="Prompt")
            wide.prop(settings, "prompt", text="")
            inputs.prop(settings, "image_model")
            inputs.prop(settings, "framing")
        else:
            wide.label(text="Image")
            wide.prop(settings, "image_path", text="")
            icon = runtime.preview_icon(bpy.path.abspath(settings.image_path)) if settings.image_path else 0
            if icon:
                inputs.template_icon(icon_value=icon, scale=7.0)

        inputs.separator()
        inputs.prop(settings, "mesh_model")
        _model_options(inputs, settings)

        options = inputs.column()
        options.prop(settings, "show_options", icon="DOWNARROW_HLT" if settings.show_options else "RIGHTARROW")
        if settings.show_options:
            model = catalog.mesh_model(settings.mesh_model)
            options.prop(settings, "seed")
            if model.key == "HUNYUAN3D_2_1":
                options.prop(settings, "hunyuan_octree")
                options.prop(settings, "remove_background")
            options.prop(settings, "place_at_cursor")

        if job is None:
            layout.separator()
            price = layout.row()
            price.alignment = "CENTER"
            price.label(text=f"Price per run: {catalog.format_price(settings.price())}")
            button = layout.row()
            button.scale_y = 1.6
            button.enabled = preferences.online()
            button.operator("wiro_ai.generate", icon="SHADERFX")
        else:
            self._draw_job(layout, context, job)

        self._draw_last(layout, context)

    def _draw_job(self, layout, context, job) -> None:
        state = job.snapshot()
        box = layout.box()
        box.label(text=state.title, icon="TIME")
        bar = box.row()
        bar.progress(factor=state.progress, type="BAR", text=f"{int(state.progress * 100)}%  {state.elapsed:.0f} s")
        if state.status:
            box.label(text=state.status)
        if state.step:
            box.label(text=state.step, icon="BLANK1")
        icon = runtime.preview_icon(state.reference_path)
        if icon:
            box.template_icon(icon_value=icon, scale=7.0)
        box.operator("wiro_ai.cancel", icon="CANCEL")

    def _draw_last(self, layout, context) -> None:
        last = runtime.last_result()
        if not last:
            return
        box = layout.box()
        phase = last.get("phase")
        if phase == "done":
            names = ", ".join(last.get("objects") or [])
            box.label(text=f"Imported {names}", icon="CHECKMARK")
            stats = last.get("stats") or {}
            if stats:
                box.label(text=f"{stats.get('faces', 0):,} faces, {len(stats.get('materials', []))} material(s)")
            box.label(text=f"{last.get('seconds', 0):.0f} s, {catalog.format_price(last.get('cost'))}")
        elif phase == "cancelled":
            box.label(text="Cancelled", icon="CANCEL")
        else:
            _wrapped(box, last.get("error") or "The generation failed.", context, icon="ERROR")
        reference = last.get("reference_path") or ""
        icon = runtime.preview_icon(reference)
        if icon:
            box.template_icon(icon_value=icon, scale=7.0)
            box.operator("wiro_ai.use_reference", icon="IMAGE_DATA")
        row = box.row(align=True)
        row.operator("wiro_ai.reimport", icon="IMPORT")
        row.operator("wiro_ai.open_folder", icon="FILE_FOLDER")


classes = (
    WIRO_AI_OT_generate,
    WIRO_AI_OT_cancel,
    WIRO_AI_OT_reimport,
    WIRO_AI_OT_use_reference,
    WIRO_AI_OT_open_folder,
    WIRO_AI_OT_open_preferences,
    WIRO_AI_PT_generate,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
