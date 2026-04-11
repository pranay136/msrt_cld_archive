---
name: mstr-cloud-migration
description: >
  Full automation toolkit for MicroStrategy on-prem to Cloud (CMC cluster) migration.
  Covers Discovery (metadata harvesting via REST API), Migration (Command Manager packages
  + REST API scripts), and Validation (diff engine + connectivity tester + sign-off report).
  Use this skill as context whenever working on MSTR migration tasks, script generation,
  AI prompt execution, connectivity testing, or validation reporting.
version: "2.0"
author: Pranay (pranay136@gmail.com)
date: 2026-04-11
tags:
  - microstrategy
  - cloud-migration
  - cmc
  - rest-api
  - odbc
  - discovery
  - validation
  - automation
  - genai
scripts:
  - mstr_harvester.py
  - mstr_validator.py
  - mstr_connectivity_tester.py
  - full_validation_runner.py
  - mstr_command_manager.scp
outputs:
  - 21 CSVs per harvest run
  - SUMMARY_REPORT.txt
  - DIFF_REPORT.csv
  - VALIDATION_REPORT.txt
  - CONNECTIVITY_REPORT.txt
  - MASTER_VALIDATION_REPORT.txt
---

# MicroStrategy Cloud Migration — Project Skill Context
## Version 2.0 | April 2026 | Pranay (pranay136@gmail.com)

---

## PROJECT OVERVIEW

This project automates the full lift-and-shift of a MicroStrategy on-prem instance to
MicroStrategy Cloud (CMC cluster) across three phases:

| Phase | Goal | Key Output |
|-------|------|-----------|
| 1 — Discovery | Extract all metadata from on-prem IS | 21 CSVs + SUMMARY_REPORT.txt |
| 2 — Migration | Move content, users, config to cloud | Migrated cloud IS + DB connections |
| 3 — Validation | Verify cloud matches on-prem baseline | DIFF_REPORT.csv + VALIDATION_REPORT.txt |

**Philosophy:** One admin, zero consultants, fully automated, AI-accelerated.

---

## SCRIPTS IN THIS TOOLKIT

### `mstr_harvester.py`
**Purpose:** Phase 1 — Metadata Discovery  
**How it works:** Connects to on-prem (or cloud) MicroStrategy Intelligence Server via REST API v2.
Systematically harvests all metadata into CSV files.

**Usage:**
```bash
python mstr_harvester.py \
  --host https://YOUR-MSTR-SERVER/MicroStrategyLibrary \
  --username Administrator \
  --password YourPassword \
  --output-dir ./discovery_output \
  --all-projects
```

**Key flags:**
- `--all-projects` — harvest all projects (default: first 3)
- `--project-id ID` — single project only
- `--no-ssl-verify` — skip SSL cert check (self-signed)
- `--login-mode 16` — LDAP auth (1=Standard, 16=LDAP, 64=SAML)

**Output files (21 CSVs + 1 report):**
```
01_server_info.csv          Server version, OS, build, ports, cluster nodes
02_projects.csv             All projects: ID, name, status, owner
03_users.csv                All users: login, type (Standard/LDAP/SAML), enabled, email
04_usergroups.csv           All groups: ID, name, description, member count
05_group_membership.csv     Flat user → group mapping
06_security_roles.csv       Security roles + privilege counts
07_security_filters.csv     Security filter definitions per project
08_datasources.csv          All DB connections: host, port, DB name, driver, type
09_reports.csv              All reports per project: ID, name, path, owner
10_documents_dossiers.csv   All documents and dossiers
11_metrics.csv              All metric definitions
12_attributes.csv           All attribute definitions
13_facts.csv                All fact definitions
14_filters.csv              All filter definitions
15_prompts.csv              All prompt definitions
16_schedules.csv            All schedules: type, frequency, enabled, next run
17_subscriptions.csv        All subscriptions: owner, delivery type, recipients
18_caches.csv               Cache stats per project: count, size, hit rate
19_security_config.csv      Auth config: LDAP servers, trusted auth, SAML
20_email_config.csv         SMTP settings, from address, SSL/TLS
21_licenses.csv             License activations, product, expiry
SUMMARY_REPORT.txt          Human-readable full instance summary with risk flags
```

---

### `mstr_validator.py`
**Purpose:** Phase 3 — Post-Migration Diff & Validation  
**How it works:** Compares on-prem (baseline) and cloud (target) harvest directories
field-by-field. Produces a severity-classified diff report and a sign-off document.

**Usage:**
```bash
python mstr_validator.py \
  --baseline ./discovery_output \
  --target   ./cloud_discovery \
  --output-dir ./validation_results
```

**Output files:**
```
DIFF_REPORT.csv          Field-by-field: severity | status | domain | record | baseline | target | fix
VALIDATION_REPORT.txt    Pass/fail scorecard + sign-off block + remediation steps
```

**Severity classification:**
- CRITICAL — blocks go-live (missing security filters, DB connections, projects, disabled admin users)
- HIGH — degrades user experience (missing subscriptions, changed schedule states)
- MEDIUM — minor impact (metadata field changes, path differences)
- INFO — expected differences (object version, date fields)

---

### `mstr_command_manager.scp`
**Purpose:** Server-side discovery and package migration via MicroStrategy Command Manager  
**How it works:** MicroStrategy's built-in scripting tool (mstrcmd). Run on the IS host directly.

**Usage (Windows):**
```cmd
%MSTR_HOME%\bin\mstrcmd.exe -f mstr_command_manager.scp -n IS_HOSTNAME -u admin -p pass -o output.txt
```

**Usage (Linux):**
```bash
$MSTR_HOME/bin/mstrcmd -f mstr_command_manager.scp -n IS_HOSTNAME -u admin -p pass -o output.txt
```

**Key sections in the script:**
- Section 1: Connect to IS
- Sections 2–11: LIST ALL (projects, users, groups, objects, security, caches)
- Section 12: EXPORT project package (for migration)
- Section 13: IMPORT project package (on cloud IS)
- Section 14: Post-migration operations (LOAD, PURGE CACHES)
- Section 15: Validation queries (re-run all LIST commands on cloud)

---

### `mstr_connectivity_tester.py`
**Purpose:** Phase 1/3 — Test network connectivity from CMC to all on-prem/cloud DB connections  
**How it works:** Reads `odbc.ini`, parses all DSN entries, creates a CSV of all connections,
then tests ping (ICMP) and port-level curl connectivity to each host:port.

**Usage:**
```bash
python mstr_connectivity_tester.py \
  --odbc-file /etc/odbc.ini \
  --cmc-host YOUR-CMC-HOST \
  --cmc-port 34952 \
  --output-dir ./connectivity_results
```

**Output files:**
```
db_connections_inventory.csv   All parsed DSN entries with category/DB type
connectivity_results.csv       Ping + port test results per connection
CONNECTIVITY_REPORT.txt        Human-readable pass/fail summary
```

---

### `full_validation_runner.py`
**Purpose:** Phase 3 — Orchestrates all validation checks in one command  
**How it works:** Runs harvester on cloud IS, runs validator against on-prem baseline,
runs connectivity tester, then produces a combined MASTER_VALIDATION_REPORT.txt.

**Usage:**
```bash
python full_validation_runner.py \
  --baseline-dir ./discovery_output \
  --cloud-host https://CLOUD-MSTR/MicroStrategyLibrary \
  --cloud-user Administrator \
  --cloud-pass Password \
  --cmc-host CLOUD-CMC-HOST \
  --cmc-port 34952 \
  --odbc-file /etc/odbc.ini \
  --output-dir ./full_validation
```

---

## REST API REFERENCE

**Base URL:** `https://YOUR-SERVER/MicroStrategyLibrary/api`  
**Docs:** `https://demo.microstrategy.com/MicroStrategyLibrary/api-docs`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/login` | POST | Get auth token |
| `/auth/logout` | POST | End session |
| `/status` | GET | Server health/version |
| `/iServer/info` | GET | IS version, build, port |
| `/iServer/nodes` | GET | Cluster nodes |
| `/projects` | GET | List all projects |
| `/users?limit=500` | GET | List all users (paginated) |
| `/users/{id}` | GET | User detail |
| `/usergroups` | GET | List all groups |
| `/usergroups/{id}` | GET | Group with members |
| `/securityRoles` | GET | Security roles |
| `/datasources` | GET | All DB connections |
| `/datasources/{id}/testConnection` | POST | Test DB connectivity |
| `/objects?type=3` | GET | List objects by type (per project) |
| `/schedules` | GET | All schedules |
| `/subscriptions` | GET | All subscriptions (per project) |
| `/ldap` | GET | LDAP configuration |
| `/emailSettings` | GET | SMTP configuration |
| `/license` | GET | License info |

**MSTR Object Type Codes:**
```
1  = Filter          8  = Prompt          39 = Document
3  = Report          11 = Subtotal        55 = Dossier
4  = Metric          12 = Transformation
5  = Attribute       14 = Consolidation
6  = Fact            15 = Custom Group
7  = Hierarchy       21 = Project
```

**Authentication Header:** `X-MSTR-AuthToken: {token}`  
**Project Context Header:** `X-MSTR-ProjectID: {projectId}`  
**Login Modes:** 1=Standard, 4=Kerberos, 8=Database, 16=LDAP, 64=SAML

---

## AI PROMPT LIBRARY

### Discovery Risk Analysis
Feed: `SUMMARY_REPORT.txt`
```
You are a senior MicroStrategy cloud migration architect. Review this discovery report and provide:
1) Top 10 migration risks ranked by severity (Critical/High/Medium) with explanation.
2) Recommended migration order for projects (easiest to hardest).
3) Deprecated or unsupported features needing redesign in cloud.
4) Estimated migration effort per project (Small/Medium/Large).
5) Pre-migration actions required before touching the cloud environment.
Output as structured tables.
[PASTE SUMMARY_REPORT.txt]
```

### User Analysis
Feed: `03_users.csv`
```
Review this MicroStrategy user export. Provide:
1) Count by auth type (Standard/LDAP/SAML/Kerberos).
2) Users with no recent login (if dates available).
3) Admin-privileged users to review.
4) Recommendations: migrate / deactivate / special handling.
[PASTE 03_users.csv]
```

### DB Connection Mapping
Feed: `08_datasources.csv`
```
Review this DB connection inventory. For each connection:
1) Flag any DB types/drivers unsupported in MicroStrategy Cloud.
2) Identify connections needing cloud equivalents.
3) Generate mapping table: Name | DB Type | Host | Cloud Action | Notes.
[PASTE 08_datasources.csv]
```

### Validation Diff Interpretation
Feed: `DIFF_REPORT.csv`
```
Review this MicroStrategy migration validation diff. For each difference:
1) Classify as Critical/High/Medium/Info.
2) Explain business impact if not fixed.
3) Provide exact remediation: REST API call, Command Manager command, or admin action.
Start with an executive summary and go/no-go recommendation.
[PASTE DIFF_REPORT.csv]
```

### REST API Script Generation
Feed: `08_datasources.csv`
```
Generate Python code using MicroStrategy REST API v2 to recreate all datasource connections
listed in this CSV on a new cloud MicroStrategy instance.
Use POST /api/datasources. Include auth, error handling, logging, and dry-run mode.
[PASTE 08_datasources.csv]
```

### Error Log Triage
Feed: Migration error log
```
Review this MicroStrategy migration error log. For each error:
1) Explain the root cause in plain English.
2) Classify as Blocking or Non-Blocking.
3) Provide the exact fix (Command Manager / REST API / config change).
4) Estimate time to fix.
Format as a table: Error | Cause | Blocking? | Fix | Time to Fix.
[PASTE ERROR LOG]
```

### Sign-Off Report Generation
Feed: `VALIDATION_REPORT.txt`
```
Generate a professional migration sign-off report for a MicroStrategy cloud migration.
Include: Executive Summary, Validation Scorecard table, Open Issues table with severity,
Migration Statistics, and a formal sign-off section with signature blocks.
[PASTE VALIDATION_REPORT.txt]
```

---

## ODBC.INI DSN STRUCTURE

Standard `odbc.ini` DSN entry format:
```ini
[DSN_NAME]
Driver   = /path/to/driver.so  OR  ODBC Driver 17 for SQL Server
Server   = db-host.company.com
Host     = db-host.company.com  (alias for Server)
Port     = 1433
Database = dbname
DBName   = dbname               (alias for Database)
UID      = username
PWD      = password
```

**DB Type Detection from Driver String:**
```
"SQL Server" / "MSSQL"  → Microsoft SQL Server (port 1433)
"Oracle"                 → Oracle DB (port 1521)
"MySQL" / "MariaDB"     → MySQL/MariaDB (port 3306)
"PostgreSQL" / "Postgre" → PostgreSQL (port 5432)
"Teradata"              → Teradata (port 1025)
"Snowflake"             → Snowflake (port 443)
"Redshift"              → Amazon Redshift (port 5439)
"BigQuery"              → Google BigQuery (port 443)
"Hive"                  → Apache Hive (port 10000)
"Spark"                 → Apache Spark (port 10001)
"DB2"                   → IBM DB2 (port 50000)
"Sybase"                → Sybase (port 5000)
```

---

## MICROSTRATEGY NATIVE TOOLS REFERENCE

| Tool | Location | Best For |
|------|----------|---------|
| Command Manager | `%MSTR_HOME%\bin\mstrcmd.exe` | Bulk listing, package export/import |
| Object Manager | MSTR Developer → Tools | Content package migration |
| Integrity Manager | MSTR Developer → Tools | Report output comparison |
| Enterprise Manager | MSTR Web → EM Project | Usage analytics, report ranking |
| Architect | MSTR Developer → Schema | Schema/data model review |
| Workbench | CMC Admin Portal | Cloud-side migration wizard |
| System Manager | `%MSTR_HOME%\bin\MSTRSystemManager` | IS health check |
| Admin Portal (CMC) | `https://CMC-URL/admin` | Cloud IS configuration |

---

## MIGRATION WORKFLOW SUMMARY

```
DISCOVERY (Phase 1)
  └─ python mstr_harvester.py --host ON-PREM-URL --all-projects
  └─ python mstr_connectivity_tester.py --odbc-file odbc.ini --cmc-host CLOUD-HOST
  └─ Feed SUMMARY_REPORT.txt + connectivity results to AI
  └─ AI produces: risk matrix, migration order, effort estimate

MIGRATION (Phase 2)
  └─ Command Manager: EXPORT project packages
  └─ CMC Workbench: metadata DB migration
  └─ REST API scripts (AI-generated): recreate users, groups, DB connections
  └─ Command Manager: IMPORT project packages to cloud IS

VALIDATION (Phase 3)
  └─ python mstr_harvester.py --host CLOUD-URL --all-projects (saves to ./cloud_discovery)
  └─ python mstr_connectivity_tester.py --odbc-file odbc.ini --cmc-host CLOUD-HOST
  └─ python mstr_validator.py --baseline ./discovery_output --target ./cloud_discovery
  └─ python full_validation_runner.py (orchestrates all of the above)
  └─ Feed DIFF_REPORT.csv to AI → classified issues + remediation
  └─ AI generates VALIDATION_REPORT (sign-off document for end user)
```

---

## VALIDATION SCORECARD DOMAINS

| Domain | CSV File | Critical If |
|--------|---------|------------|
| Projects | 02_projects.csv | Any project missing |
| Users | 03_users.csv | Enabled users missing |
| Groups | 04_usergroups.csv | Groups missing |
| Group Memberships | 05_group_membership.csv | Membership wrong |
| Security Roles | 06_security_roles.csv | Roles missing |
| Security Filters | 07_security_filters.csv | Any filter missing (data exposure risk) |
| DB Connections | 08_datasources.csv | Any connection missing |
| Reports | 09_reports.csv | More than 5% missing |
| Documents/Dossiers | 10_documents_dossiers.csv | More than 5% missing |
| Metrics | 11_metrics.csv | High count missing |
| Attributes | 12_attributes.csv | High count missing |
| Schedules | 16_schedules.csv | Enabled schedules missing |
| Subscriptions | 17_subscriptions.csv | Active subscriptions missing |
| Auth Config | 19_security_config.csv | LDAP config changed |
| Email Config | 20_email_config.csv | SMTP host changed |

---

## CONNECTIVITY TEST REFERENCE

**ping test:** ICMP reachability from CMC host to DB host  
**curl test:** TCP port reachability (curl --connect-timeout 5 telnet://HOST:PORT)  
**CMC port:** 34952 (MicroStrategy Intelligence Server default)  
**Results:** REACHABLE / UNREACHABLE / TIMEOUT / DNS_FAIL

---

## ENVIRONMENT NOTES

- Python 3.8+ required
- Dependencies: `pip install requests pyyaml python-docx`
- ODBC file location: `/etc/odbc.ini` (Linux) or `C:\Windows\System32\odbc.ini` (Windows)
- All scripts are self-contained — copy the folder to any machine with Python 3.8+
- No MSTR client installation required for REST API scripts
- Command Manager scripts require MSTR server-side installation

---

## AUTHOR / PROJECT INFO

- **Admin:** Pranay (pranay136@gmail.com)
- **Toolkit Version:** 2.0
- **Date:** April 2026
- **Migration Type:** MicroStrategy on-prem → MicroStrategy Cloud (CMC cluster)
- **Tested Against:** MicroStrategy 2021 Update 7+, 2022, 2023

---

*This SKILL.md is the single source of truth for this project. Feed it to an AI assistant
at the start of any new session to restore full project context instantly.*
