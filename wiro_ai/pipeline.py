# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""One generation job: an optional reference image, then the 3D model, then
the GLB download.

A job runs on a worker thread and never touches bpy. The add-on reads
snapshot() from a timer on Blender's main thread and imports the GLB there.
This module does not import bpy.
"""

from __future__ import annotations

import copy
import math
import os
import re
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from . import api, catalog

TEXT = "TEXT"
IMAGE = "IMAGE"

STATUS_TEXT = {
    "task_queue": "Waiting in the queue",
    "task_accept": "Preparing the inputs",
    "task_preprocess_start": "Preparing the inputs",
    "task_preprocess_end": "Preparing the inputs",
    "task_assign": "Loading the model on a GPU",
    "task_start": "Generating",
    "task_output": "Generating",
    "task_output_full": "Generating",
    "task_error": "Generating",
    "task_error_full": "Generating",
    "task_end": "Saving the result",
    "task_postprocess_start": "Saving the result",
    "task_postprocess_end": "Finished",
    "task_cancel": "Cancelled",
}

_RUNNING = ("task_start", "task_output", "task_output_full", "task_error", "task_error_full")
_SAVING = ("task_end", "task_postprocess_start", "task_postprocess_end")

# Model log lines worth showing as the current step, such as "Exporting GLB...".
_STEP_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9 ,'()+:-]{2,60}\.{0,3}$")

# Share of the progress bar for the reference image in Text to 3D.
REFERENCE_SHARE = 0.15


@dataclass
class JobRequest:
    mode: str
    mesh_model: catalog.MeshModel
    mesh_settings: catalog.MeshSettings
    output_dir: str
    prompt: str = ""
    image_path: str = ""
    image_model: catalog.ImageModel | None = None
    framing: bool = True


@dataclass
class JobState:
    phase: str = "starting"  # starting, image, mesh, download, done, failed, cancelled
    title: str = "Starting"
    status: str = ""
    step: str = ""
    progress: float = 0.0
    elapsed: float = 0.0
    finished: bool = False
    error: str = ""
    cost: float = 0.0
    task_ids: list = field(default_factory=list)
    reference_path: str = ""
    reference_url: str = ""
    glb_path: str = ""
    glb_bytes: int = 0
    image_model: str = ""
    mesh_model: str = ""

    @property
    def ok(self) -> bool:
        return self.finished and self.phase == "done"


def log_step(task: dict) -> str:
    """The latest step the model announced in its log, such as "Exporting GLB".

    Only short announcement lines count ("Generating 3D mesh..."): progress
    bars, numbers, paths and warnings are skipped.
    """
    text = str(task.get("debugoutput") or "")
    for line in reversed(text[-4000:].splitlines()[-20:]):
        line = line.strip()
        if not line or any(mark in line for mark in ("%", "it/s", "/", "\\", "=", "[", "{")):
            continue
        if line.lower().startswith(("warn", "error", "traceback", "task completed", "please", "the default")):
            continue
        announced = line.endswith("...")
        short_plain = len(line) <= 32 and not any(ch.isdigit() for ch in line)
        if _STEP_LINE.match(line) and (announced or short_plain):
            return line.rstrip(".").rstrip(":")
    return ""


_PREPARING = ("task_accept", "task_preprocess_start", "task_preprocess_end")


def _ease(seconds: float, scale: float) -> float:
    """0 at the start, approaching 1 without reaching it."""
    return 1.0 - math.exp(-max(0.0, seconds) / max(1.0, scale))


def status_group(status: str) -> str:
    if status == "task_queue":
        return "queue"
    if status in _PREPARING:
        return "prepare"
    if status == "task_assign":
        return "load"
    if status in _RUNNING:
        return "run"
    if status in _SAVING:
        return "save"
    return "other"


def task_progress(status: str, seconds_in_stage: float, expected: float) -> float:
    """0..1 progress of one task, from its status and how long it has been in that stage.

    Every stage creeps forward with time, so the bar keeps moving while a model
    waits in the queue or loads onto a GPU.
    """
    group = status_group(status)
    if group == "queue":
        return 0.01 + 0.03 * _ease(seconds_in_stage, 30.0)
    if group == "prepare":
        return 0.05
    if group == "load":
        return 0.06 + 0.04 * _ease(seconds_in_stage, 60.0)
    if group == "run":
        # Approaches 0.95: the file arrives at the end.
        return 0.1 + 0.85 * _ease(seconds_in_stage, max(5.0, expected))
    if group == "save":
        return 0.97
    return 0.0


class Job:
    def __init__(
        self,
        client: api.Client,
        request: JobRequest,
        *,
        poll_interval: float = 3.0,
        timeout: float = 1800.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.request = request
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._start = clock()
        uses_image_model = request.mode == TEXT and request.image_model is not None
        self._state = JobState(
            image_model=request.image_model.label if uses_image_model else "",
            mesh_model=request.mesh_model.label,
        )

    # -- control ----------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="wiro-ai-job", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        self._update(status="Cancelling")

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def snapshot(self) -> JobState:
        with self._lock:
            state = copy.deepcopy(self._state)
        if not state.finished:
            state.elapsed = self._clock() - self._start
        return state

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # -- work ---------------------------------------------------------------

    def run(self) -> None:
        """Do the whole job on the calling thread."""
        try:
            if self.request.mode == TEXT:
                image_field = ("inputImage", self._reference_image())
                share = REFERENCE_SHARE
            else:
                image_field = ("inputImage", api.image_upload(self.request.image_path))
                share = 0.0
            self._mesh(image_field, share)
            self._update(phase="done", title="Done", status="", step="", progress=1.0)
        except api.WiroCancelled:
            self._update(phase="cancelled", title="Cancelled", status="", step="")
        except api.WiroError as exc:
            self._update(phase="failed", title="Failed", status="", step="", error=str(exc))
        except Exception as exc:  # keep the worker thread from dying silently
            traceback.print_exc()
            self._update(phase="failed", title="Failed", status="", step="", error=f"Unexpected error: {exc}")
        finally:
            with self._lock:
                self._state.elapsed = self._clock() - self._start
                self._state.finished = True

    def _reference_image(self) -> str:
        request = self.request
        model = request.image_model or catalog.IMAGE_MODELS[0]
        prompt = catalog.reference_prompt(request.prompt, model, request.framing)
        if not request.prompt.strip():
            raise api.WiroError("Write a prompt first.")
        self._update(phase="image", title=f"Reference image ({model.label})", status="Sending", progress=0.01)
        fields = list(catalog.image_params(model, prompt).items())
        task = self._run_task(model.slug, fields, model.expected_seconds, 0.0, REFERENCE_SHARE)
        output = api.pick_image(task)
        url = str(output["url"])
        extension = os.path.splitext(str(output.get("name") or url))[1].lower()
        if extension not in api.IMAGE_EXTENSIONS:
            extension = ".png"
        path = os.path.join(request.output_dir, f"reference-{task.get('id', 'image')}{extension}")
        self.client.download(url, path, should_stop=self._cancel.is_set)
        self._update(reference_path=path, reference_url=url)
        return url

    def _mesh(self, image_field: tuple, share: float) -> None:
        request = self.request
        model = request.mesh_model
        settings = request.mesh_settings
        fields = [image_field, *catalog.mesh_params(model, settings).items()]
        status = "Uploading the image" if request.mode == IMAGE else "Sending"
        self._update(phase="mesh", title=f"3D model ({model.label})", status=status, step="")
        self._raise_progress(share + 0.01)
        expected = catalog.mesh_seconds(model, settings)
        task = self._run_task(model.slug, fields, expected, share, 0.95)
        output = api.pick_glb(task)
        name = re.sub(r"[^a-z0-9-]+", "-", model.label.lower()).strip("-")
        path = os.path.join(request.output_dir, f"{name}-{task.get('id', 'model')}.glb")
        self._update(phase="download", title="Downloading the GLB", status="", step="")
        self._raise_progress(0.96)
        size = self.client.download(str(output["url"]), path, should_stop=self._cancel.is_set)
        self._update(glb_path=path, glb_bytes=size)
        self._raise_progress(0.99)

    def _run_task(self, slug: str, fields: list, expected: float, start: float, end: float) -> dict:
        if self._cancel.is_set():
            raise api.WiroCancelled("Stopped.")
        run = self.client.run(slug, fields)
        with self._lock:
            self._state.task_ids.append(str(run.get("taskid") or ""))
        stage_started: dict = {}

        def on_update(task: dict, waited: float) -> None:
            status = str(task.get("status") or "")
            in_stage = waited - stage_started.setdefault(status_group(status), waited)
            if not self._cancel.is_set():
                self._update(
                    status=STATUS_TEXT.get(status, "Working"),
                    step=log_step(task) if status in _RUNNING else "",
                )
            self._raise_progress(start + (end - start) * task_progress(status, in_stage, expected))

        task = api.wait_for_task(
            self.client,
            run,
            timeout=self.timeout,
            poll_interval=self.poll_interval,
            on_update=on_update,
            should_stop=self._cancel.is_set,
            sleep=self._sleep,
            clock=self._clock,
        )
        cost = api.task_cost(task)
        if cost is not None:
            with self._lock:
                self._state.cost += cost
        if self._cancel.is_set():
            raise api.WiroCancelled("Stopped.")
        api.ensure_success(task)
        return task

    def _update(self, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self._state, key, value)

    def _raise_progress(self, value: float) -> None:
        with self._lock:
            self._state.progress = max(self._state.progress, min(1.0, value))
