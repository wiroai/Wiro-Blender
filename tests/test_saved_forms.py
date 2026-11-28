# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""The load_post handler on scenes saved with stale file limits. Plain objects stand in for the
scene, its settings and their form items."""

from types import SimpleNamespace

from tests.fakes import bundled_catalog
from wiro_ai import saved_forms, schema

CATALOG = bundled_catalog()
NO_LIMIT = schema.parse_tool(
    {
        "cleanslugowner": "acme",
        "cleanslugproject": "views",
        "parameters": [{"items": [{"id": "inputImage", "type": "combinefileinput"}]}],
    }
)


def schemas(slug):
    return NO_LIMIT if slug == "acme/views" else CATALOG.schema(slug)


def item(pid, max_files, kind="file"):
    return SimpleNamespace(pid=pid, kind=kind, max_files=max_files)


def form(slug, *items, built=True):
    return SimpleNamespace(slug=slug, built_slug=slug if built else "", params=list(items))


def scene(**forms):
    empty = {attr: form("") for attr in saved_forms.FORMS}
    return SimpleNamespace(wiro_ai=SimpleNamespace(**{**empty, **forms}))


def test_the_load_handler_corrects_stale_saved_limits():
    # As 1.1.1 saves a form after Wiro's rename: every file field kept a limit of 1.
    capped, prompt = item("inputImage", 1), item("prompt", 1, kind="textarea")
    uncapped = item("inputImage", 1)
    saved = scene(run_form=form("google/nano-banana-2-1", capped, prompt), mesh_form=form("acme/views", uncapped))
    saved_forms.on_load("/scenes/saved.blend", scenes=[saved], schemas=schemas)
    assert capped.max_files == 14
    assert uncapped.max_files == 0, "0 is how a form stores no cap"
    assert prompt.max_files == 1, "only file fields change"
    assert hasattr(saved_forms.on_load, "_bpy_persistent"), "Blender keeps the handler when a file loads"


def test_the_load_handler_leaves_what_it_cannot_check():
    no_schema = item("inputImage", 1)  # the model's schema is not on this computer (offline)
    gone = item("inputOldImage", 3)  # a setting the model no longer has
    unbuilt = item("inputImage", 1)  # a form not built for its model yet
    saved = scene(
        run_form=form("someone/not-cached", no_schema),
        cutout_form=form("wiro/remove-background", gone),
        texture_edit_form=form("google/nano-banana-2-1", unbuilt, built=False),
    )
    saved_forms.on_load(scenes=[saved, SimpleNamespace()], schemas=schemas)  # a scene without settings
    assert (no_schema.max_files, gone.max_files, unbuilt.max_files) == (1, 3, 1)
