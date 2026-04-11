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

Use the Command Manager script for package export/import:

```bash
# On-prem: export project package
mstrcmd.exe -f mstr_command_manager.scp -n ON-PREM-IS -u admin -p pass -o output.txt

# Cloud: import project package (edit the IMPORT section in the .scp file)
mstrcmd.exe -f mstr_command_manager.scp -n CLOUD-IS -u admin -p pass
```

Use REST API scripts (AI-generated from your CSVs) to recreate users, groups, and DB connections.

---

### Phase 3 — Validation (All-in-One)

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

| Script | Purpose | Key Output |
|--------|---------|-----------|
| `mstr_harvester.py` | Harvests all metadata from an IS via REST API | 21 CSVs + `SUMMARY_REPORT.txt` |
| `mstr_validator.py` | Diffs on-prem baseline vs cloud harvest | `DIFF_REPORT.csv` + `VALIDATION_REPORT.txt` |
| `mstr_connectivity_tester.py` | Reads odbc.ini, tests ping + TCP port to all DBs | `connectivity_results.csv` + `CONNECTIVITY_REPORT.txt` |
| `full_validation_runner.py` | Orchestrates all Phase 3 steps in one command | `MASTER_VALIDATION_REPORT.txt` |
| `mstr_command_manager.scp` | Command Manager scripts for server-side ops | Text output, package files |

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
├── README.md                          ← You are here
├── SKILL.md                           ← AI context file for this project
├── requirements.txt                   ← Python dependencies
├── mstr_harvester.py                  ← Phase 1: Metadata discovery
├── mstr_validator.py                  ← Phase 3: Metadata diff & validation
├── mstr_connectivity_tester.py        ← Phase 1/3: Network connectivity testing
├── full_validation_runner.py          ← Phase 3: Orchestrated validation runner
├── mstr_command_manager.scp           ← Server-side Command Manager scripts
├── MSTR_Migration_Playbook.docx       ← Full admin guide (all 3 phases)
└── AI_Discovery_Validation_Runbook.docx ← AI tools & prompt library
```

---

## Author

- **Project:** MicroStrategy On-Prem → Cloud Migration Automation  
- **Admin:** Pranay (pranay136@gmail.com)  
- **Version:** 2.0  
- **Date:** April 2026  
- **Target:** MicroStrategy Cloud (CMC cluster)  
- **Approach:** REST API + Command Manager + GenAI acceleration  

---

*Built to eliminate the need for discovery consultants, manual testers, and validation meetings.  
One admin. Automated everything. Signed-off by AI-generated report.*
