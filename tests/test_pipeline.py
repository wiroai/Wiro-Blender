# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from tests.fakes import FakeClock, FakeTransport, bundled_catalog, form_parts
from wiro_ai import api, pipeline

CATALOG = bundled_catalog()
TRELLIS = CATALOG.schema("microsoft/trellis-2")
WORLD = CATALOG.schema("tencent/hy-world-2-0-world-reconstruction")
NANO = CATALOG.schema("google/nano-banana-2-1")
CUTOUT = CATALOG.schema("wiro/remove-background")


def make_job(tmp_path, transport, steps):
    client = api.Client(api.Credentials("key-123"), user_agent="Wiro-Blender/test", transport=transport)
    clock = FakeClock()
    return pipeline.Job(client, steps, str(tmp_path), poll_interval=3, sleep=clock.sleep, clock=clock)


def test_text_to_3d_with_background_removal(tmp_path):
    transport = FakeTransport(costs={"google/nano-banana-2-1": "0.0336", "wiro/remove-background": "0.0012"})
    steps = pipeline.three_d_steps(
        mode=pipeline.TEXT,
        mesh=TRELLIS,
        mesh_values={"pipeline_type": "512"},
        prompt="a brass lantern",
        reference=NANO,
        reference_values={"resolution": "1K", "aspectRatio": "1:1"},
        cutout=CUTOUT,
    )
    assert [s.key for s in steps] == ["reference", "cutout", "mesh"]
    job = make_job(tmp_path, transport, steps)
    job.run()
    state = job.snapshot()
    assert state.ok, state.error
    assert state.cost == pytest.approx(0.3348)
    runs = transport.runs()
    assert [r["url"].split("/Run/")[1] for r in runs] == [
        "google/nano-banana-2-1",
        "wiro/remove-background",
        "microsoft/trellis-2",
    ]
    reference = dict((name, value) for name, _f, value in form_parts(runs[0]))
    assert reference["prompt"].startswith(b"a brass lantern. Show one whole object")
    assert reference["aspectRatio"] == b"1:1"
    assert ("inputImage", None, b"https://cdn1.wiro.ai/100/0.png") in form_parts(runs[1])
    mesh = form_parts(runs[2])
    assert ("inputImage", None, b"https://cdn1.wiro.ai/101/cutout.png") in mesh
    assert ("pipeline_type", None, b"512") in mesh
    kinds = [(o.step, o.kind) for o in state.outputs]
    assert kinds == [("reference", "image"), ("cutout", "image"), ("mesh", "3d"), ("mesh", "3d")]
    assert state.preview_path.endswith("cutout-101-cutout.png")
    assert (tmp_path / "mesh-102-0.glb").read_bytes() == b"file:https://cdn1.wiro.ai/102/0.glb"


def test_image_to_3d_sends_every_picture_to_a_multi_view_model(tmp_path):
    uploads = [(f"view{i}.png", f"PNG{i}".encode(), "image/png") for i in range(3)]
    steps = pipeline.three_d_steps(mode=pipeline.IMAGE, mesh=WORLD, mesh_values={"targetSize": 952}, images=uploads)
    transport = FakeTransport()
    job = make_job(tmp_path, transport, steps)
    job.run()
    assert job.snapshot().ok
    parts = form_parts(transport.runs()[0])
    assert [(name, filename) for name, filename, _v in parts if name == "inputImage"] == [
        ("inputImage", "view0.png"),
        ("inputImage", "view1.png"),
        ("inputImage", "view2.png"),
    ]
    assert {o.name for o in job.snapshot().outputs} == {"0.ply", "0.mp4"}


def test_single_picture_model_gets_one_picture_after_the_cutout(tmp_path):
    uploads = [("a.png", b"A", "image/png"), ("b.png", b"B", "image/png")]
    steps = pipeline.three_d_steps(mode=pipeline.IMAGE, mesh=TRELLIS, mesh_values={}, images=uploads, cutout=CUTOUT)
    transport = FakeTransport()
    job = make_job(tmp_path, transport, steps)
    job.run()
    cut, mesh = transport.runs()
    assert ("inputImage", "a.png", b"A") in form_parts(cut)
    images = [value for name, _f, value in form_parts(mesh) if name == "inputImage"]
    assert images == [b"https://cdn1.wiro.ai/100/cutout.png"]


def test_texture_steps(tmp_path):
    transport = FakeTransport()
    job = make_job(tmp_path, transport, pipeline.texture_steps(model=NANO, values={"resolution": "1K"}, prompt="moss"))
    job.run()
    assert job.snapshot().ok and job.snapshot().of_kind("image")
    fields = dict((name, value) for name, _f, value in form_parts(transport.runs()[0]))
    assert fields["prompt"].startswith(b"moss. A seamless, tileable texture")
    source = ("tex.png", b"TEX", "image/png")
    transport = FakeTransport()
    steps = pipeline.texture_steps(model=NANO, values={}, prompt="make it rusty", source=source)
    make_job(tmp_path, transport, steps).run()
    parts = form_parts(transport.runs()[0])
    assert ("inputImage", "tex.png", b"TEX") in parts and ("prompt", None, b"make it rusty") in parts


def test_run_steps_reads_an_llm_answer(tmp_path):
    transport = FakeTransport(outputs=lambda slug, task_id: [], debugoutput="Paris is the capital of France.")
    job = make_job(tmp_path, transport, pipeline.run_steps(model=NANO, values={"prompt": "q"}, files={}))
    job.run()
    state = job.snapshot()
    assert state.ok and state.text == "Paris is the capital of France." and state.outputs == []


def test_errors_before_any_run():
    with pytest.raises(api.WiroError, match="Write a prompt"):
        pipeline.three_d_steps(mode=pipeline.TEXT, mesh=TRELLIS, mesh_values={}, reference=NANO, prompt=" ")
    with pytest.raises(api.WiroError, match="Add a picture"):
        pipeline.three_d_steps(mode=pipeline.IMAGE, mesh=TRELLIS, mesh_values={}, images=[])
    with pytest.raises(api.WiroError, match="takes no picture"):
        pipeline.three_d_steps(mode=pipeline.IMAGE, mesh=NANO.__class__(slug="a/b", title="B"), mesh_values={})


def test_failed_step_stops_the_job(tmp_path):
    transport = FakeTransport(pexit="1", debugerror="Traceback\nValueError: no object found")
    steps = pipeline.three_d_steps(mode=pipeline.IMAGE, mesh=TRELLIS, mesh_values={}, images=["https://x/a.png"])
    job = make_job(tmp_path, transport, steps)
    job.run()
    state = job.snapshot()
    assert state.phase == "failed" and "no object found" in state.error and state.outputs == []


def test_cancel_while_running(tmp_path):
    transport = FakeTransport(statuses=["task_start"])
    steps = pipeline.three_d_steps(mode=pipeline.IMAGE, mesh=TRELLIS, mesh_values={}, images=["https://x/a.png"])
    job = make_job(tmp_path, transport, steps)
    original = job.client.task_detail

    def detail_then_cancel(token):
        task = original(token)
        job.cancel()
        return task

    job.client.task_detail = detail_then_cancel
    job.run()
    assert job.snapshot().phase == "cancelled"
    assert transport.paths()[-1] == "Task/Kill"


def test_progress_never_goes_back(tmp_path):
    statuses = [
        "task_queue",
        "task_accept",
        "task_assign",
        "task_start",
        "task_output",
        "task_end",
        "task_postprocess_end",
    ]
    steps = pipeline.three_d_steps(
        mode=pipeline.TEXT, mesh=TRELLIS, mesh_values={}, prompt="a cup", reference=NANO, reference_values={}
    )
    job = make_job(tmp_path, FakeTransport(statuses=statuses), steps)
    seen = []
    original = job._raise_progress

    def record(value):
        original(value)
        seen.append(job.snapshot().progress)

    job._raise_progress = record
    job.run()
    assert seen == sorted(seen) and job.snapshot().progress == 1.0


def test_log_step():
    log = "Loading image...\nGenerating 3D mesh...\n 40%|###  | 4/10 [00:01<00:02, 2.5it/s]"
    assert pipeline.log_step({"debugoutput": log}) == "Generating 3D mesh"
    log = "Running Dual Contouring...\nAfter remeshing: 11741331 vertices, 23505100 faces"
    assert pipeline.log_step({"debugoutput": log}) == "Running Dual Contouring"
    assert pipeline.log_step({"debugoutput": "{'seed': 3}"}) == ""


def test_waiting_stages_creep_but_stay_in_order():
    for status, ceiling in (("task_queue", 0.05), ("task_assign", 0.10)):
        early, late = pipeline.task_progress(status, 0, 50), pipeline.task_progress(status, 600, 50)
        assert early < late <= ceiling
    values = [pipeline.task_progress("task_start", t, 50) for t in range(0, 600, 10)]
    assert values == sorted(values) and values[-1] < 0.95
