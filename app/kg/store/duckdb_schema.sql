-- DuckDB Schema for MedFactory AI Knowledge Graph (Option 2 Normalized Schema)
-- Model: Entity <- Assertion -> Evidence[] (1:N provenance anchored to Canonical DOM)

-- 1. Entities: Canonical medical concepts linked to Ontologies (RxNorm, MeSH)
CREATE TABLE IF NOT EXISTS entities (
    id VARCHAR PRIMARY KEY,             -- e.g., 'RXNORM:187832', 'MESH:D051436'
    name VARCHAR NOT NULL,              -- Canonical clinical label (e.g., 'Pregabalin')
    category VARCHAR NOT NULL,          -- 'Drug', 'Condition', 'LabTest', 'Population', etc.
    aliases VARCHAR[],                  -- ['Lyrica', 'pregabalin']
    atc_code VARCHAR,                   -- 'N02BF02'
    mesh_id VARCHAR,                    -- 'MESH:D000069583'
    description VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for entity lookup
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_category ON entities(category);

-- 2. Qualified Assertions: Relational facts with full clinical qualifiers
CREATE TABLE IF NOT EXISTS assertions (
    id VARCHAR PRIMARY KEY,             -- Unique assertion hash / UUID
    subject_id VARCHAR NOT NULL,        -- FK to entities(id)
    predicate VARCHAR NOT NULL,         -- 'CONTRAINDICATED_IN', 'REQUIRES_DOSE_ADJUSTMENT', etc.
    object_id VARCHAR NOT NULL,         -- FK to entities(id)
    negation BOOLEAN DEFAULT FALSE,     -- True if assertion is negated (e.g. NOT recommended)
    hedging VARCHAR DEFAULT '',         -- 'may', 'investigational', 'probable'
    population VARCHAR DEFAULT '',      -- 'Stage 4 CKD (eGFR 15-29 mL/min)', 'Pediatric'
    dosage VARCHAR DEFAULT '',          -- '25 mg once daily', '< 30 mg/day'
    timing VARCHAR DEFAULT '',          -- 'pre-prandial', 'at bedtime'
    route VARCHAR DEFAULT '',           -- 'oral', 'intravenous'
    evidence_level VARCHAR DEFAULT '',  -- 'Level A (RCT)', 'Level C (Expert consensus)'
    guideline_source VARCHAR DEFAULT '',-- 'KDIGO 2024 CKD Guideline'
    confidence DOUBLE DEFAULT 1.0,      -- Extractor confidence [0.0 - 1.0]
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subject_id) REFERENCES entities(id),
    FOREIGN KEY (object_id) REFERENCES entities(id)
);

CREATE INDEX IF NOT EXISTS idx_assertions_subj ON assertions(subject_id);
CREATE INDEX IF NOT EXISTS idx_assertions_obj ON assertions(object_id);
CREATE INDEX IF NOT EXISTS idx_assertions_pred ON assertions(predicate);

-- 3. Evidence: Direct 1:N physical provenance anchored into Canonical DOM blocks
CREATE TABLE IF NOT EXISTS evidence (
    id VARCHAR PRIMARY KEY,             -- Unique evidence hash
    assertion_id VARCHAR NOT NULL,      -- FK to assertions(id)
    doc_id VARCHAR NOT NULL,            -- Source document hash/ID
    page_index INTEGER NOT NULL,        -- 0-indexed page in canonical DOM
    block_id VARCHAR NOT NULL,          -- Canonical DOM Block.id (or Table.id)
    bbox_x0 DOUBLE,                     -- Spatial bounding box
    bbox_y0 DOUBLE,
    bbox_x1 DOUBLE,
    bbox_y1 DOUBLE,
    verbatim_text VARCHAR NOT NULL,     -- Verbatim snippet from DOM block
    entailment_score DOUBLE,            -- DeBERTa-v3 NLI entailment score
    verified_by_nli BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (assertion_id) REFERENCES assertions(id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_assertion ON evidence(assertion_id);
CREATE INDEX IF NOT EXISTS idx_evidence_doc_block ON evidence(doc_id, block_id);
