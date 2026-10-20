"""
Offline backward-compatibility adapter for gemini_vision.
All external Gemini API calls have been removed in favor of the 100% offline
deterministic extraction engine (see app/ocr/ai_vision.py and header_mapper.py).
"""
from typing import Dict, Any, List, Optional
from . import ai_vision
from ..extraction import header_mapper


def _current_key() -> str:
    return "offline"


def _current_model() -> str:
    return "offline-deterministic"


def extract_bill_with_ai(image_path: str, prompt: Optional[str] = None) -> Dict[str, Any]:
    return ai_vision.extract_bill_with_ai(image_path, prompt=prompt)


def extract_purchase_bill_with_ai(image_path: str) -> Dict[str, Any]:
    return ai_vision.extract_purchase_bill_with_ai(image_path)


def map_excel_headers(grid: List[List[Any]]) -> Dict[str, Any]:
    return header_mapper.map_excel_headers_deterministic(grid)


def map_ledger_excel_headers(grid: List[List[Any]]) -> Dict[str, Any]:
    return header_mapper.map_ledger_excel_headers_deterministic(grid)
