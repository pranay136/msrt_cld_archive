<#
================================================================================
  Invoke-MSTRHarvester.ps1
  MicroStrategy Metadata Harvester — Windows / Citrix Edition
  Version 2.1 | PowerShell 5.1+ | No Python Required

  PURPOSE:
    Phase 1 Discovery — extracts all metadata from a MicroStrategy on-prem or
    cloud Intelligence Server via REST API. Produces 21 CSVs + SUMMARY_REPORT.txt.
    Functionally equivalent to mstr_harvester.py but runs natively on Windows
    with no Python, no pip installs, and no dependencies beyond PowerShell.

  USAGE:
    .\Invoke-MSTRHarvester.ps1 `
        -Host    "https://YOUR-MSTR-SERVER/MicroStrategyLibrary" `
        -Username "Administrator" `
        -Password "YourPassword" `
        -OutputDir ".\discovery_output" `
        -AllProjects

  KEY FLAGS:
    -AllProjects        Harvest all projects (default: first 3)
    -ProjectId "ID"     Single project only
    -NoSslVerify        Skip SSL cert check (self-signed certs)
    -LoginMode 16       Auth mode: 1=Standard, 4=Kerberos, 8=DB, 16=LDAP, 64=SAML

  OUTPUT FILES:
    01_server_info.csv          Server version, OS, build, ports
    02_projects.csv             All projects: ID, name, status, owner
    03_users.csv                All users: login, type, enabled, email
    04_usergroups.csv           All groups: ID, name, member count
    05_group_membership.csv     User -> group mappings
    06_security_roles.csv       Security roles + privilege counts
    07_security_filters.csv     Security filter definitions
    08_datasources.csv          DB connections: host, port, db, type
    09_reports.csv              All reports per project
    10_documents_dossiers.csv   Documents and dossiers
    11_metrics.csv              Metric definitions
    12_attributes.csv           Attribute definitions
    13_facts.csv                Fact definitions
    14_filters.csv              Filter definitions
    15_prompts.csv              Prompt definitions
    16_schedules.csv            Schedules
    17_subscriptions.csv        Subscriptions
    18_caches.csv               Cache stats
    19_security_config.csv      LDAP / auth config
    20_email_config.csv         SMTP settings
    21_licenses.csv             License info
    SUMMARY_REPORT.txt          Human-readable summary with risk flags

  AUTHOR: MicroStrategy Admin Automation Toolkit (Windows/Citrix Port)
  VERSION: 2.1 — Citrix/Windows Native
================================================================================
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$Host,

    [Parameter(Mandatory=$true)]
    [string]$Username,

    [Parameter(Mandatory=$true)]
    [string]$Password,

    [string]$OutputDir = ".\discovery_output",

    [switch]$AllProjects,

    [string]$ProjectId = "",

    [switch]$NoSslVerify,

    [ValidateSet(1,4,8,16,64)]
    [int]$LoginMode = 1,

    [int]$MaxProjects = 3,

    [int]$PageLimit = 500,

    [int]$TimeoutSec = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# ─────────────────────────────────────────────────────────────
# SSL / TLS SETUP
# ─────────────────────────────────────────────────────────────

if ($NoSslVerify) {
    # PowerShell 5.1 method
    if (-not ([System.Management.Automation.PSTypeName]'TrustAllCertsPolicy').Type) {
        Add-Type @"
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class TrustAllCertsPolicy : ICertificatePolicy {
    public bool CheckValidationResult(ServicePoint srvPoint, X509Certificate certificate,
        WebRequest request, int certificateProblem) { return true; }
}
"@
    }
    [System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCertsPolicy
}
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

# ─────────────────────────────────────────────────────────────
# GLOBALS
# ─────────────────────────────────────────────────────────────

$BaseUrl  = $Host.TrimEnd("/") + "/api"
$Script:AuthToken  = ""
$Script:SessionCookies = $null
$Script:RiskFlags  = [System.Collections.Generic.List[string]]::new()
$Script:StartTime  = Get-Date

# ─────────────────────────────────────────────────────────────
# HELPER: API CALL
# ─────────────────────────────────────────────────────────────

function Invoke-MSTRApi {
    param(
        [string]$Endpoint,
        [string]$Method = "GET",
        [hashtable]$Body = $null,
        [string]$ProjectID = "",
        [switch]$RawResponse
    )

    $uri = "$BaseUrl$Endpoint"
    $headers = @{
        "Content-Type"    = "application/json"
        "Accept"          = "application/json"
    }
    if ($Script:AuthToken) {
        $headers["X-MSTR-AuthToken"] = $Script:AuthToken
    }
    if ($ProjectID) {
        $headers["X-MSTR-ProjectID"] = $ProjectID
    }

    $params = @{
        Uri             = $uri
        Method          = $Method
        Headers         = $headers
        TimeoutSec      = $TimeoutSec
        UseBasicParsing = $true
    }
    if ($NoSslVerify -and $PSVersionTable.PSVersion.Major -ge 6) {
        $params["SkipCertificateCheck"] = $true
    }
    if ($Body) {
        $params["Body"] = ($Body | ConvertTo-Json -Depth 10 -Compress)
    }

    try {
        if ($RawResponse) {
            return Invoke-WebRequest @params
        } else {
            $resp = Invoke-WebRequest @params
            if ($resp.Content) {
                return $resp.Content | ConvertFrom-Json
            }
            return $null
        }
    } catch {
        $status = $_.Exception.Response.StatusCode.value__
        if ($status -eq 401) {
            Write-Warning "  [AUTH] 401 Unauthorized on $Endpoint — token may have expired."
        } elseif ($status -eq 403) {
            Write-Warning "  [403] Forbidden: $Endpoint — check admin privileges."
        } elseif ($status -eq 404) {
            Write-Verbose "  [404] Not Found: $Endpoint — endpoint may not exist on this MSTR version."
        } else {
            Write-Warning "  [ERR] $Method $Endpoint failed: $($_.Exception.Message)"
        }
        return $null
    }
}

# Paginated GET — handles MSTR's offset/limit pagination
function Get-MSTRPaged {
    param(
        [string]$Endpoint,
        [string]$ProjectID = "",
        [string]$ResultKey = ""
    )

    $all = [System.Collections.Generic.List[object]]::new()
    $offset = 0

    do {
        $sep = if ($Endpoint -match "\?") { "&" } else { "?" }
        $url = "$Endpoint${sep}limit=$PageLimit&offset=$offset"
        $resp = Invoke-MSTRApi -Endpoint $url -ProjectID $ProjectID

        if (-not $resp) { break }

        # Some endpoints return array directly, some wrap in a key
        $items = $null
        if ($ResultKey -and $resp.$ResultKey) {
            $items = $resp.$ResultKey
        } elseif ($resp -is [System.Array]) {
            $items = $resp
        } elseif ($resp.PSObject.Properties.Name -contains "items") {
            $items = $resp.items
        } else {
            $items = @($resp)
        }

        if (-not $items -or $items.Count -eq 0) { break }

        foreach ($item in $items) { $all.Add($item) }

        if ($items.Count -lt $PageLimit) { break }
        $offset += $PageLimit

    } while ($true)

    return $all
}

# ─────────────────────────────────────────────────────────────
# CSV WRITER
# ─────────────────────────────────────────────────────────────

function Export-MSTRCsv {
    param(
        [string]$Filename,
        [object[]]$Data,
        [string[]]$Fields
    )

    $path = Join-Path $OutputDir $Filename
    if (-not $Data -or $Data.Count -eq 0) {
        # Write header-only file so downstream validator doesn't skip it
        ($Fields -join ",") | Out-File -FilePath $path -Encoding utf8
        Write-Host "  [EMPTY] $Filename (0 records)" -ForegroundColor Yellow
        return
    }

    $rows = foreach ($item in $Data) {
        $row = [ordered]@{}
        foreach ($f in $Fields) {
            $val = $item.$f
            if ($null -eq $val) { $val = "" }
            if ($val -is [System.Array]) { $val = $val -join "; " }
            if ($val -is [psobject] -and $val.GetType().Name -ne "String") {
                $val = $val | ConvertTo-Json -Depth 2 -Compress
            }
            $row[$f] = [string]$val
        }
        [PSCustomObject]$row
    }

    $rows | Export-Csv -Path $path -NoTypeInformation -Encoding UTF8 -Force
    Write-Host "  [OK] $Filename ($($Data.Count) records)" -ForegroundColor Green
}

# ─────────────────────────────────────────────────────────────
# AUTHENTICATION
# ─────────────────────────────────────────────────────────────

function Connect-MSTR {
    Write-Host "`n  Authenticating to $BaseUrl..." -ForegroundColor Cyan

    $body = @{
        username  = $Username
        password  = $Password
        loginMode = $LoginMode
    }

    try {
        $resp = Invoke-WebRequest `
            -Uri         "$BaseUrl/auth/login" `
            -Method      POST `
            -Body        ($body | ConvertTo-Json -Compress) `
            -ContentType "application/json" `
            -TimeoutSec  $TimeoutSec `
            -UseBasicParsing

        $token = $resp.Headers["X-MSTR-AuthToken"]
        if (-not $token) {
            # Some versions return token in body
            $parsed = $resp.Content | ConvertFrom-Json
            $token = $parsed.token
        }
        if (-not $token) {
            throw "No auth token in response. Check credentials and login mode."
        }

        $Script:AuthToken = $token
        Write-Host "  [OK] Authenticated. Token: $($token.Substring(0, [Math]::Min(12,$token.Length)))..." -ForegroundColor Green
        return $true

    } catch {
        Write-Error "  [FAIL] Authentication failed: $($_.Exception.Message)"
        return $false
    }
}

function Disconnect-MSTR {
    try {
        Invoke-MSTRApi -Endpoint "/auth/logout" -Method POST | Out-Null
        Write-Host "`n  Session closed." -ForegroundColor Gray
    } catch { }
}

# ─────────────────────────────────────────────────────────────
# HARVEST FUNCTIONS
# ─────────────────────────────────────────────────────────────

function Get-ServerInfo {
    Write-Host "`n  [01] Harvesting server info..." -ForegroundColor Cyan
    $info  = Invoke-MSTRApi -Endpoint "/status"
    $isvr  = Invoke-MSTRApi -Endpoint "/iServer/info"
    $nodes = Invoke-MSTRApi -Endpoint "/iServer/nodes"

    $rows = [System.Collections.Generic.List[hashtable]]::new()
    $add  = { param($f,$v) $rows.Add(@{ field=$f; value=[string]$v }) }

    if ($info) {
        & $add "web_version"    $info.webVersion
        & $add "api_version"    $info.iServerVersion
        & $add "web_platform"   $info.webPlatform
    }
    if ($isvr) {
        & $add "is_version"     $isvr.iServerVersion
        & $add "is_build"       $isvr.iServerBuildNumber
        & $add "cluster_mode"   $isvr.clusterEnabled
        & $add "is_port"        $isvr.iServerPort
        & $add "web_server"     $isvr.webServerType
        & $add "os"             $isvr.operatingSystem
        & $add "locale"         $isvr.defaultLocale
    }
    if ($nodes) {
        $nodeList = ($nodes | ForEach-Object { "$($_.name):$($_.status)" }) -join "; "
        & $add "cluster_nodes"  $nodeList
        & $add "node_count"     $nodes.Count
    }
    & $add "harvest_time" (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    & $add "harvest_host" $Host

    $data = $rows | ForEach-Object { [PSCustomObject]$_ }
    Export-MSTRCsv -Filename "01_server_info.csv" -Data $data -Fields @("field","value")
    return $isvr
}

function Get-Projects {
    Write-Host "  [02] Harvesting projects..." -ForegroundColor Cyan
    $resp = Invoke-MSTRApi -Endpoint "/projects"
    if (-not $resp) { return @() }

    $projects = @($resp)
    $data = $projects | ForEach-Object {
        [PSCustomObject]@{
            id            = $_.id
            name          = $_.name
            description   = $_.description
            status        = $_.status
            owner_id      = $_.owner.id
            owner_name    = $_.owner.name
            alias         = $_.alias
            node          = $_.nodes -join "; "
        }
    }
    Export-MSTRCsv -Filename "02_projects.csv" -Data $data -Fields @(
        "id","name","description","status","owner_id","owner_name","alias","node"
    )
    return $projects
}

function Get-Users {
    Write-Host "  [03] Harvesting users..." -ForegroundColor Cyan
    $users = Get-MSTRPaged -Endpoint "/users"

    $loginModeMap = @{
        1  = "Standard"
        4  = "Kerberos"
        8  = "Database"
        16 = "LDAP"
        64 = "SAML"
    }

    $data = $users | ForEach-Object {
        $mode = if ($_.loginMode) { $loginModeMap[[int]$_.loginMode] } else { "Standard" }
        [PSCustomObject]@{
            id              = $_.id
            username        = $_.username
            full_name       = $_.fullName
            email           = $_.addresses.value -join "; "
            enabled         = $_.enabled
            login_mode      = $_.loginMode
            login_mode_label = $mode
            standard_auth   = $_.standardAuth
            ldap_dn         = $_.ldapdn
            home_folder_id  = $_.homeFolder.id
        }
    }
    Export-MSTRCsv -Filename "03_users.csv" -Data $data -Fields @(
        "id","username","full_name","email","enabled","login_mode","login_mode_label",
        "standard_auth","ldap_dn","home_folder_id"
    )

    # Risk flags
    $svcAccts = $users | Where-Object { $_.username -match "svc|service|api|bot" }
    if ($svcAccts.Count -gt 0) {
        $Script:RiskFlags.Add("[$($svcAccts.Count) service account(s) detected — verify cloud auth strategy]")
    }
    $saml = $users | Where-Object { [int]$_.loginMode -eq 64 }
    if ($saml.Count -gt 0) {
        $Script:RiskFlags.Add("[$($saml.Count) SAML user(s) — SAML IdP must be configured in cloud]")
    }

    return $users
}

function Get-UserGroups {
    Write-Host "  [04] Harvesting user groups..." -ForegroundColor Cyan
    $groups = Get-MSTRPaged -Endpoint "/usergroups"

    $data = $groups | ForEach-Object {
        [PSCustomObject]@{
            id          = $_.id
            name        = $_.name
            description = $_.description
            enabled     = $_.enabled
            member_count = ($_.members).Count
        }
    }
    Export-MSTRCsv -Filename "04_usergroups.csv" -Data $data -Fields @(
        "id","name","description","enabled","member_count"
    )
    return $groups
}

function Get-GroupMemberships {
    param([object[]]$Groups)
    Write-Host "  [05] Harvesting group memberships..." -ForegroundColor Cyan

    $rows = [System.Collections.Generic.List[PSCustomObject]]::new()
    foreach ($g in $Groups) {
        $detail = Invoke-MSTRApi -Endpoint "/usergroups/$($g.id)"
        if (-not $detail) { continue }
        $members = $detail.members
        if (-not $members) { continue }
        foreach ($m in $members) {
            $rows.Add([PSCustomObject]@{
                group_id    = $g.id
                group_name  = $g.name
                member_id   = $m.id
                member_name = $m.name
                member_type = if ($m.type -eq 34) { "Group" } else { "User" }
            })
        }
    }
    Export-MSTRCsv -Filename "05_group_membership.csv" -Data $rows -Fields @(
        "group_id","group_name","member_id","member_name","member_type"
    )
}

function Get-SecurityRoles {
    Write-Host "  [06] Harvesting security roles..." -ForegroundColor Cyan
    $roles = Invoke-MSTRApi -Endpoint "/securityRoles"
    if (-not $roles) { $roles = @() }

    $data = @($roles) | ForEach-Object {
        [PSCustomObject]@{
            id              = $_.id
            name            = $_.name
            description     = $_.description
            privilege_count = ($_.privileges).Count
            privileges      = ($_.privileges | ForEach-Object { $_.name }) -join "; "
        }
    }
    Export-MSTRCsv -Filename "06_security_roles.csv" -Data $data -Fields @(
        "id","name","description","privilege_count","privileges"
    )
}

function Get-SecurityFilters {
    param([object[]]$Projects)
    Write-Host "  [07] Harvesting security filters..." -ForegroundColor Cyan

    $rows = [System.Collections.Generic.List[PSCustomObject]]::new()
    foreach ($proj in $Projects) {
        $filters = Invoke-MSTRApi -Endpoint "/objects?type=1&limit=500" -ProjectID $proj.id
        if (-not $filters) { continue }
        foreach ($f in @($filters)) {
            if ($f.subtype -ne 8 -and $f.subtype -ne 776 -and $f.name -notmatch "security") { continue }
            $rows.Add([PSCustomObject]@{
                id          = $f.id
                name        = $f.name
                project_id  = $proj.id
                project_name= $proj.name
                owner_id    = $f.owner.id
                owner_name  = $f.owner.name
                date_modified = $f.dateModified
            })
        }
        # Also try dedicated security filter endpoint
        $secFilters = Invoke-MSTRApi -Endpoint "/securityFilters" -ProjectID $proj.id
        if ($secFilters) {
            foreach ($sf in @($secFilters)) {
                $rows.Add([PSCustomObject]@{
                    id          = $sf.id
                    name        = $sf.name
                    project_id  = $proj.id
                    project_name= $proj.name
                    owner_id    = $sf.owner.id
                    owner_name  = $sf.owner.name
                    date_modified = $sf.dateModified
                })
            }
        }
    }

    # Deduplicate by id+project_id
    $deduped = $rows | Sort-Object id, project_id -Unique
    Export-MSTRCsv -Filename "07_security_filters.csv" -Data $deduped -Fields @(
        "id","name","project_id","project_name","owner_id","owner_name","date_modified"
    )

    if ($deduped.Count -gt 0) {
        $Script:RiskFlags.Add("[CRITICAL: $($deduped.Count) security filter(s) detected — must be migrated exactly or data exposure risk]")
    }
}

function Get-Datasources {
    Write-Host "  [08] Harvesting datasources (DB connections)..." -ForegroundColor Cyan
    $sources = Get-MSTRPaged -Endpoint "/datasources"

    $data = $sources | ForEach-Object {
        $db = $_.database
        [PSCustomObject]@{
            id              = $_.id
            name            = $_.name
            description     = $_.description
            db_type         = $db.type
            host            = $db.host
            port            = $db.port
            database_name   = $db.databaseName
            driver          = $db.driver
            dsn             = $db.dsn
            url             = $db.url
            connection_string = $db.connectionString
            db_login_id     = $_.dbLogin.id
            db_login_name   = $_.dbLogin.name
        }
    }
    Export-MSTRCsv -Filename "08_datasources.csv" -Data $data -Fields @(
        "id","name","description","db_type","host","port","database_name",
        "driver","dsn","url","connection_string","db_login_id","db_login_name"
    )

    if ($sources.Count -eq 0) {
        $Script:RiskFlags.Add("[WARNING: No datasources found via REST API — check Command Manager output]")
    }
    return $sources
}

function Get-ProjectObjects {
    param([object[]]$Projects, [int]$ObjType, [string]$Filename, [string]$Label)

    Write-Host "  Harvesting $Label..." -ForegroundColor Cyan
    $rows = [System.Collections.Generic.List[PSCustomObject]]::new()

    foreach ($proj in $Projects) {
        $items = Get-MSTRPaged -Endpoint "/objects?type=$ObjType" -ProjectID $proj.id
        foreach ($item in $items) {
            $rows.Add([PSCustomObject]@{
                id              = $item.id
                name            = $item.name
                description     = $item.description
                path            = $item.ancestors -join "\"
                project_id      = $proj.id
                project_name    = $proj.name
                owner_id        = $item.owner.id
                owner_name      = $item.owner.name
                date_created    = $item.dateCreated
                date_modified   = $item.dateModified
                object_type     = $item.type
                subtype         = $item.subtype
            })
        }
    }
    return $rows
}

function Get-Reports {
    param([object[]]$Projects)
    Write-Host "  [09] Harvesting reports..." -ForegroundColor Cyan
    # Type 3 = Report
    $rows = Get-ProjectObjects -Projects $Projects -ObjType 3 -Filename "09_reports.csv" -Label "Reports"
    Export-MSTRCsv -Filename "09_reports.csv" -Data $rows -Fields @(
        "id","name","description","path","project_id","project_name",
        "owner_id","owner_name","date_created","date_modified","object_type","subtype"
    )
    if ($rows.Count -gt 5000) {
        $Script:RiskFlags.Add("[HIGH: $($rows.Count) reports — large migration scope, plan in batches]")
    }
}

function Get-DocumentsDossiers {
    param([object[]]$Projects)
    Write-Host "  [10] Harvesting documents and dossiers..." -ForegroundColor Cyan

    $rows = [System.Collections.Generic.List[PSCustomObject]]::new()
    foreach ($proj in $Projects) {
        # Type 39 = Document, Type 55 = Dossier (MicroStrategy 2020.3+)
        foreach ($type in @(39, 55)) {
            $label = if ($type -eq 39) { "Document" } else { "Dossier" }
            $items = Get-MSTRPaged -Endpoint "/objects?type=$type" -ProjectID $proj.id
            foreach ($item in $items) {
                $rows.Add([PSCustomObject]@{
                    id              = $item.id
                    name            = $item.name
                    path            = $item.ancestors -join "\"
                    project_id      = $proj.id
                    project_name    = $proj.name
                    owner_id        = $item.owner.id
                    owner_name      = $item.owner.name
                    date_modified   = $item.dateModified
                    object_type     = $type
                    object_type_name = $label
                })
            }
        }
    }
    Export-MSTRCsv -Filename "10_documents_dossiers.csv" -Data $rows -Fields @(
        "id","name","path","project_id","project_name",
        "owner_id","owner_name","date_modified","object_type","object_type_name"
    )
}

function Get-Metrics {
    param([object[]]$Projects)
    Write-Host "  [11] Harvesting metrics..." -ForegroundColor Cyan
    $rows = Get-ProjectObjects -Projects $Projects -ObjType 4 -Filename "11_metrics.csv" -Label "Metrics"
    Export-MSTRCsv -Filename "11_metrics.csv" -Data $rows -Fields @(
        "id","name","description","path","project_id","project_name","date_modified"
    )
}

function Get-Attributes {
    param([object[]]$Projects)
    Write-Host "  [12] Harvesting attributes..." -ForegroundColor Cyan
    $rows = Get-ProjectObjects -Projects $Projects -ObjType 12 -Filename "12_attributes.csv" -Label "Attributes"
    Export-MSTRCsv -Filename "12_attributes.csv" -Data $rows -Fields @(
        "id","name","project_id","project_name","date_modified"
    )
}

function Get-Facts {
    param([object[]]$Projects)
    Write-Host "  [13] Harvesting facts..." -ForegroundColor Cyan
    $rows = Get-ProjectObjects -Projects $Projects -ObjType 6 -Filename "13_facts.csv" -Label "Facts"
    Export-MSTRCsv -Filename "13_facts.csv" -Data $rows -Fields @(
        "id","name","project_id","project_name","date_modified"
    )
}

function Get-Filters {
    param([object[]]$Projects)
    Write-Host "  [14] Harvesting filters..." -ForegroundColor Cyan
    $rows = Get-ProjectObjects -Projects $Projects -ObjType 1 -Filename "14_filters.csv" -Label "Filters"
    Export-MSTRCsv -Filename "14_filters.csv" -Data $rows -Fields @(
        "id","name","project_id","project_name","date_modified"
    )
}

function Get-Prompts {
    param([object[]]$Projects)
    Write-Host "  [15] Harvesting prompts..." -ForegroundColor Cyan
    $rows = Get-ProjectObjects -Projects $Projects -ObjType 8 -Filename "15_prompts.csv" -Label "Prompts"
    Export-MSTRCsv -Filename "15_prompts.csv" -Data $rows -Fields @(
        "id","name","project_id","project_name","date_modified"
    )
}

function Get-Schedules {
    Write-Host "  [16] Harvesting schedules..." -ForegroundColor Cyan
    $schedules = Get-MSTRPaged -Endpoint "/schedules"

    $data = $schedules | ForEach-Object {
        [PSCustomObject]@{
            id              = $_.id
            name            = $_.name
            description     = $_.description
            enabled         = $_.enabled
            schedule_type   = $_.scheduleType
            start_date      = $_.startDate
            stop_date       = $_.stopDate
            next_run        = $_.nextDeliveryTime
            frequency       = $_.recurrencePattern.recurrenceType
            time_zone       = $_.timeZone.id
        }
    }
    Export-MSTRCsv -Filename "16_schedules.csv" -Data $data -Fields @(
        "id","name","description","enabled","schedule_type","start_date","stop_date",
        "next_run","frequency","time_zone"
    )

    $enabledSched = $schedules | Where-Object { $_.enabled -eq $true }
    if ($enabledSched.Count -gt 0) {
        $Script:RiskFlags.Add("[HIGH: $($enabledSched.Count) enabled schedule(s) — verify time zones after migration]")
    }
}

function Get-Subscriptions {
    param([object[]]$Projects)
    Write-Host "  [17] Harvesting subscriptions..." -ForegroundColor Cyan
    $rows = [System.Collections.Generic.List[PSCustomObject]]::new()

    foreach ($proj in $Projects) {
        $subs = Get-MSTRPaged -Endpoint "/subscriptions" -ProjectID $proj.id
        foreach ($s in $subs) {
            $rows.Add([PSCustomObject]@{
                id              = $s.id
                name            = $s.name
                project_id      = $proj.id
                project_name    = $proj.name
                owner_id        = $s.owner.id
                owner_name      = $s.owner.name
                delivery_type   = $s.delivery.mode
                enabled         = $s.enabled
                schedule_id     = $s.schedules.id -join "; "
                content_count   = ($s.contents).Count
            })
        }
    }
    Export-MSTRCsv -Filename "17_subscriptions.csv" -Data $rows -Fields @(
        "id","name","project_id","project_name","owner_id","owner_name",
        "delivery_type","enabled","schedule_id","content_count"
    )
}

function Get-Caches {
    param([object[]]$Projects)
    Write-Host "  [18] Harvesting caches..." -ForegroundColor Cyan
    $rows = [System.Collections.Generic.List[PSCustomObject]]::new()

    foreach ($proj in $Projects) {
        $caches = Invoke-MSTRApi -Endpoint "/caches/reportCaches?projectId=$($proj.id)&limit=200" -ProjectID $proj.id
        if (-not $caches) {
            $rows.Add([PSCustomObject]@{
                project_id   = $proj.id
                project_name = $proj.name
                cache_type   = "report"
                cache_count  = "N/A (insufficient privileges or version)"
                hit_count    = ""
                size_bytes   = ""
            })
        } else {
            $cacheArr = if ($caches -is [System.Array]) { $caches } else { @($caches) }
            $rows.Add([PSCustomObject]@{
                project_id   = $proj.id
                project_name = $proj.name
                cache_type   = "report"
                cache_count  = $cacheArr.Count
                hit_count    = ($cacheArr | Measure-Object hitCount -Sum).Sum
                size_bytes   = ($cacheArr | Measure-Object size -Sum).Sum
            })
        }
    }
    Export-MSTRCsv -Filename "18_caches.csv" -Data $rows -Fields @(
        "project_id","project_name","cache_type","cache_count","hit_count","size_bytes"
    )
}

function Get-SecurityConfig {
    Write-Host "  [19] Harvesting security / auth config..." -ForegroundColor Cyan
    $rows = [System.Collections.Generic.List[PSCustomObject]]::new()

    $ldap = Invoke-MSTRApi -Endpoint "/ldap"
    if ($ldap) {
        $rows.Add([PSCustomObject]@{ setting_category="LDAP"; setting_name="enabled";   setting_value=$ldap.enabled })
        $rows.Add([PSCustomObject]@{ setting_category="LDAP"; setting_name="host";      setting_value=$ldap.host })
        $rows.Add([PSCustomObject]@{ setting_category="LDAP"; setting_name="port";      setting_value=$ldap.port })
        $rows.Add([PSCustomObject]@{ setting_category="LDAP"; setting_name="use_ssl";   setting_value=$ldap.useSsl })
        $rows.Add([PSCustomObject]@{ setting_category="LDAP"; setting_name="base_dn";   setting_value=$ldap.baseDn })
        $rows.Add([PSCustomObject]@{ setting_category="LDAP"; setting_name="bind_dn";   setting_value=$ldap.bindDn })
        $Script:RiskFlags.Add("[LDAP configured (host: $($ldap.host)) — must replicate LDAP settings on cloud IS]")
    }

    $saml = Invoke-MSTRApi -Endpoint "/saml"
    if ($saml) {
        $rows.Add([PSCustomObject]@{ setting_category="SAML"; setting_name="enabled";    setting_value=$saml.enabled })
        $rows.Add([PSCustomObject]@{ setting_category="SAML"; setting_name="idp_url";    setting_value=$saml.identityProviderUrl })
        $rows.Add([PSCustomObject]@{ setting_category="SAML"; setting_name="entity_id";  setting_value=$saml.entityId })
        $Script:RiskFlags.Add("[SAML configured — IdP metadata must be re-registered on cloud IS]")
    }

    $trust = Invoke-MSTRApi -Endpoint "/server/settings"
    if ($trust) {
        $rows.Add([PSCustomObject]@{ setting_category="Server"; setting_name="trusted_auth"; setting_value=$trust.trustedAuthEnabled })
        $rows.Add([PSCustomObject]@{ setting_category="Server"; setting_name="anonymous_auth"; setting_value=$trust.anonymousAuthEnabled })
    }

    if ($rows.Count -eq 0) {
        $rows.Add([PSCustomObject]@{ setting_category="Auth"; setting_name="status"; setting_value="No auth config accessible via REST API" })
    }

    Export-MSTRCsv -Filename "19_security_config.csv" -Data $rows -Fields @(
        "setting_category","setting_name","setting_value"
    )
}

function Get-EmailConfig {
    Write-Host "  [20] Harvesting email (SMTP) config..." -ForegroundColor Cyan
    $email = Invoke-MSTRApi -Endpoint "/emailSettings"
    $rows = [System.Collections.Generic.List[PSCustomObject]]::new()

    if ($email) {
        $props = @("smtpHost","smtpPort","smtpSsl","smtpUsername","fromAddress",
                   "fromName","emailEncoding","emailHeader","emailFooter")
        foreach ($p in $props) {
            $rows.Add([PSCustomObject]@{
                setting_name = $p
                value        = if ($email.$p -ne $null) { [string]$email.$p } else { "" }
            })
        }
        $Script:RiskFlags.Add("[SMTP host: $($email.smtpHost):$($email.smtpPort) — verify cloud IS can reach SMTP relay]")
    } else {
        $rows.Add([PSCustomObject]@{ setting_name="status"; value="Not accessible via REST API — check MSTR Web Admin" })
    }

    Export-MSTRCsv -Filename "20_email_config.csv" -Data $rows -Fields @("setting_name","value")
}

function Get-Licenses {
    Write-Host "  [21] Harvesting license info..." -ForegroundColor Cyan
    $lic = Invoke-MSTRApi -Endpoint "/license"
    $rows = [System.Collections.Generic.List[PSCustomObject]]::new()

    if ($lic) {
        $licArr = if ($lic -is [System.Array]) { $lic } else { @($lic) }
        foreach ($l in $licArr) {
            $rows.Add([PSCustomObject]@{
                license_key  = $l.key
                product      = $l.product
                license_type = $l.licenseType
                named_users  = $l.namedUsers
                expiry_date  = $l.expiryDate
                is_active    = $l.active
            })
        }
    } else {
        $rows.Add([PSCustomObject]@{
            license_key  = "N/A"
            product      = "Could not retrieve via REST API"
            license_type = ""
            named_users  = ""
            expiry_date  = ""
            is_active    = ""
        })
    }

    Export-MSTRCsv -Filename "21_licenses.csv" -Data $rows -Fields @(
        "license_key","product","license_type","named_users","expiry_date","is_active"
    )
}

# ─────────────────────────────────────────────────────────────
# SUMMARY REPORT GENERATOR
# ─────────────────────────────────────────────────────────────

function Write-SummaryReport {
    param([object[]]$Projects, [object[]]$Users, [object[]]$Datasources)

    $path     = Join-Path $OutputDir "SUMMARY_REPORT.txt"
    $elapsed  = [Math]::Round(((Get-Date) - $Script:StartTime).TotalSeconds)
    $sep      = "=" * 72
    $thin     = "-" * 72
    $now      = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add($sep)
    $lines.Add("  MICROSTRATEGY INSTANCE DISCOVERY SUMMARY REPORT")
    $lines.Add("  Generated by Invoke-MSTRHarvester.ps1 (Windows/Citrix Edition)")
    $lines.Add($sep)
    $lines.Add("  Source Host   : $Host")
    $lines.Add("  Report Time   : $now")
    $lines.Add("  Harvest Time  : ${elapsed}s")
    $lines.Add("  Output Dir    : $(Resolve-Path $OutputDir)")
    $lines.Add($sep)

    $lines.Add("")
    $lines.Add("  INSTANCE OVERVIEW")
    $lines.Add($thin)
    $lines.Add("  Total Projects  : $($Projects.Count)")
    $lines.Add("  Total Users     : $($Users.Count)")
    $lines.Add("  DB Connections  : $($Datasources.Count)")

    $csvFiles = Get-ChildItem $OutputDir -Filter "*.csv" | Where-Object { $_.Name -ne "SUMMARY_REPORT.txt" }
    foreach ($f in $csvFiles | Sort-Object Name) {
        $count = (Import-Csv $f.FullName -ErrorAction SilentlyContinue | Measure-Object).Count
        $lines.Add("  $($f.Name.PadRight(40)) $count records")
    }

    if ($Script:RiskFlags.Count -gt 0) {
        $lines.Add("")
        $lines.Add("  RISK FLAGS (Review Before Migration)")
        $lines.Add($thin)
        foreach ($flag in $Script:RiskFlags) {
            $lines.Add("  $flag")
        }
    } else {
        $lines.Add("")
        $lines.Add("  [OK] No major risk flags detected.")
    }

    $lines.Add("")
    $lines.Add("  AI ANALYSIS PROMPT")
    $lines.Add($thin)
    $lines.Add("  Feed this file to Claude or ChatGPT with:")
    $lines.Add("  'Review this MicroStrategy discovery report. List top 10 migration risks,")
    $lines.Add("   recommended migration order for projects (easiest to hardest), and any")
    $lines.Add("   deprecated features needing redesign. Output as structured tables.'")
    $lines.Add("")
    $lines.Add($sep)
    $lines.Add("  END OF REPORT")
    $lines.Add($sep)

    $lines | Out-File -FilePath $path -Encoding UTF8
    Write-Host "`n  [OK] SUMMARY_REPORT.txt written" -ForegroundColor Green
}

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "=" * 72 -ForegroundColor Cyan
Write-Host "  Invoke-MSTRHarvester.ps1 — Windows/Citrix Edition v2.1" -ForegroundColor Cyan
Write-Host "  Target : $Host" -ForegroundColor Cyan
Write-Host "  Output : $OutputDir" -ForegroundColor Cyan
Write-Host "=" * 72 -ForegroundColor Cyan

# Create output directory
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# Authenticate
if (-not (Connect-MSTR)) {
    Write-Error "Cannot proceed without authentication. Exiting."
    exit 1
}

try {
    # Server info
    $serverInfo = Get-ServerInfo

    # Projects
    $allProjects = Get-Projects
    if (-not $allProjects -or $allProjects.Count -eq 0) {
        Write-Warning "No projects found. Check user has project access."
        exit 1
    }

    # Select projects to harvest
    if ($ProjectId) {
        $targetProjects = @($allProjects | Where-Object { $_.id -eq $ProjectId })
        if ($targetProjects.Count -eq 0) {
            Write-Error "Project ID '$ProjectId' not found."
            exit 1
        }
    } elseif ($AllProjects) {
        $targetProjects = $allProjects
        Write-Host "  Harvesting ALL $($allProjects.Count) projects" -ForegroundColor Yellow
    } else {
        $targetProjects = $allProjects | Select-Object -First $MaxProjects
        Write-Host "  Harvesting first $($targetProjects.Count) of $($allProjects.Count) projects (use -AllProjects for all)" -ForegroundColor Yellow
    }

    # Users and groups
    $users  = Get-Users
    $groups = Get-UserGroups
    Get-GroupMemberships -Groups $groups

    # Security
    Get-SecurityRoles
    Get-SecurityFilters -Projects $targetProjects

    # Datasources
    $datasources = Get-Datasources

    # Project content
    Get-Reports        -Projects $targetProjects
    Get-DocumentsDossiers -Projects $targetProjects
    Get-Metrics        -Projects $targetProjects
    Get-Attributes     -Projects $targetProjects
    Get-Facts          -Projects $targetProjects
    Get-Filters        -Projects $targetProjects
    Get-Prompts        -Projects $targetProjects

    # Server config
    Get-Schedules
    Get-Subscriptions  -Projects $targetProjects
    Get-Caches         -Projects $targetProjects
    Get-SecurityConfig
    Get-EmailConfig
    Get-Licenses

    # Summary
    Write-SummaryReport -Projects $targetProjects -Users $users -Datasources $datasources

} finally {
    Disconnect-MSTR
}

$elapsed = [Math]::Round(((Get-Date) - $Script:StartTime).TotalSeconds)

Write-Host ""
Write-Host "=" * 72 -ForegroundColor Green
Write-Host "  HARVEST COMPLETE in ${elapsed}s" -ForegroundColor Green
Write-Host "  Output directory: $(Resolve-Path $OutputDir)" -ForegroundColor Green
Write-Host "  Files written   : $(( Get-ChildItem $OutputDir | Measure-Object ).Count)" -ForegroundColor Green
Write-Host ""
Write-Host "  NEXT STEPS:" -ForegroundColor Cyan
Write-Host "  1. Feed SUMMARY_REPORT.txt to AI for risk analysis" -ForegroundColor Cyan
Write-Host "  2. Run Invoke-MSTRConnectivityTester.ps1 for network tests" -ForegroundColor Cyan
Write-Host "  3. Review 08_datasources.csv — map each connection to cloud equivalent" -ForegroundColor Cyan
Write-Host "=" * 72 -ForegroundColor Green
