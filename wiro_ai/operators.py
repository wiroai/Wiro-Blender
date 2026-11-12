# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators behind the Wiro AI panels.

No "from __future__ import annotations" here: Blender reads operator
property annotations at registration and needs real objects.
"""

import os

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from . import api, catalog, forms, importer, materials, pipeline, preferences, runtime, schema


def _settings(context):
    return context.scene.wiro_ai


def _schema(form, purpose: str) -> schema.ModelSchema:
    """The schema of a form's model, loading it if needed. Raises ValueError while it loads."""
    model = runtime.catalog.schema(form.slug)
    if model is None:
        runtime.request_schema(form.slug)
        model = runtime.catalog.schema(form.slug)
    if model is None:
        problem = runtime.schema_status(form.slug) or "Its settings are still loading"
        raise ValueError(f"{form.slug}: {problem}. Try again in a moment.")
    forms.ensure(form, purpose)
    return model


def _short(model: schema.ModelSchema) -> str:
    return catalog.short_name(model.slug, model.title)


class _Runner:
    """Shared checks for the operators that start a run."""

    def start(self, context, kind: str, title: str, steps: list, meta: dict, client) -> set:
        job = pipeline.Job(client, steps, preferences.output_dir(context))
        meta["client"] = client
        item = runtime.submit(kind, title, job, meta)
        if not bpy.app.background:
            self.report({"INFO"}, f"Wiro AI: {title} started.")
            return {"FINISHED"}
        result = item.result
        if result.get("status") == "done":
            made = ", ".join(result.get("made") or []) or "the files"
            self.report(
                {"INFO"}, f"Wiro AI: {made} in {result.get('seconds', 0):.0f} s ({result.get('cost', 0):.4f} USD)."
            )
            return {"FINISHED"}
        self.report({"ERROR"}, result.get("error") or f"Wiro AI: {result.get('status')}.")
        return {"CANCELLED"}

    def client(self, context):
        if not runtime.online():
            raise ValueError("Turn on Allow Online Access in Preferences > System > Network first.")
        return preferences.client(context)


class WIRO_AI_OT_generate(bpy.types.Operator, _Runner):
    bl_idname = "wiro_ai.generate"
    bl_label = "Generate 3D Model"
    bl_description = "Run the models on Wiro AI and import the 3D model they return. Billed to your Wiro AI wallet"

    def execute(self, context):
        settings = _settings(context)
        settings.ensure_forms()
        try:
            client = self.client(context)
            mesh = _schema(settings.mesh_form, "mesh")
            mesh_values, mesh_files = forms.collect(settings.mesh_form, mesh)
            reference = reference_values = None
            images = []
            if settings.mode == pipeline.TEXT:
                reference = _schema(settings.reference_form, "reference")
                reference_values, _files = forms.collect(settings.reference_form, reference)
            else:
                images = [forms.file_entry(entry, "Picture") for entry in settings.images]
                images = [entry for entry in images if entry]
            cutout = _schema(settings.cutout_form, "cutout") if settings.use_cutout else None
            steps = pipeline.three_d_steps(
                mode=settings.mode,
                mesh=mesh,
                mesh_values=mesh_values,
                mesh_files=mesh_files,
                images=images,
                prompt=settings.prompt,
                reference=reference,
                reference_values=reference_values,
                framing=settings.framing,
                cutout=cutout,
                mesh_seconds=60.0,
            )
        except (ValueError, api.WiroError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        models = [s.model for s in steps]
        meta = {
            "models": models,
            "prompt": settings.prompt.strip() if settings.mode == pipeline.TEXT else "",
            "name": f"Wiro {_short(mesh)}",
            "import_options": runtime.import_options(settings),
        }
        title = f"{_short(mesh)}: {settings.prompt.strip()[:40]}" if settings.mode == pipeline.TEXT else _short(mesh)
        return self.start(context, "3d", title, steps, meta, client)


class WIRO_AI_OT_generate_texture(bpy.types.Operator, _Runner):
    bl_idname = "wiro_ai.generate_texture"
    bl_label = "Generate Texture"
    bl_description = "Make a texture with an image model on Wiro AI and put it on the objects. Billed to your wallet"

    def execute(self, context):
        settings = _settings(context)
        settings.ensure_forms()
        edit = settings.texture_mode == "EDIT"
        try:
            client = self.client(context)
            if edit:
                obj = context.active_object
                image = materials.base_color_image(obj) if obj else None
                if image is None:
                    raise ValueError("Select an object whose material has a base color texture.")
                model = _schema(settings.texture_edit_form, "texture_edit")
                values, _files = forms.collect(settings.texture_edit_form, model)
                steps = pipeline.texture_steps(
                    model=model, values=values, prompt=settings.texture_prompt, source=forms.image_upload(image)
                )
                objects = [obj.name]
            else:
                targets = materials.surface_objects(context.selected_objects)
                if not targets:
                    raise ValueError("Select the objects to texture.")
                model = _schema(settings.texture_form, "texture")
                values, _files = forms.collect(settings.texture_form, model)
                steps = pipeline.texture_steps(
                    model=model, values=values, prompt=settings.texture_prompt, tileable=settings.tileable
                )
                objects = [obj.name for obj in targets]
        except (ValueError, api.WiroError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        prompt = settings.texture_prompt.strip()
        meta = {
            "models": [model.slug],
            "prompt": prompt,
            "name": f"Wiro {prompt[:32]}",
            "objects": objects,
            "edit": edit,
            "texture_options": {
                "mapping": settings.mapping,
                "tiling": settings.tiling,
                "bump": settings.bump,
                "roughness": settings.roughness,
                "replace": settings.replace_materials,
            },
        }
        return self.start(context, "texture", f"Texture: {prompt[:40]}", steps, meta, client)


class WIRO_AI_OT_run_model(bpy.types.Operator, _Runner):
    bl_idname = "wiro_ai.run_model"
    bl_label = "Run Model"
    bl_description = "Run the chosen model on Wiro AI and bring what it returns into Blender. Billed to your wallet"

    def execute(self, context):
        settings = _settings(context)
        try:
            if not settings.run_form.slug:
                raise ValueError("Search for a model and pick it first.")
            client = self.client(context)
            model = _schema(settings.run_form, "run")
            values, files = forms.collect(settings.run_form, model)
            steps = pipeline.run_steps(model=model, values=values, files=files)
        except (ValueError, api.WiroError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        meta = {
            "models": [model.slug],
            "prompt": str(values.get(model.prompt_param().id, "")) if model.prompt_param() else "",
            "name": f"Wiro {_short(model)}",
            "image_output": settings.image_output,
            "import_options": runtime.import_options(settings),
        }
        return self.start(context, "run", _short(model), steps, meta, client)


class WIRO_AI_OT_search_models(bpy.types.Operator):
    bl_idname = "wiro_ai.search_models"
    bl_label = "Search Models"
    bl_description = "Search the Wiro AI catalog. Free, and sends no key"

    def execute(self, context):
        settings = _settings(context)
        if not runtime.online():
            self.report({"ERROR"}, "Turn on Allow Online Access in Preferences > System > Network first.")
            return {"CANCELLED"}
        category = "" if settings.run_category in ("", "ALL") else settings.run_category
        try:
            rows = runtime.catalog.search(runtime.public_client(), settings.run_search, category)
        except api.WiroError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        wm = context.window_manager
        wm.wiro_ai_results.clear()
        for row in rows:
            item = wm.wiro_ai_results.add()
            item.slug = row["slug"]
            item.title = row["title"]
            item.price = runtime.catalog.price_label(row["slug"]) or ""
            item.categories = ", ".join(row.get("categories") or [])
        wm.wiro_ai_result_index = -1
        self.report({"INFO"}, f"{len(rows)} models found.")
        return {"FINISHED"}


class WIRO_AI_OT_refresh(bpy.types.Operator):
    bl_idname = "wiro_ai.refresh"
    bl_label = "Refresh"
    bl_description = "Update the model lists from the Wiro AI catalog and read your wallet balance"

    def execute(self, context):
        if not runtime.online():
            self.report({"ERROR"}, "Turn on Allow Online Access in Preferences > System > Network first.")
            return {"CANCELLED"}
        runtime.refresh_catalog(force=True)
        if preferences.has_key(context):
            try:
                runtime.refresh_balance(preferences.client(context))
            except api.WiroError as exc:
                self.report({"WARNING"}, str(exc))
        return {"FINISHED"}


class WIRO_AI_OT_image_add(bpy.types.Operator):
    bl_idname = "wiro_ai.image_add"
    bl_label = "Add Picture"
    bl_description = "Add a picture for Image to 3D"

    def execute(self, context):
        _settings(context).images.add()
        return {"FINISHED"}


class WIRO_AI_OT_image_remove(bpy.types.Operator):
    bl_idname = "wiro_ai.image_remove"
    bl_label = "Remove Picture"
    bl_description = "Remove this picture"

    index: IntProperty()

    def execute(self, context):
        images = _settings(context).images
        if 0 <= self.index < len(images):
            images.remove(self.index)
        return {"FINISHED"}


class WIRO_AI_OT_form_file_add(bpy.types.Operator):
    bl_idname = "wiro_ai.form_file_add"
    bl_label = "Add File"
    bl_description = "Add a file for this setting"

    form: StringProperty()
    index: IntProperty()

    def execute(self, context):
        params = getattr(_settings(context), self.form).params
        if 0 <= self.index < len(params):
            params[self.index].files.add()
        return {"FINISHED"}


class WIRO_AI_OT_form_file_remove(bpy.types.Operator):
    bl_idname = "wiro_ai.form_file_remove"
    bl_label = "Remove File"
    bl_description = "Remove this file"

    form: StringProperty()
    index: IntProperty()
    file_index: IntProperty()

    def execute(self, context):
        params = getattr(_settings(context), self.form).params
        if 0 <= self.index < len(params) and 0 <= self.file_index < len(params[self.index].files):
            params[self.index].files.remove(self.file_index)
        return {"FINISHED"}


class WIRO_AI_OT_reset_form(bpy.types.Operator):
    bl_idname = "wiro_ai.reset_form"
    bl_label = "Reset Settings"
    bl_description = "Put this model's settings back to their starting values"

    form: StringProperty()

    def execute(self, context):
        form = getattr(_settings(context), self.form)
        form.built_slug = ""
        forms.ensure(form, form.purpose or self.form.replace("_form", ""))
        return {"FINISHED"}


class WIRO_AI_OT_cancel_job(bpy.types.Operator):
    bl_idname = "wiro_ai.cancel_job"
    bl_label = "Cancel"
    bl_description = "Stop this job. A queued task is cancelled on Wiro AI, a running one is stopped"

    job_id: StringProperty()

    def execute(self, context):
        runtime.cancel(self.job_id)
        return {"FINISHED"}


def _entry_outputs(entry: dict) -> list:
    outputs = []
    for item in entry.get("files") or []:
        if item.get("path") and os.path.isfile(item["path"]):
            output = pipeline.Output(url=item.get("url", ""), name=item.get("name", ""), kind=item.get("kind", "file"))
            output.path, output.step = item["path"], item.get("step", "")
            outputs.append(output)
    return outputs


class WIRO_AI_OT_history(bpy.types.Operator):
    bl_idname = "wiro_ai.history"
    bl_label = "History Action"
    bl_description = "Use a finished run again"

    entry_id: StringProperty()
    action: EnumProperty(
        items=(
            ("IMPORT", "Import Again", "Import the 3D model again, without a new run"),
            ("APPLY", "Apply to Selection", "Put this texture on the selected objects"),
            ("LOAD", "Load Again", "Bring the files of this run into Blender again"),
            ("PICTURE", "Use Picture", "Use the reference picture in Image to 3D"),
            ("REUSE", "Reuse Prompt", "Put this prompt back in its panel"),
            ("OPEN", "Open Folder", "Open the folder with these files"),
            ("REMOVE", "Remove", "Remove this entry from the history. The files stay"),
        )
    )

    def execute(self, context):
        if runtime.history is None:
            return {"CANCELLED"}
        entry = runtime.history.get(self.entry_id)
        if entry is None:
            self.report({"ERROR"}, "This history entry is gone.")
            return {"CANCELLED"}
        settings = _settings(context)
        try:
            if self.action == "IMPORT":
                path = entry.get("model_path") or ""
                if not os.path.isfile(path):
                    raise ValueError("The 3D file is no longer on disk.")
                name = f"Wiro {catalog.short_name((entry.get('models') or [''])[-1])}"
                importer.import_3d(path, name=name, options=runtime.import_options(settings))
            elif self.action == "APPLY":
                path = entry.get("texture") or ""
                if not os.path.isfile(path):
                    raise ValueError("The texture is no longer on disk.")
                options = dict(entry.get("texture_options") or {})
                materials.apply_texture(context.selected_objects, path, name=f"Wiro {entry.get('prompt', '')[:32]}",
                                        **options)  # fmt: skip
            elif self.action == "LOAD":
                importer.load_outputs(_entry_outputs(entry), name="Wiro", image_output=settings.image_output,
                                      import_options=runtime.import_options(settings))  # fmt: skip
            elif self.action == "PICTURE":
                path = entry.get("reference") or ""
                if not os.path.isfile(path):
                    raise ValueError("The picture is no longer on disk.")
                settings.images.clear()
                settings.images.add().path = path
                settings.mode = pipeline.IMAGE
            elif self.action == "REUSE":
                if entry.get("kind") == "texture":
                    settings.texture_prompt = entry.get("prompt", "")
                else:
                    settings.prompt = entry.get("prompt", "")
                    settings.mode = pipeline.TEXT
            elif self.action == "OPEN":
                paths = [f.get("path") for f in entry.get("files") or [] if f.get("path")]
                folder = os.path.dirname(paths[0]) if paths else preferences.output_dir(context)
                bpy.ops.wm.path_open(filepath=folder)
            elif self.action == "REMOVE":
                runtime.history.remove(self.entry_id)
        except (ValueError, RuntimeError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class WIRO_AI_OT_history_clear(bpy.types.Operator):
    bl_idname = "wiro_ai.history_clear"
    bl_label = "Clear History"
    bl_description = "Remove every entry from the history. The files stay in their folder"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        if runtime.history is not None:
            runtime.history.clear()
        return {"FINISHED"}


class WIRO_AI_OT_open_folder(bpy.types.Operator):
    bl_idname = "wiro_ai.open_folder"
    bl_label = "Open Folder"
    bl_description = "Open the folder with the generated files"

    def execute(self, context):
        bpy.ops.wm.path_open(filepath=preferences.output_dir(context))
        return {"FINISHED"}


class WIRO_AI_OT_open_preferences(bpy.types.Operator):
    bl_idname = "wiro_ai.open_preferences"
    bl_label = "Add API Key"
    bl_description = "Open the Wiro AI add-on preferences"

    def execute(self, context):
        bpy.ops.preferences.addon_show(module=preferences.PACKAGE)
        return {"FINISHED"}


classes = (
    WIRO_AI_OT_generate,
    WIRO_AI_OT_generate_texture,
    WIRO_AI_OT_run_model,
    WIRO_AI_OT_search_models,
    WIRO_AI_OT_refresh,
    WIRO_AI_OT_image_add,
    WIRO_AI_OT_image_remove,
    WIRO_AI_OT_form_file_add,
    WIRO_AI_OT_form_file_remove,
    WIRO_AI_OT_reset_form,
    WIRO_AI_OT_cancel_job,
    WIRO_AI_OT_history,
    WIRO_AI_OT_history_clear,
    WIRO_AI_OT_open_folder,
    WIRO_AI_OT_open_preferences,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
