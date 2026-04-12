#!/usr/bin/env python3
"""
================================================================================
  MicroStrategy Cache Warmer v2.0
  =================================
  After migration, caches on the cloud IS are empty. The first users to open
  reports experience slow load times while caches rebuild from scratch.

  This script pre-executes your top reports on the cloud IS via REST API so
  that caches are warm BEFORE users log in — eliminating the first-access
  slowdown entirely.

  EXECUTION LAYER: REST API — runs from your laptop, no cluster shell needed.
  The IS executes the reports itself and populates its own report cache.

  USAGE:
    # Warm top 50 reports across all projects (uses 09_reports.csv from harvest)
    python mstr_cache_warmer.py \
        --host        https://CLOUD-MSTR/MicroStrategyLibrary \
        --username    Administrator \
        --password    CloudPass \
        --reports-csv ./discovery_output/09_reports.csv \
        --top-n       50 \
        --output-dir  ./cache_warm_results

    # Warm specific project only
    python mstr_cache_warmer.py \
        --host        https://CLOUD-MSTR/MicroStrategyLibrary \
        --username    Administrator \
        --password    CloudPass \
        --reports-csv ./discovery_output/09_reports.csv \
        --project-id  YOUR_PROJECT_GUID \
        --top-n       20

    # Dry run — see which reports would be executed
    python mstr_cache_warmer.py \
        --host        https://CLOUD-MSTR/MicroStrategyLibrary \
        --username    Administrator --password CloudPass \
        --reports-csv ./discovery_output/09_reports.csv \
        --mode        dry-run

  REQUIREMENTS: Python 3.8+, pip install requests

  AUTHOR: MicroStrategy Admin Automation Toolkit | VERSION: 2.0
================================================================================
"""

import argparse
import csv
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
    print("[ERROR] pip install requests"); sys.exit(1)


class MSTRClient:
    def __init__(self, url: str, verify: bool = True):
        self.api = url.rstrip("/") + "/api"
        self.session = requests.Session()
        self.session.verify = verify
        self.token = None

    def _h(self, project_id: str = None) -> Dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:    h["X-MSTR-AuthToken"] = self.token
        if project_id:    h["X-MSTR-ProjectID"] = project_id
        return h

    def login(self, user, pw, mode=1):
        r = self.session.post(f"{self.api}/auth/login",
            json={"username": user, "password": pw, "loginMode": mode, "applicationType": 35},
            headers={"Content-Type": "application/json"}, timeout=30)
        if r.status_code == 204:
            self.token = r.headers["X-MSTR-AuthToken"]; return True
        print(f"  [LOGIN FAIL] {r.status_code}: {r.text[:200]}"); return False

    def logout(self):
        try: self.session.post(f"{self.api}/auth/logout", headers=self._h(), timeout=10)
        except: pass

    def get(self, path, params=None, project_id=None):
        try:
            r = self.session.get(f"{self.api}/{path.lstrip('/')}",
                headers=self._h(project_id), params=params, timeout=60)
            return r.json() if r.status_code == 200 else None
        except: return None

    def post(self, path, payload=None, project_id=None, timeout=60):
        try:
            r = self.session.post(f"{self.api}/{path.lstrip('/')}",
                headers=self._h(project_id), json=payload or {}, timeout=timeout)
            try:    body = r.json()
            except: body = r.text
            return r.status_code, body
        except Exception as e: return 0, str(e)

    def delete(self, path, project_id=None):
        try:
            r = self.session.delete(f"{self.api}/{path.lstrip('/')}",
                headers=self._h(project_id), timeout=30)
            return r.status_code
        except: return 0


def load_csv(path: str) -> List[Dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


# ─────────────────────────────────────────────────────────────
# EXECUTE REPORT (create instance → get result → delete instance)
# ─────────────────────────────────────────────────────────────

def execute_report(client: MSTRClient, report_id: str, project_id: str,
                   timeout_sec: int = 120) -> Dict:
    """
    Execute a report on the IS by creating a report instance.
    This populates the IS report cache.
    POST /api/reports/{id}/instances
    Returns timing and result metadata.
    """
    start = time.time()
    result = {
        "status": "",
        "elapsed_ms": "",
        "rows": 0,
        "instance_id": "",
        "error": "",
    }

    # Create report instance — this executes the report and caches the result
    payload = {
        "promptAnswers": [],    # Accept all defaults
        "resolveOnly":   False,
    }
    status, body = client.post(
        f"/reports/{report_id}/instances",
        payload=payload,
        project_id=project_id,
        timeout=timeout_sec
    )
    elapsed = (time.time() - start) * 1000
    result["elapsed_ms"] = f"{elapsed:.0f}"

    if status in (200, 201):
        result["status"] = "EXECUTED"
        if isinstance(body, dict):
            result["instance_id"] = body.get("instanceId", body.get("id", ""))
            # Get row count if available
            result["rows"] = body.get("data", {}).get("paging", {}).get("total", 0)

        # Clean up the instance to not leave orphans
        if result["instance_id"]:
            client.delete(
                f"/reports/{report_id}/instances/{result['instance_id']}",
                project_id=project_id
            )
    elif status == 400:
        body_str = str(body).lower() if body else ""
        if "prompt" in body_str:
            # Report has required prompts — can't execute without answering
            result["status"] = "SKIP_PROMPTS"
            result["error"] = "Report has required prompts — skipped for cache warming"
        else:
            error = body.get("message", str(body))[:150] if isinstance(body, dict) else str(body)[:150]
            result["status"] = "FAIL"
            result["error"] = error
    elif status == 403:
        result["status"] = "SKIP_ACCESS"
        result["error"] = "Admin account lacks execute access to this report"
    elif status == 404:
        result["status"] = "NOT_FOUND"
        result["error"] = "Report not found on cloud IS — may not have been migrated yet"
    elif status == 0:
        result["status"] = "TIMEOUT"
        result["error"] = "Request timed out"
    else:
        error = body.get("message", str(body))[:150] if isinstance(body, dict) else str(body)[:150]
        result["status"] = f"HTTP_{status}"
        result["error"] = error

    return result


# ─────────────────────────────────────────────────────────────
# DOSSIER / DOCUMENT WARM (via /api/dossiers/{id}/instances)
# ─────────────────────────────────────────────────────────────

def execute_dossier(client: MSTRClient, dossier_id: str, project_id: str,
                    timeout_sec: int = 120) -> Dict:
    """Execute a dossier to warm its cache."""
    start = time.time()
    result = {"status": "", "elapsed_ms": "", "error": ""}
    status, body = client.post(
        f"/dossiers/{dossier_id}/instances",
        payload={},
        project_id=project_id,
        timeout=timeout_sec
    )
    elapsed = (time.time() - start) * 1000
    result["elapsed_ms"] = f"{elapsed:.0f}"

    if status in (200, 201, 204):
        result["status"] = "EXECUTED"
        if isinstance(body, dict):
            instance_id = body.get("mid", body.get("id", ""))
            if instance_id:
                client.delete(f"/dossiers/{dossier_id}/instances/{instance_id}",
                              project_id=project_id)
    elif status == 0:
        result["status"] = "TIMEOUT"
    else:
        result["status"] = f"HTTP_{status}"
        result["error"] = str(body)[:120]

    return result


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run(args):
    dry_run = args.mode == "dry-run"
    os.makedirs(args.output_dir, exist_ok=True)
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 64)
    print("  MicroStrategy Cache Warmer v2.0")
    print(f"  Target IS : {args.host}")
    print(f"  Top-N     : {args.top_n} reports per project")
    print(f"  Mode      : {args.mode}")
    print(f"  Started   : {report_time}")
    print("=" * 64)

    # Load reports from harvest CSV
    all_reports = load_csv(args.reports_csv)
    dossiers_csv = args.reports_csv.replace("09_reports", "10_documents_dossiers")
    all_dossiers = load_csv(dossiers_csv) if os.path.exists(dossiers_csv) else []

    print(f"\n  Loaded {len(all_reports)} reports, {len(all_dossiers)} dossiers from harvest")

    # Filter by project if specified
    if args.project_id:
        all_reports  = [r for r in all_reports  if r.get("project_id") == args.project_id]
        all_dossiers = [d for d in all_dossiers if d.get("project_id") == args.project_id]
        print(f"  Filtered to project {args.project_id}: {len(all_reports)} reports, {len(all_dossiers)} dossiers")

    # Group by project and take top N per project
    projects_seen = {}
    for r in all_reports:
        pid = r.get("project_id", "")
        if pid not in projects_seen:
            projects_seen[pid] = {"name": r.get("project_name", pid), "reports": [], "dossiers": []}
        if len(projects_seen[pid]["reports"]) < args.top_n:
            projects_seen[pid]["reports"].append(r)

    for d in all_dossiers:
        pid = d.get("project_id", "")
        if pid in projects_seen and d.get("object_type_name") in ("Dossier", "Document"):
            if len(projects_seen[pid]["dossiers"]) < (args.top_n // 2):
                projects_seen[pid]["dossiers"].append(d)

    total_to_warm = sum(
        len(p["reports"]) + len(p["dossiers"]) for p in projects_seen.values()
    )
    print(f"\n  Will warm {total_to_warm} objects across {len(projects_seen)} project(s)")

    client = MSTRClient(args.host, verify=not args.no_ssl_verify)
    if not dry_run:
        if not client.login(args.username, args.password, int(args.login_mode)):
            sys.exit(1)
        print("  Authenticated.")

    all_results = []
    executed = 0
    skipped  = 0
    failed   = 0

    try:
        for pid, pdata in projects_seen.items():
            pname = pdata["name"]
            print(f"\n  ── Project: {pname}")
            print(f"     Reports to warm: {len(pdata['reports'])} | Dossiers: {len(pdata['dossiers'])}")
            print(f"     {'#':>4}  {'Object Name':<45} {'Status':<20} {'Elapsed':>8}  {'Rows'}")
            print(f"     {'-'*100}")

            # Warm reports
            for idx, report in enumerate(pdata["reports"], 1):
                rid   = report.get("id", "")
                rname = report.get("name", "")

                if dry_run:
                    print(f"     {idx:>4}  {rname[:44]:<45} DRY-RUN")
                    all_results.append({**report, "warm_status": "DRY_RUN",
                                       "elapsed_ms": "", "rows": "", "error": ""})
                    continue

                result = execute_report(client, rid, pid,
                                        timeout_sec=args.timeout)
                status = result["status"]
                elapsed = result["elapsed_ms"]
                rows    = result.get("rows", "")
                error   = result.get("error", "")

                icon = "✓" if status == "EXECUTED" else "~" if "SKIP" in status else "✗"
                print(f"     {idx:>4}  {icon} {rname[:43]:<44} {status:<20} {elapsed:>6}ms  {rows}")

                all_results.append({
                    **report,
                    "warm_status": status,
                    "elapsed_ms":  elapsed,
                    "rows":        rows,
                    "error":       error,
                })

                if status == "EXECUTED":   executed += 1
                elif "SKIP" in status:     skipped  += 1
                else:                      failed   += 1

                time.sleep(args.delay)

            # Warm dossiers
            for idx, dossier in enumerate(pdata["dossiers"], 1):
                did   = dossier.get("id", "")
                dname = dossier.get("name", "")

                if dry_run:
                    all_results.append({**dossier, "warm_status": "DRY_RUN",
                                       "elapsed_ms": "", "rows": "", "error": ""})
                    continue

                result = execute_dossier(client, did, pid, timeout_sec=args.timeout)
                status  = result["status"]
                elapsed = result["elapsed_ms"]
                icon = "✓" if status == "EXECUTED" else "✗"
                print(f"     {idx:>4}  {icon} [DOSSIER] {dname[:39]:<40} {status:<20} {elapsed:>6}ms")

                all_results.append({
                    **dossier,
                    "warm_status": status,
                    "elapsed_ms":  elapsed,
                    "rows":        "",
                    "error":       result.get("error", ""),
                })

                if status == "EXECUTED":   executed += 1
                else:                      failed   += 1

                time.sleep(args.delay)

    finally:
        if not dry_run:
            client.logout()

    # ── Write results ────────────────────────────────────────
    results_path = os.path.join(args.output_dir, "cache_warm_results.csv")
    if all_results:
        keys = list(all_results[0].keys())
        with open(results_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\n  Results → {results_path}")

    # ── Summary ──────────────────────────────────────────────
    print("")
    print("=" * 64)
    print(f"  CACHE WARMING COMPLETE")
    print(f"  Executed (cached) : {executed}")
    print(f"  Skipped (prompts) : {skipped}")
    print(f"  Failed            : {failed}")
    if executed > 0:
        print(f"  [PASS] {executed} report(s) now cached on cloud IS.")
        print(f"  Users will experience fast load times from first login.")
    if failed > 0:
        print(f"  [INFO] {failed} failure(s) — check cache_warm_results.csv.")
        print(f"  Failures are non-blocking. Reports will cache on first user access.")
    print("=" * 64)


def main():
    p = argparse.ArgumentParser(
        description="Pre-execute top reports on cloud IS to warm caches before go-live."
    )
    p.add_argument("--host",        required=True)
    p.add_argument("--username",    required=True)
    p.add_argument("--password",    required=True)
    p.add_argument("--reports-csv", default="./discovery_output/09_reports.csv")
    p.add_argument("--project-id",  default=None, help="Warm a single project only")
    p.add_argument("--top-n",       type=int, default=50,
                   help="Top N reports to warm per project (default: 50)")
    p.add_argument("--timeout",     type=int, default=120,
                   help="Seconds to wait per report execution (default: 120)")
    p.add_argument("--delay",       type=float, default=1.0,
                   help="Seconds to wait between report executions (default: 1.0)")
    p.add_argument("--mode",        default="warm", choices=["warm", "dry-run"])
    p.add_argument("--login-mode",  default="1", choices=["1", "4", "8", "16", "64"])
    p.add_argument("--no-ssl-verify", action="store_true")
    p.add_argument("--output-dir",  default="./cache_warm_results")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
