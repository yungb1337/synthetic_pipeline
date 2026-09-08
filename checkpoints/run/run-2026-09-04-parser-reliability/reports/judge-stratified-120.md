# Stratified LLM Judge Audit — 120-Document Benchmark

- Generated: `2026-09-07T17:44:49Z`
- Target sample: `120` documents (`24` per stratum across `S1..S5`)
- Evaluated: `120` documents (newly judged: `115`, cached: `5`)
- Judge Model: `gemini-3.5-flash-lite`
- Evaluation Harness: Multi-metric source-vs-DOM correspondence (`scripts/llm_judge.py`)

## 1. Executive Summary & Verdict Distribution

| Verdict | Count | Share | Description |
|---|---|---|---|
| **PASS** | `86` | 71.7% | Complete, high-fidelity DOM extraction with zero critical/major defects |
| **PASS_WITH_ISSUES** | `34` | 28.3% | High-fidelity extraction with minor structural / formatting nuances |
| **FAIL** | `0` | 0.0% | Major extraction defect / substantial content loss |
| **Total** | `120` | 100.0% | **Acceptance Rate (PASS + PASS_WITH_ISSUES): 100.0%** |

## 2. Multi-Metric Accuracy by Risk Stratum

| Stratum | Description | Docs | Completeness | Fidelity | Structure | Tables | References | Scans/OCR | Verdict (P / PWI / F) |
|---|---|---|---|---|---|---|---|---|---|
| `S1` | Dense Tables & Multi-Table Studies | `24` | `0.969` | `0.980` | `0.943` | `0.780` | `0.947` | `1.000` | 14 / 10 / 0 |
| `S2` | Multi-Column Layouts & Typography | `24` | `0.978` | `0.985` | `0.957` | `0.880` | `0.965` | `1.000` | 19 / 5 / 0 |
| `S3` | OCR / Scans & Legacy Literature | `24` | `0.978` | `0.984` | `0.956` | `0.706` | `0.962` | `1.000` | 15 / 9 / 0 |
| `S4` | Long Documents (>30 pages) & Guidelines | `24` | `0.983` | `0.986` | `0.965` | `0.642` | `0.973` | `1.000` | 21 / 3 / 0 |
| `S5` | Clinical Trial Reports & Structured Outcomes | `24` | `0.982` | `0.989` | `0.963` | `0.682` | `0.965` | `1.000` | 17 / 7 / 0 |
| **Overall** | **Full Stratified Corpus** | `120` | **`0.978`** | **`0.985`** | **`0.957`** | **`0.738`** | **`0.962`** | **`1.000`** | **86 / 34 / 0** |

## 3. Issues & Nuances Tally

- **Total Issues Identified:** `53`
  - Critical: `0`
  - Major: `0`
  - Minor: `53`

### Issues by Surface

| Surface | Total Issues | Critical | Major | Minor | Sample Feedback / Suggestion |
|---|---|---|---|---|---|
| `reference` | `3` | `0` | `0` | `3` | Verify reference extraction mapping to ensure distinct reference strings are cor... |
| `structure` | `19` | `0` | `0` | `19` | Improve multi-column reading order assembly.... |
| `table` | `24` | `0` | `0` | `24` | Refine table parsing boundary detection for complex multi-column grids.... |
| `text` | `7` | `0` | `0` | `7` | Verify symbol normalization for mathematical operators and inequality signs.... |

## 4. Per-Document Audit Log (Stratified Sample)

| # | Doc ID | PMC ID | Stratum | Verdict | Completeness | Fidelity | Structure | Tables | Refs | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `d-20f3a9f24da77e3f` | `PMC13358229` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 0.00 | 0.95 | The parsed DOM corresponds very closely to the source PDF across all sampled pages. Tex... |
| 2 | `d-4ce4b9c7b670a992` | `PMC13384139` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 0.00 | 0.98 | The parser accurately extracted text, headings, and references from the multi-column la... |
| 3 | `d-23277375c12ede26` | `PMC13358261` | `S1` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The document is parsed with high accuracy, preserving complex multi-column structures a... |
| 4 | `d-f976f08627e99710` | `PMC13288683` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.96 | The parser demonstrates exceptional fidelity and completeness on a complex academic rea... |
| 5 | `d-9f7d66e07ef562c3` | `PMC13289178` | `S1` | `PASS` | 0.98 | 0.99 | 0.98 | 1.00 | 0.95 | The automated parser successfully extracted text, figures, and table structures across ... |
| 6 | `d-2db83de84e3ddb15` | `PMC13239455` | `S1` | `PASS` | 0.98 | 0.98 | 0.95 | 1.00 | 0.95 | The parser demonstrates exceptional fidelity in recovering complex multi-column academi... |
| 7 | `d-6654eb95b4a3e958` | `PMC13250215` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 0.00 | 0.98 | The automated parser successfully extracted the text, titles, authors, and references f... |
| 8 | `d-4bd65c8c707b8f97` | `PMC13239533` | `S1` | `PASS` | 0.95 | 0.98 | 0.95 | 0.90 | 1.00 | The automated parser successfully extracted the text, layout, and structured tables fro... |
| 9 | `d-51e6bec48d3ddebe` | `PMC13202132` | `S1` | `PASS` | 0.95 | 0.96 | 0.95 | 1.00 | 0.95 | The parser successfully extracted the text, metadata, and structural elements of the mu... |
| 10 | `d-ef2a44cbf01d7063` | `PMC13223673` | `S1` | `PASS_WITH_ISSUES` | 0.95 | 0.96 | 0.95 | 0.90 | 0.95 | The parser extracts text, tables, and multi-column academic layout with high fidelity. ... |
| 11 | `d-1032d5500a6ed113` | `PMC13150882` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The document was successfully parsed with high fidelity and completeness. Standard mult... |
| 12 | `d-747472a36ea2ff61` | `PMC13158606` | `S1` | `PASS_WITH_ISSUES` | 0.98 | 0.98 | 0.95 | 0.95 | 0.95 | The automated parser successfully extracted text, structure, tables, and reference entr... |
| 13 | `d-e6794f331ef652a1` | `PMC12626327` | `S1` | `PASS_WITH_ISSUES` | 0.95 | 0.96 | 0.92 | 0.85 | 0.90 | The document is successfully parsed with high fidelity and completeness. Minor structur... |
| 14 | `d-298032cf9e06bd6c` | `PMC11095673` | `S1` | `PASS` | 0.98 | 0.99 | 0.98 | 0.95 | 0.98 | The DOM successfully captures the multi-column text, metadata, headings, references, an... |
| 15 | `d-edc8eac20e182fb0` | `PMC10699593` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The automated parser successfully extracted the text, metadata, and tabular data from t... |
| 16 | `d-a5e5abf2b9bb442d` | `PMC9333258` | `S1` | `PASS_WITH_ISSUES` | 0.95 | 0.98 | 0.92 | 0.90 | 0.95 | The parser successfully extracted text, section headers, and tabular data across multip... |
| 17 | `d-d37b3d97b90f8ce4` | `PMC8559957` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 0.00 | 0.95 | The PARSED DOM successfully extracted the text, metadata, and structural elements of th... |
| 18 | `d-a444502a23b3815c` | `PMC7108743` | `S1` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.95 | 0.90 | The automated parser successfully extracted complex multi-column layouts, structured he... |
| 19 | `d-8441a0d134f547d2` | `PMC6557496` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 0.96 | 0.98 | The parser demonstrates exceptional extraction quality across complex multi-column acad... |
| 20 | `d-ab4b1ccab153353b` | `PMC5784921` | `S1` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The DOM successfully captures the multi-column layout, complex academic text, and refer... |
| 21 | `d-69467b6b781ea449` | `PMC5862400` | `S1` | `PASS_WITH_ISSUES` | 0.95 | 0.95 | 0.90 | 0.85 | 0.90 | The parser successfully extracts the vast majority of the text, structure, and tabular ... |
| 22 | `d-d0d46d67427a16fe` | `PMC4907518` | `S1` | `PASS_WITH_ISSUES` | 0.95 | 0.95 | 0.90 | 0.90 | 0.90 | The parser successfully extracts the vast majority of the text, dense metadata, and com... |
| 23 | `d-e365d53cc3cf9a9a` | `PMC4912069` | `S1` | `PASS_WITH_ISSUES` | 0.95 | 0.98 | 0.92 | 0.85 | 0.90 | The document was successfully parsed with high fidelity across text blocks and structur... |
| 24 | `d-777a11ad3a36d215` | `PMC4128723` | `S1` | `PASS_WITH_ISSUES` | 0.95 | 0.98 | 0.92 | 0.90 | 0.95 | The document is parsed with high fidelity, successfully capturing the academic text, me... |
| 25 | `d-b91658b49d30e297` | `PMC13235863` | `S2` | `PASS` | 0.98 | 0.99 | 0.97 | 0.95 | 0.98 | The DOM successfully parses all the primary text, metadata, figures, and structural ele... |
| 26 | `d-bb0835ac5551dec7` | `PMC12892031` | `S2` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The parsed DOM demonstrates high fidelity and completeness compared to the source PDF. ... |
| 27 | `d-a1af8f0b772fdeb2` | `PMC12638520` | `S2` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The DOM successfully captures the text, headings, and structure of the source PDF. Tabl... |
| 28 | `d-8f13e13125720a2f` | `PMC12606923` | `S2` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 1.00 | The PARSED DOM matches the SOURCE PDF content with extremely high fidelity across all s... |
| 29 | `d-c9be82fef18ecc63` | `PMC13170890` | `S2` | `PASS` | 0.98 | 0.99 | 0.97 | 0.95 | 0.96 | The PARSED DOM matches the source PDF with high fidelity. Text content, headers, and st... |
| 30 | `d-ffae5e192586c330` | `PMC13121329` | `S2` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.95 | 0.90 | The parser successfully extracted the text, layout, and table structures across all sam... |
| 31 | `d-e546a005ed1bb895` | `PMC13167926` | `S2` | `PASS_WITH_ISSUES` | 0.98 | 0.97 | 0.95 | 0.96 | 0.95 | The parser successfully extracts the dense multi-column layout, complex medical tables,... |
| 32 | `d-bb645a78c07f4764` | `PMC13109983` | `S2` | `PASS` | 0.98 | 0.99 | 0.98 | 0.95 | 1.00 | The DOM shows extremely high fidelity and completeness compared to the source preview. ... |
| 33 | `d-6f68642623a4965e` | `PMC13233200` | `S2` | `PASS` | 0.99 | 0.99 | 0.98 | 1.00 | 0.95 | The automated parser successfully extracted text, structure, and tables from the PDF wi... |
| 34 | `d-0c77389834b669fb` | `PMC13338676` | `S2` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.98 | The DOM successfully captures the core text, structure, tables, and references of the s... |
| 35 | `d-945b372a20b84ee7` | `PMC13219989` | `S2` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The parser successfully extracted text, structure, and tables with high fidelity across... |
| 36 | `d-43c39c36311b9b33` | `PMC13171855` | `S2` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The parser successfully captured the document's content, maintaining high fidelity acro... |
| 37 | `d-0c44aed57e7326c4` | `PMC13278967` | `S2` | `PASS` | 0.95 | 0.95 | 0.90 | 0.95 | 1.00 | The parsed DOM accurately captures the text, headings, and tabular data from the source... |
| 38 | `d-58d2758034bd393c` | `PMC12945157` | `S2` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The automated parser successfully extracted text, structure, and tabular data from the ... |
| 39 | `d-9a3f89e46c2d261e` | `PMC13222256` | `S2` | `PASS` | 0.95 | 0.98 | 0.95 | 0.95 | 0.95 | The automated parser successfully extracted the complex medical study text, tables, and... |
| 40 | `d-dc622738bcd57d3a` | `PMC13404421` | `S2` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The DOM successfully captures all text, headings, and layout elements from the source P... |
| 41 | `d-3ed45cb7c80225c0` | `PMC12931137` | `S2` | `PASS_WITH_ISSUES` | 0.95 | 0.98 | 0.95 | 0.90 | 0.95 | The DOM successfully captures the multi-column layout, headings, and textual content of... |
| 42 | `d-ea0676d5cab5fbf6` | `PMC13392530` | `S2` | `PASS` | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | The PARSED DOM faithfully captures all text, headings, structure, and table contents fr... |
| 43 | `d-1d3b00990748d231` | `PMC12673442` | `S2` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The automated parser successfully extracted the text, metadata, and table structures wi... |
| 44 | `d-97d03866c385ec3a` | `PMC13014435` | `S2` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The automated parser successfully extracted all major sections, author affiliations, ru... |
| 45 | `d-730339a6f12064a3` | `PMC13257860` | `S2` | `PASS_WITH_ISSUES` | 0.98 | 0.98 | 0.95 | 1.00 | 0.95 | The automated parser successfully extracted the medical study text, preserving complex ... |
| 46 | `d-d849d7e1fc6ae6a5` | `PMC13055821` | `S2` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The DOM faithfully reproduces all text, headings, sections, and references from the sou... |
| 47 | `d-410c06ede77fb6c4` | `PMC12264332` | `S2` | `PASS_WITH_ISSUES` | 0.95 | 0.95 | 0.92 | 0.90 | 1.00 | The automated parser successfully extracted the dense medical article text, headings, a... |
| 48 | `d-54291d94837a9de5` | `PMC13178146` | `S2` | `PASS` | 0.98 | 0.98 | 0.95 | 0.95 | 0.95 | The parser successfully extracted the text, metadata, and structured tables from the mu... |
| 49 | `d-720fdc00ea191545` | `PMC13366240` | `S3` | `PASS_WITH_ISSUES` | 0.98 | 0.97 | 0.95 | 0.95 | 0.95 | The DOM successfully captures the document text, layout, and structured tables with hig... |
| 50 | `d-97b717427b6bf503` | `PMC13276589` | `S3` | `PASS_WITH_ISSUES` | 0.98 | 0.95 | 0.94 | 0.85 | 0.92 | The document parser successfully extracts the vast majority of text, structure, and tab... |
| 51 | `d-b70dc08bcdd1671b` | `PMC13427308` | `S3` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The parser successfully captures the entire source document content, including complex ... |
| 52 | `d-ed1e4205c7e09978` | `PMC13350664` | `S3` | `PASS` | 0.98 | 0.98 | 0.95 | 1.00 | 0.95 | The PARSED DOM demonstrates excellent fidelity and completeness across the sampled page... |
| 53 | `d-1db6e78f0476265f` | `PMC13034276` | `S3` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.98 | The automated parser successfully extracted text, structure, tables, and references fro... |
| 54 | `d-971fa4f56ea80868` | `PMC13141763` | `S3` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.95 | 0.98 | The automated parser successfully extracted complex multi-column text, medical guidelin... |
| 55 | `d-d7be340551eeb6bc` | `PMC13097674` | `S3` | `PASS_WITH_ISSUES` | 0.95 | 0.95 | 0.90 | 0.85 | 0.90 | The parser successfully extracts the dense multi-column medical guidelines, handling co... |
| 56 | `d-b79470abc9d5dd45` | `PMC13308672` | `S3` | `PASS` | 0.95 | 0.98 | 0.95 | 0.90 | 0.95 | The automated parser successfully extracted the medical guideline document with high fi... |
| 57 | `d-33899172657b3b42` | `PMC13182049` | `S3` | `PASS` | 0.95 | 0.98 | 0.95 | 0.90 | 0.95 | The automated parser successfully extracted the text, layout, and structured elements s... |
| 58 | `d-a20716ef76da2ddc` | `PMC13108442` | `S3` | `PASS_WITH_ISSUES` | 0.98 | 0.98 | 0.95 | 0.95 | 0.90 | The DOM successfully parses all complex multi-column layouts, tables, and dense academi... |
| 59 | `d-f01806f2ace0470c` | `PMC13282293` | `S3` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The PARSED DOM shows excellent correspondence with the SOURCE PDF text. All previewed s... |
| 60 | `d-46693f453db84eaf` | `PMC13391794` | `S3` | `PASS` | 0.98 | 0.99 | 0.97 | 0.96 | 0.95 | The automated parser successfully extracted the dense medical consensus document with h... |
| 61 | `d-8bc313f906baa1bb` | `PMC12832217` | `S3` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The DOM successfully extracts the text, headings, and structure across all sampled page... |
| 62 | `d-2b7814268644f616` | `PMC12913039` | `S3` | `PASS` | 0.98 | 0.98 | 0.95 | 1.00 | 1.00 | The DOM successfully extracted the content from the multi-column layout with high fidel... |
| 63 | `d-6a2618c675b40553` | `PMC12408990` | `S3` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The DOM accurately captures all text, headers, and metadata from the source PDF. No inf... |
| 64 | `d-29054f1779ed1b8a` | `PMC12158961` | `S3` | `PASS` | 0.98 | 0.99 | 0.95 | 0.00 | 0.95 | The automated parser successfully extracted the text, section headings, and author list... |
| 65 | `d-2235660c2369b630` | `PMC11955994` | `S3` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The automated parser successfully extracted all text, metadata, and structural elements... |
| 66 | `d-8b19694041452d8d` | `PMC10597714` | `S3` | `PASS_WITH_ISSUES` | 0.95 | 0.96 | 0.90 | 0.90 | 0.95 | The DOM successfully parses the multi-column medical review text, complex clinical scor... |
| 67 | `d-5d103c30bc65b555` | `PMC9979817` | `S3` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The document text is parsed with high fidelity, maintaining correct institutional affil... |
| 68 | `d-85b0215a5a07d02e` | `PMC11288196` | `S3` | `PASS` | 0.98 | 0.99 | 0.98 | 0.98 | 1.00 | The automated parser successfully extracted the text, structure, and tables with very h... |
| 69 | `d-e3afd3357975edf6` | `PMC11883620` | `S3` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The parser successfully captured all sampled pages with high fidelity, maintaining corr... |
| 70 | `d-3250818d91ca92fb` | `PMC9298162` | `S3` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The PARSED DOM faithfully captures all text, headings, structure, and metadata from the... |
| 71 | `d-b96cef0aa20a593b` | `PMC8323279` | `S3` | `PASS` | 0.98 | 0.99 | 0.95 | 0.00 | 1.00 | The document was parsed with high fidelity, accurately capturing the scientific text, a... |
| 72 | `d-afc2a97455d76530` | `PMC9710482` | `S3` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.90 | 0.95 | The parser successfully extracted the complex multi-author metadata, structured clinica... |
| 73 | `d-f844aa5fd9c37984` | `PMC13102093` | `S4` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The automated parser successfully extracted all text, figures, and metadata from the do... |
| 74 | `d-39767859a73858b6` | `PMC13045940` | `S4` | `PASS` | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | The PARSED DOM accurately captures all text, structure, tables, and references from the... |
| 75 | `d-f60c498b29f8e6d4` | `PMC12821447` | `S4` | `PASS` | 0.98 | 0.98 | 0.95 | 1.00 | 0.95 | The PARSED DOM demonstrates extremely high fidelity and completeness against the SOURCE... |
| 76 | `d-a5fcc75160ed5efd` | `PMC13431896` | `S4` | `PASS` | 0.98 | 0.99 | 0.95 | 0.00 | 1.00 | The DOM successfully captured the content, text, and structure of the source PDF. No si... |
| 77 | `d-c6592343d7af4241` | `PMC13275671` | `S4` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The DOM successfully captures all text, metadata, headings, and structure from the sour... |
| 78 | `d-acd4c8ad93dd45ce` | `PMC13278141` | `S4` | `PASS` | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | The PARSED DOM matches the SOURCE PDF text accurately across all sampled pages, maintai... |
| 79 | `d-1615b768595a8d29` | `PMC13261077` | `S4` | `PASS` | 0.98 | 0.98 | 0.95 | 0.00 | 0.95 | The PARSED DOM faithfully captures the text content, sections, and metadata from the SO... |
| 80 | `d-39a0440751030131` | `PMC13224223` | `S4` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The document was successfully parsed with high fidelity and completeness. All visible h... |
| 81 | `d-f6640f746094da45` | `PMC13426713` | `S4` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 1.00 | The document parser accurately captures text, table data (Table 1), and image captions ... |
| 82 | `d-fb1b014bc9ff127b` | `PMC12522516` | `S4` | `PASS` | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | The PARSED DOM matches the SOURCE PDF text accurately across all sampled pages. The lay... |
| 83 | `d-929b92abf461b1b6` | `PMC12497498` | `S4` | `PASS_WITH_ISSUES` | 0.98 | 0.95 | 0.92 | 0.90 | 1.00 | The parser successfully captures the entire text, metadata, and figures across the samp... |
| 84 | `d-2ca808a137f1fc7a` | `PMC12240162` | `S4` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The automated parser successfully extracted the text, metadata, and structured tables w... |
| 85 | `d-7ac38258aa73da5b` | `PMC12561375` | `S4` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The PARSED DOM successfully captures all sampled source content with high fidelity. Tex... |
| 86 | `d-165197429174e642` | `PMC12838556` | `S4` | `PASS` | 0.95 | 0.95 | 0.95 | 0.95 | 0.90 | The document was successfully parsed with high fidelity across text, complex multi-colu... |
| 87 | `d-2a11fe797be1013c` | `PMC11930524` | `S4` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The DOM faithfully reproduces all text, headings, figure captions, and references from ... |
| 88 | `d-241e2e117ff72fc9` | `PMC13162998` | `S4` | `PASS_WITH_ISSUES` | 0.95 | 0.98 | 0.95 | 0.90 | 0.95 | The automated parser successfully extracted the text, metadata, and structured elements... |
| 89 | `d-4f2e63420cf1a623` | `PMC13282008` | `S4` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The automated parser successfully extracted the text, metadata, headings, and tabular d... |
| 90 | `d-97ac6def3514a7ba` | `PMC12725579` | `S4` | `PASS_WITH_ISSUES` | 0.98 | 0.97 | 0.95 | 0.00 | 1.00 | The document is successfully parsed with high completeness and fidelity. Minor layout c... |
| 91 | `d-ca53f7c2b99302f7` | `PMC13371192` | `S4` | `PASS` | 0.98 | 0.98 | 0.95 | 0.95 | 0.95 | The parsed DOM accurately captures the text, tables, and overall structure of the sourc... |
| 92 | `d-8eca19d9d6158c25` | `PMC12707045` | `S4` | `PASS` | 0.98 | 0.98 | 0.95 | 0.95 | 1.00 | The automated parser successfully extracted the text, complex multi-column tables, and ... |
| 93 | `d-26e80123d7cee323` | `PMC12705802` | `S4` | `PASS` | 0.98 | 0.98 | 0.95 | 0.95 | 0.95 | The automated parser successfully extracted the text, metadata, and complex multi-colum... |
| 94 | `d-4ae3d0e9afca98c7` | `PMC13199094` | `S4` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The DOM successfully captures almost all text, layout structure, tables, and references... |
| 95 | `d-c6b378498400cfb7` | `PMC12992057` | `S4` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The automated parser successfully extracted text, figures, and tables from the multi-co... |
| 96 | `d-99413b2a94a2dc56` | `PMC12520129` | `S4` | `PASS` | 0.95 | 0.98 | 0.95 | 0.95 | 0.90 | The automated parser successfully extracted the text, tables, and structure of the docu... |
| 97 | `d-090c472e097ddff1` | `PMC13324859` | `S5` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The automated parser successfully extracted the text, metadata, and structural elements... |
| 98 | `d-aacfba180918a7b1` | `PMC13212515` | `S5` | `PASS` | 0.98 | 0.99 | 0.95 | 0.00 | 0.95 | The automated parser successfully extracted the text, metadata, and structural elements... |
| 99 | `d-4360c965c77ed108` | `PMC12034030` | `S5` | `PASS` | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | The parsed DOM accurately captures all text, headings, layout structure, and table elem... |
| 100 | `d-5d86970d4b7f879b` | `PMC13179839` | `S5` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The document parser accurately extracted the core text, tables, and scientific content ... |
| 101 | `d-6d8359031c98e93d` | `PMC12980909` | `S5` | `PASS` | 0.98 | 0.99 | 0.97 | 0.00 | 0.98 | The automated parser successfully extracted the text, section headings, and reference s... |
| 102 | `d-3228c4803e6f1004` | `PMC12951640` | `S5` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.98 | The DOM successfully captures the text, headings, complex tables, and figures from the ... |
| 103 | `d-c194fde6a271afb9` | `PMC13204019` | `S5` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.96 | 0.95 | The parser successfully captures multi-column layouts, metadata, tables, and complex me... |
| 104 | `d-f90cb286ee0a4918` | `PMC12992767` | `S5` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The automated parser successfully extracted all text, structural headings, and bibliogr... |
| 105 | `d-8fb8d751e9640df5` | `PMC13237279` | `S5` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 1.00 | The DOM shows extremely high fidelity and completeness compared to the source PDF. Docu... |
| 106 | `d-6b2a9a886d75cad9` | `PMC13275025` | `S5` | `PASS` | 0.98 | 0.99 | 0.98 | 1.00 | 0.98 | The PARSED DOM matches the SOURCE PDF text with high fidelity across all sampled pages.... |
| 107 | `d-adb767407decae73` | `PMC12546231` | `S5` | `PASS_WITH_ISSUES` | 0.98 | 0.97 | 0.95 | 0.92 | 0.95 | The document parser successfully captured nearly all textual content, layout sections, ... |
| 108 | `d-61566973c9f21039` | `PMC13289446` | `S5` | `PASS` | 0.98 | 0.99 | 0.95 | 1.00 | 0.95 | The automated parser successfully extracted the text, structured sections, and complex ... |
| 109 | `d-72ce20c042271860` | `PMC13141516` | `S5` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.95 | 0.95 | The parser successfully extracts the academic text, tables, and structured layout with ... |
| 110 | `d-c2af528aa64421d8` | `PMC13334201` | `S5` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 1.00 | The parser successfully extracted text, structure, and complex tabular data across all ... |
| 111 | `d-c5016352d93d71a8` | `PMC12732550` | `S5` | `PASS` | 1.00 | 0.99 | 1.00 | 0.00 | 1.00 | The automated parser successfully extracted all text blocks, headings, author lists, an... |
| 112 | `d-1406cf01ce601541` | `PMC12629836` | `S5` | `PASS_WITH_ISSUES` | 0.98 | 0.98 | 0.95 | 0.00 | 0.90 | The parser successfully extracted text, section headings, and figure captions across al... |
| 113 | `d-59f3d2c8e3ba1be5` | `PMC12205544` | `S5` | `PASS` | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | The automated parser successfully extracted all textual content, figure captions, and s... |
| 114 | `d-aac507568831897e` | `PMC12384842` | `S5` | `PASS` | 0.98 | 0.98 | 0.95 | 0.95 | 1.00 | The DOM successfully captures the document text, tables, and references with high fidel... |
| 115 | `d-47dfd943560f641b` | `PMC13399433` | `S5` | `PASS` | 0.98 | 0.99 | 0.97 | 0.95 | 0.98 | The automated parser successfully extracted the text, metadata, and complex tables with... |
| 116 | `d-216569a8181659b2` | `PMC13349848` | `S5` | `PASS` | 0.98 | 0.99 | 0.95 | 0.95 | 0.98 | The automated document parser accurately extracted the structural elements, text conten... |
| 117 | `d-01e3f03d79f6cd59` | `PMC13373206` | `S5` | `PASS_WITH_ISSUES` | 0.95 | 0.98 | 0.95 | 0.95 | 0.90 | The document parser accurately extracts the scientific article's main text, complex sec... |
| 118 | `d-ca62026bc241a805` | `PMC13398365` | `S5` | `PASS_WITH_ISSUES` | 0.98 | 0.99 | 0.95 | 0.00 | 0.90 | The document is successfully parsed with high fidelity and completeness for the main te... |
| 119 | `d-e216c52725844d5a` | `PMC13364315` | `S5` | `PASS` | 0.95 | 0.98 | 0.95 | 0.95 | 0.90 | The DOM successfully parses all the text blocks, structured headers, and complex scient... |
| 120 | `d-759954e9bdd86f83` | `PMC13224676` | `S5` | `PASS` | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | The DOM successfully captures all text, metadata, tables, and structural elements acros... |