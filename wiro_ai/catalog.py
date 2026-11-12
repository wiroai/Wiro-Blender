# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Wiro AI models the add-on offers, how their settings map to Run
parameters, and what one run costs.

Prices follow the model pages on wiro.ai (per successful run, in USD). The
cost Wiro AI actually charged is read back from each task when it finishes.
This module does not import bpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MODEL_PAGE = "https://wiro.ai/models/{slug}"


@dataclass(frozen=True)
class ImageModel:
    """A text-to-image model that draws the reference picture for Text to 3D."""

    key: str
    slug: str
    label: str
    description: str
    params: dict = field(default_factory=dict)
    price: float = 0.0
    expected_seconds: float = 20.0

    @property
    def page(self) -> str:
        return MODEL_PAGE.format(slug=self.slug)


@dataclass(frozen=True)
class MeshModel:
    """An image-to-3D model that returns a GLB."""

    key: str
    slug: str
    label: str
    maker: str
    description: str
    detail_label: str
    details: tuple  # (value, label, price, expected seconds)
    default_detail: str
    texture_sizes: bool
    face_limit: tuple  # (min, max) of the face parameter
    expected_seconds: float = 60.0

    @property
    def page(self) -> str:
        return MODEL_PAGE.format(slug=self.slug)

    def detail(self, value: str) -> tuple:
        for row in self.details:
            if row[0] == value:
                return row
        return next(row for row in self.details if row[0] == self.default_detail)


IMAGE_MODELS: tuple[ImageModel, ...] = (
    ImageModel(
        key="NANO_BANANA_2_1",
        slug="google/nano-banana-2-1",
        label="Nano Banana 2.1",
        description="Google Nano Banana 2.1, a 1024 x 1024 PNG",
        params={"resolution": "1K", "aspectRatio": "1:1", "outputFormat": "png"},
        price=0.0336,
        expected_seconds=20.0,
    ),
    ImageModel(
        key="GPT_IMAGE_2_6_FLARE",
        slug="openai/gpt-image-2-6-flare",
        label="GPT Image 2.6 Flare",
        description="OpenAI GPT Image 2.6 Flare, a 1024 x 1024 PNG with a transparent background",
        params={
            "resolution": "1k",
            "ratio": "1:1",
            "quality": "medium",
            "samples": "1",
            "background": "transparent",
            "outputFormat": "png",
        },
        price=0.043,
        expected_seconds=40.0,
    ),
)

MESH_MODELS: tuple[MeshModel, ...] = (
    MeshModel(
        key="TRELLIS_2",
        slug="microsoft/trellis-2",
        label="Trellis-2",
        maker="Microsoft",
        description="Microsoft Trellis-2: a detailed mesh with PBR textures",
        detail_label="Resolution",
        details=(
            ("512", "512", 0.25, 30.0),
            ("1024_cascade", "1024", 0.30, 50.0),
            ("1536_cascade", "1536", 0.35, 90.0),
        ),
        default_detail="1024_cascade",
        texture_sizes=True,
        face_limit=(10_000, 2_000_000),
    ),
    MeshModel(
        key="PIXAL3D",
        slug="tencentarc/pixal3d",
        label="Pixal3D",
        maker="Tencent ARC",
        description="Tencent ARC Pixal3D: a textured mesh that keeps the camera view of the input picture",
        detail_label="Resolution",
        details=(
            ("1024_cascade", "1024", 0.30, 60.0),
            ("1536_cascade", "1536", 0.42, 100.0),
        ),
        default_detail="1024_cascade",
        texture_sizes=True,
        face_limit=(10_000, 2_000_000),
    ),
    MeshModel(
        key="HUNYUAN3D_2_1",
        slug="tencent/hunyuan3d-2-1",
        label="Hunyuan3D-2.1",
        maker="Tencent",
        description="Tencent Hunyuan3D-2.1: a mesh with optional PBR textures",
        detail_label="Octree resolution",
        details=(
            ("256", "256", None, 0.0),
            ("384", "384", None, 0.0),
            ("512", "512", None, 0.0),
        ),
        default_detail="256",
        texture_sizes=False,
        face_limit=(1_000, 200_000),
    ),
)

TEXTURE_SIZES = ("1024", "2048", "4096", "8192")

# Hunyuan3D-2.1 is priced by texturing, not by resolution.
HUNYUAN_PRICE = {True: 0.90, False: 0.30}
HUNYUAN_SECONDS = {True: 120.0, False: 30.0}

FRAMING = (
    "Show one whole object, centered, in a three-quarter view, on a plain white background "
    "with soft studio lighting. No text, no other objects."
)
FRAMING_TRANSPARENT = (
    "Show one whole object, centered, in a three-quarter view, on a transparent background "
    "with soft studio lighting. No text, no other objects."
)


def image_model(key: str) -> ImageModel:
    for model in IMAGE_MODELS:
        if model.key == key:
            return model
    return IMAGE_MODELS[0]


def mesh_model(key: str) -> MeshModel:
    for model in MESH_MODELS:
        if model.key == key:
            return model
    return MESH_MODELS[0]


@dataclass
class MeshSettings:
    """The 3D settings from the panel."""

    detail: str = ""
    texture_size: str = "2048"
    face_limit: int = 200_000
    seed: int = 0
    textured: bool = True
    remove_background: bool = True


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def mesh_params(model: MeshModel, settings: MeshSettings) -> dict[str, str]:
    """Run parameters (except the input image) for a 3D model."""
    detail = model.detail(settings.detail)[0]
    seed = str(max(0, int(settings.seed)))
    low, high = model.face_limit
    faces = str(clamp(settings.face_limit, low, high))
    if model.key == "HUNYUAN3D_2_1":
        return {
            "octree_resolution": detail,
            "max_facenum": faces,
            "generate_texture": "true" if settings.textured else "false",
            "remove_background": "true" if settings.remove_background else "false",
            "seed": seed,
        }
    texture = settings.texture_size if settings.texture_size in TEXTURE_SIZES else "2048"
    return {
        "pipeline_type": detail,
        "texture_size": texture,
        "decimation_target": faces,
        "seed": seed,
    }


def mesh_price(model: MeshModel, settings: MeshSettings) -> float:
    if model.key == "HUNYUAN3D_2_1":
        return HUNYUAN_PRICE[bool(settings.textured)]
    return float(model.detail(settings.detail)[2])


def mesh_seconds(model: MeshModel, settings: MeshSettings) -> float:
    if model.key == "HUNYUAN3D_2_1":
        return HUNYUAN_SECONDS[bool(settings.textured)]
    return float(model.detail(settings.detail)[3]) or model.expected_seconds


def reference_prompt(prompt: str, model: ImageModel, framing: bool) -> str:
    """The prompt sent to the image model in Text to 3D."""
    text = " ".join((prompt or "").split())
    if not framing:
        return text
    hint = FRAMING_TRANSPARENT if model.params.get("background") == "transparent" else FRAMING
    if text and text[-1] not in ".!?":
        text += "."
    return f"{text} {hint}".strip()


def image_params(model: ImageModel, prompt: str) -> dict[str, Any]:
    params = dict(model.params)
    params["prompt"] = prompt
    return params


def format_price(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{value:.4f}".rstrip("0")
    if len(text.split(".")[1]) < 2:
        text = f"{value:.2f}"
    return f"${text}"
