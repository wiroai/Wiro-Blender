# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Wiro AI tab in the 3D Viewport sidebar."""

import textwrap
import time

import bpy

from . import forms, materials, pipeline, preferences, pricing, runtime

CATEGORY = "Wiro AI"


def _settings(context):
    return context.scene.wiro_ai


def _wrapped(layout, text: str, context, icon: str = "NONE") -> None:
    width = context.region.width if context.region else 300
    scale = max(0.5, context.preferences.view.ui_scale)
    chars = max(20, int(width / (7.0 * scale)))
    for index, line in enumerate(textwrap.wrap(text, chars) or [""]):
        layout.label(text=line, icon=icon if index == 0 else ("BLANK1" if icon != "NONE" else "NONE"))


def _quote(form):
    model = runtime.catalog.schema(form.slug) if form.slug else None
    if model is None or not forms.ready(form):
        return None
    params = forms.price_params(form, model)
    prompt = model.prompt_param()
    chars = len(params.get(prompt.id, "")) if prompt and isinstance(params.get(prompt.id), str) else 0
    return pricing.quote(model, params, prompt_chars=chars)


def _price_row(layout, quotes: list) -> None:
    quotes = [q for q in quotes if q is not None]
    row = layout.row()
    row.alignment = "CENTER"
    if not quotes:
        row.label(text="Price: loading the model's settings...")
        return
    total = pricing.total(quotes) if len(quotes) > 1 else quotes[0]
    balance = runtime.status().get("balance")
    text = f"Price per run: {total.label}"
    if balance is not None:
        text += f"   Balance: ${balance:,.2f}"
    row.label(text=text)


def _model_box(layout, settings, form_attr: str, enum_attr: str, title: str, *, expanded_default=False) -> None:
    form = getattr(settings, form_attr)
    layout.prop(settings, enum_attr, text=title)
    if not form.slug:
        return
    if not forms.ready(form):
        message = runtime.schema_status(form.slug) or "Loading this model's settings..."
        layout.label(text=message, icon="TIME")
        return
    forms.draw(layout, settings, form_attr, section="main")
    if forms.count(form, "more"):
        header, body = layout.panel(f"wiro_ai_{form_attr}_more", default_closed=not expanded_default)
        header.label(text="More Options")
        if body is not None:
            forms.draw(body, settings, form_attr, section="more")
            body.operator("wiro_ai.reset_form", text="Reset Settings", icon="LOOP_BACK").form = form_attr


class WIRO_AI_PT_main(bpy.types.Panel):
    bl_idname = "WIRO_AI_PT_main"
    bl_label = "Wiro AI"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        if not runtime.online():
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
        status = runtime.status()
        row = layout.row(align=True)
        if status.get("balance") is not None:
            row.label(text=f"Balance: ${status['balance']:,.2f}", icon="FUND")
        else:
            row.label(text="1,000+ models on Wiro AI", icon="WORLD")
        row.operator("wiro_ai.refresh", text="", icon="FILE_REFRESH")
        if status.get("catalog"):
            _wrapped(layout, status["catalog"], context, icon="INFO")
        active = [item for item in runtime.jobs() if item.state in ("running", "waiting")]
        if active:
            layout.label(text=f"{len(active)} job(s) in the queue", icon="SORTTIME")


class WIRO_AI_PT_generate_3d(bpy.types.Panel):
    bl_idname = "WIRO_AI_PT_generate_3d"
    bl_label = "3D Model"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 1

    def draw_header(self, context):
        self.layout.label(text="", icon="MESH_MONKEY")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = _settings(context)

        wide = layout.column()
        wide.use_property_split = False
        wide.row().prop(settings, "mode", expand=True)
        if settings.mode == pipeline.TEXT:
            wide.label(text="Prompt")
            wide.prop(settings, "prompt", text="")
            _model_box(layout, settings, "reference_form", "reference_model", "Image Model")
            layout.prop(settings, "framing")
        else:
            mesh = runtime.catalog.schema(settings.mesh_form.slug)
            image_param = mesh.image_param() if mesh else None
            limit = image_param.max_files if image_param else 1
            header = "Pictures" if limit > 1 else "Picture"
            wide.label(text=f"{header} ({len(settings.images)}/{limit})" if limit > 1 else header)
            for index, entry in enumerate(settings.images):
                row = wide.row(align=True)
                if entry.image is None:
                    row.prop(entry, "path", text="")
                row.prop(entry, "image", text="")
                row.operator("wiro_ai.image_remove", text="", icon="X").index = index
            if len(settings.images) < max(1, limit):
                wide.operator("wiro_ai.image_add", icon="ADD")
            first = settings.images[0] if len(settings.images) else None
            if first is not None and first.path and not first.image:
                icon = runtime.preview_icon(bpy.path.abspath(first.path))
                if icon:
                    layout.template_icon(icon_value=icon, scale=6.0)
        layout.prop(settings, "use_cutout")
        if settings.use_cutout:
            _model_box(layout, settings, "cutout_form", "cutout_model", "Cutout Model")

        layout.separator()
        _model_box(layout, settings, "mesh_form", "mesh_model", "3D Model", expanded_default=False)

        layout.separator()
        quotes = [_quote(settings.mesh_form)]
        if settings.mode == pipeline.TEXT:
            quotes.append(_quote(settings.reference_form))
        if settings.use_cutout:
            quotes.append(_quote(settings.cutout_form))
        _price_row(layout, quotes)
        button = layout.row()
        button.scale_y = 1.5
        button.enabled = runtime.online()
        button.operator("wiro_ai.generate", icon="SHADERFX")


class WIRO_AI_PT_import_options(bpy.types.Panel):
    bl_idname = "WIRO_AI_PT_import_options"
    bl_label = "Import Options"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_parent_id = "WIRO_AI_PT_generate_3d"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        options = _settings(context).import_options
        layout.prop(options, "location")
        layout.prop(options, "origin")
        layout.prop(options, "size")
        if options.size == "FIT":
            layout.prop(options, "target_size")
        else:
            layout.prop(options, "scale")
        layout.prop(options, "use_collection")
        layout.prop(options, "cleanup")


class WIRO_AI_PT_texture(bpy.types.Panel):
    bl_idname = "WIRO_AI_PT_texture"
    bl_label = "Texture"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 2

    def draw_header(self, context):
        self.layout.label(text="", icon="TEXTURE")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = _settings(context)
        wide = layout.column()
        wide.use_property_split = False
        wide.row().prop(settings, "texture_mode", expand=True)
        edit = settings.texture_mode == "EDIT"
        if edit:
            obj = context.active_object
            image = materials.base_color_image(obj) if obj else None
            if image is None:
                wide.label(text="Select an object with a base color texture", icon="INFO")
            else:
                wide.label(text=f"{obj.name}: {image.name}", icon="IMAGE_DATA")
        else:
            targets = materials.surface_objects(context.selected_objects)
            text = f"{len(targets)} selected object(s)" if targets else "Select the objects to texture"
            wide.label(text=text, icon="OBJECT_DATA" if targets else "INFO")
        wide.label(text="Prompt")
        wide.prop(settings, "texture_prompt", text="")
        if edit:
            _model_box(layout, settings, "texture_edit_form", "texture_edit_model", "Edit Model")
        else:
            _model_box(layout, settings, "texture_form", "texture_model", "Image Model")
            layout.prop(settings, "tileable")
            header, body = layout.panel("wiro_ai_material_options", default_closed=True)
            header.label(text="Material")
            if body is not None:
                body.prop(settings, "mapping")
                body.prop(settings, "tiling")
                body.prop(settings, "bump")
                body.prop(settings, "roughness")
                body.prop(settings, "replace_materials")
        layout.separator()
        _price_row(layout, [_quote(settings.texture_edit_form if edit else settings.texture_form)])
        button = layout.row()
        button.scale_y = 1.5
        button.enabled = runtime.online()
        button.operator("wiro_ai.generate_texture", text="Edit Texture" if edit else "Generate Texture",
                        icon="BRUSH_DATA")  # fmt: skip


class WIRO_AI_UL_results(bpy.types.UIList):
    bl_idname = "WIRO_AI_UL_results"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.slug)
        price = row.row()
        price.alignment = "RIGHT"
        price.label(text=item.price)


class WIRO_AI_PT_run(bpy.types.Panel):
    bl_idname = "WIRO_AI_PT_run"
    bl_label = "Run Any Model"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 3
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="VIEWZOOM")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = _settings(context)
        wm = context.window_manager
        wide = layout.column(align=True)
        wide.use_property_split = False
        row = wide.row(align=True)
        row.prop(settings, "run_search", text="", icon="VIEWZOOM")
        row.operator("wiro_ai.search_models", text="", icon="FORWARD")
        wide.prop(settings, "run_category", text="")
        if len(wm.wiro_ai_results):
            layout.template_list("WIRO_AI_UL_results", "", wm, "wiro_ai_results", wm, "wiro_ai_result_index", rows=5)
        form = settings.run_form
        if not form.slug:
            layout.label(text="Search, then pick a model from the list", icon="INFO")
            return
        model = runtime.catalog.schema(form.slug)
        box = layout.box()
        box.label(text=form.slug, icon="NODE")
        if model is not None and model.description:
            _wrapped(box, model.description[:220], context)
        if not forms.ready(form):
            box.label(text=runtime.schema_status(form.slug) or "Loading this model's settings...", icon="TIME")
            return
        forms.draw(box, settings, "run_form", section="main")
        if forms.count(form, "more"):
            header, body = box.panel("wiro_ai_run_more", default_closed=True)
            header.label(text="More Options")
            if body is not None:
                forms.draw(body, settings, "run_form", section="more")
        box.prop(settings, "image_output")
        _price_row(layout, [_quote(form)])
        button = layout.row()
        button.scale_y = 1.5
        button.enabled = runtime.online()
        button.operator("wiro_ai.run_model", icon="PLAY")


class WIRO_AI_PT_queue(bpy.types.Panel):
    bl_idname = "WIRO_AI_PT_queue"
    bl_label = "Queue"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 4

    def draw_header(self, context):
        self.layout.label(text="", icon="SORTTIME")

    def draw(self, context):
        layout = self.layout
        items = runtime.jobs()
        if not items:
            layout.label(text="No jobs running")
            return
        for item in items:
            box = layout.box()
            row = box.row()
            row.label(text=item.title, icon="TIME" if item.state == "running" else "PAUSE")
            row.operator("wiro_ai.cancel_job", text="", icon="X").job_id = item.id
            if item.state == "waiting":
                box.label(text="Waiting for a free slot")
                continue
            state = item.job.snapshot()
            box.label(text=state.title)
            box.progress(factor=state.progress, type="BAR", text=f"{int(state.progress * 100)}%  {state.elapsed:.0f} s")
            if state.status:
                box.label(text=state.status if not state.step else f"{state.status}: {state.step}")
            icon = runtime.preview_icon(state.preview_path)
            if icon:
                box.template_icon(icon_value=icon, scale=5.0)


def _ago(stamp: float) -> str:
    seconds = max(0, time.time() - float(stamp or 0))
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{int(seconds // 60)} min ago"
    if seconds < 129600:
        return f"{int(seconds // 3600)} h ago"
    return time.strftime("%b %d", time.localtime(stamp))


KIND_ICONS = {"3d": "MESH_MONKEY", "texture": "TEXTURE", "run": "NODE"}


class WIRO_AI_PT_history(bpy.types.Panel):
    bl_idname = "WIRO_AI_PT_history"
    bl_label = "History"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 5
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="RECOVER_LAST")

    def draw(self, context):
        layout = self.layout
        history = runtime.history
        entries = history.entries if history is not None else []
        if not entries:
            layout.label(text="Finished runs show up here")
            return
        for entry in entries[:12]:
            box = layout.box()
            ok = entry.get("status") == "done"
            row = box.row()
            row.label(text=entry.get("title") or "Run", icon=KIND_ICONS.get(entry.get("kind"), "DOT"))
            row.label(text=_ago(entry.get("time", 0)))
            if ok:
                cost = pricing.format_usd(float(entry.get("cost") or 0))
                detail = f"{entry.get('seconds', 0):.0f} s, {cost}"
                stats = entry.get("stats") or {}
                if stats.get("faces"):
                    detail = f"{stats['faces']:,} faces, " + detail
                box.label(text=detail)
            else:
                _wrapped(box, entry.get("error") or entry.get("status", ""), context, icon="ERROR")
            icon = runtime.preview_icon(entry.get("reference") or entry.get("texture") or "")
            if icon:
                box.template_icon(icon_value=icon, scale=4.0)
            buttons = box.row(align=True)
            kind = entry.get("kind")
            if ok and kind == "3d":
                op = buttons.operator("wiro_ai.history", text="Import", icon="IMPORT")
                op.entry_id, op.action = entry["id"], "IMPORT"
                if entry.get("reference"):
                    op = buttons.operator("wiro_ai.history", text="Picture", icon="IMAGE_DATA")
                    op.entry_id, op.action = entry["id"], "PICTURE"
            elif ok and kind == "texture":
                op = buttons.operator("wiro_ai.history", text="Apply", icon="BRUSH_DATA")
                op.entry_id, op.action = entry["id"], "APPLY"
            elif ok:
                op = buttons.operator("wiro_ai.history", text="Load", icon="IMPORT")
                op.entry_id, op.action = entry["id"], "LOAD"
            if entry.get("prompt"):
                op = buttons.operator("wiro_ai.history", text="", icon="TEXT")
                op.entry_id, op.action = entry["id"], "REUSE"
            op = buttons.operator("wiro_ai.history", text="", icon="FILE_FOLDER")
            op.entry_id, op.action = entry["id"], "OPEN"
            op = buttons.operator("wiro_ai.history", text="", icon="TRASH")
            op.entry_id, op.action = entry["id"], "REMOVE"
        row = layout.row()
        row.label(text=f"{len(entries)} runs, {pricing.format_usd(history.total_cost())} in total")
        row.operator("wiro_ai.history_clear", text="", icon="TRASH")


classes = (
    WIRO_AI_PT_main,
    WIRO_AI_PT_generate_3d,
    WIRO_AI_PT_import_options,
    WIRO_AI_PT_texture,
    WIRO_AI_UL_results,
    WIRO_AI_PT_run,
    WIRO_AI_PT_queue,
    WIRO_AI_PT_history,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
