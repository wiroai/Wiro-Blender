# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline smoke test that runs inside Blender.

It enables the installed extension and runs every panel through its real
operator against a fake Wiro AI API, then checks what lands in the scene. No
network, no API key.

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/smoke_test.py -- tests/fixtures/cube.glb

The extension must be installed first, for example with
    blender --command extension install-file -r user_default dist/wiro_ai-1.1.0.zip
"""

import importlib
import json
import os
import struct
import sys
import tempfile
import zlib

import bpy

MODULE = os.environ.get("WIRO_AI_MODULE", "bl_ext.user_default.wiro_ai")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from tests.fakes import FakeTransport, default_outputs  # noqa: E402


def tiny_png(path: str, rgb=(200, 150, 100)) -> bytes:
    width = height = 8
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    if path:
        with open(path, "wb") as handle:
            handle.write(png)
    return png


def gaussian_ply(count: int = 50) -> bytes:
    """A tiny Gaussian splat file like the ones reconstruction models return."""
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity"]
    header = "ply\nformat binary_little_endian 1.0\n" + f"element vertex {count}\n"
    header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
    body = b"".join(struct.pack("<7f", i * 0.01, 0.0, 0.0, 1.0, 0.0, -1.0, 3.0) for i in range(count))
    return header.encode() + body


def nested_glb(path: str) -> bytes:
    """A GLB whose mesh sits under an empty parent, to test the Single Object option."""
    scene = bpy.context.scene
    empty = bpy.data.objects.new("Root", None)
    mesh = bpy.data.meshes.new("Box")
    mesh.from_pydata(
        [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0), (-1, -1, 3), (1, -1, 3), (1, 1, 3), (-1, 1, 3)],
        [],
        [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    )
    child = bpy.data.objects.new("Box", mesh)
    child.parent = empty
    scene.collection.objects.link(empty)
    scene.collection.objects.link(child)
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj in (empty, child))
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True)
    for obj in (child, empty):
        bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    with open(path, "rb") as handle:
        return handle.read()


class BlenderFake(FakeTransport):
    """FakeTransport that serves real files, so Blender can import them."""

    def __init__(self, files_by_ext: dict, **kwargs):
        super().__init__(**kwargs)
        self.by_ext = files_by_ext

    def __call__(self, method, url, headers, body, timeout):
        if method == "GET":
            self.requests.append({"method": method, "url": url, "headers": dict(headers), "body": body})
            assert "x-api-key" not in headers, "the API key must not go to the file CDN"
            ext = os.path.splitext(url)[1].lower()
            return (200, self.by_ext[ext]) if ext in self.by_ext else (404, b"")
        return super().__call__(method, url, headers, body, timeout)


def outputs_for(slug: str, task_id: str) -> list:
    base = f"https://cdn1.wiro.ai/{task_id}"
    if slug.startswith("acme/"):
        return [
            {"name": "0.png", "contenttype": "image/png", "url": f"{base}/0.png"},
            {"contenttype": "raw", "content": {"raw": "A short answer."}},
        ]
    return default_outputs(slug, task_id)


ACME = {
    "cleanslugowner": "acme",
    "cleanslugproject": "llm-image",
    "title": "Acme LLM Image",
    "parameters": [
        {
            "items": [
                {"id": "prompt", "type": "textarea", "required": True, "label": "Prompt"},
                {
                    "id": "style",
                    "type": "select",
                    "defaultvalue": "flat",
                    "options": [{"value": "flat", "text": "Flat"}, {"value": "photo", "text": "Photo"}],
                },
                {"id": "inputImage", "type": "combinefileinput", "maxinputlenght": 2, "label": "Image"},
            ]
        }
    ],
    "dynamicprice": [{"inputs": {}, "price": 0.01, "priceMethod": "cpr"}],
}


def expect_error(call, words: str) -> None:
    try:
        result = call()
    except RuntimeError as exc:  # operator errors are raised in background mode
        assert words in str(exc), exc
        return
    raise AssertionError(f"expected an error with {words!r}, got {result}")


def main() -> None:
    work = tempfile.mkdtemp(prefix="wiro-ai-smoke-")
    nested = nested_glb(os.path.join(work, "nested.glb"))

    bpy.ops.preferences.addon_enable(module=MODULE)
    api = importlib.import_module(f"{MODULE}.api")
    preferences = importlib.import_module(f"{MODULE}.preferences")
    runtime = importlib.import_module(f"{MODULE}.runtime")
    version = importlib.import_module(f"{MODULE}.version")
    panels = ("main", "generate_3d", "import_options", "texture", "run", "queue", "history")
    for name in panels:
        assert hasattr(bpy.types, f"WIRO_AI_PT_{name}"), f"WIRO_AI_PT_{name} not registered"

    fake = BlenderFake(
        {".glb": nested, ".png": tiny_png(""), ".ply": gaussian_ply(), ".mp4": b"\x00"},
        outputs=outputs_for,
        statuses=["task_postprocess_end"],
        costs={"google/nano-banana-2-1": "0.0336", "wiro/remove-background": "0.0012"},
        tools={
            "3d-generation": [
                {"cleanslugowner": "microsoft", "cleanslugproject": "trellis-2", "title": "Trellis-2"},
                {"cleanslugowner": "meshy", "cleanslugproject": "meshy-6", "title": "Meshy 6"},
            ],
            "": [{"cleanslugowner": "acme", "cleanslugproject": "llm-image", "title": "Acme LLM Image"}],
            "acme/llm-image": ACME,
        },
    )
    agent = version.user_agent(bpy.app.version_string)
    preferences.client = lambda context=None: api.Client(api.Credentials("test-key"), user_agent=agent, transport=fake)
    runtime.public_client = lambda: api.Client(None, user_agent=agent, transport=fake)
    preferences.online = lambda: True
    runtime.online = lambda: True
    preferences.has_key = lambda context=None: True

    scene = bpy.context.scene
    settings = scene.wiro_ai
    settings.ensure_forms()
    assert settings.mesh_form.built_slug == "microsoft/trellis-2", settings.mesh_form.built_slug
    assert settings.reference_form.built_slug == "google/nano-banana-2-1"
    faces = next(p for p in settings.mesh_form.params if p.pid == "decimation_target")
    assert faces.label == "Face limit" and faces.value_int == 200000
    print("forms OK")

    # Text to 3D with background removal and import options.
    scene.cursor.location = (2.0, 0.0, 0.0)
    settings.mode = "TEXT"
    settings.prompt = "a small wooden chair"
    settings.use_cutout = True
    options = settings.import_options
    options.origin, options.location, options.size, options.target_size = "BOTTOM", "CURSOR", "FIT", 2.0
    options.use_collection, options.cleanup = True, True
    assert bpy.ops.wiro_ai.generate() == {"FINISHED"}
    slugs = [r["url"].split("/Run/")[1] for r in fake.runs()]
    assert slugs == ["google/nano-banana-2-1", "wiro/remove-background", "microsoft/trellis-2"], slugs
    chair = bpy.data.objects.get("Wiro Trellis-2")
    assert chair is not None and chair.type == "MESH" and chair.parent is None, [o.name for o in bpy.data.objects]
    assert chair.name in bpy.data.collections["Wiro AI"].objects
    assert abs(max(chair.dimensions) - 2.0) < 1e-4, tuple(chair.dimensions)
    low_z = min((chair.matrix_world @ v.co).z for v in chair.data.vertices)
    assert abs(low_z) < 1e-4 and abs(chair.location.x - 2.0) < 1e-4, (low_z, tuple(chair.location))
    assert chair["wiro_prompt"] == "a small wooden chair"
    print("text to 3D with cutout and import options OK")

    # Image to 3D with several pictures, on a model that takes several.
    settings.use_cutout = False
    settings.mode = "IMAGE"
    settings.mesh_model = "tencent/hy-world-2-0-world-reconstruction"
    assert settings.mesh_form.built_slug == "tencent/hy-world-2-0-world-reconstruction"
    for index in range(3):
        entry = settings.images.add()
        entry.path = os.path.join(work, f"view{index}.png")
        tiny_png(entry.path, (index * 60, 100, 200))
    options.size, options.scale = "SCALE", 1.0
    assert bpy.ops.wiro_ai.generate() == {"FINISHED"}
    run = fake.runs()[-1]
    assert run["url"].endswith("/Run/tencent/hy-world-2-0-world-reconstruction")
    assert run["body"].count(b'name="inputImage"; filename="view') == 3
    cloud = bpy.data.objects.get("Wiro HY-World 2.0")
    assert cloud is not None and len(cloud.data.vertices) == 50, [o.name for o in bpy.data.objects]
    assert "Col" in cloud.data.color_attributes and cloud.modifiers.get("Wiro points")
    print("multi-picture 3D as a point cloud OK")

    # New texture on selected objects: one with a UV map, one without.
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    cube = bpy.context.active_object
    bare_mesh = bpy.data.meshes.new("bare")
    bare_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    bare = bpy.data.objects.new("Bare", bare_mesh)
    scene.collection.objects.link(bare)
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj in (cube, bare))
    settings.texture_mode = "NEW"
    settings.texture_prompt = "mossy stone"
    assert bpy.ops.wiro_ai.generate_texture() == {"FINISHED"}
    material = cube.active_material
    assert material is not None and material.name.startswith("Wiro mossy stone"), material
    types = {n.type for n in material.node_tree.nodes}
    assert {"TEX_IMAGE", "BUMP", "VALTORGB", "MAPPING"} <= types, types
    image_node = next(n for n in material.node_tree.nodes if n.type == "TEX_IMAGE")
    assert image_node.projection == "FLAT"
    bare_node = next(n for n in bare.active_material.node_tree.nodes if n.type == "TEX_IMAGE")
    assert bare_node.projection == "BOX", "a mesh without a UV map gets box projection"
    texture_run = fake.runs()[-1]
    assert b"mossy stone. A seamless, tileable texture" in texture_run["body"]
    print("new texture OK")

    # Edit the texture of an object that has one.
    old = bpy.data.images.new("old texture", 8, 8)
    old.pack()
    image_node.image = old
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj == cube)
    bpy.context.view_layer.objects.active = cube
    settings.texture_mode = "EDIT"
    settings.texture_prompt = "make it rusty"
    assert bpy.ops.wiro_ai.generate_texture() == {"FINISHED"}
    edit_run = fake.runs()[-1]
    assert b'name="inputImage"; filename=' in edit_run["body"] and b"make it rusty" in edit_run["body"]
    new_image = next(n for n in cube.active_material.node_tree.nodes if n.type == "TEX_IMAGE").image
    assert new_image is not None and new_image.name.startswith("Wiro "), new_image
    print("texture edit OK")

    # Search, pick and run any model.
    settings.run_search = "llm"
    assert bpy.ops.wiro_ai.search_models() == {"FINISHED"}
    wm = bpy.context.window_manager
    assert [r.slug for r in wm.wiro_ai_results] == ["acme/llm-image"]
    wm.wiro_ai_result_index = 0
    assert settings.run_form.built_slug == "acme/llm-image", settings.run_form.built_slug
    expect_error(lambda: bpy.ops.wiro_ai.run_model(), "Fill in")
    next(p for p in settings.run_form.params if p.pid == "prompt").value_text = "a poster"
    settings.image_output = "PLANE"
    assert bpy.ops.wiro_ai.run_model() == {"FINISHED"}
    run = fake.runs()[-1]
    assert run["url"].endswith("/Run/acme/llm-image") and b'name="style"\r\n\r\nflat' in run["body"]
    assert bpy.data.texts.get("Wiro llm-image answer") is not None
    assert any(obj.name.startswith("Wiro llm-image") for obj in bpy.data.objects), "image added as a plane"
    print("run any model OK")

    # Refresh: model lists and balance.
    version_before = runtime.catalog.version
    assert bpy.ops.wiro_ai.refresh() == {"FINISHED"}
    assert runtime.catalog.version > version_before
    assert "meshy/meshy-6" in [row["slug"] for row in runtime.catalog.models("3d-generation")]
    assert runtime.status()["balance"] == 12.5
    print("refresh OK")

    # History.
    entries = runtime.history.entries
    kinds = [entry["kind"] for entry in entries]
    assert kinds[:5] == ["run", "texture", "texture", "3d", "3d"], kinds
    first_3d = next(e for e in entries if e["kind"] == "3d" and e.get("reference"))
    count = len(bpy.data.objects)
    assert bpy.ops.wiro_ai.history(entry_id=first_3d["id"], action="IMPORT") == {"FINISHED"}
    assert len(bpy.data.objects) == count + 1
    assert bpy.ops.wiro_ai.history(entry_id=first_3d["id"], action="PICTURE") == {"FINISHED"}
    assert settings.mode == "IMAGE" and settings.images[0].path == first_3d["reference"]
    assert bpy.ops.wiro_ai.history(entry_id=first_3d["id"], action="REUSE") == {"FINISHED"}
    assert settings.prompt == "a small wooden chair" and settings.mode == "TEXT"
    with open(runtime.history.path) as handle:
        assert "test-key" not in handle.read(), "the history never holds the key"
    print("history OK")

    # A failed task imports nothing; online access off sends nothing.
    fake.pexit, fake.debugerror = "1", "Traceback\nRuntimeError: no object found in the image"
    count = len(bpy.data.objects)
    expect_error(lambda: bpy.ops.wiro_ai.generate(), "no object found")
    assert len(bpy.data.objects) == count
    fake.pexit = "0"
    runtime.online = preferences.online = lambda: False
    sent = len(fake.requests)
    expect_error(lambda: bpy.ops.wiro_ai.generate(), "Online Access")
    assert len(fake.requests) == sent
    print("failure and offline paths OK")

    bpy.ops.preferences.addon_disable(module=MODULE)
    assert not hasattr(bpy.types, "WIRO_AI_PT_generate_3d"), "panel still registered after disable"
    print(json.dumps({"runs": len(fake.runs()), "history": len(entries)}))
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
