===============================================================================
 MSTR Report Audit  -  365-Day Usage Classifier (Windows / CMD / No Python)
===============================================================================
 Files shipped:
   MSTR-ReportAudit.ps1        The PowerShell worker script
   Run-MSTRReportAudit.bat     CMD wrapper - edit + run this
   MSTR-ReportAudit-README.txt This file

-------------------------------------------------------------------------------
 WHAT IT DOES
-------------------------------------------------------------------------------
 Scans every MicroStrategy report in your environment and splits them into
 two lists based on whether they were executed in the last 365 days
 (configurable via the DAYS setting):

   reports_to_migrate.csv         -> Used within the window -> MIGRATE to cloud
   reports_to_decommission.csv    -> NOT used within window -> DECOMMISSION
   reports_audit_raw.csv          -> Full joined dataset (both classes)
   reports_audit_summary.txt      -> Executive summary + per-project counts

-------------------------------------------------------------------------------
 PREREQS  (you already have all of these on any Windows 10/11 or Server box)
-------------------------------------------------------------------------------
 * Windows PowerShell 5.1+   (built-in, nothing to install)
 * Network path to EITHER:
     - the Enterprise Manager / Platform Analytics DB  (recommended), OR
     - the MicroStrategy Intelligence Server REST API

 NO Python. NO extra modules. NO pip. NO admin install.

-------------------------------------------------------------------------------
 WHICH MODE SHOULD I USE?
-------------------------------------------------------------------------------
 MODE = EMStats  (RECOMMENDED for 365-day history)
   Queries the Enterprise Manager statistics DB directly.
   This is where MSTR logs every report execution, forever (or however
   long your EM retention is set to). Accurate, fast, complete.

 MODE = REST     (Fallback / inventory only)
   Uses the MSTR REST API /monitors endpoints.
   Limitation: the Monitor API usually only retains the last 30-90 days of
   job history, so reports that only ran 200 days ago may be mis-classified
   as DECOMMISSION. Use this only if you do NOT have EM DB access.

 MODE = Hybrid
   Runs EMStats and uses it as the source of truth. REST enrichment hook
   is present for future extension.

-------------------------------------------------------------------------------
 SETUP  (3 steps)
-------------------------------------------------------------------------------
 1. Put both files (.ps1 and .bat) in the SAME folder.
    Example:  C:\mstr_audit\

 2. Right-click Run-MSTRReportAudit.bat  ->  Edit
    Update the SET lines at the top:

       SET MODE=EMStats
       SET DAYS=365
       SET EM_SERVER=em-sqlserver.yourcompany.com
       SET EM_DATABASE=MSTR_EM
       SET EM_DB_TYPE=SqlServer
       SET EM_USER=                  (blank = use your Windows login)
       SET EM_PASSWORD=

    If your EM DB is Oracle:
       SET EM_DB_TYPE=Oracle
       SET EM_USER=mstr_em_reader
       SET EM_PASSWORD=xxx

    If you must use REST mode:
       SET MODE=REST
       SET MSTR_HOST=https://mstr.yourcompany.com/MicroStrategyLibrary
       SET MSTR_USER=Administrator
       SET MSTR_PASSWORD=xxx
       SET MSTR_LOGIN_MODE=1     (1=Standard, 16=LDAP, 64=SAML)

 3. Open CMD in that folder and run:
       Run-MSTRReportAudit.bat

    ... or just double-click the .bat file.

-------------------------------------------------------------------------------
 OUTPUT
-------------------------------------------------------------------------------
 Results drop into:   .\report_audit_output\

   reports_to_migrate.csv        <- feed to Phase 2 migration
   reports_to_decommission.csv   <- review with business owners
   reports_audit_raw.csv         <- full dataset for pivot tables / Excel
   reports_audit_summary.txt     <- summary for stakeholders / email

 Each CSV has these columns:
   ProjectId, ProjectName, ReportId, ReportName, ReportPath, Owner,
   CreatedDate, ModifiedDate, LastRunDate, RunCountWindow,
   UniqueUsersWindow, Classification, Reason

-------------------------------------------------------------------------------
 DIRECT USAGE (without the BAT wrapper)
-------------------------------------------------------------------------------
 From cmd.exe:

   powershell -ExecutionPolicy Bypass -File MSTR-ReportAudit.ps1 ^
       -Mode EMStats ^
       -Days 365 ^
       -EMServer em-sqlserver.company.com ^
       -EMDatabase MSTR_EM ^
       -EMDbType SqlServer ^
       -OutputDir .\report_audit_output

 For REST mode:

   powershell -ExecutionPolicy Bypass -File MSTR-ReportAudit.ps1 ^
       -Mode REST ^
       -Days 365 ^
       -MstrHost https://mstr.company.com/MicroStrategyLibrary ^
       -MstrUser Administrator ^
       -MstrPassword xxx ^
       -LoginMode 1 ^
       -SkipSslCheck

-------------------------------------------------------------------------------
 ORACLE EM DB NOTES
-------------------------------------------------------------------------------
 If your Enterprise Manager DB is Oracle, you need Oracle.ManagedDataAccess.dll
 on the machine running the script. Download the Oracle ODP.NET Managed Driver
 (free), drop the DLL in the same folder as MSTR-ReportAudit.ps1, and the
 script will pick it up automatically.

 For SQL Server, no extra drivers are needed - System.Data.SqlClient is
 built into .NET Framework on every Windows box.

-------------------------------------------------------------------------------
 TABLE MAPPING  (in case your EM schema is customized)
-------------------------------------------------------------------------------
 Default tables queried:
   LU_OBJECT       (full object catalog; OBJECT_TYPE=3 filters to reports)
   LU_PROJECT      (project lookup for names)
   IS_REPORT_STATS (one row per report execution; REPORT_TYPE=3 = Report)

 These are the standard Enterprise Manager / Platform Analytics warehouse
 table names. If your warehouse uses a different schema (older EM builds
 use IS_PERF_DATA_FACT etc.), edit the SQL inside Invoke-EMStatsAudit.

-------------------------------------------------------------------------------
 NEXT STEPS
-------------------------------------------------------------------------------
 1. Open reports_audit_summary.txt - share the scorecard with stakeholders.
 2. Send reports_to_decommission.csv to business owners for sign-off.
 3. Feed reports_to_migrate.csv to the Phase 2 migration packager
    (mstr_package_migrator.py or Command Manager EXPORT script).
 4. After cloud migration, re-run the validator (mstr_validator.py) using
    the MIGRATE list as the expected baseline.

 Author : Pranay (pranay136@gmail.com)
 Version: 1.0  -  April 2026
===============================================================================
