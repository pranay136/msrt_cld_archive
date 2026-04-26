<#
================================================================================
  Invoke-MSTRValidator.ps1
  MicroStrategy Migration Validator — Windows / Citrix Edition
  Version 2.1 | PowerShell 5.1+ | No Python Required

  PURPOSE:
    Phase 3 — Compares on-prem (baseline) and cloud (target) harvest directories
    field-by-field. Produces a severity-classified DIFF_REPORT.csv and a
    sign-off-ready VALIDATION_REPORT.txt.
    Functionally equivalent to mstr_validator.py but native Windows PowerShell.

  USAGE:
    .\Invoke-MSTRValidator.ps1 `
        -Baseline ".\discovery_output" `
        -Target   ".\cloud_discovery" `
        -OutputDir ".\validation_results"

  OUTPUT:
    DIFF_REPORT.csv          Every comparison: severity, status, domain, record, fix
    VALIDATION_REPORT.txt    Pass/fail scorecard + sign-off block + remediation steps

  SEVERITY CLASSIFICATION:
    CRITICAL  — blocks go-live (missing security filters, DB connections, projects)
    HIGH      — degrades experience (missing schedules, subscriptions, groups)
    MEDIUM    — minor impact (metadata field changes, path differences)
    INFO      — expected differences (version fields, date stamps)
    OK        — fully matched

  AUTHOR: MicroStrategy Admin Automation Toolkit (Windows/Citrix Port)
  VERSION: 2.1 — Citrix/Windows Native
================================================================================
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$Baseline,

    [Parameter(Mandatory=$true)]
    [string]$Target,

    [string]$OutputDir = ".\validation_results"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# ─────────────────────────────────────────────────────────────
# COMPARISON CONFIG
# Maps each CSV filename to: key fields, compare fields, missing severity
# ─────────────────────────────────────────────────────────────

$COMPARISON_CONFIG = @(
    @{
        File="01_server_info.csv"; Domain="Server Infrastructure"
        Keys=@("field"); Compare=@("value")
        MissingSev="WARNING"; Desc="Intelligence Server configuration"
    },
    @{
        File="02_projects.csv"; Domain="Projects"
        Keys=@("id"); Compare=@("name","status")
        MissingSev="CRITICAL"; Desc="Project inventory"
    },
    @{
        File="03_users.csv"; Domain="Users"
        Keys=@("id"); Compare=@("username","full_name","enabled","login_mode_label")
        MissingSev="CRITICAL"; Desc="User accounts"
    },
    @{
        File="04_usergroups.csv"; Domain="User Groups"
        Keys=@("id"); Compare=@("name","description")
        MissingSev="HIGH"; Desc="User group structure"
    },
    @{
        File="05_group_membership.csv"; Domain="Group Memberships"
        Keys=@("group_id","member_id"); Compare=@("group_name","member_name","member_type")
        MissingSev="HIGH"; Desc="User-to-group membership assignments"
    },
    @{
        File="06_security_roles.csv"; Domain="Security Roles"
        Keys=@("id"); Compare=@("name","privilege_count")
        MissingSev="HIGH"; Desc="Security role definitions"
    },
    @{
        File="07_security_filters.csv"; Domain="Security Filters"
        Keys=@("id","project_id"); Compare=@("name","owner_name")
        MissingSev="CRITICAL"; Desc="Security filter assignments (access control)"
    },
    @{
        File="08_datasources.csv"; Domain="Database Connections"
        Keys=@("id"); Compare=@("name","db_type","host","database_name")
        MissingSev="CRITICAL"; Desc="Datasource and DB connection definitions"
    },
    @{
        File="09_reports.csv"; Domain="Reports"
        Keys=@("id","project_id"); Compare=@("name","path")
        MissingSev="HIGH"; Desc="Report objects"
    },
    @{
        File="10_documents_dossiers.csv"; Domain="Documents and Dossiers"
        Keys=@("id","project_id"); Compare=@("name","path","object_type_name")
        MissingSev="HIGH"; Desc="Document and Dossier objects"
    },
    @{
        File="11_metrics.csv"; Domain="Metrics"
        Keys=@("id","project_id"); Compare=@("name")
        MissingSev="HIGH"; Desc="Metric definitions"
    },
    @{
        File="12_attributes.csv"; Domain="Attributes"
        Keys=@("id","project_id"); Compare=@("name")
        MissingSev="MEDIUM"; Desc="Attribute definitions"
    },
    @{
        File="13_facts.csv"; Domain="Facts"
        Keys=@("id","project_id"); Compare=@("name")
        MissingSev="MEDIUM"; Desc="Fact definitions"
    },
    @{
        File="14_filters.csv"; Domain="Filters"
        Keys=@("id","project_id"); Compare=@("name")
        MissingSev="MEDIUM"; Desc="Filter definitions"
    },
    @{
        File="15_prompts.csv"; Domain="Prompts"
        Keys=@("id","project_id"); Compare=@("name")
        MissingSev="MEDIUM"; Desc="Prompt definitions"
    },
    @{
        File="16_schedules.csv"; Domain="Schedules"
        Keys=@("id"); Compare=@("name","enabled","schedule_type")
        MissingSev="HIGH"; Desc="Schedule configurations"
    },
    @{
        File="17_subscriptions.csv"; Domain="Subscriptions"
        Keys=@("id","project_id"); Compare=@("name","owner_name","delivery_type","enabled")
        MissingSev="HIGH"; Desc="Subscription delivery configurations"
    },
    @{
        File="19_security_config.csv"; Domain="Security Config"
        Keys=@("setting_category","setting_name"); Compare=@("setting_value")
        MissingSev="HIGH"; Desc="Authentication and security settings"
    },
    @{
        File="20_email_config.csv"; Domain="Email Config"
        Keys=@("setting_name"); Compare=@("value")
        MissingSev="MEDIUM"; Desc="SMTP and email delivery settings"
    },
    @{
        File="21_licenses.csv"; Domain="Licensing"
        Keys=@("license_key"); Compare=@("product","license_type","named_users")
        MissingSev="WARNING"; Desc="License activations"
    }
)

$SEVERITY_ORDER = @{ CRITICAL=0; HIGH=1; MEDIUM=2; WARNING=3; INFO=4; OK=5 }

# ─────────────────────────────────────────────────────────────
# CSV COMPARISON ENGINE
# ─────────────────────────────────────────────────────────────

function Build-Index {
    param([object[]]$Rows, [string[]]$KeyFields)

    $index = @{}
    foreach ($row in $Rows) {
        $keyParts = $KeyFields | ForEach-Object { [string]$row.$_ }
        $key = $keyParts -join "|~|"
        if ($key -replace "\|~\|","" -ne "") {
            $index[$key] = $row
        }
    }
    return $index
}

function Compare-CsvFiles {
    param([hashtable]$Config)

    $basePath   = Join-Path $Baseline $Config.File
    $targPath   = Join-Path $Target   $Config.File
    $keyFields  = $Config.Keys
    $cmpFields  = $Config.Compare
    $missingSev = $Config.MissingSev
    $domain     = $Config.Domain
    $filename   = $Config.File

    if (-not (Test-Path $basePath)) {
        Write-Host "  [SKIP] $filename — not in baseline" -ForegroundColor Gray
        return @()
    }

    $baseRows = @(Import-Csv $basePath -Encoding UTF8 -ErrorAction SilentlyContinue)
    $targRows = if (Test-Path $targPath) {
        @(Import-Csv $targPath -Encoding UTF8 -ErrorAction SilentlyContinue)
    } else { @() }

    $baseIndex = Build-Index -Rows $baseRows -KeyFields $keyFields
    $targIndex = Build-Index -Rows $targRows -KeyFields $keyFields

    $diffs = [System.Collections.Generic.List[PSCustomObject]]::new()

    # Baseline -> Target: find MISSING and CHANGED
    foreach ($key in $baseIndex.Keys) {
        $baseRow  = $baseIndex[$key]
        $keyStr   = ($keyFields | ForEach-Object { "$_=$($baseRow.$_)" }) -join " | "

        if (-not $targIndex.ContainsKey($key)) {
            # Missing in cloud
            $firstName = [string]($baseRow.($cmpFields[0]))
            $diffs.Add([PSCustomObject]@{
                severity        = $missingSev
                status          = "MISSING"
                domain          = $domain
                file            = $filename
                record_key      = $keyStr
                field_name      = "[RECORD]"
                baseline_value  = $firstName.Substring(0,[Math]::Min(100,$firstName.Length))
                target_value    = "MISSING"
                remediation     = "Object '$firstName' not migrated. Re-run migration for this object or use Command Manager IMPORT."
            })
        } else {
            $targRow = $targIndex[$key]
            foreach ($field in $cmpFields) {
                $bVal = [string]$baseRow.$field
                $tVal = [string]$targRow.$field
                if ($bVal -ne $tVal) {
                    $sev = switch ($field) {
                        { $_ -in "enabled","status" }       { "HIGH" }
                        { $_ -in "name","username" }         { "MEDIUM" }
                        default                              { "INFO" }
                    }
                    $bShort = $bVal.Substring(0,[Math]::Min(150,$bVal.Length))
                    $tShort = $tVal.Substring(0,[Math]::Min(150,$tVal.Length))
                    $diffs.Add([PSCustomObject]@{
                        severity        = $sev
                        status          = "CHANGED"
                        domain          = $domain
                        file            = $filename
                        record_key      = $keyStr
                        field_name      = $field
                        baseline_value  = $bShort
                        target_value    = $tShort
                        remediation     = "Field '$field' differs. On-Prem: '$($bVal.Substring(0,[Math]::Min(50,$bVal.Length)))' vs Cloud: '$($tVal.Substring(0,[Math]::Min(50,$tVal.Length)))'. Verify migration."
                    })
                }
            }
        }
    }

    # Target -> Baseline: find EXTRA
    foreach ($key in $targIndex.Keys) {
        if (-not $baseIndex.ContainsKey($key)) {
            $targRow   = $targIndex[$key]
            $keyStr    = ($keyFields | ForEach-Object { "$_=$($targRow.$_)" }) -join " | "
            $firstName = [string]($targRow.($cmpFields[0]))
            $diffs.Add([PSCustomObject]@{
                severity        = "INFO"
                status          = "EXTRA"
                domain          = $domain
                file            = $filename
                record_key      = $keyStr
                field_name      = "[RECORD]"
                baseline_value  = "NOT IN BASELINE"
                target_value    = $firstName.Substring(0,[Math]::Min(100,$firstName.Length))
                remediation     = "Object exists in cloud but not on-prem. Verify this is intentional (may be a new cloud-side object)."
            })
        }
    }

    # If no diffs — record a MATCH
    if ($diffs.Count -eq 0 -and ($baseRows.Count -gt 0 -or $targRows.Count -gt 0)) {
        $diffs.Add([PSCustomObject]@{
            severity        = "OK"
            status          = "MATCH"
            domain          = $domain
            file            = $filename
            record_key      = "$($baseRows.Count) records compared"
            field_name      = "[ALL FIELDS]"
            baseline_value  = "$($baseRows.Count)"
            target_value    = "$($targRows.Count)"
            remediation     = ""
        })
    }

    $issues = @($diffs | Where-Object { $_.status -ne "MATCH" })
    $color  = if ($issues.Count -eq 0) { "Green" } elseif (@($diffs | Where-Object { $_.severity -eq "CRITICAL" }).Count -gt 0) { "Red" } else { "Yellow" }
    Write-Host "  $filename — $($issues.Count) difference(s)" -ForegroundColor $color

    return $diffs
}

# ─────────────────────────────────────────────────────────────
# VALIDATION REPORT GENERATOR
# ─────────────────────────────────────────────────────────────

function Write-ValidationReport {
    param([object[]]$AllDiffs, [string]$ReportTime)

    $path = Join-Path $OutputDir "VALIDATION_REPORT.txt"
    $sep  = "=" * 80
    $thin = "-" * 80
    $out  = [System.Collections.Generic.List[string]]::new()

    # Count by severity and status
    $sevCounts    = @{ CRITICAL=0; HIGH=0; MEDIUM=0; WARNING=0; INFO=0; OK=0 }
    $domainStatus = @{}

    foreach ($d in $AllDiffs) {
        $sev = $d.severity
        $sta = $d.status
        $dom = $d.domain
        if ($sevCounts.ContainsKey($sev)) { $sevCounts[$sev]++ }
        if (-not $domainStatus.ContainsKey($dom)) {
            $domainStatus[$dom] = @{ MATCH=0; MISSING=0; CHANGED=0; EXTRA=0 }
        }
        if ($domainStatus[$dom].ContainsKey($sta)) { $domainStatus[$dom][$sta]++ }
    }

    $critical = $sevCounts.CRITICAL
    $high     = $sevCounts.HIGH
    $medium   = $sevCounts.MEDIUM

    # Verdict
    if ($critical -gt 0) {
        $verdict = "FAIL — CRITICAL ISSUES MUST BE RESOLVED BEFORE GO-LIVE"
        $vi      = "[!!]"
    } elseif ($high -gt 5) {
        $verdict = "CONDITIONAL PASS — HIGH-SEVERITY ISSUES REQUIRE REVIEW"
        $vi      = "[! ]"
    } elseif ($high -gt 0 -or $medium -gt 0) {
        $verdict = "CONDITIONAL PASS — REVIEW AND REMEDIATE FLAGGED ISSUES"
        $vi      = "[~ ]"
    } else {
        $verdict = "PASS — MIGRATION VALIDATED SUCCESSFULLY"
        $vi      = "[OK]"
    }

    $out.Add($sep)
    $out.Add("  MICROSTRATEGY MIGRATION VALIDATION REPORT")
    $out.Add("  Generated by Invoke-MSTRValidator.ps1 (Windows/Citrix Edition v2.1)")
    $out.Add($sep)
    $out.Add("  Baseline (On-Prem) : $(Resolve-Path $Baseline)")
    $out.Add("  Target (Cloud)     : $(Resolve-Path $Target)")
    $out.Add("  Report Time        : $ReportTime")
    $out.Add($sep)

    # Section 1: Verdict
    $out.Add(""); $out.Add($sep); $out.Add("  1. OVERALL VALIDATION VERDICT"); $out.Add($sep)
    $out.Add("  $vi $verdict")
    $out.Add("")
    $out.Add("  {0,-46} {1}" -f "CRITICAL Issues (blocks go-live)", $critical)
    $out.Add("  {0,-46} {1}" -f "HIGH Issues (degrades experience)", $high)
    $out.Add("  {0,-46} {1}" -f "MEDIUM Issues (minor impact)", $medium)
    $out.Add("  {0,-46} {1}" -f "WARNING (informational)", $sevCounts.WARNING)
    $out.Add("  {0,-46} {1}" -f "INFO (expected differences)", $sevCounts.INFO)
    $out.Add("  {0,-46} {1}" -f "MATCH (fully validated)", $sevCounts.OK)

    # Section 2: Scorecard
    $out.Add(""); $out.Add($sep); $out.Add("  2. VALIDATION SCORECARD BY DOMAIN"); $out.Add($sep)
    $out.Add("  {0,-35} {1,7} {2,8} {3,8} {4,7}  {5}" -f "Domain","MATCH","MISSING","CHANGED","EXTRA","Status")
    $out.Add("  $thin")
    foreach ($dom in $domainStatus.Keys | Sort-Object) {
        $c       = $domainStatus[$dom]
        $missing = $c.MISSING
        $changed = $c.CHANGED
        $extra   = $c.EXTRA
        $match   = $c.MATCH
        $status  = if ($missing -gt 0 -or $changed -gt 5) { "FAIL" } elseif ($changed -gt 0 -or $extra -gt 0) { "REVIEW" } else { "PASS" }
        $out.Add("  {0,-35} {1,7} {2,8} {3,8} {4,7}  {5}" -f $dom, $match, $missing, $changed, $extra, $status)
    }

    # Section 3: Critical issues
    $critDiffs = @($AllDiffs | Where-Object { $_.severity -eq "CRITICAL" -and $_.status -ne "MATCH" })
    if ($critDiffs.Count -gt 0) {
        $out.Add(""); $out.Add($sep); $out.Add("  3. CRITICAL ISSUES — MUST FIX BEFORE GO-LIVE"); $out.Add($sep)
        $n = 0
        foreach ($d in $critDiffs | Select-Object -First 50) {
            $n++
            $out.Add("")
            $out.Add("  [$("{0:D2}" -f $n)] Domain    : $($d.domain)")
            $out.Add("       Record    : $($d.record_key)")
            $out.Add("       Status    : $($d.status)")
            $out.Add("       On-Prem   : $($d.baseline_value)")
            $out.Add("       Cloud     : $($d.target_value)")
            $out.Add("       Fix       : $($d.remediation)")
        }
    }

    # Section 4: High issues
    $highDiffs = @($AllDiffs | Where-Object { $_.severity -eq "HIGH" -and $_.status -ne "MATCH" })
    if ($highDiffs.Count -gt 0) {
        $out.Add(""); $out.Add($sep); $out.Add("  4. HIGH SEVERITY ISSUES ($($highDiffs.Count) found)"); $out.Add($sep)
        $n = 0
        foreach ($d in $highDiffs | Select-Object -First 30) {
            $n++
            $key  = if ($d.record_key.Length -gt 50) { $d.record_key.Substring(0,50) } else { $d.record_key }
            $bVal = if ($d.baseline_value.Length -gt 40) { $d.baseline_value.Substring(0,40) } else { $d.baseline_value }
            $tVal = if ($d.target_value.Length -gt 40) { $d.target_value.Substring(0,40) } else { $d.target_value }
            $fix  = if ($d.remediation.Length -gt 100) { $d.remediation.Substring(0,100) } else { $d.remediation }
            $out.Add("  [$("{0:D2}" -f $n)] $($d.domain) | $key | $($d.status)")
            $out.Add("       On-Prem: $bVal  |  Cloud: $tVal")
            $out.Add("       Fix: $fix")
        }
    }

    # Section 5: Medium issues
    $medDiffs = @($AllDiffs | Where-Object { $_.severity -eq "MEDIUM" -and $_.status -ne "MATCH" })
    if ($medDiffs.Count -gt 0) {
        $out.Add(""); $out.Add($sep); $out.Add("  5. MEDIUM SEVERITY ISSUES ($($medDiffs.Count) found — review but not blocking)"); $out.Add($sep)
        $n = 0
        foreach ($d in $medDiffs | Select-Object -First 20) {
            $n++
            $key  = if ($d.record_key.Length -gt 50) { $d.record_key.Substring(0,50) } else { $d.record_key }
            $bVal = if ($d.baseline_value.Length -gt 30) { $d.baseline_value.Substring(0,30) } else { $d.baseline_value }
            $tVal = if ($d.target_value.Length -gt 30) { $d.target_value.Substring(0,30) } else { $d.target_value }
            $out.Add("  [$("{0:D2}" -f $n)] $($d.domain) | $key | $($d.status) | $($d.field_name): '$bVal' -> '$tVal'")
        }
    }

    # Section 6: Passing domains
    $passDomains = $domainStatus.Keys | Where-Object {
        $domainStatus[$_].MISSING -eq 0 -and $domainStatus[$_].CHANGED -eq 0 -and $domainStatus[$_].MATCH -gt 0
    }
    if ($passDomains) {
        $out.Add(""); $out.Add($sep); $out.Add("  6. FULLY VALIDATED DOMAINS (PASS)"); $out.Add($sep)
        foreach ($dom in $passDomains | Sort-Object) { $out.Add("  [PASS] $dom") }
    }

    # Section 7: Sign-off
    $out.Add(""); $out.Add($sep); $out.Add("  7. MIGRATION SIGN-OFF"); $out.Add($sep)
    $out.Add("  Migration Validated By : _______________________________")
    $out.Add("  Date                   : $ReportTime")
    $out.Add("  Environment            : $(Resolve-Path $Target)")
    $out.Add("  Overall Result         : $verdict")
    $out.Add("")
    $out.Add("  End User Acceptance:")
    $out.Add("  Accepted By            : _______________________________")
    $out.Add("  Acceptance Date        : _______________________________")
    $out.Add("  Sign-Off Notes         : _______________________________")

    # Section 8: Next steps
    $out.Add(""); $out.Add($sep); $out.Add("  8. NEXT STEPS"); $out.Add($sep)
    if ($critical -gt 0) {
        $out.Add("  1. Address all CRITICAL issues listed in Section 3 before go-live.")
        $out.Add("  2. Re-run Invoke-MSTRValidator.ps1 after each fix to confirm resolution.")
        $out.Add("  3. Obtain end-user sign-off only after all CRITICAL issues are resolved.")
    } elseif ($high -gt 0) {
        $out.Add("  1. Review all HIGH issues in Section 4.")
        $out.Add("  2. Remediate issues that affect user access or data delivery.")
        $out.Add("  3. Schedule monitoring for the first week post-go-live.")
    } else {
        $out.Add("  1. Migration is validated. Proceed to end-user communication.")
        $out.Add("  2. Schedule go-live window and notify users.")
        $out.Add("  3. Monitor cloud IS logs for first 24 hours post go-live.")
    }
    $out.Add("")
    $out.Add("  AI ASSISTANCE TIP:")
    $out.Add("  Feed DIFF_REPORT.csv to Claude with:")
    $out.Add("  'Review this MSTR migration diff. For each CRITICAL and HIGH item,")
    $out.Add("   provide the exact Command Manager command or REST API call to fix it.'")
    $out.Add("")
    $out.Add($sep)
    $out.Add("  Report generated : $ReportTime")
    $out.Add("  Full diff data   : DIFF_REPORT.csv")
    $out.Add($sep)

    $out | Out-File -FilePath $path -Encoding UTF8
    Write-Host "  [OK] VALIDATION_REPORT.txt written" -ForegroundColor Green
}

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "=" * 72 -ForegroundColor Cyan
Write-Host "  Invoke-MSTRValidator.ps1 — Windows/Citrix Edition v2.1" -ForegroundColor Cyan
Write-Host "  Baseline : $Baseline" -ForegroundColor Cyan
Write-Host "  Target   : $Target" -ForegroundColor Cyan
Write-Host "  Output   : $OutputDir" -ForegroundColor Cyan
Write-Host "=" * 72 -ForegroundColor Cyan

# Validate inputs
if (-not (Test-Path $Baseline)) { Write-Error "Baseline directory not found: $Baseline"; exit 1 }
if (-not (Test-Path $Target))   { Write-Error "Target directory not found: $Target"; exit 1 }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$reportTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# Run comparisons
Write-Host "`n  Comparing CSV files..." -ForegroundColor Cyan
$allDiffs = [System.Collections.Generic.List[PSCustomObject]]::new()

foreach ($cfg in $COMPARISON_CONFIG) {
    $diffs = Compare-CsvFiles -Config $cfg
    foreach ($d in $diffs) { $allDiffs.Add($d) }
}

# Sort by severity
$allDiffs = [System.Linq.Enumerable]::OrderBy(
    [object[]]$allDiffs,
    [Func[object,object]]{ param($d) @($SEVERITY_ORDER[$d.severity], $d.domain) -join "" }
)

# Write DIFF_REPORT.csv
$diffPath = Join-Path $OutputDir "DIFF_REPORT.csv"
$allDiffs | Select-Object severity,status,domain,file,record_key,field_name,baseline_value,target_value,remediation |
    Export-Csv -Path $diffPath -NoTypeInformation -Encoding UTF8 -Force
Write-Host "`n  [OK] DIFF_REPORT.csv written ($($allDiffs.Count) rows)" -ForegroundColor Green

# Write VALIDATION_REPORT.txt
Write-ValidationReport -AllDiffs $allDiffs -ReportTime $reportTime

# Console summary
$critical   = @($allDiffs | Where-Object { $_.severity -eq "CRITICAL" -and $_.status -ne "MATCH" }).Count
$high       = @($allDiffs | Where-Object { $_.severity -eq "HIGH"     -and $_.status -ne "MATCH" }).Count
$matchCount = @($allDiffs | Where-Object { $_.status -eq "MATCH" }).Count

Write-Host ""
Write-Host "=" * 72 -ForegroundColor $(if ($critical -gt 0) { "Red" } elseif ($high -gt 0) { "Yellow" } else { "Green" })
if ($critical -gt 0) {
    Write-Host "  [FAIL]  $critical CRITICAL issue(s) found — fix before go-live!" -ForegroundColor Red
} elseif ($high -gt 0) {
    Write-Host "  [WARN]  $high HIGH issue(s) — review required" -ForegroundColor Yellow
} else {
    Write-Host "  [PASS]  Migration validated successfully!" -ForegroundColor Green
}
Write-Host "  MATCH   : $matchCount domains"
Write-Host "  CRITICAL: $critical"
Write-Host "  HIGH    : $high"
Write-Host "  Output  : $(Resolve-Path $OutputDir)"
Write-Host "=" * 72
