@ECHO OFF
REM =============================================================================
REM Run-MSTRReportAudit.bat
REM -----------------------------------------------------------------------------
REM CMD wrapper for MSTR-ReportAudit.ps1. Edit the SET lines below to match your
REM environment, then double-click this file or run it from cmd.
REM No Python required. Uses Windows built-in PowerShell.
REM =============================================================================

SETLOCAL ENABLEDELAYEDEXPANSION

REM ---- EDIT THESE VALUES ------------------------------------------------------
SET MODE=EMStats
SET DAYS=365
SET OUTPUT_DIR=%~dp0report_audit_output

REM -- Enterprise Manager DB (required for EMStats / Hybrid modes) --
SET EM_SERVER=em-sqlserver.company.com
SET EM_DATABASE=MSTR_EM
SET EM_DB_TYPE=SqlServer
SET EM_USER=
SET EM_PASSWORD=
SET EM_PORT=0

REM -- MicroStrategy REST API (required for REST / Hybrid modes) --
SET MSTR_HOST=https://mstr.company.com/MicroStrategyLibrary
SET MSTR_USER=Administrator
SET MSTR_PASSWORD=ChangeMe
SET MSTR_LOGIN_MODE=1

REM -- Misc --
SET SKIP_SSL=1
REM -----------------------------------------------------------------------------

SET SCRIPT=%~dp0MSTR-ReportAudit.ps1
IF NOT EXIST "%SCRIPT%" (
    ECHO [ERROR] Cannot find %SCRIPT%
    EXIT /B 1
)

ECHO.
ECHO ============================================================
ECHO  MSTR Report Audit  -  Mode: %MODE%  -  Window: %DAYS% days
ECHO ============================================================
ECHO.

SET PS_ARGS=-Mode %MODE% -Days %DAYS% -OutputDir "%OUTPUT_DIR%"

IF /I "%MODE%"=="EMStats" (
    SET PS_ARGS=!PS_ARGS! -EMServer "%EM_SERVER%" -EMDatabase "%EM_DATABASE%" -EMDbType "%EM_DB_TYPE%" -EMPort %EM_PORT%
    IF NOT "%EM_USER%"=="" SET PS_ARGS=!PS_ARGS! -EMUser "%EM_USER%" -EMPassword "%EM_PASSWORD%"
)
IF /I "%MODE%"=="REST" (
    SET PS_ARGS=!PS_ARGS! -MstrHost "%MSTR_HOST%" -MstrUser "%MSTR_USER%" -MstrPassword "%MSTR_PASSWORD%" -LoginMode %MSTR_LOGIN_MODE%
)
IF /I "%MODE%"=="Hybrid" (
    SET PS_ARGS=!PS_ARGS! -EMServer "%EM_SERVER%" -EMDatabase "%EM_DATABASE%" -EMDbType "%EM_DB_TYPE%" -EMPort %EM_PORT%
    IF NOT "%EM_USER%"=="" SET PS_ARGS=!PS_ARGS! -EMUser "%EM_USER%" -EMPassword "%EM_PASSWORD%"
    SET PS_ARGS=!PS_ARGS! -MstrHost "%MSTR_HOST%" -MstrUser "%MSTR_USER%" -MstrPassword "%MSTR_PASSWORD%" -LoginMode %MSTR_LOGIN_MODE%
)
IF "%SKIP_SSL%"=="1" SET PS_ARGS=!PS_ARGS! -SkipSslCheck

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" !PS_ARGS!

IF ERRORLEVEL 1 (
    ECHO.
    ECHO [FAIL] Audit did not complete. Scroll up for the error.
    EXIT /B 1
)

ECHO.
ECHO [OK] Audit complete. Output folder: %OUTPUT_DIR%
ECHO.
START "" "%OUTPUT_DIR%"
ENDLOCAL
