# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Brings model outputs into Blender: 3D files with Blender's own importers,
plus pictures, sound, video and text."""

import contextlib
import os

import bpy
from mathutils import Matrix, Vector

COLLECTION_NAME = "Wiro AI"
SH_C0 = 0.28209479177387814  # zeroth spherical harmonic, for Gaussian splat colors


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


# -- 3D files ------------------------------------------------------------------------------


def _run_importer(path: str) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        result = bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        result = bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".fbx":
        result = bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".ply":
        if is_gaussian_ply(path):
            load_gaussian_ply(path)
            return
        result = bpy.ops.wm.ply_import(filepath=path)
    elif ext == ".stl":
        result = bpy.ops.wm.stl_import(filepath=path)
    elif ext in (".usd", ".usda", ".usdc", ".usdz"):
        result = bpy.ops.wm.usd_import(filepath=path)
    elif ext == ".abc":
        result = bpy.ops.wm.alembic_import(filepath=path)
    else:
        raise RuntimeError(f"Blender has no importer for {ext} files.")
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender could not import {os.path.basename(path)}.")


def _descendants(obj) -> list:
    out, stack = [], list(obj.children)
    while stack:
        child = stack.pop()
        out.append(child)
        stack.extend(child.children)
    return out


def _bounds(objects) -> tuple:
    """World-space bounding box (min, max) of the mesh-like objects, or None."""
    points = []
    for obj in objects:
        if obj.type in ("MESH", "CURVE", "SURFACE", "FONT", "META", "POINTCLOUD") and obj.bound_box:
            points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return None
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return low, high


def _cleanup(roots: list) -> list:
    """Replace an empty parent that holds a single mesh with the mesh itself."""
    result = []
    for root in roots:
        children = _descendants(root)
        meshes = [c for c in children if c.type == "MESH"]
        if root.type == "EMPTY" and len(meshes) == 1 and len(children) == 1:
            mesh = meshes[0]
            world = mesh.matrix_world.copy()
            mesh.parent = None
            mesh.matrix_world = world
            bpy.data.objects.remove(root, do_unlink=True)
            result.append(mesh)
        else:
            result.append(root)
    return result


def _set_origin(obj, pivot_world: Vector) -> None:
    """Move obj's origin to a world point without moving what it shows."""
    local = obj.matrix_world.inverted() @ pivot_world
    if obj.type == "MESH" and obj.data.users == 1:
        obj.data.transform(Matrix.Translation(-local))
        for child in obj.children:
            child.matrix_parent_inverse = Matrix.Translation(-local) @ child.matrix_parent_inverse
        obj.matrix_world = obj.matrix_world @ Matrix.Translation(local)
    else:
        worlds = {child: child.matrix_world.copy() for child in obj.children}
        obj.matrix_world = Matrix.Translation(pivot_world - obj.matrix_world.translation) @ obj.matrix_world
        for child, world in worlds.items():
            child.matrix_world = world


def _move_to_collection(objects) -> None:
    scene = bpy.context.scene
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION_NAME)
    if collection.name not in scene.collection.children:
        with contextlib.suppress(RuntimeError):
            scene.collection.children.link(collection)
    for obj in objects:
        for owner in list(obj.users_collection):
            if owner != collection:
                owner.objects.unlink(obj)
        if obj.name not in collection.objects:
            collection.objects.link(obj)


def arrange(roots: list, options, cursor: Vector) -> list:
    """Apply the import options to freshly imported top-level objects."""
    if options is None:
        return roots
    if getattr(options, "cleanup", False):
        roots = _cleanup(roots)
    every = list(roots) + [d for root in roots for d in _descendants(root)]
    bounds = _bounds(every)
    origin = getattr(options, "origin", "KEEP")
    pivot = None
    if bounds is not None and origin in ("BOTTOM", "CENTER"):
        low, high = bounds
        center = (low + high) / 2
        pivot = Vector((center.x, center.y, low.z)) if origin == "BOTTOM" else center
        if len(roots) == 1:
            _set_origin(roots[0], pivot)
    if pivot is None:
        pivot = roots[0].matrix_world.translation.copy() if roots else Vector()
    # Size.
    factor = 1.0
    if getattr(options, "size", "SCALE") == "FIT" and bounds is not None:
        largest = max(bounds[1] - bounds[0])
        if largest > 0:
            factor = options.target_size / largest
    else:
        factor = getattr(options, "scale", 1.0) or 1.0
    location = getattr(options, "location", "KEEP")
    target = cursor if location == "CURSOR" else Vector() if location == "ORIGIN" else pivot
    move = Matrix.Translation(target) @ Matrix.Scale(factor, 4) @ Matrix.Translation(-pivot)
    for root in roots:
        root.matrix_world = move @ root.matrix_world
    if getattr(options, "use_collection", False):
        _move_to_collection(list(roots) + [d for root in roots for d in _descendants(root)])
    return roots


def import_3d(path: str, *, name: str, options=None, metadata: dict | None = None) -> list:
    """Import a 3D file into the scene. Returns the new top-level objects."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    override = view3d_override()
    before = set(bpy.data.objects)
    with bpy.context.temp_override(**override):
        _run_importer(path)
        # Matrices and bounds of new objects are only current after an update.
        bpy.context.view_layer.update()
        scene = bpy.context.scene
        new_objects = [obj for obj in bpy.data.objects if obj not in before]
        roots = [obj for obj in new_objects if obj.parent is None or obj.parent not in new_objects]
        roots = arrange(roots, options, scene.cursor.location.copy())
        for index, root in enumerate(roots):
            root.name = name if index == 0 else f"{name}.{index:03d}"
            for key, value in (metadata or {}).items():
                if value not in (None, ""):
                    root[key] = value
        everything = set(roots)
        for root in roots:
            everything.update(_descendants(root))
        view_layer = bpy.context.view_layer
        # Removing the empty parent leaves a stale entry until the view layer updates.
        view_layer.update()
        for obj in view_layer.objects:
            if obj is not None:
                obj.select_set(obj in everything)
        if roots and roots[0].name in view_layer.objects:
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


# -- Gaussian splat PLY (multi-view reconstruction) ------------------------------------------

_PLY_TYPES = {
    "char": "i1",
    "uchar": "u1",
    "short": "i2",
    "ushort": "u2",
    "int": "i4",
    "uint": "u4",
    "float": "f4",
    "double": "f8",
    "int8": "i1",
    "uint8": "u1",
    "int16": "i2",
    "uint16": "u2",
    "int32": "i4",
    "uint32": "u4",
    "float32": "f4",
    "float64": "f8",
}


def _ply_header(path: str) -> tuple:
    with open(path, "rb") as handle:
        if handle.readline().strip() != b"ply":
            return None, 0, []
        fmt, count, props, element = "", 0, [], ""
        while True:
            line = handle.readline()
            if not line:
                return None, 0, []
            words = line.decode("ascii", "replace").split()
            if not words:
                continue
            if words[0] == "format":
                fmt = words[1]
            elif words[0] == "element":
                element = words[1]
                if element == "vertex":
                    count = int(words[2])
            elif words[0] == "property" and element == "vertex" and words[1] != "list":
                props.append((words[2], _PLY_TYPES.get(words[1], "f4")))
            elif words[0] == "end_header":
                return (fmt, handle.tell()), count, props


def is_gaussian_ply(path: str) -> bool:
    header, _count, props = _ply_header(path)
    names = {name for name, _type in props}
    return header is not None and header[0] == "binary_little_endian" and {"x", "y", "z", "f_dc_0"} <= names


def load_gaussian_ply(path: str):
    """A Gaussian splat file as a colored point cloud: one point per splat."""
    import numpy as np

    (fmt, offset), count, props = _ply_header(path)
    dtype = np.dtype([(name, "<" + kind) for name, kind in props])
    data = np.fromfile(path, dtype=dtype, count=count, offset=offset)
    if "opacity" in data.dtype.names:
        alpha = 1.0 / (1.0 + np.exp(-data["opacity"].astype(np.float32)))
        data = data[alpha > 0.05]
        alpha = alpha[alpha > 0.05]
    else:
        alpha = np.ones(len(data), dtype=np.float32)
    # Reconstructions use camera axes (X right, Y down, Z forward). Blender is
    # Z up with Y forward, so (x, y, z) becomes (x, z, -y).
    points = np.stack([data["x"], data["z"], -data["y"]], axis=1).astype(np.float32)
    color = np.ones((len(data), 4), dtype=np.float32)
    for channel in range(3):
        key = f"f_dc_{channel}"
        if key in data.dtype.names:
            srgb = np.clip(0.5 + SH_C0 * data[key], 0.0, 1.0)
            # Splat colors are display (sRGB) values; Blender's float colors are linear.
            color[:, channel] = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    color[:, 3] = alpha
    name = os.path.splitext(os.path.basename(path))[0]
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(points))
    mesh.vertices.foreach_set("co", points.ravel())
    attribute = mesh.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    attribute.data.foreach_set("color", color.ravel())
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    _point_display(obj, mesh)
    return obj


def _point_display(obj, mesh) -> None:
    """Show the points as small colored spheres in the viewport and in renders."""
    material = bpy.data.materials.new(f"{obj.name} points")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    # Unlit points: the color comes only from emission, as in Gaussian splat viewers.
    for node in list(nodes):
        if node.type != "OUTPUT_MATERIAL":
            nodes.remove(node)
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
    attribute = nodes.new("ShaderNodeAttribute")
    attribute.attribute_name = "Col"
    attribute.location = (-400, 200)
    emission = nodes.new("ShaderNodeEmission")
    emission.location = (-150, 200)
    links.new(attribute.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    mesh.materials.append(material)
    modifier = obj.modifiers.new("Wiro points", "NODES")
    group = bpy.data.node_groups.new(f"{obj.name} points", "GeometryNodeTree")
    if hasattr(group, "interface"):
        group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    g_nodes, g_links = group.nodes, group.links
    group_in = g_nodes.new("NodeGroupInput")
    group_out = g_nodes.new("NodeGroupOutput")
    to_points = g_nodes.new("GeometryNodeMeshToPoints")
    radius = to_points.inputs.get("Radius")
    if radius is not None:
        radius.default_value = 0.004
    set_material = g_nodes.new("GeometryNodeSetMaterial")
    set_material.inputs["Material"].default_value = material
    g_links.new(group_in.outputs[0], to_points.inputs["Mesh"])
    g_links.new(to_points.outputs["Points"], set_material.inputs["Geometry"])
    g_links.new(set_material.outputs["Geometry"], group_out.inputs[0])
    modifier.node_group = group


# -- pictures, sound, video, text ------------------------------------------------------------


def load_image(path: str):
    return bpy.data.images.load(path, check_existing=True)


def image_plane(image, *, name: str, location: Vector, height: float = 1.0):
    """A plane with the picture's aspect ratio and the picture as its material."""
    width_px, height_px = image.size[0] or 1, image.size[1] or 1
    width = height * width_px / height_px
    mesh = bpy.data.meshes.new(name)
    half_w = width / 2
    mesh.from_pydata([(-half_w, 0, 0), (half_w, 0, 0), (half_w, 0, height), (-half_w, 0, height)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop, coords in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1)), strict=True):
        loop.uv = coords
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.location = (-400, 200)
    if shader is not None:
        links.new(texture.outputs["Color"], shader.inputs["Base Color"])
        links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.collection.objects.link(obj)
    return obj


def show_image(image) -> None:
    """Show the image in an open Image Editor, if there is one."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = image
                return


def load_text(name: str, content: str):
    text = bpy.data.texts.new(name)
    text.from_string(content)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "TEXT_EDITOR":
                area.spaces.active.text = text
                break
    return text


def load_outputs(outputs, *, text: str = "", name: str = "Wiro", image_output: str = "LOAD", import_options=None):
    """Bring every downloaded output of a run into Blender. Returns what was made."""
    made = []
    cursor = bpy.context.scene.cursor.location.copy()
    offset = 0.0
    for output in outputs:
        if not output.path or not os.path.isfile(output.path):
            continue
        if output.kind == "3d":
            roots = import_3d(output.path, name=name, options=import_options)
            made.extend(f"Object {root.name}" for root in roots)
        elif output.kind == "image":
            image = load_image(output.path)
            made.append(f"Image {image.name}")
            if image_output == "PLANE":
                plane = image_plane(image, name=f"{name} {image.name}", location=cursor + Vector((offset, 0, 0)))
                offset += plane.dimensions.x + 0.1
                made.append(f"Object {plane.name}")
            else:
                show_image(image)
        elif output.kind == "video":
            clip = bpy.data.movieclips.load(output.path, check_existing=True)
            made.append(f"Movie clip {clip.name}")
        elif output.kind == "audio":
            sound = bpy.data.sounds.load(output.path, check_existing=True)
            made.append(f"Sound {sound.name}")
        elif output.kind == "text":
            with open(output.path, encoding="utf-8", errors="replace") as handle:
                block = load_text(os.path.basename(output.path), handle.read())
            made.append(f"Text {block.name}")
    if text:
        block = load_text(f"{name} answer", text)
        made.append(f"Text {block.name}")
    return made
