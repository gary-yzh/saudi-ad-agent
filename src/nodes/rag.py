"""RAG node — load the brand manual and surface constraints to downstream nodes.

For a take-home demo we keep retrieval intentionally simple:
- Read the brand manual from disk (markdown or PDF).
- Extract the bullet-point sections that describe constraints (do/don't lists).
- Pass them through the state so the Planner and Guardrail can read them.

In production you'd swap this for a vector store + chunked retriever; the
interface (`load_brand_constraints`) would not change.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ..state import AgentState

DEFAULT_BRAND_DOC = Path(__file__).resolve().parents[2] / "data" / "brand_manual.md"


def _read_doc(path: Path) -> str:
    """Read .md or .pdf brand manuals. PDF is optional (pypdf)."""
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError(
                "pypdf is required to read PDF brand manuals. "
                "Run `pip install -r requirements.txt`."
            ) from e
        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    return path.read_text(encoding="utf-8")


def _extract_constraints(doc: str) -> list[str]:
    """Pull bullet-point lines that look like rules/constraints.

    Heuristic: any markdown bullet ('-' or '*') that contains an imperative
    verb, prohibition word, or a hex colour. Good enough for a demo and
    deterministic across runs.
    """
    keywords = re.compile(
        r"\b(must|never|avoid|always|primary|secondary|respect|no |only|use|"
        r"do not|don't|hijab|modest|alcohol|pork|halal|ramadan|"
        r"prayer|adhan|sfda|gamr)\b",
        re.IGNORECASE,
    )
    hex_colour = re.compile(r"#[0-9a-fA-F]{6}")

    rules: list[str] = []
    for raw in doc.splitlines():
        line = raw.strip()
        if not (line.startswith("-") or line.startswith("*")):
            continue
        body = line.lstrip("-*").strip()
        if keywords.search(body) or hex_colour.search(body):
            rules.append(body)
    return rules


def rag_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: read the brand manual, attach constraints to state."""
    path = Path(state.get("brand_doc_path") or DEFAULT_BRAND_DOC)
    if not path.exists():
        return {
            "brand_constraints": [],
            "errors": state.get("errors", []) + [f"brand manual not found at {path}"],
            "log": state.get("log", []) + [{"node": "rag", "status": "missing", "path": str(path)}],
        }

    doc = _read_doc(path)
    rules = _extract_constraints(doc)
    return {
        "brand_constraints": rules,
        "brand_doc_path": str(path),
        "log": state.get("log", []) + [
            {"node": "rag", "status": "ok", "rules_loaded": len(rules), "path": str(path)}
        ],
    }
