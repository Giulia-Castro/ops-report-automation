# Prompt changelog

Published versions are never edited. A change means a new file (`v2.md`), a new entry here and
a comparison on the same days of logs.

## daily-report/v1

- **Goal:** turn the computed facts of the day into a summary a manager reads in one minute.
- **Design decisions:**
  - The model receives aggregated, masked facts only. It never sees raw log lines.
  - Health status (GREEN/YELLOW/RED) is decided by code and passed in. The model may not
    contradict it.
  - Log-derived text (`signature`) is declared as data, never instructions (prompt injection).
  - Output is constrained by a JSON Schema (structured outputs) and revalidated in the service
    (length, item count, masking).
- **Fallback:** if the call fails, is refused or returns invalid JSON, the service writes a
  rule-based summary and the e-mail still goes out (`summarySource = FALLBACK`).
- **How to evaluate the next version:** run both versions on the same sample days and compare
  (1) numbers quoted that are not in the facts (should be zero), (2) anomalies left out of
  `risks`, (3) cost and tokens per report.
