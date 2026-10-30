"""Pharmacology Ontology Seeder and Gazetteer for MedFactory AI.

Handles MeSH (Medical Subject Headings) and RxNorm dictionary construction for
high-precision entity linking in the Pharmacology domain (drugs, contraindications, dosages).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ONTOLOGY_DIR = Path("ontologies")


# Curated starter pharmacology knowledge base: High-frequency drugs, classes, and contraindication concepts
STARTER_PHARMACOLOGY_KB: list[dict[str, Any]] = [
    # Analgesics / Neuropathic Pain
    {
        "id": "RXNORM:187832",
        "name": "Pregabalin",
        "synonyms": ["Lyrica", "pregabalin"],
        "category": "Drug",
        "atc_code": "N02BF02",
        "mesh_id": "MESH:D000069583",
        "description": "Anticonvulsant and neuropathic pain agent; requires renal dose adjustment.",
    },
    {
        "id": "RXNORM:25480",
        "name": "Gabapentin",
        "synonyms": ["Neurontin", "Gralise", "gabapentin"],
        "category": "Drug",
        "atc_code": "N02BF01",
        "mesh_id": "MESH:D005680",
        "description": "GABA analog used for neuropathic pain and seizures.",
    },
    {
        "id": "RXNORM:284637",
        "name": "Duloxetine",
        "synonyms": ["Cymbalta", "duloxetine"],
        "category": "Drug",
        "atc_code": "N06AX21",
        "mesh_id": "MESH:D000069338",
        "description": "SNRI antidepressant and neuropathic pain agent; avoid in severe renal impairment (eGFR < 30).",
    },
    # Antidiabetics / Metformin & SGLT2i
    {
        "id": "RXNORM:6809",
        "name": "Metformin",
        "synonyms": ["Glucophage", "Fortamet", "metformin hydrochloride"],
        "category": "Drug",
        "atc_code": "A10BA02",
        "mesh_id": "MESH:D008687",
        "description": "Biguanide antidiabetic; contraindicated in severe renal impairment (eGFR < 30) due to lactic acidosis risk.",
    },
    {
        "id": "RXNORM:1373458",
        "name": "Empagliflozin",
        "synonyms": ["Jardiance", "empagliflozin"],
        "category": "Drug",
        "atc_code": "A10BK03",
        "mesh_id": "MESH:D000077209",
        "description": "SGLT2 inhibitor; reduces cardiovascular death and CKD progression.",
    },
    # Cardiovascular & Anticoagulants
    {
        "id": "RXNORM:1364430",
        "name": "Apixaban",
        "synonyms": ["Eliquis", "apixaban"],
        "category": "Drug",
        "atc_code": "B01AF02",
        "mesh_id": "MESH:D000069478",
        "description": "Direct factor Xa inhibitor anticoagulant.",
    },
    {
        "id": "RXNORM:1114195",
        "name": "Rivaroxaban",
        "synonyms": ["Xarelto", "rivaroxaban"],
        "category": "Drug",
        "atc_code": "B01AF01",
        "mesh_id": "MESH:D000069477",
        "description": "Direct factor Xa inhibitor; contraindicated if CrCl < 15 mL/min.",
    },
    {
        "id": "RXNORM:11289",
        "name": "Warfarin",
        "synonyms": ["Coumadin", "Jantoven", "warfarin sodium"],
        "category": "Drug",
        "atc_code": "B01AA03",
        "mesh_id": "MESH:D014859",
        "description": "Vitamin K antagonist anticoagulant; high risk of CYP2C9 interactions.",
    },
    # Disease & Organ Impairment Concepts (MeSH)
    {
        "id": "MESH:D051436",
        "name": "Renal Insufficiency, Chronic",
        "synonyms": [
            "Chronic Kidney Disease",
            "CKD",
            "Chronic Renal Failure",
            "ESRD",
            "renal impairment",
            "severe renal impairment",
            "renal failure",
            "kidney disease",
        ],
        "category": "Condition",
        "atc_code": None,
        "mesh_id": "MESH:D051436",
        "description": "Gradual loss of kidney function characterized by declining eGFR.",
    },
    {
        "id": "MESH:D000140",
        "name": "Acidosis, Lactic",
        "synonyms": ["Lactic Acidosis", "lactic acidosis", "risk of lactic acidosis"],
        "category": "AdverseEffect",
        "atc_code": None,
        "mesh_id": "MESH:D000140",
        "description": "Metabolic acidosis characterized by elevated blood lactate levels.",
    },
    {
        "id": "MESH:D000077273",
        "name": "Acute Kidney Injury",
        "synonyms": ["AKI", "Acute Renal Failure", "Acute Tubular Necrosis"],
        "category": "Condition",
        "atc_code": None,
        "mesh_id": "MESH:D000077273",
        "description": "Sudden decline in renal function.",
    },
    {
        "id": "MESH:D003924",
        "name": "Diabetes Mellitus, Type 2",
        "synonyms": ["Type 2 Diabetes", "T2DM", "Non-Insulin-Dependent Diabetes"],
        "category": "Condition",
        "atc_code": None,
        "mesh_id": "MESH:D003924",
        "description": "Metabolic disorder marked by hyperglycemia and insulin resistance.",
    },
    {
        "id": "MESH:D006333",
        "name": "Heart Failure",
        "synonyms": ["Congestive Heart Failure", "CHF", "HFrEF", "HFpEF"],
        "category": "Condition",
        "atc_code": None,
        "mesh_id": "MESH:D006333",
        "description": "Clinical syndrome caused by structural or functional cardiac abnormalities.",
    },
    {
        "id": "MESH:D006973",
        "name": "Hypertension",
        "synonyms": ["High Blood Pressure", "HTN", "Essential Hypertension"],
        "category": "Condition",
        "atc_code": None,
        "mesh_id": "MESH:D006973",
        "description": "Persistent elevation of systemic arterial blood pressure.",
    },
    {
        "id": "MESH:D009437",
        "name": "Neuralgia",
        "synonyms": ["Neuropathic Pain", "Nerve Pain", "Peripheral Neuropathy"],
        "category": "Condition",
        "atc_code": None,
        "mesh_id": "MESH:D009437",
        "description": "Pain caused by damage or disease affecting the somatosensory nervous system.",
    },
]


def seed_ontology_files(target_dir: Path | None = None) -> Path:
    out_dir = target_dir or ONTOLOGY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    pharma_file = out_dir / "pharma_starter_gazetteer.json"
    with open(pharma_file, "w", encoding="utf-8") as f:
        json.dump(STARTER_PHARMACOLOGY_KB, f, indent=2)

    print(f"Seeded {len(STARTER_PHARMACOLOGY_KB)} pharmacology concepts to {pharma_file}")
    return pharma_file


if __name__ == "__main__":
    seed_ontology_files()
