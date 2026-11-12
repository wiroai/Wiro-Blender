# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""The job queue, the model catalog and the history, while Blender runs.

Jobs work on background threads, a few at a time. A timer on Blender's main
thread starts waiting jobs, redraws the panels and brings finished results
into the scene. In background mode (blender -b) a job runs to the end when it
is submitted, so scripts get their result back from the operator.
"""

import contextlib
import os
import threading
import time
import uuid

import bpy
import bpy.utils.previews

from . import api, importer, materials, pipeline, version
from . import catalog as catalog_module
from . import history as history_module

PACKAGE = __package__
POLL_SECONDS = 0.5
CATALOG_CATEGORIES = ("3d-generation", "text-to-image", "image-editing", "background-removal")

catalog = catalog_module.Catalog()
history = None

_queue: list = []
_schemas_pending: set = set()
_schema_errors: dict = {}
_status = {"catalog": "", "balance": None, "balance_error": "", "refreshing": False, "refreshed": False}
_previews = None
_lock = threading.Lock()


class QueueItem:
    def __init__(self, kind: str, title: str, job: pipeline.Job, meta: dict) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.title = title
        self.job = job
        self.meta = meta
        self.state = "waiting"
        self.created = time.time()
        self.result: dict = {}


# -- paths and clients --------------------------------------------------------------


def user_dir(*parts: str) -> str:
    folder = bpy.utils.extension_path_user(PACKAGE, path=os.path.join(*parts) if parts else "", create=True)
    os.makedirs(folder, exist_ok=True)
    return folder


def public_client() -> api.Client:
    """A client for the public catalog. It sends no key."""
    return api.Client(None, user_agent=version.user_agent(bpy.app.version_string))


def online() -> bool:
    return bool(getattr(bpy.app, "online_access", True))


def init() -> None:
    global catalog, history
    bundled = os.path.join(os.path.dirname(__file__), "data", "catalog.json")
    catalog = catalog_module.Catalog(bundled, os.path.join(user_dir("cache"), "catalog.json"))
    history = history_module.History(os.path.join(user_dir(), "history.json"))


# -- catalog --------------------------------------------------------------------------


def request_schema(slug: str) -> None:
    """Fetch a model's settings in the background, unless they are known or on the way."""
    if not slug or catalog.has_schema(slug) or slug in _schemas_pending:
        return
    if not online():
        _schema_errors[slug] = "Turn on Allow Online Access to load this model's settings."
        return
    if bpy.app.background:
        try:
            catalog.fetch_schema(public_client(), slug)
        except (api.WiroError, ValueError) as exc:
            _schema_errors[slug] = str(exc)
        return
    _schemas_pending.add(slug)
    _schema_errors.pop(slug, None)

    def work():
        try:
            catalog.fetch_schema(public_client(), slug)
        except (api.WiroError, ValueError) as exc:
            _schema_errors[slug] = str(exc)
        finally:
            _schemas_pending.discard(slug)

    threading.Thread(target=work, name="wiro-ai-schema", daemon=True).start()
    _ensure_timer()


def schema_status(slug: str) -> str:
    if slug in _schemas_pending:
        return "Loading this model's settings..."
    return _schema_errors.get(slug, "")


def refresh_catalog(*, force: bool = False) -> None:
    """Refresh the model lists from Wiro AI in the background (public, no key)."""
    if _status["refreshing"] or not online():
        return
    if not force and _status["refreshed"]:
        return
    categories = [c for c in CATALOG_CATEGORIES if force or catalog.stale(c)]
    _status["refreshed"] = True
    if not categories:
        return
    _status["refreshing"] = True
    _status["catalog"] = "Updating the model lists..."

    def work():
        try:
            catalog.refresh(public_client(), categories)
            _status["catalog"] = ""
        except (api.WiroError, OSError) as exc:
            _status["catalog"] = f"Could not update the model lists: {exc}"
        finally:
            _status["refreshing"] = False

    if bpy.app.background:
        work()
        return
    threading.Thread(target=work, name="wiro-ai-catalog", daemon=True).start()
    _ensure_timer()


def refresh_balance(client: api.Client) -> None:
    """Read the wallet balance in the background. Needs the API key; free."""

    def work():
        try:
            _status["balance"] = client.balance()
            _status["balance_error"] = ""
        except api.WiroError as exc:
            _status["balance_error"] = str(exc)

    if bpy.app.background:
        work()
        return
    threading.Thread(target=work, name="wiro-ai-balance", daemon=True).start()
    _ensure_timer()


def status() -> dict:
    return dict(_status)


# -- queue ------------------------------------------------------------------------------


def jobs() -> list:
    return list(_queue)


def running_count() -> int:
    return sum(1 for item in _queue if item.state == "running")


def submit(kind: str, title: str, job: pipeline.Job, meta: dict) -> QueueItem:
    """Queue a job. In background mode it runs now and is finished on return."""
    item = QueueItem(kind, title, job, meta)
    if bpy.app.background:
        item.state = "running"
        _run_in_foreground(item)
        _finish(item)
        return item
    _queue.append(item)
    _ensure_timer()
    return item


def cancel(item_id: str) -> None:
    for item in _queue:
        if item.id == item_id:
            if item.state == "waiting":
                item.state = "cancelled"
                _queue.remove(item)
            else:
                item.job.cancel()
            break


def _max_parallel() -> int:
    from . import preferences

    prefs = preferences.get()
    return max(1, int(getattr(prefs, "max_parallel", 2) or 2))


def _run_in_foreground(item: QueueItem) -> None:
    """Background mode: wait for the job and print its progress to the console."""
    job = item.job
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


_timer_on = False


def _ensure_timer() -> None:
    global _timer_on
    if bpy.app.background or _timer_on:
        return
    _timer_on = True
    bpy.app.timers.register(_tick, first_interval=POLL_SECONDS, persistent=True)


def _tick():
    global _timer_on
    try:
        free = _max_parallel() - running_count()
        for item in _queue:
            if free <= 0:
                break
            if item.state == "waiting":
                item.state = "running"
                item.job.start()
                free -= 1
        for item in list(_queue):
            if item.state == "running" and item.job.snapshot().finished:
                _queue.remove(item)
                _finish(item)
        _apply_loaded_schemas()
    finally:
        _redraw()
    busy = bool(_queue) or bool(_schemas_pending) or _status["refreshing"]
    if busy:
        return POLL_SECONDS
    _timer_on = False
    return None


def _apply_loaded_schemas() -> None:
    from . import forms

    for scene in bpy.data.scenes:
        settings = getattr(scene, "wiro_ai", None)
        if settings is None:
            continue
        for attr in ("mesh_form", "reference_form", "cutout_form", "texture_form", "texture_edit_form", "run_form"):
            form = getattr(settings, attr)
            if form.slug and form.built_slug != form.slug and catalog.has_schema(form.slug):
                forms.ensure(form, form.purpose or attr.replace("_form", ""))


# -- finishing ----------------------------------------------------------------------------


def _primary_3d(outputs: list):
    order = (".glb", ".gltf", ".fbx", ".obj", ".usdz", ".usdc", ".usd", ".ply", ".stl", ".abc")
    models = [o for o in outputs if o.kind == "3d" and o.path]
    for ext in order:
        textured = [o for o in models if o.path.lower().endswith(ext) and "untextured" not in o.name.lower()]
        if textured:
            return textured[0]
    return models[0] if models else None


def _finish(item: QueueItem) -> None:
    state = item.job.snapshot()
    meta = item.meta
    entry = {
        "kind": item.kind,
        "title": item.title,
        "models": meta.get("models", []),
        "prompt": meta.get("prompt", ""),
        "cost": round(state.cost, 4),
        "seconds": round(state.elapsed, 1),
        "status": state.phase,
        "error": state.error,
        "task_ids": state.task_ids,
        "files": [
            {"path": o.path, "url": o.url, "name": o.name, "kind": o.kind, "step": o.step} for o in state.outputs
        ],  # fmt: skip
        "made": [],
    }
    if state.ok:
        try:
            if item.kind == "3d":
                _finish_3d(item, state, entry)
            elif item.kind == "texture":
                _finish_texture(item, state, entry)
            else:
                entry["made"] = importer.load_outputs(
                    state.outputs,
                    text=state.text,
                    name=meta.get("name", "Wiro"),
                    image_output=meta.get("image_output", "LOAD"),
                    import_options=meta.get("import_options"),
                )
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = f"The files were saved, but bringing them into Blender failed: {exc}"
    item.state = entry["status"]
    item.result = entry
    if history is not None:
        with contextlib.suppress(OSError):
            history.add(entry)
    if state.cost and meta.get("client") is not None and not bpy.app.background:
        refresh_balance(meta["client"])


class _Options:
    """A plain copy of the import options, so a job keeps the ones it started with."""

    def __init__(self, source) -> None:
        for key in ("location", "origin", "size", "scale", "target_size", "use_collection", "cleanup"):
            setattr(self, key, getattr(source, key))


def import_options(settings) -> _Options:
    return _Options(settings.import_options)


def _finish_3d(item: QueueItem, state, entry: dict) -> None:
    output = _primary_3d(state.outputs)
    if output is None:
        raise RuntimeError("The model returned no 3D file.")
    meta = item.meta
    roots = importer.import_3d(
        output.path,
        name=meta.get("name", "Wiro model"),
        options=meta.get("import_options"),
        metadata={
            "wiro_model": meta.get("models", [""])[-1],
            "wiro_task_id": state.task_ids[-1] if state.task_ids else "",
            "wiro_prompt": (meta.get("prompt") or "")[:1000],
        },
    )
    entry["objects"] = [root.name for root in roots]
    entry["stats"] = importer.mesh_stats(roots)
    entry["model_path"] = output.path
    entry["made"] = [f"Object {root.name}" for root in roots]
    reference = next((o for o in state.outputs if o.step in ("cutout", "reference") and o.kind == "image"), None)
    if reference is not None:
        entry["reference"] = reference.path


def _finish_texture(item: QueueItem, state, entry: dict) -> None:
    image = next((o for o in state.outputs if o.kind == "image" and o.path), None)
    if image is None:
        raise RuntimeError("The image model returned no picture.")
    meta = item.meta
    objects = [bpy.data.objects.get(name) for name in meta.get("objects", [])]
    objects = [obj for obj in objects if obj is not None]
    entry["texture"] = image.path
    entry["objects"] = [obj.name for obj in objects]
    entry["texture_options"] = meta.get("texture_options", {})
    if meta.get("edit"):
        if not objects:
            raise RuntimeError("The object to edit is gone.")
        name = materials.replace_base_color(objects[0], image.path)
        entry["made"] = [f"Image {name}"]
    else:
        options = meta.get("texture_options", {})
        made = materials.apply_texture(objects, image.path, name=meta.get("name", "Wiro texture"), **options)
        entry["made"] = [f"Material {name}" for name in made]


# -- previews and redraws --------------------------------------------------------------------


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


def _redraw() -> None:
    wm = bpy.context.window_manager
    for window in getattr(wm, "windows", []):
        for area in window.screen.areas:
            if area.type in ("VIEW_3D", "PREFERENCES"):
                for region in area.regions:
                    if region.type in ("UI", "WINDOW"):
                        region.tag_redraw()


def register() -> None:
    init()


def unregister() -> None:
    global _previews, _timer_on
    for item in list(_queue):
        item.job.cancel()
    _queue.clear()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _timer_on = False
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
