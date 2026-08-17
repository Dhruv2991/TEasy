"""
Tesseract-based OCR engine. Returns raw text plus a mean confidence score
(0-1) so the review dashboard can flag low-confidence extractions.
"""
import numpy as np
import pytesseract
from pytesseract import Output


def run_ocr(img: np.ndarray) -> tuple[str, float]:
    """
    Returns (raw_text, mean_confidence in [0,1]).
    """
    data = pytesseract.image_to_data(img, output_type=Output.DICT, config="--psm 6")

    words = []
    confidences = []
    for i, word in enumerate(data["text"]):
        word = word.strip()
        conf = data["conf"][i]
        try:
            conf = float(conf)
        except (ValueError, TypeError):
            continue
        if word and conf >= 0:
            words.append(word)
            confidences.append(conf)

    raw_text = " ".join(words) if words else pytesseract.image_to_string(img)
    mean_conf = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
    return raw_text, mean_conf
