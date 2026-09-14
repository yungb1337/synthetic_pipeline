# Benchmark comparison — pre-fix baseline vs post-fix runs

Baseline: `benchmark-b100.md`
Runs compared: `benchmark-postfixA.md`, `benchmark-postfixB.md`, `benchmark-postfix_group_b.md`

| metric | baseline (b100, pre-fix) | postfixA (post-fix) | postfixB (post-fix) | postfix_group_b (post-fix) |
|---|---|---|---|
| docs OK / issued | 100/100 | 100/100 | 100/100 | 100/100 |
| failed/dead/unparsed | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| wall time (min) | 47.36 | 28.32 | 26.89 | 27.33 |
| pages parsed | 1297 | 1297 | 1297 | 1297 |
| mean ms/page | 2191 | 1303 | 1238 | 1256 |
| pages/s (per-doc mean) | 0.4564 | 0.7677 | 0.808 | 0.7962 |
| pages/s (batch wall) | 0.4564 | 0.7633 | 0.8039 | 0.791 |
| peak tree RSS (MB) | 2600 | 3498 | 4476 | 3885 |
| peak worker RSS (MB) | 2573 | 3469 | 4464 | 3858 |
| blocks | 19231 | 19226 | 19226 | 19226 |
| tables | 317 | 306 | 306 | 306 |
| references | 61 | 61 | 61 | 61 |

## Deltas vs baseline

| metric | postfixA | postfixB | postfix_group_b |
|---|---|---|---|
| pages/s (per-doc mean) | +68.2% | +77.0% | +74.5% |
| pages/s (batch wall) | +67.2% | +76.1% | +73.3% |
| wall time | -40.2% | -43.2% | -42.3% |
| peak tree RSS | +34.5% | +72.2% | +49.4% |
| peak worker RSS | +34.8% | +73.5% | +49.9% |
