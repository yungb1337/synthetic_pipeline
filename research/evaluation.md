# Synthetic Data Post-Generation Lifecycle & 12-Dimensional Evaluation Framework

This document defines the post-generation curation lifecycle and the exhaustive 12-dimensional evaluation framework required to certify synthetic datasets for enterprise and healthcare fine-tuning.

---

## 1. Post-Generation Lifecycle Workflow

```
[ Raw Synthetic Candidate Pool ]
                │
                ▼
      [ 1. Symbolic Verification ] ───────── (Graph execution: Query -> Answer node match)
                │ (Pass)
                ▼
     [ 2. De-duplication & Diversity ] ──── (SimHash / ROUGE-L < 0.70 threshold)
                │ (Pass)
                ▼
       [ 3. Privacy & Leakage Gate ] ────── (Strict PII/PHI scrubbing + N-gram membership inference)
                │ (Pass)
                ▼
       [ 4. LLM-as-a-Judge Audit ] ──────── (Ragas Faithfulness & CoT Step-by-Step Verification)
                │ (Pass)
                ▼
     [ 5. Stratified Dataset Split ] ────── (Train 80% / Val 10% / Test 10% with zero document leakage)
                │
                ▼
[ Certified Gold Synthetic Corpus ]
```

### A. What Happens When a Generated Sample Fails?
1. **Symbolic Execution Failure (Answer Mismatch):** Rejected immediately. Logged to `failures/symbolic_mismatch.jsonl` with the sampled graph path to detect extraction errors in the underlying KG.
2. **Shortcut / Leakage Failure (Intermediate Entity Leaked in Question):** Sent to prompt rewriting loop with strict masking instructions. If it fails a second time, dropped.
3. **Privacy Violation (PII / Memorization):** Permanently deleted; triggering source document flagged for redaction review.
4. **Regeneration Policy:** If a specific sub-domain or ontology branch experiences a high failure rate ($>30\%$), the generation policy halts batch synthesis and triggers a targeted extraction repair.

### B. Mixing Synthetic Data with Real Data
* **Empirical Best Practice (from research literature):** Optimal downstream model performance is achieved with a **curriculum mixing ratio**:
  * Phase 1 (Broad Domain Pre-training / Continual Pre-training): $70\%$ Real Data + $30\%$ Synthetic KG-reasoning data.
  * Phase 2 (Instruction / SFT Tuning): $40\%$ Real Data + $60\%$ High-Fidelity Synthetic CoT data.
  * Phase 3 (DPO / Alignment): $20\%$ Real Hard Negatives + $80\%$ Calibrated Synthetic Perturbation Pairs.
* **Evaluation Requirement:** Synthetic and real datasets must maintain strict document-level isolation (no document appearing in synthetic training may have fragments in the real evaluation test set).

---

## 2. Exhaustive 12-Dimensional Evaluation Framework

Every generated dataset is evaluated against 12 standardized, research-grounded dimensions:

```
                                  [ 12 Evaluation Dimensions ]
 ┌───────────────────────┬───────────────────────┬───────────────────────┬───────────────────────┐
 │ 1. Fidelity           │ 2. Utility            │ 3. Diversity          │ 4. Coverage           │
 ├───────────────────────┼───────────────────────┼───────────────────────┼───────────────────────┤
 │ 5. Correctness        │ 6. Consistency        │ 7. Privacy            │ 8. Memorization       │
 ├───────────────────────┼───────────────────────┼───────────────────────┼───────────────────────┤
 │ 9. Bias & Fairness    │ 10. Robustness        │ 11. Reproducibility   │ 12. Cost & Efficiency │
 └───────────────────────┴───────────────────────┴───────────────────────┴───────────────────────┘
```

### Detailed Metric Specifications

| Dimension | Definition | Established Research Methodology / Formula | Target Threshold |
| :--- | :--- | :--- | :--- |
| **1. Fidelity** | Preservation of factual semantic structure from source documents. | **Ragas Faithfulness Score**: Ratio of reasoning claims directly entailed by source DOM blocks. | $\ge 0.95$ |
| **2. Utility** | Downstream model accuracy improvement on target tasks (e.g. MedQA, USMLE). | **Downstream Task Delta ($\Delta$ Acc)**: Performance of model trained on synthetic data vs. baseline on gold benchmarks. | $\Delta \text{Acc} \ge +5.0\%$ |
| **3. Diversity** | Lexical, syntactic, and semantic variation across generated prompts. | **Self-BLEU & Vendi Score**: Mean Self-BLEU-4 across prompt embeddings; higher Vendi Score indicates higher diversity. | Self-BLEU $\le 0.35$; Vendi Score $> 15.0$ |
| **4. Coverage** | Proportion of KG nodes, relations, and document sections represented. | **Ontological Coverage**: $\frac{|\text{Edges in Synthetic QA}|}{|\text{Total Valid KG Edges}|} \times 100\%$. | $\ge 80\%$ of active TBox relations |
| **5. Correctness** | Factual truth of generated answers and reasoning steps. | **Graph Execution Match Rate**: SPARQL/Cypher query verification against ground truth KG. | $100\%$ exact execution match |
| **6. Consistency** | Logical alignment within reasoning chains (no self-contradictions). | **Internal Entailment (NLI)**: Zero contradictory claim pairs within the generated Chain-of-Thought. | Contradiction Rate $< 0.5\%$ |
| **7. Privacy** | Absence of Protected Health Information (PHI) or identifying markers. | **Presidio / De-ID NER Audit**: Automated PII detection scanning 100% of generated tokens. | **Zero** unmasked PHI entities |
| **8. Memorization & Leakage** | Generator reproducing verbatim training sentences without synthesis. | **Longest Common Subsequence (LCS) / N-gram Overlap**: Longest verbatim token sequence from source DOM. | Max verbatim span $< 15$ continuous tokens |
| **9. Bias & Fairness** | Uniform distribution across demographic, severity, and clinical subgroups. | **Demographic Parity / Disparate Impact Ratio**: Skewness across synthetic patient profiles. | Ratio $\in [0.80, 1.25]$ |
| **10. Robustness** | Resistance to perturbations, noisy prompts, and edge-case phrasing. | **Adversarial Paraphrase Stability**: Downstream model output consistency under input semantic paraphrasing. | Consistency $\ge 90\%$ |
| **11. Reproducibility** | Pipeline determinism given identical source hash and generation seed. | **Dataset Hash Stability**: Fixed random seed + temperature=0 yields bitwise identical symbolic graph paths. | $100\%$ deterministic graph paths |
| **12. Cost & Performance** | Computational and monetary cost per 1,000 verified synthetic samples. | **Cost per Valid Record**: Aggregate API token spend + GPU compute hours / yield count. | $\le \$0.015$ per verified reasoning pair |

---

## 3. Automated Evaluation Test Suite Implementation

The evaluation framework is executed as an automated test harness via pytest and dedicated evaluation runners:

```python
# Conceptual Test Harness Structure (app/evaluation/suite.py)
class SyntheticDatasetEvaluationSuite:
    def evaluate_dataset(
        self, dataset: list[SyntheticRecord], kg: KnowledgeGraph
    ) -> EvaluationReport:
        return EvaluationReport(
            fidelity=self.compute_ragas_faithfulness(dataset),
            symbolic_accuracy=self.verify_graph_execution(dataset, kg),
            diversity_self_bleu=self.compute_self_bleu(dataset),
            privacy_violations=self.scan_phi_leakage(dataset),
            memorization_max_lcs=self.compute_max_verbatim_lcs(dataset),
            ontological_coverage=self.compute_graph_coverage(dataset, kg),
        )
```
