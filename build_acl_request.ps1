# =============================================================================
# build_acl_request.ps1
# -----------------------------------------------------------------------------
# Purpose : Parse an odbc.ini (or an 08_datasources.csv from the MSTR harvester)
#           and generate a firewall / ACL opening request that the network
#           team can action directly.
#
# Why    : Before a MicroStrategy Cloud (CMC) IS can query on-prem databases,
#          the CMC host needs outbound TCP reachability to every DB host:port
#          referenced by the on-prem datasources. This script builds that
#          request - as CSV (for tickets), plain-text table (for email body),
#          and JSON (for ServiceNow / Jira automation).
#
# Runtime : Windows PowerShell 5.1 (built-in on Windows 10/11/Server 2016+)
#           NO PowerShell 7+ syntax used. NO null-coalescing (??), NO pipeline
#           chain operators (&&/||), NO ternary (?:). Pure 5.1-compatible.
#
# Author  : Pranay (pranay136@gmail.com)
# Version : 2.0 (April 2026)  -  PS 5.1 compatibility rewrite
# =============================================================================

[CmdletBinding()]
param(
    # ---- Input ---------------------------------------------------------------
    # Provide EITHER -OdbcFile OR -DatasourcesCsv (or both; they merge).
    [string]$OdbcFile        = "",          # e.g. C:\Windows\System32\odbc.ini or /etc/odbc.ini
    [string]$DatasourcesCsv  = "",          # e.g. .\discovery_output\08_datasources.csv

    # ---- Source host (the CMC that needs to reach the DBs) -------------------
    [Parameter(Mandatory=$true)]
    [string]$CmcHost,                        # e.g. cmc-prod.cloud.company.com
    [string]$CmcIp           = "",           # optional, auto-resolved if blank
    [string]$CmcCidr         = "",           # optional, e.g. 10.50.0.0/24 (overrides host)
    [string]$Environment     = "PROD",       # PROD / UAT / DR - for the ticket

    # ---- Output --------------------------------------------------------------
    [string]$OutputDir       = ".\acl_request_output",
    [string]$TicketId        = "",           # optional ticket/change ID to stamp on outputs
    [string]$Requester       = $env:USERNAME,
    [string]$BusinessJustification = "MicroStrategy on-prem to Cloud migration - CMC needs DB reachability",

    # ---- Behavior ------------------------------------------------------------
    [switch]$IncludeUnknown,                 # include rows where DB type can't be detected
    [switch]$DeduplicateByHostPort           # collapse to unique host:port rows
)

$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# Logging helpers
# -----------------------------------------------------------------------------
function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $color = 'White'
    if ($Level -eq 'ERROR') { $color = 'Red' }
    elseif ($Level -eq 'WARN') { $color = 'Yellow' }
    elseif ($Level -eq 'OK') { $color = 'Green' }
    Write-Host "[$ts] [$Level] $Message" -ForegroundColor $color
}

function Ensure-Dir([string]$Path) {
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

# 5.1-compatible null-coalescing replacement
function Coalesce {
    foreach ($v in $args) {
        if ($null -ne $v -and $v -ne "") { return $v }
    }
    return ""
}

# -----------------------------------------------------------------------------
# DB type detection from driver string + default ports
# -----------------------------------------------------------------------------
function Get-DbTypeAndPort {
    param([string]$Driver, [string]$ExistingPort)

    $type = "Unknown"
    $port = ""

    if ([string]::IsNullOrWhiteSpace($Driver)) {
        return @{ DbType = $type; Port = $ExistingPort }
    }

    $d = $Driver.ToLower()

    if ($d -match "sql server|sqlncli|mssql|odbc driver 1[0-9]|msoledbsql") {
        $type = "SQL Server"; $port = "1433"
    }
    elseif ($d -match "oracle") {
        $type = "Oracle"; $port = "1521"
    }
    elseif ($d -match "postgres|postgre") {
        $type = "PostgreSQL"; $port = "5432"
    }
    elseif ($d -match "mysql|mariadb") {
        $type = "MySQL/MariaDB"; $port = "3306"
    }
    elseif ($d -match "teradata") {
        $type = "Teradata"; $port = "1025"
    }
    elseif ($d -match "snowflake") {
        $type = "Snowflake"; $port = "443"
    }
    elseif ($d -match "redshift") {
        $type = "Amazon Redshift"; $port = "5439"
    }
    elseif ($d -match "bigquery") {
        $type = "Google BigQuery"; $port = "443"
    }
    elseif ($d -match "hive") {
        $type = "Apache Hive"; $port = "10000"
    }
    elseif ($d -match "spark|simbaspark|databricks") {
        $type = "Apache Spark / Databricks"; $port = "443"
    }
    elseif ($d -match "db2") {
        $type = "IBM DB2"; $port = "50000"
    }
    elseif ($d -match "sybase") {
        $type = "Sybase"; $port = "5000"
    }
    elseif ($d -match "vertica") {
        $type = "Vertica"; $port = "5433"
    }
    elseif ($d -match "netezza") {
        $type = "Netezza"; $port = "5480"
    }
    elseif ($d -match "impala") {
        $type = "Impala"; $port = "21050"
    }

    # Existing explicit port wins over the default
    if (-not [string]::IsNullOrWhiteSpace($ExistingPort)) { $port = $ExistingPort }

    return @{ DbType = $type; Port = $port }
}

# -----------------------------------------------------------------------------
# Parse odbc.ini (Windows or Linux format)
# -----------------------------------------------------------------------------
function Parse-OdbcIni {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        Write-Log "odbc.ini not found at $Path" 'WARN'
        return @()
    }

    Write-Log "Parsing $Path"
    $lines    = Get-Content -Path $Path -Encoding UTF8
    $entries  = New-Object System.Collections.Generic.List[object]
    $current  = $null
    $inKnown  = $false

    # Lines to skip as section headers (not actual DSN entries)
    $skipSections = @('ODBC Data Sources','ODBC','ODBC 32 bit Data Sources','ODBC 64 bit Data Sources')

    foreach ($raw in $lines) {
        $line = $raw.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line.StartsWith(';') -or $line.StartsWith('#')) { continue }

        if ($line.StartsWith('[') -and $line.EndsWith(']')) {
            # Close previous section
            if ($current -ne $null -and $inKnown) {
                $entries.Add($current) | Out-Null
            }
            $sectionName = $line.Substring(1, $line.Length - 2).Trim()
            if ($skipSections -contains $sectionName) {
                $inKnown = $false
                $current = $null
            } else {
                $inKnown = $true
                $current = [ordered]@{
                    DsnName = $sectionName
                    Properties = [ordered]@{}
                }
            }
            continue
        }

        if (-not $inKnown -or $current -eq $null) { continue }

        # key = value
        $idx = $line.IndexOf('=')
        if ($idx -le 0) { continue }

        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()

        # Strip wrapping quotes if present
        if ($val.Length -ge 2 -and $val.StartsWith('"') -and $val.EndsWith('"')) {
            $val = $val.Substring(1, $val.Length - 2)
        }

        $current.Properties[$key] = $val
    }

    # Flush last section
    if ($current -ne $null -and $inKnown) {
        $entries.Add($current) | Out-Null
    }

    # Convert to flat records
    $result = New-Object System.Collections.Generic.List[object]
    foreach ($e in $entries) {
        $p = $e.Properties

        $driver   = Coalesce $p['Driver']        $p['DRIVER']
        $server   = Coalesce $p['Server']        $p['SERVER']   $p['Host']     $p['HOST']      $p['HostName']  $p['ServerName']
        $port     = Coalesce $p['Port']          $p['PORT']     $p['PortNumber']
        $database = Coalesce $p['Database']      $p['DATABASE'] $p['DBName']   $p['DBNAME']    $p['InitialCatalog']  $p['ServiceName']
        $uid      = Coalesce $p['UID']           $p['User']     $p['Username'] $p['UserName']  $p['LogonID']

        $typeInfo = Get-DbTypeAndPort -Driver $driver -ExistingPort $port

        $obj = [pscustomobject]@{
            Source          = "odbc.ini"
            DsnName         = $e.DsnName
            Driver          = $driver
            Server          = $server
            Port            = $typeInfo.Port
            Database        = $database
            Username        = $uid
            DbType          = $typeInfo.DbType
        }
        $result.Add($obj) | Out-Null
    }

    Write-Log ("  Parsed {0} DSN entries" -f $result.Count) 'OK'
    return $result
}

# -----------------------------------------------------------------------------
# Parse 08_datasources.csv from the MSTR harvester
# -----------------------------------------------------------------------------
function Parse-DatasourcesCsv {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        Write-Log "datasources CSV not found at $Path" 'WARN'
        return @()
    }

    Write-Log "Parsing $Path"
    $rows = Import-Csv -Path $Path
    $result = New-Object System.Collections.Generic.List[object]

    foreach ($r in $rows) {
        # Try several column name variants (harvester output differs slightly by version)
        $name     = Coalesce $r.Name         $r.DatasourceName $r.DSN      $r.dsn_name
        $driver   = Coalesce $r.Driver       $r.DriverType     $r.driver
        $server   = Coalesce $r.Server       $r.Host           $r.HostName $r.server_host
        $port     = Coalesce $r.Port         $r.PortNumber     $r.server_port
        $database = Coalesce $r.Database     $r.DbName         $r.InitialCatalog
        $user     = Coalesce $r.Username     $r.User           $r.UID      $r.login
        $dbType   = Coalesce $r.DbType       $r.DatabaseType   $r.Type

        # If DbType wasn't in the CSV, derive it from Driver
        if ([string]::IsNullOrWhiteSpace($dbType) -or $dbType -eq "Unknown") {
            $typeInfo = Get-DbTypeAndPort -Driver $driver -ExistingPort $port
            $dbType = $typeInfo.DbType
            $port   = $typeInfo.Port
        }

        $obj = [pscustomobject]@{
            Source          = "08_datasources.csv"
            DsnName         = $name
            Driver          = $driver
            Server          = $server
            Port            = $port
            Database        = $database
            Username        = $user
            DbType          = $dbType
        }
        $result.Add($obj) | Out-Null
    }

    Write-Log ("  Parsed {0} datasource rows" -f $result.Count) 'OK'
    return $result
}

# -----------------------------------------------------------------------------
# Resolve CMC source info
# -----------------------------------------------------------------------------
function Resolve-CmcSource {
    param([string]$Hostname, [string]$ExplicitIp, [string]$Cidr)

    if (-not [string]::IsNullOrWhiteSpace($Cidr)) {
        return @{ Source = $Cidr; Type = "CIDR" }
    }

    $ip = $ExplicitIp
    if ([string]::IsNullOrWhiteSpace($ip)) {
        try {
            $resolved = [System.Net.Dns]::GetHostAddresses($Hostname) |
                Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
                Select-Object -First 1
            if ($resolved) { $ip = $resolved.IPAddressToString }
        } catch {
            Write-Log ("Could not resolve {0}: {1}" -f $Hostname, $_.Exception.Message) 'WARN'
        }
    }

    if ([string]::IsNullOrWhiteSpace($ip)) {
        return @{ Source = $Hostname; Type = "Hostname (unresolved)" }
    }
    return @{ Source = ("{0} ({1})" -f $Hostname, $ip); Type = "Hostname" }
}

# =============================================================================
# MAIN
# =============================================================================
try {
    Ensure-Dir $OutputDir
    if ([string]::IsNullOrWhiteSpace($TicketId)) {
        $TicketId = "ACL-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
    }

    Write-Log "=== MSTR ACL Request Builder ==="
    Write-Log ("Ticket      : {0}" -f $TicketId)
    Write-Log ("Environment : {0}" -f $Environment)
    Write-Log ("CMC host    : {0}" -f $CmcHost)
    Write-Log ("Output dir  : {0}" -f $OutputDir)

    if ([string]::IsNullOrWhiteSpace($OdbcFile) -and [string]::IsNullOrWhiteSpace($DatasourcesCsv)) {
        throw "Provide at least one of -OdbcFile or -DatasourcesCsv"
    }

    # ---- Collect all DB connections ----------------------------------------
    $all = New-Object System.Collections.Generic.List[object]

    if (-not [string]::IsNullOrWhiteSpace($OdbcFile)) {
        $fromIni = Parse-OdbcIni -Path $OdbcFile
        foreach ($r in $fromIni) { $all.Add($r) | Out-Null }
    }
    if (-not [string]::IsNullOrWhiteSpace($DatasourcesCsv)) {
        $fromCsv = Parse-DatasourcesCsv -Path $DatasourcesCsv
        foreach ($r in $fromCsv) { $all.Add($r) | Out-Null }
    }

    if ($all.Count -eq 0) {
        throw "No DB connections parsed. Check your input files."
    }

    # ---- Filter out unknowns (unless asked to keep) ------------------------
    if (-not $IncludeUnknown) {
        $filtered = $all | Where-Object { $_.DbType -ne "Unknown" -and -not [string]::IsNullOrWhiteSpace($_.Server) }
    } else {
        $filtered = $all
    }

    # ---- Resolve CMC source ------------------------------------------------
    $cmc = Resolve-CmcSource -Hostname $CmcHost -ExplicitIp $CmcIp -Cidr $CmcCidr
    $cmcSource = $cmc.Source

    # ---- Build ACL rows ----------------------------------------------------
    $aclRows = New-Object System.Collections.Generic.List[object]
    foreach ($r in $filtered) {
        $port = $r.Port
        if ([string]::IsNullOrWhiteSpace($port)) { $port = "TBD" }

        $aclRows.Add([pscustomobject]@{
            TicketId         = $TicketId
            Environment      = $Environment
            Source           = $cmcSource
            SourceType       = $cmc.Type
            Destination      = $r.Server
            DestinationPort  = $port
            Protocol         = "TCP"
            DbType           = $r.DbType
            DsnName          = $r.DsnName
            Database         = $r.Database
            Requester        = $Requester
            Justification    = $BusinessJustification
            OriginSource     = $r.Source
        }) | Out-Null
    }

    # ---- Deduplicate by destination host+port (if requested) ---------------
    if ($DeduplicateByHostPort) {
        $seen = @{}
        $dedup = New-Object System.Collections.Generic.List[object]
        foreach ($row in $aclRows) {
            $key = ("{0}|{1}" -f $row.Destination, $row.DestinationPort)
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $dedup.Add($row) | Out-Null
            }
        }
        $aclRows = $dedup
        Write-Log ("Deduplicated to {0} unique destinations" -f $aclRows.Count)
    }

    # ---- Write outputs -----------------------------------------------------
    $csvPath  = Join-Path $OutputDir ("ACL_Request_{0}.csv"  -f $TicketId)
    $txtPath  = Join-Path $OutputDir ("ACL_Request_{0}.txt"  -f $TicketId)
    $jsonPath = Join-Path $OutputDir ("ACL_Request_{0}.json" -f $TicketId)

    $aclRows | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8

    # --- Plain text / email-ready ---
    $txt = @()
    $txt += "============================================================================"
    $txt += (" MSTR MIGRATION - FIREWALL / ACL OPENING REQUEST")
    $txt += "============================================================================"
    $txt += (" Ticket ID     : {0}" -f $TicketId)
    $txt += (" Environment   : {0}" -f $Environment)
    $txt += (" Requester     : {0}" -f $Requester)
    $txt += (" Generated     : {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    $txt += (" Justification : {0}" -f $BusinessJustification)
    $txt += ""
    $txt += (" Source (CMC)  : {0}" -f $cmcSource)
    $txt += ""
    $txt += (" Total rules requested : {0}" -f $aclRows.Count)
    $txt += ""
    $txt += "-----------------------------------------------------------------------------"
    $txt += " #   Destination Host                          Port   Proto  DB Type"
    $txt += "-----------------------------------------------------------------------------"

    $i = 1
    foreach ($row in $aclRows) {
        $line = ("{0,3}  {1,-40}  {2,-5}  {3,-5}  {4}" -f $i, $row.Destination, $row.DestinationPort, $row.Protocol, $row.DbType)
        $txt += $line
        $i++
    }

    $txt += "-----------------------------------------------------------------------------"
    $txt += ""
    $txt += " Please open TCP connectivity from the source above to each destination"
    $txt += " host on the listed port. Bi-directional return path is required for"
    $txt += " established connections."
    $txt += ""
    $txt += "============================================================================"

    $txt | Out-File -FilePath $txtPath -Encoding UTF8

    # --- JSON (for ServiceNow / Jira automation) ---
    $jsonDoc = [ordered]@{
        ticketId        = $TicketId
        environment     = $Environment
        requester       = $Requester
        generated       = (Get-Date -Format "o")
        source          = @{ hostname = $CmcHost; ip = $CmcIp; cidr = $CmcCidr; resolved = $cmcSource }
        justification   = $BusinessJustification
        ruleCount       = $aclRows.Count
        rules           = @($aclRows | ForEach-Object {
            [ordered]@{
                source           = $_.Source
                destinationHost  = $_.Destination
                destinationPort  = $_.DestinationPort
                protocol         = $_.Protocol
                dbType           = $_.DbType
                dsnName          = $_.DsnName
                database         = $_.Database
            }
        })
    }
    $jsonDoc | ConvertTo-Json -Depth 6 | Out-File -FilePath $jsonPath -Encoding UTF8

    # ---- Summary ------------------------------------------------------------
    Write-Log ("CSV  : {0}" -f $csvPath)  'OK'
    Write-Log ("TXT  : {0}" -f $txtPath)  'OK'
    Write-Log ("JSON : {0}" -f $jsonPath) 'OK'
    Write-Log ("Rules: {0}" -f $aclRows.Count) 'OK'
    Write-Log "DONE" 'OK'
}
catch {
    Write-Log $_.Exception.Message 'ERROR'
    exit 1
}
