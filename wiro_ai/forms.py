# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model settings forms built from a model's schema, and the model pickers.

A form is a collection of parameter items on the scene. Building one from a
schema keeps the user's values when the same model is picked again; reading it
gives the values and files for a run.
"""

import json
import mimetypes
import os
import tempfile

import bpy

from . import catalog, schema

# Dynamic enum items must stay referenced while Blender shows them.
_ITEMS_CACHE: dict = {}
_PARAM_ITEMS_CACHE: dict = {}

# Which forms a panel fills, and what each one is for.
PURPOSE_CATEGORY = {
    "mesh": "3d-generation",
    "reference": "text-to-image",
    "cutout": "background-removal",
    "texture": "text-to-image",
    "texture_edit": "image-editing",
}


def _runtime():
    from . import runtime

    return runtime


# -- model pickers -------------------------------------------------------------


def model_items(category: str, current: str) -> list:
    rt = _runtime()
    key = (category, current, rt.catalog.version)
    cached = _ITEMS_CACHE.get(key)
    if cached is not None:
        return cached
    rows = rt.catalog.models(category)
    slugs = [row["slug"] for row in rows]
    if current and current not in slugs:
        rows = [{"slug": current, "title": rt.catalog.title(current), "description": ""}, *rows]
    items = []
    for index, row in enumerate(rows):
        price = rt.catalog.price_label(row["slug"])
        tip = " · ".join(part for part in (row.get("title", ""), price, row.get("description", "")[:160]) if part)
        items.append((row["slug"], row["slug"], tip, index))
    if not items:
        items = [("NONE", "No models yet", "Refresh the model list", 0)]
    if len(_ITEMS_CACHE) > 64:
        _ITEMS_CACHE.clear()
    _ITEMS_CACHE[key] = items
    return items


def model_enum(purpose: str, form_attr: str, name: str, description: str):
    category = PURPOSE_CATEGORY[purpose]

    def items(self, context):
        return model_items(category, getattr(self, form_attr).slug)

    def get(self):
        slug = getattr(self, form_attr).slug
        for identifier, _name, _tip, number in model_items(category, slug):
            if identifier == slug:
                return number
        return 0

    def set(self, value):
        form = getattr(self, form_attr)
        for identifier, _name, _tip, number in model_items(category, form.slug):
            if number == value and identifier != "NONE":
                if identifier != form.slug:
                    form.slug = identifier
                    ensure(form, purpose)
                return

    return bpy.props.EnumProperty(name=name, description=description, items=items, get=get, set=set)


# -- parameter enums -------------------------------------------------------------


def _options(item) -> list:
    if not item.options_json:
        return []
    try:
        return [tuple(option) for option in json.loads(item.options_json)]
    except ValueError:
        return []


def param_items(self, context):
    key = self.options_json
    cached = _PARAM_ITEMS_CACHE.get(key)
    if cached is None:
        cached = [(value, label, value, index) for index, (value, label) in enumerate(_options(self))]
        if not cached:
            cached = [("", "(none)", "", 0)]
        if len(_PARAM_ITEMS_CACHE) > 512:
            _PARAM_ITEMS_CACHE.clear()
        _PARAM_ITEMS_CACHE[key] = cached
    return cached


def param_get(self):
    for index, (value, _label) in enumerate(_options(self)):
        if value == self.value_text:
            return index
    return 0


def param_set(self, value):
    options = _options(self)
    if 0 <= value < len(options):
        self.value_text = options[value][0]


# -- building ----------------------------------------------------------------------


def skip_ids(purpose: str, model: schema.ModelSchema) -> set:
    """Parameters a panel fills itself, so its form leaves them out."""
    skip = set()
    if purpose in ("mesh", "cutout", "texture_edit"):
        image = model.image_param()
        if image is not None:
            skip.add(image.id)
    if purpose in ("reference", "texture", "texture_edit"):
        prompt = model.prompt_param()
        if prompt is not None:
            skip.add(prompt.id)
    if purpose in ("reference", "texture"):
        # A picture to draw from would turn these into edits: leave it to the Run Any Model panel.
        skip.update(p.id for p in model.file_params())
    return skip


def start_values(purpose: str, model: schema.ModelSchema) -> dict:
    values = catalog.overrides(model.slug)
    if purpose == "reference":
        values.update(catalog.context_values(model, "3d"))
    elif purpose in ("texture", "texture_edit"):
        values.update(catalog.context_values(model, "texture"))
    return values


def _set_value(item, param: schema.Param, value) -> None:
    kind = item.kind
    if kind == "int":
        item.value_int = int(param.fit(value))
    elif kind == "float":
        item.value_float = float(param.fit(value))
    elif kind == "bool":
        item.value_bool = value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes", "on")
    elif kind == "enum":
        item.value_text = str(param.fit(value))
    elif kind != "file":
        item.value_text = "" if value is None else str(value)


def build(form, model: schema.ModelSchema, purpose: str) -> None:
    """Fill the form from the schema. Values of a form built for the same model are kept."""
    keep = {}
    if form.built_slug == model.slug:
        for item in form.params:
            keep[item.pid] = (
                item.value_text,
                item.value_int,
                item.value_float,
                item.value_bool,
                item.text_block,
                [(f.path, f.image) for f in item.files],
            )
    form.params.clear()
    skip = skip_ids(purpose, model) | model.url_companions
    starts = start_values(purpose, model)
    for param in model.params:
        if param.hidden or param.id in skip:
            continue
        item = form.params.add()
        item.pid = param.id
        item.ptype = param.type
        item.label = catalog.LABELS.get(param.id, param.label) or param.id
        item.kind = param.value_kind
        item.file_kind = param.file_kind
        item.max_files = param.max_files
        item.required = param.required
        notes = [param.note]
        if param.range_text():
            notes.append(f"Range: {param.range_text()}")
        item.note = " ".join(n for n in notes if n)[:500]
        item.section = "main" if (not param.advanced or param.required or param.id in catalog.PROMOTED) else "more"
        item.options_json = json.dumps(param.options) if param.options else ""
        if param.id in keep:
            text, number, real, flag, block, files = keep[param.id]
            item.value_text, item.value_int, item.value_float, item.value_bool = text, number, real, flag
            item.text_block = block
            for path, image in files:
                entry = item.files.add()
                entry.path, entry.image = path, image
            continue
        _set_value(item, param, starts.get(param.id, param.form_default()))
    form.built_slug = model.slug
    form.purpose = purpose


def ensure(form, purpose: str) -> bool:
    """Build the form if its model's schema is known; otherwise ask for it. True when ready."""
    if not form.slug:
        return False
    rt = _runtime()
    model = rt.catalog.schema(form.slug)
    form.purpose = purpose
    if model is None:
        rt.request_schema(form.slug)  # in background mode this loads it right away
        model = rt.catalog.schema(form.slug)
        if model is None:
            return False
    if form.built_slug != form.slug:
        build(form, model, purpose)
    return True


def ready(form) -> bool:
    return bool(form.slug) and form.built_slug == form.slug


# -- reading -------------------------------------------------------------------------


def image_upload(image) -> tuple:
    """A Blender image as (filename, bytes, mime), without changing it."""
    name = bpy.path.clean_name(image.name) or "image"
    if image.packed_file is not None:
        data = bytes(image.packed_file.data)
        ext = os.path.splitext(image.filepath_raw or "")[1].lower() or ".png"
        return f"{name}{ext}", data, mimetypes.guess_type(f"x{ext}")[0] or "image/png"
    path = bpy.path.abspath(image.filepath_raw or image.filepath or "")
    if path and os.path.isfile(path):
        with open(path, "rb") as handle:
            data = handle.read()
        return os.path.basename(path), data, mimetypes.guess_type(path)[0] or "application/octet-stream"
    # Generated or rendered pixels: save a PNG copy.
    folder = tempfile.mkdtemp(prefix="wiro-ai-")
    target = os.path.join(folder, f"{name}.png")
    image.save_render(target)
    with open(target, "rb") as handle:
        return f"{name}.png", handle.read(), "image/png"


def file_entry(entry, label: str = "file"):
    """A form file entry as a URL or an upload. Raises ValueError when it is missing."""
    if entry.image is not None:
        return image_upload(entry.image)
    path = (entry.path or "").strip()
    if not path:
        return None
    if schema.is_url(path):
        return path
    full = bpy.path.abspath(path)
    if not os.path.isfile(full):
        raise ValueError(f"{label}: file not found: {full}")
    with open(full, "rb") as handle:
        data = handle.read()
    mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
    if full.lower().endswith(".webp"):
        mime = "image/webp"
    return os.path.basename(full), data, mime


def text_value(item) -> str:
    if item.text_block is not None:
        return item.text_block.as_string()
    return item.value_text


def collect(form, model: schema.ModelSchema) -> tuple:
    """(values, files) for a run. Raises ValueError for a missing required setting."""
    values, files = {}, {}
    for item in form.params:
        param = model.param(item.pid)
        if param is None:
            continue
        if item.kind == "file":
            entries = [file_entry(entry, item.label) for entry in item.files]
            entries = [entry for entry in entries if entry]
            if entries:
                files[item.pid] = entries[: max(1, param.max_files)]
            elif param.required:
                raise ValueError(f'"{item.label}" needs a file.')
            continue
        if item.kind == "int":
            value = param.fit(item.value_int)
        elif item.kind == "float":
            value = param.fit(item.value_float)
        elif item.kind == "bool":
            value = item.value_bool
        elif item.kind == "enum":
            value = item.value_text
        else:
            value = text_value(item)
            if item.kind == "json" and value.strip():
                try:
                    json.loads(value)
                except ValueError as exc:
                    raise ValueError(f'"{item.label}" is not valid JSON: {exc}') from None
        if value in ("", None):
            if param.required:
                raise ValueError(f'Fill in "{item.label}".')
            continue
        values[item.pid] = value
    return values, files


def price_params(form, model: schema.ModelSchema) -> dict:
    """The settings the price depends on, without reading any file."""
    params = {}
    for item in form.params:
        if item.kind == "file":
            params[item.pid] = [e.path or (e.image.name if e.image else "") for e in item.files if e.path or e.image]
        elif item.kind == "int":
            params[item.pid] = item.value_int
        elif item.kind == "float":
            params[item.pid] = item.value_float
        elif item.kind == "bool":
            params[item.pid] = "true" if item.value_bool else "false"
        else:
            params[item.pid] = item.value_text
    return params


# -- drawing ---------------------------------------------------------------------------


def draw_item(layout, form_attr: str, index: int, item) -> None:
    # A star marks required settings that can be left empty: text and files.
    starred = item.required and item.kind in ("text", "textarea", "json", "file")
    label = f"{item.label} *" if starred else item.label
    if item.kind == "enum":
        layout.prop(item, "value_enum", text=label)
    elif item.kind == "int":
        layout.prop(item, "value_int", text=label)
    elif item.kind == "float":
        layout.prop(item, "value_float", text=label)
    elif item.kind == "bool":
        layout.prop(item, "value_bool", text=label)
    elif item.kind in ("textarea", "json"):
        col = layout.column(align=True)
        col.use_property_split = False
        col.label(text=label)
        row = col.row(align=True)
        if item.text_block is None:
            row.prop(item, "value_text", text="")
        row.prop(item, "text_block", text="", icon="TEXT")
    elif item.kind == "file":
        col = layout.column(align=True)
        col.use_property_split = False
        header = f"{label} ({len(item.files)}/{item.max_files})" if item.max_files > 1 else label
        col.label(text=header, icon="FILE_IMAGE" if item.file_kind == "image" else "FILE")
        for file_index, entry in enumerate(item.files):
            row = col.row(align=True)
            if entry.image is None:
                row.prop(entry, "path", text="")
            if item.file_kind == "image":
                row.prop(entry, "image", text="")
            op = row.operator("wiro_ai.form_file_remove", text="", icon="X")
            op.form, op.index, op.file_index = form_attr, index, file_index
        if len(item.files) < max(1, item.max_files):
            op = col.operator("wiro_ai.form_file_add", text="Add File", icon="ADD")
            op.form, op.index = form_attr, index
    else:
        layout.prop(item, "value_text", text=label)


def draw(layout, settings, form_attr: str, *, section: str = "main") -> int:
    """Draw one section of a form. Returns how many settings it drew."""
    form = getattr(settings, form_attr)
    drawn = 0
    for index, item in enumerate(form.params):
        if item.section != section:
            continue
        draw_item(layout, form_attr, index, item)
        drawn += 1
    return drawn


def count(form, section: str) -> int:
    return sum(1 for item in form.params if item.section == section)
