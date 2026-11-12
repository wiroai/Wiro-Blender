# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""A small client for the Wiro AI REST API.

It uses only the Python standard library, so the add-on needs no extra
packages. This module does not import bpy, which keeps it usable (and
testable) outside Blender.

API reference: https://wiro.ai/docs/run-a-model and https://wiro.ai/docs/tasks
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable
from typing import Any

API_BASE = "https://api.wiro.ai/v1"

# Task/Detail statuses that close a task. Every other status, including a
# persisted "task_error", means the task is still in progress.
DONE_STATUSES = ("task_postprocess_end", "task_cancel")

# Before these statuses end, a task can still be cancelled instead of killed.
QUEUED_STATUSES = (
    "task_queue",
    "task_accept",
    "task_preprocess_start",
    "task_preprocess_end",
)

MISSING_KEY = "Add your Wiro AI API key in the add-on preferences (Edit > Preferences > Add-ons > Wiro AI)."

# (method, url, headers, body, timeout) -> (status code, response body)
Transport = Callable[[str, str, dict, "bytes | None", float], "tuple[int, bytes]"]


class WiroError(RuntimeError):
    """A Wiro AI request failed, or a task did not succeed."""


class WiroCancelled(WiroError):
    """The user stopped the job."""


class Credentials:
    """The API key of a Wiro AI project, plus its secret for signature projects.

    repr() never shows the values, so the object is safe to log.
    """

    __slots__ = ("_key", "_secret")

    def __init__(self, api_key: str, api_secret: str = "") -> None:
        self._key = (api_key or "").strip()
        self._secret = (api_secret or "").strip()
        if not self._key:
            raise WiroError(MISSING_KEY)

    @property
    def uses_signature(self) -> bool:
        return bool(self._secret)

    def headers(self) -> dict[str, str]:
        """Auth headers for one request, with a fresh nonce for signature projects."""
        headers = {"x-api-key": self._key}
        if self._secret:
            nonce = str(time.time_ns() // 1_000_000)
            headers["x-nonce"] = nonce
            headers["x-signature"] = hmac.new(
                self._key.encode("utf-8"),
                (self._secret + nonce).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        return headers

    def __repr__(self) -> str:
        kind = "API key and secret" if self._secret else "API key"
        return f"<Wiro AI credentials: {kind}>"

    __str__ = __repr__


def _ssl_context() -> ssl.SSLContext:
    # Blender ships certifi with its Python. Prefer its CA bundle, because the
    # system certificate store is not always visible to Blender's OpenSSL.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def urllib_transport() -> Transport:
    context = _ssl_context()

    def transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read() or b""
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise WiroError(f"Could not reach Wiro AI: {reason}") from None

    return transport


def encode_multipart(fields: Iterable[tuple[str, Any]]) -> tuple[bytes, str]:
    """Encode form fields as multipart/form-data.

    fields: (name, value) pairs. A value is a string, or a (filename, bytes,
    mime type) tuple for a file. A name may repeat.
    Returns the body and its Content-Type header.
    """
    boundary = "wiro-blender-" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields:
        safe_name = _header_safe(name)
        chunks.append(f"--{boundary}\r\n".encode())
        if isinstance(value, tuple):
            filename, content, mime = value
            chunks.append(
                (
                    f'Content-Disposition: form-data; name="{safe_name}"; '
                    f'filename="{_header_safe(filename)}"\r\n'
                    f"Content-Type: {mime or 'application/octet-stream'}\r\n\r\n"
                ).encode()
            )
            chunks.append(content)
        else:
            chunks.append(f'Content-Disposition: form-data; name="{safe_name}"\r\n\r\n'.encode())
            chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _header_safe(text: str) -> str:
    return str(text).replace("\r", " ").replace("\n", " ").replace('"', "%22")


def error_text(body: Any, status: int, path: str) -> str:
    """A readable message from a Wiro error response."""
    errors = body.get("errors") if isinstance(body, dict) else None
    messages: list[str] = []
    codes: set[int] = set()
    for item in errors or []:
        if isinstance(item, dict):
            message = str(item.get("message") or "").strip()
            code = item.get("code")
            if isinstance(code, int) or (isinstance(code, str) and code.isdigit()):
                codes.add(int(code))
        else:
            message = str(item).strip()
        if message:
            messages.append(message)
    text = "; ".join(messages).rstrip(".") or f"HTTP {status}"
    hint = ""
    if 97 in codes:
        hint = " Your Wiro AI wallet does not have enough credit for this run."
    elif 96 in codes:
        hint = " Wait for one of your running tasks to finish, then try again."
    elif status in (401, 403) or "authorization" in text.lower():
        hint = " Check the API key (and the API secret, if the project uses signature authentication)."
    return f"Wiro AI ({path}): {text}.{hint}"


class Client:
    """The Wiro AI endpoints the add-on uses."""

    def __init__(
        self,
        credentials: Credentials | None,
        *,
        user_agent: str,
        base_url: str = API_BASE,
        transport: Transport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.credentials = credentials
        self.user_agent = user_agent
        self.base_url = base_url.rstrip("/")
        self.transport = transport or urllib_transport()
        self.timeout = timeout

    # -- requests ---------------------------------------------------------

    def post(
        self,
        path: str,
        *,
        json_body: Any = None,
        form: Iterable | None = None,
        timeout: float | None = None,
        auth: bool = True,
    ) -> dict:
        """POST to the API. auth=False is for the public catalog: the key is not sent."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        # Cloudflare in front of the API rejects Python's default User-Agent,
        # so every request names the add-on.
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if auth:
            if self.credentials is None:
                raise WiroError(MISSING_KEY)
            headers.update(self.credentials.headers())
        if form is not None:
            body, content_type = encode_multipart(form)
        else:
            body, content_type = json.dumps(json_body or {}).encode("utf-8"), "application/json"
        headers["Content-Type"] = content_type
        status, raw = self.transport("POST", url, headers, body, timeout or self.timeout)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            data = None
        if not isinstance(data, dict):
            raise WiroError(f"Wiro AI ({path}) answered HTTP {status} without a JSON body.")
        if data.get("result") is not True:
            raise WiroError(error_text(data, status, path))
        return data

    def run(self, model: str, fields: Iterable[tuple[str, Any]]) -> dict:
        """Start a task as multipart form data. Returns taskid and socketaccesstoken."""
        data = self.post(f"Run/{model}", form=list(fields), timeout=300)
        if not data.get("socketaccesstoken"):
            raise WiroError("Wiro AI accepted the run but returned no task token.")
        return data

    def task_detail(self, token: str) -> dict:
        data = self.post("Task/Detail", json_body={"tasktoken": token})
        tasks = data.get("tasklist") or []
        if not tasks or not isinstance(tasks[0], dict):
            raise WiroError("Task/Detail returned no task.")
        return tasks[0]

    def stop(self, task: dict) -> None:
        """Cancel a queued task or kill a running one. Best effort."""
        status = task.get("status")
        task_id = str(task.get("id") or "")
        token = str(task.get("socketaccesstoken") or "")
        attempts = []
        if task_id and (status is None or status in QUEUED_STATUSES):
            attempts.append(("Task/Cancel", {"taskid": task_id}))
        if token:
            attempts.append(("Task/Kill", {"socketaccesstoken": token}))
        for path, body in attempts:
            try:
                self.post(path, json_body=body)
                return
            except WiroError:
                continue

    def check_key(self) -> None:
        """Raise WiroError unless the credentials are accepted. Free to call."""
        self.post("Task/List", json_body={"limit": "1"})

    def balance(self) -> float | None:
        """The USD balance of the wallet the key's project pays from. Free to call."""
        data = self.post("Wallet/List", json_body={})
        wallets = [w for w in data.get("wallet") or [] if isinstance(w, dict)]
        wallet = next((w for w in wallets if w.get("symbol") == "USD"), wallets[0] if wallets else None)
        if wallet is None:
            return None
        total = 0.0
        for key in ("amount", "giftedamount", "storeamount"):
            try:
                total += float(wallet.get(key) or 0)
            except (TypeError, ValueError):
                continue
        return round(total, 2)

    def download(
        self,
        url: str,
        path: str,
        *,
        should_stop: Callable[[], bool] | None = None,
        timeout: float = 300.0,
    ) -> int:
        """Save an output file. Output URLs are public, so no auth headers are sent."""
        if urllib.parse.urlparse(url).scheme != "https":
            raise WiroError(f"Refusing to download a file that is not served over HTTPS: {url}")
        if should_stop is not None and should_stop():
            raise WiroCancelled("Stopped.")
        status, content = self.transport("GET", url, {"User-Agent": self.user_agent}, None, timeout)
        if status != 200:
            raise WiroError(f"Downloading {os.path.basename(urllib.parse.urlparse(url).path)} failed (HTTP {status}).")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        partial = path + ".part"
        with open(partial, "wb") as handle:
            handle.write(content)
        os.replace(partial, path)
        return len(content)


def wait_for_task(
    client: Client,
    run_response: dict,
    *,
    timeout: float = 1800.0,
    poll_interval: float = 3.0,
    on_update: Callable[[dict, float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_poll_errors: int = 5,
) -> dict:
    """Poll Task/Detail until the task reaches task_postprocess_end or task_cancel."""
    token = str(run_response.get("socketaccesstoken") or "")
    task: dict = {"id": run_response.get("taskid"), "socketaccesstoken": token, "status": "task_queue"}
    start = clock()
    errors = 0
    while True:
        if should_stop is not None and should_stop():
            client.stop(task)
            raise WiroCancelled("Stopped.")
        try:
            task = client.task_detail(token)
            errors = 0
        except WiroError:
            errors += 1
            if errors >= max_poll_errors:
                raise
        else:
            if on_update is not None:
                on_update(task, clock() - start)
            if task.get("status") in DONE_STATUSES:
                return task
        if clock() - start >= timeout:
            raise WiroError(
                f"Task {task.get('id', '')} did not finish within {int(timeout)} seconds. "
                "It may still finish on Wiro AI; its files will be in your Wiro AI dashboard."
            )
        # Sleep in short steps so Cancel reacts quickly.
        deadline = clock() + poll_interval
        while clock() < deadline:
            if should_stop is not None and should_stop():
                break
            sleep(min(0.25, max(0.0, deadline - clock())))


def ensure_success(task: dict) -> None:
    """Raise unless the task finished with exit code 0."""
    task_id = task.get("id", "")
    if task.get("status") == "task_cancel":
        raise WiroError(f"Task {task_id} was cancelled.")
    pexit = str(task.get("pexit", "")).strip()
    if pexit != "0":
        detail = ""
        for line in reversed(str(task.get("debugerror") or "").splitlines()):
            if line.strip():
                detail = line.strip()[:200]
                break
        message = f"Task {task_id} failed (exit code {pexit or 'unknown'})."
        if detail:
            message += f" {detail}"
        raise WiroError(message)


def _output_name(output: dict) -> str:
    name = str(output.get("name") or "")
    if not name:
        name = os.path.basename(urllib.parse.urlparse(str(output.get("url") or "")).path)
    return name


def pick_glb(task: dict) -> dict:
    """The GLB output of a 3D task. A textured GLB wins over an untextured one."""
    glbs = []
    for output in task.get("outputs") or []:
        if not isinstance(output, dict) or not output.get("url"):
            continue
        name = _output_name(output).lower()
        ctype = str(output.get("contenttype") or "").lower()
        if name.endswith(".glb") or ctype == "model/gltf-binary":
            glbs.append(output)
    if not glbs:
        raise WiroError(f"Task {task.get('id', '')} returned no GLB file.")
    glbs.sort(key=lambda o: "untextured" in _output_name(o).lower())
    return glbs[0]


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def pick_image(task: dict) -> dict:
    """The first image output of an image task."""
    for output in task.get("outputs") or []:
        if not isinstance(output, dict) or not output.get("url"):
            continue
        name = _output_name(output).lower()
        ctype = str(output.get("contenttype") or "").lower()
        if ctype.startswith("image/") or name.endswith(IMAGE_EXTENSIONS):
            return output
    raise WiroError(f"Task {task.get('id', '')} returned no image.")


def image_upload(path: str, *, max_bytes: int = 30 * 1024 * 1024) -> tuple[str, bytes, str]:
    """Read a local image for upload: (filename, bytes, mime type)."""
    if not os.path.isfile(path):
        raise WiroError(f"Image not found: {path}")
    size = os.path.getsize(path)
    if size > max_bytes:
        raise WiroError(f"The image is {size / 1048576:.0f} MB. Use an image under {max_bytes // 1048576} MB.")
    if not path.lower().endswith(IMAGE_EXTENSIONS):
        raise WiroError("Use a PNG, JPEG or WebP image.")
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if path.lower().endswith(".webp"):
        mime = "image/webp"
    with open(path, "rb") as handle:
        return os.path.basename(path), handle.read(), mime


def task_cost(task: dict) -> float | None:
    try:
        return float(task.get("totalcost"))
    except (TypeError, ValueError):
        return None


MODEL_EXTENSIONS = (".glb", ".gltf", ".obj", ".fbx", ".ply", ".stl", ".usdz", ".usd", ".usdc", ".usda", ".abc")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")
TEXT_EXTENSIONS = (".txt", ".md", ".json", ".srt", ".vtt", ".csv")


def output_kind(name: str, content_type: str = "") -> str:
    """3d, image, video, audio, text or file, from an output's name and type."""
    lower = name.lower().split("?")[0]
    ctype = (content_type or "").lower()
    if lower.endswith(MODEL_EXTENSIONS) or ctype.startswith("model/"):
        return "3d"
    if ctype.startswith("image/") or lower.endswith(IMAGE_EXTENSIONS + (".gif", ".bmp", ".tif", ".tiff", ".exr")):
        return "image"
    if ctype.startswith("video/") or lower.endswith(VIDEO_EXTENSIONS):
        return "video"
    if ctype.startswith("audio/") or lower.endswith(AUDIO_EXTENSIONS):
        return "audio"
    if ctype.startswith("text/") or lower.endswith(TEXT_EXTENSIONS):
        return "text"
    return "file"


def task_outputs(task: dict) -> list:
    """The task's output files as (url, name, kind), in the order Wiro lists them."""
    result = []
    for output in task.get("outputs") or []:
        if not isinstance(output, dict) or not output.get("url"):
            continue
        ctype = str(output.get("contenttype") or "")
        if ctype.lower() == "raw":
            continue
        name = _output_name(output)
        result.append((str(output["url"]), name, output_kind(name, ctype)))
    return result


def llm_text(task: dict) -> str:
    """The answer of an LLM task: debugoutput, else the structured raw output."""
    text = str(task.get("debugoutput") or "").strip()
    has_raw = any(
        isinstance(o, dict) and str(o.get("contenttype") or "").lower() == "raw" for o in task.get("outputs") or []
    )
    if text and (has_raw or not task_outputs(task)):
        return text
    for output in task.get("outputs") or []:
        if not isinstance(output, dict) or str(output.get("contenttype") or "").lower() != "raw":
            continue
        content = output.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, dict):
            raw = content.get("raw")
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            answers = [
                str(s.get("text") or "")
                for s in content.get("segments") or []
                if isinstance(s, dict) and s.get("type") == "answer"
            ]
            if any(answers):
                return "".join(answers).strip()
    return ""
