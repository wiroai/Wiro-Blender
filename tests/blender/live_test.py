# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live test: one real generation through the Generate operator, in Blender's
background mode. It calls the Wiro AI API and is billed to the key's wallet,
so it is not part of CI.

    WIRO_API_KEY=... blender --background --factory-startup --online-mode \
        --python-exit-code 1 --python tests/blender/live_test.py -- \
        --mode text --prompt "a wooden treasure chest" --model TRELLIS_2 --detail 1024_cascade \
        --save-blend /tmp/chest.blend

The key is read from WIRO_API_KEY (and WIRO_API_SECRET for signature
projects); it is never printed.
"""

import argparse
import importlib
import json
import os
import sys

import bpy

MODULE = os.environ.get("WIRO_AI_MODULE", "bl_ext.user_default.wiro_ai")


def parse_args():
    parser = argparse.ArgumentParser(prog="live_test.py")
    parser.add_argument("--mode", choices=("text", "image"), default="text")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--image", default="")
    parser.add_argument("--image-model", default="NANO_BANANA_2_1")
    parser.add_argument("--model", default="TRELLIS_2")
    parser.add_argument("--detail", default="")
    parser.add_argument("--texture-size", default="2048")
    parser.add_argument("--faces", type=int, default=200_000)
    parser.add_argument("--untextured", action="store_true", help="Hunyuan3D-2.1 without textures")
    parser.add_argument("--save-blend", default="")
    parser.add_argument("--report", default="", help="Write the result as JSON to this file")
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def main():
    args = parse_args()
    if not bpy.app.online_access:
        raise SystemExit("Start Blender with --online-mode for the live test.")
    bpy.ops.preferences.addon_enable(module=MODULE)
    runtime = importlib.import_module(f"{MODULE}.runtime")

    # Start from an empty scene.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    settings = bpy.context.scene.wiro_ai
    settings.mode = args.mode.upper()
    settings.prompt = args.prompt
    settings.image_path = os.path.abspath(args.image) if args.image else ""
    settings.image_model = args.image_model
    settings.mesh_model = args.model
    if args.detail:
        if args.model == "TRELLIS_2":
            settings.trellis_resolution = args.detail
        elif args.model == "PIXAL3D":
            settings.pixal_resolution = args.detail
        else:
            settings.hunyuan_octree = args.detail
    settings.texture_size = args.texture_size
    settings.face_limit = args.faces
    settings.hunyuan_texture = not args.untextured
    print(f"Live test: {settings.mode} with {settings.mesh_model}, expected price ${settings.price():.4f}", flush=True)

    result = bpy.ops.wiro_ai.generate()
    last = runtime.last_result()
    summary = {
        "operator": sorted(result),
        "phase": last.get("phase"),
        "error": last.get("error"),
        "objects": last.get("objects"),
        "stats": last.get("stats"),
        "cost": last.get("cost"),
        "seconds": round(last.get("seconds") or 0, 1),
        "task_ids": last.get("task_ids"),
        "glb_path": last.get("glb_path"),
        "glb_bytes": os.path.getsize(last["glb_path"]) if last.get("glb_path") else 0,
        "reference_path": last.get("reference_path"),
        "blender": bpy.app.version_string,
    }
    print("LIVE RESULT " + json.dumps(summary, indent=2), flush=True)
    if args.report:
        with open(args.report, "w") as handle:
            json.dump(summary, handle, indent=2)
    assert result == {"FINISHED"} and last.get("phase") == "done", summary
    assert last["stats"]["faces"] > 0, "the imported model has no faces"
    if args.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save_blend))
        print(f"Saved {args.save_blend}", flush=True)


main()
