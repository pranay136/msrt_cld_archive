<#
================================================================================
  build_acl_request.ps1
  PowerShell Script — Windows / Citrix Server
  ================================================================================
  PURPOSE:
    Scrapes ALL database server names and IP addresses from:
      1. Windows ODBC System Data Sources (registry — most accurate for DSN connections)
      2. Windows ODBC User Data Sources (registry)
      3. Command Manager output file from fetch_mstr_datasources.scp (optional)

    Resolves every hostname to its IP address via DNS.
    Outputs a clean CSV ready to paste into an ACL firewall request.

  HOW TO RUN:
  ─────────────────────────────────────────────────────
    Option A — ODBC registry only (fastest, no CM output needed):
      powershell.exe -ExecutionPolicy Bypass -File build_acl_request.ps1

    Option B — ODBC registry + parse CM output file:
      powershell.exe -ExecutionPolicy Bypass -File build_acl_request.ps1 `
          -CMOutputFile "C:\ACL_Work\mstr_datasource_dump.txt"

  OUTPUT:
    acl_request_db_inventory.csv   — ready-to-use table for ACL/firewall request
    acl_request_db_inventory.html  — formatted HTML version for email / ticket

  SUPPORTED DRIVERS (auto-detected from registry):
    SQL Server / MSOLEDBSQL / ODBC Driver for SQL Server
    Oracle
    MySQL / SingleStore (uses "Server" key)
    Teradata
    PostgreSQL / Redshift
    IBM DB2
    Snowflake
    Hive / Impala / Spark (via Simba)
    Generic ODBC (fallback: try all known server key names)
================================================================================
#>

param(
    [string]$CMOutputFile = "",
    [string]$OutputDir    = "C:\ACL_Work",
    [switch]$OpenCSV
)

# ─── SETUP ────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "SilentlyContinue"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$csvPath   = Join-Path $OutputDir "acl_request_db_inventory_$timestamp.csv"
$htmlPath  = Join-Path $OutputDir "acl_request_db_inventory_$timestamp.html"

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  MSTR Database Server Inventory — ACL Request Builder" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

# ─── DATA COLLECTION ──────────────────────────────────────────────────────────
$allEntries = [System.Collections.Generic.List[PSObject]]::new()

# ── Helper: Resolve hostname to IP(s) ─────────────────────────────────────────
function Resolve-HostToIP {
    param([string]$Hostname)
    if ([string]::IsNullOrWhiteSpace($Hostname)) { return "—" }

    # Already an IP?
    if ($Hostname -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$') {
        return $Hostname
    }

    try {
        $ips = [System.Net.Dns]::GetHostAddresses($Hostname) |
               Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
               Select-Object -ExpandProperty IPAddressToString
        if ($ips) { return ($ips -join ", ") }

        # Try with IPv6 too as fallback label
        $all = [System.Net.Dns]::GetHostAddresses($Hostname) |
               Select-Object -ExpandProperty IPAddressToString
        if ($all) { return ($all -join ", ") }
    } catch {}
    return "DNS_UNRESOLVED"
}

# ── Helper: Clean server string (strip port if embedded) ──────────────────────
function Get-ServerAndPort {
    param([string]$Raw, [string]$DefaultPort = "")
    $server = $Raw.Trim()
    $port   = $DefaultPort

    # "server,1433" (SQL Server format)
    if ($server -match '^(.+),(\d+)$') {
        $server = $matches[1].Trim()
        $port   = $matches[2]
    }
    # "server:port"
    elseif ($server -match '^([^:]+):(\d+)$') {
        $server = $matches[1].Trim()
        $port   = $matches[2]
    }
    return @{ Server = $server; Port = $port }
}

# ─── SECTION 1: Windows ODBC System and User DSNs (registry) ──────────────────
Write-Host "[ 1/3 ] Scanning Windows ODBC Data Sources (registry)..." -ForegroundColor Yellow

$odbcPaths = @(
    @{ Path = "HKLM:\SOFTWARE\ODBC\ODBC.INI";                  Scope = "System (64-bit)" },
    @{ Path = "HKLM:\SOFTWARE\WOW6432Node\ODBC\ODBC.INI";      Scope = "System (32-bit)" },
    @{ Path = "HKCU:\SOFTWARE\ODBC\ODBC.INI";                  Scope = "User" }
)

foreach ($odbcPath in $odbcPaths) {
    $regBase = $odbcPath.Path
    $scope   = $odbcPath.Scope

    if (-not (Test-Path $regBase)) { continue }

    # Get DSN list from "ODBC Data Sources" sub-key
    $dsnListKey = Join-Path $regBase "ODBC Data Sources"
    $dsnNames   = @()
    if (Test-Path $dsnListKey) {
        $props = Get-ItemProperty $dsnListKey
        $dsnNames = $props.PSObject.Properties |
                    Where-Object { $_.Name -notmatch '^PS' } |
                    Select-Object -ExpandProperty Name
    }

    foreach ($dsnName in $dsnNames) {
        $dsnKey = Join-Path $regBase $dsnName
        if (-not (Test-Path $dsnKey)) { continue }

        $props = Get-ItemProperty $dsnKey

        # Detect driver type
        $driver = $props.Driver ?? ""

        # Extract server — different drivers use different key names
        $serverRaw = ""
        $port      = ""
        $database  = ""
        $dbType    = "Unknown"

        # SQL Server family (SQL Server, MSOLEDBSQL, ODBC Driver 17/18)
        if ($driver -match "SQL Server|sqlncli|ODBC Driver") {
            $dbType    = "SQL Server"
            $serverRaw = $props.Server ?? $props.ServerName ?? ""
            $port      = $props.Port ?? "1433"
            $database  = $props.Database ?? ""
        }
        # Oracle
        elseif ($driver -match "Oracle") {
            $dbType    = "Oracle"
            $serverRaw = $props.SERVER ?? $props.DBQ ?? $props.Host ?? ""
            $port      = $props.Port ?? "1521"
            $database  = $props.Database ?? $props.SERVER ?? ""
        }
        # MySQL
        elseif ($driver -match "MySQL") {
            $dbType    = "MySQL"
            $serverRaw = $props.Server ?? $props.SERVER ?? ""
            $port      = $props.Port ?? $props.TCP_PORT ?? "3306"
            $database  = $props.Database ?? $props.SCHEMA ?? ""
        }
        # SingleStore (formerly MemSQL)
        elseif ($driver -match "SingleStore|Singlestore|MemSQL|memsql") {
            $dbType    = "SingleStore"
            $serverRaw = $props.Server ?? $props.HOST ?? ""
            $port      = $props.Port ?? "3306"
            $database  = $props.Database ?? ""
        }
        # Teradata
        elseif ($driver -match "Teradata") {
            $dbType    = "Teradata"
            $serverRaw = $props.DBCName ?? $props.TDServerName ?? $props.Server ?? ""
            $port      = $props.Port ?? $props.DBS_Port ?? "1025"
            $database  = $props.DefaultDatabase ?? ""
        }
        # PostgreSQL / Redshift
        elseif ($driver -match "PostgreSQL|psqlODBC|Redshift") {
            $dbType    = if ($driver -match "Redshift") { "Amazon Redshift" } else { "PostgreSQL" }
            $serverRaw = $props.Servername ?? $props.Server ?? $props.HOST ?? ""
            $port      = $props.Port ?? "5439"
            $database  = $props.Database ?? ""
        }
        # IBM DB2
        elseif ($driver -match "DB2|IBM") {
            $dbType    = "IBM DB2"
            $serverRaw = $props.Hostname ?? $props.Server ?? $props.HOST ?? ""
            $port      = $props.Port ?? $props.PORTNUMBER ?? "50000"
            $database  = $props.Database ?? $props.DBAlias ?? ""
        }
        # Snowflake
        elseif ($driver -match "Snowflake") {
            $dbType    = "Snowflake"
            $serverRaw = $props.Server ?? $props.Account ?? ""
            $port      = "443"
            $database  = $props.Database ?? ""
        }
        # Hive / Impala / Spark (Simba drivers)
        elseif ($driver -match "Hive|Impala|Spark|Simba") {
            $dbType    = if ($driver -match "Impala") { "Impala" }
                         elseif ($driver -match "Spark") { "Spark" }
                         else { "Hive" }
            $serverRaw = $props.Host ?? $props.Server ?? $props.HS2Host ?? ""
            $port      = $props.Port ?? $props.HS2ThriftPort ?? "10000"
            $database  = $props.Schema ?? $props.Database ?? ""
        }
        # Generic fallback — try common key names
        else {
            $dbType    = "Other"
            foreach ($key in @("Server","Servername","ServerName","Host","HOST","DBCName","DBQ","Hostname")) {
                $val = $props.$key
                if ($val -and -not [string]::IsNullOrWhiteSpace($val)) {
                    $serverRaw = $val
                    break
                }
            }
            $port     = $props.Port ?? $props.PORT ?? ""
            $database = $props.Database ?? $props.DB ?? ""
        }

        if ([string]::IsNullOrWhiteSpace($serverRaw)) {
            $serverRaw = "(not found in registry)"
        }

        $parsed = Get-ServerAndPort -Raw $serverRaw -DefaultPort $port
        $serverClean = $parsed.Server
        $portClean   = $parsed.Port

        Write-Host "   Found DSN: $dsnName  →  $serverClean" -ForegroundColor Gray

        $ipAddress = Resolve-HostToIP -Hostname $serverClean

        $allEntries.Add([PSCustomObject]@{
            Source       = "ODBC_Registry"
            Scope        = $scope
            DSN_Name     = $dsnName
            DB_Type      = $dbType
            Server_Name  = $serverClean
            Port         = $portClean
            Database     = $database
            IP_Address   = $ipAddress
            Driver       = $driver
            Notes        = ""
        })
    }
}

Write-Host "   → Found $($allEntries | Where-Object Source -eq 'ODBC_Registry' | Measure-Object | Select-Object -ExpandProperty Count) ODBC DSNs" -ForegroundColor Green
Write-Host ""

# ─── SECTION 2: Parse Command Manager output file (if provided) ───────────────
if ($CMOutputFile -and (Test-Path $CMOutputFile)) {
    Write-Host "[ 2/3 ] Parsing Command Manager output: $CMOutputFile" -ForegroundColor Yellow

    $cmContent = Get-Content $CMOutputFile -Raw
    $cmLines   = Get-Content $CMOutputFile

    # Extract entries from CM output — MSTR uses patterns like:
    #   DSN:  MyDSN
    #   DATABASE TYPE:  SQL Server
    #   SERVER:  myserver.company.com
    # We'll parse these blocks and extract server names.

    $currentBlock = @{}
    $cmEntries    = [System.Collections.Generic.List[PSObject]]::new()

    foreach ($line in $cmLines) {
        $line = $line.Trim()

        # Detect field lines  "FIELD NAME: value"
        if ($line -match '^(NAME|DSN|SERVER|HOST|DATABASE TYPE|DB TYPE|TYPE|PORT|DATABASE NAME|DATABASE)\s*[:=]\s*"?([^"]*)"?\s*$') {
            $key = $matches[1].Trim().ToUpper() -replace '\s+', '_'
            $val = $matches[2].Trim()
            $currentBlock[$key] = $val
        }

        # Blank line or new object separator — flush the current block
        if (($line -eq "" -or $line -match "^-{3,}") -and $currentBlock.Count -gt 0) {
            # Only save if we found a useful server/host entry
            $srv = $currentBlock["SERVER"] ?? $currentBlock["HOST"] ?? $currentBlock["DSN"] ?? ""
            if ($srv -and $srv -ne "") {
                $ip = Resolve-HostToIP -Hostname $srv
                $cmEntries.Add([PSCustomObject]@{
                    Source       = "CM_Output"
                    Scope        = "MSTR_Metadata"
                    DSN_Name     = $currentBlock["DSN"] ?? $currentBlock["NAME"] ?? ""
                    DB_Type      = $currentBlock["DATABASE_TYPE"] ?? $currentBlock["DB_TYPE"] ?? $currentBlock["TYPE"] ?? ""
                    Server_Name  = $srv
                    Port         = $currentBlock["PORT"] ?? ""
                    Database     = $currentBlock["DATABASE_NAME"] ?? $currentBlock["DATABASE"] ?? ""
                    IP_Address   = $ip
                    Driver       = ""
                    Notes        = "From Command Manager output"
                })
            }
            $currentBlock = @{}
        }
    }

    # Merge CM entries — skip if server already captured from ODBC
    $existingServers = $allEntries | Select-Object -ExpandProperty Server_Name
    foreach ($cm in $cmEntries) {
        if ($cm.Server_Name -notin $existingServers -and $cm.Server_Name -ne "") {
            $allEntries.Add($cm)
        }
    }

    Write-Host "   → Added $($cmEntries.Count) entries from CM output" -ForegroundColor Green
} else {
    Write-Host "[ 2/3 ] No CM output file provided — ODBC registry only" -ForegroundColor DarkGray
}
Write-Host ""

# ─── SECTION 3: Deduplicate and sort ──────────────────────────────────────────
Write-Host "[ 3/3 ] Resolving IPs and building ACL request..." -ForegroundColor Yellow

# Re-resolve any that came back empty (belt-and-suspenders)
foreach ($entry in $allEntries) {
    if ([string]::IsNullOrWhiteSpace($entry.IP_Address)) {
        $entry.IP_Address = Resolve-HostToIP -Hostname $entry.Server_Name
    }
}

# Deduplicate by Server_Name (keep first occurrence)
$seen      = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$deduped   = $allEntries | Where-Object {
    $key = "$($_.Server_Name):$($_.Port)"
    $seen.Add($key)
}

$sorted = $deduped | Sort-Object DB_Type, Server_Name

# ─── OUTPUT: CSV ──────────────────────────────────────────────────────────────
$csvHeader = "Source,Scope,DSN_Name,DB_Type,Server_Name,Port,Database,IP_Address,Driver,Notes"
$csvRows   = $sorted | ForEach-Object {
    "$($_.Source),$($_.Scope),`"$($_.DSN_Name)`",$($_.DB_Type),`"$($_.Server_Name)`",$($_.Port),`"$($_.Database)`",$($_.IP_Address),`"$($_.Driver)`",$($_.Notes)"
}

Set-Content -Path $csvPath -Value ($csvHeader + "`n" + ($csvRows -join "`n")) -Encoding UTF8

Write-Host "   → CSV written: $csvPath" -ForegroundColor Green

# ─── OUTPUT: HTML ─────────────────────────────────────────────────────────────
$tableRows = ""
foreach ($e in $sorted) {
    $ipColor = if ($e.IP_Address -eq "DNS_UNRESOLVED") { "color:#c0392b;font-weight:600" }
               elseif ($e.IP_Address -eq "—")          { "color:#888" }
               else                                    { "color:#1a7a45;font-weight:600" }

    $tableRows += @"
<tr>
  <td>$($e.DSN_Name)</td>
  <td>$($e.DB_Type)</td>
  <td><strong>$($e.Server_Name)</strong></td>
  <td>$($e.Port)</td>
  <td style='$ipColor'>$($e.IP_Address)</td>
  <td>$($e.Database)</td>
  <td style='font-size:.8em;color:#888'>$($e.Scope)</td>
</tr>
"@
}

$totalCount     = $sorted.Count
$resolvedCount  = ($sorted | Where-Object { $_.IP_Address -ne "DNS_UNRESOLVED" -and $_.IP_Address -ne "—" }).Count
$unresolvedCount = ($sorted | Where-Object { $_.IP_Address -eq "DNS_UNRESOLVED" }).Count

$html = @"
<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<title>MSTR Database ACL Request — $timestamp</title>
<style>
  body { font-family: 'Segoe UI', Arial, sans-serif; margin: 0; background: #f5f7fa; color: #1a1a2e; }
  header { background: #0B1E33; color: #fff; padding: 20px 32px; }
  header h1 { margin: 0; font-size: 1.3em; }
  header p  { margin: 4px 0 0; opacity: .7; font-size: .85em; }
  .summary { display: flex; gap: 16px; padding: 20px 32px; flex-wrap: wrap; }
  .card { background: #fff; border-radius: 8px; padding: 14px 22px; text-align: center;
          box-shadow: 0 1px 4px rgba(0,0,0,.1); min-width: 100px; }
  .card .num { font-size: 2em; font-weight: 700; }
  .card .lbl { font-size: .78em; opacity: .6; margin-top: 3px; }
  .green { color: #155724; } .red { color: #721c24; } .blue { color: #004085; }
  table { width: calc(100% - 64px); margin: 0 32px 32px; border-collapse: collapse;
          background: #fff; border-radius: 8px; overflow: hidden;
          box-shadow: 0 1px 4px rgba(0,0,0,.1); font-size: .88em; }
  th { background: #0B1E33; color: #fff; padding: 10px 14px; text-align: left; }
  td { padding: 9px 14px; border-bottom: 1px solid #f0f0f0; }
  tr:hover td { background: #f8f9ff; }
  .note { padding: 0 32px 24px; font-size: .85em; color: #666; }
</style>
</head>
<body>
<header>
  <h1>MSTR Database Server Inventory — ACL Firewall Request</h1>
  <p>Generated: $timestamp &nbsp;|&nbsp; Machine: $env:COMPUTERNAME &nbsp;|&nbsp; User: $env:USERNAME</p>
</header>
<div class='summary'>
  <div class='card'><div class='num blue'>$totalCount</div><div class='lbl'>TOTAL SERVERS</div></div>
  <div class='card'><div class='num green'>$resolvedCount</div><div class='lbl'>IP RESOLVED</div></div>
  <div class='card'><div class='num red'>$unresolvedCount</div><div class='lbl'>UNRESOLVED</div></div>
</div>
<table>
  <thead><tr>
    <th>DSN Name</th>
    <th>DB Type</th>
    <th>Server / Hostname</th>
    <th>Port</th>
    <th>IP Address</th>
    <th>Database</th>
    <th>Scope</th>
  </tr></thead>
  <tbody>$tableRows</tbody>
</table>
<div class='note'>
  <strong>DNS_UNRESOLVED</strong> = hostname exists in ODBC config but could not be resolved from this machine.
  This may indicate a firewall block or stale entry. Provide the hostname to the network team for manual lookup.<br><br>
  <strong>Next step:</strong> Paste the IP addresses from the CSV into your ACL request ticket.
  Include the Port column for precise rule creation (source: MSTR IS host, destination: DB host:port).
</div>
</body>
</html>
"@

Set-Content -Path $htmlPath -Value $html -Encoding UTF8
Write-Host "   → HTML written: $htmlPath" -ForegroundColor Green

# ─── SUMMARY ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  RESULTS SUMMARY" -ForegroundColor Cyan
Write-Host "  Total DB servers found : $totalCount"
Write-Host "  IP resolved            : $resolvedCount" -ForegroundColor Green
Write-Host "  DNS unresolved         : $unresolvedCount" -ForegroundColor $(if ($unresolvedCount -gt 0) { "Red" } else { "Green" })
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  CSV  → $csvPath" -ForegroundColor White
Write-Host "  HTML → $htmlPath" -ForegroundColor White
Write-Host ""

# Print quick table to console
Write-Host "  DB SERVERS FOR ACL REQUEST:" -ForegroundColor Cyan
Write-Host "  ─────────────────────────────────────────────────────────" -ForegroundColor DarkGray
$sorted | Format-Table DSN_Name, DB_Type, Server_Name, Port, IP_Address -AutoSize

# Open CSV in Excel if requested
if ($OpenCSV) {
    Start-Process $csvPath
}
