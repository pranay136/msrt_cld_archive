================================================================================
  MicroStrategy Metadata Harvester Toolkit v2.0
  AI-Accelerated Discovery, Migration & Validation for MicroStrategy Cloud
================================================================================

CONTENTS OF THIS PACKAGE
─────────────────────────
  mstr_harvester.py         Main discovery script (REST API based)
  mstr_validator.py         Post-migration comparison & validation script
  mstr_command_manager.scp  Command Manager script (server-side operations)
  requirements.txt          Python dependencies
  README.txt                This file
  MSTR_Migration_Playbook.docx  Full admin migration guide
  AI_Discovery_Validation_Runbook.docx  AI tools & MSTR-native tool runbook


QUICK START — DISCOVERY
────────────────────────
1. Install dependencies:
     pip install -r requirements.txt

2. Run the harvester against your on-prem instance:
     python mstr_harvester.py \
       --host https://YOUR-MSTR-SERVER/MicroStrategyLibrary \
       --username Administrator \
       --password YourPassword \
       --output-dir ./onprem_discovery \
       --all-projects

3. Output files will be in ./onprem_discovery/:
     01_server_info.csv
     02_projects.csv
     03_users.csv
     04_usergroups.csv
     05_group_membership.csv
     06_security_roles.csv
     07_security_filters.csv
     08_datasources.csv
     09_reports.csv
     10_documents_dossiers.csv
     11_metrics.csv
     12_attributes.csv
     13_facts.csv
     14_filters.csv
     15_prompts.csv
     16_schedules.csv
     17_subscriptions.csv
     18_caches.csv
     19_security_config.csv
     20_email_config.csv
     21_licenses.csv
     SUMMARY_REPORT.txt       <-- Feed this to AI for risk analysis
     harvester.log

4. Feed SUMMARY_REPORT.txt to Claude or ChatGPT:
     Prompt: "You are a MicroStrategy migration expert. Review this discovery
     report and identify the top migration risks, estimate effort per project,
     flag deprecated features, and generate a migration runbook table."


QUICK START — VALIDATION (After Cloud Migration)
─────────────────────────────────────────────────
1. Run harvester against your CLOUD instance:
     python mstr_harvester.py \
       --host https://YOUR-CLOUD-MSTR/MicroStrategyLibrary \
       --username Administrator \
       --password CloudPassword \
       --output-dir ./cloud_discovery \
       --all-projects

2. Run the validator to compare:
     python mstr_validator.py \
       --baseline ./onprem_discovery \
       --target ./cloud_discovery \
       --output-dir ./validation_results

3. Review:
     DIFF_REPORT.csv          Field-by-field comparison
     VALIDATION_REPORT.txt    Human-readable pass/fail summary

4. Feed DIFF_REPORT.csv to AI:
     Prompt: "Review this MSTR migration diff report. Classify each issue as
     Critical/Warning/Info. For Critical items, provide the remediation step."


COMMAND MANAGER USAGE
──────────────────────
The mstr_command_manager.scp file works with MicroStrategy Command Manager:

  Windows:
    "%MSTR_HOME%\bin\mstrcmd.exe" -f mstr_command_manager.scp \
      -n YOUR_IS_SERVER -u Administrator -p Password \
      -o command_manager_output.txt

  Linux:
    $MSTR_HOME/bin/mstrcmd -f mstr_command_manager.scp \
      -n YOUR_IS_SERVER -u Administrator -p Password \
      -o command_manager_output.txt

  Edit the script to replace <YOUR_IS_HOSTNAME> and <YOUR_PROJECT_NAME>
  with actual values before running.


LOGIN MODES
────────────
  --login-mode 1   Standard MicroStrategy authentication (default)
  --login-mode 16  LDAP authentication
  --login-mode 8   Database authentication
  --login-mode 4   Kerberos
  --login-mode 64  SAML


TROUBLESHOOTING
────────────────
  [SSL Error]       Add --no-ssl-verify for self-signed certificates
  [401 Unauthorized] Check username/password and --login-mode
  [Empty CSVs]      Some endpoints require project context — ensure projects
                    are loaded (status: "loaded") on the IS
  [Timeout]         Increase timeout in MSTRClient.__init__ (default: 60s)
  [Zero objects]    Verify the IS has the project loaded and user has access


API REFERENCE
──────────────
  REST API Docs: https://demo.microstrategy.com/MicroStrategyLibrary/api-docs
  Base URL: https://YOUR-SERVER/MicroStrategyLibrary/api


SUPPORT
────────
  This toolkit was generated for MicroStrategy Cloud migration projects.
  Customize as needed for your environment.
  Tested with MicroStrategy 2021 Update 7 and later.

================================================================================
