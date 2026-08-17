"""
Detects one or more distinct bill regions in a single photo, so a page with
multiple handwritten bills (e.g. 4 bills on one sheet) is split into separate
crops before OCR.

Approach (classic CV, no ML model required for Phase 1):
 1. Convert to grayscale, blur, edge-detect.
 2. Dilate edges so text/lines inside one bill merge into one blob.
 3. Find external contours -> candidate bill rectangles.
 4. Filter by area (drop tiny noise contours) and merge overlapping boxes.
 5. If nothing reasonable is found, fall back to "whole image = one bill".
"""
import cv2
import numpy as np
from typing import List, Tuple

Box = Tuple[int, int, int, int]  # x, y, w, h


def _boxes_overlap(a: Box, b: Box) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw < bx or bx + bw < ax or ay + ah < by or by + bh < ay)


def _merge_box(a: Box, b: Box) -> Box:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = min(ax, bx)
    y1 = min(ay, by)
    x2 = max(ax + aw, bx + bw)
    y2 = max(ay + ah, by + bh)
    return (x1, y1, x2 - x1, y2 - y1)


def detect_bills(img: np.ndarray, min_area_ratio: float = 0.03) -> List[Box]:
    h, w = img.shape[:2]
    total_area = h * w

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    edges = cv2.Canny(blur, 30, 100)
    kernel = np.ones((25, 25), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: List[Box] = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        if area / total_area < min_area_ratio:
            continue
        # Discard thin "skewer" boxes — these are almost always a fragment of a
        # bill's own border line (e.g. its left or right edge) getting picked up
        # as a separate contour, not a distinct bill. A real bill region should
        # have reasonable width AND height relative to the page.
        if cw / w < 0.15 or ch / h < 0.15:
            continue
        boxes.append((x, y, cw, ch))

    # merge overlapping boxes (common when a bill's border + its text both trigger contours)
    merged: List[Box] = []
    for b in boxes:
        placed = False
        for i, m in enumerate(merged):
            if _boxes_overlap(b, m):
                merged[i] = _merge_box(b, m)
                placed = True
                break
        if not placed:
            merged.append(b)

    if not merged:
        return [(0, 0, w, h)]

    # If we only ended up with one giant box covering almost the whole page,
    # treat the whole page as a single bill (common for a single bill photo).
    if len(merged) == 1:
        bx, by, bw, bh = merged[0]
        if (bw * bh) / total_area > 0.85:
            return [(0, 0, w, h)]

    # sort top-to-bottom, left-to-right (reading order)
    merged.sort(key=lambda b: (b[1] // (h // 4 + 1), b[0]))
    return merged


def crop_bills(img: np.ndarray, boxes: List[Box], padding: int = 10) -> List[Tuple[Box, np.ndarray]]:
    """Returns (box, crop) pairs — only for boxes that produced a valid, non-degenerate crop."""
    h, w = img.shape[:2]
    results: List[Tuple[Box, np.ndarray]] = []
    for box in boxes:
        x, y, bw, bh = box
        x0 = max(0, x - padding)
        y0 = max(0, y - padding)
        x1 = min(w, x + bw + padding)
        y1 = min(h, y + bh + padding)
        crop = img[y0:y1, x0:x1]
        # Guard against degenerate (empty/near-empty) crops, which happen
        # when a detected box sits right at the image edge. cv2.imwrite
        # would otherwise fail silently (returns False, no exception) and
        # leave a DB row pointing at a file that was never written.
        if crop.size == 0 or crop.shape[0] < 10 or crop.shape[1] < 10:
            continue
        results.append((box, crop))
    return results
