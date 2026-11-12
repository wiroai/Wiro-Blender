# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs one job at a time and brings its result into the scene.

The job works on a background thread. A timer on Blender's main thread
watches it, redraws the panel and imports the GLB when it is ready. In
background mode (blender -b) the job runs on the calling thread instead, so
scripts can call bpy.ops.wiro_ai.generate() and get the model back.
"""

import os
import time

import bpy
import bpy.utils.previews

from . import importer, pipeline

POLL_SECONDS = 0.5

_job = None
_job_meta: dict = {}
_last: dict = {}
_previews = None


def current():
    return _job


def running() -> bool:
    return _job is not None


def last_result() -> dict:
    return _last


def start(job: pipeline.Job, meta: dict) -> dict | None:
    """Start a job. In background mode, run it to the end and return the result."""
    global _job, _job_meta
    _job, _job_meta = job, dict(meta)
    if bpy.app.background:
        _run_in_foreground(job)
        return _finish()
    job.start()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=POLL_SECONDS, persistent=True)
    return None


def cancel() -> None:
    if _job is not None:
        _job.cancel()


def _run_in_foreground(job: pipeline.Job) -> None:
    """Background mode: wait for the job and print its progress to the console."""
    job.start()
    printed = ""
    try:
        while True:
            job.join(1.0)
            state = job.snapshot()
            line = f"Wiro AI: {state.title}"
            if state.status:
                line += f": {state.status}"
            if state.step:
                line += f" ({state.step})"
            if line != printed:
                print(f"{line}  [{int(state.progress * 100)}%, {state.elapsed:.0f} s]", flush=True)
                printed = line
            if state.finished:
                return
    except KeyboardInterrupt:
        job.cancel()
        job.join(30.0)
        raise


def _tick():
    if _job is None:
        return None
    state = _job.snapshot()
    _redraw()
    if not state.finished:
        return POLL_SECONDS
    _finish()
    _redraw()
    return None


def _finish() -> dict:
    """Import the finished job's GLB and record the result for the panel."""
    global _job, _job_meta
    job, meta = _job, _job_meta
    _job, _job_meta = None, {}
    if job is None:
        return {}
    state = job.snapshot()
    result = {
        "phase": state.phase,
        "error": state.error,
        "cost": state.cost,
        "seconds": state.elapsed,
        "task_ids": list(state.task_ids),
        "reference_path": state.reference_path,
        "glb_path": state.glb_path,
        "mesh_model": state.mesh_model,
        "image_model": state.image_model,
        "objects": [],
        "finished_at": time.time(),
        "meta": meta,
    }
    if state.ok:
        try:
            roots = importer.import_glb(
                state.glb_path,
                name=meta.get("object_name") or f"Wiro {state.mesh_model}",
                place_at_cursor=meta.get("place_at_cursor", True),
                metadata={
                    "wiro_model": meta.get("mesh_slug", ""),
                    "wiro_task_id": state.task_ids[-1] if state.task_ids else "",
                    "wiro_prompt": (meta.get("prompt") or "")[:1000],
                    "wiro_reference_image": state.reference_url,
                },
            )
            result["objects"] = [obj.name for obj in roots]
            result["stats"] = importer.mesh_stats(roots)
        except Exception as exc:
            result["phase"] = "failed"
            result["error"] = f"The GLB was saved to {state.glb_path}, but importing it failed: {exc}"
    _last.clear()
    _last.update(result)
    return result


def reimport_last(context) -> list:
    path = _last.get("glb_path") or ""
    meta = _last.get("meta") or {}
    roots = importer.import_glb(
        path,
        name=meta.get("object_name") or f"Wiro {_last.get('mesh_model', '')}".strip(),
        place_at_cursor=meta.get("place_at_cursor", True),
    )
    return roots


def _redraw() -> None:
    wm = bpy.context.window_manager
    for window in getattr(wm, "windows", []):
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "UI":
                        region.tag_redraw()


def preview_icon(path: str) -> int:
    """Icon id of an image file, for template_icon. 0 when it cannot be shown."""
    global _previews
    if bpy.app.background or not path or not os.path.isfile(path):
        return 0
    if _previews is None:
        _previews = bpy.utils.previews.new()
    key = f"{path}|{os.path.getmtime(path)}"
    if key not in _previews:
        try:
            _previews.load(key, path, "IMAGE")
        except Exception:
            return 0
    return _previews[key].icon_id


def register() -> None:
    pass


def unregister() -> None:
    global _previews, _job
    if _job is not None:
        _job.cancel()
        _job = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
