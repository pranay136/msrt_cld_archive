# MicroStrategy Cloud Migration Toolkit

> **AI-accelerated, fully automated lift-and-shift from MicroStrategy on-prem to MicroStrategy Cloud (CMC cluster)**  
> Designed for a solo admin — no consultants, no manual testers, automated validation and sign-off.

---

## What Problem Does This Solve?

Migrating a MicroStrategy environment from on-prem to cloud is traditionally a multi-week, multi-person effort involving:

- Discovery consultants manually inventorying objects
- Migration engineers hand-building runbooks
- QA teams manually validating every report and user
- Back-and-forth between admins and stakeholders for sign-off

**This toolkit eliminates all of that.** One admin with Python 3 and admin credentials can run a complete, documented, validated migration — and hand the end user an auto-generated sign-off report.

---

## The Three-Phase Approach

```
Phase 1 — DISCOVERY    →    Phase 2 — MIGRATION    →    Phase 3 — VALIDATION
─────────────────────────────────────────────────────────────────────────────
Run harvester scripts        Export packages              Re-harvest cloud IS
Get 21 CSV files             Import to cloud IS           Diff vs on-prem baseline
Feed to AI → risk matrix     Recreate users/groups        AI interprets diff
AI generates runbook         via REST API scripts         Auto sign-off report
```

---

## Efficiency Gains: Manual vs. Automated

> **TLDR:** What takes a team of 4–5 people **3–10 weeks** manually is reduced to **2–6 hours** with this toolkit. That is a **97–99% reduction in calendar time** and a **95–98% reduction in labour cost**.

### Task-by-Task Time Comparison

The table below uses three real-world instance sizes. All manual estimates reflect industry-standard consulting engagements for MicroStrategy migrations.

| Task | What's Done Manually | Manual Time | Automated Time | Tool Used |
|------|---------------------|-------------|----------------|-----------|
| **DISCOVERY** | | | | |
| Server & infrastructure inventory | Click through System Admin, screenshot every tab, type into a doc | 2–4 hrs | 30 sec | `mstr_harvester.py` |
| Project enumeration | Open each project, record name, status, warehouse DB | 1–2 hrs | 30 sec | `mstr_harvester.py` |
| User & group inventory | Click through every user in User Manager, document auth type, privileges, email | 4–16 hrs | 2–5 min | `mstr_harvester.py` |
| Security roles & filters | Open each role/filter, document privilege set and assignments | 2–6 hrs | 1–2 min | `mstr_harvester.py` |
| DB connection documentation | Open each DSN, manually record host, port, DB, driver | 1–4 hrs | 30 sec | `mstr_harvester.py` |
| Object inventory (reports, metrics, attributes…) | Browse folder tree, copy-paste object names and counts into spreadsheet | 16–80 hrs | 3–10 min | `mstr_harvester.py` |
| Schedule & subscription documentation | Open each schedule/subscription individually | 2–8 hrs | 1–2 min | `mstr_harvester.py` |
| LDAP / SAML / email config | Navigate 6+ admin panels, document each setting | 1–3 hrs | 30 sec | `mstr_harvester.py` |
| License documentation | Find and read license portal, match to object counts | 1–2 hrs | 30 sec | `mstr_harvester.py` |
| **Subtotal — Discovery** | | **30–125 hrs** | **~20 min** | |
| **MIGRATION PLANNING** | | | | |
| Risk analysis | Consultant reviews docs, writes risk matrix | 8–16 hrs | 5 min (AI) | Claude / ChatGPT |
| Migration runbook creation | Analyst writes 40–60 step runbook with dependencies | 8–24 hrs | 10 min (AI) | Claude / ChatGPT |
| DB connection mapping (on-prem → cloud) | Cross-reference each connection to cloud equivalent manually | 4–8 hrs | 5 min (AI) | Claude / ChatGPT |
| User cleanup analysis | Manually review each user record for stale/over-privileged accounts | 4–12 hrs | 5 min (AI) | Claude / ChatGPT |
| **Subtotal — Planning** | | **24–60 hrs** | **~25 min** | |
| **MIGRATION EXECUTION** | | | | |
| Recreate users in cloud IS | Create each user one-by-one in cloud admin UI | 4–20 hrs | 10–30 min | REST API script |
| Recreate groups & memberships | Manually assign each user to each group | 2–8 hrs | 5–15 min | REST API script |
| Recreate DB connections | Enter each connection in cloud IS admin | 1–4 hrs | 5–10 min | REST API script |
| Recreate schedules & subscriptions | Manually re-enter every schedule and subscription | 2–8 hrs | 10–20 min | REST API script |
| **Subtotal — Execution extras** | | **9–40 hrs** | **~1 hr** | |
| **VALIDATION** | | | | |
| Re-inventory cloud instance | Same manual effort as discovery — repeated entirely | 30–125 hrs | ~20 min | `mstr_harvester.py` |
| Compare cloud vs. on-prem | Manually diff two sets of spreadsheets, row by row | 20–60 hrs | 30 sec | `mstr_validator.py` |
| Connectivity testing (all DSNs) | Manually ping and telnet each host:port | 2–8 hrs | 5–15 min | `mstr_connectivity_tester.py` |
| Report execution smoke tests | Manually open and run each report in both environments | 8–24 hrs | 10–30 min | REST API smoke test |
| Issue triage and root cause | Senior engineer reads logs, maps errors to fixes | 8–24 hrs | 10 min (AI) | Claude / ChatGPT |
| Write validation report | QA lead manually writes pass/fail doc with sign-off block | 4–8 hrs | 5 min (AI) | Claude / ChatGPT |
| **Subtotal — Validation** | | **72–249 hrs** | **~1.5 hrs** | |
| **TOTAL** | | **135–474 hrs** | **~3.5–4 hrs** | |

---

### Summary by Instance Size

| Instance Size | Definition | Manual Effort | Team Required | Manual Calendar Time | Automated Time | **Time Saved** | **Efficiency Gain** |
|---------------|-----------|---------------|--------------|---------------------|----------------|----------------|---------------------|
| **Small** | 1–3 projects, ≤200 users, ≤1,000 reports | ~135 hrs | 2–3 people | 3–4 weeks | ~2 hrs | ~133 hrs | **98.5%** |
| **Medium** | 4–10 projects, ≤1,000 users, ≤5,000 reports | ~250 hrs | 3–4 people | 6–8 weeks | ~3.5 hrs | ~246.5 hrs | **98.6%** |
| **Large** | 11–30 projects, ≤5,000 users, ≤20,000 reports | ~474 hrs | 5–6 people | 12–16 weeks | ~6 hrs | ~468 hrs | **98.7%** |

---

### Cost Savings Estimate

Based on typical MicroStrategy consulting and QA rates (USD):

| Role | Rate | Manual Hours (Medium Instance) | Manual Cost |
|------|------|-------------------------------|-------------|
| Discovery Consultant | $175/hr | 60 hrs | $10,500 |
| Migration Engineer | $200/hr | 50 hrs | $10,000 |
| QA / Validation Analyst | $125/hr | 80 hrs | $10,000 |
| Technical Writer (reports/runbooks) | $100/hr | 30 hrs | $3,000 |
| Project Manager (coordination) | $150/hr | 30 hrs | $4,500 |
| **Total Manual Cost** | | **250 hrs** | **~$38,000** |

| Automated Approach | Rate | Automated Hours | Automated Cost |
|-------------------|------|-----------------|----------------|
| 1 Admin running scripts + AI | $120/hr | 3.5 hrs | **~$420** |
| **Total Savings** | | **246.5 hrs** | **~$37,580 (99%)** |

---

---

### Your Savings at $70/Hour Developer Rate

If your developer or admin costs **$70 per hour**, here is exactly what this toolkit saves you across each instance size:

| Instance Size | Manual Hours | Manual Cost @ $70/hr | Automated Hours | Automated Cost @ $70/hr | **You Save** | **ROI** |
|---------------|-------------|----------------------|-----------------|------------------------|--------------|---------|
| Small | ~135 hrs | **$9,450** | ~2 hrs | $140 | **$9,310** | 66x |
| Medium | ~250 hrs | **$17,500** | ~3.5 hrs | $245 | **$17,255** | 71x |
| Large | ~474 hrs | **$33,180** | ~6 hrs | $420 | **$32,760** | 78x |

> **For a medium-sized instance: you recover $17,255 in developer time on the very first migration you run with this toolkit.**

#### Breaking It Down by Phase (Medium Instance @ $70/hr)

| Phase | Manual Hours | Manual Cost | Automated Hours | Automated Cost | Saved |
|-------|-------------|-------------|-----------------|----------------|-------|
| Discovery | ~95 hrs | $6,650 | ~20 min | $23 | **$6,627** |
| Migration Planning | ~45 hrs | $3,150 | ~25 min | $29 | **$3,121** |
| Migration Execution extras | ~25 hrs | $1,750 | ~60 min | $70 | **$1,680** |
| Validation | ~85 hrs | $5,950 | ~90 min | $105 | **$5,845** |
| **Total** | **~250 hrs** | **$17,500** | **~3.5 hrs** | **$245** | **$17,255** |

#### What That $17,255 Saving Means in Real Terms

- **1 developer at $70/hr works 250 hours** — that is 6.25 full work weeks (assuming 40-hour weeks), or **1.5 months** of one person's time, just on discovery and validation paperwork.
- **With this toolkit, the same developer spends 3.5 hours** — freeing up 246.5 hours for actual engineering work.
- At $70/hr, those 246.5 recovered hours are worth **$17,255 in productive developer capacity** that can be redirected to other projects.
- If you run **3 migrations per year** (small, medium, large), your total annual saving at $70/hr is:

| Migrations Per Year | Total Manual Cost | Total Automated Cost | **Annual Saving** |
|--------------------|-------------------|----------------------|-------------------|
| 1 (medium) | $17,500 | $245 | **$17,255** |
| 3 (1 small + 1 medium + 1 large) | $60,130 | $805 | **$59,325** |
| 5 (mixed) | ~$87,500 | ~$1,225 | **~$86,275** |

> At $70/hr, running this toolkit across 5 migrations saves you the equivalent of a **full developer salary for the year**.

---

### What Gets Eliminated Entirely

| Traditionally Required | With This Toolkit |
|-----------------------|------------------|
| 1–2 Discovery Consultants | ❌ Not needed |
| 1 Migration Engineer for runbook creation | ❌ Not needed (AI generates it) |
| 1–2 QA Analysts for validation | ❌ Not needed (automated diff) |
| 1 Technical Writer for sign-off reports | ❌ Not needed (AI generates it) |
| 3–6 weeks of project calendar time | ✅ Reduced to 1–2 days |
| Manual validation spreadsheets | ✅ Replaced by `DIFF_REPORT.csv` |
| Stakeholder meetings for discovery sign-off | ✅ Replaced by `SUMMARY_REPORT.txt` → AI → one-pager |
| Back-and-forth for error triage | ✅ Replaced by AI error log analysis |

---

### Where the Time Actually Goes (Automated)

With this toolkit, the ~3.5 hours of admin time breaks down as:

| Activity | Time | Script |
|----------|------|--------|
| Run `mstr_harvester.py` on on-prem IS | 20 min | `mstr_harvester.py` |
| Feed `SUMMARY_REPORT.txt` to AI, review risk matrix | 15 min | Claude / ChatGPT |
| Run `mstr_connectivity_tester.py` | 10 min | `mstr_connectivity_tester.py` |
| Export packages from on-prem, import to cloud | 30–90 min | `mstr_package_migrator.py` |
| Bulk-create users, groups, memberships | 10–20 min | `mstr_user_migrator.py` |
| Recreate DB connections + IS-side connectivity test | 10–15 min | `mstr_db_connection_creator.py` |
| Bulk VLDB, schedules, security filters | 10–15 min | `extended_command_manager.scp` |
| Pre-warm top-50 report caches | 15–30 min | `mstr_cache_warmer.py` |
| Report/dossier execution validation (EBI-owned) | 20–45 min | `mstr_report_validator.py` |
| Run `full_validation_runner.py` on cloud IS | 30 min | `full_validation_runner.py` |
| Feed `DIFF_REPORT.csv` to AI, review issues | 15 min | Claude / ChatGPT |
| Deliver AI-generated sign-off report to end user | 10 min | Claude / ChatGPT |

The remaining effort is **human judgement** — reviewing what the scripts and AI surface, making go/no-go calls, and applying targeted fixes. The scripts eliminate all the mechanical, repetitive work.

---

## Quick Start

### Prerequisites

```bash
pip install requests python-docx pyyaml
```

Python 3.8+ required. No MicroStrategy client installation needed for REST API scripts.

---

### Phase 1 — Discovery (On-Prem)

```bash
python mstr_harvester.py \
  --host https://YOUR-ONPREM-MSTR/MicroStrategyLibrary \
  --username Administrator \
  --password YourPassword \
  --output-dir ./discovery_output \
  --all-projects
```

**Then test network connectivity from CMC to all DB sources:**

```bash
python mstr_connectivity_tester.py \
  --odbc-file /etc/odbc.ini \
  --cmc-host  cloud-mstr.company.com \
  --cmc-port  34952 \
  --output-dir ./connectivity_results
```

**Feed the output to AI (Claude / ChatGPT):**
```
Paste SUMMARY_REPORT.txt → get risk matrix, migration order, effort estimate
Paste 03_users.csv        → get user cleanup list and LDAP/SAML recommendations
Paste 08_datasources.csv  → get DB connection mapping for cloud
```

---

### Phase 2 — Migration

**Step 1 — Export/import project packages via REST API:**

```bash
python mstr_package_migrator.py \
    --source-host  https://ONPREM-MSTR/MicroStrategyLibrary \
    --source-user  Administrator --source-pass OnPremPass \
    --target-host  https://CLOUD-MSTR/MicroStrategyLibrary \
    --target-user  Administrator --target-pass CloudPass \
    --project-id   YOUR-PROJECT-GUID \
    --output-dir   ./migration_packages
```

**Step 2 — Recreate users, groups, and memberships:**

```bash
python mstr_user_migrator.py \
    --host          https://CLOUD-MSTR/MicroStrategyLibrary \
    --username      Administrator --password CloudPass \
    --harvest-dir   ./discovery_output \
    --temp-password "Temp@MigrPwd2026!" \
    --mode          full
```

**Step 3 — Recreate DB connections and test from IS:**

```bash
python mstr_db_connection_creator.py \
    --host      https://CLOUD-MSTR/MicroStrategyLibrary \
    --username  Administrator --password CloudPass \
    --odbc-file /etc/odbc.ini \
    --mode      create-and-test
```

**Step 4 — Bulk VLDB + schedules + security filters (Command Manager):**

```bash
# Edit #DEFINE values at top of script, then run:
mstrcmd.exe -f extended_command_manager.scp -n CLOUD-IS -u admin -p pass -o cm_output.txt
```

**Step 5 — Pre-warm caches before user go-live:**

```bash
python mstr_cache_warmer.py \
    --host        https://CLOUD-MSTR/MicroStrategyLibrary \
    --username    Administrator --password CloudPass \
    --reports-csv ./discovery_output/09_reports.csv \
    --top-n       50
```

---

### Phase 3 — Report & Dossier Execution Validation (EBI-owned)

This step runs actual report executions on the cloud IS and compares outputs against the on-prem baseline.
It is owned entirely by EBI — no business user involvement needed for non-prompted reports.

**Step 0 — Generate config template:**
```bash
python mstr_report_validator.py --init
# Edit the generated config.yaml: set source/target hosts, credentials, project name
```

**Step 1 — Capture on-prem baseline (before or during migration):**
```bash
python mstr_report_validator.py \
    --mode capture \
    --config config.yaml \
    --harvest-csv ./discovery_output/09_reports.csv
# Snapshots saved to: ./baseline/{report_id}.json
```

**Step 2 — Compare cloud vs baseline (after migration):**
```bash
python mstr_report_validator.py \
    --mode compare \
    --config config.yaml \
    --label post-migration
# Output: ./validation_reports/validation_YYYYMMDD.html (interactive dashboard)
#         ./validation_reports/validation_YYYYMMDD.csv  (for tickets/JIRA)
```

**Or run both sides simultaneously (full mode):**
```bash
python mstr_report_validator.py \
    --mode full \
    --config config.yaml \
    --harvest-csv ./discovery_output/09_reports.csv
```

**For MSTR upgrade testing (same workflow, reusable tool):**
```bash
# Before upgrade — capture baseline
python mstr_report_validator.py --mode capture --config config.yaml --label pre-upgrade-v12

# After upgrade — compare
python mstr_report_validator.py --mode compare --config config.yaml --label post-upgrade-v12
```

---

### Phase 3 — Validation (Metadata All-in-One)

```bash
python full_validation_runner.py \
  --baseline-dir  ./discovery_output \
  --cloud-host    https://CLOUD-MSTR/MicroStrategyLibrary \
  --cloud-user    Administrator \
  --cloud-pass    CloudPassword \
  --cmc-host      cloud-mstr.company.com \
  --cmc-port      34952 \
  --odbc-file     /etc/odbc.ini \
  --output-dir    ./full_validation
```

Or run steps individually:

```bash
# Step 1: Re-harvest cloud instance
python mstr_harvester.py --host https://CLOUD/MicroStrategyLibrary \
    --username admin --password pass --all-projects --output-dir ./cloud_harvest

# Step 2: Diff on-prem vs cloud
python mstr_validator.py \
    --baseline ./discovery_output --target ./cloud_harvest --output-dir ./diff_results
```

---

## Scripts in This Toolkit

### Execution Layers

All scripts use one of two MSTR-native remote execution layers — no shell access to the cloud IS required.

**Layer 1 — REST API** (run from any laptop with HTTPS access to the IS):

| Script | Phase | Purpose | Key Output |
|--------|-------|---------|-----------|
| `mstr_harvester.py` | 1 | Harvest all metadata from an IS | 21 CSVs + `SUMMARY_REPORT.txt` |
| `mstr_connectivity_tester.py` | 1/3 | Test ping + TCP to all DBs from odbc.ini | `connectivity_results.csv` |
| `mstr_package_migrator.py` | 2 | Export packages from source, import to cloud IS | `.mmp` packages + status log |
| `mstr_user_migrator.py` | 2 | Bulk-create users/groups/memberships from harvest CSVs | `user_migration_results.csv` |
| `mstr_db_connection_creator.py` | 2 | Create datasources on cloud IS; test FROM the IS | `db_connection_results.csv` |
| `mstr_cache_warmer.py` | 2/Pre-go-live | Pre-execute top-N reports to warm IS cache | `cache_warm_results.csv` |
| `mstr_report_validator.py` | 3/Upgrades | Execute reports/dossiers on both envs, compare output (rows, schema, data) | `validation_*.html` + `.csv` |
| `mstr_validator.py` | 3 | Diff on-prem baseline vs cloud harvest | `DIFF_REPORT.csv` + `VALIDATION_REPORT.txt` |
| `full_validation_runner.py` | 3 | Orchestrate all Phase 3 steps in one command | `MASTER_VALIDATION_REPORT.txt` |

**Layer 2 — Command Manager** (run as IS remote client; requires MSTR client tools):

| Script | Phase | Purpose | Key Output |
|--------|-------|---------|-----------|
| `mstr_command_manager.scp` | 1/2/3 | Discovery, package migration, post-migration ops | Text output, `.mmp` packages |
| `extended_command_manager.scp` | 2 | Bulk VLDB, schedules, users, security filters, caches | Text output + validation checklist |

---

## What the Harvester Collects

Running `mstr_harvester.py` against any MicroStrategy instance produces:

```
01_server_info.csv          Server version, OS, build, cluster nodes
02_projects.csv             All projects: name, ID, status, owner
03_users.csv                All users: login type (Standard/LDAP/SAML), enabled, email
04_usergroups.csv           All groups and descriptions
05_group_membership.csv     User → group membership mapping
06_security_roles.csv       Security roles and privilege counts
07_security_filters.csv     Security filter definitions (critical for data governance)
08_datasources.csv          All DB connections: host, port, database, driver, type
09_reports.csv              All reports per project
10_documents_dossiers.csv   All documents and dossiers
11_metrics.csv              All metric definitions
12_attributes.csv           All attribute definitions
13_facts.csv                All fact definitions
14_filters.csv              All filter definitions
15_prompts.csv              All prompt definitions
16_schedules.csv            All schedules: type, frequency, next run, enabled
17_subscriptions.csv        All subscriptions: owner, delivery type, recipients
18_caches.csv               Cache stats per project
19_security_config.csv      LDAP, SAML, trusted auth configuration
20_email_config.csv         SMTP settings
21_licenses.csv             License activations and expiry
SUMMARY_REPORT.txt          Full instance summary with risk flags (feed this to AI)
```

---

## Connectivity Tester — odbc.ini Support

`mstr_connectivity_tester.py` reads standard `odbc.ini` files and:

1. Parses every DSN entry (any DB type — SQL Server, Oracle, MySQL, Snowflake, Redshift, etc.)
2. Detects the DB type from the `Driver` string
3. Creates `db_connections_inventory.csv` with host, port, DB type, and category
4. Tests DNS resolution, ping (ICMP), and TCP port connectivity for every connection
5. Produces `connectivity_results.csv` and `CONNECTIVITY_REPORT.txt`

**Supported DB types detected automatically:**
SQL Server · Oracle · MySQL · MariaDB · PostgreSQL · Teradata · Snowflake · Redshift · BigQuery · Azure Synapse · Databricks · Apache Hive · Apache Spark · Apache Impala · IBM DB2 · Sybase · Vertica · Netezza · Greenplum · SAP HANA · Amazon Athena · Presto/Trino

---

## AI Acceleration — Key Prompts

These prompts unlock AI-powered analysis at every step. Copy-paste to Claude or ChatGPT:

**Discovery Risk Analysis** (feed `SUMMARY_REPORT.txt`):
```
You are a senior MicroStrategy cloud migration architect. Review this discovery report.
Identify the top 10 migration risks by severity, recommended project migration order,
deprecated features, and estimated effort per project. Output as structured tables.
```

**Validation Diff Interpretation** (feed `DIFF_REPORT.csv`):
```
Review this MicroStrategy migration diff report. Classify each issue as Critical/High/Medium/Info.
Explain the business impact and provide the exact remediation step for each issue.
Start with an executive summary and go/no-go recommendation.
```

**Sign-Off Report Generation** (feed `MASTER_VALIDATION_REPORT.txt`):
```
Generate a professional migration sign-off report. Include executive summary,
validation scorecard table, open issues with severity, and a formal sign-off section
with signature blocks for the admin and end user.
```

**REST API Script Generation** (feed `08_datasources.csv`):
```
Generate Python code using MicroStrategy REST API v2 to recreate all datasource connections
in this CSV on a new cloud instance. Use POST /api/datasources. Include error handling and logging.
```

---

## Authentication Modes

| Flag | Mode | Use When |
|------|------|---------|
| `--login-mode 1` | Standard | Default MSTR auth |
| `--login-mode 16` | LDAP | Active Directory integrated |
| `--login-mode 64` | SAML | SSO / Okta / ADFS |
| `--login-mode 4` | Kerberos | Kerberos-integrated environments |

---

## MicroStrategy Native Tools Referenced

| Tool | Where | Use For |
|------|-------|---------|
| Command Manager (`mstrcmd`) | `%MSTR_HOME%/bin/` | Bulk listing, package export/import |
| Object Manager | MSTR Developer → Tools | GUI-based content migration |
| Integrity Manager | MSTR Developer → Tools | Report output comparison (data validation) |
| Enterprise Manager | MSTR Web → EM Project | Usage analytics, report ranking |
| MicroStrategy Workbench | CMC Admin Portal | Cloud-side migration wizard |
| REST API | `/MicroStrategyLibrary/api` | Full automation of all phases |

---

## SKILL.md — AI Context File

`SKILL.md` is a machine-readable context file for this project. Load it at the start of any AI session to restore full project context:

```
[Paste SKILL.md into Claude or ChatGPT at the start of a new session]
Prompt: "This is the SKILL.md context file for a MicroStrategy cloud migration project.
Use it as your reference for all questions I ask during this session."
```

---

## Requirements

```
Python 3.8+
requests>=2.28.0
pyyaml>=6.0
python-docx>=0.8.11    (for DOCX report generation)

MicroStrategy Intelligence Server 2021 Update 7+ (REST API v2)
curl (standard on Linux/macOS; use Git Bash or WSL on Windows)
ping (standard on all platforms)
```

Install:
```bash
pip install -r requirements.txt
```

---

## Project Structure

```
mstr_harvester/
├── README.md                            ← You are here
├── SKILL.md                             ← AI context file — feed to Claude/ChatGPT at session start
├── requirements.txt                     ← Python dependencies
│
├── ── REST API SCRIPTS (run from laptop) ──────────────────────────────────────
├── mstr_harvester.py                    ← Phase 1: Harvest all IS metadata → 21 CSVs
├── mstr_connectivity_tester.py          ← Phase 1/3: Test odbc.ini connections (ping + TCP)
├── mstr_package_migrator.py             ← Phase 2: Export packages from source, import to cloud
├── mstr_user_migrator.py                ← Phase 2: Bulk-create users/groups from harvest CSVs
├── mstr_db_connection_creator.py        ← Phase 2: Create datasources on cloud IS; IS-side test
├── mstr_cache_warmer.py                 ← Phase 2: Pre-warm top-N report caches before go-live
├── mstr_validator.py                    ← Phase 3: Diff on-prem vs cloud → DIFF_REPORT.csv
├── full_validation_runner.py            ← Phase 3: Orchestrate all validation steps
│
├── ── COMMAND MANAGER SCRIPTS (IS remote client) ──────────────────────────────
├── mstr_command_manager.scp             ← Phase 1/2/3: Discovery, packages, post-migration ops
├── extended_command_manager.scp         ← Phase 2: Bulk VLDB, schedules, users, security filters
│
└── ── DOCUMENTATION ────────────────────────────────────────────────────────────
    ├── MSTR_Migration_Playbook.docx       ← Full admin guide (all 3 phases)
    └── AI_Discovery_Validation_Runbook.docx ← AI tools & prompt library
```

---

## Known Issues & Fixes

Real issues encountered during CMC migration validation. Check here before raising a support ticket.

---

### Issue 1 — Free-Form SQL Auto-Converts String Column to Date

**Symptom:** A VARCHAR column from the warehouse is rendered as a date inside a free-form SQL report on CMC.

**Why it happens:** MicroStrategy reads column type metadata directly from the JDBC/ODBC driver. If the driver reports the column as `DATE` or `TIMESTAMP`, MSTR will honour it regardless of what you intended.

**Fix A — CAST in SQL (most reliable):**
```sql
-- Force the driver to return VARCHAR metadata:
SELECT CAST(order_date AS VARCHAR(30)) AS order_date FROM orders      -- ANSI SQL
SELECT CONVERT(order_date, CHAR)       AS order_date FROM orders      -- MySQL / SingleStore
```

**Fix B — Override type in the report editor:**
Free-Form SQL report → column definition pane → change **Data Type** from `Date` → `VarChar`. Save and re-run.

**Fix C — VLDB property:**
```
Project Configuration → VLDB Properties → "Preserve column data type from query" → Disabled
```

**Fix D — JDBC URL parameter (MySQL / SingleStore):**
Add to connection URL in CMC datasource settings:
```
noDatetimeStringSync=true&zeroDateTimeBehavior=convertToNull
```

---

### Issue 2 — SingleStore JDBC Error Despite Telnet/Ping Passing

**Symptom:** `telnet HOST 3306` succeeds. Ping succeeds. Adding the datasource in CMC returns a JDBC error.

**Why it happens:** Telnet confirms TCP. JDBC errors are at the application layer — wrong JAR, wrong URL prefix, SSL mismatch, or wrong driver class name.

**Checklist:**

1. **JAR location** — copy the SingleStore JDBC JAR to the IS JDBC driver folder and restart IS:
   ```
   /opt/MicroStrategy/install/JDBC/drivers/   (Linux / CMC)
   ```
   Download: `https://github.com/memsql/singlestore-jdbc-client/releases`

2. **JDBC URL prefix:**
   ```
   jdbc:singlestore://HOST:3306/DATABASE      ← SingleStore driver v1.1.4+
   jdbc:mysql://HOST:3306/DATABASE            ← legacy MemSQL driver only
   ```

3. **Driver class:**
   | Driver | Class |
   |--------|-------|
   | SingleStore v1.1.4+ | `com.singlestore.jdbc.Driver` |
   | MemSQL / Legacy | `com.mysql.jdbc.Driver` |

4. **SSL** — SingleStore Cloud enforces SSL:
   ```
   ?sslMode=REQUIRED&serverSslCert=/path/to/cert.pem
   ```
   For internal testing: `?sslMode=DISABLED`

5. **Auth plugin** — add if authentication fails:
   ```
   ?authenticationPlugins=mysql_native_password
   ```

**Full working JDBC URL:**
```
jdbc:singlestore://your-host.svc:3306/your_db?sslMode=REQUIRED&allowMultiQueries=true&characterEncoding=UTF-8&authenticationPlugins=mysql_native_password
```

After any JDBC URL change, validate via IS-side test (fires FROM the IS — correct vantage point):
```
POST /api/datasources/{datasource_id}/testConnection
```
Use `mstr_db_connection_creator.py --mode test-existing` to automate this for all connections.

---

### Issue 3 — CMC on GKE: Gatekeeper Blocks Global Variable Passwords; GKE Secrets Not Wired

**Symptom:** CMC stores DB credentials as pod environment variables. OPA Gatekeeper policies prohibit this. No GKE Secrets integration in place.

**Solution A — External Secrets Operator + Google Secret Manager** *(recommended — no plaintext anywhere)*

```bash
# Store password in GSM:
echo -n "your-db-password" | gcloud secrets create mstr-db-password --data-file=- --project=YOUR_PROJECT

# Install ESO:
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets -n external-secrets --create-namespace
```

```yaml
# ExternalSecret — syncs GSM secret into a K8s Secret automatically:
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: mstr-db-credentials
  namespace: microstrategy
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: gcp-secret-store
    kind: SecretStore
  target:
    name: mstr-db-secret
    creationPolicy: Owner
  data:
    - secretKey: db_password
      remoteRef:
        key: mstr-db-password   # GSM secret name

# Mount in MSTR pod:
env:
  - name: MSTR_DB_PASSWORD
    valueFrom:
      secretKeyRef:
        name: mstr-db-secret
        key: db_password
```

**Solution B — Workload Identity + Secret Manager SDK** *(if Gatekeeper blocks all K8s Secrets)*

Bind the MSTR pod's service account to a GCP service account with Secret Manager access, then fetch the secret at startup in your init script — nothing ever stored in the cluster.

```python
from google.cloud import secretmanager
client = secretmanager.SecretManagerServiceClient()
secret = client.access_secret_version(
    name="projects/YOUR_PROJECT/secrets/mstr-db-password/versions/latest"
)
password = secret.payload.data.decode("UTF-8")
```

**Solution C — HashiCorp Vault sidecar** *(if your org already runs Vault)*

Annotate the MSTR pod — Vault Agent injects the secret as a tmpfs file at `/vault/secrets/`:
```yaml
annotations:
  vault.hashicorp.com/agent-inject: "true"
  vault.hashicorp.com/agent-inject-secret-db-password: "secret/mstr/db"
  vault.hashicorp.com/role: "mstr-role"
```

**Decision guide:**
| Situation | Use |
|-----------|-----|
| Gatekeeper allows K8s Secrets in MSTR namespace | Solution A (ESO + GSM) |
| Gatekeeper blocks ALL K8s Secrets | Solution B (Workload Identity) |
| Org already runs HashiCorp Vault | Solution C (Vault sidecar) |

---

## Author

- **Project:** MicroStrategy On-Prem → Cloud Migration Automation  
- **Admin:** Pranay (pranay136@gmail.com)  
- **Version:** 2.1  
- **Date:** April 2026  
- **Target:** MicroStrategy Cloud (CMC cluster)  
- **Approach:** REST API + Command Manager + GenAI acceleration  

---

*Built to eliminate the need for discovery consultants, manual testers, and validation meetings.  
One admin. Automated everything. Signed-off by AI-generated report.*
