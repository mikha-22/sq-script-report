# Monthly SonarQube Report ΓÇö Runbook

**Pipeline location:** `monthly_report/`
**Deliverables each month:**
1. **CISO report (.docx)** ΓÇö assembled from the outputs below, using the report template
2. **All-issues workbook (.xlsx)** ΓÇö generated from the issue CSVs via `utils/issues_csv_to_xlsx.py`

**Cadence:** once a month, on the 5th (covers the period from the previous snapshot +1 day through the 5th).

---

## 0. Prerequisites

```bash
export SONAR_URL="http://<server>:9000"   # base URL ONLY ΓÇö no /projects, no trailing path
export SONAR_TOKEN="<token>"
```

- Scripts 1ΓÇô3 are pure stdlib ΓÇö any Python 3.8+ runs them, no pip installs.
- `utils/issues_csv_to_xlsx.py` needs `openpyxl` (`pip install openpyxl`) ΓÇö run this one on your laptop, not the server.

All commands below run from inside `monthly_report/`. Everything lands in `results/YYYY_MM/`.

---

## 1. Fetch issues (Script 1) ΓÇö the long one

```bash
python3 1_fetch_issues.py --date 2026-08-05
```

- Takes a while (per-issue changelog lookups). Let it run.
- Output: `results/2026_08/open_issues_2026_08_05.csv`, `closed_issues_2026_08_05.csv`
- `--date` reconstructs the issue state *as of that date* from issue history. Omit it for a live snapshot of today.

## 2. Platform snapshot (Script 2) ΓÇö the one you must never skip

```bash
python3 2_platform_snapshot.py \
    --date 2026-08-05 \
    --baseline results/2026_07/platform_report_2026_07_05.csv
```

- Output: `results/2026_08/platform_report_2026_08_05.csv`
- `--baseline` = **last month's platform report** ΓÇö fills the LoC/duplication delta columns.
- ΓÜá∩╕Å Platform metrics are **current-state only** ΓÇö SonarQube has no LoC history. `--date` only labels the file and drives the 90-day Active/Inactive rule. If you skip a month, that month's numbers are gone forever. The report file doubles as next month's baseline, so **keep every month's CSV**.
- Console summary shows project counts, license usage vs the 1M LoC cap, QG pass/fail counts, and the list of failing projects.

## 3. Report tables (Script 3) ΓÇö feeds the docx

```bash
python3 3_report_metrics.py \
    --open   results/2026_08/open_issues_2026_08_05.csv \
    --closed results/2026_08/closed_issues_2026_08_05.csv \
    --start  2026-07-06 --end 2026-08-05 \
    | tee results/2026_08/report_tables_2026_08_05.txt
```

- `--start` = day after the previous snapshot; `--end` = this snapshot's date.
- Prints every issue-based table for the report, labeled by section: **2.1** (new/closed), **2.2** (resolutions), **3.1** (security + ratings), **3.1.1** (critical/high by rule), **3.1.2** (unreviewed hotspots), **3.2** (reliability), **3.3** (maintainability).
- Deltas `(+x)` are reconstructed from issue history ΓÇö no baseline file needed here.
- `tee` saves the tables to a file so you're not digging through scrollback.
- Watch for `WARNING: unmapped type/severity labels skipped` ΓÇö means the server produced a label the script doesn't know; don't ignore these silently.

## 4. Issues workbook (.xlsx) ΓÇö on your laptop

```bash
python3 utils/issues_csv_to_xlsx.py \
    --open   results/2026_08/open_issues_2026_08_05.csv \
    --closed results/2026_08/closed_issues_2026_08_05.csv \
    --output results/2026_08/all_issues_2026_08_05.xlsx
```

- Produces the 3-sheet workbook: open issues, closed issues, and the "Penjelasan Header" glossary tab (Indonesian column explanations with hover comments).
- This is the companion deliverable to the report docx.

## 5. Assemble the report (.docx)

Manual step ΓÇö copy into the CISO report template:

| Report section | Source |
|---|---|
| 1.5 QG/QP config | Script 2 CSV (`quality_gate`, `quality_profile` columns) |
| Project status / last scan | Script 2 CSV (`status`, `last_analysis`) |
| LoC + license usage | Script 2 summary + CSV (`ncloc`, `ncloc_delta`) |
| 2.1 New / closed issues | Script 3 output |
| 2.2 Resolutions | Script 3 output |
| 3.1 / 3.1.1 / 3.1.2 Security | Script 3 output |
| 3.2 Reliability | Script 3 output |
| 3.3 Maintainability + duplication | Script 3 output + Script 2 CSV (`duplicated_lines_density`, `dup_delta`) |

---

## Gotchas (learned the hard way)

- **SONAR_URL must be the base URL.** `http://host:9000/projects` ΓåÆ every API call 404s into an HTML error page and the JSON parser dies with `Expecting value: line 1 column 1`.
- **A timeout looks like "0 projects"** in older runs ΓÇö the current Script 2 refuses to write a CSV when the project list comes back empty. If you see that FATAL, it's network/VPN, not the server being empty.
- **Don't break the baseline chain.** Each month's `platform_report_*.csv` is next month's `--baseline`. Lost month = `N/A` deltas for one cycle, then it self-heals.
- **Keep all monthly CSVs** ΓÇö they're the audit trail behind every number in the docx.
- **Token stays out of git.** `token-BNIS-SQ.txt` (or a future `.env`) never gets committed.

## Future: server deployment

The target server (Rocky 8, no Python) needs PyInstaller-built binaries, compiled **on Rocky 8** (or a `rockylinux:8` container) so glibc matches. Scripts 1ΓÇô3 are stdlib-only, so the build is straightforward. The xlsx converter stays laptop-only (openpyxl). Not done yet ΓÇö validate on the real server first.

