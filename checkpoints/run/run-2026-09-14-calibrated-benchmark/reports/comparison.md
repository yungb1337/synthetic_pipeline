# Benchmark comparison — pre-fix baseline vs post-fix runs

Baseline: `benchmark-b100.md`
Runs compared: `benchmark-postfix_group_b.md`, `benchmark-calibrated.md`

| metric | baseline (b100, pre-fix) | postfix_group_b (post-fix) | calibrated (post-fix) |
|---|---|---|
| docs OK / issued | 100/100 | 100/100 | 100/100 |
| failed/dead/unparsed | 0/0/0 | 0/0/0 | 0/0/0 |
| wall time (min) | 47.36 | 27.33 | 7.99 |
| pages parsed | 1297 | 1297 | 1297 |
| mean ms/page | 2191 | 1256 | 367 |
| pages/s (per-doc mean) | 0.4564 | 0.7962 | 2.727 |
| pages/s (batch wall) | 0.4564 | 0.791 | 2.7066 |
| peak tree RSS (MB) | 2600 | 3885 | 3010 |
| peak worker RSS (MB) | 2573 | 3858 | 2983 |
| blocks | 19231 | 19226 | 25485 |
| tables | 317 | 306 | 268 |
| references | 61 | 61 | 9 |

## Deltas vs baseline

| metric | postfix_group_b | calibrated |
|---|---|---|
| pages/s (per-doc mean) | +74.5% | +497.5% |
| pages/s (batch wall) | +73.3% | +493.0% |
| wall time | -42.3% | -83.1% |
| peak tree RSS | +49.4% | +15.8% |
| peak worker RSS | +49.9% | +15.9% |
