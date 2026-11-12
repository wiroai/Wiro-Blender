# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""A fake Wiro AI API for the unit tests."""

from __future__ import annotations

import json


class FakeTransport:
    """Plays back Task/Detail statuses for each Run and serves output files."""

    def __init__(self, *, statuses=None, outputs=None, fail_paths=(), files=None, pexit="0", debugerror=""):
        # statuses: what Task/Detail returns on each poll, the last one repeats.
        self.statuses = list(statuses or ["task_queue", "task_start", "task_postprocess_end"])
        self.outputs = outputs
        self.fail_paths = set(fail_paths)
        self.files = dict(files or {})
        self.pexit = pexit
        self.debugerror = debugerror
        self.requests: list[dict] = []
        self.tasks: dict[str, dict] = {}

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "GET":
            if url in self.files:
                return 200, self.files[url]
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
            detail = {
                "id": task["id"],
                "socketaccesstoken": token,
                "status": status,
                "debugoutput": "Loading image...\nGenerating 3D mesh...\n 40%|####      | 4/10 [00:01<00:02, 2.5it/s]",
            }
            if status == "task_postprocess_end":
                detail.update(pexit=self.pexit, debugerror=self.debugerror, totalcost="0.30", outputs=task["outputs"])
            return 200, json.dumps({"result": True, "tasklist": [detail]}).encode()
        if path in ("Task/Cancel", "Task/Kill", "Task/List"):
            return 200, json.dumps({"result": True}).encode()
        return 404, b"{}"

    def paths(self, method="POST"):
        return [r["url"].split("/v1/", 1)[-1] for r in self.requests if r["method"] == method]


class FakeClock:
    """A clock that moves only when the code sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.01)
