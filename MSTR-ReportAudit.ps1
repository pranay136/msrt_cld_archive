# =============================================================================
# MSTR-ReportAudit.ps1
# -----------------------------------------------------------------------------
# Purpose : Classify all MicroStrategy reports into "Migrate" vs "Decommission"
#           based on whether they were executed in the last N days (default 365).
# Runtime : Windows PowerShell 5.1+ (ships with Windows 10/11 and Server 2016+)
#           NO Python required. Run from cmd via the Run-MSTRReportAudit.bat
#           wrapper, or call directly with powershell.exe.
#
# Primary data source  : Enterprise Manager statistics DB (SQL Server / Oracle)
#                        This is where MSTR records every report execution.
# Fallback data source : MicroStrategy REST API (inventory only, limited history)
#
# Output files (CSV + TXT in -OutputDir):
#   reports_to_migrate.csv          Reports executed within the window
#   reports_to_decommission.csv     Reports NOT executed within the window
#   reports_audit_summary.txt       Human-readable executive summary
#   reports_audit_raw.csv           Full joined dataset (all reports + usage)
#
# Author  : Pranay  (pranay136@gmail.com)
# Version : 1.0  -  April 2026
# =============================================================================

[CmdletBinding()]
param(
    # --- Mode selection --------------------------------------------------------
    [ValidateSet('EMStats','REST','Hybrid')]
    [string]$Mode = 'EMStats',

    # --- Window --------------------------------------------------------------
    [int]$Days = 365,

    # --- Enterprise Manager DB connection (required for EMStats / Hybrid) -----
    [string]$EMServer   = '',         # e.g. em-sqlserver.company.com
    [string]$EMDatabase = 'MSTR_EM',  # Enterprise Manager / Platform Analytics DB name
    [ValidateSet('SqlServer','Oracle')]
    [string]$EMDbType   = 'SqlServer',
    [string]$EMUser     = '',         # blank = Windows integrated auth (SQL Server only)
    [string]$EMPassword = '',
    [int]   $EMPort     = 0,          # 0 = default (1433 SQL / 1521 Oracle)

    # --- MSTR REST API (required for REST / Hybrid) --------------------------
    [string]$MstrHost     = '',       # e.g. https://mstr.company.com/MicroStrategyLibrary
    [string]$MstrUser     = '',
    [string]$MstrPassword = '',
    [int]   $LoginMode    = 1,        # 1=Standard, 16=LDAP, 64=SAML

    # --- Output ---------------------------------------------------------------
    [string]$OutputDir = ".\report_audit_output",

    # --- Misc -----------------------------------------------------------------
    [switch]$SkipSslCheck,
    [switch]$VerboseLog
)

$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
function Write-Log {
    param([string]$Message, [string]$Level='INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $color = switch ($Level) { 'ERROR' {'Red'} 'WARN' {'Yellow'} 'OK' {'Green'} default {'White'} }
    Write-Host "[$ts] [$Level] $Message" -ForegroundColor $color
}

function Ensure-Dir([string]$Path) {
    if (-not (Test-Path $Path)) { New-Item -ItemType Directory -Path $Path -Force | Out-Null }
}

function Disable-SslCheck {
    # Allow self-signed certs if the caller passed -SkipSslCheck
    if ($SkipSslCheck) {
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
        [System.Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocol]::Tls12
    }
}

# =============================================================================
# MODE 1: EM STATS  -  SQL query against Enterprise Manager / Platform Analytics
# =============================================================================
function Invoke-EMStatsAudit {
    if ([string]::IsNullOrWhiteSpace($EMServer)) {
        throw "EMStats mode requires -EMServer"
    }
    Write-Log "Connecting to Enterprise Manager DB: $EMDbType // $EMServer / $EMDatabase"

    $cutoffDays = $Days

    # ---- Build connection string ----------------------------------------
    if ($EMDbType -eq 'SqlServer') {
        $port = if ($EMPort -gt 0) { ",$EMPort" } else { '' }
        if ([string]::IsNullOrWhiteSpace($EMUser)) {
            $connStr = "Server=$EMServer$port;Database=$EMDatabase;Integrated Security=SSPI;Encrypt=False;TrustServerCertificate=True"
        } else {
            $connStr = "Server=$EMServer$port;Database=$EMDatabase;User ID=$EMUser;Password=$EMPassword;Encrypt=False;TrustServerCertificate=True"
        }
        $conn = New-Object System.Data.SqlClient.SqlConnection($connStr)

        # Two queries: full report inventory + 365-day execution stats
        $qryInventory = @"
SELECT
    p.PROJECT_ID                 AS ProjectId,
    p.PROJECT_NAME               AS ProjectName,
    o.OBJECT_GUID                AS ReportId,
    o.OBJECT_NAME                AS ReportName,
    ISNULL(o.LOCATION, '')       AS ReportPath,
    ISNULL(o.OWNER, '')          AS Owner,
    o.CREATION_TIME              AS CreatedDate,
    o.MODIFICATION_TIME          AS ModifiedDate
FROM LU_OBJECT o
JOIN LU_PROJECT p ON o.PROJECT_ID = p.PROJECT_ID
WHERE o.OBJECT_TYPE = 3     -- 3 = Report
"@

        $qryUsage = @"
SELECT
    OBJECT_GUID                  AS ReportId,
    PROJECT_ID                   AS ProjectId,
    MAX(SESSION_START_TIME)      AS LastRunDate,
    COUNT(*)                     AS RunCountWindow,
    COUNT(DISTINCT USER_GUID)    AS UniqueUsersWindow
FROM IS_REPORT_STATS
WHERE SESSION_START_TIME >= DATEADD(day, -$cutoffDays, GETDATE())
  AND REPORT_TYPE = 3            -- 3 = Report (not Document/Dossier)
GROUP BY OBJECT_GUID, PROJECT_ID
"@
    }
    else {
        # Oracle
        # Requires Oracle ODP.NET Managed driver (Oracle.ManagedDataAccess.dll)
        # If not present this will throw with a clear error.
        try { Add-Type -AssemblyName 'Oracle.ManagedDataAccess' -ErrorAction Stop } catch {
            throw "Oracle mode requires Oracle.ManagedDataAccess.dll in GAC or PowerShell folder. See README."
        }
        $port = if ($EMPort -gt 0) { $EMPort } else { 1521 }
        $connStr = "User Id=$EMUser;Password=$EMPassword;Data Source=(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=$EMServer)(PORT=$port))(CONNECT_DATA=(SERVICE_NAME=$EMDatabase)));"
        $conn = New-Object Oracle.ManagedDataAccess.Client.OracleConnection($connStr)

        $qryInventory = @"
SELECT
    p.PROJECT_ID      AS ProjectId,
    p.PROJECT_NAME    AS ProjectName,
    o.OBJECT_GUID     AS ReportId,
    o.OBJECT_NAME     AS ReportName,
    NVL(o.LOCATION,'') AS ReportPath,
    NVL(o.OWNER,'')   AS Owner,
    o.CREATION_TIME   AS CreatedDate,
    o.MODIFICATION_TIME AS ModifiedDate
FROM LU_OBJECT o
JOIN LU_PROJECT p ON o.PROJECT_ID = p.PROJECT_ID
WHERE o.OBJECT_TYPE = 3
"@
        $qryUsage = @"
SELECT
    OBJECT_GUID     AS ReportId,
    PROJECT_ID      AS ProjectId,
    MAX(SESSION_START_TIME) AS LastRunDate,
    COUNT(*)        AS RunCountWindow,
    COUNT(DISTINCT USER_GUID) AS UniqueUsersWindow
FROM IS_REPORT_STATS
WHERE SESSION_START_TIME >= SYSDATE - $cutoffDays
  AND REPORT_TYPE = 3
GROUP BY OBJECT_GUID, PROJECT_ID
"@
    }

    $conn.Open()
    Write-Log "Connected." 'OK'

    # ---- Run inventory query -------------------------------------------
    Write-Log "Fetching full report inventory from LU_OBJECT..."
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = $qryInventory
    $cmd.CommandTimeout = 600
    $adapter = if ($EMDbType -eq 'SqlServer') {
        New-Object System.Data.SqlClient.SqlDataAdapter($cmd)
    } else {
        New-Object Oracle.ManagedDataAccess.Client.OracleDataAdapter($cmd)
    }
    $dtInventory = New-Object System.Data.DataTable
    [void]$adapter.Fill($dtInventory)
    Write-Log ("Inventory rows: {0}" -f $dtInventory.Rows.Count) 'OK'

    # ---- Run usage query -----------------------------------------------
    Write-Log "Fetching report executions in last $Days days from IS_REPORT_STATS..."
    $cmd2 = $conn.CreateCommand()
    $cmd2.CommandText = $qryUsage
    $cmd2.CommandTimeout = 600
    $adapter2 = if ($EMDbType -eq 'SqlServer') {
        New-Object System.Data.SqlClient.SqlDataAdapter($cmd2)
    } else {
        New-Object Oracle.ManagedDataAccess.Client.OracleDataAdapter($cmd2)
    }
    $dtUsage = New-Object System.Data.DataTable
    [void]$adapter2.Fill($dtUsage)
    Write-Log ("Usage rows: {0}" -f $dtUsage.Rows.Count) 'OK'

    $conn.Close()

    # ---- Join in-memory --------------------------------------------------
    $usageLookup = @{}
    foreach ($r in $dtUsage.Rows) {
        $key = "$($r.ProjectId)|$($r.ReportId)"
        $usageLookup[$key] = @{
            LastRunDate       = $r.LastRunDate
            RunCountWindow    = [int]$r.RunCountWindow
            UniqueUsersWindow = [int]$r.UniqueUsersWindow
        }
    }

    $result = New-Object System.Collections.Generic.List[object]
    foreach ($r in $dtInventory.Rows) {
        $key = "$($r.ProjectId)|$($r.ReportId)"
        $hit = $usageLookup[$key]
        $obj = [pscustomobject]@{
            ProjectId         = $r.ProjectId
            ProjectName       = $r.ProjectName
            ReportId          = $r.ReportId
            ReportName        = $r.ReportName
            ReportPath        = $r.ReportPath
            Owner             = $r.Owner
            CreatedDate       = $r.CreatedDate
            ModifiedDate      = $r.ModifiedDate
            LastRunDate       = if ($hit) { $hit.LastRunDate } else { $null }
            RunCountWindow    = if ($hit) { $hit.RunCountWindow } else { 0 }
            UniqueUsersWindow = if ($hit) { $hit.UniqueUsersWindow } else { 0 }
            Classification    = if ($hit) { 'MIGRATE' } else { 'DECOMMISSION' }
            Reason            = if ($hit) { "Executed $($hit.RunCountWindow)x in last $Days days" } else { "No executions in last $Days days" }
        }
        $result.Add($obj) | Out-Null
    }
    return $result
}

# =============================================================================
# MODE 2: REST API  -  Uses MSTR REST API (monitor) for recent activity
# Note: Monitor API only reliably shows jobs within its retention window,
#       typically ~30-90 days. Use EMStats for a true 365-day audit.
# =============================================================================
function Invoke-RestAudit {
    if ([string]::IsNullOrWhiteSpace($MstrHost)) { throw "REST mode requires -MstrHost" }

    Disable-SslCheck

    # ---- Login ----------------------------------------------------------
    Write-Log "Logging in to $MstrHost ..."
    $body = @{
        username  = $MstrUser
        password  = $MstrPassword
        loginMode = $LoginMode
    } | ConvertTo-Json

    $resp = Invoke-WebRequest -Uri "$MstrHost/api/auth/login" -Method POST -Body $body `
        -ContentType 'application/json' -UseBasicParsing -SessionVariable mstrSession
    $token = $resp.Headers['X-MSTR-AuthToken']
    if (-not $token) { throw "Login failed: no auth token received" }
    Write-Log "Authenticated." 'OK'

    $headers = @{ 'X-MSTR-AuthToken' = $token }

    # ---- Projects --------------------------------------------------------
    $projects = Invoke-RestMethod -Uri "$MstrHost/api/projects" -Headers $headers -WebSession $mstrSession
    Write-Log ("Found {0} projects" -f $projects.Count)

    $cutoff = (Get-Date).AddDays(-$Days)
    $result = New-Object System.Collections.Generic.List[object]

    foreach ($proj in $projects) {
        $pid = $proj.id
        $pname = $proj.name
        Write-Log "Project: $pname ($pid)"

        $phdr = $headers.Clone()
        $phdr['X-MSTR-ProjectID'] = $pid

        # ---- Inventory: reports (object type 3) ------------------------
        $offset = 0; $limit = 500
        $reports = @()
        do {
            $url = "$MstrHost/api/objects?type=3&limit=$limit&offset=$offset"
            $r = Invoke-RestMethod -Uri $url -Headers $phdr -WebSession $mstrSession
            if ($r.objects) { $reports += $r.objects }
            $offset += $limit
        } while ($r.objects -and $r.objects.Count -eq $limit)
        Write-Log "  Reports found: $($reports.Count)"

        # ---- Monitor: executed jobs (last N days where retained) ------
        $jobsMap = @{}
        try {
            $jobs = Invoke-RestMethod -Uri "$MstrHost/api/monitors/projects/$pid/jobs?limit=5000" -Headers $phdr -WebSession $mstrSession
            foreach ($j in $jobs.jobs) {
                if ($j.objectId -and $j.startTime) {
                    $jt = [datetime]::Parse($j.startTime)
                    if ($jt -ge $cutoff) {
                        if ($jobsMap.ContainsKey($j.objectId)) {
                            $jobsMap[$j.objectId].RunCount++
                            if ($jt -gt $jobsMap[$j.objectId].LastRunDate) { $jobsMap[$j.objectId].LastRunDate = $jt }
                        } else {
                            $jobsMap[$j.objectId] = @{ RunCount = 1; LastRunDate = $jt }
                        }
                    }
                }
            }
        } catch { Write-Log "  Monitor API not available for this project: $_" 'WARN' }

        foreach ($rep in $reports) {
            $hit = $jobsMap[$rep.id]
            $mod = $null
            if ($rep.modificationTime) { $mod = [datetime]::Parse($rep.modificationTime) }

            $result.Add([pscustomobject]@{
                ProjectId         = $pid
                ProjectName       = $pname
                ReportId          = $rep.id
                ReportName        = $rep.name
                ReportPath        = $rep.location
                Owner             = $rep.owner.name
                CreatedDate       = $rep.dateCreated
                ModifiedDate      = $rep.modificationTime
                LastRunDate       = if ($hit) { $hit.LastRunDate } else { $null }
                RunCountWindow    = if ($hit) { $hit.RunCount } else { 0 }
                UniqueUsersWindow = $null
                Classification    = if ($hit) { 'MIGRATE' } elseif ($mod -and $mod -ge $cutoff) { 'MIGRATE' } else { 'DECOMMISSION' }
                Reason            = if ($hit) { "Executed $($hit.RunCount)x in last $Days days" }
                                    elseif ($mod -and $mod -ge $cutoff) { "No executions but modified within window" }
                                    else { "No executions and not modified in last $Days days" }
            }) | Out-Null
        }
    }

    # ---- Logout ----------------------------------------------------------
    try { Invoke-WebRequest -Uri "$MstrHost/api/auth/logout" -Method POST -Headers $headers -WebSession $mstrSession -UseBasicParsing | Out-Null } catch {}
    return $result
}

# =============================================================================
# MAIN
# =============================================================================
try {
    Ensure-Dir $OutputDir
    Write-Log "=== MSTR Report Audit ==="
    Write-Log "Mode   : $Mode"
    Write-Log "Window : $Days days"
    Write-Log "Output : $OutputDir"

    switch ($Mode) {
        'EMStats' { $data = Invoke-EMStatsAudit }
        'REST'    { $data = Invoke-RestAudit }
        'Hybrid'  {
            Write-Log "Hybrid mode: running EMStats, then enriching with REST inventory"
            $data = Invoke-EMStatsAudit
            # (inventory already full from EM; REST enrichment left as extension point)
        }
    }

    if (-not $data -or $data.Count -eq 0) {
        throw "No report data returned. Check credentials, connectivity, and permissions."
    }

    # ---- Split & write outputs -----------------------------------------
    $migrate      = $data | Where-Object { $_.Classification -eq 'MIGRATE' }
    $decommission = $data | Where-Object { $_.Classification -eq 'DECOMMISSION' }

    $pathMigrate = Join-Path $OutputDir 'reports_to_migrate.csv'
    $pathDecom   = Join-Path $OutputDir 'reports_to_decommission.csv'
    $pathRaw     = Join-Path $OutputDir 'reports_audit_raw.csv'
    $pathSumm    = Join-Path $OutputDir 'reports_audit_summary.txt'

    $migrate      | Export-Csv -Path $pathMigrate -NoTypeInformation -Encoding UTF8
    $decommission | Export-Csv -Path $pathDecom   -NoTypeInformation -Encoding UTF8
    $data         | Export-Csv -Path $pathRaw     -NoTypeInformation -Encoding UTF8

    # ---- Per-project stats ---------------------------------------------
    $byProject = $data | Group-Object ProjectName | ForEach-Object {
        $m = ($_.Group | Where-Object { $_.Classification -eq 'MIGRATE' }).Count
        $d = ($_.Group | Where-Object { $_.Classification -eq 'DECOMMISSION' }).Count
        [pscustomobject]@{ Project=$_.Name; Migrate=$m; Decommission=$d; Total=$_.Count }
    }

    # ---- Summary text ---------------------------------------------------
    $summary = @()
    $summary += "================================================================"
    $summary += " MSTR REPORT AUDIT SUMMARY"
    $summary += "================================================================"
    $summary += " Generated  : $(Get-Date)"
    $summary += " Mode       : $Mode"
    $summary += " Window     : Last $Days days"
    $summary += ""
    $summary += " Total reports scanned : $($data.Count)"
    $summary += " --> TO MIGRATE        : $($migrate.Count)"
    $summary += " --> TO DECOMMISSION   : $($decommission.Count)"
    if ($data.Count -gt 0) {
        $pct = [math]::Round(($migrate.Count / $data.Count) * 100, 1)
        $summary += " Active-usage rate     : ${pct}%"
    }
    $summary += ""
    $summary += "---------------- PER-PROJECT BREAKDOWN ----------------"
    $summary += ($byProject | Format-Table -AutoSize | Out-String)
    $summary += ""
    $summary += "---------------- OUTPUT FILES ----------------"
    $summary += " $pathMigrate"
    $summary += " $pathDecom"
    $summary += " $pathRaw"
    $summary += ""
    $summary | Out-File -FilePath $pathSumm -Encoding UTF8

    Write-Log "Migrate      : $($migrate.Count)"       'OK'
    Write-Log "Decommission : $($decommission.Count)"  'OK'
    Write-Log "Summary      : $pathSumm"               'OK'
    Write-Log "DONE"                                   'OK'
}
catch {
    Write-Log $_.Exception.Message 'ERROR'
    exit 1
}
