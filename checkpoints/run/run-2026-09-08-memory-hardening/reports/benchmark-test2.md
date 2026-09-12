# Benchmark test2 — 2026-09-08T09:34:49Z

## Summary
- **Documents**: 2 issued · `2` OK · `0` failed · `0` dead · `0` unparsed
- **Throughput & Timing**:
  - Total wall time: `134.6s` (2.24 mins)
  - Total pages parsed: `40`
  - Mean time per doc: `37489.9 ms` (37.49s)
  - Mean time per page: `1874.5 ms` (1.87s)
- **Memory Telemetry**:
  - Peak memory per corpus (Total tree RSS): `1893 MB`
  - Peak worker memory (Max single-process RSS): `1866 MB`
  - Host RAM available: `4.50 GB` start -> `4.60 GB` end
  - Pagefile/Swap committed: `2.76 GB` start -> `2.76 GB` end
- **Extraction Yield**: `448` blocks · `14` tables · `0` references
- **Command**: `C:\Users\Asus\Downloads\projects\22_07\.venv\Scripts\python.exe C:\Users\Asus\Downloads\projects\22_07\scripts\parse_folder.py checkpoints\run\run-2026-09-04-parser-reliability\sources\pdf checkpoints\run\run-2026-09-08-memory-hardening\parsed-test2 --limit 2`

## Document Metrics
| file | assembly | pages(done/exp) | time(s) | ms/page | peak_worker(MB) | peak_tree(MB) | blocks | tables | refs | ro_full |
|------|----------|------------------|---------|---------|-----------------|---------------|--------|--------|------|---------|
| PMC10088797.pdf | ok | 17/17 | 16.56 | 974 | 1457 | 1484 | 237 | 7 | 0 | 249 |
| PMC10121009.pdf | ok | 23/23 | 58.42 | 2540 | 1748 | 1775 | 211 | 7 | 0 | 227 |

## Parser Log Tail
    OK   PMC10121009.pdf              pdf        pages=23  blocks=211  tables=7   route=docling
          timings: detect_ms=0.0ms  route_ms=1017.5ms  scan_ms=7.9ms  plan_ms=2.3ms  run_ms=57247.0ms  assemble_ms=144.9ms  total_ms=58420.8ms  total=58420.8ms
    
    parsed 2/2 documents -> store under C:\Users\Asus\Downloads\projects\22_07\checkpoints\run\run-2026-09-08-memory-hardening\parsed-test2
    [parse_folder] input : C:\Users\Asus\Downloads\projects\22_07\checkpoints\run\run-2026-09-04-parser-reliability\sources\pdf
    [parse_folder] output: C:\Users\Asus\Downloads\projects\22_07\checkpoints\run\run-2026-09-08-memory-hardening\parsed-test2
    [parse_folder] launching parser...
    