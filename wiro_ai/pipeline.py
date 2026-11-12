# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Jobs: one or more model runs in a row, each one able to use the files the
previous one returned (a reference picture, then its cutout, then the 3D model).

A job runs on a worker thread and never touches bpy. The add-on reads
snapshot() from a timer on Blender's main thread and brings the results into
the scene there. This module does not import bpy.
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

from . import api, catalog, schema

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
_PREPARING = ("task_accept", "task_preprocess_start", "task_preprocess_end")
_STEP_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9 ,'()+:-]{2,60}\.{0,3}$")


@dataclass
class Output:
    url: str
    name: str
    kind: str
    path: str = ""
    size: int = 0
    step: str = ""


@dataclass
class Step:
    """One model run in a job."""

    key: str
    title: str
    model: str
    fields: Callable[[dict], list]  # previous results by key -> multipart fields
    expected_seconds: float = 30.0
    download: tuple = ("all",)  # output kinds to save, or "all"
    weight: float = 1.0


@dataclass
class StepResult:
    task: dict
    outputs: list = field(default_factory=list)
    text: str = ""

    def first(self, kind: str) -> Output | None:
        return next((o for o in self.outputs if o.kind == kind), None)


@dataclass
class JobState:
    phase: str = "starting"  # starting, running, download, done, failed, cancelled
    title: str = "Starting"
    status: str = ""
    step: str = ""
    progress: float = 0.0
    elapsed: float = 0.0
    finished: bool = False
    error: str = ""
    cost: float = 0.0
    task_ids: list = field(default_factory=list)
    outputs: list = field(default_factory=list)  # every downloaded Output
    preview_path: str = ""  # the newest picture, for the panel
    text: str = ""  # an LLM answer
    results: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.finished and self.phase == "done"

    def of_kind(self, kind: str) -> list:
        return [o for o in self.outputs if o.kind == kind]


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


def _ease(seconds: float, scale: float) -> float:
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
        return 0.1 + 0.85 * _ease(seconds_in_stage, max(5.0, expected))
    if group == "save":
        return 0.97
    return 0.0


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "file"


class Job:
    def __init__(
        self,
        client: api.Client,
        steps: list,
        output_dir: str,
        *,
        poll_interval: float = 3.0,
        timeout: float = 1800.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.steps = list(steps)
        self.output_dir = output_dir
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._start = clock()
        total = sum(max(0.01, step.weight) for step in self.steps) or 1.0
        self._shares = []
        position = 0.0
        for step in self.steps:
            width = 0.95 * max(0.01, step.weight) / total
            self._shares.append((position, position + width))
            position += width
        self._state = JobState()

    # -- control ----------------------------------------------------------

    def start(self) -> None:
        self._start = self._clock()
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
        if not state.finished and state.phase != "starting":
            state.elapsed = self._clock() - self._start
        return state

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # -- work ---------------------------------------------------------------

    def run(self) -> None:
        """Run every step on the calling thread."""
        self._start = self._clock()
        self._update(phase="running")
        try:
            results: dict = {}
            for index, step in enumerate(self.steps):
                results[step.key] = self._run_step(index, step, results)
                with self._lock:
                    self._state.results = dict(results)
            self._update(phase="done", title="Done", status="", step="", progress=1.0)
        except api.WiroCancelled:
            self._update(phase="cancelled", title="Cancelled", status="", step="")
        except (api.WiroError, OSError, ValueError) as exc:
            self._update(phase="failed", title="Failed", status="", step="", error=str(exc))
        except Exception as exc:  # keep the worker thread from dying silently
            traceback.print_exc()
            self._update(phase="failed", title="Failed", status="", step="", error=f"Unexpected error: {exc}")
        finally:
            with self._lock:
                self._state.elapsed = self._clock() - self._start
                self._state.finished = True

    def _run_step(self, index: int, step: Step, results: dict) -> StepResult:
        start, end = self._shares[index]
        if self._cancel.is_set():
            raise api.WiroCancelled("Stopped.")
        self._update(title=step.title, status="Sending", step="")
        self._raise_progress(start + 0.005)
        fields = step.fields(results)
        task = self._run_task(step, fields, start, end)
        outputs = []
        wanted = step.download
        for url, name, kind in api.task_outputs(task):
            outputs.append(Output(url=url, name=name, kind=kind, step=step.key))
        to_save = [o for o in outputs if "all" in wanted or o.kind in wanted]
        if to_save:
            self._update(status="Downloading")
        for output in to_save:
            path = os.path.join(self.output_dir, f"{step.key}-{task.get('id', 'task')}-{_safe(output.name)}")
            output.path = path
            output.size = self.client.download(output.url, path, should_stop=self._cancel.is_set)
            with self._lock:
                self._state.outputs.append(copy.copy(output))
                if output.kind == "image":
                    self._state.preview_path = path
        text = api.llm_text(task)
        if text:
            with self._lock:
                self._state.text = text
        self._raise_progress(end)
        return StepResult(task=task, outputs=outputs, text=text)

    def _run_task(self, step: Step, fields: list, start: float, end: float) -> dict:
        run = self.client.run(step.model, fields)
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
            self._raise_progress(start + (end - start) * task_progress(status, in_stage, step.expected_seconds))

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


# ---------------------------------------------------------------------------
# Job recipes. Each takes ready schemas and values, so it never waits on the
# catalog; the Blender side makes sure the schemas are loaded first.


def _need(result: StepResult | None, kind: str, what: str) -> Output:
    output = result.first(kind) if result else None
    if output is None:
        raise api.WiroError(f"The {what} step returned no {kind}.")
    return output


def three_d_steps(
    *,
    mode: str,
    mesh: schema.ModelSchema,
    mesh_values: dict,
    mesh_files: dict | None = None,
    images: list | None = None,
    prompt: str = "",
    reference: schema.ModelSchema | None = None,
    reference_values: dict | None = None,
    framing: bool = True,
    cutout: schema.ModelSchema | None = None,
    mesh_seconds: float = 60.0,
) -> list:
    """Steps for Text to 3D or Image to 3D.

    images: the pictures for Image to 3D, each a URL or a (filename, bytes,
        mime) upload. A model that takes several pictures gets all of them.
    """
    steps = []
    image_param = mesh.image_param()
    if image_param is None:
        raise api.WiroError(f"{mesh.title} takes no picture, so it cannot run from the 3D panel.")
    if mode == TEXT:
        if reference is None:
            raise api.WiroError("Choose an image model for the reference picture.")
        if not prompt.strip():
            raise api.WiroError("Write a prompt first.")
        prompt_param = reference.prompt_param()
        if prompt_param is None:
            raise api.WiroError(f"{reference.title} takes no prompt.")
        values = dict(reference_values or {})
        transparent = str(values.get("background", "")) == "transparent"
        values[prompt_param.id] = catalog.framed_prompt(prompt, "3d", transparent=transparent) if framing else prompt
        steps.append(
            Step(
                key="reference",
                title=f"Reference picture ({catalog.short_name(reference.slug, reference.title)})",
                model=reference.slug,
                fields=lambda results, schema_=reference, values_=values: schema.build_fields(schema_, values_),
                expected_seconds=20.0,
                download=("image",),
                weight=0.15,
            )
        )
    elif not images:
        raise api.WiroError("Add a picture first.")

    if cutout is not None:
        cut_param = cutout.image_param()
        if cut_param is None:
            raise api.WiroError(f"{cutout.title} takes no picture.")

        def cutout_fields(results, schema_=cutout, pid=cut_param.id):
            reference_result = results.get("reference")
            source = _need(reference_result, "image", "reference picture").url if mode == TEXT else images[0]
            return schema.build_fields(schema_, {}, {pid: [source]})

        steps.append(
            Step(
                key="cutout",
                title=f"Background removal ({catalog.short_name(cutout.slug, cutout.title)})",
                model=cutout.slug,
                fields=cutout_fields,
                expected_seconds=10.0,
                download=("image",),
                weight=0.05,
            )
        )

    def mesh_fields(results):
        if "cutout" in results:
            pictures = [_need(results["cutout"], "image", "background removal").url]
            if mode == IMAGE and images and image_param.max_files > 1:
                pictures += list(images[1:])
        elif mode == TEXT:
            pictures = [_need(results.get("reference"), "image", "reference picture").url]
        else:
            pictures = list(images or [])
        pictures = pictures[: max(1, image_param.max_files)]
        files = dict(mesh_files or {})
        files[image_param.id] = pictures
        return schema.build_fields(mesh, mesh_values, files)

    steps.append(
        Step(
            key="mesh",
            title=f"3D model ({catalog.short_name(mesh.slug, mesh.title)})",
            model=mesh.slug,
            fields=mesh_fields,
            expected_seconds=mesh_seconds,
            download=("3d", "image", "video"),
            weight=0.8,
        )
    )
    return steps


def texture_steps(
    *,
    model: schema.ModelSchema,
    values: dict,
    prompt: str,
    tileable: bool = True,
    source: object | None = None,
) -> list:
    """One image run that makes a texture, from a prompt or by editing a source picture."""
    prompt_param = model.prompt_param()
    if prompt_param is None:
        raise api.WiroError(f"{model.title} takes no prompt.")
    if not prompt.strip():
        raise api.WiroError("Write a prompt first.")
    values = dict(values)
    values[prompt_param.id] = catalog.framed_prompt(prompt, "texture") if tileable and source is None else prompt
    files = {}
    if source is not None:
        image_param = model.image_param()
        if image_param is None:
            raise api.WiroError(f"{model.title} cannot edit a picture.")
        files[image_param.id] = [source]
    title = f"Texture ({catalog.short_name(model.slug, model.title)})"
    return [
        Step(
            key="texture",
            title=title,
            model=model.slug,
            fields=lambda results: schema.build_fields(model, values, files),
            expected_seconds=25.0,
            download=("image",),
        )
    ]


def run_steps(*, model: schema.ModelSchema, values: dict, files: dict, expected_seconds: float = 60.0) -> list:
    """One run of any model, saving every file it returns."""
    return [
        Step(
            key="run",
            title=catalog.short_name(model.slug, model.title),
            model=model.slug,
            fields=lambda results: schema.build_fields(model, values, files),
            expected_seconds=expected_seconds,
            download=("all",),
        )
    ]
