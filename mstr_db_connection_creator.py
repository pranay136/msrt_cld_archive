#!/usr/bin/env python3
"""
================================================================================
  MicroStrategy DB Connection Creator & IS-Side Tester v2.0
  ===========================================================
  PROBLEM THIS SOLVES:
    ping/curl tests from your LAPTOP only prove your laptop can reach the DB.
    They say nothing about whether the Intelligence SERVER can reach it.

    This script solves that by:
      1. Reading odbc.ini to get all connection definitions
      2. Creating each connection on the TARGET IS via REST API POST /api/datasources
      3. Calling POST /api/datasources/{id}/testConnection — which fires the test
         FROM THE IS ITSELF, through the IS's own network path
      4. Reporting pass/fail per connection with the IS's actual error message

    Run this from YOUR LAPTOP. The IS does the real connectivity work.

  TWO MODES:
    --mode create-and-test   Create all connections on IS, then test each one (DEFAULT)
    --mode test-existing     Test connections already on the IS (skips creation)
    --mode create-only       Create connections without testing
    --mode dry-run           Print payloads only, no API calls made

  USAGE:
    # Create all connections from odbc.ini on cloud IS, then test each from IS side
    python mstr_db_connection_creator.py \
        --host    https://CLOUD-MSTR/MicroStrategyLibrary \
        --username Administrator \
        --password YourPassword \
        --odbc-file /etc/odbc.ini \
        --mode    create-and-test \
        --output-dir ./db_connection_results

    # Only test what is already on the IS (post-migration validation)
    python mstr_db_connection_creator.py \
        --host    https://CLOUD-MSTR/MicroStrategyLibrary \
        --username Administrator \
        --password YourPassword \
        --mode    test-existing

    # Dry run — see what payloads would be sent without touching the IS
    python mstr_db_connection_creator.py \
        --host    https://CLOUD-MSTR/MicroStrategyLibrary \
        --username Administrator \
        --password YourPassword \
        --odbc-file /etc/odbc.ini \
        --mode    dry-run

  OUTPUT:
    db_connection_results.csv      All connections with IS-side test result
    DB_CONNECTION_REPORT.txt       Human-readable pass/fail summary
    created_connection_ids.json    Map of DSN name → cloud IS datasource ID (for cleanup)

  REQUIREMENTS:
    Python 3.8+   pip install requests

  NOTE ON CREDENTIALS:
    DB login credentials are NOT stored in the datasource connection itself in MSTR.
    They are stored in separate "Database Login" objects linked to the datasource.
    This script creates anonymous datasource shells (no credentials embedded) which
    is the correct MSTR architecture. Credentials are managed via MSTR User Manager
    → Database Logins, linked to the datasource via Database Roles.

  AUTHOR: MicroStrategy Admin Automation Toolkit
  VERSION: 2.0
================================================================================
"""

import argparse
import configparser
import csv
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except ImportError:
    print("[ERROR] 'requests' not installed. Run: pip install requests")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# MSTR DB TYPE CODE MAP
# Maps human-readable DB type → MSTR internal dbType enum value
# Used in POST /api/datasources payload
# ─────────────────────────────────────────────────────────────

MSTR_DB_TYPE_CODES = {
    "Microsoft SQL Server": {"dbType": "sql_server",        "defaultPort": 1433},
    "Oracle Database":      {"dbType": "oracle",            "defaultPort": 1521},
    "MySQL":                {"dbType": "my_sql",            "defaultPort": 3306},
    "MariaDB":              {"dbType": "my_sql",            "defaultPort": 3306},
    "PostgreSQL":           {"dbType": "postgre_sql",       "defaultPort": 5432},
    "Teradata":             {"dbType": "teradata",          "defaultPort": 1025},
    "Snowflake":            {"dbType": "snowflake",         "defaultPort": 443},
    "Amazon Redshift":      {"dbType": "redshift",          "defaultPort": 5439},
    "Google BigQuery":      {"dbType": "big_query",         "defaultPort": 443},
    "Azure Synapse":        {"dbType": "sql_server",        "defaultPort": 1433},
    "Databricks":           {"dbType": "databricks",        "defaultPort": 443},
    "Apache Hive":          {"dbType": "hive",              "defaultPort": 10000},
    "Apache Spark":         {"dbType": "spark_sql",         "defaultPort": 10001},
    "Apache Impala":        {"dbType": "impala",            "defaultPort": 21050},
    "IBM DB2":              {"dbType": "db2",               "defaultPort": 50000},
    "Sybase ASE":           {"dbType": "sybase_iq",         "defaultPort": 5000},
    "Vertica":              {"dbType": "vertica",           "defaultPort": 5433},
    "SAP HANA":             {"dbType": "sap_hana",          "defaultPort": 30015},
    "Amazon Athena":        {"dbType": "athena",            "defaultPort": 443},
    "Presto/Trino":         {"dbType": "presto",            "defaultPort": 8080},
    "Unknown / Other":      {"dbType": "generic_odbc",      "defaultPort": 0},
}

DB_DRIVER_DETECT = [
    (["sqlserver", "sql server", "mssql"],  "Microsoft SQL Server"),
    (["oracle"],                             "Oracle Database"),
    (["mysql"],                              "MySQL"),
    (["mariadb"],                            "MariaDB"),
    (["postgresql", "postgre", "psql"],      "PostgreSQL"),
    (["teradata"],                           "Teradata"),
    (["snowflake"],                          "Snowflake"),
    (["redshift"],                           "Amazon Redshift"),
    (["bigquery"],                           "Google BigQuery"),
    (["synapse"],                            "Azure Synapse"),
    (["databricks"],                         "Databricks"),
    (["hive"],                               "Apache Hive"),
    (["spark"],                              "Apache Spark"),
    (["impala"],                             "Apache Impala"),
    (["db2"],                                "IBM DB2"),
    (["sybase"],                             "Sybase ASE"),
    (["vertica"],                            "Vertica"),
    (["hana"],                               "SAP HANA"),
    (["athena"],                             "Amazon Athena"),
    (["presto", "trino"],                    "Presto/Trino"),
]


def detect_db_type(driver_str: str, dsn_name: str) -> str:
    search = (driver_str + " " + dsn_name).lower()
    for keywords, label in DB_DRIVER_DETECT:
        if any(k in search for k in keywords):
            return label
    return "Unknown / Other"


# ─────────────────────────────────────────────────────────────
# ODBC PARSER
# ─────────────────────────────────────────────────────────────

def parse_odbc_ini(filepath: str) -> List[Dict]:
    if not os.path.exists(filepath):
        print(f"  [ERROR] File not found: {filepath}")
        return []

    config = configparser.RawConfigParser()
    config.optionxform = str
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            config.read_string(f.read())
    except Exception as e:
        print(f"  [ERROR] Cannot parse {filepath}: {e}")
        return []

    skip = {"odbc data sources", "odbc", "odbc driver manager"}
    connections = []

    for section in config.sections():
        if section.lower().strip() in skip:
            continue

        row = dict(config[section])

        def get(*keys):
            for k in keys:
                for ak in row:
                    if ak.lower() == k.lower():
                        return row[ak].strip()
            return ""

        driver   = get("Driver", "DRIVER")
        server   = get("Server", "SERVER", "Host", "HOST",
                       "Servername", "Hostname", "AccountName")
        port_str = get("Port", "PORT", "TDMSTPortNumber")
        database = get("Database", "DATABASE", "DBName", "Catalog", "db")
        uid      = get("UID", "uid", "User", "Username")
        description = get("Description", "DESCRIPTION")
        charset  = get("Charset", "charset", "Encoding")
        ssl_mode = get("SSLMode", "ssl_mode", "Encrypt", "SSL")

        db_type  = detect_db_type(driver, section)
        type_cfg = MSTR_DB_TYPE_CODES.get(db_type, MSTR_DB_TYPE_CODES["Unknown / Other"])
        default_port = type_cfg["defaultPort"]

        try:
            port = int(port_str) if port_str else default_port
        except ValueError:
            port = default_port

        if not server and not database:
            continue  # Skip incomplete / placeholder entries

        connections.append({
            "dsn_name":    section,
            "driver":      driver,
            "server":      server,
            "port":        port,
            "database":    database,
            "uid":         uid,
            "db_type":     db_type,
            "mstr_db_type_code": type_cfg["dbType"],
            "description": description,
            "charset":     charset,
            "ssl_mode":    ssl_mode,
        })

    return connections


# ─────────────────────────────────────────────────────────────
# MSTR REST CLIENT (minimal)
# ─────────────────────────────────────────────────────────────

class MSTRClient:
    def __init__(self, base_url: str, verify_ssl: bool = True):
        self.api = base_url.rstrip("/") + "/api"
        self.verify = verify_ssl
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.token = None

    def _h(self, extra: Dict = None) -> Dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            h["X-MSTR-AuthToken"] = self.token
        if extra:
            h.update(extra)
        return h

    def login(self, username: str, password: str, login_mode: int = 1) -> bool:
        resp = self.session.post(
            f"{self.api}/auth/login",
            json={"username": username, "password": password,
                  "loginMode": login_mode, "applicationType": 35},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        if resp.status_code == 204:
            self.token = resp.headers.get("X-MSTR-AuthToken")
            return True
        print(f"  [ERROR] Login failed: HTTP {resp.status_code} — {resp.text[:200]}")
        return False

    def logout(self):
        try:
            self.session.post(f"{self.api}/auth/logout", headers=self._h(), timeout=10)
        except Exception:
            pass

    def get(self, path: str, params: Dict = None) -> Optional[Any]:
        try:
            r = self.session.get(f"{self.api}/{path.lstrip('/')}",
                                 headers=self._h(), params=params, timeout=60)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def post(self, path: str, payload: Dict, timeout: int = 30) -> Tuple[int, Any]:
        try:
            r = self.session.post(f"{self.api}/{path.lstrip('/')}",
                                  headers=self._h(), json=payload, timeout=timeout)
            try:
                body = r.json()
            except Exception:
                body = r.text
            return r.status_code, body
        except Exception as e:
            return 0, str(e)

    def delete(self, path: str) -> int:
        try:
            r = self.session.delete(f"{self.api}/{path.lstrip('/')}",
                                    headers=self._h(), timeout=30)
            return r.status_code
        except Exception:
            return 0


# ─────────────────────────────────────────────────────────────
# DATASOURCE BUILDER
# Constructs the MSTR REST API payload for POST /api/datasources
# ─────────────────────────────────────────────────────────────

def build_datasource_payload(conn: Dict) -> Dict:
    """
    Build a POST /api/datasources payload from an odbc.ini connection dict.
    Creates the datasource DEFINITION (not the login/password — those are
    managed separately via Database Login objects in MSTR).
    """
    db_type_code = conn.get("mstr_db_type_code", "generic_odbc")

    payload = {
        "name": conn["dsn_name"],
        "description": conn.get("description", f"Auto-created from odbc.ini by migration toolkit"),
        "datasourceType": "normal",
        "database": {
            "type": db_type_code,
            "host": conn.get("server", ""),
            "port": conn.get("port", 0),
            "databaseName": conn.get("database", ""),
        },
    }

    # Add optional fields if present
    if conn.get("charset"):
        payload["database"]["charset"] = conn["charset"]

    # SSL: if ssl_mode is set to yes/require/true, flag it
    ssl_val = conn.get("ssl_mode", "").lower()
    if ssl_val in ("yes", "true", "require", "1", "enabled"):
        payload["database"]["ssl"] = True

    return payload


# ─────────────────────────────────────────────────────────────
# CREATE DATASOURCE ON IS
# ─────────────────────────────────────────────────────────────

def create_datasource(client: MSTRClient, conn: Dict, dry_run: bool = False) -> Dict:
    """
    Create a datasource on the IS via POST /api/datasources.
    Returns a result dict with status and the created datasource ID.
    """
    payload = build_datasource_payload(conn)
    result = {
        "dsn_name": conn["dsn_name"],
        "server": conn.get("server", ""),
        "port": conn.get("port", ""),
        "database": conn.get("database", ""),
        "db_type": conn.get("db_type", ""),
        "created_id": "",
        "create_status": "",
        "create_error": "",
        "test_status": "",
        "test_latency_ms": "",
        "test_error": "",
        "overall": "",
    }

    if dry_run:
        result["create_status"] = "DRY_RUN"
        result["overall"] = "DRY_RUN"
        print(f"  [DRY-RUN] Would POST /api/datasources:")
        print(f"    {json.dumps(payload, indent=4)[:400]}")
        return result

    status_code, body = client.post("/datasources", payload)

    if status_code in (200, 201):
        ds_id = body.get("id", "") if isinstance(body, dict) else ""
        result["created_id"] = ds_id
        result["create_status"] = "CREATED"
        return result
    else:
        error_msg = ""
        if isinstance(body, dict):
            error_msg = body.get("message", body.get("description", str(body)))[:200]
        else:
            error_msg = str(body)[:200]
        result["create_status"] = "FAILED"
        result["create_error"] = error_msg
        result["overall"] = "CREATE_FAIL"
        return result


# ─────────────────────────────────────────────────────────────
# TEST DATASOURCE FROM IS (the key function)
# POST /api/datasources/{id}/testConnection
# This fires from the IS's network — correct vantage point
# ─────────────────────────────────────────────────────────────

def test_datasource_from_is(client: MSTRClient, ds_id: str,
                             dsn_name: str, dry_run: bool = False) -> Dict:
    """
    Call POST /api/datasources/{id}/testConnection.
    The IS fires the connection test itself — this validates that the IS
    can reach the database, NOT just that your laptop can.
    """
    result = {
        "test_status": "",
        "test_latency_ms": "",
        "test_error": "",
    }

    if dry_run or not ds_id:
        result["test_status"] = "DRY_RUN" if dry_run else "NO_ID"
        return result

    start = time.time()
    # The testConnection endpoint takes an empty body or login credentials
    # We test without embedded credentials first (anonymous test of TCP reachability)
    status_code, body = client.post(
        f"/datasources/{ds_id}/testConnection",
        payload={},  # No credentials for TCP-level test
        timeout=15
    )
    elapsed_ms = f"{(time.time() - start) * 1000:.0f} ms"

    if status_code == 200:
        result["test_status"] = "REACHABLE"
        result["test_latency_ms"] = elapsed_ms
    elif status_code == 400:
        # 400 with a message about credentials means TCP worked but auth needed
        # That is still a connectivity WIN — IS can reach the host
        body_str = str(body).lower() if body else ""
        if any(k in body_str for k in ["credential", "login", "password",
                                         "authentication", "authorization",
                                         "access denied", "user"]):
            result["test_status"] = "REACHABLE_AUTH_NEEDED"
            result["test_latency_ms"] = elapsed_ms
            result["test_error"] = "IS reached DB but login credentials needed"
        else:
            error_msg = body.get("message", str(body))[:200] if isinstance(body, dict) else str(body)[:200]
            result["test_status"] = "FAILED"
            result["test_error"] = error_msg
    elif status_code == 404:
        result["test_status"] = "NOT_FOUND"
        result["test_error"] = "Datasource not found on IS — may have been deleted"
    else:
        error_msg = ""
        if isinstance(body, dict):
            error_msg = body.get("message", str(body))[:200]
        else:
            error_msg = str(body)[:200]

        # Classify common MSTR error messages
        error_lower = error_msg.lower()
        if any(k in error_lower for k in ["cannot connect", "connection refused",
                                            "unreachable", "network", "host"]):
            result["test_status"] = "UNREACHABLE"
        elif any(k in error_lower for k in ["timeout", "timed out"]):
            result["test_status"] = "TIMEOUT"
        elif any(k in error_lower for k in ["dns", "resolve", "unknown host"]):
            result["test_status"] = "DNS_FAIL"
        else:
            result["test_status"] = f"HTTP_{status_code}"

        result["test_error"] = error_msg

    return result


# ─────────────────────────────────────────────────────────────
# LIST EXISTING DATASOURCES
# ─────────────────────────────────────────────────────────────

def list_existing_datasources(client: MSTRClient) -> List[Dict]:
    raw = client.get("/datasources", params={"limit": 500}) or {}
    if isinstance(raw, list):
        return raw
    return raw.get("datasources", raw.get("result", []))


# ─────────────────────────────────────────────────────────────
# REPORT GENERATOR
# ─────────────────────────────────────────────────────────────

def generate_report(results: List[Dict], output_dir: str,
                    mode: str, report_time: str, base_url: str) -> str:
    lines = []
    sep = "=" * 80
    thin = "-" * 80

    def row(label, value, w=45):
        lines.append(f"  {label:<{w}} {value}")

    lines.append(sep)
    lines.append("  MICROSTRATEGY DB CONNECTION REPORT")
    lines.append("  IS-Side Connectivity Test (via POST /api/datasources/{id}/testConnection)")
    lines.append(sep)
    lines.append(f"  IS URL       : {base_url}")
    lines.append(f"  Mode         : {mode}")
    lines.append(f"  Report Time  : {report_time}")
    lines.append(f"  Total Tested : {len(results)}")
    lines.append(sep)

    # Counts
    reachable    = [r for r in results if r.get("test_status") in ("REACHABLE", "REACHABLE_AUTH_NEEDED")]
    auth_needed  = [r for r in results if r.get("test_status") == "REACHABLE_AUTH_NEEDED"]
    failed       = [r for r in results if r.get("test_status") in ("UNREACHABLE", "TIMEOUT", "DNS_FAIL", "FAILED")]
    created_fail = [r for r in results if r.get("create_status") == "FAILED"]

    lines.append("")
    lines.append("  SUMMARY")
    lines.append(thin)
    row("IS-Reachable (TCP level)", str(len(reachable)))
    row("  Fully reachable (no auth)", str(len(reachable) - len(auth_needed)))
    row("  Reachable (auth credentials needed)", str(len(auth_needed)))
    row("IS-Unreachable (network blocked)", str(len(failed)))
    if mode == "create-and-test":
        row("Creation failures", str(len(created_fail)))

    lines.append("")
    lines.append("  IMPORTANT: 'REACHABLE_AUTH_NEEDED' is a PASS for connectivity.")
    lines.append("  It means the IS TCP connection succeeded but DB login credentials")
    lines.append("  need to be configured in MSTR User Manager → Database Logins.")

    # Detail table
    lines.append("")
    lines.append("  DETAIL")
    lines.append(thin)
    lines.append(f"  {'DSN Name':<35} {'Host':<30} {'Port':>5}  {'DB Type':<22} {'Test Result':<25} {'Note'}")
    lines.append(f"  {thin}")

    for r in sorted(results, key=lambda x: (
        0 if x.get("test_status") in ("UNREACHABLE", "TIMEOUT", "DNS_FAIL", "FAILED") else
        1 if x.get("test_status") == "REACHABLE_AUTH_NEEDED" else 2
    )):
        status = r.get("test_status", "")
        icon = "✓" if status in ("REACHABLE", "REACHABLE_AUTH_NEEDED") else "✗"
        note = r.get("test_error", "")[:40]
        lines.append(
            f"  {icon} {r['dsn_name'][:34]:<35} "
            f"{r['server'][:29]:<30} "
            f"{str(r['port']):>5}  "
            f"{r['db_type'][:21]:<22} "
            f"{status:<25} "
            f"{note}"
        )

    # Failed detail
    if failed:
        lines.append("")
        lines.append(f"  FAILED CONNECTIONS — REQUIRE ACTION ({len(failed)})")
        lines.append(thin)
        for r in failed:
            lines.append(f"\n  [{r['dsn_name']}]")
            lines.append(f"    Server  : {r['server']}:{r['port']}")
            lines.append(f"    DB Type : {r['db_type']}")
            lines.append(f"    Status  : {r['test_status']}")
            lines.append(f"    Error   : {r['test_error']}")
            lines.append(f"    Action  : Check firewall/security group — open port {r['port']} "
                         f"from IS IP to {r['server']}")

    # Auth-needed detail
    if auth_needed:
        lines.append("")
        lines.append(f"  AUTH-NEEDED CONNECTIONS — TCP OK, CONFIGURE DB LOGIN ({len(auth_needed)})")
        lines.append(thin)
        lines.append("  For each of these, create a Database Login in MSTR:")
        lines.append("  MSTR Developer → Administration → Database Instances → DB Logins → New")
        for r in auth_needed:
            lines.append(f"    - {r['dsn_name']} ({r['server']})")

    lines.append("")
    lines.append(sep)
    lines.append("  NEXT STEPS")
    lines.append(sep)
    if failed:
        lines.append("  1. For UNREACHABLE connections: open firewall rules on cloud VPC/VNet")
        lines.append("     to allow IS IP → DB Host:Port. Ask network team for:")
        lines.append("     'Allow TCP from [IS IP address] to [DB Host] on port [Port]'")
    if auth_needed:
        lines.append("  2. For REACHABLE_AUTH_NEEDED: configure Database Login objects in MSTR")
        lines.append("     Administration console, then re-run with --mode test-existing")
    if not failed and not auth_needed:
        lines.append("  [PASS] All connections are fully reachable from the IS.")
        lines.append("  Proceed with migration — DB layer is network-ready.")
    lines.append(sep)

    report_path = os.path.join(output_dir, "DB_CONNECTION_REPORT.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return report_path


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run(args):
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dry_run = args.mode == "dry-run"

    print("=" * 64)
    print("  MicroStrategy DB Connection Creator & IS-Side Tester v2.0")
    print(f"  IS URL  : {args.host}")
    print(f"  Mode    : {args.mode}")
    print(f"  Output  : {output_dir}")
    print("=" * 64)

    client = MSTRClient(args.host, verify_ssl=not args.no_ssl_verify)

    if not dry_run:
        print("\n  Authenticating...")
        if not client.login(args.username, args.password, int(args.login_mode)):
            sys.exit(1)
        print("  Authenticated.")

    all_results = []
    created_ids = {}

    try:
        if args.mode in ("create-and-test", "create-only", "dry-run"):
            # ── Parse ODBC file ───────────────────────────────
            odbc_path = args.odbc_file
            if not odbc_path:
                for candidate in ["/etc/odbc.ini", os.path.expanduser("~/.odbc.ini"), "./odbc.ini"]:
                    if os.path.exists(candidate):
                        odbc_path = candidate
                        break
            if not odbc_path:
                print("[ERROR] No odbc.ini found. Use --odbc-file.")
                sys.exit(1)

            print(f"\n  Parsing {odbc_path}...")
            connections = parse_odbc_ini(odbc_path)
            print(f"  Found {len(connections)} DSN entries")

            # ── Create on IS ──────────────────────────────────
            print(f"\n  {'Creating' if not dry_run else 'Dry-running'} {len(connections)} datasources on IS...")
            for i, conn in enumerate(connections, 1):
                print(f"  [{i:>3}/{len(connections)}] {conn['dsn_name']} ({conn['server']}:{conn['port']})")
                result = create_datasource(client, conn, dry_run=dry_run)
                all_results.append(result)
                if result.get("created_id"):
                    created_ids[conn["dsn_name"]] = result["created_id"]
                time.sleep(0.3)

            # Save created IDs for cleanup / reference
            ids_path = os.path.join(output_dir, "created_connection_ids.json")
            with open(ids_path, "w") as f:
                json.dump(created_ids, f, indent=2)

        elif args.mode == "test-existing":
            # ── List existing datasources on IS ───────────────
            print("\n  Listing existing datasources on IS...")
            existing = list_existing_datasources(client)
            print(f"  Found {len(existing)} datasources")

            for ds in existing:
                all_results.append({
                    "dsn_name":    ds.get("name", ""),
                    "server":      ds.get("database", {}).get("host", ""),
                    "port":        ds.get("database", {}).get("port", ""),
                    "database":    ds.get("database", {}).get("databaseName", ""),
                    "db_type":     ds.get("database", {}).get("type", ""),
                    "created_id":  ds.get("id", ""),
                    "create_status": "EXISTING",
                    "create_error": "",
                    "test_status": "",
                    "test_latency_ms": "",
                    "test_error": "",
                    "overall": "",
                })
                created_ids[ds.get("name", "")] = ds.get("id", "")

        # ── Test each datasource from IS ──────────────────────
        if args.mode in ("create-and-test", "test-existing"):
            print(f"\n  Testing {len(all_results)} connections FROM the IS...")
            print(f"  {'#':>3}  {'DSN Name':<35} {'Result':<30} {'Latency'}")
            print(f"  {'-'*80}")

            for i, result in enumerate(all_results, 1):
                ds_id = result.get("created_id", "")
                if not ds_id:
                    result["test_status"] = "NO_ID"
                    print(f"  {i:>3}  {result['dsn_name']:<35} {'SKIPPED (no DS ID)':<30}")
                    continue

                test = test_datasource_from_is(client, ds_id, result["dsn_name"])
                result.update(test)

                status = test["test_status"]
                latency = test.get("test_latency_ms", "")
                icon = "✓" if status in ("REACHABLE", "REACHABLE_AUTH_NEEDED") else "✗"
                result["overall"] = "PASS" if icon == "✓" else "FAIL"

                print(f"  {i:>3}  {result['dsn_name'][:34]:<35} {icon} {status:<28} {latency}")
                time.sleep(0.3)

        # ── Write CSV ─────────────────────────────────────────
        csv_fields = ["dsn_name", "server", "port", "database", "db_type",
                      "create_status", "create_error",
                      "test_status", "test_latency_ms", "test_error", "overall"]
        csv_path = os.path.join(output_dir, "db_connection_results.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\n  Results → db_connection_results.csv")

        # ── Generate report ───────────────────────────────────
        report_path = generate_report(all_results, output_dir, args.mode,
                                      report_time, args.host)
        print(f"  Report  → DB_CONNECTION_REPORT.txt")

        # ── Summary ───────────────────────────────────────────
        reachable = sum(1 for r in all_results
                        if r.get("test_status") in ("REACHABLE", "REACHABLE_AUTH_NEEDED"))
        failed    = sum(1 for r in all_results
                        if r.get("test_status") in ("UNREACHABLE", "TIMEOUT", "DNS_FAIL", "FAILED"))

        print("")
        print("=" * 64)
        print(f"  IS-Reachable  : {reachable}/{len(all_results)}")
        print(f"  IS-Unreachable: {failed}/{len(all_results)}")
        if failed == 0:
            print("  [PASS] All DB connections reachable from IS.")
        else:
            print(f"  [FAIL] {failed} connection(s) blocked — fix firewall/routing.")
        print("=" * 64)

    finally:
        if not dry_run:
            client.logout()


def main():
    parser = argparse.ArgumentParser(
        description="Create + IS-side test all DB connections from odbc.ini on MicroStrategy cloud IS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  create-and-test   Create datasources from odbc.ini, then test each FROM the IS (default)
  test-existing     Test connections already defined on the IS (post-migration check)
  create-only       Create datasource definitions without testing
  dry-run           Print payloads only — no API calls, no changes on IS

Examples:
  python mstr_db_connection_creator.py \\
      --host https://cloud-mstr.company.com/MicroStrategyLibrary \\
      --username Administrator --password Pass \\
      --odbc-file /etc/odbc.ini --mode create-and-test

  python mstr_db_connection_creator.py \\
      --host https://cloud-mstr.company.com/MicroStrategyLibrary \\
      --username Administrator --password Pass \\
      --mode test-existing
"""
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--odbc-file", default=None)
    parser.add_argument("--mode", default="create-and-test",
                        choices=["create-and-test", "test-existing", "create-only", "dry-run"])
    parser.add_argument("--login-mode", default="1",
                        choices=["1", "4", "8", "16", "64"])
    parser.add_argument("--no-ssl-verify", action="store_true")
    parser.add_argument("--output-dir", default="./db_connection_results")

    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
