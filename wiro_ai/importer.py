# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Imports a generated GLB with Blender's own glTF importer."""

import contextlib
import os

import bpy


def view3d_override(context=None) -> dict:
    """Context members of a 3D Viewport, for operators run from a timer."""
    context = context or bpy.context
    wm = context.window_manager
    for window in getattr(wm, "windows", []):
        screen = window.screen
        for area in screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                members = {"window": window, "screen": screen, "area": area}
                if region is not None:
                    members["region"] = region
                return members
    windows = getattr(wm, "windows", [])
    if windows:
        return {"window": windows[0], "screen": windows[0].screen}
    return {}


def import_glb(filepath: str, *, name: str, place_at_cursor: bool = True, metadata: dict | None = None) -> list:
    """Import a GLB into the active collection. Returns the new top-level objects."""
    if not os.path.isfile(filepath):
        raise FileNotFoundError(filepath)
    override = view3d_override()
    before = set(bpy.data.objects)
    with bpy.context.temp_override(**override):
        result = bpy.ops.import_scene.gltf(filepath=filepath)
        if "FINISHED" not in result:
            raise RuntimeError(f"The glTF importer could not read {os.path.basename(filepath)}.")
        scene = bpy.context.scene
        new_objects = [obj for obj in bpy.data.objects if obj not in before]
        roots = [obj for obj in new_objects if obj.parent is None or obj.parent not in new_objects]
        cursor = scene.cursor.location.copy()
        for index, root in enumerate(roots):
            root.name = name if index == 0 else f"{name}.{index:03d}"
            if place_at_cursor:
                root.location = root.location + cursor
            for key, value in (metadata or {}).items():
                if value not in (None, ""):
                    root[key] = value
        if roots:
            view_layer = bpy.context.view_layer
            for obj in view_layer.objects:
                obj.select_set(obj in new_objects)
            view_layer.objects.active = roots[0]
        if not bpy.app.background:
            with contextlib.suppress(RuntimeError):
                bpy.ops.ed.undo_push(message="Import Wiro AI model")
    return roots


def mesh_stats(objects) -> dict:
    """Vertex, face and material counts of the given objects and their children."""
    seen = set()
    stack = list(objects)
    vertices = faces = 0
    materials = set()
    while stack:
        obj = stack.pop()
        if obj in seen:
            continue
        seen.add(obj)
        stack.extend(obj.children)
        if obj.type == "MESH":
            vertices += len(obj.data.vertices)
            faces += len(obj.data.polygons)
            materials.update(slot.material.name for slot in obj.material_slots if slot.material)
    return {"objects": len(seen), "vertices": vertices, "faces": faces, "materials": sorted(materials)}
