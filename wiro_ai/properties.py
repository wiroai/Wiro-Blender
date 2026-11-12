# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Panel settings, stored per scene. The API key lives in the add-on
preferences instead, so it never ends up inside a .blend file.

No "from __future__ import annotations" here: Blender reads the property
annotations of these classes at registration and needs real objects.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import catalog


def path_options() -> set:
    """Lets a file or folder field take paths relative to the .blend file ("//").

    Newer Blender releases mark a "//" path as missing without this option.
    Older ones, such as 4.2, do not have the option, and the add-on resolves
    "//" paths itself either way.
    """
    if "is_path_supports_blend_relative" in bpy.types.Property.bl_rna.properties:
        return {"PATH_SUPPORTS_BLEND_RELATIVE"}
    return set()


def _image_model_items():
    return [
        (m.key, m.label, f"{m.description}. {catalog.format_price(m.price)} per image") for m in catalog.IMAGE_MODELS
    ]


def _mesh_model_items():
    return [(m.key, m.label, m.description) for m in catalog.MESH_MODELS]


def _detail_items(key: str):
    model = catalog.mesh_model(key)
    items = []
    for value, label, price, _seconds in model.details:
        note = f"{catalog.format_price(price)} per run" if price is not None else "Same price at every resolution"
        items.append((value, label, note))
    return items


class WiroAISettings(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="Mode",
        items=(
            ("TEXT", "Text to 3D", "Describe an object. An image model draws it, then a 3D model builds the mesh"),
            ("IMAGE", "Image to 3D", "Build a mesh from a picture of one object"),
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
    image_model: EnumProperty(
        name="Image model",
        description="Draws the reference picture from your prompt",
        items=_image_model_items(),
    )
    image_path: StringProperty(
        name="Image",
        description="A PNG, JPEG or WebP picture of one object. A plain background works best",
        subtype="FILE_PATH",
        options=path_options(),
        default="",
    )
    mesh_model: EnumProperty(
        name="3D model",
        description="The image-to-3D model that builds the mesh",
        items=_mesh_model_items(),
    )
    trellis_resolution: EnumProperty(
        name="Resolution",
        description="Voxel resolution. Higher is more detailed and takes longer",
        items=_detail_items("TRELLIS_2"),
        default="1024_cascade",
    )
    pixal_resolution: EnumProperty(
        name="Resolution",
        description="Voxel resolution. Higher is more detailed and takes longer",
        items=_detail_items("PIXAL3D"),
        default="1024_cascade",
    )
    hunyuan_octree: EnumProperty(
        name="Octree resolution",
        description="Resolution of the volume the mesh is extracted from. Higher gives finer geometry",
        items=_detail_items("HUNYUAN3D_2_1"),
        default="256",
    )
    hunyuan_texture: BoolProperty(
        name="Textures",
        description="Paint PBR textures on the mesh. Turn off for a plain white mesh, which is faster and cheaper",
        default=True,
    )
    remove_background: BoolProperty(
        name="Remove background",
        description="Cut the object out of the picture before building the mesh",
        default=True,
    )
    texture_size: EnumProperty(
        name="Texture size",
        description="Size of the PBR texture maps in the GLB",
        items=[(size, size, f"{size} x {size} pixels") for size in catalog.TEXTURE_SIZES],
        default="2048",
    )
    face_limit: IntProperty(
        name="Face limit",
        description="Target number of faces after simplification. Hunyuan3D-2.1 keeps at most 200,000",
        default=200_000,
        min=10_000,
        max=2_000_000,
        soft_max=1_000_000,
        step=10_000,
    )
    seed: IntProperty(
        name="Seed",
        description="Use the same seed and inputs to get the same mesh again. 0 picks a random seed",
        default=0,
        min=0,
        max=2_147_483_647,
    )
    place_at_cursor: BoolProperty(
        name="Place at 3D cursor",
        description="Move the imported model to the 3D cursor",
        default=True,
    )
    show_options: BoolProperty(name="More options", default=False)

    def mesh_settings(self) -> catalog.MeshSettings:
        detail = {
            "TRELLIS_2": self.trellis_resolution,
            "PIXAL3D": self.pixal_resolution,
            "HUNYUAN3D_2_1": self.hunyuan_octree,
        }.get(self.mesh_model, "")
        return catalog.MeshSettings(
            detail=detail,
            texture_size=self.texture_size,
            face_limit=self.face_limit,
            seed=self.seed,
            textured=self.hunyuan_texture,
            remove_background=self.remove_background,
        )

    def price(self) -> float:
        model = catalog.mesh_model(self.mesh_model)
        total = catalog.mesh_price(model, self.mesh_settings())
        if self.mode == "TEXT":
            total += catalog.image_model(self.image_model).price
        return total


classes = (WiroAISettings,)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.wiro_ai = bpy.props.PointerProperty(type=WiroAISettings)


def unregister() -> None:
    del bpy.types.Scene.wiro_ai
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
