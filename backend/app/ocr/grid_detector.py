"""Reliable 4-up sales-bill page splitting.

The sales source used by this project is a bill-book page containing four
handwritten bills (2 x 2).  The safest strategy is therefore to explicitly
split the page into four regions instead of asking a contour detector to
"discover" four bills.  We still use the ink projection to locate the real
horizontal/vertical gutter near the centre; if the gutter is obscured, the
centre of the page is used as a deterministic fallback.
"""
import cv2
import numpy as np
from typing import List, Tuple

Box = Tuple[int, int, int, int]


def _binary_ink_mask(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    mask = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        35, 10,
    )
    kernel = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)


def _best_centre_gutter(density: np.ndarray) -> int:
    """Return a stable split near 50%, preferring the lowest-ink valley."""
    n = len(density)
    lo, hi = int(n * 0.40), int(n * 0.60)
    if hi <= lo:
        return n // 2
    window = density[lo:hi]
    # Smooth enough that a few handwritten strokes cannot move the split.
    kernel = max(5, int(n * 0.006) | 1)
    smooth = np.convolve(window, np.ones(kernel) / kernel, mode="same")
    idx = int(np.argmin(smooth))
    return lo + idx


def detect_four_bill_grid(img: np.ndarray) -> List[Box]:
    """Split a 2x2 sales bill-book page into exactly four bill crops."""
    h, w = img.shape[:2]
    mask = _binary_ink_mask(img)
    row_density = mask.sum(axis=1) / 255.0 / max(w, 1)
    col_density = mask.sum(axis=0) / 255.0 / max(h, 1)

    y_cut = _best_centre_gutter(row_density)
    x_cut = _best_centre_gutter(col_density)

    # Keep a small overlap around the gutter so a line/number sitting exactly
    # on a divider is not lost. The overlap is later clipped to page bounds.
    # overlap_y is larger than overlap_x: the handwritten (often red) invoice
    # number sits right at the top of each cell, just below the divider —
    # under-cropping there silently drops the single most important field.
    overlap_x = max(8, int(w * 0.012))
    overlap_y = max(16, int(h * 0.025))

    boxes = [
        (0, 0, min(w, x_cut + overlap_x), min(h, y_cut + overlap_y)),
        (max(0, x_cut - overlap_x), 0, w - max(0, x_cut - overlap_x), min(h, y_cut + overlap_y)),
        (0, max(0, y_cut - overlap_y), min(w, x_cut + overlap_x), h - max(0, y_cut - overlap_y)),
        (max(0, x_cut - overlap_x), max(0, y_cut - overlap_y), w - max(0, x_cut - overlap_x), h - max(0, y_cut - overlap_y)),
    ]
    return boxes


def detect_grid_bills(img: np.ndarray) -> List[Box]:
    """Backward-compatible alias used by older callers."""
    return detect_four_bill_grid(img)
