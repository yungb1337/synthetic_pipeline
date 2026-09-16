"""Reuses the exact repository LLM Judge methodology to evaluate Unlimited-OCR DOMs vs Source PDFs.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.llm_judge import (
    DEFAULT_MODEL,
    FALLBACK_MODELS,
    PROMPT_TEMPLATE,
    _MAX_ATTEMPTS,
    _rate_limited,
    _retry_delay,
    extract_source_text,
    resolve_key,
    summarize_dom,
)

METRICS = ("completeness", "fidelity", "structure", "tables", "references", "scans_ocr")


class JudgeEvaluator:
    """Evaluates canonical DOMs against source PDFs using the existing Gemini LLM Judge."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_chars: int = 12000,
        pacing_seconds: float = 3.0,
    ):
        self.api_key = resolve_key(api_key)
        self.model_name = model
        self.max_chars = max_chars
        self.pacing_seconds = pacing_seconds

    def is_available(self) -> bool:
        return bool(self.api_key)

    def judge_dom(self, pdf_path: str | Path, dom_dict: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            return {
                "error": "No Gemini API key available (judge skipped)",
                "verdict": "SKIPPED",
                "metrics": {m: 0.0 for m in METRICS},
                "issues": [],
                "notes": "API key missing",
            }

        pdf = Path(pdf_path)
        if not pdf.is_file():
            return {
                "error": f"Source PDF not found: {pdf}",
                "verdict": "ERROR",
                "metrics": {m: 0.0 for m in METRICS},
                "issues": [],
                "notes": "Source PDF missing",
            }

        dom_sum = summarize_dom(dom_dict)
        src = extract_source_text(pdf, self.max_chars)

        src_budget = int(self.max_chars)
        src_parts: list[str] = []
        for idx in range(len(src["pages"])):
            chunk = src["pages"][idx][: src_budget // max(1, len(src["pages"]))]
            src_parts.append(f"[page {idx + 1} of {src['page_count']}] {chunk}")

        source_json = json.dumps(
            {
                "source_page_count": src.get("page_count") or len(src_parts),
                "preview_pages": [i + 1 for i in range(len(src_parts))],
                "pages": src_parts,
                "preview_note": "PARTIAL PREVIEW: only pages listed in preview_pages are shown, sampled across the document. Do NOT flag page-count differences as defects; judge fidelity/structure/tables/references ONLY on shown pages.",
            },
            ensure_ascii=False,
        )
        dom_json = json.dumps(dom_sum, ensure_ascii=False)
        prompt = PROMPT_TEMPLATE.format(source_json=source_json, dom_json=dom_json)

        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)

            candidate_models = [self.model_name]
            for fm in FALLBACK_MODELS:
                if fm not in candidate_models:
                    candidate_models.append(fm)

            resp = None
            used_model = self.model_name
            last_exc = None

            for current_model_name in candidate_models:
                model = genai.GenerativeModel(current_model_name)
                for attempt in range(1, _MAX_ATTEMPTS + 1):
                    try:
                        resp = model.generate_content(prompt)
                        used_model = current_model_name
                        break
                    except Exception as exc:
                        last_exc = exc
                        err_msg = str(exc)
                        if "PerDay" in err_msg or "per day" in err_msg.lower():
                            break
                        if not _rate_limited(exc):
                            break
                        delay = min(25.0, max(3.0, _retry_delay(exc)))
                        if attempt < _MAX_ATTEMPTS:
                            time.sleep(delay)
                if resp is not None:
                    break

        except Exception as exc:
            return {
                "error": f"Gemini call exception: {exc}",
                "verdict": "ERROR",
                "metrics": {m: 0.0 for m in METRICS},
                "issues": [],
                "notes": f"Gemini call failed: {exc}",
            }

        if resp is None:
            return {
                "error": f"Rate-limited after retries: {last_exc}",
                "verdict": "RATE_LIMITED",
                "metrics": {m: 0.0 for m in METRICS},
                "issues": [],
                "notes": "Rate limit exhausted",
            }

        text = resp.text.strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
            else:
                match_start = re.search(r"```(?:json)?\s*(\{.*)", text, re.DOTALL)
                if match_start:
                    text = match_start.group(1).rstrip("`")
        if not text.startswith("{"):
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                text = match.group(0)

        try:
            verdict_obj = json.loads(text)
        except json.JSONDecodeError as exc:
            return {
                "error": f"Judge returned non-JSON: {exc}",
                "raw_text": text[:500],
                "verdict": "ERROR",
                "metrics": {m: 0.0 for m in METRICS},
                "issues": [],
                "notes": "JSON decode failed",
            }

        if self.pacing_seconds > 0:
            time.sleep(self.pacing_seconds)

        return {
            "model": used_model,
            "judged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "verdict": verdict_obj.get("verdict", "UNKNOWN"),
            "metrics": verdict_obj.get("metrics", {}),
            "issues": verdict_obj.get("issues", []),
            "notes": verdict_obj.get("notes", ""),
            "dom_summary": dom_sum,
        }
