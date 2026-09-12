# Benchmark b5 — 2026-09-08T09:27:31Z

## Summary
- **Documents**: 5 issued · `0` OK · `0` failed · `0` dead · `4` unparsed
- **Throughput & Timing**:
  - Total wall time: `24.5s` (0.41 mins)
  - Total pages parsed: `17`
  - Mean time per doc: `0.0 ms` (0.00s)
  - Mean time per page: `0.0 ms` (0.00s)
- **Memory Telemetry**:
  - Peak memory per corpus (Total tree RSS): `1594 MB`
  - Peak worker memory (Max single-process RSS): `1567 MB`
  - Host RAM available: `4.48 GB` start -> `4.47 GB` end
  - Pagefile/Swap committed: `2.75 GB` start -> `2.75 GB` end
- **Extraction Yield**: `0` blocks · `0` tables · `0` references
- **Command**: `C:\Users\Asus\Downloads\projects\22_07\.venv\Scripts\python.exe C:\Users\Asus\Downloads\projects\22_07\scripts\parse_folder.py checkpoints\run\run-2026-09-04-parser-reliability\sources\pdf checkpoints\run\run-2026-09-08-memory-hardening\parsed-5 --limit 5`

## Document Metrics
| file | assembly | pages(done/exp) | time(s) | ms/page | peak_worker(MB) | peak_tree(MB) | blocks | tables | refs | ro_full |
|------|----------|------------------|---------|---------|-----------------|---------------|--------|--------|------|---------|
| PMC10088797.pdf | pending | 17/17 | - | - | - | - | - | - | - | - |
| PMC10121009.pdf | UNPARSED | 0/? | - | - | - | - | - | - | - | - |
| PMC10133056.pdf | UNPARSED | 0/? | - | - | - | - | - | - | - | - |
| PMC10174557.pdf | UNPARSED | 0/? | - | - | - | - | - | - | - | - |
| PMC10288765.pdf | UNPARSED | 0/? | - | - | - | - | - | - | - | - |

## Parser Log Tail
    
    Loading weights:   0%|          | 0/770 [00:00<?, ?it/s]
    Loading weights:  96%|#########5| 737/770 [00:00<00:00, 7348.56it/s]
    Loading weights: 100%|##########| 770/770 [00:00<00:00, 7373.67it/s]
    [parse_folder] input : C:\Users\Asus\Downloads\projects\22_07\checkpoints\run\run-2026-09-04-parser-reliability\sources\pdf
    [parse_folder] output: C:\Users\Asus\Downloads\projects\22_07\checkpoints\run\run-2026-09-08-memory-hardening\parsed-5
    [parse_folder] launching parser...
    