# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later

import email
import hashlib
import hmac
import json

import pytest

from tests.fakes import FakeClock, FakeTransport
from wiro_ai import api


def make_client(transport):
    return api.Client(api.Credentials("key-123"), user_agent="Wiro-Blender/test", transport=transport)


def test_credentials_need_a_key():
    with pytest.raises(api.WiroError, match="API key"):
        api.Credentials("   ")


def test_key_only_headers():
    assert api.Credentials(" key-123 ").headers() == {"x-api-key": "key-123"}


def test_signature_headers():
    headers = api.Credentials("key-123", "secret-456").headers()
    expected = hmac.new(b"key-123", ("secret-456" + headers["x-nonce"]).encode(), hashlib.sha256).hexdigest()
    assert headers["x-signature"] == expected
    assert headers["x-api-key"] == "key-123"


def test_credentials_repr_hides_values():
    creds = api.Credentials("key-123", "secret-456")
    assert "key-123" not in repr(creds) and "secret-456" not in str(creds)


def parse_multipart(body: bytes, content_type: str):
    message = email.message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    parts = []
    for part in message.get_payload():
        parts.append(
            (part.get_param("name", header="content-disposition"), part.get_filename(), part.get_payload(decode=True))
        )
    return parts


def test_multipart_fields_and_files():
    body, content_type = api.encode_multipart(
        [("prompt", "a chair"), ("inputImage", ("photo.png", b"\x89PNG data", "image/png")), ("seed", 7)]
    )
    assert content_type.startswith("multipart/form-data; boundary=")
    assert parse_multipart(body, content_type) == [
        ("prompt", None, b"a chair"),
        ("inputImage", "photo.png", b"\x89PNG data"),
        ("seed", None, b"7"),
    ]


def test_multipart_escapes_header_values():
    body, _ = api.encode_multipart([("inputImage", ('bad"\r\nname.png', b"x", "image/png"))])
    assert b'filename="bad%22  name.png"' in body


def test_error_text_hints():
    body = {"errors": [{"code": 97, "message": "Insufficient balance."}]}
    assert "enough credit" in api.error_text(body, 200, "Run/x/y")
    assert "Wait for one" in api.error_text({"errors": [{"code": "96", "message": "Limit"}]}, 200, "Run/x/y")
    assert "Check the API key" in api.error_text(
        {"errors": ["Project authorization is not founded."]}, 200, "Task/List"
    )
    assert api.error_text(None, 502, "Task/Detail") == "Wiro AI (Task/Detail): HTTP 502."


def test_post_sends_json_and_user_agent():
    transport = FakeTransport()
    client = make_client(transport)
    client.check_key()
    request = transport.requests[0]
    assert request["url"] == "https://api.wiro.ai/v1/Task/List"
    assert request["headers"]["User-Agent"] == "Wiro-Blender/test"
    assert request["headers"]["x-api-key"] == "key-123"
    assert request["headers"]["Content-Type"] == "application/json"
    assert json.loads(request["body"]) == {"limit": "1"}


def test_post_raises_on_result_false():
    client = make_client(FakeTransport(fail_paths={"Task/List"}))
    with pytest.raises(api.WiroError, match="Task/List failed"):
        client.check_key()


def test_post_raises_without_json():
    client = make_client(lambda *args: (502, b"<html>bad gateway</html>"))
    with pytest.raises(api.WiroError, match="HTTP 502 without a JSON body"):
        client.check_key()


def test_run_is_multipart():
    transport = FakeTransport()
    client = make_client(transport)
    response = client.run("microsoft/trellis-2", [("inputImage", "https://cdn/x.png"), ("pipeline_type", "512")])
    assert response["socketaccesstoken"] == "tok100"
    request = transport.requests[0]
    assert request["url"].endswith("/v1/Run/microsoft/trellis-2")
    parts = parse_multipart(request["body"], request["headers"]["Content-Type"])
    assert parts == [("inputImage", None, b"https://cdn/x.png"), ("pipeline_type", None, b"512")]


def test_download_skips_auth_and_needs_https(tmp_path):
    transport = FakeTransport(files={"https://cdn1.wiro.ai/a/0.glb": b"glTF-bytes"})
    client = make_client(transport)
    target = tmp_path / "sub" / "model.glb"
    assert client.download("https://cdn1.wiro.ai/a/0.glb", str(target)) == 10
    assert target.read_bytes() == b"glTF-bytes"
    assert "x-api-key" not in transport.requests[0]["headers"]
    with pytest.raises(api.WiroError, match="HTTPS"):
        client.download("http://cdn1.wiro.ai/a/0.glb", str(target))
    with pytest.raises(api.WiroError, match="HTTP 404"):
        client.download("https://cdn.example/missing.glb", str(target))


def test_wait_for_task_until_done():
    transport = FakeTransport(statuses=["task_queue", "task_assign", "task_start", "task_postprocess_end"])
    client = make_client(transport)
    run = client.run("m/x", [])
    clock = FakeClock()
    seen = []
    task = api.wait_for_task(
        client, run, poll_interval=3, on_update=lambda t, s: seen.append(t["status"]), sleep=clock.sleep, clock=clock
    )
    assert task["status"] == "task_postprocess_end"
    assert seen == ["task_queue", "task_assign", "task_start", "task_postprocess_end"]


@pytest.mark.parametrize(("status", "expected"), [("task_queue", "Task/Cancel"), ("task_start", "Task/Kill")])
def test_wait_for_task_cancel_queued_and_running(status, expected):
    transport = FakeTransport(statuses=[status])
    client = make_client(transport)
    run = client.run("m/x", [])
    clock = FakeClock()
    polls = []
    with pytest.raises(api.WiroCancelled):
        api.wait_for_task(
            client,
            run,
            on_update=lambda task, seconds: polls.append(task),
            should_stop=lambda: bool(polls),
            sleep=clock.sleep,
            clock=clock,
        )
    assert transport.paths()[-1] == expected


def test_wait_for_task_times_out():
    client = make_client(FakeTransport(statuses=["task_start"]))
    run = client.run("m/x", [])
    clock = FakeClock()
    with pytest.raises(api.WiroError, match="did not finish within 10 seconds"):
        api.wait_for_task(client, run, timeout=10, poll_interval=3, sleep=clock.sleep, clock=clock)


def test_wait_for_task_tolerates_a_few_poll_errors():
    calls = {"n": 0}
    transport = FakeTransport(statuses=["task_postprocess_end"])

    def flaky(method, url, headers, body, timeout):
        if url.endswith("Task/Detail"):
            calls["n"] += 1
            if calls["n"] < 3:
                return 503, b"busy"
        return transport(method, url, headers, body, timeout)

    client = make_client(flaky)
    run = client.run("m/x", [])
    clock = FakeClock()
    assert api.wait_for_task(client, run, sleep=clock.sleep, clock=clock)["status"] == "task_postprocess_end"


def test_ensure_success():
    api.ensure_success({"id": "1", "status": "task_postprocess_end", "pexit": "0"})
    with pytest.raises(api.WiroError, match=r"exit code 1\)\. ValueError: no object"):
        api.ensure_success({"id": "1", "pexit": "1", "debugerror": "Traceback\n  File x\nValueError: no object\n\n"})
    with pytest.raises(api.WiroError, match="cancelled"):
        api.ensure_success({"id": "1", "status": "task_cancel"})


def test_pick_glb_prefers_textured():
    task = {
        "outputs": [
            {"name": "0_untextured.glb", "url": "https://c/0_untextured.glb", "contenttype": "model/gltf-binary"},
            {"name": "0.glb", "url": "https://c/0.glb", "contenttype": "model/gltf-binary"},
        ]
    }
    assert api.pick_glb(task)["name"] == "0.glb"
    only_untextured = {"outputs": task["outputs"][:1]}
    assert api.pick_glb(only_untextured)["name"] == "0_untextured.glb"
    with pytest.raises(api.WiroError, match="no GLB"):
        api.pick_glb({"id": "9", "outputs": [{"name": "preview.mp4", "url": "https://c/p.mp4"}]})


def test_pick_image():
    task = {"outputs": [{"name": "a.txt", "url": "https://c/a.txt"}, {"url": "https://c/x/0.webp"}]}
    assert api.pick_image(task)["url"] == "https://c/x/0.webp"
    with pytest.raises(api.WiroError, match="no image"):
        api.pick_image({"outputs": []})


def test_image_upload(tmp_path):
    good = tmp_path / "chair.webp"
    good.write_bytes(b"RIFF....WEBP")
    assert api.image_upload(str(good)) == ("chair.webp", b"RIFF....WEBP", "image/webp")
    with pytest.raises(api.WiroError, match="not found"):
        api.image_upload(str(tmp_path / "missing.png"))
    other = tmp_path / "chair.gif"
    other.write_bytes(b"GIF89a")
    with pytest.raises(api.WiroError, match="PNG, JPEG or WebP"):
        api.image_upload(str(other))
    with pytest.raises(api.WiroError, match="under 0 MB"):
        api.image_upload(str(good), max_bytes=4)


def test_task_cost():
    assert api.task_cost({"totalcost": "0.300000000000"}) == 0.3
    assert api.task_cost({}) is None


def test_public_calls_send_no_key():
    transport = FakeTransport()
    client = api.Client(None, user_agent="Wiro-Blender/test", transport=transport)
    client.post("Tool/List", json_body={"limit": "1"}, auth=False)
    assert "x-api-key" not in transport.requests[0]["headers"]
    with pytest.raises(api.WiroError, match="API key"):
        client.check_key()


def test_balance():
    assert make_client(FakeTransport()).balance() == 12.5


@pytest.mark.parametrize(
    ("name", "ctype", "kind"),
    [
        ("0.glb", "model/gltf-binary", "3d"),
        ("0.ply", "", "3d"),
        ("scene.usdz", "", "3d"),
        ("0.png", "image/png", "image"),
        ("clip.mp4", "", "video"),
        ("voice.mp3", "audio/mpeg", "audio"),
        ("notes.txt", "", "text"),
        ("archive.zip", "application/zip", "file"),
    ],
)
def test_output_kind(name, ctype, kind):
    assert api.output_kind(name, ctype) == kind


def test_task_outputs_and_llm_text():
    task = {
        "outputs": [
            {"name": "0.glb", "url": "https://c/0.glb", "contenttype": "model/gltf-binary"},
            {"contenttype": "raw", "content": {"raw": "ignored"}},
            {"name": "", "url": "https://c/x/preview.mp4"},
        ],
        "debugoutput": "Generating 3D mesh...",
    }
    assert api.task_outputs(task) == [
        ("https://c/0.glb", "0.glb", "3d"),
        ("https://c/x/preview.mp4", "preview.mp4", "video"),
    ]
    assert api.llm_text({"debugoutput": " The answer. ", "outputs": []}) == "The answer."
    raw = {
        "debugoutput": "",
        "outputs": [{"contenttype": "raw", "content": {"segments": [{"type": "answer", "text": "Hi"}]}}],
    }
    assert api.llm_text(raw) == "Hi"
    # A 3D task's log is not an answer.
    assert api.llm_text({"debugoutput": "Exporting GLB...", "outputs": task["outputs"][:1]}) == ""
