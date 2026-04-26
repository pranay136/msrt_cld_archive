# MicroStrategy Migration Toolkit — Windows / Citrix Edition
## Version 2.1 | No Python Required | PowerShell 5.1+

This folder contains the **Windows-native port** of the MicroStrategy migration automation toolkit. Every script runs on a Windows Citrix server with no Python, no pip installs, and no additional dependencies — just PowerShell, which is built into every modern Windows Server.

---

## What's Here

| File | Phase | Purpose |
|------|-------|---------|
| `Invoke-MSTRHarvester.ps1` | Phase 1 | Extracts all metadata from on-prem IS via REST API → 21 CSVs |
| `Invoke-MSTRConnectivityTester.ps1` | Phase 1/3 | Tests DNS + ping + TCP for all DB connections |
| `Invoke-MSTRValidator.ps1` | Phase 3 | Diffs on-prem baseline vs cloud, produces DIFF_REPORT |
| `Invoke-MSTRFullValidation.ps1` | Phase 3 | Orchestrates all three scripts in one command |
| `Run-Discovery.bat` | Phase 1 | Double-click launcher for discovery (edit config, run) |
| `Run-Validation.bat` | Phase 3 | Double-click launcher for validation (edit config, run) |

The Phase 0 scripts (`MSTR-ReportAudit.ps1`, `Run-MSTRReportAudit.bat`) already exist in the root folder and require no changes.

---

## Quick Start

### Step 1 — Edit the bat file

Open `Run-Discovery.bat` in Notepad. Change the CONFIG block at the top:

```bat
set MSTR_HOST=https://YOUR-ONPREM-SERVER/MicroStrategyLibrary
set MSTR_USER=Administrator
set MSTR_PASS=YourPassword
set CMC_HOST=your-cloud-mstr.microstrategy.com
```

### Step 2 — Run Discovery

Right-click `Run-Discovery.bat` → **Run as Administrator**.

This will:
1. Connect to your on-prem MSTR server via REST API
2. Extract all metadata into `.\discovery_output\` (21 CSVs + SUMMARY_REPORT.txt)
3. Test connectivity to your cloud CMC host and all DSN entries

### Step 3 — Analyse with AI

Paste `SUMMARY_REPORT.txt` into Claude with this prompt:

> *"Review this MicroStrategy discovery report. List the top 10 migration risks ranked by severity, the recommended project migration order (easiest to hardest), any deprecated features, and pre-migration actions required."*

### Step 4 — After Migration, Run Validation

Edit `Run-Validation.bat` with cloud IS credentials, then run it:

1. Re-harvests the cloud IS
2. Compares all 21 CSV files between on-prem and cloud
3. Re-tests DB connectivity
4. Produces `MASTER_VALIDATION_REPORT.txt` — your go/no-go sign-off document

---

## Requirements

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| PowerShell | 5.1 | Built into Windows Server 2016+ and Windows 10+ |
| TLS | 1.2 | Enabled by scripts automatically |
| Network access | TCP | Must reach MSTR Library port (usually 443 or 8080) |
| MSTR user | Administrator | Or any user with full IS admin privileges |
| MSTR version | 2021 Update 7+ | REST API v2 required |

No Python. No pip. No curl. No external libraries.

---

## Running Scripts Directly (Without Bat Files)

### Phase 1 Discovery

```powershell
# Harvest all metadata from on-prem IS
.\Invoke-MSTRHarvester.ps1 `
    -Host     "https://onprem-mstr.company.com/MicroStrategyLibrary" `
    -Username "Administrator" `
    -Password "YourPassword" `
    -OutputDir ".\discovery_output" `
    -AllProjects

# Test DB + CMC connectivity
.\Invoke-MSTRConnectivityTester.ps1 `
    -CMCHost   "cloud-mstr.company.com" `
    -CMCPort   34952 `
    -OutputDir ".\connectivity_results"
```

### Phase 3 Validation

```powershell
# Full validation (harvest + diff + connectivity)
.\Invoke-MSTRFullValidation.ps1 `
    -BaselineDir ".\discovery_output" `
    -CloudHost   "https://cloud-mstr.company.com/MicroStrategyLibrary" `
    -CloudUser   "Administrator" `
    -CloudPass   "CloudPassword" `
    -CMCHost     "cloud-mstr.company.com" `
    -OutputDir   ".\full_validation"

# Diff only (if you already have cloud_discovery)
.\Invoke-MSTRValidator.ps1 `
    -Baseline  ".\discovery_output" `
    -Target    ".\cloud_discovery" `
    -OutputDir ".\validation_results"
```

---

## Key Flags

| Flag | Script | Purpose |
|------|--------|---------|
| `-AllProjects` | Harvester | Harvest all projects (default: first 3) |
| `-ProjectId "ID"` | Harvester | Single project only |
| `-NoSslVerify` | Harvester, FullValidation | Skip SSL cert check (self-signed) |
| `-LoginMode 16` | Harvester, FullValidation | LDAP auth (1=Standard, 16=LDAP, 64=SAML) |
| `-OdbcFile "path"` | Connectivity, FullValidation | Also read odbc.ini file |
| `-SkipPing` | Connectivity, FullValidation | Skip ICMP ping (if blocked by firewall) |
| `-SkipHarvest` | FullValidation | Reuse existing cloud_discovery, skip re-harvest |
| `-SkipConnectivity` | FullValidation | Skip connectivity tests |

---

## Output Files

### Phase 1 (discovery_output\)

```
01_server_info.csv          Server version, build, cluster nodes
02_projects.csv             All projects
03_users.csv                All users with auth type
04_usergroups.csv           User groups
05_group_membership.csv     User → group mappings
06_security_roles.csv       Security roles
07_security_filters.csv     Security filters (RLS)
08_datasources.csv          DB connections
09_reports.csv              All reports per project
10_documents_dossiers.csv   Documents and dossiers
11_metrics.csv              Metrics
12_attributes.csv           Attributes
13_facts.csv                Facts
14_filters.csv              Filters
15_prompts.csv              Prompts
16_schedules.csv            Schedules
17_subscriptions.csv        Subscriptions
18_caches.csv               Cache stats
19_security_config.csv      LDAP / SAML / auth config
20_email_config.csv         SMTP settings
21_licenses.csv             License info
SUMMARY_REPORT.txt          Human-readable summary + risk flags
```

### Phase 1 (connectivity_results\)

```
db_connections_inventory.csv   All Windows ODBC DSNs + odbc.ini entries
connectivity_results.csv       DNS + Ping + TCP status per DSN
CONNECTIVITY_REPORT.txt        Pass/fail summary + action items
```

### Phase 3 (full_validation\)

```
cloud_discovery\               Re-harvested cloud IS CSVs
diff_results\DIFF_REPORT.csv   Every field comparison with severity
diff_results\VALIDATION_REPORT.txt  Sign-off scorecard
connectivity_results\          Re-tested connectivity
MASTER_VALIDATION_REPORT.txt   Overall go/no-go verdict
```

---

## Windows ODBC Registry

The connectivity tester automatically reads DB connections from the Windows ODBC registry at:

- `HKLM\SOFTWARE\ODBC\ODBC.INI` (System DSNs — most MSTR installations)
- `HKLM\SOFTWARE\WOW6432Node\ODBC\ODBC.INI` (32-bit drivers on 64-bit Windows)
- `HKCU\SOFTWARE\ODBC\ODBC.INI` (User DSNs)

No odbc.ini file needed. If MSTR IS is configured with Windows System DSNs (the typical Windows IS setup), the script reads them automatically. Run as Administrator for full registry access.

---

## Troubleshooting

**"Cannot connect to server"**
- Verify the URL includes `/MicroStrategyLibrary` (not just the hostname)
- Try `-NoSslVerify` if using a self-signed certificate
- Check firewall allows TCP from this Citrix server to the MSTR port (443 or 8080)

**"Authentication failed"**
- Double-check username/password in the bat file
- For LDAP: set `-LoginMode 16`; for SAML: set `-LoginMode 64`
- Verify the user has IS Administrator privileges

**"ExecutionPolicy" error when running scripts**
- The bat files include `-ExecutionPolicy Bypass` automatically
- If running .ps1 directly: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

**"No DSN entries found" in connectivity tester**
- Run as Administrator for registry access
- Or provide an explicit odbc.ini: `-OdbcFile "C:\MSTR\odbc.ini"`

**Harvest times out on large environments**
- Use `-ProjectId "ID"` to harvest one project at a time
- Remove `-AllProjects` to limit to the first 3 projects initially

---

## AI Prompts (Copy-Paste Ready)

### After Discovery
Paste `SUMMARY_REPORT.txt` into Claude:
> *"You are a senior MSTR cloud migration architect. Review this discovery report. Provide: (1) Top 10 migration risks by severity with explanation, (2) Recommended project migration order easiest to hardest, (3) Deprecated/unsupported features needing redesign, (4) Effort estimate per project Small/Medium/Large, (5) Pre-migration actions. Output as structured tables."*

### After Validation
Paste `DIFF_REPORT.csv` into Claude:
> *"Review this MSTR migration validation diff. For each CRITICAL and HIGH item: (1) explain the business impact, (2) provide the exact fix — REST API call, Command Manager command, or admin action. Start with a go/no-go recommendation. Format: Issue | Impact | Exact Fix | Time."*

### DB Connection Mapping
Paste `08_datasources.csv` into Claude:
> *"Review this DB connection inventory. Flag any DB types unsupported in MSTR Cloud. For each connection, generate a mapping table: Name | DB Type | On-Prem Host | Cloud Action | Notes."*

---

*Toolkit v2.1 | Windows/Citrix Edition | pranay136@gmail.com*
