# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live test: real runs through the add-on's operators, in background mode.
It calls the Wiro AI API and is billed to the key's wallet, so it is not part
of CI.

    WIRO_API_KEY=... blender --background --factory-startup --online-mode \
        --python-exit-code 1 --python tests/blender/live_test.py -- \
        3d --prompt "a wooden treasure chest" --mesh microsoft/trellis-2 --set pipeline_type=512

    ... -- texture --prompt "mossy cobblestone" --object Cube
    ... -- texture --edit --prompt "paint the wood dark red" --object "Wiro Trellis-2"
    ... -- run --model openai/gpt-6-luna --set prompt="Name three woods"

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
    parser.add_argument("what", choices=("3d", "texture", "run"))
    parser.add_argument("--mode", choices=("text", "image"), default="text")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--image", action="append", default=[], help="A picture for Image to 3D; repeat for more")
    parser.add_argument("--mesh", default="")
    parser.add_argument("--reference", default="")
    parser.add_argument("--cutout", action="store_true", help="Remove the background first")
    parser.add_argument("--edit", action="store_true", help="Texture: edit the object's current texture")
    parser.add_argument("--object", default="", help="Texture: the object to texture")
    parser.add_argument("--model", default="", help="Run: the model slug")
    parser.add_argument("--set", action="append", default=[], help="key=value for the model's form; repeat")
    parser.add_argument("--file", action="append", default=[], help="key=path for a file setting; repeat")
    parser.add_argument("--keep-scene", action="store_true", help="Do not clear the scene first")
    parser.add_argument("--save-blend", default="")
    parser.add_argument("--report", default="", help="Write the result as JSON to this file")
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def apply_values(form, pairs):
    for pair in pairs:
        key, _, value = pair.partition("=")
        item = next((p for p in form.params if p.pid == key), None)
        if item is None:
            raise SystemExit(f"{form.slug} has no setting {key!r}; it has {[p.pid for p in form.params]}")
        if item.kind == "int":
            item.value_int = int(value)
        elif item.kind == "float":
            item.value_float = float(value)
        elif item.kind == "bool":
            item.value_bool = value.lower() in ("1", "true", "yes", "on")
        else:
            item.value_text = value


def main():
    args = parse_args()
    if not bpy.app.online_access:
        raise SystemExit("Start Blender with --online-mode for the live test.")
    bpy.ops.preferences.addon_enable(module=MODULE)
    runtime = importlib.import_module(f"{MODULE}.runtime")
    forms = importlib.import_module(f"{MODULE}.forms")
    if not args.keep_scene and args.what == "3d":
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    settings = bpy.context.scene.wiro_ai
    settings.ensure_forms()

    if args.what == "3d":
        settings.mode = args.mode.upper()
        settings.prompt = args.prompt
        if args.mesh:
            settings.mesh_model = args.mesh
        if args.reference:
            settings.reference_model = args.reference
        settings.use_cutout = args.cutout
        settings.images.clear()
        for path in args.image:
            settings.images.add().path = os.path.abspath(path)
        apply_values(settings.mesh_form, args.set)
        result = bpy.ops.wiro_ai.generate()
    elif args.what == "texture":
        target = bpy.data.objects.get(args.object)
        if target is None:
            raise SystemExit(f"No object named {args.object!r}")
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj == target)
        bpy.context.view_layer.objects.active = target
        settings.texture_mode = "EDIT" if args.edit else "NEW"
        settings.texture_prompt = args.prompt
        apply_values(settings.texture_edit_form if args.edit else settings.texture_form, args.set)
        result = bpy.ops.wiro_ai.generate_texture()
    else:
        settings.run_form.slug = args.model
        if not forms.ensure(settings.run_form, "run"):
            raise SystemExit(runtime.schema_status(args.model) or f"Could not load {args.model}")
        apply_values(settings.run_form, args.set)
        for pair in args.file:
            key, _, path = pair.partition("=")
            item = next(p for p in settings.run_form.params if p.pid == key)
            item.files.add().path = os.path.abspath(path)
        result = bpy.ops.wiro_ai.run_model()

    entry = runtime.history.entries[0] if runtime.history.entries else {}
    summary = {k: entry.get(k) for k in ("kind", "status", "error", "models", "cost", "seconds", "task_ids", "made",
                                         "stats", "objects", "model_path", "reference", "texture")}  # fmt: skip
    summary["files"] = [(f["step"], f["kind"], os.path.basename(f["path"])) for f in entry.get("files") or []]
    summary["operator"] = sorted(result)
    summary["blender"] = bpy.app.version_string
    texts = [t.name for t in bpy.data.texts if t.name.startswith("Wiro")]
    if texts:
        summary["answer"] = bpy.data.texts[texts[-1]].as_string()[:400]
    print("LIVE RESULT " + json.dumps(summary, indent=2), flush=True)
    if args.report:
        with open(args.report, "w") as handle:
            json.dump(summary, handle, indent=2)
    assert result == {"FINISHED"} and entry.get("status") == "done", summary
    if args.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save_blend))
        print(f"Saved {args.save_blend}", flush=True)


main()
