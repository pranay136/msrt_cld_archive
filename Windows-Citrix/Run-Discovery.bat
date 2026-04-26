@echo off
REM ============================================================================
REM  Run-Discovery.bat
REM  MicroStrategy Phase 1 Discovery — Windows/Citrix Launcher
REM  Version 2.1 | No Python Required
REM
REM  WHAT THIS DOES:
REM    Runs both Phase 1 scripts:
REM      1. Invoke-MSTRHarvester.ps1     — extracts all metadata (21 CSVs)
REM      2. Invoke-MSTRConnectivityTester.ps1  — tests DB + CMC connectivity
REM
REM  HOW TO USE:
REM    1. Edit the CONFIG section below with your real values
REM    2. Double-click this file, OR right-click > Run as Administrator
REM       (Run as Administrator recommended for ODBC registry access)
REM    3. Output appears in .\discovery_output\ and .\connectivity_results\
REM
REM  AFTER RUNNING:
REM    Feed SUMMARY_REPORT.txt to Claude with:
REM    "Review this MSTR discovery. List top 10 migration risks, project
REM     migration order, and pre-migration actions needed."
REM ============================================================================

echo.
echo ================================================================
echo   MicroStrategy Phase 1 Discovery — Windows/Citrix Edition
echo ================================================================
echo.

REM ── CONFIG — EDIT THESE VALUES ──────────────────────────────────
set MSTR_HOST=https://YOUR-ONPREM-SERVER/MicroStrategyLibrary
set MSTR_USER=Administrator
set MSTR_PASS=YourPassword
set LOGIN_MODE=1
REM   Login modes: 1=Standard  16=LDAP  64=SAML  4=Kerberos

set CMC_HOST=your-cloud-mstr.microstrategy.com
set CMC_PORT=34952

set DISCOVERY_OUTPUT=.\discovery_output
set CONNECTIVITY_OUTPUT=.\connectivity_results

REM   Set to your odbc.ini path if you have one, otherwise leave blank
set ODBC_FILE=

REM   Set to 1 to skip SSL cert check (self-signed certs)
set NO_SSL=0

REM   Set to 1 to harvest ALL projects (default harvests first 3)
set ALL_PROJECTS=1

REM   Set to 1 to skip ping tests (if ICMP is blocked by firewall)
set SKIP_PING=0
REM ── END CONFIG ──────────────────────────────────────────────────

echo   Target Server : %MSTR_HOST%
echo   CMC Host      : %CMC_HOST%:%CMC_PORT%
echo   Output        : %DISCOVERY_OUTPUT%
echo.

REM Build optional flags
set HARVESTER_EXTRA=
if "%NO_SSL%"=="1" set HARVESTER_EXTRA=%HARVESTER_EXTRA% -NoSslVerify
if "%ALL_PROJECTS%"=="1" set HARVESTER_EXTRA=%HARVESTER_EXTRA% -AllProjects

set CONN_EXTRA=
if "%SKIP_PING%"=="1" set CONN_EXTRA=%CONN_EXTRA% -SkipPing
if NOT "%ODBC_FILE%"=="" set CONN_EXTRA=%CONN_EXTRA% -OdbcFile "%ODBC_FILE%"

REM ── Step 1: Metadata Harvest ─────────────────────────────────────
echo ================================================================
echo   STEP 1 of 2: Metadata Harvest
echo ================================================================
echo.

powershell.exe -ExecutionPolicy Bypass -NonInteractive -File "%~dp0Invoke-MSTRHarvester.ps1" ^
    -Host "%MSTR_HOST%" ^
    -Username "%MSTR_USER%" ^
    -Password "%MSTR_PASS%" ^
    -OutputDir "%DISCOVERY_OUTPUT%" ^
    -LoginMode %LOGIN_MODE% ^
    %HARVESTER_EXTRA%

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo   [ERROR] Harvester failed with exit code %ERRORLEVEL%
    echo   Check: credentials, server URL, network access to MSTR server
    echo.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo   [OK] Harvest complete. Output in: %DISCOVERY_OUTPUT%
echo.

REM ── Step 2: Connectivity Test ────────────────────────────────────
echo ================================================================
echo   STEP 2 of 2: Connectivity Test
echo ================================================================
echo.

powershell.exe -ExecutionPolicy Bypass -NonInteractive -File "%~dp0Invoke-MSTRConnectivityTester.ps1" ^
    -CMCHost "%CMC_HOST%" ^
    -CMCPort %CMC_PORT% ^
    -OutputDir "%CONNECTIVITY_OUTPUT%" ^
    %CONN_EXTRA%

echo.
echo   [OK] Connectivity test complete. Output in: %CONNECTIVITY_OUTPUT%
echo.

REM ── Summary ──────────────────────────────────────────────────────
echo ================================================================
echo   PHASE 1 DISCOVERY COMPLETE
echo ================================================================
echo.
echo   Files written to:
echo     %DISCOVERY_OUTPUT%\         (21 CSVs + SUMMARY_REPORT.txt)
echo     %CONNECTIVITY_OUTPUT%\      (inventory + results + report)
echo.
echo   NEXT STEPS:
echo   1. Open SUMMARY_REPORT.txt and paste into Claude:
echo      "Review this MSTR discovery. What are the top migration risks?"
echo   2. Open CONNECTIVITY_REPORT.txt — fix any FAIL entries
echo   3. Open 08_datasources.csv — map each DB to its cloud equivalent
echo   4. When ready for migration, update and run Run-Validation.bat
echo.
echo ================================================================
pause
