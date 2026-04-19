# MicroStrategy Cloud Migration — Full Q&A Reference
## EBI Team | Pranay (pranay136@gmail.com) | April 2026

> **How to read this document:** Every question asked during this migration project is captured here with a plain-language answer and a **YES / NO / PARTIAL** verdict. Technical detail follows each verdict. Use this as a living reference for stakeholder conversations, handover, and future MSTR upgrades.

---

## TABLE OF CONTENTS

1. [Can the migration toolkit scripts be automated end-to-end?](#q1)
2. [How do I update README.md as new scripts are added?](#q2)
3. [CMC Issue: Free-form SQL columns are auto-converting to date format — how to fix?](#q3)
4. [CMC Issue: SingleStore JDBC fails even though telnet passes — why and how to fix?](#q4)
5. [CMC Issue: GKE Gatekeeper blocks credential storage — how to handle DB passwords securely?](#q5)
6. [How do I disable specific MCP tools in Cowork / Claude Code?](#q6)
7. [Can EBI team technically own Web Library API testing and integration?](#q7)
8. [What cross-team dependencies exist for API testing, and how do we eliminate them?](#q8)
9. [Should we build a custom tool to replace Integrity Manager?](#q9)
10. [Can the custom test tool be reused for future MSTR upgrades?](#q10)
11. [Can we automate the database IP/hostname scraping for the ACL firewall request?](#q11)
12. [We have seven separate instances (e1–e7) with unknown customizations — can discovery be automated?](#q12)
13. [Java-based SDKs work on-prem but may not work in CMC — is the Java version difference the cause?](#q13)
14. [Integrity Manager won't cover everything — how do we automate Library API + report bursting + subscription emails without any external team?](#q14)
15. [What RACI structure covers all four teams in this migration?](#q15)
16. [Can we contain this migration entirely between HCL and EBI, minimising business involvement?](#q16)

---

## Q1
### Can the migration toolkit scripts be automated end-to-end?
**Answer: YES**

The full toolkit runs in a single pipeline. No manual steps, no consultants, no QA team required.

**Pipeline sequence:**
```
Phase 1 — Discovery
  mstr_harvester.py          → 21 CSVs + SUMMARY_REPORT.txt

Phase 2 — Migration
  mstr_connectivity_tester.py
  mstr_package_migrator.py   → Export on-prem packages, import to cloud IS
  mstr_user_migrator.py      → Bulk users, groups, memberships
  mstr_db_connection_creator.py → Recreate datasources; test from IS
  extended_command_manager.scp  → VLDB, schedules, security filters (IS remote)
  mstr_cache_warmer.py       → Pre-warm top-N report caches

Phase 3 — Validation
  mstr_report_validator.py   → Execute + compare all reports/dossiers
  mstr_subscription_tester.py → Trigger + verify all subscription emails
  full_validation_runner.py  → Metadata diff, MASTER_VALIDATION_REPORT.txt
```

All REST API scripts run from any laptop with HTTPS access to the IS. Command Manager scripts run from any machine with MSTR client tools installed (Citrix server in your case). Zero shell access to the cloud IS host required.

---

## Q2
### How do I keep README.md and SKILL.md updated as new scripts are added?
**Answer: YES — follow a consistent pattern**

Every new script added to the toolkit requires updates in three places:

1. **SKILL.md** — add to the `scripts:` YAML list and write a `### script_name` section with: purpose, execution layer, usage command, output files.
2. **README.md** — add a row to the execution layer table (REST API or Command Manager), and add a usage block in the relevant Phase section.
3. **SKILL.md `## EXECUTION ARCHITECTURE` table** — add the script's network requirement.

The SKILL.md version number (in YAML frontmatter) should be incremented with each addition. Current version: **2.2**.

---

## Q3
### CMC Issue: Free-form SQL columns are being auto-converted to a date format — how to fix it?
**Answer: YES — fixable, four approaches in priority order**

**Root cause:** The JDBC driver reads column metadata from the database and reports the column's native type as `DATE`. MicroStrategy trusts this and applies date formatting — even if the column contains a string value that happens to look like a date.

**Fix chain (try in order):**

1. **CAST in the free-form SQL** (recommended, cleanest):
   ```sql
   SELECT CAST(date_column AS VARCHAR(20)) AS date_column FROM table
   ```
   Forces the driver to see a string type; MSTR stops converting.

2. **Column type override in the Report Editor**: Open the report → right-click the metric/attribute → Format → change Data Type to `Text/String`. Overrides driver metadata for that report only.

3. **VLDB property — Metric data type** (`MetricLevelDimty` or `MetricFormatType`): Set via the MSTR Developer / Web admin panel, or via `extended_command_manager.scp` `CONFIGURE VLDB SETTING` block.

4. **JDBC URL parameter** (for supported drivers): Append `noDatetimeStringSync=true` or `zeroDateTimeBehavior=convertToNull` to the JDBC connection URL in the datasource definition.

**Permanent fix for CMC:** Apply approach 1 (CAST) in the original report SQL — it survives any environment change and is driver-agnostic.

---

## Q4
### CMC Issue: SingleStore JDBC fails even though telnet to the port succeeds — why, and how to fix?
**Answer: YES — fixable. TCP pass ≠ JDBC pass. Here is why.**

**Root cause:** Telnet tests only the TCP handshake (Layer 4 — network reachability). JDBC fails at the application layer (Layer 7) for separate reasons.

**Checklist — in order of likelihood:**

| Check | Correct value for SingleStore |
|-------|------------------------------|
| Driver JAR | `singlestore-jdbc-client-x.x.x.jar` (NOT `mysql-connector`) |
| Driver class | `com.singlestore.jdbc.Driver` |
| JDBC URL prefix | `jdbc:singlestore://` (NOT `jdbc:mysql://`) |
| SSL enforcement | Add `?sslMode=DISABLED` if CMC doesn't have the cert, or `sslMode=VERIFY_CA` with cert |
| Auth plugin | `mysql_native_password` (set in SingleStore server config) |
| Port | Default `3306` — confirm CMC security group allows inbound on this port |

**Validation without business team:** Use `POST /api/datasources/{id}/testConnection` from the REST API. This fires the connection test **from the IS itself** (not your laptop). A result of `REACHABLE_AUTH_NEEDED` (HTTP 400 + credential error) means TCP is passing — the IS reached the DB. The problem is then purely credential or driver-config.

---

## Q5
### CMC Issue: GKE Gatekeeper blocks credential storage — how do we store DB passwords securely in the cloud IS?
**Answer: YES — three viable approaches, one recommended**

**Root cause:** OPA/Gatekeeper policies in GKE reject pod specs that contain plaintext secrets (passwords, connection strings) in environment variables or config maps. This is correct security behavior — you need a secrets management approach.

**Option 1 — External Secrets Operator + Google Secret Manager (RECOMMENDED for CMC):**
- Store all MSTR datasource passwords in Google Secret Manager
- External Secrets Operator (ESO) syncs them automatically into Kubernetes Secrets
- Mount as environment variables into the IS pod
- MSTR reads the env var at runtime
- Zero plaintext credentials in any YAML/config file
- MSTR CloudOps team can manage this without EBI involvement

**Option 2 — Workload Identity (for GKE-native auth):**
- If the database supports IAM authentication (e.g., Cloud SQL, BigQuery)
- The IS pod's GKE Service Account is granted DB-level IAM permissions
- No passwords at all — purely identity-based
- Requires DB-side IAM setup (one-time EBI ACL Specialist task)

**Option 3 — HashiCorp Vault Sidecar Injector (enterprise environments):**
- Vault agent sidecar injects secrets as files at pod startup
- MSTR IS reads credential files rather than env vars
- Highest compliance posture; adds operational complexity

**For the ACL request:** EBI ACL Specialist needs to raise a request to: (a) create the Google Secret Manager secret entries, and (b) grant the IS service account `roles/secretmanager.secretAccessor` on those entries.

---

## Q6
### How do I disable specific MCP tools in Cowork / Claude Code?
**Answer: YES — two methods**

**Method 1 — Disable the entire MCP server** (if you want to block all tools from one provider):
In Claude Code, edit `.claude/settings.json` or the global config and remove or comment out the relevant entry under `mcpServers`. The tool names like `mcp__333d8943-612b-4845-af35-6df76200006e__merge-designs` all share the same server UUID prefix — removing that server removes all its tools.

**Method 2 — PreToolUse hook** (if you want to block specific tools only):
Add a hook in `.claude/settings.json`:
```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "mcp__333d8943.*merge-designs|mcp__Claude_in_Chrome__computer",
      "hooks": [{ "type": "command", "command": "exit 1" }]
    }]
  }
}
```
This allows all other tools from the same MCP server while blocking just the listed ones.

---

## Q7
### Can EBI team technically own Web Library API testing and integration end-to-end?
**Answer: YES — fully EBI-ownable with the right tooling**

EBI already controls every dependency:
- **IS admin credentials** for both on-prem and CMC: EBI owns
- **Report and dossier object IDs**: harvested by `mstr_harvester.py` into `09_reports.csv` — EBI owns
- **Datasource credentials on IS**: set by EBI ACL Specialist — no DBA involvement needed at run time
- **Network path to IS (HTTPS 443)**: EBI ACL manages this

The only one-time touch with Business: ask them to confirm the correct prompt answer values for prompted reports. Configure these once in the `prompt_answers:` YAML block of `config.yaml`. After that, zero business involvement for any test run.

**Key REST API endpoints EBI uses for execution testing:**

| Endpoint | Purpose |
|----------|---------|
| `POST /api/auth/login` | Get session token |
| `POST /api/v2/reports/{id}/instances` | Execute a report |
| `GET /api/v2/reports/{id}/instances/{instanceId}` | Fetch result rows + schema |
| `PUT /api/v2/reports/{id}/instances/{instanceId}/prompts/answers` | Answer prompts programmatically |
| `POST /api/dossiers/{id}/instances` | Execute a dossier |
| `GET /api/dossiers/{id}/instances/{instanceId}/chapters` | Get dossier chapter structure |
| `GET /api/subscriptions` | List all subscriptions |
| `POST /api/subscriptions/{id}/deliver` | Trigger subscription delivery |

All of these are standard MSTR REST API v2 endpoints. They work remotely over HTTPS — no cluster access needed.

---

## Q8
### What cross-team dependencies exist for API testing, and how do we eliminate them?

| Dependency | Owned by | Elimination strategy |
|-----------|----------|---------------------|
| IS admin credentials (source + target) | EBI admin | Already in hand — no dependency |
| Report/dossier object IDs | EBI (from `09_reports.csv`) | Already harvested — no dependency |
| Datasource credentials on IS | EBI ACL Specialist | Pre-configured on IS — no run-time dependency |
| Prompted report answer values | Business — one time only | Capture once in YAML config; run-time independent after |
| Network access to IS (HTTPS 443) | EBI ACL | Already required for other scripts — no new dependency |
| SMTP relay for subscription emails | EBI ACL / CMC Ops | Route test subscriptions to EBI mailbox — no Business dependency |

**Net result:** Zero run-time dependency on Business or HCL for any test execution after initial prompt-value collection.

---

## Q9
### Should we build a custom tool to replace Integrity Manager?
**Answer: YES — and it has been built**

**Why Integrity Manager is insufficient:**

| Integrity Manager limitation | Impact |
|-----------------------------|--------|
| Requires both environments live simultaneously | Cannot store a reusable baseline — can't test before/after |
| Cannot handle prompted reports | Skips all prompted reports silently |
| Binary screenshot comparison | No row count diff, no schema diff, no data value diff |
| No structured output | No CSV, no HTML dashboard, no CI/CD integration |
| Cannot run at scale programmatically | Manual UI steps — impractical for 5,000+ reports |
| Cannot test dossier structure | No chapter/visualization comparison |
| Cannot test subscriptions or email delivery | Out of scope entirely |

**What was built:** `mstr_report_validator.py`

- **Capture mode**: Execute all reports/dossiers on source, save one JSON snapshot per report to `./baseline/`
- **Compare mode**: Execute target live, compare row count + column schema + data hash against stored baseline
- **Full mode**: Execute both simultaneously, compare on-the-fly
- **Detects**: Row count changes, added/removed columns, silent data value drift (hash mismatch with same shape)
- **Parallel execution**: 4–8 workers; tests 500+ reports in ~30 minutes
- **Prompt support**: Pre-configured YAML answers — no business user at runtime
- **Output**: Interactive HTML dashboard + CSV for JIRA/ServiceNow tickets

---

## Q10
### Can the custom test tool be reused for future MSTR upgrades?
**Answer: YES — this is a first-class use case the tool was designed for**

The capture-and-compare model makes it naturally reusable:

```bash
# Before MSTR upgrade — capture baseline
python mstr_report_validator.py --mode capture --config config.yaml --label pre-upgrade-v12.1

# Perform the MSTR upgrade

# After upgrade — compare results
python mstr_report_validator.py --mode upgrade --config config.yaml --label post-upgrade-v12.1
# Output: validation_TIMESTAMP.html showing every report that changed behaviour
```

Baselines are stored as JSON files in `./baseline/` — they persist indefinitely. You can keep multiple baseline sets (pre-migration, post-migration, pre-upgrade, post-upgrade) and diff across any pair. This effectively gives EBI a regression test suite for every future MSTR change — platform upgrades, VLDB changes, schema changes, or driver updates.

---

## Q11
### Can we automate the database IP/hostname scraping for the ACL firewall request?
**Answer: YES — fully automated on Windows/Citrix**

**Two scripts were created:**

**`fetch_mstr_datasources.scp`** (Command Manager):
- Connects to the IS and lists all datasources and DB connections across all projects
- Run from Citrix: `mstrcmd.exe -f fetch_mstr_datasources.scp -n IS-HOST -u admin -p pass -o output.txt`

**`build_acl_request.ps1`** (PowerShell):
- Reads Windows ODBC System + User DSNs directly from the Windows registry (fastest path — no MSTR login needed)
- Auto-detects driver type: SQL Server, Oracle, MySQL, SingleStore, Teradata, PostgreSQL, Redshift, IBM DB2, Snowflake, Hive/Impala, and generic ODBC
- Resolves every hostname to IP address via DNS
- Optionally merges with Command Manager output file for DSN-less connections
- Outputs: `acl_request_db_inventory_TIMESTAMP.csv` + formatted HTML report

**Run on Citrix:**
```powershell
powershell.exe -ExecutionPolicy Bypass -File build_acl_request.ps1
# With CM output merged:
powershell.exe -ExecutionPolicy Bypass -File build_acl_request.ps1 -CMOutputFile .\mstr_datasource_dump.txt
```

Output CSV is ready to paste directly into the ACL/firewall ticket. Hostnames that cannot be resolved via DNS are flagged as `DNS_UNRESOLVED` for manual network team lookup.

---

## Q12
### We have seven separate MSTR instances (e1–e7) with their own customizations. Can discovery be automated?
**Answer: YES — fully automatable**

**What "customizations" means in MSTR and where they live:**

| Customization type | Location | Discoverable via |
|-------------------|----------|-----------------|
| VLDB settings (project + object level) | MSTR metadata (IS) | REST API |
| Project-level settings (execution, caching, auth) | MSTR metadata (IS) | REST API `GET /api/projects/{id}/settings` |
| DB connection configs | MSTR metadata (IS) | REST API + `08_datasources.csv` |
| Web SDK plugins (JSP/Java customizations) | `MicroStrategyLibrary/plugins/` folder | File system scan (PowerShell on Citrix) |
| Custom properties overrides | `MicroStrategyLibrary/WEB-INF/classes/` | File system scan |
| Security config (LDAP, SAML, trusted auth) | MSTR metadata | REST API + `19_security_config.csv` |
| Email / SMTP config | MSTR metadata | REST API + `20_email_config.csv` |
| Schedules + subscriptions | MSTR metadata | REST API + `16_schedules.csv`, `17_subscriptions.csv` |
| Custom auth plugins (JAR files on IS) | IS file system | File system scan (if IS file access available) |

**Automation approach:**

Run `mstr_harvester.py` against all seven IS instances — it already collects all metadata-level customizations. Compare the seven resulting `SUMMARY_REPORT.txt` and CSV files to identify what differs per environment.

For file-system level web customizations (plugins folder), add a PowerShell scan on the Citrix server:
```powershell
# Scan plugins folder across all environments and list non-standard files
Get-ChildItem "\\e1-server\MicroStrategyLibrary\plugins\" -Recurse |
  Where-Object { $_.Extension -in '.jar','.xml','.properties' } |
  Select-Object Name, LastWriteTime, Length, DirectoryName |
  Export-Csv ".\e1_plugins_inventory.csv" -NoTypeInformation
```
Repeat for e2–e7 and diff the CSVs. Any file present in some environments but not others is a customization that needs to be explicitly ported to CMC.

**For CMC:** Customizations go into the CMC Customization Framework — not directly into the web app folder. Each plugin must be repackaged as a CMC-compatible bundle and deployed via the CMC admin console. The inventory above tells you exactly which ones to package.

---

## Q13
### Java-based SDKs work on-prem but may not work in CMC — is Java version differences the cause?
**Answer: PARTIAL — Java version is a contributing factor, but the full picture has three separate issues**

**Issue 1 — Java version mismatch (the stated hypothesis)**

| Environment | Typical Java version |
|-------------|---------------------|
| On-prem IS (older deployments) | Java 8 (JDK 1.8) |
| CMC IS (containerized) | Java 11 or Java 17 |

Java is backward-compatible upward: code compiled for Java 8 (`-target 1.8`) will run on Java 11/17. However, Java 11+ removed several APIs that existed in Java 8 (`javax.xml.bind`, `javax.activation`, etc.) — if your custom JARs use these, they will fail with `ClassNotFoundException` on CMC.

**Diagnosis command (run on Windows):**
```cmd
javap -verbose MyCustomClass.class | findstr "major version"
```
| major version | Java version |
|---------------|-------------|
| 52 | Java 8 |
| 55 | Java 11 |
| 61 | Java 17 |

If the major version is higher than what CMC supports, you will get `UnsupportedClassVersionError`.

**Fix:** Recompile against CMC's Java version using the MSTR SDK JARs from the CMC build (not the on-prem build). The SDK JARs must match the exact MSTR version deployed in CMC.

---

**Issue 2 — Direct MSTR Java API connections (more serious)**

The MSTR Java API (`com.microstrategy.web.app`, `com.microstrategy.webapi`) makes direct socket connections to the IS on port 34952. In CMC, this port is typically not externally exposed — the IS sits behind a load balancer/API gateway that only exposes HTTPS (443).

**Verdict: Direct Java API connections will NOT work in CMC unless port 34952 is explicitly opened** (which CMC typically does not do).

**Fix:** Replace all direct Java API usage with REST API calls (`/api/*` over HTTPS). This is the architecturally supported path for CMC. REST API provides equivalent functionality for: authentication, report execution, object management, user management, and subscription management.

---

**Issue 3 — CMC platform constraints on custom IS plugins**

Some on-prem deployments include custom Java plugins deployed directly into the IS (custom auth handlers, custom event handlers, custom transformations). In CMC, the IS runs in a managed container — you cannot deploy arbitrary JARs to the IS host.

**Verdict:** Custom IS-side Java plugins will NOT work in CMC as-is. They must be refactored to use CMC's supported extension points (REST API hooks, SAML/LDAP for auth, CMC customization framework for web layer).

---

**Summary for Q13:**

| SDK type | Works in CMC? | Fix required |
|----------|--------------|-------------|
| Web SDK customizations (compiled JAR, Java 8 target) | YES if no removed-API usage | Recompile against CMC MSTR JARs + Java 11 |
| Web SDK customizations (using Java 8 removed APIs) | NO | Refactor + recompile |
| Direct MSTR Java API (port 34952) | NO | Replace with REST API |
| Custom IS-side Java plugins | NO | Refactor to REST API / CMC extension points |
| JDBC driver JARs on IS | YES (usually) | Verify driver JAR compatibility with CMC's Java version |

---

## Q14
### Integrity Manager won't cover everything — how do we automate Library API + report bursting + subscription email testing without any external team?
**Answer: YES — fully automatable, entirely EBI-owned, zero external team involvement**

Three layers, three tools:

---

### Layer 1 — Report and Dossier Output Testing (Library API)
**Tool: `mstr_report_validator.py`** (already built)

Covers: every report and dossier in every project. Compares row counts, column schemas, and data hashes between on-prem and cloud. Handles prompted reports via pre-configured YAML answers. Produces HTML dashboard + CSV.

This is also the Library API test — the same `/api/reports` and `/api/dossiers` endpoints that MSTR Library uses for rendering are what this tool calls. If the tool passes, Library rendering will pass.

---

### Layer 2 — Subscription Email and Report Bursting Testing
**Tool: extend `mstr_report_validator.py` or create `mstr_subscription_tester.py`**

**How to automate subscription/email testing without Business or external team:**

**Step 1 — Inventory all subscriptions:**
```
GET /api/subscriptions?limit=200
```
Returns all subscriptions with: name, delivery type (email/file/FTP), schedule, report/dossier ID, recipient list.

**Step 2 — Create shadow test subscriptions:**
For each subscription in the inventory, create a copy pointing to an EBI-controlled mailbox (e.g., `mstr-test@company.com`). This avoids triggering deliveries to real business users during testing.
```
POST /api/subscriptions
{
  "name": "TEST_COPY — {original_name}",
  "contents": [{"id": "{report_id}", "type": "report"}],
  "schedules": [{"id": "{schedule_id}"}],
  "delivery": {
    "mode": "EMAIL",
    "toAddresses": ["mstr-test@company.com"]
  }
}
```

**Step 3 — Trigger delivery:**
```
POST /api/subscriptions/{id}/deliver
```
This fires the subscription immediately (ignores schedule) and delivers to the configured address.

**Step 4 — Verify receipt via IMAP:**
```python
import imaplib, email
mail = imaplib.IMAP4_SSL("mail.company.com")
mail.login("mstr-test@company.com", "password")
mail.select("INBOX")
# Search for emails from MSTR in the last 5 minutes
_, msgs = mail.search(None, 'FROM "noreply@mstr.company.com" SINCE "today"')
```
Verify: email received, subject matches report name, attachment present (PDF/Excel), attachment is non-empty.

**Step 5 — Cleanup:** Delete all TEST_COPY subscriptions after testing.

**What this tests end-to-end:**
- IS can execute the report (same as `mstr_report_validator.py`)
- IS can render the output to PDF/Excel format (bursting engine)
- IS can connect to the SMTP relay configured in CMC (email delivery path)
- SMTP relay in CMC can route to the destination mailbox
- Attachment is non-corrupt and non-empty

**CMC-specific SMTP check:** Verify the SMTP relay host/port in CMC is different from on-prem. The CMC IS may need to use a cloud SMTP relay (SendGrid, AWS SES, or internal corporate relay). This is an EBI ACL Specialist task — raise the SMTP outbound rule as part of the ACL request.

---

### Layer 3 — Full Automated Test Run (all three layers together)

```bash
# Step 1: Report and dossier output (Library API test)
python mstr_report_validator.py --mode compare --config config.yaml --label post-migration

# Step 2: Subscription email delivery test
python mstr_subscription_tester.py --config config.yaml --mailbox mstr-test@company.com

# Step 3: Metadata diff (users, groups, connections, schedules)
python full_validation_runner.py --baseline-dir ./discovery_output \
    --cloud-host https://CMC-MSTR/MicroStrategyLibrary \
    --cloud-user Administrator --cloud-pass CloudPass \
    --output-dir ./full_validation
```

All three produce structured output. Feed the combined results to AI (Claude) for instant triage and a go/no-go recommendation. Zero external team involvement at any stage.

---

## Q15
### What RACI structure covers all four teams in this migration?
**Answer: YES — detailed RACI was created**

The RACI Excel (`MSTR_Migration_RACI_v3.xlsx`) covers four teams across four phases:

| Team | Role in migration | Phase involvement |
|------|------------------|------------------|
| **HCL** (6 roles) | Lift-and-shift execution — technical migration lead | Phases 1, 2, 3 |
| **EBI** (4 roles) | Core admin ownership — ACL, validation, automation | Phases 1, 2, 3 |
| **Business** (3 roles) | Validation sign-off only — minimised involvement | Phase 3 only |
| **SRE** (4 roles) | Post-migration monitoring and steady-state | Phase 4 (post-migration) only |

**Key ownership rules:**
- EBI ACL Specialist is Accountable on every access/firewall/credential task
- Business is Informed (not Consulted) for most tasks — they sign off on UAT, not technical decisions
- SRE columns are amber-tinted throughout the RACI as a visual reminder they are not engaged during Phases 1–3
- HCL is Responsible for migration execution; EBI is Accountable for outcome quality

---

## Q16
### Can we contain this migration entirely between HCL and EBI, minimising business user involvement?
**Answer: YES — and the toolkit is specifically designed for this**

**Business involvement is limited to three touch points:**

1. **Prompt answer values** (one time): A 30-minute call to confirm the correct input values for prompted reports. Configure in `prompt_answers:` YAML block. Never needed again after that.

2. **UAT sign-off** (Phase 3): Business users run a subset of their most critical reports in the cloud environment and confirm outputs look correct. They do NOT need to run any tools — they just log in to MSTR Web and use reports normally. Target: ≤ 5 business users, ≤ 2 days.

3. **Go/no-go decision**: Business approves the migration go-live date based on the automated validation report (auto-generated by `full_validation_runner.py` and AI-summarised). They read a one-pager — they do not review raw data.

**Everything else is HCL + EBI:**
- All discovery, migration execution, technical validation, connectivity testing, cache warming, subscription testing, and sign-off report generation is handled entirely by the toolkit and the two teams.
- The automated toolkit was explicitly designed so that business users never see a command line, never configure a tool, and never interpret a diff report.

---

## QUICK REFERENCE — YES / NO SUMMARY

| # | Question | Answer |
|---|----------|--------|
| Q1 | Can the toolkit be automated end-to-end? | **YES** |
| Q2 | Can README/SKILL.md be kept current easily? | **YES** |
| Q3 | Can the free-form SQL date conversion be fixed? | **YES** — CAST in SQL |
| Q4 | Can SingleStore JDBC be fixed despite telnet passing? | **YES** — driver class + URL fix |
| Q5 | Can GKE Gatekeeper credential blocking be solved? | **YES** — ESO + Secret Manager |
| Q6 | Can specific MCP tools be disabled? | **YES** — hook or server removal |
| Q7 | Can EBI own Web Library API testing end-to-end? | **YES** |
| Q8 | Can cross-team dependencies for API testing be eliminated? | **YES** — one-time YAML config |
| Q9 | Should we build a custom tool to replace Integrity Manager? | **YES** — built: `mstr_report_validator.py` |
| Q10 | Can the custom tool be reused for MSTR upgrades? | **YES** — capture-and-compare model |
| Q11 | Can DB IP/hostname scraping for ACL be automated? | **YES** — built: `build_acl_request.ps1` |
| Q12 | Can customization discovery across 7 instances be automated? | **YES** — REST API + file scan |
| Q13 | Is Java version the cause of SDK failures in CMC? | **PARTIAL** — 3 separate issues |
| Q14 | Can all testing be automated without external teams? | **YES** — 3-layer automation |
| Q15 | Is there a RACI for all four teams? | **YES** — Excel RACI v3 |
| Q16 | Can migration be contained between HCL and EBI? | **YES** — 3 business touch points only |

---

## SCRIPTS IN THIS TOOLKIT (as of v2.2)

| Script | Phase | Layer | Purpose |
|--------|-------|-------|---------|
| `mstr_harvester.py` | 1 | REST API | Harvest all metadata → 21 CSVs |
| `mstr_connectivity_tester.py` | 1/3 | REST API | Test DB connectivity from CMC IS |
| `mstr_package_migrator.py` | 2 | REST API | Export/import project packages |
| `mstr_user_migrator.py` | 2 | REST API | Bulk users, groups, memberships |
| `mstr_db_connection_creator.py` | 2 | REST API | Create + test datasources from IS |
| `mstr_cache_warmer.py` | 2 | REST API | Pre-warm top-N report caches |
| `mstr_report_validator.py` | 3 | REST API | Execute + compare all reports/dossiers |
| `mstr_validator.py` | 3 | Local | Diff on-prem vs cloud metadata CSVs |
| `full_validation_runner.py` | 3 | REST API | Orchestrate all Phase 3 steps |
| `mstr_command_manager.scp` | 1/2/3 | Command Manager | Discovery, packages, post-migration ops |
| `extended_command_manager.scp` | 2 | Command Manager | Bulk VLDB, schedules, security filters |
| `fetch_mstr_datasources.scp` | ACL | Command Manager | List all datasources for ACL request |
| `build_acl_request.ps1` | ACL | PowerShell/Windows | ODBC registry scan → IPs → ACL CSV |

---

---

## Q17
### Where can this migration go wrong? Stress-test the entire strategy.
**Answer: 13 identified failure points across 5 categories — all mitigable with specific actions**

---

#### CATEGORY 1 — TIME BOMBS (Pass all checks, detonate at go-live)

**Risk 1 — DB passwords are NOT in migration packages.**
Packages migrate datasource definitions (server, port, driver) but not the password. The password is encrypted using the IS installation's own key and cannot be transported. Anyone who assumes "packages migrated everything" will discover this at 11pm on cutover night when every report returns auth errors.

*Mitigation:* Build a `datasource_passwords.yaml` (git-ignored, encrypted at rest) with all credential pairs during Phase 1. `mstr_db_connection_creator.py` reads from this file. Run `testConnection` immediately after import. Make this an explicit line item on the go-live checklist.

---

**Risk 2 — Schedule timezone trap.**
MSTR stores schedule times in the IS server's local timezone. If on-prem IS is `America/Chicago` and CMC IS defaults to `UTC`, every scheduled report runs 5–6 hours off. This is invisible in metadata validation — the schedule definition migrates correctly; only execution time is wrong. Business discovers this Monday morning after go-live.

*Mitigation:* Before go-live, confirm CMC IS timezone matches on-prem IS timezone. On Linux CMC: `timedatectl`. If different, adjust all schedule times in `extended_command_manager.scp` or request CMC Ops to set the container timezone.

---

**Risk 3 — Event-based schedules will silently never fire.**
Reports triggered by ETL completion, file arrival, or API calls fire against the IS URL. If external systems (ETL pipelines, middleware) still point to the on-prem IS URL after cutover, events fire at the dead IS and nothing happens on CMC. No error, no alert — reports simply never run.

*Mitigation:* From `16_schedules.csv`, filter `SCHEDULE_TYPE = EVENT`. For each event-triggered schedule, identify which external system fires the event. Create an "integration update checklist" — each firing system must be updated to the CMC URL before go-live. This is a cross-team dependency that must be in the RACI.

---

**Risk 4 — Non-email subscriptions break completely.**
Subscriptions delivering to `\\fileserver\shared\` or `ftp://internal-ftp/mstr/` will fail silently from CMC — the cloud IS has no network path to on-prem file shares without explicit VPN/Direct Connect. Email subscriptions work; file-path subscriptions do not.

*Mitigation:* From `17_subscriptions.csv`, filter `DELIVERY_TYPE != EMAIL`. Every non-email subscription is a risk. Classify each as: convert to email, establish network path, or decommission. Resolve before go-live — not after.

---

**Risk 5 — Report API passes but browser renders blank.**
`mstr_report_validator.py` confirms the IS executes the report and returns correct data. It does not test browser rendering. A Web SDK customization that loaded on the on-prem Tomcat but wasn't ported to CMC causes a blank render even though the underlying data is correct.

*Mitigation:* After automated validation, run a browser-based smoke test on the top 20 highest-accessed reports. Use `mstr_cache_warmer.py` output to identify them. Manually open and verify in CMC Web before DNS flip.

---

**Risk 6 — The baseline goes stale.**
If the baseline is captured in Week 1 and go-live is Week 8, reports created or modified in those 7 weeks show as false failures or are absent from the baseline entirely.

*Mitigation:* Re-run `mstr_harvester.py` and `mstr_report_validator.py --mode capture` within 24 hours of cutover. Enforce a **change freeze** on the on-prem IS for 48 hours before go-live. Treat final baseline capture as a go-live activity, not a Phase 1 activity.

---

#### CATEGORY 2 — ASSUMPTION FAILURES

**Risk 7 — IP resolution ran from the wrong machine.**
`build_acl_request.ps1` resolves hostnames from the Citrix machine's DNS. With split-horizon DNS, the same hostname may resolve to a different IP from the CMC network segment. The ACL is raised for the Citrix-resolved IP; the CMC IS traffic is blocked by the actual IP.

*Mitigation:* After raising ACLs, validate every datasource using `POST /api/datasources/{id}/testConnection`. This fires from the IS itself. `REACHABLE_AUTH_NEEDED` = correct IP, traffic passing. Do not close the ACL request until IS-side validation passes for every connection.

---

**Risk 8 — Object cross-references break when projects migrate in the wrong order.**
Reports in Project A reference shared metrics or filters in Project B. If Project A migrates first, those cross-references resolve to nothing and reports fail with "object not found."

*Mitigation:* Run `mstr_harvester.py` across all instances and ask AI to analyze `09_reports.csv`, `11_metrics.csv`, and `14_filters.csv` for cross-project dependencies. Migrate in dependency order — shared-object projects always first.

---

**Risk 9 — LDAP/SAML breaks because the CMC IS URL changed.**
SAML IdP metadata has the Assertion Consumer Service URL hardcoded to the on-prem hostname. LDAP server firewall allows only the on-prem IS IP. After DNS flip, all users get authentication errors simultaneously.

*Mitigation:* Before go-live: (1) update the SAML ACS URL in the IdP to the CMC hostname, (2) add CMC IS egress IP to the LDAP server allow list. Both are in the ACL request but NOT in the ODBC-based `build_acl_request.ps1` output — add them manually to the firewall ticket.

---

#### CATEGORY 3 — SEVEN-INSTANCE SPECIFIC RISKS

**Risk 10 — Object GUID collision if consolidating into a single CMC.**
If all seven instances migrate into one shared CMC environment, objects with the same GUID overwrite each other silently. Whichever instance migrated last wins; earlier instances lose their objects with no error.

*Mitigation:* Run this across all seven harvest outputs before migrating anything:
```python
import pandas as pd, glob
all_ids = pd.concat([pd.read_csv(f)['id'] for f in glob.glob("*/09_reports.csv")])
print(all_ids[all_ids.duplicated(keep=False)])
```
Any duplicate ID is a collision candidate. Resolve before migration begins.

---

**Risk 11 — Version drift across seven instances.**
If e1 is MSTR 2021 and e3 is MSTR 2022, features deprecated between those versions and the CMC version will silently break. The report validator catches the symptom (data hash mismatch) but not the cause.

*Mitigation:* Compare `01_server_info.csv` across all seven instances. Build a version-gap risk list by reviewing MSTR release notes for deprecations between each instance version and the CMC version. Treat each version gap as a separate risk work stream.

---

**Risk 12 — Instance plugins contain hardcoded environment-specific URLs.**
A plugin on e3 calls `http://internal-api-e3.company.com/data`. After migration, that URL is unreachable from CMC. The plugin source code is the only place this is visible — it doesn't appear in any harvest CSV.

*Mitigation:* For each instance, collect plugin JARs and decompile or review source. Search for HTTP calls, file paths, and hostname strings. Flag any environment-specific reference. This is manual — budget 2–4 hours per instance.

---

#### CATEGORY 4 — THE MOST DANGEROUS GAP: NO ROLLBACK PLAN

**Risk 13 — There is currently no documented rollback procedure.**
If critical reports produce wrong data 6 hours after go-live, the team needs a clear, tested path back to on-prem. If the on-prem IS was decommissioned, that path doesn't exist.

*Mitigation:*
- Keep on-prem IS running for **minimum 4 weeks** after go-live, read-only mode
- Do not decommission until at least one full monthly reporting cycle completes on CMC
- Document the rollback as a 3-step runbook: flip DNS back → confirm on-prem IS responds → notify users
- Define explicit rollback triggers before go-live: e.g., >5% of Priority-1 reports failing, Finance data access down >30 min, LDAP auth failure for all users
- Practice the DNS rollback during a dry-run migration weekend before the real cutover

---

#### QUICK-REFERENCE RISK REGISTER

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|-----------|
| 1 | DB passwords not in packages | **HIGH** | Critical | `datasource_passwords.yaml` + testConnection |
| 2 | Schedule timezone mismatch | **HIGH** | High | Verify IS timezone before go-live |
| 3 | Event schedules never fire | **MEDIUM** | High | Integration update checklist |
| 4 | File-path subscriptions break | **HIGH** | Medium | Inventory + reclassify before go-live |
| 5 | Browser renders blank despite API pass | **MEDIUM** | High | Top-20 browser smoke test |
| 6 | Stale baseline | **HIGH** | Medium | Re-capture within 24h of cutover |
| 7 | ACL raised for wrong IP | **MEDIUM** | Critical | IS-side testConnection validation |
| 8 | Cross-project reference failure | **MEDIUM** | High | Dependency-ordered migration |
| 9 | LDAP/SAML auth breaks | **HIGH** | Critical | Update IdP + LDAP allow list pre-cutover |
| 10 | GUID collision (if consolidating) | **HIGH** | Critical | Dedup audit before any package migration |
| 11 | Version drift across 7 instances | **HIGH** | High | Version-gap risk list per instance |
| 12 | Plugin hardcoded URLs | **MEDIUM** | High | Plugin source code review |
| 13 | No rollback plan | **CONFIRMED** | Critical | Keep on-prem live 4 weeks post go-live |

---

## Q18
### Instance tech teams are asking for funding to do validation. What is the strategy?

**Answer: Reframe before you negotiate. The automated toolkit eliminates the work they think they're being paid for.**

---

**Why they're really asking:**
This is not a technical problem. It is an organisational signal. The funding ask means they feel the migration is being done *to* them rather than *with* them. They expect weeks of manual testing effort and want to be compensated for it. Show them the tool output first — the ask typically deflates immediately.

---

**The five-move strategy:**

**Move 1 — Show before you talk.**
Before any funding conversation, run `mstr_report_validator.py` against their instance and present the HTML dashboard. "Here are your 847 reports. 831 pass. 16 need attention. Here is exactly what each failure is. We found this in 45 minutes with zero manual effort from your team." Once they see the automation, the premise of the funding ask is gone.

**Move 2 — Reframe the value exchange.**
Each instance team benefits from migration — they lose their on-prem maintenance burden (server patching, MSTR upgrades, hardware refresh cycles). The migration saves them budget. The conversation becomes: "We are delivering a validated cloud environment at zero cost to your team. We need three things: Citrix access to your IS, one hour with your MSTR admin for prompt value confirmation, and a sign-off review of the validation report. What funding is required for those three activities?"

**Move 3 — Use the RACI as a governance document.**
The RACI shows each instance team as "Informed" on most migration tasks. If they escalate to wanting "Responsible" or "Accountable" ownership, they gain authority but also gain accountability for delivery timelines. Most teams will choose to stay "Informed" once they understand what accountability means.

**Move 4 — Propose a centralised validation model.**
EBI runs the full automated validation for all seven instances centrally. Each instance team receives a validation report specific to their instance. Their validation effort is: open the report, read the executive summary, sign the approval email. That is 30 minutes. No team can claim external funding for 30 minutes of reading.

**Move 5 — Document access refusal as a project risk.**
If a team refuses to provide access without funding, document it: "Instance e3 tech team has not provided IS credentials. EBI cannot perform automated validation for this instance. Risk: go-live for e3 cannot be certified." This is not a threat — it is accurate project risk documentation. Leadership will apply pressure when they see a go-live date slipping because one team withheld access credentials.

**The underlying rule:** Never let another team's budget politics become your technical problem. The only non-negotiable is access. Everything else is negotiable.

---

*Document version: 1.1 | Last updated: April 2026 | EBI Team — Pranay (pranay136@gmail.com)*
