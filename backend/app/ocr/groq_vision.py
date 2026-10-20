"""
Offline backward-compatibility adapter for groq_vision.
All external LLM calls have been removed in favor of the 100% offline
deterministic extraction engine (see app/ocr/ai_vision.py and header_mapper.py).
"""
from typing import Dict, Any, List, Optional
from . import ai_vision
from ..extraction import header_mapper

# Preserved prompt constants for backward compatibility
SALES_EXTRACTION_PROMPT = "Offline deterministic sales extraction active"
EXTRACTION_PROMPT = SALES_EXTRACTION_PROMPT
PURCHASE_EXTRACTION_PROMPT = "Offline deterministic purchase extraction active"
HEADER_MAP_CANONICAL_FIELDS = ""
HEADER_MAP_PROMPT_TEMPLATE = ""
LEDGER_HEADER_MAP_CANONICAL_FIELDS = ""
LEDGER_HEADER_MAP_PROMPT_TEMPLATE = ""


def _current_key() -> str:
    return "offline"


def _current_model() -> str:
    return "offline-deterministic"


GROQ_API_KEY = "offline"


def _encode_image(path: str) -> str:
    return ""


def extract_bill_with_ai(image_path: str, prompt: Optional[str] = None) -> Dict[str, Any]:
    return ai_vision.extract_bill_with_ai(image_path, prompt=prompt)


def extract_purchase_bill_with_ai(image_path: str) -> Dict[str, Any]:
    return ai_vision.extract_purchase_bill_with_ai(image_path)


def map_excel_headers(grid: List[List[Any]]) -> Dict[str, Any]:
    return header_mapper.map_excel_headers_deterministic(grid)


def map_ledger_excel_headers(grid: List[List[Any]]) -> Dict[str, Any]:
    return header_mapper.map_ledger_excel_headers_deterministic(grid)