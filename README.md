# MicroStrategy Cloud Migration Toolkit

**One-admin, zero-consultants, AI-accelerated migration** from MicroStrategy on-prem to MicroStrategy Cloud (CMC cluster).

Version: **2.1** &nbsp;|&nbsp; Updated: **April 2026** &nbsp;|&nbsp; Owner: **Pranay** (pranay136@gmail.com)

---

## What's in this toolkit

A complete, end-to-end migration automation suite built around four phases:

| Phase | Goal | Primary Scripts |
|-------|------|-----------------|
| **0. Usage Audit** *(NEW in v2.1)* | Classify reports: keep vs. retire | `MSTR-ReportAudit.ps1` + `Run-MSTRReportAudit.bat` |
| **1. Discovery** | Harvest on-prem metadata | `mstr_harvester.py` + `mstr_connectivity_tester.py` |
| **2. Migration** | Move everything to cloud | `mstr_package_migrator.py` + `mstr_db_connection_creator.py` + `mstr_user_migrator.py` |
| **3. Validation** | Verify cloud matches baseline | `mstr_validator.py` + `full_validation_runner.py` + `mstr_report_validator.py` |

---

## Quick start (run Phase 0 right now)

The new **Report Audit** script classifies every MSTR report into "migrate" vs. "decommission" based on whether it was executed in the last 365 days. It runs on Windows — no Python needed.

```cmd
:: 1. Open Run-MSTRReportAudit.bat in Notepad
:: 2. Edit these SET lines:
SET EM_SERVER=em-sqlserver.yourcompany.com
SET EM_DATABASE=MSTR_EM
SET EM_DB_TYPE=SqlServer
SET DAYS=365

:: 3. Save, close, and run from cmd:
Run-MSTRReportAudit.bat
```

Output lands in `.\report_audit_output\`:

- `reports_to_migrate.csv` — feed this into Phase 2
- `reports_to_decommission.csv` — send to business owners for sign-off
- `reports_audit_raw.csv` — full dataset for Excel pivots
- `reports_audit_summary.txt` — executive summary

Full details in [`MSTR-ReportAudit-README.txt`](./MSTR-ReportAudit-README.txt).

---

## File inventory

### Windows / PowerShell (no Python)

| File | Purpose |
|------|---------|
| `MSTR-ReportAudit.ps1` | Report usage classifier — SQL against Enterprise Manager DB, or MSTR REST API fallback |
| `Run-MSTRReportAudit.bat` | CMD wrapper for the report audit — edit the SET lines and double-click |
| `MSTR-ReportAudit-README.txt` | Detailed usage guide for the audit script |
| `build_acl_request.ps1` | Generates firewall/ACL opening requests for network teams |

### Python (cross-platform)

| File | Purpose |
|------|---------|
| `mstr_harvester.py` | Phase 1 — Extract all metadata (21 CSVs) via REST API |
| `mstr_connectivity_tester.py` | Phase 1/3 — Test ping + port connectivity from CMC to all DBs |
| `mstr_db_connection_creator.py` | Phase 2 — Recreate DB connections on cloud via REST API |
| `mstr_user_migrator.py` | Phase 2 — Recreate users, groups, memberships on cloud |
| `mstr_package_migrator.py` | Phase 2 — Orchestrated Command Manager package export/import |
| `mstr_cache_warmer.py` | Phase 3 — Pre-execute migrate-list reports on cloud to warm caches |
| `mstr_validator.py` | Phase 3 — Diff on-prem baseline vs cloud target, severity-classified |
| `mstr_report_validator.py` | Phase 3 — Report-level content validation (run + compare outputs) |
| `full_validation_runner.py` | Phase 3 — Orchestrates harvester + validator + connectivity tester |

### MicroStrategy Command Manager scripts

| File | Purpose |
|------|---------|
| `mstr_command_manager.scp` | Full LIST + EXPORT/IMPORT + post-migration operations |
| `extended_command_manager.scp` | Extended admin operations beyond the base script |
| `fetch_mstr_datasources.scp` | Command Manager dump of all datasource definitions |

### Documentation

| File | Purpose |
|------|---------|
| `README.md` | **This file** — entry point for the toolkit |
| `SKILL.md` | Full technical reference (REST API, schemas, AI prompts, workflows) |
| `README.txt` | Legacy plain-text README |
| `MIGRATION_QA.md` | Q&A log + FAQ from migration engagements |
| `MSTR_Migration_Playbook.docx` | Full written playbook (stakeholder-ready) |
| `AI_Discovery_Validation_Runbook.docx` | AI prompts runbook for discovery + validation |
| `MSTR_Migration_Automation_Deck.pptx` | Migration approach deck (dark theme) |
| `MSTR_Migration_Automation_Deck_BlueWhite.pptx` | Same deck, blue/white theme |
| `MSTR_Migration_Roles_Infographic.pptx` | One-slide RACI visual |
| `MSTR_Migration_RACI_v3.xlsx` | Detailed RACI matrix |
| `microstrategy_roles_responsibilities.xlsx` | Role definitions + responsibilities |
| `requirements.txt` | Python dependencies for the .py scripts |

### Backups (previous versions, preserved)

| File | Notes |
|------|-------|
| `README.md.bak` | Previous README (preserved during v2.1 update) |
| `SKILL.md.bak` | Previous SKILL.md (preserved during v2.1 update) |

---

## End-to-end workflow

```
[PHASE 0]  Usage Audit       -> Run-MSTRReportAudit.bat
           |                      produces reports_to_migrate.csv
           v
[PHASE 1]  Discovery         -> mstr_harvester.py (21 CSVs)
           |                    mstr_connectivity_tester.py
           v
[PHASE 2]  Migration         -> Command Manager EXPORT (migrate list only)
           |                    mstr_db_connection_creator.py
           |                    mstr_user_migrator.py
           |                    Command Manager IMPORT on cloud
           v
[PHASE 3]  Validation        -> full_validation_runner.py
                                mstr_validator.py (DIFF_REPORT.csv)
                                mstr_report_validator.py
                                -> sign-off + go-live
```

---

## How to run each phase

### Phase 0 — Usage Audit (Windows, no Python)

```cmd
Run-MSTRReportAudit.bat
```

Produces `reports_to_migrate.csv` (keep) and `reports_to_decommission.csv` (retire). See [`MSTR-ReportAudit-README.txt`](./MSTR-ReportAudit-README.txt) for SQL Server vs. Oracle config, REST API fallback mode, and schema notes.

### Phase 1 — Discovery

```bash
pip install -r requirements.txt

python mstr_harvester.py \
  --host https://mstr.company.com/MicroStrategyLibrary \
  --username Administrator \
  --password "xxx" \
  --output-dir ./discovery_output \
  --all-projects \
  --no-ssl-verify

python mstr_connectivity_tester.py \
  --odbc-file /etc/odbc.ini \
  --cmc-host cmc.cloud.mstr.com \
  --cmc-port 34952 \
  --output-dir ./connectivity_results
```

### Phase 2 — Migration

Run the Python migrators in order: DB connections → users/groups → package export → package import. See `SKILL.md` for the full Command Manager `.scp` workflow.

### Phase 3 — Validation

```bash
python full_validation_runner.py \
  --baseline-dir ./discovery_output \
  --cloud-host https://cloud-mstr.company.com/MicroStrategyLibrary \
  --cloud-user Administrator \
  --cloud-pass "xxx" \
  --cmc-host cmc.cloud.mstr.com \
  --cmc-port 34952 \
  --odbc-file /etc/odbc.ini \
  --output-dir ./full_validation
```

Produces `MASTER_VALIDATION_REPORT.txt` + `DIFF_REPORT.csv`. Feed the diff to your AI assistant with the prompts in `SKILL.md` for classified remediation steps.

---

## Requirements

**Python scripts:** Python 3.8+ with packages from `requirements.txt` (`requests`, `pyyaml`, `python-docx`).

**PowerShell scripts:** Windows PowerShell 5.1+ (built into Windows 10/11, Server 2016+). Zero external dependencies for SQL Server mode. Oracle mode needs `Oracle.ManagedDataAccess.dll` in the same folder.

**MSTR-side:** Access to the MicroStrategy REST API, Command Manager on the IS host, and (for Phase 0) network reach to the Enterprise Manager / Platform Analytics statistics DB.

---

## What's new in v2.1

- **Phase 0 added:** Usage-based report classification so you only migrate reports that are actually used.
- **`MSTR-ReportAudit.ps1`:** Windows-native PowerShell script — **no Python required**. Works from cmd via `Run-MSTRReportAudit.bat`.
- **Enterprise Manager schema reference** added to `SKILL.md` (LU_OBJECT / LU_PROJECT / IS_REPORT_STATS).
- **New AI prompt:** Usage Audit Interpretation for classifying decommission candidates.
- **Workflow update:** Phase 0 now feeds the migration scope into Phase 2, typically reducing package size by 40–70%.

---

## Support & references

- Full project context: [`SKILL.md`](./SKILL.md)
- Report audit details: [`MSTR-ReportAudit-README.txt`](./MSTR-ReportAudit-README.txt)
- Stakeholder playbook: `MSTR_Migration_Playbook.docx`
- AI runbook: `AI_Discovery_Validation_Runbook.docx`
- Q&A log: `MIGRATION_QA.md`

**Maintainer:** Pranay — pranay136@gmail.com
