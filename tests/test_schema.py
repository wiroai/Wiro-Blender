# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later

from tests.fakes import bundled_catalog
from wiro_ai import schema

CATALOG = bundled_catalog()


def tool(items, **extra):
    return {
        "cleanslugowner": "acme",
        "cleanslugproject": "model",
        "title": "Acme",
        "parameters": [{"items": items}],
        **extra,
    }


def test_trellis_schema():
    model = CATALOG.schema("microsoft/trellis-2")
    assert model.slug == "microsoft/trellis-2"
    image = model.image_param()
    assert image.id == "inputImage" and image.type == "combinefileinput" and image.max_files == 1
    resolution = model.param("pipeline_type")
    assert resolution.value_kind == "enum" and ("1024_cascade", "1024") in resolution.options
    faces = model.param("decimation_target")
    assert faces.value_kind == "int" and faces.minimum == 10000 and faces.maximum == 2000000
    assert model.param("seed").value_kind == "text"
    assert model.param("ss_guidance_strength").value_kind == "float"
    assert {row["inputs"]["pipeline_type"] for row in model.price_rows} == {"512", "1024_cascade", "1536_cascade"}


def test_multi_image_model():
    model = CATALOG.schema("tencent/hy-world-2-0-world-reconstruction")
    image = model.image_param()
    assert image.id == "inputImage" and image.max_files == 100
    assert model.param("inputVideo").file_kind == "video"


def test_image_model_slots():
    model = CATALOG.schema("google/nano-banana-2-1")
    assert model.prompt_param().id == "prompt"
    # Optional number with an empty default: a text field, so empty sends nothing.
    assert model.param("seed").value_kind == "text"
    assert model.param("seed").form_default() == ""
    assert model.param("aspectRatio").value_kind == "enum"
    assert model.image_param().id == "inputImage" and not model.image_param().required


def test_samples_never_prefill():
    model = schema.parse_tool(
        tool(
            [
                {"id": "prompt", "type": "textarea", "defaultvalue": "A sample prompt", "required": True},
                {"id": "inputImage", "type": "combinefileinput", "defaultvalue": "https://cdn.wiro.ai/sample.png"},
                {"id": "messages", "type": "json-array", "defaultvalue": "[]"},
                {"id": "steps", "type": "number", "defaultvalue": "30", "minvalue": "1", "maxvalue": "50"},
                {"id": "guide", "type": "float", "defaultvalue": "7.5"},
                {"id": "flag", "type": "checkbox", "defaultvalue": "true"},
                {
                    "id": "size",
                    "type": "select",
                    "defaultvalue": "nope",
                    "options": [{"value": "1K", "text": "1K"}, {"value": "2K", "text": "2K"}],
                },
            ]
        )
    )
    defaults = {p.id: p.form_default() for p in model.params}
    assert defaults == {
        "prompt": "",
        "inputImage": "",
        "messages": "",
        "steps": 30,
        "guide": 7.5,
        "flag": True,
        "size": "1K",
    }


def test_fit():
    model = schema.parse_tool(
        tool(
            [
                {"id": "steps", "type": "number", "minvalue": "1", "maxvalue": "50", "defaultvalue": "30"},
                {
                    "id": "size",
                    "type": "select",
                    "options": [{"value": "512", "text": "Small"}, {"value": "1024", "text": "Large"}],
                },
            ]
        )
    )
    steps, size = model.param("steps"), model.param("size")
    assert steps.fit(99) == 50 and steps.fit(-3) == 1 and steps.fit("12.6") == 13
    assert size.fit("900") == "1024" and size.fit("large") == "1024" and size.fit("xyz") == "512"


def test_notes_and_ranges():
    model = schema.parse_tool(
        tool(
            [
                {
                    "id": "seed",
                    "type": "number",
                    "note": "seed-help",
                    "minvalue": "0",
                    "maxvalue": "10",
                    "defaultvalue": "0",
                }
            ]
        )
    )
    param = model.param("seed")
    assert param.note == "" and param.range_text() == "0 to 10"


def test_build_fields_file_types():
    model = schema.parse_tool(
        tool(
            [
                {"id": "inputImage", "type": "combinefileinput"},
                {"id": "inputMask", "type": "fileinput"},
                {"id": "inputMaskUrl", "type": "text"},
                {"id": "inputDocumentMultiple", "type": "multifileinput"},
                {"id": "inputDocumentUrlMultiple", "type": "text"},
                {"id": "prompt", "type": "textarea"},
            ]
        )
    )
    upload = ("a.png", b"PNG", "image/png")
    fields = schema.build_fields(
        model,
        {"prompt": "hi", "flag": True, "empty": "", "none": None, "inputMaskUrl": "ignored"},
        {
            "inputImage": [upload, "https://x/b.png"],
            "inputMask": ["https://x/mask.png"],
            "inputDocumentMultiple": [upload, "https://x/1.pdf", "https://x/2.pdf"],
        },
    )
    assert fields == [
        ("inputImage", upload),
        ("inputImage", "https://x/b.png"),
        ("inputMask", ""),
        ("inputMaskUrl", "https://x/mask.png"),
        ("inputDocumentMultiple", upload),
        ("inputDocumentUrlMultiple", "https://x/1.pdf,https://x/2.pdf"),
        ("prompt", "hi"),
        ("flag", "true"),
    ]
    assert schema.build_fields(model, {}, {"inputMask": [upload]}) == [("inputMask", upload), ("inputMaskUrl", "")]
    assert model.url_companions == {"inputMaskUrl", "inputDocumentUrlMultiple"}


def test_form_value():
    assert schema.form_value(3.0) == "3"
    assert schema.form_value(False) == "false"
    assert schema.form_value({"a": 1}) == '{"a": 1}'
