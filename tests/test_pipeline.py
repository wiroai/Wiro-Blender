# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later

import email

import pytest

from tests.fakes import FakeClock, FakeTransport
from wiro_ai import api, catalog, pipeline


def outputs_for(slug, task_id):
    if slug.startswith("google/"):
        return [{"name": "0.png", "contenttype": "image/png", "url": f"https://cdn1.wiro.ai/{task_id}/0.png"}]
    return [
        {
            "name": "0_untextured.glb",
            "contenttype": "model/gltf-binary",
            "url": f"https://cdn1.wiro.ai/{task_id}/0_untextured.glb",
        },
        {"name": "0.glb", "contenttype": "model/gltf-binary", "url": f"https://cdn1.wiro.ai/{task_id}/0.glb"},
    ]


def make_job(tmp_path, transport, **request_changes):
    request = pipeline.JobRequest(
        mode=pipeline.TEXT,
        prompt="a brass lantern",
        image_model=catalog.image_model("NANO_BANANA_2_1"),
        mesh_model=catalog.mesh_model("TRELLIS_2"),
        mesh_settings=catalog.MeshSettings(detail="512"),
        output_dir=str(tmp_path),
    )
    for key, value in request_changes.items():
        setattr(request, key, value)
    client = api.Client(api.Credentials("key-123"), user_agent="Wiro-Blender/test", transport=transport)
    clock = FakeClock()
    return pipeline.Job(client, request, poll_interval=3, sleep=clock.sleep, clock=clock)


def form_parts(request):
    content_type = request["headers"]["Content-Type"].encode()
    message = email.message_from_bytes(b"Content-Type: " + content_type + b"\r\n\r\n" + request["body"])
    return {
        part.get_param("name", header="content-disposition"): (part.get_filename(), part.get_payload(decode=True))
        for part in message.get_payload()
    }


def test_text_to_3d(tmp_path):
    files = {
        "https://cdn1.wiro.ai/100/0.png": b"png-bytes",
        "https://cdn1.wiro.ai/101/0.glb": b"glb-bytes",
    }
    transport = FakeTransport(outputs=outputs_for, files=files)
    job = make_job(tmp_path, transport)
    job.run()
    state = job.snapshot()
    assert state.ok, state.error
    assert state.progress == 1.0
    assert state.task_ids == ["100", "101"]
    assert state.cost == pytest.approx(0.60)
    assert state.reference_url == "https://cdn1.wiro.ai/100/0.png"
    assert (tmp_path / "reference-100.png").read_bytes() == b"png-bytes"
    assert state.glb_path == str(tmp_path / "trellis-2-101.glb")
    assert (tmp_path / "trellis-2-101.glb").read_bytes() == b"glb-bytes"
    assert state.image_model == "Nano Banana 2.1" and state.mesh_model == "Trellis-2"

    runs = [r for r in transport.requests if "/Run/" in r["url"]]
    assert runs[0]["url"].endswith("/Run/google/nano-banana-2-1")
    image_fields = form_parts(runs[0])
    assert image_fields["prompt"][1].startswith(b"a brass lantern. Show one whole object")
    assert image_fields["resolution"][1] == b"1K"
    assert runs[1]["url"].endswith("/Run/microsoft/trellis-2")
    mesh_fields = form_parts(runs[1])
    assert mesh_fields["inputImage"] == (None, b"https://cdn1.wiro.ai/100/0.png")
    assert mesh_fields["pipeline_type"][1] == b"512"


def test_image_to_3d_uploads_the_file(tmp_path):
    picture = tmp_path / "lamp.png"
    picture.write_bytes(b"\x89PNG lamp")
    files = {"https://cdn1.wiro.ai/100/0.glb": b"glb"}
    transport = FakeTransport(outputs=outputs_for, files=files)
    job = make_job(tmp_path, transport, mode=pipeline.IMAGE, image_path=str(picture), image_model=None)
    job.run()
    state = job.snapshot()
    assert state.ok, state.error
    assert state.reference_path == "" and state.image_model == ""
    run = next(r for r in transport.requests if "/Run/" in r["url"])
    assert form_parts(run)["inputImage"] == ("lamp.png", b"\x89PNG lamp")


def test_progress_never_goes_back(tmp_path):
    files = {"https://cdn1.wiro.ai/100/0.png": b"p", "https://cdn1.wiro.ai/101/0.glb": b"g"}
    statuses = [
        "task_queue",
        "task_accept",
        "task_assign",
        "task_start",
        "task_output",
        "task_end",
        "task_postprocess_end",
    ]
    transport = FakeTransport(statuses=statuses, outputs=outputs_for, files=files)
    job = make_job(tmp_path, transport)
    seen = []
    original = job._raise_progress

    def record(value):
        original(value)
        seen.append(job.snapshot().progress)

    job._raise_progress = record
    job.run()
    assert seen == sorted(seen)
    assert job.snapshot().progress == 1.0


def test_failed_task(tmp_path):
    transport = FakeTransport(outputs=outputs_for, pexit="1", debugerror="Traceback\nValueError: no object found")
    job = make_job(tmp_path, transport)
    job.run()
    state = job.snapshot()
    assert state.phase == "failed" and state.finished
    assert "exit code 1" in state.error and "no object found" in state.error
    assert state.glb_path == ""


def test_api_error(tmp_path):
    transport = FakeTransport(fail_paths={"Run/google/nano-banana-2-1"})
    job = make_job(tmp_path, transport)
    job.run()
    state = job.snapshot()
    assert state.phase == "failed" and "failed" in state.error


def test_empty_prompt(tmp_path):
    job = make_job(tmp_path, FakeTransport(), prompt="   ")
    job.run()
    assert job.snapshot().error == "Write a prompt first."


def test_cancel_while_running(tmp_path):
    transport = FakeTransport(statuses=["task_start"], outputs=outputs_for)
    job = make_job(tmp_path, transport)
    original = job.client.task_detail

    def detail_then_cancel(token):
        task = original(token)
        job.cancel()
        return task

    job.client.task_detail = detail_then_cancel
    job.run()
    state = job.snapshot()
    assert state.phase == "cancelled" and state.error == ""
    assert transport.paths()[-1] == "Task/Kill"


def test_log_step():
    log = "Loading image...\nGenerating 3D mesh...\n 40%|###  | 4/10 [00:01<00:02, 2.5it/s]"
    assert pipeline.log_step({"debugoutput": log}) == "Generating 3D mesh"
    log = "Exporting GLB...\n/wiro-env/lib/x.py:12: UserWarning: x\nPlease pass the dim explicitly."
    assert pipeline.log_step({"debugoutput": log}) == "Exporting GLB"
    # Long lines full of numbers are skipped in favour of the last announced step.
    log = "Running Dual Contouring...\nAfter remeshing: 11741331 vertices, 23505100 faces"
    assert pipeline.log_step({"debugoutput": log}) == "Running Dual Contouring"
    assert pipeline.log_step({"debugoutput": "Mesh generated!\nDone"}) == "Done"
    assert pipeline.log_step({"debugoutput": "{'seed': 3}"}) == ""
    assert pipeline.log_step({}) == ""


def test_task_progress_shape():
    values = [pipeline.task_progress("task_start", t, 50) for t in range(0, 600, 10)]
    assert values == sorted(values)
    assert values[0] == pytest.approx(0.1) and values[-1] < 0.95
    assert pipeline.task_progress("task_postprocess_start", 0, 50) == 0.97


def test_waiting_stages_creep_but_stay_in_order():
    # A long queue or a cold GPU start still moves the bar, but never past the next stage.
    for status, ceiling in (("task_queue", 0.05), ("task_assign", 0.10)):
        early, late = pipeline.task_progress(status, 0, 50), pipeline.task_progress(status, 600, 50)
        assert early < late <= ceiling
    assert pipeline.task_progress("task_queue", 9999, 50) <= pipeline.task_progress("task_accept", 0, 50)
    assert pipeline.task_progress("task_assign", 9999, 50) <= pipeline.task_progress("task_start", 0, 50)
    assert pipeline.status_group("task_output") == "run" and pipeline.status_group("weird") == "other"
