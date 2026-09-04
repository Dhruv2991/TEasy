"""
Image preprocessing: deskew + contrast/denoise cleanup before OCR.
"""
import cv2
import numpy as np


def load_image(path: str):
    img = cv2.imread(path)
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return img


def deskew(img: np.ndarray, max_correction_deg: float = 12.0) -> np.ndarray:
    """Correct small camera-tilt skew only.

    The previous implementation ran ``minAreaRect`` over *every* thresholded
    pixel (background texture, table grain, table edge, text — all mixed
    together). For a busy photo that box's angle is essentially noise and can
    land anywhere in [-90, 90]; the old "angle < -45" branch then treated
    that noise as a ~90 degree tilt and rotated the whole bill onto its side
    (this is what produced crops showing blank background at the top with
    the invoice text running sideways underneath).

    This version instead:
      1. Uses Hough line detection to find long, straight, *text-baseline or
         table-line* segments, which are a much cleaner skew signal than raw
         ink pixels.
      2. Takes the median angle of near-horizontal lines only (photos of a
         bill book are never rotated anywhere close to 90 degrees, so lines
         far from horizontal are discarded as noise, not "the real skew").
      3. Clamps the correction to +/- max_correction_deg. A real photo skew
         is a few degrees at most; anything larger is treated as a detection
         failure and the image is left untouched rather than "corrected"
         into a bad rotation.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=120,
        minLineLength=max(60, img.shape[1] // 8), maxLineGap=15,
    )
    if lines is None or len(lines) == 0:
        return img

    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        dx, dy = x2 - x1, y2 - y1
        if dx == 0:
            continue
        angle = np.degrees(np.arctan2(dy, dx))
        # Keep only near-horizontal candidates (table/text lines). Anything
        # steeper than this is almost certainly a vertical divider or noise,
        # not the page's skew.
        if abs(angle) <= max_correction_deg:
            angles.append(angle)

    if len(angles) < 5:
        # Not enough reliable evidence of a consistent tilt — safer to do
        # nothing than to guess.
        return img

    angle = float(np.median(angles))
    if abs(angle) < 0.5:
        return img

    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def enhance_for_ocr(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    gray = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return gray


def preprocess_pipeline(path: str) -> np.ndarray:
    img = load_image(path)
    img = deskew(img)
    return img
