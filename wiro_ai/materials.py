# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Textures from image models, put on objects as materials.

The image model makes the base color. The material derives the rest from it:
a bump from the texture's brightness and a roughness that follows it, so the
surface has relief and varied shine without extra runs. Every node stays
editable in the Shader Editor.
"""

import contextlib
import os

import bpy

SURFACE_TYPES = ("MESH", "CURVE", "SURFACE", "META", "FONT")


def surface_objects(objects) -> list:
    return [obj for obj in objects if obj.type in SURFACE_TYPES and getattr(obj, "data", None) is not None]


def _has_uv(obj) -> bool:
    return obj.type == "MESH" and len(obj.data.uv_layers) > 0


def build_material(name: str, image, *, mapping: str = "UV", tiling: float = 1.0, bump: float = 0.3,
                   roughness: float = 0.6):  # fmt: skip
    """A Principled BSDF material driven by one texture."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if shader is None:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.location = (300, 300)
    coords = nodes.new("ShaderNodeTexCoord")
    coords.location = (-1100, 300)
    transform = nodes.new("ShaderNodeMapping")
    transform.location = (-900, 300)
    transform.inputs["Scale"].default_value = (tiling, tiling, tiling)
    texture = nodes.new("ShaderNodeTexImage")
    texture.location = (-650, 300)
    texture.image = image
    texture.label = "Wiro AI texture"
    if mapping == "BOX":
        texture.projection = "BOX"
        texture.projection_blend = 0.25
        links.new(coords.outputs["Object"], transform.inputs["Vector"])
    else:
        links.new(coords.outputs["UV"], transform.inputs["Vector"])
    links.new(transform.outputs["Vector"], texture.inputs["Vector"])
    links.new(texture.outputs["Color"], shader.inputs["Base Color"])

    gray = nodes.new("ShaderNodeRGBToBW")
    gray.location = (-350, 0)
    links.new(texture.outputs["Color"], gray.inputs["Color"])

    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-150, 100)
    ramp.label = "Roughness from brightness"
    low, high = ramp.color_ramp.elements[0], ramp.color_ramp.elements[1]
    rough = max(0.0, min(1.0, roughness))
    low.color = (min(1.0, rough + 0.15),) * 3 + (1.0,)
    high.color = (max(0.0, rough - 0.15),) * 3 + (1.0,)
    links.new(gray.outputs["Val"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], shader.inputs["Roughness"])

    bump_node = nodes.new("ShaderNodeBump")
    bump_node.location = (50, -150)
    bump_node.inputs["Strength"].default_value = bump
    height = bump_node.inputs.get("Height")
    if height is not None:
        links.new(gray.outputs["Val"], height)
    links.new(bump_node.outputs["Normal"], shader.inputs["Normal"])
    return material


def apply_texture(objects, image_path: str, *, name: str, mapping: str = "AUTO", tiling: float = 1.0,
                  bump: float = 0.3, roughness: float = 0.6, replace: bool = True) -> list:  # fmt: skip
    """Put a texture on the objects. Returns the names of the materials made."""
    targets = surface_objects(objects)
    if not targets:
        raise RuntimeError("Select a mesh, curve or text object to texture.")
    image = bpy.data.images.load(image_path, check_existing=True)
    made = {}
    for obj in targets:
        projection = mapping if mapping != "AUTO" else ("UV" if _has_uv(obj) else "BOX")
        if projection == "UV" and not _has_uv(obj):
            projection = "BOX"
        material = made.get(projection)
        if material is None:
            material = build_material(f"{name} ({projection.lower()})" if len(made) else name, image,
                                      mapping=projection, tiling=tiling, bump=bump, roughness=roughness)  # fmt: skip
            made[projection] = material
        slots = obj.data.materials
        if replace or len(slots) == 0:
            slots.clear()
            slots.append(material)
        else:
            obj.active_material = material
    if not bpy.app.background:
        with contextlib.suppress(RuntimeError):
            bpy.ops.ed.undo_push(message="Apply Wiro AI texture")
    return [m.name for m in made.values()]


def base_color_node(obj):
    """The Image Texture node feeding the Base Color of the active material, if any."""
    material = obj.active_material if obj else None
    if material is None or not material.use_nodes:
        return None
    shader = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if shader is None:
        return None
    socket = shader.inputs.get("Base Color")
    seen = set()
    stack = [link.from_node for link in socket.links] if socket else []
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node
        for socket_in in node.inputs:
            stack.extend(link.from_node for link in socket_in.links)
    return None


def base_color_image(obj):
    node = base_color_node(obj)
    return node.image if node else None


def replace_base_color(obj, image_path: str) -> str:
    """Swap the base color texture of the active material for a new image. Returns its name."""
    node = base_color_node(obj)
    if node is None:
        raise RuntimeError(f"{obj.name} has no base color texture to edit.")
    image = bpy.data.images.load(image_path, check_existing=True)
    image.name = f"Wiro {os.path.splitext(os.path.basename(image_path))[0]}"
    material = obj.active_material
    if material.users > 1:
        material = material.copy()
        obj.active_material = material
        node = base_color_node(obj)
    node.image = image
    if not bpy.app.background:
        with contextlib.suppress(RuntimeError):
            bpy.ops.ed.undo_push(message="Edit texture with Wiro AI")
    return image.name
