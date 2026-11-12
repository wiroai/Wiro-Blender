# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""A fake Wiro AI API for the unit tests."""

from __future__ import annotations

import email
import json
import pathlib

from wiro_ai import catalog

BUNDLED = pathlib.Path(__file__).resolve().parents[1] / "wiro_ai" / "data" / "catalog.json"


def bundled_catalog() -> catalog.Catalog:
    return catalog.Catalog(str(BUNDLED))


def default_outputs(slug: str, task_id: str) -> list:
    """What each kind of model returns."""
    base = f"https://cdn1.wiro.ai/{task_id}"
    if "remove-background" in slug or "rmbg" in slug:
        return [{"name": "cutout.png", "contenttype": "image/png", "url": f"{base}/cutout.png"}]
    if slug.startswith(("google/", "openai/", "bytedance/")):
        return [{"name": "0.png", "contenttype": "image/png", "url": f"{base}/0.png"}]
    if "hy-world" in slug:
        return [
            {"name": "0.ply", "contenttype": "", "url": f"{base}/0.ply"},
            {"name": "0.mp4", "contenttype": "video/mp4", "url": f"{base}/0.mp4"},
        ]
    return [
        {"name": "0_untextured.glb", "contenttype": "model/gltf-binary", "url": f"{base}/0_untextured.glb"},
        {"name": "0.glb", "contenttype": "model/gltf-binary", "url": f"{base}/0.glb"},
    ]


class FakeTransport:
    """Plays back Task/Detail statuses for each Run and serves output files."""

    def __init__(
        self,
        *,
        statuses=None,
        outputs=default_outputs,
        fail_paths=(),
        files=None,
        pexit="0",
        debugerror="",
        debugoutput="Loading image...\nGenerating 3D mesh...\n 40%|####      | 4/10 [00:01<00:02, 2.5it/s]",
        costs=None,
        tools=None,
    ):
        self.statuses = list(statuses or ["task_queue", "task_start", "task_postprocess_end"])
        self.outputs = outputs
        self.fail_paths = set(fail_paths)
        self.files = dict(files or {})
        self.pexit = pexit
        self.debugerror = debugerror
        self.debugoutput = debugoutput
        self.costs = dict(costs or {})
        self.tools = dict(tools or {})
        self.requests: list[dict] = []
        self.tasks: dict[str, dict] = {}

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "GET":
            if url in self.files:
                return 200, self.files[url]
            if url.startswith("https://cdn1.wiro.ai/"):
                return 200, b"file:" + url.encode()
            return 404, b"not found"
        path = url.split("/v1/", 1)[1]
        if path in self.fail_paths:
            return 200, json.dumps({"result": False, "errors": [{"code": 1, "message": f"{path} failed"}]}).encode()
        if path.startswith("Run/"):
            task_id = str(100 + len(self.tasks))
            token = f"tok{task_id}"
            slug = path[len("Run/") :]
            outputs = self.outputs(slug, task_id) if callable(self.outputs) else self.outputs
            self.tasks[token] = {"id": task_id, "slug": slug, "polls": 0, "outputs": outputs or []}
            return 200, json.dumps({"result": True, "taskid": task_id, "socketaccesstoken": token}).encode()
        if path == "Task/Detail":
            token = json.loads(body)["tasktoken"]
            task = self.tasks[token]
            status = self.statuses[min(task["polls"], len(self.statuses) - 1)]
            task["polls"] += 1
            detail = {"id": task["id"], "socketaccesstoken": token, "status": status, "debugoutput": self.debugoutput}
            if status == "task_postprocess_end":
                cost = self.costs.get(task["slug"], "0.30")
                detail.update(pexit=self.pexit, debugerror=self.debugerror, totalcost=cost, outputs=task["outputs"])
            return 200, json.dumps({"result": True, "tasklist": [detail]}).encode()
        if path == "Wallet/List":
            wallet = {"symbol": "USD", "amount": "10.5", "giftedamount": "2", "storeamount": "0"}
            return 200, json.dumps({"result": True, "wallet": [wallet]}).encode()
        if path == "Tool/List":
            request = json.loads(body)
            category = (request.get("categories") or [""])[0]
            tools = self.tools.get(category, [])
            return 200, json.dumps({"result": True, "tool": tools, "total": len(tools)}).encode()
        if path == "Tool/Detail":
            request = json.loads(body)
            slug = f"{request['slugowner']}/{request['slugproject']}"
            tool = self.tools.get(slug)
            return 200, json.dumps({"result": True, "tool": [tool] if tool else []}).encode()
        if path in ("Task/Cancel", "Task/Kill", "Task/List"):
            return 200, json.dumps({"result": True}).encode()
        return 404, b"{}"

    def paths(self, method="POST"):
        return [r["url"].split("/v1/", 1)[-1] for r in self.requests if r["method"] == method]

    def runs(self):
        return [r for r in self.requests if "/v1/Run/" in r["url"]]


def form_parts(request) -> list:
    """The multipart fields of a request as (name, filename, bytes), in order."""
    content_type = request["headers"]["Content-Type"].encode()
    message = email.message_from_bytes(b"Content-Type: " + content_type + b"\r\n\r\n" + request["body"])
    return [
        (part.get_param("name", header="content-disposition"), part.get_filename(), part.get_payload(decode=True))
        for part in message.get_payload()
    ]


class FakeClock:
    """A clock that moves only when the code sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.01)
