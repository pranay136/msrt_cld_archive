#!/usr/bin/env python3
"""
================================================================================
  MicroStrategy Connectivity Tester v2.0
  ========================================
  Reads an odbc.ini file, parses all DSN entries, and tests:
    1. Ping (ICMP) reachability to each DB host
    2. TCP port reachability (curl-style) to each DB host:port
    3. CMC port reachability from this machine to the cloud IS

  Produces:
    db_connections_inventory.csv   — All DSNs with host, port, DB type, category
    connectivity_results.csv       — Ping + TCP test results per connection
    CONNECTIVITY_REPORT.txt        — Human-readable pass/fail summary

  USAGE:
    python mstr_connectivity_tester.py \
        --odbc-file /etc/odbc.ini \
        --cmc-host  your-cloud-mstr.microstrategy.com \
        --cmc-port  34952 \
        --output-dir ./connectivity_results

    # Also test a second ODBC file (e.g., user-level):
    python mstr_connectivity_tester.py \
        --odbc-file /etc/odbc.ini \
        --odbc-file-user ~/.odbc.ini \
        --cmc-host  cloud.company.com \
        --cmc-port  34952

  REQUIREMENTS:
    Python 3.8+ (stdlib only — no pip installs needed)
    curl must be available in PATH (standard on Linux/macOS; Windows Subsystem for Linux or Git Bash)
    ping must be available in PATH (standard everywhere)

  NOTES:
    - On Linux: requires 'ping' with -c flag (POSIX ping)
    - On Windows: requires 'ping' with -n flag (auto-detected)
    - curl timeout is 5 seconds per connection
    - ping sends 2 packets per host

  AUTHOR: MicroStrategy Admin Automation Toolkit
  VERSION: 2.0
================================================================================
"""

import argparse
import configparser
import csv
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────
# DB TYPE DETECTION — maps driver/DSN keywords → DB category
# ─────────────────────────────────────────────────────────────

DB_TYPE_RULES = [
    # (keyword_list,          db_type_label,        default_port)
    (["sqlserver", "sql server", "mssql", "ms sql"], "Microsoft SQL Server", 1433),
    (["oracle"],                                      "Oracle Database",      1521),
    (["mysql"],                                       "MySQL",                3306),
    (["mariadb"],                                     "MariaDB",              3306),
    (["postgresql", "postgre", "psql", "pg"],         "PostgreSQL",           5432),
    (["teradata"],                                    "Teradata",             1025),
    (["snowflake"],                                   "Snowflake",            443),
    (["redshift"],                                    "Amazon Redshift",      5439),
    (["bigquery"],                                    "Google BigQuery",       443),
    (["hive"],                                        "Apache Hive",         10000),
    (["spark"],                                       "Apache Spark",        10001),
    (["db2", "ibm db"],                               "IBM DB2",             50000),
    (["sybase", "ase"],                               "Sybase ASE",           5000),
    (["impala"],                                      "Apache Impala",       21050),
    (["presto", "trino"],                             "Presto/Trino",         8080),
    (["vertica"],                                     "Vertica",              5433),
    (["netezza"],                                     "IBM Netezza",          5480),
    (["greenplum"],                                   "Greenplum",            5432),
    (["athena"],                                      "Amazon Athena",         443),
    (["azure synapse", "synapse"],                    "Azure Synapse",        1433),
    (["databricks"],                                  "Databricks",            443),
    (["sap hana", "hana"],                            "SAP HANA",            30015),
    (["informix"],                                    "IBM Informix",          9088),
    (["progress", "openedge"],                        "Progress OpenEdge",    9999),
]

# Category groupings for the inventory CSV
DB_CATEGORIES = {
    "Microsoft SQL Server":  "Relational — Microsoft",
    "Oracle Database":       "Relational — Oracle",
    "MySQL":                 "Relational — Open Source",
    "MariaDB":               "Relational — Open Source",
    "PostgreSQL":            "Relational — Open Source",
    "IBM DB2":               "Relational — IBM",
    "Sybase ASE":            "Relational — SAP/Sybase",
    "SAP HANA":              "Relational — SAP/Sybase",
    "Teradata":              "Enterprise DW",
    "Vertica":               "Enterprise DW",
    "Netezza":               "Enterprise DW",
    "Greenplum":             "Enterprise DW",
    "IBM Informix":          "Enterprise DW",
    "Snowflake":             "Cloud Data Warehouse",
    "Amazon Redshift":       "Cloud Data Warehouse",
    "Google BigQuery":       "Cloud Data Warehouse",
    "Azure Synapse":         "Cloud Data Warehouse",
    "Databricks":            "Cloud Data Warehouse",
    "Amazon Athena":         "Cloud Data Warehouse",
    "Apache Hive":           "Big Data / Hadoop",
    "Apache Spark":          "Big Data / Hadoop",
    "Apache Impala":         "Big Data / Hadoop",
    "Presto/Trino":          "Big Data / Hadoop",
    "Progress OpenEdge":     "Specialty",
}


def detect_db_type(driver_str: str, dsn_name: str) -> Tuple[str, int]:
    """
    Detect DB type and default port from the Driver string (or DSN name as fallback).
    Returns (db_type_label, default_port).
    """
    search_str = (driver_str + " " + dsn_name).lower()
    for keywords, label, default_port in DB_TYPE_RULES:
        if any(kw in search_str for kw in keywords):
            return label, default_port
    return "Unknown / Other", 0


def get_category(db_type: str) -> str:
    return DB_CATEGORIES.get(db_type, "Unknown / Other")


# ─────────────────────────────────────────────────────────────
# ODBC.INI PARSER
# ─────────────────────────────────────────────────────────────

def parse_odbc_ini(filepath: str) -> List[Dict]:
    """
    Parse an odbc.ini file and return a list of DSN connection dicts.
    Handles both system-level (/etc/odbc.ini) and user-level (~/.odbc.ini) formats.
    Also handles Windows-style odbc.ini / ODBC.INI.
    """
    if not os.path.exists(filepath):
        print(f"  [WARN] odbc.ini not found: {filepath}")
        return []

    config = configparser.RawConfigParser()
    config.optionxform = str  # Preserve key case

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        config.read_string(content)
    except Exception as e:
        print(f"  [ERROR] Failed to parse {filepath}: {e}")
        return []

    # Skip the [ODBC Data Sources] section which just lists names
    skip_sections = {"odbc data sources", "odbc", "odbc driver manager"}

    connections = []
    for section in config.sections():
        if section.lower().strip() in skip_sections:
            continue

        row = dict(config[section])

        # Extract key fields with case-insensitive matching
        def get_field(*keys):
            for k in keys:
                for actual_key in row:
                    if actual_key.lower() == k.lower():
                        return row[actual_key].strip()
            return ""

        driver    = get_field("Driver", "DRIVER")
        server    = get_field("Server", "SERVER", "Host", "HOST",
                              "Servername", "SERVERNAME", "Hostname", "HOSTNAME",
                              "DSN", "AccountName", "UID_Server")
        port_str  = get_field("Port", "PORT", "TDMSTPortNumber")
        database  = get_field("Database", "DATABASE", "DBName", "DBNAME",
                              "DefaultDB", "Catalog", "Schema", "db")
        uid       = get_field("UID", "uid", "User", "Username", "UserName")
        pwd_set   = bool(get_field("PWD", "pwd", "Password", "password"))
        charset   = get_field("Charset", "charset", "Encoding")
        ssl_mode  = get_field("SSLMode", "ssl_mode", "Encrypt", "SSL")
        description = get_field("Description", "DESCRIPTION", "Comment")

        db_type, default_port = detect_db_type(driver, section)

        # Resolve port
        try:
            port = int(port_str) if port_str else default_port
        except ValueError:
            port = default_port

        category = get_category(db_type)

        # Skip entries with no server — likely template or placeholder
        if not server and not database:
            continue

        connections.append({
            "dsn_name":    section,
            "driver":      driver,
            "server":      server,
            "port":        port,
            "database":    database,
            "uid":         uid,
            "has_password": "Yes" if pwd_set else "No",
            "db_type":     db_type,
            "category":    category,
            "charset":     charset,
            "ssl_mode":    ssl_mode,
            "description": description,
            "odbc_file":   filepath,
            # These will be filled by connectivity tests
            "ping_status": "",
            "ping_latency_ms": "",
            "tcp_port_status": "",
            "tcp_port_latency_ms": "",
            "dns_resolves": "",
            "resolved_ip": "",
        })

    return connections


# ─────────────────────────────────────────────────────────────
# CONNECTIVITY TESTS
# ─────────────────────────────────────────────────────────────

IS_WINDOWS = platform.system().lower() == "windows"


def resolve_dns(hostname: str) -> Tuple[str, str]:
    """
    Attempt DNS resolution for hostname.
    Returns (status, resolved_ip).
    """
    if not hostname:
        return "NO_HOSTNAME", ""
    try:
        ip = socket.gethostbyname(hostname)
        return "RESOLVED", ip
    except socket.gaierror as e:
        return f"DNS_FAIL", ""
    except Exception:
        return "DNS_ERROR", ""


def test_ping(hostname: str, count: int = 2, timeout_sec: int = 5) -> Tuple[str, str]:
    """
    Run ping command against hostname.
    Returns (status, avg_latency_ms_string).
    Status: REACHABLE | UNREACHABLE | TIMEOUT | DNS_FAIL | ERROR
    """
    if not hostname:
        return "NO_HOSTNAME", ""

    try:
        if IS_WINDOWS:
            cmd = ["ping", "-n", str(count), "-w", str(timeout_sec * 1000), hostname]
        else:
            cmd = ["ping", "-c", str(count), "-W", str(timeout_sec), hostname]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec * count + 5
        )

        output = result.stdout + result.stderr

        if result.returncode == 0:
            # Try to extract latency
            latency = ""
            for line in output.splitlines():
                line_low = line.lower()
                # Linux: "rtt min/avg/max/mdev = 1.2/1.5/2.0/0.3 ms"
                if "rtt" in line_low and "avg" in line_low:
                    parts = line.split("=")
                    if len(parts) > 1:
                        stats = parts[1].strip().split("/")
                        if len(stats) >= 2:
                            latency = stats[1].strip() + " ms"
                # Windows: "Average = 15ms"
                elif "average" in line_low and "=" in line:
                    latency = line.split("=")[-1].strip()
            return "REACHABLE", latency

        elif "ttl expired" in output.lower() or "request timed out" in output.lower():
            return "TIMEOUT", ""
        elif "could not find host" in output.lower() or "unknown host" in output.lower():
            return "DNS_FAIL", ""
        else:
            return "UNREACHABLE", ""

    except subprocess.TimeoutExpired:
        return "TIMEOUT", ""
    except FileNotFoundError:
        return "PING_NOT_FOUND", ""
    except Exception as e:
        return f"ERROR", ""


def test_tcp_port(hostname: str, port: int, timeout_sec: int = 5) -> Tuple[str, str]:
    """
    Test TCP connectivity to hostname:port using Python socket (most portable).
    Also tries curl as a secondary method for logging.
    Returns (status, latency_ms_string).
    Status: OPEN | CLOSED | TIMEOUT | DNS_FAIL | NO_PORT | ERROR
    """
    if not hostname:
        return "NO_HOSTNAME", ""
    if not port or port == 0:
        return "NO_PORT", ""

    start = time.time()
    try:
        sock = socket.create_connection((hostname, port), timeout=timeout_sec)
        sock.close()
        latency_ms = f"{(time.time() - start) * 1000:.1f} ms"
        return "OPEN", latency_ms
    except socket.timeout:
        return "TIMEOUT", ""
    except ConnectionRefusedError:
        return "CLOSED", ""
    except socket.gaierror:
        return "DNS_FAIL", ""
    except OSError as e:
        error_msg = str(e).lower()
        if "network unreachable" in error_msg or "no route" in error_msg:
            return "NO_ROUTE", ""
        return "ERROR", ""
    except Exception:
        return "ERROR", ""


def test_curl_port(hostname: str, port: int, timeout_sec: int = 5) -> Tuple[str, str]:
    """
    Test TCP port using curl (for systems where it's preferred or for HTTPS endpoints).
    Returns (status, latency_or_http_code).
    """
    if not hostname or not port:
        return "SKIPPED", ""

    # For HTTPS ports (443), do an actual HTTP request
    if port == 443:
        url = f"https://{hostname}"
        cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
               "--connect-timeout", str(timeout_sec), "--max-time", str(timeout_sec),
               "-k", url]
    else:
        # Generic TCP port test
        url = f"telnet://{hostname}:{port}"
        cmd = ["curl", "-s", "-v", "--connect-timeout", str(timeout_sec),
               "--max-time", str(timeout_sec), url, "-o", "/dev/null"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec + 3)
        if port == 443:
            code = result.stdout.strip()
            if code.isdigit() and int(code) > 0:
                return f"HTTP_{code}", ""
            return "UNREACHABLE", ""
        else:
            combined = (result.stdout + result.stderr).lower()
            if result.returncode == 0:
                return "OPEN", ""
            elif "connection refused" in combined:
                return "CLOSED", ""
            elif "could not resolve" in combined or "name or service" in combined:
                return "DNS_FAIL", ""
            elif "timed out" in combined or "operation timeout" in combined:
                return "TIMEOUT", ""
            else:
                return "UNREACHABLE", ""
    except subprocess.TimeoutExpired:
        return "TIMEOUT", ""
    except FileNotFoundError:
        return "CURL_NOT_FOUND", ""
    except Exception:
        return "ERROR", ""


# ─────────────────────────────────────────────────────────────
# REPORT GENERATOR
# ─────────────────────────────────────────────────────────────

def generate_connectivity_report(
    connections: List[Dict],
    cmc_results: Dict,
    output_dir: str,
    report_time: str,
    cmc_host: str,
    cmc_port: int,
) -> str:
    """Generate CONNECTIVITY_REPORT.txt."""

    lines = []
    sep = "=" * 80
    thin = "-" * 80

    def section(title):
        lines.append("")
        lines.append(sep)
        lines.append(f"  {title}")
        lines.append(sep)

    def row(label, value, w=40):
        lines.append(f"  {label:<{w}} {value}")

    lines.append(sep)
    lines.append("  MICROSTRATEGY CONNECTIVITY TEST REPORT")
    lines.append("  Generated by MicroStrategy Connectivity Tester v2.0")
    lines.append(sep)
    lines.append(f"  Report Time   : {report_time}")
    lines.append(f"  CMC Host      : {cmc_host}")
    lines.append(f"  CMC Port      : {cmc_port}")
    lines.append(f"  Total DSNs    : {len(connections)}")
    lines.append(sep)

    # CMC connection results
    section("1. CMC (CLOUD IS) CONNECTIVITY")
    row("CMC Host", cmc_host)
    row("CMC Port", str(cmc_port))
    row("DNS Resolution", cmc_results.get("dns_status", "N/A"))
    row("Resolved IP", cmc_results.get("resolved_ip", "N/A"))
    row("Ping Status", cmc_results.get("ping_status", "N/A"))
    row("Ping Latency", cmc_results.get("ping_latency", "N/A"))
    row("TCP Port Status", cmc_results.get("tcp_status", "N/A"))
    row("TCP Latency", cmc_results.get("tcp_latency", "N/A"))

    cmc_ok = cmc_results.get("tcp_status") == "OPEN"
    lines.append("")
    if cmc_ok:
        lines.append("  [PASS] CMC IS port is reachable — cloud IS accessible from this machine.")
    else:
        lines.append("  [FAIL] CMC IS port is NOT reachable — check firewall / VPN / security group.")

    # Summary by DB category
    section("2. DB CONNECTION SUMMARY BY CATEGORY")
    category_stats = {}
    for conn in connections:
        cat = conn.get("category", "Unknown")
        tcp = conn.get("tcp_port_status", "")
        ping = conn.get("ping_status", "")
        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "tcp_open": 0, "tcp_fail": 0, "ping_ok": 0, "ping_fail": 0}
        category_stats[cat]["total"] += 1
        if tcp == "OPEN":
            category_stats[cat]["tcp_open"] += 1
        elif tcp in ("CLOSED", "TIMEOUT", "DNS_FAIL", "NO_ROUTE", "ERROR"):
            category_stats[cat]["tcp_fail"] += 1
        if ping == "REACHABLE":
            category_stats[cat]["ping_ok"] += 1
        elif ping in ("UNREACHABLE", "TIMEOUT", "DNS_FAIL"):
            category_stats[cat]["ping_fail"] += 1

    lines.append(f"  {'Category':<35} {'Total':>6} {'TCP OK':>7} {'TCP FAIL':>9} {'Ping OK':>8} {'Ping FAIL':>10}")
    lines.append(f"  {thin}")
    for cat, stats in sorted(category_stats.items()):
        lines.append(
            f"  {cat:<35} {stats['total']:>6} {stats['tcp_open']:>7} "
            f"{stats['tcp_fail']:>9} {stats['ping_ok']:>8} {stats['ping_fail']:>10}"
        )

    # Overall counts
    total = len(connections)
    tcp_open = sum(1 for c in connections if c.get("tcp_port_status") == "OPEN")
    tcp_fail = sum(1 for c in connections if c.get("tcp_port_status") in ("CLOSED", "TIMEOUT", "DNS_FAIL", "NO_ROUTE", "ERROR"))
    ping_ok = sum(1 for c in connections if c.get("ping_status") == "REACHABLE")
    dns_ok = sum(1 for c in connections if c.get("dns_resolves") == "RESOLVED")

    section("3. OVERALL CONNECTIVITY SUMMARY")
    row("Total DSN Connections", str(total))
    row("DNS Resolvable", f"{dns_ok}/{total} ({100*dns_ok//total if total else 0}%)")
    row("Ping Reachable", f"{ping_ok}/{total} ({100*ping_ok//total if total else 0}%)")
    row("TCP Port Open", f"{tcp_open}/{total} ({100*tcp_open//total if total else 0}%)")
    row("TCP Port Failures", f"{tcp_fail}/{total}")

    if tcp_fail == 0 and dns_ok == total:
        lines.append("")
        lines.append("  [PASS] All DB connections are reachable. Network connectivity is healthy.")
    else:
        lines.append("")
        lines.append(f"  [WARN] {tcp_fail} connection(s) failed TCP test — review FAILED section below.")

    # Failed connections detail
    failed = [c for c in connections
              if c.get("tcp_port_status") not in ("OPEN", "NO_PORT", "SKIPPED", "")
              or c.get("dns_resolves") == "DNS_FAIL"]
    if failed:
        section("4. FAILED / UNREACHABLE CONNECTIONS — ACTION REQUIRED")
        lines.append(f"  {'DSN Name':<35} {'Server':<35} {'Port':>5} {'DNS':>12} {'TCP Status'}")
        lines.append(f"  {thin}")
        for c in failed:
            lines.append(
                f"  {c['dsn_name'][:34]:<35} "
                f"{c['server'][:34]:<35} "
                f"{c['port']:>5} "
                f"{c.get('dns_resolves',''):>12} "
                f"{c.get('tcp_port_status','')}"
            )
        lines.append("")
        lines.append("  RECOMMENDED ACTIONS:")
        dns_fails = [c for c in failed if c.get("dns_resolves") == "DNS_FAIL"]
        tcp_fails = [c for c in failed if c.get("tcp_port_status") in ("CLOSED", "TIMEOUT", "NO_ROUTE")]
        if dns_fails:
            lines.append(f"  - {len(dns_fails)} DSN(s) have DNS resolution failures → "
                         "add DNS entries or update /etc/hosts on the cloud IS.")
        if tcp_fails:
            lines.append(f"  - {len(tcp_fails)} DSN(s) have TCP port failures → "
                         "open firewall rules / security groups for listed host:port combinations.")

    # Passing connections
    passing = [c for c in connections if c.get("tcp_port_status") == "OPEN"]
    if passing:
        section("5. PASSING CONNECTIONS — FULLY REACHABLE")
        lines.append(f"  {'DSN Name':<35} {'Server':<35} {'Port':>5} {'Latency'}")
        lines.append(f"  {thin}")
        for c in passing:
            lines.append(
                f"  {c['dsn_name'][:34]:<35} "
                f"{c['server'][:34]:<35} "
                f"{c['port']:>5} "
                f"{c.get('tcp_port_latency_ms','')}"
            )

    # No-port entries
    no_port = [c for c in connections if c.get("tcp_port_status") in ("NO_PORT", "")]
    if no_port:
        section("6. CONNECTIONS WITH NO PORT DEFINED — TCP TEST SKIPPED")
        for c in no_port:
            lines.append(f"  {c['dsn_name']} — Server: {c['server']} — No port defined in odbc.ini")

    section("7. NEXT STEPS")
    lines.append("  1. Fix all entries in Section 4 (FAILED) before cloud migration.")
    lines.append("  2. Verify CMC port (Section 1) is open — this is mandatory for MSTR IS.")
    lines.append("  3. Feed this report to AI for root cause analysis:")
    lines.append("     'Review this connectivity report. For each failed connection,")
    lines.append("      explain the likely cause and provide the exact fix command")
    lines.append("      (firewall rule, DNS entry, security group update).'")
    lines.append("  4. After fixing, re-run this script to confirm all connections are OPEN.")
    lines.append(sep)

    report_path = os.path.join(output_dir, "CONNECTIVITY_REPORT.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


# ─────────────────────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────

def run_connectivity_test(args):
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 64)
    print("  MicroStrategy Connectivity Tester v2.0")
    print(f"  CMC Host   : {args.cmc_host}")
    print(f"  CMC Port   : {args.cmc_port}")
    print(f"  Output     : {output_dir}")
    print(f"  Started    : {report_time}")
    print("=" * 64)

    # ── Parse all ODBC files ──────────────────────────────────
    odbc_files = []
    if args.odbc_file:
        odbc_files.append(args.odbc_file)
    if hasattr(args, "odbc_file_user") and args.odbc_file_user:
        odbc_files.append(args.odbc_file_user)

    # Auto-discover common locations if no file specified
    if not odbc_files:
        candidates = [
            "/etc/odbc.ini",
            os.path.expanduser("~/.odbc.ini"),
            "C:/Windows/System32/odbc.ini",
            "C:/odbc.ini",
            "./odbc.ini",
        ]
        for c in candidates:
            if os.path.exists(c):
                odbc_files.append(c)
                print(f"  Auto-discovered: {c}")
                break

    if not odbc_files:
        print("\n  [ERROR] No odbc.ini file found. Use --odbc-file to specify path.")
        sys.exit(1)

    all_connections = []
    for f in odbc_files:
        print(f"\n  Parsing: {f}")
        conns = parse_odbc_ini(f)
        print(f"  Found {len(conns)} DSN entries")
        all_connections.extend(conns)

    # Deduplicate by DSN name
    seen = set()
    unique_connections = []
    for c in all_connections:
        if c["dsn_name"] not in seen:
            seen.add(c["dsn_name"])
            unique_connections.append(c)
    all_connections = unique_connections

    print(f"\n  Total unique DSNs: {len(all_connections)}")

    # ── Write inventory CSV (before tests) ───────────────────
    inventory_fields = [
        "dsn_name", "server", "port", "database", "db_type",
        "category", "driver", "uid", "has_password",
        "charset", "ssl_mode", "description", "odbc_file"
    ]
    inventory_path = os.path.join(output_dir, "db_connections_inventory.csv")
    with open(inventory_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=inventory_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_connections)
    print(f"  Inventory written → db_connections_inventory.csv ({len(all_connections)} entries)")

    # ── Test CMC connectivity ─────────────────────────────────
    print(f"\n  Testing CMC connectivity: {args.cmc_host}:{args.cmc_port}")
    cmc_dns_status, cmc_ip = resolve_dns(args.cmc_host)
    cmc_ping_status, cmc_ping_latency = test_ping(args.cmc_host)
    cmc_tcp_status, cmc_tcp_latency = test_tcp_port(args.cmc_host, args.cmc_port)

    cmc_results = {
        "dns_status": cmc_dns_status,
        "resolved_ip": cmc_ip,
        "ping_status": cmc_ping_status,
        "ping_latency": cmc_ping_latency,
        "tcp_status": cmc_tcp_status,
        "tcp_latency": cmc_tcp_latency,
    }

    cmc_icon = "[PASS]" if cmc_tcp_status == "OPEN" else "[FAIL]"
    print(f"  CMC DNS: {cmc_dns_status} ({cmc_ip})")
    print(f"  CMC Ping: {cmc_ping_status} {cmc_ping_latency}")
    print(f"  CMC TCP Port: {cmc_icon} {cmc_tcp_status} {cmc_tcp_latency}")

    # ── Test each DB connection ───────────────────────────────
    print(f"\n  Testing {len(all_connections)} DB connections...")
    print(f"  {'#':>3}  {'DSN Name':<35} {'Host':<35} {'Port':>5}  {'DNS':>8}  {'Ping':>12}  {'TCP Port'}")
    print(f"  {'-'*120}")

    for idx, conn in enumerate(all_connections, 1):
        server = conn.get("server", "")
        port = conn.get("port", 0)
        dsn = conn.get("dsn_name", "")

        # DNS
        dns_status, resolved_ip = resolve_dns(server)
        conn["dns_resolves"] = dns_status
        conn["resolved_ip"] = resolved_ip

        # Ping
        if not args.skip_ping and server:
            ping_status, ping_latency = test_ping(server, count=2, timeout_sec=3)
        else:
            ping_status, ping_latency = "SKIPPED", ""
        conn["ping_status"] = ping_status
        conn["ping_latency_ms"] = ping_latency

        # TCP Port
        if server and port:
            tcp_status, tcp_latency = test_tcp_port(server, int(port), timeout_sec=5)
            # Also try curl for HTTP ports
            if port in (80, 443, 8080, 8443):
                curl_status, curl_detail = test_curl_port(server, int(port), timeout_sec=5)
                if curl_status.startswith("HTTP_"):
                    tcp_status = curl_status  # Use HTTP status code for web ports
        else:
            tcp_status, tcp_latency = "NO_PORT", ""
        conn["tcp_port_status"] = tcp_status
        conn["tcp_port_latency_ms"] = tcp_latency

        # Print progress
        tcp_icon = "✓" if tcp_status == "OPEN" else "✗" if tcp_status in ("CLOSED", "TIMEOUT", "DNS_FAIL") else "?"
        print(
            f"  {idx:>3}  {dsn[:34]:<35} {server[:34]:<35} {str(port):>5}  "
            f"{dns_status:>8}  {ping_status:>12}  {tcp_icon} {tcp_status} {tcp_latency}"
        )

        # Small delay to avoid hammering networks
        time.sleep(0.2)

    # ── Write results CSV ─────────────────────────────────────
    results_fields = [
        "dsn_name", "server", "port", "database", "db_type", "category",
        "dns_resolves", "resolved_ip",
        "ping_status", "ping_latency_ms",
        "tcp_port_status", "tcp_port_latency_ms",
        "driver", "odbc_file"
    ]
    results_path = os.path.join(output_dir, "connectivity_results.csv")
    with open(results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_connections)
    print(f"\n  Results written → connectivity_results.csv")

    # ── Generate text report ──────────────────────────────────
    report_path = generate_connectivity_report(
        all_connections, cmc_results, output_dir, report_time,
        args.cmc_host, args.cmc_port
    )
    print(f"  Report written  → CONNECTIVITY_REPORT.txt")

    # ── Summary ───────────────────────────────────────────────
    total = len(all_connections)
    tcp_open = sum(1 for c in all_connections if c.get("tcp_port_status") == "OPEN")
    tcp_fail = sum(1 for c in all_connections
                   if c.get("tcp_port_status") in ("CLOSED", "TIMEOUT", "DNS_FAIL", "NO_ROUTE", "ERROR"))

    print("")
    print("=" * 64)
    print(f"  CONNECTIVITY TEST COMPLETE")
    print(f"  CMC Port      : {'OPEN ✓' if cmc_results['tcp_status'] == 'OPEN' else 'FAIL ✗ ' + cmc_results['tcp_status']}")
    print(f"  DB Connections: {tcp_open}/{total} reachable")
    if tcp_fail:
        print(f"  FAILURES      : {tcp_fail} — see CONNECTIVITY_REPORT.txt Section 4")
    print("=" * 64)
    print(f"\n  NEXT: Feed CONNECTIVITY_REPORT.txt to AI for remediation steps.")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MicroStrategy Connectivity Tester — validate DB reachability from CMC.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Standard run with system odbc.ini
  python mstr_connectivity_tester.py \\
      --odbc-file /etc/odbc.ini \\
      --cmc-host  cloud-mstr.company.com \\
      --cmc-port  34952

  # Windows with custom odbc.ini location
  python mstr_connectivity_tester.py \\
      --odbc-file C:\\MSTR\\odbc.ini \\
      --cmc-host  cloud.company.com \\
      --cmc-port  34952

  # Skip ping tests (useful in environments blocking ICMP)
  python mstr_connectivity_tester.py \\
      --odbc-file /etc/odbc.ini \\
      --cmc-host  cloud.company.com \\
      --cmc-port  443 \\
      --skip-ping

  # Also test a second ODBC file (user-level)
  python mstr_connectivity_tester.py \\
      --odbc-file /etc/odbc.ini \\
      --odbc-file-user ~/.odbc.ini \\
      --cmc-host  cloud.company.com \\
      --cmc-port  34952
"""
    )
    parser.add_argument("--odbc-file", default=None,
                        help="Path to system-level odbc.ini (default: auto-discover /etc/odbc.ini)")
    parser.add_argument("--odbc-file-user", default=None,
                        help="Path to user-level odbc.ini (optional, merged with system odbc.ini)")
    parser.add_argument("--cmc-host", required=True,
                        help="CMC cloud IS hostname or IP (e.g. cloud-mstr.company.com)")
    parser.add_argument("--cmc-port", type=int, default=34952,
                        help="CMC Intelligence Server port (default: 34952)")
    parser.add_argument("--output-dir", default="./connectivity_results",
                        help="Directory to write output files (default: ./connectivity_results)")
    parser.add_argument("--skip-ping", action="store_true",
                        help="Skip ping tests (useful when ICMP is blocked by firewall)")

    args = parser.parse_args()
    run_connectivity_test(args)


if __name__ == "__main__":
    main()
