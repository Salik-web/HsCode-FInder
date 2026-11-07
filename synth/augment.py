"""Turn a clean PDF into a messy, phone-photo-like JPEG."""

import io
import random
from typing import Literal, get_args

import numpy as np
import pypdfium2
from PIL import Image, ImageFilter
from pydantic import BaseModel

TransformName = Literal["perspective", "rotation", "blur", "noise", "jpeg"]
# Applied in this order, matching how a photo degrades: framing, then optics, then encoding.
TRANSFORM_ORDER: tuple[TransformName, ...] = get_args(TransformName)

# Strength units: fraction of image size, degrees, blur radius in px, noise sigma, JPEG
# quality. Capped so a person can still read the page: unreadable pages can't be scored.
STRENGTH_RANGES: dict[TransformName, tuple[float, float]] = {
    "perspective": (0.01, 0.04),
    "rotation": (1.0, 6.0),
    "blur": (0.4, 1.0),
    "noise": (4.0, 12.0),
    "jpeg": (35, 60),
}

MIN_TRANSFORMS = 2
RENDER_DPI = 150
PDF_POINTS_PER_INCH = 72
DEFAULT_JPEG_QUALITY = 90
BACKGROUND_COLOUR = (196, 190, 178)  # a desk, showing around the skewed page
MAX_PIXEL_VALUE = 255


class Transform(BaseModel):
    name: TransformName
    strength: float


def augment_pdf(pdf: bytes, rng: random.Random) -> tuple[bytes, list[Transform]]:
    """Degrade page one of `pdf`; return the JPEG and exactly what was applied."""
    transforms = _pick_transforms(rng)
    image = _rasterise_first_page(pdf)
    for transform in transforms:
        image = _apply(image, transform, rng)
    return _encode_jpeg(image, transforms), transforms


def _pick_transforms(rng: random.Random) -> list[Transform]:
    count = rng.randint(MIN_TRANSFORMS, len(TRANSFORM_ORDER))
    chosen = set(rng.sample(TRANSFORM_ORDER, count))
    return [
        Transform(name=name, strength=_pick_strength(name, rng))
        for name in TRANSFORM_ORDER
        if name in chosen
    ]


def _pick_strength(name: TransformName, rng: random.Random) -> float:
    low, high = STRENGTH_RANGES[name]
    strength = round(rng.uniform(low, high), 3)
    if name == "jpeg":
        return float(round(strength))
    if name == "rotation":
        return strength * rng.choice((-1, 1))
    return strength


def _rasterise_first_page(pdf: bytes) -> Image.Image:
    with pypdfium2.PdfDocument(pdf) as document:
        # convert() copies the pixels: the rendered bitmap is freed with the document.
        return document[0].render(scale=RENDER_DPI / PDF_POINTS_PER_INCH).to_pil().convert("RGB")


def _apply(image: Image.Image, transform: Transform, rng: random.Random) -> Image.Image:
    match transform.name:
        case "perspective":
            return _skew(image, transform.strength, rng)
        case "rotation":
            return image.rotate(
                transform.strength,
                resample=Image.Resampling.BICUBIC,
                expand=True,  # keeps the page corners in frame
                fillcolor=BACKGROUND_COLOUR,
            )
        case "blur":
            return image.filter(ImageFilter.GaussianBlur(transform.strength))
        case "noise":
            return _add_noise(image, transform.strength, rng)
        case "jpeg":
            return image  # applied when encoding


def _skew(image: Image.Image, strength: float, rng: random.Random) -> Image.Image:
    """Pull each corner inward, so the whole page stays in frame and no text is cropped."""
    width, height = image.size

    def inset(size: int) -> float:
        return rng.uniform(0, strength) * size

    page_corners = [
        (inset(width), inset(height)),
        (width - inset(width), inset(height)),
        (width - inset(width), height - inset(height)),
        (inset(width), height - inset(height)),
    ]
    source_corners = [(0, 0), (width, 0), (width, height), (0, height)]
    return image.transform(
        image.size,
        Image.Transform.PERSPECTIVE,
        _perspective_coefficients(page_corners, source_corners),
        Image.Resampling.BICUBIC,
        fillcolor=BACKGROUND_COLOUR,
    )


def _perspective_coefficients(
    output_points: list[tuple[float, float]], source_points: list[tuple[float, float]]
) -> tuple[float, ...]:
    """Solve the 8 coefficients Pillow uses to map output pixels back to source pixels."""
    rows = []
    targets = []
    for (x, y), (source_x, source_y) in zip(output_points, source_points, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -source_x * x, -source_x * y])
        rows.append([0, 0, 0, x, y, 1, -source_y * x, -source_y * y])
        targets += [source_x, source_y]
    return tuple(np.linalg.solve(np.array(rows), np.array(targets)).tolist())


def _add_noise(image: Image.Image, sigma: float, rng: random.Random) -> Image.Image:
    generator = np.random.default_rng(rng.getrandbits(64))
    pixels = np.asarray(image, dtype=np.float32)
    noisy = pixels + generator.normal(0, sigma, pixels.shape)
    return Image.fromarray(np.clip(noisy, 0, MAX_PIXEL_VALUE).astype(np.uint8))


def _encode_jpeg(image: Image.Image, transforms: list[Transform]) -> bytes:
    quality = next(
        (int(transform.strength) for transform in transforms if transform.name == "jpeg"),
        DEFAULT_JPEG_QUALITY,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
