@echo off
REM ============================================================================
REM  Run-Validation.bat
REM  MicroStrategy Phase 3 Full Validation — Windows/Citrix Launcher
REM  Version 2.1 | No Python Required
REM
REM  WHAT THIS DOES:
REM    Orchestrates all Phase 3 validation in one click:
REM      1. Re-harvests the cloud IS metadata
REM      2. Diffs cloud metadata vs your on-prem baseline
REM      3. Re-tests DB + CMC connectivity
REM      4. Generates MASTER_VALIDATION_REPORT.txt
REM
REM  PREREQUISITES:
REM    - Phase 1 must be complete (discovery_output\ must exist)
REM    - Cloud IS must be live and accessible
REM
REM  HOW TO USE:
REM    1. Edit the CONFIG section below
REM    2. Right-click > Run as Administrator (recommended)
REM    3. Output appears in .\full_validation\
REM
REM  AFTER RUNNING:
REM    Feed MASTER_VALIDATION_REPORT.txt + DIFF_REPORT.csv to Claude:
REM    "Review this MSTR migration diff. For each CRITICAL and HIGH issue,
REM     provide the exact Command Manager command or REST API call to fix it."
REM ============================================================================

echo.
echo ================================================================
echo   MicroStrategy Phase 3 Validation — Windows/Citrix Edition
echo ================================================================
echo.

REM ── CONFIG — EDIT THESE VALUES ──────────────────────────────────
REM   On-prem baseline from Phase 1
set BASELINE_DIR=.\discovery_output

REM   Cloud IS credentials
set CLOUD_HOST=https://YOUR-CLOUD-MSTR/MicroStrategyLibrary
set CLOUD_USER=Administrator
set CLOUD_PASS=YourCloudPassword
set LOGIN_MODE=1
REM   Login modes: 1=Standard  16=LDAP  64=SAML  4=Kerberos

REM   Cloud CMC host
set CMC_HOST=your-cloud-mstr.microstrategy.com
set CMC_PORT=34952

REM   Output directory
set OUTPUT_DIR=.\full_validation

REM   Set path to odbc.ini if you have one, otherwise leave blank
set ODBC_FILE=

REM   Optional flags (set to 1 to enable)
set NO_SSL=0
set SKIP_PING=0
set SKIP_HARVEST=0
REM   SKIP_HARVEST=1 reuses existing cloud_discovery without re-harvesting
REM   Useful for re-running the diff without connecting to cloud IS again
REM ── END CONFIG ──────────────────────────────────────────────────

REM Validate baseline exists
if not exist "%BASELINE_DIR%" (
    echo   [ERROR] Baseline directory not found: %BASELINE_DIR%
    echo   Run Run-Discovery.bat against your on-prem IS first.
    echo.
    pause
    exit /b 1
)

echo   Baseline     : %BASELINE_DIR%
echo   Cloud IS     : %CLOUD_HOST%
echo   CMC Host     : %CMC_HOST%:%CMC_PORT%
echo   Output       : %OUTPUT_DIR%
echo.

REM Build optional flags
set EXTRA_FLAGS=
if "%NO_SSL%"=="1"          set EXTRA_FLAGS=%EXTRA_FLAGS% -NoSslVerify
if "%SKIP_PING%"=="1"       set EXTRA_FLAGS=%EXTRA_FLAGS% -SkipPing
if "%SKIP_HARVEST%"=="1"    set EXTRA_FLAGS=%EXTRA_FLAGS% -SkipHarvest
if NOT "%ODBC_FILE%"==""    set EXTRA_FLAGS=%EXTRA_FLAGS% -OdbcFile "%ODBC_FILE%"

REM ── Run Full Validation ───────────────────────────────────────────
echo ================================================================
echo   Running Full Validation (all 3 steps)...
echo ================================================================
echo.

powershell.exe -ExecutionPolicy Bypass -NonInteractive -File "%~dp0Invoke-MSTRFullValidation.ps1" ^
    -BaselineDir "%BASELINE_DIR%" ^
    -CloudHost "%CLOUD_HOST%" ^
    -CloudUser "%CLOUD_USER%" ^
    -CloudPass "%CLOUD_PASS%" ^
    -CMCHost "%CMC_HOST%" ^
    -CMCPort %CMC_PORT% ^
    -OutputDir "%OUTPUT_DIR%" ^
    -LoginMode %LOGIN_MODE% ^
    %EXTRA_FLAGS%

set EXIT_CODE=%ERRORLEVEL%

echo.
echo ================================================================
if %EXIT_CODE% EQU 0 (
    echo   [PASS] VALIDATION COMPLETE — No critical issues found
) else (
    echo   [WARN] VALIDATION COMPLETE — Issues found, review reports
)
echo ================================================================
echo.
echo   Output directory : %OUTPUT_DIR%\
echo.
echo   Key files to review:
echo     %OUTPUT_DIR%\MASTER_VALIDATION_REPORT.txt    (overall verdict)
echo     %OUTPUT_DIR%\diff_results\DIFF_REPORT.csv    (field-by-field diff)
echo     %OUTPUT_DIR%\diff_results\VALIDATION_REPORT.txt
echo     %OUTPUT_DIR%\connectivity_results\CONNECTIVITY_REPORT.txt
echo.
echo   NEXT STEPS:
echo   1. Open MASTER_VALIDATION_REPORT.txt for the go/no-go verdict
echo   2. Paste DIFF_REPORT.csv into Claude for remediation steps
echo   3. Fix all CRITICAL issues before go-live
echo   4. Re-run this bat to confirm issues resolved
echo.
pause
exit /b %EXIT_CODE%
