<#
================================================================================
  Invoke-MSTRConnectivityTester.ps1
  MicroStrategy Connectivity Tester — Windows / Citrix Edition
  Version 2.1 | PowerShell 5.1+ | No Python Required

  PURPOSE:
    Phase 1 / Phase 3 — Reads database connection information from Windows ODBC
    (registry) and/or an odbc.ini file, then tests DNS resolution, ICMP ping,
    and TCP port reachability for each connection.
    Also verifies connectivity to the CMC (cloud Intelligence Server) port.

    Functionally equivalent to mstr_connectivity_tester.py but native Windows.

  USAGE:
    # Read from Windows ODBC registry (default, no odbc.ini needed)
    .\Invoke-MSTRConnectivityTester.ps1 `
        -CMCHost "cloud-mstr.company.com" `
        -CMCPort 34952

    # Also include an odbc.ini file
    .\Invoke-MSTRConnectivityTester.ps1 `
        -OdbcFile "C:\MSTR\odbc.ini" `
        -CMCHost  "cloud-mstr.company.com" `
        -CMCPort  34952

    # Skip ping (ICMP blocked by firewall)
    .\Invoke-MSTRConnectivityTester.ps1 `
        -CMCHost "cloud-mstr.company.com" `
        -SkipPing

  OUTPUT:
    db_connections_inventory.csv   All parsed DSN entries: host, port, DB type
    connectivity_results.csv       DNS + Ping + TCP test results per connection
    CONNECTIVITY_REPORT.txt        Human-readable pass/fail summary

  AUTHOR: MicroStrategy Admin Automation Toolkit (Windows/Citrix Port)
  VERSION: 2.1 — Citrix/Windows Native
================================================================================
#>

[CmdletBinding()]
param(
    [string]$OdbcFile    = "",
    [string]$OdbcFileUser = "",

    [Parameter(Mandatory=$true)]
    [string]$CMCHost,

    [int]$CMCPort        = 34952,
    [string]$OutputDir   = ".\connectivity_results",
    [switch]$SkipPing,
    [int]$TcpTimeoutMs   = 5000,
    [int]$PingTimeoutMs  = 3000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# ─────────────────────────────────────────────────────────────
# DB TYPE DETECTION
# ─────────────────────────────────────────────────────────────

$DB_TYPE_RULES = @(
    @{ Keywords=@("sqlserver","sql server","mssql","ms sql"); Label="Microsoft SQL Server"; Port=1433 },
    @{ Keywords=@("oracle");                                   Label="Oracle Database";      Port=1521 },
    @{ Keywords=@("mysql");                                    Label="MySQL";                Port=3306 },
    @{ Keywords=@("mariadb");                                  Label="MariaDB";              Port=3306 },
    @{ Keywords=@("postgresql","postgre","psql"," pg");        Label="PostgreSQL";           Port=5432 },
    @{ Keywords=@("teradata");                                 Label="Teradata";             Port=1025 },
    @{ Keywords=@("snowflake");                                Label="Snowflake";            Port=443  },
    @{ Keywords=@("redshift");                                 Label="Amazon Redshift";      Port=5439 },
    @{ Keywords=@("bigquery");                                 Label="Google BigQuery";      Port=443  },
    @{ Keywords=@("hive");                                     Label="Apache Hive";          Port=10000},
    @{ Keywords=@("spark");                                    Label="Apache Spark";         Port=10001},
    @{ Keywords=@("db2","ibm db");                             Label="IBM DB2";              Port=50000},
    @{ Keywords=@("sybase","ase");                             Label="Sybase ASE";           Port=5000 },
    @{ Keywords=@("impala");                                   Label="Apache Impala";        Port=21050},
    @{ Keywords=@("presto","trino");                           Label="Presto/Trino";         Port=8080 },
    @{ Keywords=@("vertica");                                  Label="Vertica";              Port=5433 },
    @{ Keywords=@("netezza");                                  Label="IBM Netezza";          Port=5480 },
    @{ Keywords=@("greenplum");                                Label="Greenplum";            Port=5432 },
    @{ Keywords=@("athena");                                   Label="Amazon Athena";        Port=443  },
    @{ Keywords=@("azure synapse","synapse");                  Label="Azure Synapse";        Port=1433 },
    @{ Keywords=@("databricks");                               Label="Databricks";           Port=443  },
    @{ Keywords=@("sap hana","hana");                         Label="SAP HANA";             Port=30015},
    @{ Keywords=@("informix");                                 Label="IBM Informix";         Port=9088 },
    @{ Keywords=@("progress","openedge");                      Label="Progress OpenEdge";    Port=9999 }
)

$DB_CATEGORIES = @{
    "Microsoft SQL Server" = "Relational - Microsoft"
    "Oracle Database"      = "Relational - Oracle"
    "MySQL"                = "Relational - Open Source"
    "MariaDB"              = "Relational - Open Source"
    "PostgreSQL"           = "Relational - Open Source"
    "IBM DB2"              = "Relational - IBM"
    "Sybase ASE"           = "Relational - SAP/Sybase"
    "SAP HANA"             = "Relational - SAP/Sybase"
    "Teradata"             = "Enterprise DW"
    "Vertica"              = "Enterprise DW"
    "IBM Netezza"          = "Enterprise DW"
    "Greenplum"            = "Enterprise DW"
    "Snowflake"            = "Cloud Data Warehouse"
    "Amazon Redshift"      = "Cloud Data Warehouse"
    "Google BigQuery"      = "Cloud Data Warehouse"
    "Azure Synapse"        = "Cloud Data Warehouse"
    "Databricks"           = "Cloud Data Warehouse"
    "Amazon Athena"        = "Cloud Data Warehouse"
    "Apache Hive"          = "Big Data / Hadoop"
    "Apache Spark"         = "Big Data / Hadoop"
    "Apache Impala"        = "Big Data / Hadoop"
    "Presto/Trino"         = "Big Data / Hadoop"
    "IBM Informix"         = "Specialty"
    "Progress OpenEdge"    = "Specialty"
}

function Get-DbTypeFromString {
    param([string]$DriverStr, [string]$DsnName)
    $search = ($DriverStr + " " + $DsnName).ToLower()
    foreach ($rule in $DB_TYPE_RULES) {
        foreach ($kw in $rule.Keywords) {
            if ($search -match [regex]::Escape($kw)) {
                return @{ Label=$rule.Label; Port=$rule.Port }
            }
        }
    }
    return @{ Label="Unknown / Other"; Port=0 }
}

# ─────────────────────────────────────────────────────────────
# WINDOWS ODBC REGISTRY READER
# ─────────────────────────────────────────────────────────────

function Read-WindowsOdbc {
    $connections = [System.Collections.Generic.List[PSCustomObject]]::new()
    $odbcPaths = @(
        "HKLM:\SOFTWARE\ODBC\ODBC.INI",
        "HKLM:\SOFTWARE\WOW6432Node\ODBC\ODBC.INI",
        "HKCU:\SOFTWARE\ODBC\ODBC.INI"
    )

    foreach ($regPath in $odbcPaths) {
        if (-not (Test-Path $regPath)) { continue }

        $dsnRoot = Get-ChildItem $regPath -ErrorAction SilentlyContinue
        foreach ($dsn in $dsnRoot) {
            $dsnName = $dsn.PSChildName
            if ($dsnName -eq "ODBC Data Sources") { continue }

            try {
                $props   = Get-ItemProperty $dsn.PSPath -ErrorAction SilentlyContinue
                $driver  = [string]($props.Driver + " " + $props.DRIVER)
                $server  = [string]($props.Server ?? $props.SERVER ?? $props.Host ?? $props.HOST ??
                            $props.Servername ?? $props.AccountName ?? "")
                $portStr = [string]($props.Port ?? $props.PORT ?? "")
                $dbname  = [string]($props.Database ?? $props.DATABASE ?? $props.DBName ??
                            $props.DefaultDB ?? "")
                $uid     = [string]($props.UID ?? $props.uid ?? $props.User ?? "")
                $hasPwd  = if ($props.PWD -or $props.pwd -or $props.Password) { "Yes" } else { "No" }

                if (-not $server -and -not $dbname) { continue }

                $dbInfo = Get-DbTypeFromString -DriverStr $driver -DsnName $dsnName
                $port   = 0
                if ($portStr -match "^\d+$") { $port = [int]$portStr }
                if ($port -eq 0) { $port = $dbInfo.Port }

                $category = if ($DB_CATEGORIES.ContainsKey($dbInfo.Label)) { $DB_CATEGORIES[$dbInfo.Label] } else { "Unknown / Other" }

                $connections.Add([PSCustomObject]@{
                    dsn_name       = $dsnName
                    server         = $server.Trim()
                    port           = $port
                    database       = $dbname.Trim()
                    db_type        = $dbInfo.Label
                    category       = $category
                    driver         = $driver.Trim()
                    uid            = $uid.Trim()
                    has_password   = $hasPwd
                    source         = "Windows Registry ($regPath)"
                    dns_resolves   = ""
                    resolved_ip    = ""
                    ping_status    = ""
                    ping_latency_ms = ""
                    tcp_port_status = ""
                    tcp_port_latency_ms = ""
                })
            } catch {
                Write-Verbose "Skipping DSN '$dsnName': $($_.Exception.Message)"
            }
        }
    }
    return $connections
}

# ─────────────────────────────────────────────────────────────
# ODBC.INI FILE READER
# ─────────────────────────────────────────────────────────────

function Read-OdbcIniFile {
    param([string]$FilePath)

    $connections = [System.Collections.Generic.List[PSCustomObject]]::new()
    if (-not (Test-Path $FilePath)) {
        Write-Warning "  odbc.ini not found: $FilePath"
        return $connections
    }

    $lines = Get-Content $FilePath -Encoding UTF8 -ErrorAction SilentlyContinue
    $currentSection = ""
    $sectionData    = @{}
    $sections       = [System.Collections.Generic.List[hashtable]]::new()

    foreach ($line in $lines) {
        $line = $line.Trim()
        if ($line -match "^\[(.+)\]$") {
            if ($currentSection -and $sectionData.Count -gt 0) {
                $sections.Add(@{ Name=$currentSection; Data=$sectionData.Clone() })
            }
            $currentSection = $Matches[1]
            $sectionData    = @{}
        } elseif ($line -match "^([^=;#]+)=(.*)$") {
            $sectionData[$Matches[1].Trim()] = $Matches[2].Trim()
        }
    }
    if ($currentSection -and $sectionData.Count -gt 0) {
        $sections.Add(@{ Name=$currentSection; Data=$sectionData.Clone() })
    }

    $skipSections = @("odbc data sources","odbc","odbc driver manager")

    foreach ($sec in $sections) {
        if ($skipSections -contains $sec.Name.ToLower()) { continue }

        $d = $sec.Data
        function Get-Val {
            param([string[]]$Keys)
            foreach ($k in $Keys) {
                foreach ($dk in $d.Keys) {
                    if ($dk.ToLower() -eq $k.ToLower()) { return $d[$dk] }
                }
            }
            return ""
        }

        $driver   = Get-Val "Driver","DRIVER"
        $server   = Get-Val "Server","SERVER","Host","HOST","Servername","Hostname","AccountName"
        $portStr  = Get-Val "Port","PORT"
        $dbname   = Get-Val "Database","DATABASE","DBName","DefaultDB","Catalog"
        $uid      = Get-Val "UID","uid","User","Username","UserName"
        $hasPwd   = if ((Get-Val "PWD","pwd","Password","password")) { "Yes" } else { "No" }

        if (-not $server -and -not $dbname) { continue }

        $dbInfo   = Get-DbTypeFromString -DriverStr $driver -DsnName $sec.Name
        $port     = 0
        if ($portStr -match "^\d+$") { $port = [int]$portStr }
        if ($port -eq 0) { $port = $dbInfo.Port }

        $category = if ($DB_CATEGORIES.ContainsKey($dbInfo.Label)) { $DB_CATEGORIES[$dbInfo.Label] } else { "Unknown / Other" }

        $connections.Add([PSCustomObject]@{
            dsn_name            = $sec.Name
            server              = $server.Trim()
            port                = $port
            database            = $dbname.Trim()
            db_type             = $dbInfo.Label
            category            = $category
            driver              = $driver.Trim()
            uid                 = $uid.Trim()
            has_password        = $hasPwd
            source              = "File: $FilePath"
            dns_resolves        = ""
            resolved_ip         = ""
            ping_status         = ""
            ping_latency_ms     = ""
            tcp_port_status     = ""
            tcp_port_latency_ms = ""
        })
    }
    return $connections
}

# ─────────────────────────────────────────────────────────────
# CONNECTIVITY TESTS
# ─────────────────────────────────────────────────────────────

function Test-DnsResolve {
    param([string]$Hostname)
    if (-not $Hostname) { return @{ Status="NO_HOSTNAME"; IP="" } }
    try {
        $result = [System.Net.Dns]::GetHostAddresses($Hostname)
        $ip = ($result | Select-Object -First 1).IPAddressToString
        return @{ Status="RESOLVED"; IP=$ip }
    } catch [System.Net.Sockets.SocketException] {
        return @{ Status="DNS_FAIL"; IP="" }
    } catch {
        return @{ Status="DNS_ERROR"; IP="" }
    }
}

function Test-IcmpPing {
    param([string]$Hostname, [int]$TimeoutMs=3000)
    if (-not $Hostname) { return @{ Status="NO_HOSTNAME"; LatencyMs="" } }
    if ($SkipPing) { return @{ Status="SKIPPED"; LatencyMs="" } }

    try {
        $ping   = New-Object System.Net.NetworkInformation.Ping
        $reply  = $ping.Send($Hostname, $TimeoutMs)

        if ($reply.Status -eq "Success") {
            return @{ Status="REACHABLE"; LatencyMs="$($reply.RoundtripTime) ms" }
        } elseif ($reply.Status -eq "TimedOut") {
            return @{ Status="TIMEOUT"; LatencyMs="" }
        } else {
            return @{ Status=$reply.Status.ToString(); LatencyMs="" }
        }
    } catch [System.Net.NetworkInformation.PingException] {
        return @{ Status="DNS_FAIL"; LatencyMs="" }
    } catch {
        return @{ Status="ERROR"; LatencyMs="" }
    }
}

function Test-TcpPort {
    param([string]$Hostname, [int]$Port, [int]$TimeoutMs=5000)
    if (-not $Hostname) { return @{ Status="NO_HOSTNAME"; LatencyMs="" } }
    if ($Port -le 0)    { return @{ Status="NO_PORT";     LatencyMs="" } }

    $client    = $null
    $startTime = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async  = $client.BeginConnect($Hostname, $Port, $null, $null)
        $waited = $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        $startTime.Stop()

        if ($waited) {
            try { $client.EndConnect($async) } catch { }
            if ($client.Connected) {
                return @{ Status="OPEN"; LatencyMs="$($startTime.ElapsedMilliseconds) ms" }
            } else {
                return @{ Status="CLOSED"; LatencyMs="" }
            }
        } else {
            return @{ Status="TIMEOUT"; LatencyMs="" }
        }
    } catch [System.Net.Sockets.SocketException] {
        $startTime.Stop()
        $msg = $_.Exception.Message.ToLower()
        if ($msg -match "refused") { return @{ Status="CLOSED"; LatencyMs="" } }
        if ($msg -match "name.*not.*resolve|hostname") { return @{ Status="DNS_FAIL"; LatencyMs="" } }
        if ($msg -match "network.*unreachable|no route") { return @{ Status="NO_ROUTE"; LatencyMs="" } }
        return @{ Status="ERROR"; LatencyMs="" }
    } catch {
        $startTime.Stop()
        return @{ Status="ERROR"; LatencyMs="" }
    } finally {
        if ($client) { $client.Close() }
    }
}

# ─────────────────────────────────────────────────────────────
# CONNECTIVITY REPORT WRITER
# ─────────────────────────────────────────────────────────────

function Write-ConnectivityReport {
    param(
        [System.Collections.Generic.List[PSCustomObject]]$Connections,
        [hashtable]$CmcResults,
        [string]$ReportTime
    )

    $path = Join-Path $OutputDir "CONNECTIVITY_REPORT.txt"
    $sep  = "=" * 80
    $thin = "-" * 80
    $out  = [System.Collections.Generic.List[string]]::new()

    $out.Add($sep)
    $out.Add("  MICROSTRATEGY CONNECTIVITY TEST REPORT")
    $out.Add("  Generated by Invoke-MSTRConnectivityTester.ps1 (Windows/Citrix Edition)")
    $out.Add($sep)
    $out.Add("  Report Time  : $ReportTime")
    $out.Add("  CMC Host     : $CMCHost")
    $out.Add("  CMC Port     : $CMCPort")
    $out.Add("  Total DSNs   : $($Connections.Count)")
    $out.Add($sep)

    # CMC Section
    $out.Add(""); $out.Add($sep); $out.Add("  1. CMC (CLOUD IS) CONNECTIVITY"); $out.Add($sep)
    $out.Add("  DNS Status   : $($CmcResults.DnsStatus) ($($CmcResults.DnsIp))")
    $out.Add("  Ping Status  : $($CmcResults.PingStatus) $($CmcResults.PingLatency)")
    $out.Add("  TCP Port     : $($CmcResults.TcpStatus) $($CmcResults.TcpLatency)")
    $out.Add("")
    if ($CmcResults.TcpStatus -eq "OPEN") {
        $out.Add("  [PASS] CMC IS port is reachable from this Citrix server.")
    } else {
        $out.Add("  [FAIL] CMC IS port NOT reachable. Check firewall / VPN / security group rules.")
        $out.Add("         Ensure port $CMCPort is open FROM this Citrix server TO $CMCHost")
    }

    # Category summary
    $out.Add(""); $out.Add($sep); $out.Add("  2. DB CONNECTION SUMMARY BY CATEGORY"); $out.Add($sep)
    $catGroups = $Connections | Group-Object category
    $out.Add("  {0,-35} {1,6} {2,8} {3,9}" -f "Category","Total","TCP OK","TCP FAIL")
    $out.Add("  $thin")
    foreach ($g in $catGroups | Sort-Object Name) {
        $tcpOk   = ($g.Group | Where-Object { $_.tcp_port_status -eq "OPEN" }).Count
        $tcpFail = ($g.Group | Where-Object { $_.tcp_port_status -in @("CLOSED","TIMEOUT","DNS_FAIL","NO_ROUTE","ERROR") }).Count
        $out.Add("  {0,-35} {1,6} {2,8} {3,9}" -f $g.Name, $g.Count, $tcpOk, $tcpFail)
    }

    # Overall
    $total   = $Connections.Count
    $tcpOpen = ($Connections | Where-Object { $_.tcp_port_status -eq "OPEN" }).Count
    $tcpFail = ($Connections | Where-Object { $_.tcp_port_status -in @("CLOSED","TIMEOUT","DNS_FAIL","NO_ROUTE","ERROR") }).Count
    $dnsOk   = ($Connections | Where-Object { $_.dns_resolves -eq "RESOLVED" }).Count
    $pct     = if ($total -gt 0) { [Math]::Round(100*$tcpOpen/$total) } else { 0 }

    $out.Add(""); $out.Add($sep); $out.Add("  3. OVERALL SUMMARY"); $out.Add($sep)
    $out.Add("  Total DSN Connections   : $total")
    $out.Add("  DNS Resolvable          : $dnsOk / $total")
    $out.Add("  TCP Port OPEN           : $tcpOpen / $total ($pct%)")
    $out.Add("  TCP Port FAILURES       : $tcpFail")
    $out.Add("")
    if ($tcpFail -eq 0) { $out.Add("  [PASS] All DB connections reachable.") }
    else                { $out.Add("  [WARN] $tcpFail connection(s) failed — see Section 4.") }

    # Failures
    $failed = $Connections | Where-Object {
        $_.tcp_port_status -in @("CLOSED","TIMEOUT","DNS_FAIL","NO_ROUTE","ERROR") -or
        $_.dns_resolves -eq "DNS_FAIL"
    }
    if ($failed) {
        $out.Add(""); $out.Add($sep); $out.Add("  4. FAILED CONNECTIONS — ACTION REQUIRED"); $out.Add($sep)
        $out.Add("  {0,-35} {1,-35} {2,5} {3,10} {4}" -f "DSN","Server","Port","DNS","TCP Status")
        $out.Add("  $thin")
        foreach ($c in $failed) {
            $out.Add("  {0,-35} {1,-35} {2,5} {3,10} {4}" -f `
                $c.dsn_name.Substring(0,[Math]::Min(34,$c.dsn_name.Length)), `
                $c.server.Substring(0,[Math]::Min(34,$c.server.Length)), `
                $c.port, $c.dns_resolves, $c.tcp_port_status)
        }
        $out.Add("")
        $out.Add("  RECOMMENDED ACTIONS:")
        $dnsFails = $failed | Where-Object { $_.dns_resolves -eq "DNS_FAIL" }
        $tcpFails = $failed | Where-Object { $_.tcp_port_status -in @("CLOSED","TIMEOUT","NO_ROUTE") }
        if ($dnsFails) { $out.Add("  - $($dnsFails.Count) DNS failure(s): Add DNS entries or update %WINDIR%\system32\drivers\etc\hosts") }
        if ($tcpFails) { $out.Add("  - $($tcpFails.Count) TCP failure(s): Open firewall rules for listed host:port combinations") }
    }

    # Passing
    $passing = $Connections | Where-Object { $_.tcp_port_status -eq "OPEN" }
    if ($passing) {
        $out.Add(""); $out.Add($sep); $out.Add("  5. PASSING CONNECTIONS"); $out.Add($sep)
        $out.Add("  {0,-35} {1,-35} {2,5} {3}" -f "DSN","Server","Port","Latency")
        $out.Add("  $thin")
        foreach ($c in $passing) {
            $out.Add("  {0,-35} {1,-35} {2,5} {3}" -f `
                $c.dsn_name.Substring(0,[Math]::Min(34,$c.dsn_name.Length)), `
                $c.server.Substring(0,[Math]::Min(34,$c.server.Length)), `
                $c.port, $c.tcp_port_latency_ms)
        }
    }

    $out.Add(""); $out.Add($sep); $out.Add("  6. NEXT STEPS"); $out.Add($sep)
    $out.Add("  1. Fix all FAILED entries (Section 4) before cloud go-live.")
    $out.Add("  2. Ensure CMC port (Section 1) is OPEN — mandatory for MSTR IS.")
    $out.Add("  3. Feed CONNECTIVITY_REPORT.txt to AI:")
    $out.Add("     'For each failed connection, explain the likely cause and provide")
    $out.Add("      the exact Windows firewall command or DNS fix to resolve it.'")
    $out.Add("  4. Re-run this script after fixes to confirm all connections OPEN.")
    $out.Add($sep)

    $out | Out-File -FilePath $path -Encoding UTF8
    Write-Host "  [OK] CONNECTIVITY_REPORT.txt written" -ForegroundColor Green
}

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "=" * 72 -ForegroundColor Cyan
Write-Host "  Invoke-MSTRConnectivityTester.ps1 — Windows/Citrix Edition v2.1" -ForegroundColor Cyan
Write-Host "  CMC Host : $CMCHost : $CMCPort" -ForegroundColor Cyan
Write-Host "  Output   : $OutputDir" -ForegroundColor Cyan
Write-Host "=" * 72 -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$reportTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# ── Collect DSN entries ───────────────────────────────────────
$allConnections = [System.Collections.Generic.List[PSCustomObject]]::new()

# Windows ODBC registry (always read unless overridden)
Write-Host "`n  Reading Windows ODBC registry..." -ForegroundColor Cyan
$regConns = Read-WindowsOdbc
Write-Host "  Found $($regConns.Count) DSN(s) in Windows ODBC registry" -ForegroundColor $(if ($regConns.Count -gt 0) { "Green" } else { "Yellow" })
foreach ($c in $regConns) { $allConnections.Add($c) }

# odbc.ini file (optional)
foreach ($f in @($OdbcFile, $OdbcFileUser) | Where-Object { $_ }) {
    Write-Host "  Reading odbc.ini: $f..." -ForegroundColor Cyan
    $fileConns = Read-OdbcIniFile -FilePath $f
    Write-Host "  Found $($fileConns.Count) DSN(s) in $f" -ForegroundColor $(if ($fileConns.Count -gt 0) { "Green" } else { "Yellow" })
    foreach ($c in $fileConns) { $allConnections.Add($c) }
}

# Deduplicate by dsn_name
$seen    = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$deduped = [System.Collections.Generic.List[PSCustomObject]]::new()
foreach ($c in $allConnections) {
    if ($seen.Add($c.dsn_name)) { $deduped.Add($c) }
}
$allConnections = $deduped

if ($allConnections.Count -eq 0) {
    Write-Warning "  No DSN entries found. If DSNs are in an odbc.ini file, use -OdbcFile parameter."
}

Write-Host "  Total unique DSNs: $($allConnections.Count)" -ForegroundColor Cyan

# ── Write inventory CSV ───────────────────────────────────────
$inventoryPath = Join-Path $OutputDir "db_connections_inventory.csv"
$allConnections | Select-Object dsn_name,server,port,database,db_type,category,driver,uid,has_password,source |
    Export-Csv -Path $inventoryPath -NoTypeInformation -Encoding UTF8 -Force
Write-Host "  [OK] db_connections_inventory.csv ($($allConnections.Count) entries)" -ForegroundColor Green

# ── Test CMC connectivity ─────────────────────────────────────
Write-Host "`n  Testing CMC connectivity: $CMCHost : $CMCPort" -ForegroundColor Cyan
$cmcDns  = Test-DnsResolve -Hostname $CMCHost
$cmcPing = Test-IcmpPing   -Hostname $CMCHost -TimeoutMs $PingTimeoutMs
$cmcTcp  = Test-TcpPort    -Hostname $CMCHost -Port $CMCPort -TimeoutMs $TcpTimeoutMs

$cmcResults = @{
    DnsStatus   = $cmcDns.Status
    DnsIp       = $cmcDns.IP
    PingStatus  = $cmcPing.Status
    PingLatency = $cmcPing.LatencyMs
    TcpStatus   = $cmcTcp.Status
    TcpLatency  = $cmcTcp.LatencyMs
}

$cmcIcon = if ($cmcTcp.Status -eq "OPEN") { "[PASS]" } else { "[FAIL]" }
Write-Host "  CMC DNS   : $($cmcDns.Status) ($($cmcDns.IP))" -ForegroundColor $(if ($cmcDns.Status -eq "RESOLVED") { "Green" } else { "Red" })
Write-Host "  CMC Ping  : $($cmcPing.Status) $($cmcPing.LatencyMs)" -ForegroundColor $(if ($cmcPing.Status -eq "REACHABLE") { "Green" } else { "Yellow" })
Write-Host "  CMC TCP   : $cmcIcon $($cmcTcp.Status) $($cmcTcp.LatencyMs)" -ForegroundColor $(if ($cmcTcp.Status -eq "OPEN") { "Green" } else { "Red" })

# ── Test each DB connection ───────────────────────────────────
if ($allConnections.Count -gt 0) {
    Write-Host "`n  Testing $($allConnections.Count) DB connection(s)..." -ForegroundColor Cyan
    Write-Host ("  {0,3}  {1,-35} {2,-30} {3,5}  {4,-10}  {5,-12}  {6}" -f "#","DSN","Server","Port","DNS","Ping","TCP")
    Write-Host ("  " + "-"*110)

    $idx = 0
    foreach ($conn in $allConnections) {
        $idx++
        $server = $conn.server
        $port   = $conn.port

        # DNS
        $dns = Test-DnsResolve -Hostname $server
        $conn.dns_resolves  = $dns.Status
        $conn.resolved_ip   = $dns.IP

        # Ping
        $ping = Test-IcmpPing -Hostname $server -TimeoutMs $PingTimeoutMs
        $conn.ping_status       = $ping.Status
        $conn.ping_latency_ms   = $ping.LatencyMs

        # TCP
        $tcp = Test-TcpPort -Hostname $server -Port ([int]$port) -TimeoutMs $TcpTimeoutMs
        $conn.tcp_port_status       = $tcp.Status
        $conn.tcp_port_latency_ms   = $tcp.LatencyMs

        $tcpIcon = if ($tcp.Status -eq "OPEN") { "V" } elseif ($tcp.Status -in @("CLOSED","TIMEOUT","DNS_FAIL")) { "X" } else { "?" }
        $color   = if ($tcp.Status -eq "OPEN") { "Green" } elseif ($tcp.Status -in @("NO_PORT","SKIPPED","")) { "Gray" } else { "Red" }

        $dsnShort    = if ($conn.dsn_name.Length -gt 34) { $conn.dsn_name.Substring(0,34) } else { $conn.dsn_name }
        $serverShort = if ($server.Length -gt 29) { $server.Substring(0,29) } else { $server }

        Write-Host ("  {0,3}  {1,-35} {2,-30} {3,5}  {4,-10}  {5,-12}  {6} {7} {8}" -f `
            $idx, $dsnShort, $serverShort, $port, $dns.Status, $ping.Status, `
            $tcpIcon, $tcp.Status, $tcp.LatencyMs) -ForegroundColor $color

        Start-Sleep -Milliseconds 200
    }
}

# ── Write results CSV ─────────────────────────────────────────
$resultsPath = Join-Path $OutputDir "connectivity_results.csv"
$allConnections | Select-Object dsn_name,server,port,database,db_type,category,
    dns_resolves,resolved_ip,ping_status,ping_latency_ms,
    tcp_port_status,tcp_port_latency_ms,driver,source |
    Export-Csv -Path $resultsPath -NoTypeInformation -Encoding UTF8 -Force
Write-Host "`n  [OK] connectivity_results.csv written" -ForegroundColor Green

# ── Generate text report ──────────────────────────────────────
Write-ConnectivityReport -Connections $allConnections -CmcResults $cmcResults -ReportTime $reportTime

# ── Final summary ─────────────────────────────────────────────
$total   = $allConnections.Count
$tcpOpen = ($allConnections | Where-Object { $_.tcp_port_status -eq "OPEN" }).Count
$tcpFail = ($allConnections | Where-Object { $_.tcp_port_status -in @("CLOSED","TIMEOUT","DNS_FAIL","NO_ROUTE","ERROR") }).Count

Write-Host ""
Write-Host "=" * 72 -ForegroundColor $(if ($tcpFail -eq 0 -and $cmcResults.TcpStatus -eq "OPEN") { "Green" } else { "Yellow" })
Write-Host "  CONNECTIVITY TEST COMPLETE" -ForegroundColor Cyan
Write-Host "  CMC Port      : $(if ($cmcResults.TcpStatus -eq 'OPEN') { 'OPEN (PASS)' } else { "FAIL - $($cmcResults.TcpStatus)" })"
Write-Host "  DB Connections: $tcpOpen / $total reachable"
if ($tcpFail -gt 0) { Write-Host "  FAILURES      : $tcpFail — see CONNECTIVITY_REPORT.txt Section 4" -ForegroundColor Red }
Write-Host ""
Write-Host "  Output: $(Resolve-Path $OutputDir)"
Write-Host "  Feed CONNECTIVITY_REPORT.txt to AI for remediation steps."
Write-Host "=" * 72
