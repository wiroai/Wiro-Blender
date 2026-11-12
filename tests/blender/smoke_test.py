# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline smoke test that runs inside Blender.

It enables the installed extension, runs both modes through the real
Generate operator against a fake Wiro AI API, and checks what lands in the
scene. No network, no API key.

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/smoke_test.py -- tests/fixtures/cube.glb

The extension must be installed first, for example with
    blender --command extension install-file -r user_default wiro_ai-1.0.0.zip
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


def tiny_png(path: str) -> None:
    width = height = 8
    raw = b"".join(b"\x00" + b"\xc8\x96\x64" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    with open(path, "wb") as handle:
        handle.write(png)


class FakeWiro:
    """Answers Run, Task/Detail and file downloads like the Wiro AI API does."""

    def __init__(self, glb: bytes, png: bytes, fail: bool = False):
        self.glb, self.png, self.fail = glb, png, fail
        self.requests = []
        self.tasks = {}

    def __call__(self, method, url, headers, body, timeout):
        assert headers.get("User-Agent", "").startswith("Wiro-Blender/"), headers.get("User-Agent")
        self.requests.append((method, url, headers.get("Content-Type", ""), body))
        if method == "GET":
            assert "x-api-key" not in headers, "the API key must not go to the file CDN"
            if url.endswith(".glb"):
                return 200, self.glb
            if url.endswith(".png"):
                return 200, self.png
            return 404, b""
        assert headers.get("x-api-key") == "test-key"
        path = url.split("/v1/", 1)[1]
        if path.startswith("Run/"):
            task_id = str(5000 + len(self.tasks))
            token = f"token-{task_id}"
            slug = path[len("Run/") :]
            is_image = slug.startswith(("google/", "openai/"))
            if is_image:
                outputs = [{"name": "0.png", "contenttype": "image/png", "url": f"https://cdn.example/{task_id}/0.png"}]
            else:
                outputs = [
                    {
                        "name": "0_untextured.glb",
                        "contenttype": "model/gltf-binary",
                        "url": f"https://cdn.example/{task_id}/0_untextured.glb",
                    },
                    {
                        "name": "0.glb",
                        "contenttype": "model/gltf-binary",
                        "url": f"https://cdn.example/{task_id}/0.glb",
                    },
                ]
            self.tasks[token] = {
                "id": task_id,
                "socketaccesstoken": token,
                "status": "task_postprocess_end",
                "pexit": "1" if self.fail and not is_image else "0",
                "debugerror": "Traceback...\nRuntimeError: no object found in the image" if self.fail else "",
                "debugoutput": "Generating 3D mesh...\nExporting GLB...\nGLB saved",
                "totalcost": "0.03" if is_image else "0.30",
                "outputs": outputs,
                "slug": slug,
            }
            return 200, json.dumps({"result": True, "taskid": task_id, "socketaccesstoken": token}).encode()
        if path == "Task/Detail":
            token = json.loads(body.decode())["tasktoken"]
            return 200, json.dumps({"result": True, "tasklist": [self.tasks[token]]}).encode()
        return 200, json.dumps({"result": False, "errors": [{"code": 0, "message": f"unknown path {path}"}]}).encode()

    def runs(self):
        return [(url.split("/v1/Run/", 1)[1], body) for method, url, _ct, body in self.requests if "/v1/Run/" in url]


def main() -> None:
    glb_path = sys.argv[sys.argv.index("--") + 1]
    with open(glb_path, "rb") as handle:
        glb = handle.read()

    bpy.ops.preferences.addon_enable(module=MODULE)
    package = importlib.import_module(MODULE)
    preferences = importlib.import_module(f"{package.__name__}.preferences")
    api = importlib.import_module(f"{package.__name__}.api")
    version = importlib.import_module(f"{package.__name__}.version")

    assert hasattr(bpy.types, "WIRO_AI_PT_generate"), "panel not registered"
    assert hasattr(bpy.ops.wiro_ai, "generate"), "operator not registered"
    scene = bpy.context.scene
    settings = scene.wiro_ai
    print("registered OK, defaults:", settings.mode, settings.mesh_model, settings.image_model)

    work = tempfile.mkdtemp(prefix="wiro-ai-smoke-")
    png_path = os.path.join(work, "input.png")
    tiny_png(png_path)
    with open(png_path, "rb") as handle:
        png = handle.read()

    fake = FakeWiro(glb, png)

    def fake_client(context=None):
        return api.Client(
            api.Credentials("test-key"), user_agent=version.user_agent(bpy.app.version_string), transport=fake
        )

    preferences.client = fake_client
    preferences.online = lambda: True
    preferences.has_key = lambda context=None: True

    # Text to 3D: Nano Banana 2.1, then Trellis-2.
    scene.cursor.location = (2.0, 0.0, 0.0)
    settings.mode = "TEXT"
    settings.prompt = "a small wooden chair"
    settings.mesh_model = "TRELLIS_2"
    settings.trellis_resolution = "512"
    assert abs(settings.price() - (0.0336 + 0.25)) < 1e-9, settings.price()
    assert bpy.ops.wiro_ai.generate() == {"FINISHED"}
    chair = bpy.data.objects.get("Wiro Trellis-2")
    assert chair is not None, [o.name for o in bpy.data.objects]
    assert chair["wiro_model"] == "microsoft/trellis-2"
    assert chair["wiro_prompt"] == "a small wooden chair"
    assert tuple(round(v, 4) for v in chair.location) == (2.0, 0.0, 0.0), tuple(chair.location)
    runs = fake.runs()
    assert [slug for slug, _ in runs] == ["google/nano-banana-2-1", "microsoft/trellis-2"], runs
    trellis_body = runs[1][1].decode("utf-8", "replace")
    assert "https://cdn.example/5000/0.png" in trellis_body, "the reference image URL goes to the 3D model"
    assert 'name="pipeline_type"\r\n\r\n512' in trellis_body
    print("text to 3D OK:", chair.name, [c.name for c in chair.children_recursive])

    # Image to 3D: upload a local picture to Hunyuan3D-2.1 without textures.
    settings.mode = "IMAGE"
    settings.image_path = png_path
    settings.mesh_model = "HUNYUAN3D_2_1"
    settings.hunyuan_texture = False
    assert abs(settings.price() - 0.30) < 1e-9, settings.price()
    assert bpy.ops.wiro_ai.generate() == {"FINISHED"}
    mesh = bpy.data.objects.get("Wiro Hunyuan3D-2.1")
    assert mesh is not None, [o.name for o in bpy.data.objects]
    slug, body = fake.runs()[-1]
    assert slug == "tencent/hunyuan3d-2-1"
    assert b'filename="input.png"' in body and png in body, "the picture is uploaded as a file"
    assert b'name="generate_texture"\r\n\r\nfalse' in body
    print("image to 3D OK:", mesh.name)

    # A failed task reports the model's last error line and imports nothing.
    fake.fail = True
    count = len(bpy.data.objects)
    try:
        result = bpy.ops.wiro_ai.generate()
    except RuntimeError as exc:  # operator errors are raised in background mode
        result = {"CANCELLED"}
        assert "no object found" in str(exc), exc
    assert result == {"CANCELLED"}, result
    assert len(bpy.data.objects) == count
    print("failure path OK")

    # Online access off: nothing is sent.
    fake.fail = False
    preferences.online = lambda: False
    sent = len(fake.requests)
    try:
        result = bpy.ops.wiro_ai.generate()
    except RuntimeError as exc:
        result = {"CANCELLED"}
        assert "Online Access" in str(exc), exc
    assert result == {"CANCELLED"} and len(fake.requests) == sent
    print("offline path OK")

    bpy.ops.preferences.addon_disable(module=MODULE)
    assert not hasattr(bpy.types, "WIRO_AI_PT_generate"), "panel still registered after disable"
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
