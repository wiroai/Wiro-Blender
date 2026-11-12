# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later

from wiro_ai import history


def test_add_persist_remove(tmp_path):
    path = tmp_path / "sub" / "history.json"
    runs = history.History(str(path), limit=3)
    first = runs.add({"kind": "3d", "title": "Trellis-2: a chest", "cost": 0.3336})
    runs.add({"kind": "texture", "title": "Texture: moss", "cost": 0.0336})
    assert [e["kind"] for e in runs.entries] == ["texture", "3d"]
    assert runs.total_cost() == 0.3672
    again = history.History(str(path))
    assert again.get(first["id"])["title"] == "Trellis-2: a chest"
    again.remove(first["id"])
    assert history.History(str(path)).get(first["id"]) is None


def test_limit_and_clear(tmp_path):
    runs = history.History(str(tmp_path / "h.json"), limit=2)
    for index in range(4):
        runs.add({"title": str(index)})
    assert [e["title"] for e in runs.entries] == ["3", "2"]
    runs.clear()
    assert history.History(str(tmp_path / "h.json")).entries == []


def test_broken_file_starts_empty(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not json")
    assert history.History(str(path)).entries == []
