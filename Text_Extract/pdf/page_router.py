from __future__ import annotations

import re
from typing import Iterable

from common.models import OCR_METHOD, PYMUPDF_METHOD, PageRoutingDecision


MIN_TEXT_CHARS = 80
MIN_TEXT_WORDS = 12
DOMINANT_LARGEST_IMAGE_RATIO = 0.55
DOMINANT_TOTAL_IMAGE_RATIO = 0.70


def route_page(page_number: int, page: object) -> PageRoutingDecision:
    page_index = page_number - 1
    text = _extract_text(page)
    char_count = len(text.strip())
    word_count = len(re.findall(r"\S+", text))
    total_image_ratio, largest_image_ratio = _calculate_image_ratios(page)

    has_enough_text = char_count >= MIN_TEXT_CHARS or word_count >= MIN_TEXT_WORDS
    has_dominant_image = (
        largest_image_ratio >= DOMINANT_LARGEST_IMAGE_RATIO
        or total_image_ratio >= DOMINANT_TOTAL_IMAGE_RATIO
    )

    if has_dominant_image:
        method = OCR_METHOD
        reason = "image_dominant"
    elif has_enough_text:
        method = PYMUPDF_METHOD
        reason = "embedded_text"
    else:
        method = OCR_METHOD
        reason = "insufficient_text"

    return PageRoutingDecision(
        page_number=page_number,
        page_index=page_index,
        method=method,
        reason=reason,
        char_count=char_count,
        word_count=word_count,
        total_image_ratio=total_image_ratio,
        largest_image_ratio=largest_image_ratio,
    )


def _extract_text(page: object) -> str:
    get_text = getattr(page, "get_text")
    return str(get_text("text"))


def _calculate_image_ratios(page: object) -> tuple[float, float]:
    page_area = _page_area(page)
    if page_area <= 0:
        return 0.0, 0.0

    image_areas = list(_iter_image_areas(page))
    if not image_areas:
        return 0.0, 0.0

    total_ratio = min(sum(image_areas) / page_area, 1.0)
    largest_ratio = min(max(image_areas) / page_area, 1.0)
    return total_ratio, largest_ratio


def _page_area(page: object) -> float:
    rect = getattr(page, "rect")
    width = float(getattr(rect, "width", 0.0))
    height = float(getattr(rect, "height", 0.0))
    return max(width, 0.0) * max(height, 0.0)


def _iter_image_areas(page: object) -> Iterable[float]:
    get_image_info = getattr(page, "get_image_info", None)
    if get_image_info is None:
        return []

    areas: list[float] = []
    for image in get_image_info(xrefs=False):
        bbox = image.get("bbox")
        area = _bbox_area(bbox)
        if area > 0:
            areas.append(area)
    return areas


def _bbox_area(bbox: object) -> float:
    if bbox is None:
        return 0.0
    try:
        x0, y0, x1, y1 = bbox
    except (TypeError, ValueError):
        return 0.0

    width = max(float(x1) - float(x0), 0.0)
    height = max(float(y1) - float(y0), 0.0)
    return width * height
