# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Panel settings, stored per scene. The API key lives in the add-on
preferences instead, so it never ends up inside a .blend file.

No "from __future__ import annotations" here: Blender reads the property
annotations of these classes at registration and needs real objects.
"""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from . import catalog, forms


def path_options() -> set:
    """Lets a file or folder field take paths relative to the .blend file ("//").

    Newer Blender releases mark a "//" path as missing without this option.
    Older ones, such as 4.2, do not have the option, and the add-on resolves
    "//" paths itself either way.
    """
    if "is_path_supports_blend_relative" in bpy.types.Property.bl_rna.properties:
        return {"PATH_SUPPORTS_BLEND_RELATIVE"}
    return set()


class WiroAIFile(bpy.types.PropertyGroup):
    path: StringProperty(
        name="File",
        description="A local file, or the https URL of one",
        subtype="FILE_PATH",
        options=path_options(),
        default="",
    )
    image: PointerProperty(
        name="Image",
        description="Use an image that is already in this .blend file",
        type=bpy.types.Image,
    )


class WiroAIParam(bpy.types.PropertyGroup):
    pid: StringProperty()
    ptype: StringProperty()
    label: StringProperty()
    note: StringProperty()
    kind: StringProperty()
    file_kind: StringProperty()
    section: StringProperty(default="main")
    required: BoolProperty()
    max_files: IntProperty(default=1)
    options_json: StringProperty()
    value_text: StringProperty(name="Value", description="The value sent to the model")
    value_int: IntProperty(name="Value", description="The value sent to the model")
    value_float: FloatProperty(name="Value", description="The value sent to the model", precision=3)
    value_bool: BoolProperty(name="Value", description="The value sent to the model")
    value_enum: EnumProperty(
        name="Value",
        description="The value sent to the model",
        items=forms.param_items,
        get=forms.param_get,
        set=forms.param_set,
    )
    text_block: PointerProperty(
        name="Text",
        description="Use a text block from the Text Editor, for long prompts or JSON",
        type=bpy.types.Text,
    )
    files: CollectionProperty(type=WiroAIFile)


class WiroAIForm(bpy.types.PropertyGroup):
    slug: StringProperty(name="Model")
    built_slug: StringProperty()
    purpose: StringProperty()
    params: CollectionProperty(type=WiroAIParam)


class WiroAIImportOptions(bpy.types.PropertyGroup):
    location: EnumProperty(
        name="Location",
        description="Where the imported model goes",
        items=(
            ("CURSOR", "3D Cursor", "Place the model at the 3D cursor"),
            ("ORIGIN", "World Origin", "Place the model at the world origin"),
            ("KEEP", "As Exported", "Keep the position the file has"),
        ),
        default="CURSOR",
    )
    origin: EnumProperty(
        name="Origin",
        description="Where the object's origin sits",
        items=(
            ("BOTTOM", "Bottom Center", "Origin at the middle of the model's base, so it stands on the floor"),
            ("CENTER", "Bounds Center", "Origin at the center of the model's bounding box"),
            ("KEEP", "As Exported", "Keep the origin the file has"),
        ),
        default="BOTTOM",
    )
    size: EnumProperty(
        name="Size",
        description="How big the imported model is",
        items=(
            ("SCALE", "Scale", "Multiply the size the file has"),
            ("FIT", "Fit to Size", "Scale the model so its largest side has this length"),
        ),
        default="SCALE",
    )
    scale: FloatProperty(name="Scale", default=1.0, min=0.0001, soft_max=100.0)
    target_size: FloatProperty(name="Largest Side", default=1.0, min=0.001, soft_max=100.0, unit="LENGTH")
    use_collection: BoolProperty(
        name="Wiro AI Collection",
        description="Put imported models in a collection named Wiro AI",
        default=True,
    )
    cleanup: BoolProperty(
        name="Single Object",
        description="Remove the empty parent a file brings, so a one-mesh model is one object",
        default=True,
    )


class WiroAIResult(bpy.types.PropertyGroup):
    slug: StringProperty()
    title: StringProperty()
    price: StringProperty()
    categories: StringProperty()


def _result_selected(self, context):
    wm = context.window_manager
    results = wm.wiro_ai_results
    if 0 <= wm.wiro_ai_result_index < len(results):
        settings = context.scene.wiro_ai
        slug = results[wm.wiro_ai_result_index].slug
        if settings.run_form.slug != slug:
            settings.run_form.slug = slug
            forms.ensure(settings.run_form, "run")


def _category_items(self, context):
    return [("ALL", "All Categories", "Search every category", 0)] + [
        (key, label, f"Models in the {label} category", index + 1)
        for index, (key, label) in enumerate(catalog.CATEGORY_LABELS.items())
    ]


class WiroAISettings(bpy.types.PropertyGroup):
    # -- 3D ------------------------------------------------------------------
    mode: EnumProperty(
        name="Mode",
        items=(
            ("TEXT", "Text to 3D", "Describe an object. An image model draws it, then a 3D model builds the mesh"),
            ("IMAGE", "Image to 3D", "Build a mesh from one picture, or several for a model that takes several"),
        ),
        default="TEXT",
    )
    prompt: StringProperty(
        name="Prompt",
        description="What to make, for example: a weathered bronze lantern with a glass panel",
        default="",
    )
    framing: BoolProperty(
        name="Frame for 3D",
        description=(
            "Ask the image model for one whole object, centered on a plain background, "
            "which is what image-to-3D models work best with"
        ),
        default=True,
    )
    use_cutout: BoolProperty(
        name="Remove Background",
        description="Cut the object out of the picture with a background removal model before the 3D model runs",
        default=False,
    )
    images: CollectionProperty(type=WiroAIFile)
    mesh_form: PointerProperty(type=WiroAIForm)
    reference_form: PointerProperty(type=WiroAIForm)
    cutout_form: PointerProperty(type=WiroAIForm)
    mesh_model: forms.model_enum(
        "mesh", "mesh_form", "3D Model", "The model that builds the mesh. The list comes from the Wiro AI catalog"
    )
    reference_model: forms.model_enum(
        "reference", "reference_form", "Image Model", "Draws the reference picture from your prompt"
    )
    cutout_model: forms.model_enum(
        "cutout", "cutout_form", "Cutout Model", "The model that removes the background from the picture"
    )
    import_options: PointerProperty(type=WiroAIImportOptions)

    # -- Texture -------------------------------------------------------------
    texture_mode: EnumProperty(
        name="Texture",
        items=(
            ("NEW", "New Texture", "Make a texture from a prompt and put it on the selected objects"),
            ("EDIT", "Edit Texture", "Change the active object's base color texture with a prompt"),
        ),
        default="NEW",
    )
    texture_prompt: StringProperty(
        name="Prompt",
        description="What the surface looks like, for example: mossy cobblestone",
        default="",
    )
    tileable: BoolProperty(
        name="Tileable",
        description="Ask for a seamless texture that repeats without visible edges",
        default=True,
    )
    texture_form: PointerProperty(type=WiroAIForm)
    texture_edit_form: PointerProperty(type=WiroAIForm)
    texture_model: forms.model_enum("texture", "texture_form", "Image Model", "Draws the texture from your prompt")
    texture_edit_model: forms.model_enum(
        "texture_edit", "texture_edit_form", "Edit Model", "Edits the current texture with your prompt"
    )
    mapping: EnumProperty(
        name="Mapping",
        items=(
            ("AUTO", "Auto", "UV coordinates when the mesh has a UV map, box projection otherwise"),
            ("UV", "UV", "Use the mesh's UV map"),
            ("BOX", "Box", "Project the texture from six sides, no UV map needed"),
        ),
        default="AUTO",
    )
    tiling: FloatProperty(name="Tiling", description="How many times the texture repeats", default=1.0, min=0.01)
    bump: FloatProperty(
        name="Bump",
        description="Strength of the bump derived from the texture's brightness",
        default=0.3,
        min=0.0,
        max=1.0,
    )
    roughness: FloatProperty(
        name="Roughness",
        description="Base roughness; darker parts of the texture come out a little rougher",
        default=0.6,
        min=0.0,
        max=1.0,
    )
    replace_materials: BoolProperty(
        name="Replace Materials",
        description="Replace every material on the objects. Off: put the new material in the active slot",
        default=True,
    )

    # -- Run any model -------------------------------------------------------
    run_search: StringProperty(name="Search", description="Words to look for in model names and descriptions")
    run_category: EnumProperty(name="Category", items=_category_items)
    run_form: PointerProperty(type=WiroAIForm)
    image_output: EnumProperty(
        name="Images",
        items=(
            ("LOAD", "Load", "Load returned images into Blender"),
            ("PLANE", "Plane", "Load them and add each one as a textured plane at the 3D cursor"),
        ),
        default="LOAD",
    )

    def ensure_forms(self) -> None:
        for attr, purpose in (
            ("mesh_form", "mesh"),
            ("reference_form", "reference"),
            ("cutout_form", "cutout"),
            ("texture_form", "texture"),
            ("texture_edit_form", "texture_edit"),
        ):
            form = getattr(self, attr)
            if not form.slug:
                form.slug = catalog.DEFAULTS[forms.PURPOSE_CATEGORY[purpose]]
            forms.ensure(form, purpose)
        if self.run_form.slug:
            forms.ensure(self.run_form, "run")


classes = (
    WiroAIFile,
    WiroAIParam,
    WiroAIForm,
    WiroAIImportOptions,
    WiroAIResult,
    WiroAISettings,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.wiro_ai = PointerProperty(type=WiroAISettings)
    bpy.types.WindowManager.wiro_ai_results = CollectionProperty(type=WiroAIResult)
    bpy.types.WindowManager.wiro_ai_result_index = IntProperty(default=-1, update=_result_selected)


def unregister() -> None:
    del bpy.types.WindowManager.wiro_ai_result_index
    del bpy.types.WindowManager.wiro_ai_results
    del bpy.types.Scene.wiro_ai
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
