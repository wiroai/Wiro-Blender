# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""A model's settings, read from its Tool/Detail record.

Every panel that runs a model builds its form from this, so a model that joins
the Wiro AI catalog gets a working form without an add-on update. It follows
the rules of https://wiro.ai/docs/model-parameters:

- fileinput: a file goes in {id} with {id}Url sent empty; a URL goes in {id}Url.
- multifileinput: files go in {id}; URLs go comma-separated in the field that
  puts "Url" before "Multiple" (inputDocumentMultiple -> inputDocumentUrlMultiple).
- combinefileinput: files and URLs both go in {id}, one entry each.

This module does not import bpy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

FILE_TYPES = ("fileinput", "multifileinput", "combinefileinput")
ENUM_TYPES = ("select", "selectwithcover", "multiselect")
JSON_TYPES = ("json", "json-array")

# How the panels fill the common slots of a model.
PROMPT_IDS = re.compile(
    r"^(prompt|text|script|lyrics|caption|instruction|instructions|query|question|message|userprompt|"
    r"user_prompt|inputtext|input_text)$",
    re.IGNORECASE,
)
RATIO_IDS = re.compile(r"^(ratio|aspectratio|aspect_ratio|aspect)$", re.IGNORECASE)
COUNT_IDS = re.compile(r"^(n|samples|numberofimages|number_of_images|numimages|num_images|numoutputs|count)$", re.I)
FORMAT_IDS = re.compile(r"^(outputformat|output_format|format|imageformat)$", re.IGNORECASE)


@dataclass
class Param:
    id: str
    type: str
    label: str
    note: str = ""
    required: bool = False
    advanced: bool = False
    hidden: bool = False
    default: Any = ""
    options: list = field(default_factory=list)  # [(value, label)]
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    max_files: int = 1

    @property
    def is_file(self) -> bool:
        return self.type in FILE_TYPES

    @property
    def file_kind(self) -> str:
        """image, video, audio or file, read from the parameter's id and label."""
        text = f"{self.id} {self.label}".lower()
        if re.search(r"video|clip|mp4|footage", text):
            return "video"
        if re.search(r"audio|voice|speech|song|music|sound|mp3|wav|vocal", text):
            return "audio"
        if re.search(r"image|photo|picture|face|portrait|frame|logo|mask|garment|cloth|human|person|product|"
                     r"reference|selfie|avatar|style", text):  # fmt: skip
            return "image"
        return "file"

    @property
    def value_kind(self) -> str:
        """How the panel edits it: file, enum, bool, int, float, json, textarea or text."""
        if self.is_file:
            return "file"
        if self.type in ENUM_TYPES or (self.options and self.type not in ("checkbox",)):
            return "enum"
        if self.type == "checkbox":
            return "bool"
        if self.type in ("number", "float", "range") and not self.required and str(self.default).strip() == "":
            # An optional number without a default ("empty means random") stays a text
            # field, so leaving it empty sends nothing.
            return "text"
        if self.type in ("number", "float", "range"):
            whole = all(value is None or float(value).is_integer() for value in (self.minimum, self.maximum, self.step))
            if self.type == "number" and whole:
                return "int"
            return "float"
        if self.type in JSON_TYPES:
            return "json"
        if self.type == "textarea":
            return "textarea"
        return "text"

    @property
    def is_sample(self) -> bool:
        """Whether the default is only an example to show, never a value to send.

        Wiro puts sample inputs in the default of file fields, prompts and chat
        fields. A form starts those empty.
        """
        if self.is_file or self.type in JSON_TYPES:
            return True
        if PROMPT_IDS.match(self.id) or self.type == "textarea":
            return True
        return "input" in self.id.lower()

    def form_default(self) -> Any:
        """The value a new form starts with."""
        kind = self.value_kind
        if self.is_sample:
            return "" if kind not in ("bool",) else False
        default = self.default
        if isinstance(default, list):
            default = default[0] if default else ""
        if kind == "bool":
            return str(default).strip().lower() in ("true", "1", "yes", "on")
        if kind == "int":
            return int(round(to_float(default, self.minimum or 0.0)))
        if kind == "float":
            return to_float(default, self.minimum or 0.0)
        if kind == "enum":
            values = [value for value, _label in self.options]
            text = str(default)
            return text if text in values else (values[0] if values else text)
        return "" if default is None else str(default)

    def fit(self, value: Any) -> Any:
        """Clamp a number to the parameter's range, or pick the nearest option."""
        kind = self.value_kind
        if kind in ("int", "float"):
            number = to_float(value, 0.0)
            if self.minimum is not None:
                number = max(self.minimum, number)
            if self.maximum is not None:
                number = min(self.maximum, number)
            return int(round(number)) if kind == "int" else number
        if kind == "enum" and self.options:
            values = [option for option, _label in self.options]
            if str(value) in values:
                return str(value)
            try:
                target = float(value)
                numeric = [float(option) for option in values]
            except (TypeError, ValueError):
                lowered = str(value).lower()
                for option, label in self.options:
                    if option.lower() == lowered or label.lower() == lowered:
                        return option
                return values[0]
            return values[min(range(len(numeric)), key=lambda i: abs(numeric[i] - target))]
        return value

    def range_text(self) -> str:
        if self.minimum is None and self.maximum is None:
            return ""
        low = "" if self.minimum is None else format_number(self.minimum)
        high = "" if self.maximum is None else format_number(self.maximum)
        return f"{low} to {high}".strip()


@dataclass
class ModelSchema:
    slug: str
    title: str
    description: str = ""
    categories: list = field(default_factory=list)
    params: list = field(default_factory=list)
    price_rows: list = field(default_factory=list)
    estimated_cost: float | None = None
    cost_per_second: float | None = None
    discount_percent: float | None = None

    def param(self, pid: str) -> Param | None:
        return next((p for p in self.params if p.id == pid), None)

    def file_params(self, kind: str | None = None) -> list:
        params = [p for p in self.params if p.is_file]
        if kind is not None:
            params = [p for p in params if p.file_kind == kind]
        return params

    def image_param(self) -> Param | None:
        """The image input a panel fills: the first required one, else the first one."""
        images = [p for p in self.file_params("image") if "mask" not in p.id.lower()]
        if not images:
            images = self.file_params("file")
        return next((p for p in images if p.required), images[0] if images else None)

    def prompt_param(self) -> Param | None:
        named = next((p for p in self.params if not p.is_file and PROMPT_IDS.match(p.id)), None)
        if named:
            return named
        return next((p for p in self.params if p.type == "textarea" and p.required), None)

    def find(self, pattern: re.Pattern) -> Param | None:
        return next((p for p in self.params if not p.is_file and pattern.match(p.id)), None)

    @property
    def url_companions(self) -> set:
        """Text fields that only repeat a file field as a URL. Forms leave them out."""
        ids = {p.id for p in self.params}
        companions = set()
        for p in self.file_params():
            twin = url_field(p.id, p.type)
            if twin and twin in ids:
                companions.add(twin)
            if f"{p.id}Url" in ids:
                companions.add(f"{p.id}Url")
        return companions


def to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number else fallback  # NaN check


def format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows(value: Any) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else []
        except ValueError:
            return []
    return [row for row in value or [] if isinstance(row, dict)]


def parse_tool(tool: dict) -> ModelSchema:
    """A ModelSchema from a Tool/Detail (or bundled) record."""
    owner = str(tool.get("cleanslugowner") or tool.get("slugowner") or "").strip()
    project = str(tool.get("cleanslugproject") or tool.get("slugproject") or "").strip()
    groups = tool.get("parameters")
    if isinstance(groups, str):
        try:
            groups = json.loads(groups)
        except ValueError:
            groups = []
    params = []
    for group in groups or []:
        for item in (group or {}).get("items") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            options = []
            for option in item.get("options") or []:
                if isinstance(option, dict) and option.get("value") not in (None, ""):
                    value = str(option["value"])
                    options.append((value, str(option.get("text") or option.get("label") or value)))
            max_files = item.get("maxinputlenght")
            ptype = str(item.get("type") or "text").lower()
            params.append(
                Param(
                    id=str(item["id"]),
                    type=ptype,
                    label=str(item.get("label") or item.get("placeholder") or item["id"]).strip(),
                    note=_note(item.get("note")),
                    required=bool(item.get("required")),
                    advanced=bool(item.get("advanced")),
                    hidden=bool(item.get("hidden")),
                    default=item.get("defaultvalue", ""),
                    options=options,
                    minimum=_number(item.get("minvalue")),
                    maximum=_number(item.get("maxvalue")),
                    step=_number(item.get("incrementby")),
                    max_files=int(max_files) if str(max_files or "").isdigit() and int(max_files) > 0 else 1,
                )
            )
    discount = _number(tool.get("discount"))
    return ModelSchema(
        slug=f"{owner}/{project}".lower(),
        title=str(tool.get("title") or project),
        description=str(tool.get("description") or ""),
        categories=[str(c) for c in tool.get("categories") or []],
        params=params,
        price_rows=_rows(tool.get("dynamicprice")),
        estimated_cost=_number(tool.get("approximatelycost")) or None,
        cost_per_second=_number(tool.get("cps")) or None,
        discount_percent=discount if discount and 0 < discount <= 100 else None,
    )


def _note(value: Any) -> str:
    text = " ".join(str(value or "").split())
    # Some notes are translation keys such as "seed-help"; they read as noise.
    return "" if re.fullmatch(r"[a-z0-9-]+-help", text) else text


def url_field(param_id: str, param_type: str | None) -> str | None:
    """The companion URL field of a file parameter, if it has one."""
    if param_type == "fileinput":
        return f"{param_id}Url"
    if param_type == "multifileinput":
        if param_id.endswith("Multiple"):
            return param_id[: -len("Multiple")] + "UrlMultiple"
        return f"{param_id}Url"
    return None


def is_url(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("https://", "http://"))


def form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def build_fields(schema: ModelSchema, values: dict, files: dict | None = None) -> list:
    """Multipart fields for a run.

    values: parameter id -> value. Empty strings and None are left out.
    files: parameter id -> list of entries, each a URL string or a
        (filename, bytes, mime type) upload.
    """
    files = {pid: [entry for entry in entries if entry] for pid, entries in (files or {}).items()}
    types = {p.id: p.type for p in schema.params}
    fields: list = []
    file_ids = set()
    for pid, entries in files.items():
        if not entries:
            continue
        ptype = types.get(pid, "combinefileinput")
        uploads = [entry for entry in entries if isinstance(entry, tuple)]
        urls = [entry.strip() for entry in entries if is_url(entry)]
        file_ids.add(pid)
        companion = url_field(pid, ptype)
        if ptype == "combinefileinput" or companion is None:
            fields.extend((pid, entry) for entry in uploads)
            fields.extend((pid, url) for url in urls)
            continue
        if ptype == "fileinput":
            if uploads:
                fields.append((pid, uploads[0]))
                fields.append((companion, ""))
            elif urls:
                fields.append((pid, ""))
                fields.append((companion, urls[0]))
            continue
        # multifileinput
        fields.extend((pid, entry) for entry in uploads)
        fields.append((companion, ",".join(urls)))
        if not uploads:
            fields.append((pid, ""))
    companions = schema.url_companions
    for pid, value in values.items():
        if pid in file_ids or pid in companions or value is None or value == "":
            continue
        fields.append((pid, form_value(value)))
    return fields
