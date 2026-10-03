"""Tests for the LLM-judge side of the accuracy verification leg (scripts/llm_judge.py).

Guards the judge INPUT: `summarize_dom` compresses a real parser DOM into the
shape the model sees. These tests lock the behavior that was the root cause of
the b02 "tables metric 0.631 outlier": the model could not evaluate tables
because the summary only carried dims (`T1:6r x 3c`), never cell content, and
the per-page `enumerate` re-labeled tables across pages (T1 on every page).

They also pin the metric-determinants: a DOM with tables must produce a
non-empty `tables_preview` (>{no content} for the model to verify), empty-table
DOMs must produce an empty preview (model correctly scores "not evaluable"),
and cell text must be drawn from the real `rows[].cells[].text` fields.
"""

from __future__ import annotations

from scripts.llm_judge import summarize_dom


def _dom(tables: list, pages: int = 2) -> dict:
    """Minimal canonical-DOM shape the judge reads (metadata/pages/references)."""
    return {
        "metadata": {"title": "t", "page_count": pages, "detected_type": "paper"},
        "pages": [
            {
                "index": i,
                "blocks": [{"text": f"page {i} block"}] if i == 0 else [],
                "tables": ([tables[i]] if i < len(tables) else []),
                "images": [],
            }
            for i in range(pages)
        ],
        "references": [],
        "citation_index": {},
        "reading_order_full": [],
    }


def test_tables_preview_contains_real_cells():
    """A DOM with tables must expose header + cell text, not just dims."""
    dom = _dom(
        [
            {
                "header": ["A", "B"],
                "rows": [
                    {
                        "cells": [
                            {"text": "1", "bbox": None},
                            {"text": "2", "bbox": None},
                        ]
                    },
                    {
                        "cells": [
                            {"text": "3", "bbox": None},
                            {"text": "4", "bbox": None},
                        ]
                    },
                ],
            }
        ]
    )
    s = summarize_dom(dom)
    assert s["tables_total"] == 1
    assert len(s["tables_preview"]) == 1
    seg = s["tables_preview"][0]
    assert "A | B" in seg, "header cell text must be present for the model to verify"
    assert "1" in seg and "2" in seg, "data-row cell text must be present"


def test_tables_preview_empty_when_no_tables():
    """No tables -> empty preview; the model correctly marks 'not evaluable'."""
    dom = _dom([])
    s = summarize_dom(dom)
    assert s["tables_total"] == 0
    assert s["tables_preview"] == []


def test_tables_preview_global_index_across_pages():
    """Label tables T1.. by global sequence, not per-page (old bug re-labeled T1)."""
    dom = _dom(
        [
            {"header": ["h1"], "rows": [{"cells": [{"text": "a"}]}]},  # page 0
            {"header": ["h2"], "rows": [{"cells": [{"text": "b"}]}]},  # page 1
        ]
    )
    s = summarize_dom(dom)
    labels = [seg.split(":")[0] for seg in s["tables_preview"]]
    assert labels == ["T1", "T2"]


def test_tables_preview_character_budget():
    """Many/large tables are trimmed to the budget, not all shown."""
    rows = [{"cells": [{"text": "x" * 40}] for _ in range(4)} for _ in range(50)]
    dom = _dom([{"header": ["h"], "rows": rows}])
    s = summarize_dom(dom)
    total = sum(len(seg) for seg in s["tables_preview"])
    assert total <= 1000 + 40  # one final partial segment may exceed slightly


def test_summarize_dom_keeps_metric_determinants():
    """The numeric counters the judge derives metrics from stay present."""
    dom = _dom([{"header": ["h"], "rows": [{"cells": [{"text": "a"}]}]}])
    s = summarize_dom(dom)
    for key in (
        "blocks_total",
        "tables_total",
        "references_total",
        "citation_index_size",
        "reading_order_full_size",
        "sample_page1_blocks",
        "tables_preview",
    ):
        assert key in s, f"judge input lost {key}"


def test_tables_preview_note_warns_about_truncation():
    """The summary must tell the model that truncation is a preview limit, not a defect."""
    dom = _dom([{"header": ["h"], "rows": [{"cells": [{"text": "a"}]}]}])
    s = summarize_dom(dom)
    note = s.get("tables_preview_note", "")
    assert "PREVIEW LIMIT" in note, "judge must not penalize preview truncation"
    assert "truncated" in note
    assert "BOUNDED SAMPLE" in note


def test_cell_text_extraction_is_defensive_about_row_shape():
    """Document-cell rows are dicts; accept list-of-cell-dicts too without crashing."""
    dom = _dom(
        [
            {
                "header": ["x"],
                "rows": [
                    {"cells": [{"text": "alpha"}]},
                    [{"text": "beta"}, {"text": "gamma"}],  # alternate legal shape
                ],
            }
        ]
    )
    s = summarize_dom(dom)
    assert any("alpha" in seg for seg in s["tables_preview"])
    assert any("beta" in seg for seg in s["tables_preview"])
