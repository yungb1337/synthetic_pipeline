# Judge Summary — Full Corpus (2026-09-14T18:23:30Z)

- Docs judged: **300** · model: `gemini-2.5-flash`
- Verdicts: PASS=`170`, FAIL=`20`, PASS_WITH_ISSUES=`110`

## Metrics means (0..1, across judged docs)

| metric | mean | n |
|--------|------|---|
| completeness | 0.948 | 300 |
| fidelity | 0.953 | 300 |
| structure | 0.912 | 300 |
| tables | 0.711 | 300 |
| references | 0.891 | 300 |
| scans_ocr | 0.990 | 300 |

## Issue tally

- By severity: critical=`44`, major=`46`, minor=`202`
- By surface:  completeness=`7`, fidelity=`3`, reference=`23`, structure=`127`, table=`91`, tables=`3`, text=`38`

## Critical/Major examples (FAIL docs)

- **[critical / completeness]** Significant sections of page 1 content (authors and affiliations, the 'To cite' block, and the 'Prepublication history' block) are entirely missing from the DOM's sample blocks for Source Preview Page
- **[critical / completeness]** Large portions of the previewed document (Source Preview Pages 4, 5, 6, 7, 8, corresponding to actual pages 7, 9, 11, 13, 15) have no corresponding blocks in the provided `sample_blocks`. This implies
- **[critical / tables]** Table 2 ('A network meta-analysis...') is shown as having 0 rows (`0r x 34c`) in the `tables_preview`, indicating a critical failure to parse its data rows and internal structure, despite some header 
- **[critical / reference]** The reference list clearly visible on Source Preview Page 8 (actual page 15) is entirely missing from the DOM; `references_total` is reported as 0 and `sample_references` is empty.
- **[critical / structure]** The parser incorrectly maps content from the source PDF pages to the DOM's logical pages, leading to a severe page content misattribution. For instance, the running header 'Page 3 of 12' (which is on 
- **[critical / reference]** The parser failed to extract any references, reporting `references_total: 0`. However, source page 8 clearly contains a substantial list of numbered academic references.
- **[critical / text]** A substantial block of text, starting with 'METHODS Study Design Three bench studies were conducted...', is present on DOM page 2 but is entirely fabricated and does not exist in the source PDF on any
- **[critical / structure]** Key front-matter sections on source page 1, including the author list, 'Purpose:', 'Setting:', 'Design:', and 'Methods:', are either missing from the DOM or severely misrepresented. The 'Purpose' sect