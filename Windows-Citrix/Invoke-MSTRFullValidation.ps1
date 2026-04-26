<#
================================================================================
  Invoke-MSTRFullValidation.ps1
  MicroStrategy Full Validation Orchestrator — Windows / Citrix Edition
  Version 2.1 | PowerShell 5.1+ | No Python Required

  PURPOSE:
    Phase 3 — Runs all three validation checks in a single command:
      1. Re-harvests the cloud IS  (Invoke-MSTRHarvester.ps1)
      2. Diffs cloud vs on-prem    (Invoke-MSTRValidator.ps1)
      3. Tests DB connectivity     (Invoke-MSTRConnectivityTester.ps1)
      4. Produces MASTER_VALIDATION_REPORT.txt combining all results

    Functionally equivalent to full_validation_runner.py but native Windows.
    All scripts must be in the SAME directory as this script.

  USAGE:
    .\Invoke-MSTRFullValidation.ps1 `
        -BaselineDir  ".\discovery_output" `
        -CloudHost    "https://cloud-mstr.company.com/MicroStrategyLibrary" `
        -CloudUser    "Administrator" `
        -CloudPass    "YourCloudPassword" `
        -CMCHost      "cloud-mstr.company.com" `
        -CMCPort      34952 `
        -OutputDir    ".\full_validation"

  OPTIONAL:
    -OdbcFile       "C:\MSTR\odbc.ini"    # If you have an odbc.ini file
    -LoginMode      16                     # 1=Standard 16=LDAP 64=SAML
    -SkipPing                              # Skip ICMP if firewall blocks it
    -NoSslVerify                           # Skip SSL cert check
    -SkipHarvest                           # Use existing cloud_discovery dir
    -SkipConnectivity                      # Skip DB connectivity tests

  AUTHOR: MicroStrategy Admin Automation Toolkit (Windows/Citrix Port)
  VERSION: 2.1 — Citrix/Windows Native
================================================================================
#>

[CmdletBinding()]
param(
    # Cloud IS credentials
    [Parameter(Mandatory=$true)]
    [string]$CloudHost,

    [Parameter(Mandatory=$true)]
    [string]$CloudUser,

    [Parameter(Mandatory=$true)]
    [string]$CloudPass,

    [ValidateSet(1,4,8,16,64)]
    [int]$LoginMode = 1,

    [switch]$NoSslVerify,

    # Baseline (on-prem harvest from Phase 1)
    [Parameter(Mandatory=$true)]
    [string]$BaselineDir,

    # CMC connectivity
    [Parameter(Mandatory=$true)]
    [string]$CMCHost,

    [int]$CMCPort = 34952,

    # ODBC
    [string]$OdbcFile = "",

    [switch]$SkipPing,

    # Output
    [string]$OutputDir = ".\full_validation",

    # Skip flags for re-runs
    [switch]$SkipHarvest,
    [switch]$SkipConnectivity
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"
$Script:StartTime = Get-Date

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

function Write-Step {
    param([string]$Num, [string]$Title)
    Write-Host ""
    Write-Host "  ┌─────────────────────────────────────────────────────" -ForegroundColor Cyan
    Write-Host "  │  Step $Num : $Title" -ForegroundColor Cyan
    Write-Host "  └─────────────────────────────────────────────────────" -ForegroundColor Cyan
}

function Invoke-StepScript {
    param(
        [string]$ScriptName,
        [string[]]$Arguments,
        [int]$TimeoutSec = 1800
    )

    $scriptPath = Join-Path $PSScriptRoot $ScriptName
    if (-not (Test-Path $scriptPath)) {
        Write-Warning "  [SKIP] $ScriptName not found at $scriptPath"
        return @{ Status="SKIP"; Output="Script not found"; ElapsedSec=0 }
    }

    Write-Host "  CMD: powershell.exe -ExecutionPolicy Bypass -File `"$scriptPath`" $($Arguments -join ' ')" -ForegroundColor Gray
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    try {
        $job = Start-Job -ScriptBlock {
            param($path, $args_)
            & powershell.exe -ExecutionPolicy Bypass -NonInteractive -File $path @args_ 2>&1
        } -ArgumentList $scriptPath, $Arguments

        $completed = Wait-Job $job -Timeout $TimeoutSec
        $sw.Stop()

        if ($null -eq $completed) {
            Stop-Job $job
            Remove-Job $job -Force
            return @{ Status="TIMEOUT"; Output="Timed out after ${TimeoutSec}s"; ElapsedSec=$sw.Elapsed.TotalSeconds }
        }

        $output = Receive-Job $job
        $state  = $job.State
        Remove-Job $job -Force

        # Print last several lines of output
        $outputStr = $output -join "`n"
        $lines = $outputStr -split "`n"
        foreach ($line in ($lines | Select-Object -Last 8)) {
            Write-Host "  │  $line" -ForegroundColor Gray
        }

        $status = if ($state -eq "Completed") { "PASS" } else { "FAIL" }
        return @{ Status=$status; Output=$outputStr; ElapsedSec=[Math]::Round($sw.Elapsed.TotalSeconds,1) }

    } catch {
        $sw.Stop()
        Write-Warning "  [ERR] $($_.Exception.Message)"
        return @{ Status="FAIL"; Output=$_.Exception.Message; ElapsedSec=[Math]::Round($sw.Elapsed.TotalSeconds,1) }
    }
}

# ─────────────────────────────────────────────────────────────
# READ DIFF SUMMARY
# ─────────────────────────────────────────────────────────────

function Read-DiffSummary {
    param([string]$DiffReportPath)
    $counts = @{ CRITICAL=0; HIGH=0; MEDIUM=0; WARNING=0; INFO=0; OK=0 }
    $status = @{ MISSING=0; CHANGED=0; EXTRA=0; MATCH=0 }
    $total  = 0

    if (-not (Test-Path $DiffReportPath)) {
        return @{ Severity=$counts; Status=$status; Total=0 }
    }

    Import-Csv $DiffReportPath -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
        $sev = $_.severity.ToUpper()
        $sta = $_.status.ToUpper()
        if ($counts.ContainsKey($sev)) { $counts[$sev]++ }
        if ($status.ContainsKey($sta)) { $status[$sta]++ }
        $total++
    }
    return @{ Severity=$counts; Status=$status; Total=$total }
}

function Read-ConnectivitySummary {
    param([string]$ConnResultsPath)
    $total   = 0; $tcpOpen = 0; $tcpFail = 0

    if (-not (Test-Path $ConnResultsPath)) {
        return @{ Total=0; TcpOpen=0; TcpFail=0 }
    }

    Import-Csv $ConnResultsPath -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
        $total++
        if ($_.tcp_port_status -eq "OPEN") { $tcpOpen++ }
        elseif ($_.tcp_port_status -in @("CLOSED","TIMEOUT","DNS_FAIL","NO_ROUTE","ERROR")) { $tcpFail++ }
    }
    return @{ Total=$total; TcpOpen=$tcpOpen; TcpFail=$tcpFail }
}

# ─────────────────────────────────────────────────────────────
# MASTER REPORT GENERATOR
# ─────────────────────────────────────────────────────────────

function Write-MasterReport {
    param(
        [hashtable[]]$StepResults,
        [hashtable]$DiffSummary,
        [hashtable]$ConnSummary,
        [string]$ReportTime,
        [string]$CloudHarvestDir,
        [string]$DiffReportPath
    )

    $path = Join-Path $OutputDir "MASTER_VALIDATION_REPORT.txt"
    $sep  = "=" * 80
    $thin = "-" * 80
    $out  = [System.Collections.Generic.List[string]]::new()

    $elapsed = [Math]::Round(((Get-Date) - $Script:StartTime).TotalSeconds)

    # Overall verdict
    $critical = $DiffSummary.Severity.CRITICAL
    $high     = $DiffSummary.Severity.HIGH
    $connFail = $ConnSummary.TcpFail

    $step1 = ($StepResults | Where-Object { $_.Step -eq 1 }).Status
    $step2 = ($StepResults | Where-Object { $_.Step -eq 2 }).Status
    $step3 = ($StepResults | Where-Object { $_.Step -eq 3 }).Status

    if ($critical -gt 0 -or $step2 -eq "FAIL") {
        $verdict = "FAIL — DO NOT GO LIVE. Resolve CRITICAL issues first."
        $vi      = "[!!]"
    } elseif ($connFail -gt 0 -or $high -gt 5) {
        $verdict = "CONDITIONAL PASS — Review HIGH issues and connectivity failures."
        $vi      = "[! ]"
    } elseif ($high -gt 0 -or $DiffSummary.Severity.MEDIUM -gt 0) {
        $verdict = "CONDITIONAL PASS — Minor issues found. Review before go-live."
        $vi      = "[~ ]"
    } else {
        $verdict = "PASS — Migration fully validated. Ready for go-live."
        $vi      = "[OK]"
    }

    $out.Add($sep)
    $out.Add("  MICROSTRATEGY MIGRATION — MASTER VALIDATION REPORT")
    $out.Add("  Generated by Invoke-MSTRFullValidation.ps1 (Windows/Citrix Edition v2.1)")
    $out.Add($sep)
    $out.Add("  Cloud IS         : $CloudHost")
    $out.Add("  CMC Host         : $CMCHost : $CMCPort")
    $out.Add("  On-Prem Baseline : $BaselineDir")
    $out.Add("  Report Time      : $ReportTime")
    $out.Add("  Total Run Time   : ${elapsed}s")
    $out.Add($sep)

    # Section 1: Overall verdict
    $out.Add(""); $out.Add($sep); $out.Add("  1. OVERALL VALIDATION VERDICT"); $out.Add($sep)
    $out.Add("  $vi $verdict")
    $out.Add("")
    $out.Add("  {0,-46} {1}" -f "Cloud Harvest (Step 1)",$step1)
    $out.Add("  {0,-46} {1}" -f "Metadata Diff Analysis (Step 2)",$step2)
    $out.Add("  {0,-46} {1}" -f "Connectivity Test (Step 3)",$step3)

    # Section 2: Diff summary
    $out.Add(""); $out.Add($sep); $out.Add("  2. METADATA DIFF SUMMARY"); $out.Add($sep)
    $out.Add("  {0,-46} {1}" -f "Total records compared",$DiffSummary.Total)
    $out.Add("  {0,-46} {1}" -f "CRITICAL issues (blocks go-live)",$critical)
    $out.Add("  {0,-46} {1}" -f "HIGH issues (degrades experience)",$high)
    $out.Add("  {0,-46} {1}" -f "MEDIUM issues (minor)",$DiffSummary.Severity.MEDIUM)
    $out.Add("  {0,-46} {1}" -f "MATCH (fully validated)",$DiffSummary.Severity.OK)
    $out.Add("  {0,-46} {1}" -f "MISSING objects (not in cloud)",$DiffSummary.Status.MISSING)
    $out.Add("  {0,-46} {1}" -f "CHANGED objects (field differences)",$DiffSummary.Status.CHANGED)
    $out.Add("  {0,-46} {1}" -f "EXTRA objects (cloud only)",$DiffSummary.Status.EXTRA)
    if (Test-Path $DiffReportPath) {
        $out.Add("")
        $out.Add("  Full diff: $DiffReportPath")
    }

    # Section 3: Connectivity summary
    $out.Add(""); $out.Add($sep); $out.Add("  3. NETWORK CONNECTIVITY SUMMARY"); $out.Add($sep)
    $out.Add("  {0,-46} {1}" -f "DB Connections tested",$ConnSummary.Total)
    $out.Add("  {0,-46} {1}" -f "TCP Port OPEN (reachable)",$ConnSummary.TcpOpen)
    $out.Add("  {0,-46} {1}" -f "TCP Port FAILURES",$ConnSummary.TcpFail)
    if ($ConnSummary.Total -gt 0) {
        $pct = [Math]::Round(100 * $ConnSummary.TcpOpen / $ConnSummary.Total)
        $out.Add("  {0,-46} {1}%" -f "Connectivity success rate",$pct)
    }
    $out.Add("")
    if ($connFail -eq 0 -and $ConnSummary.Total -gt 0) {
        $out.Add("  [PASS] All DB connections are reachable from the Citrix/CMC server.")
    } elseif ($ConnSummary.Total -eq 0) {
        $out.Add("  [SKIP] Connectivity test was skipped or found no DSN entries.")
    } else {
        $out.Add("  [FAIL] $connFail connection(s) unreachable — see CONNECTIVITY_REPORT.txt for details.")
    }

    # Section 4: Step execution log
    $out.Add(""); $out.Add($sep); $out.Add("  4. STEP EXECUTION LOG"); $out.Add($sep)
    foreach ($sr in $StepResults) {
        $icon = switch ($sr.Status) {
            "PASS" { "[PASS]" }; "FAIL" { "[FAIL]" }; "SKIP" { "[SKIP]" }
            default { "[????]" }
        }
        $out.Add("  $icon  Step $($sr.Step): $($sr.Name) ($($sr.ElapsedSec)s)")
    }

    # Section 5: Output files
    $out.Add(""); $out.Add($sep); $out.Add("  5. OUTPUT FILES"); $out.Add($sep)
    Get-ChildItem $OutputDir -Recurse -File | Sort-Object FullName | ForEach-Object {
        $rel = $_.FullName.Replace($OutputDir, "").TrimStart("\")
        $out.Add("  $rel")
    }

    # Section 6: Sign-off
    $out.Add(""); $out.Add($sep); $out.Add("  6. MIGRATION SIGN-OFF"); $out.Add($sep)
    $out.Add("  Validation Result     : $verdict")
    $out.Add("  Validated By          : _______________________________")
    $out.Add("  Validation Date       : $ReportTime")
    $out.Add("  Cloud Environment     : $CloudHost")
    $out.Add("")
    $out.Add("  End User Acceptance:")
    $out.Add("  Accepted By           : _______________________________")
    $out.Add("  Acceptance Date       : _______________________________")

    # Section 7: AI prompt
    $out.Add(""); $out.Add($sep); $out.Add("  7. AI ANALYSIS — NEXT STEP"); $out.Add($sep)
    $out.Add("  Feed DIFF_REPORT.csv to Claude with this prompt:")
    $out.Add("")
    $out.Add("  'Review this MicroStrategy migration validation diff. For each CRITICAL")
    $out.Add("   and HIGH item, provide the exact fix: REST API call, Command Manager")
    $out.Add("   command, or admin action. Start with a go/no-go recommendation.")
    $out.Add("   Format output as: Issue | Impact | Exact Fix | Time to Resolve.'")
    $out.Add("")
    $out.Add($sep)
    $out.Add("  Report generated : $ReportTime | Run time: ${elapsed}s")
    $out.Add($sep)

    $out | Out-File -FilePath $path -Encoding UTF8
    Write-Host "  [OK] MASTER_VALIDATION_REPORT.txt" -ForegroundColor Green
    return $path
}

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "=" * 72 -ForegroundColor Cyan
Write-Host "  Invoke-MSTRFullValidation.ps1 — Windows/Citrix Edition v2.1" -ForegroundColor Cyan
Write-Host "  Cloud Host   : $CloudHost" -ForegroundColor Cyan
Write-Host "  CMC Host     : $CMCHost : $CMCPort" -ForegroundColor Cyan
Write-Host "  Baseline     : $BaselineDir" -ForegroundColor Cyan
Write-Host "  Output       : $OutputDir" -ForegroundColor Cyan
Write-Host "=" * 72 -ForegroundColor Cyan

# Validate baseline
if (-not (Test-Path $BaselineDir)) {
    Write-Error "Baseline directory not found: $BaselineDir"
    Write-Error "Run Invoke-MSTRHarvester.ps1 against your on-prem IS first."
    exit 1
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$reportTime       = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$cloudHarvestDir  = Join-Path $OutputDir "cloud_discovery"
$diffDir          = Join-Path $OutputDir "diff_results"
$connectivityDir  = Join-Path $OutputDir "connectivity_results"
$diffReportPath   = Join-Path $diffDir "DIFF_REPORT.csv"
$connResultsPath  = Join-Path $connectivityDir "connectivity_results.csv"

$stepResults = [System.Collections.Generic.List[hashtable]]::new()

# ── Step 1: Harvest Cloud IS ──────────────────────────────────
Write-Step -Num "1/3" -Title "Harvesting Cloud IS Metadata"

if ($SkipHarvest -and (Test-Path $cloudHarvestDir)) {
    Write-Host "  [SKIP] -SkipHarvest flag set. Using existing: $cloudHarvestDir" -ForegroundColor Yellow
    $stepResults.Add(@{ Step=1; Name="Cloud Harvest"; Status="SKIP"; ElapsedSec=0 })
} else {
    New-Item -ItemType Directory -Force -Path $cloudHarvestDir | Out-Null
    $harvArgs = @(
        "-Host", "`"$CloudHost`"",
        "-Username", "`"$CloudUser`"",
        "-Password", "`"$CloudPass`"",
        "-OutputDir", "`"$cloudHarvestDir`"",
        "-AllProjects",
        "-LoginMode", $LoginMode
    )
    if ($NoSslVerify) { $harvArgs += "-NoSslVerify" }

    $sr1 = Invoke-StepScript -ScriptName "Invoke-MSTRHarvester.ps1" -Arguments $harvArgs -TimeoutSec 1800
    $stepResults.Add(@{ Step=1; Name="Cloud Harvest"; Status=$sr1.Status; ElapsedSec=$sr1.ElapsedSec })

    if ($sr1.Status -ne "PASS" -and -not (Test-Path (Join-Path $cloudHarvestDir "02_projects.csv"))) {
        Write-Warning "  Cloud harvest failed and no prior cloud_discovery data exists."
        Write-Warning "  Diff analysis will still run against whatever data is available."
    }
}

# ── Step 2: Diff Analysis ─────────────────────────────────────
Write-Step -Num "2/3" -Title "Running Metadata Diff Analysis"
New-Item -ItemType Directory -Force -Path $diffDir | Out-Null

$valArgs = @(
    "-Baseline", "`"$BaselineDir`"",
    "-Target",   "`"$cloudHarvestDir`"",
    "-OutputDir","`"$diffDir`""
)
$sr2 = Invoke-StepScript -ScriptName "Invoke-MSTRValidator.ps1" -Arguments $valArgs -TimeoutSec 600
$stepResults.Add(@{ Step=2; Name="Metadata Diff"; Status=$sr2.Status; ElapsedSec=$sr2.ElapsedSec })

# ── Step 3: Connectivity Test ─────────────────────────────────
Write-Step -Num "3/3" -Title "Testing DB Connectivity"
New-Item -ItemType Directory -Force -Path $connectivityDir | Out-Null

if ($SkipConnectivity) {
    Write-Host "  [SKIP] -SkipConnectivity flag set." -ForegroundColor Yellow
    $stepResults.Add(@{ Step=3; Name="Connectivity Test"; Status="SKIP"; ElapsedSec=0 })
} else {
    $connArgs = @(
        "-CMCHost",   "`"$CMCHost`"",
        "-CMCPort",   $CMCPort,
        "-OutputDir", "`"$connectivityDir`""
    )
    if ($OdbcFile) { $connArgs += "-OdbcFile"; $connArgs += "`"$OdbcFile`"" }
    if ($SkipPing) { $connArgs += "-SkipPing" }

    $sr3 = Invoke-StepScript -ScriptName "Invoke-MSTRConnectivityTester.ps1" -Arguments $connArgs -TimeoutSec 600
    $stepResults.Add(@{ Step=3; Name="Connectivity Test"; Status=$sr3.Status; ElapsedSec=$sr3.ElapsedSec })
}

# ── Generate Master Report ────────────────────────────────────
Write-Host ""
Write-Host "  Generating MASTER_VALIDATION_REPORT.txt..." -ForegroundColor Cyan

$diffSummary = Read-DiffSummary -DiffReportPath $diffReportPath
$connSummary = Read-ConnectivitySummary -ConnResultsPath $connResultsPath

$masterPath = Write-MasterReport `
    -StepResults    $stepResults `
    -DiffSummary    $diffSummary `
    -ConnSummary    $connSummary `
    -ReportTime     $reportTime `
    -CloudHarvestDir $cloudHarvestDir `
    -DiffReportPath $diffReportPath

# ── Final Summary ─────────────────────────────────────────────
$passCount = ($stepResults | Where-Object { $_.Status -eq "PASS" }).Count
$failCount = ($stepResults | Where-Object { $_.Status -eq "FAIL" }).Count
$skipCount = ($stepResults | Where-Object { $_.Status -eq "SKIP" }).Count
$elapsed   = [Math]::Round(((Get-Date) - $Script:StartTime).TotalSeconds)
$critical  = $diffSummary.Severity.CRITICAL
$color     = if ($critical -gt 0 -or $failCount -gt 0) { "Red" } elseif ($diffSummary.Severity.HIGH -gt 0) { "Yellow" } else { "Green" }

Write-Host ""
Write-Host "=" * 72 -ForegroundColor $color
Write-Host "  FULL VALIDATION COMPLETE in ${elapsed}s" -ForegroundColor Cyan
Write-Host "  Steps PASS    : $passCount"
Write-Host "  Steps FAIL    : $failCount"
Write-Host "  Steps SKIP    : $skipCount"
Write-Host "  CRITICAL diffs: $critical"
Write-Host "  HIGH diffs    : $($diffSummary.Severity.HIGH)"
Write-Host "  DB Failures   : $($connSummary.TcpFail)"
Write-Host ""
Write-Host "  All output    : $(Resolve-Path $OutputDir)"
Write-Host "  Master report : $masterPath"
Write-Host ""
Write-Host "  NEXT: Feed MASTER_VALIDATION_REPORT.txt + DIFF_REPORT.csv" -ForegroundColor Cyan
Write-Host "        to Claude for classified issues and exact remediation." -ForegroundColor Cyan
Write-Host "=" * 72 -ForegroundColor $color

exit $(if ($failCount -gt 0 -or $critical -gt 0) { 1 } else { 0 })
