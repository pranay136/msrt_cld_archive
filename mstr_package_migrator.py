#!/usr/bin/env python3
"""
================================================================================
  MicroStrategy Package Migrator v2.0
  =====================================
  Uses the MSTR REST API /api/packages and /api/migrations endpoints
  (available in MSTR 2021 Update 5+) to programmatically:
    1. Create a migration package on the SOURCE IS (on-prem)
    2. Download the package binary
    3. Upload and import it to the TARGET IS (cloud CMC)

  This replaces the manual Object Manager GUI workflow entirely.
  Run entirely from your laptop over HTTPS — no shell access to either IS needed.

  EXECUTION LAYER: REST API — runs from your laptop, logic executes on IS.

  USAGE:
    # Full migrate: export from on-prem, import to cloud
    python mstr_package_migrator.py \
        --source-host   https://ONPREM-MSTR/MicroStrategyLibrary \
        --source-user   Administrator --source-pass OldPass \
        --target-host   https://CLOUD-MSTR/MicroStrategyLibrary \
        --target-user   Administrator --target-pass NewPass \
        --project-id    YOUR_PROJECT_GUID \
        --output-dir    ./packages

    # Export only (download package, import later)
    python mstr_package_migrator.py \
        --source-host   https://ONPREM-MSTR/MicroStrategyLibrary \
        --source-user   Administrator --source-pass OldPass \
        --project-id    YOUR_PROJECT_GUID \
        --mode          export-only --output-dir ./packages

    # Import only (upload a previously downloaded package)
    python mstr_package_migrator.py \
        --target-host   https://CLOUD-MSTR/MicroStrategyLibrary \
        --target-user   Administrator --target-pass NewPass \
        --package-file  ./packages/project_package.mmp \
        --mode          import-only

  REQUIREMENTS: Python 3.8+, pip install requests

  AUTHOR: MicroStrategy Admin Automation Toolkit | VERSION: 2.0
================================================================================
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except ImportError:
    print("[ERROR] pip install requests")
    sys.exit(1)


class MSTRClient:
    def __init__(self, base_url: str, verify_ssl: bool = True):
        self.api  = base_url.rstrip("/") + "/api"
        self.verify = verify_ssl
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.token = None

    def _h(self, extra: Dict = None, project_id: str = None) -> Dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:       h["X-MSTR-AuthToken"] = self.token
        if project_id:       h["X-MSTR-ProjectID"] = project_id
        if extra:            h.update(extra)
        return h

    def login(self, user: str, pw: str, mode: int = 1) -> bool:
        r = self.session.post(f"{self.api}/auth/login",
            json={"username": user, "password": pw, "loginMode": mode, "applicationType": 35},
            headers={"Content-Type": "application/json"}, timeout=30)
        if r.status_code == 204:
            self.token = r.headers.get("X-MSTR-AuthToken")
            return True
        print(f"  [LOGIN FAIL] {r.status_code}: {r.text[:200]}")
        return False

    def logout(self):
        try:
            self.session.post(f"{self.api}/auth/logout", headers=self._h(), timeout=10)
        except Exception:
            pass

    def get(self, path: str, params=None, project_id: str = None) -> Optional[Any]:
        try:
            r = self.session.get(f"{self.api}/{path.lstrip('/')}",
                headers=self._h(project_id=project_id), params=params, timeout=120)
            return r.json() if r.status_code == 200 else None
        except Exception as e:
            print(f"  [GET ERROR] {path}: {e}")
            return None

    def post(self, path: str, payload: Any = None, project_id: str = None,
             timeout: int = 120) -> Tuple[int, Any]:
        try:
            r = self.session.post(f"{self.api}/{path.lstrip('/')}",
                headers=self._h(project_id=project_id),
                json=payload, timeout=timeout)
            try:    body = r.json()
            except: body = r.text
            return r.status_code, body
        except Exception as e:
            return 0, str(e)

    def put(self, path: str, payload: Any = None, project_id: str = None,
            timeout: int = 300) -> Tuple[int, Any]:
        try:
            r = self.session.put(f"{self.api}/{path.lstrip('/')}",
                headers=self._h(project_id=project_id),
                json=payload, timeout=timeout)
            try:    body = r.json()
            except: body = r.text
            return r.status_code, body
        except Exception as e:
            return 0, str(e)

    def download_binary(self, path: str, out_path: str) -> bool:
        """Download a binary file (package .mmp) from the IS."""
        try:
            h = self._h()
            h["Accept"] = "application/octet-stream"
            r = self.session.get(f"{self.api}/{path.lstrip('/')}",
                headers=h, stream=True, timeout=300)
            if r.status_code == 200:
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
            print(f"  [DOWNLOAD FAIL] HTTP {r.status_code}: {r.text[:200]}")
            return False
        except Exception as e:
            print(f"  [DOWNLOAD ERROR] {e}")
            return False

    def upload_binary(self, path: str, file_path: str) -> Tuple[int, Any]:
        """Upload a binary .mmp package to the IS."""
        try:
            h = {"X-MSTR-AuthToken": self.token, "Accept": "application/json"}
            with open(file_path, "rb") as f:
                r = self.session.post(f"{self.api}/{path.lstrip('/')}",
                    headers=h, files={"file": f}, timeout=300)
            try:    body = r.json()
            except: body = r.text
            return r.status_code, body
        except Exception as e:
            return 0, str(e)


# ─────────────────────────────────────────────────────────────
# PACKAGE EXPORT (source IS)
# ─────────────────────────────────────────────────────────────

def export_project_package(client: MSTRClient, project_id: str,
                            output_dir: str, project_name: str = "") -> Optional[str]:
    """
    Create and download a full project migration package from the source IS.
    Uses POST /api/packages + GET /api/packages/{id}/binary
    Returns the path to the downloaded .mmp file, or None on failure.
    """
    name = project_name or f"migration_{project_id[:8]}"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_name = f"{name}_{timestamp}"

    print(f"\n  Creating package for project: {project_name or project_id}")

    # Step 1: Create the package definition
    # type "project" exports the entire project content
    payload = {
        "name": package_name,
        "type": "project",
        "settings": {
            "updateSchema":              ["recal_table_logical_size"],
            "aclOnReplacingObjects":     "replace",
            "aclOnNewObjects":           ["keep_acl_as_source_object"],
            "defaultAction":             "replace",
            "updateOnly":                False,
            "usePrimaryKeyWhereAvailable": True,
        },
        "content": [
            {
                "id":    project_id,
                "type":  32,           # 32 = Project / Schema object
                "action": "replace",
                "includeDependents": True,
            }
        ]
    }

    status, body = client.post("/packages", payload, project_id=project_id, timeout=300)
    if status not in (200, 201):
        print(f"  [ERROR] Package creation failed: HTTP {status} — {str(body)[:300]}")
        return None

    pkg_id = body.get("id", "") if isinstance(body, dict) else ""
    if not pkg_id:
        print(f"  [ERROR] No package ID returned: {body}")
        return None

    print(f"  Package created — ID: {pkg_id}")

    # Step 2: Wait for package to be ready
    print("  Waiting for package to build...", end="", flush=True)
    for attempt in range(60):  # Up to 5 minutes
        time.sleep(5)
        status_data = client.get(f"/packages/{pkg_id}", project_id=project_id)
        if status_data:
            pkg_status = status_data.get("status", "").lower()
            if pkg_status in ("ready", "created"):
                print(" READY")
                break
            elif pkg_status in ("error", "failed"):
                print(f" FAILED: {status_data.get('statusMessage', '')}")
                return None
            else:
                print(".", end="", flush=True)
    else:
        print(" TIMEOUT — package took too long to build")
        return None

    # Step 3: Download the binary
    out_path = os.path.join(output_dir, f"{package_name}.mmp")
    print(f"  Downloading package → {os.path.basename(out_path)}")
    success = client.download_binary(f"/packages/{pkg_id}/binary", out_path)
    if not success:
        return None

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"  Downloaded: {size_mb:.1f} MB")

    # Step 4: Delete the temporary package on server (cleanup)
    client.session.delete(f"{client.api}/packages/{pkg_id}",
                          headers=client._h(project_id=project_id), timeout=30)

    return out_path


# ─────────────────────────────────────────────────────────────
# PACKAGE IMPORT (target IS)
# ─────────────────────────────────────────────────────────────

def import_package_to_cloud(client: MSTRClient, package_file: str,
                             target_project_id: str = None) -> bool:
    """
    Upload and import an .mmp package to the target (cloud) IS.
    Uses POST /api/packages/migrations to start the import.
    Returns True on success.
    """
    print(f"\n  Uploading package to cloud IS: {os.path.basename(package_file)}")

    # Step 1: Upload the binary package
    status, body = client.upload_binary("/packages/binary", package_file)
    if status not in (200, 201):
        print(f"  [ERROR] Upload failed: HTTP {status} — {str(body)[:300]}")
        return False

    pkg_id = body.get("id", "") if isinstance(body, dict) else ""
    if not pkg_id:
        print(f"  [ERROR] No package ID from upload: {body}")
        return False

    print(f"  Uploaded — package ID: {pkg_id}")

    # Step 2: Trigger the import / migration
    migration_payload = {
        "packageInfo": {"id": pkg_id},
        "importSettings": {
            "defaultAction":         "replace",
            "updateSchema":          ["recal_table_logical_size"],
            "aclOnReplacingObjects": "replace",
            "aclOnNewObjects":       ["keep_acl_as_source_object"],
        }
    }
    if target_project_id:
        migration_payload["projectId"] = target_project_id

    print("  Starting migration import on cloud IS...")
    status, body = client.post("/packages/migrations", migration_payload,
                               project_id=target_project_id, timeout=600)

    if status not in (200, 201, 202):
        print(f"  [ERROR] Migration start failed: HTTP {status} — {str(body)[:300]}")
        return False

    migration_id = body.get("id", "") if isinstance(body, dict) else ""
    print(f"  Migration started — ID: {migration_id}")

    # Step 3: Poll for completion
    print("  Waiting for import to complete...", end="", flush=True)
    for attempt in range(120):  # Up to 10 minutes
        time.sleep(5)
        result = client.get(f"/packages/migrations/{migration_id}")
        if result:
            mig_status = result.get("status", "").lower()
            if mig_status in ("completed", "success", "successful"):
                print(" COMPLETE")
                return True
            elif mig_status in ("failed", "error"):
                error_msg = result.get("statusMessage", result.get("message", ""))
                print(f" FAILED: {error_msg}")
                return False
            else:
                print(".", end="", flush=True)
        else:
            print(".", end="", flush=True)

    print(" TIMEOUT — check migration status manually in CMC admin")
    return False


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run(args):
    os.makedirs(args.output_dir, exist_ok=True)
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []

    print("=" * 64)
    print("  MicroStrategy Package Migrator v2.0")
    print(f"  Mode    : {args.mode}")
    print(f"  Started : {report_time}")
    print("=" * 64)

    # ── EXPORT PHASE ─────────────────────────────────────────
    downloaded_packages = []
    if args.mode in ("full", "export-only"):
        if not args.source_host:
            print("[ERROR] --source-host required for export modes")
            sys.exit(1)

        src = MSTRClient(args.source_host, verify_ssl=not args.no_ssl_verify)
        print(f"\n  Connecting to source IS: {args.source_host}")
        if not src.login(args.source_user, args.source_pass, int(args.login_mode)):
            sys.exit(1)

        try:
            # Get projects to migrate
            projects_raw = src.get("/projects") or []
            projects = projects_raw if isinstance(projects_raw, list) else []

            if args.project_id:
                projects = [p for p in projects if p.get("id") == args.project_id]
                if not projects:
                    print(f"  [ERROR] Project {args.project_id} not found on source IS")
                    sys.exit(1)
            elif not args.all_projects:
                print(f"  Found {len(projects)} projects. Use --all-projects to migrate all,")
                print(f"  or --project-id to specify one.")
                for i, p in enumerate(projects):
                    print(f"    [{i+1}] {p.get('name', '')}  ({p.get('id', '')})")
                sys.exit(0)

            for proj in projects:
                pid   = proj.get("id", "")
                pname = proj.get("name", "").replace(" ", "_")
                pkg_path = export_project_package(src, pid, args.output_dir, pname)
                if pkg_path:
                    downloaded_packages.append({"project": pname, "file": pkg_path})
                    results.append({"project": pname, "export": "PASS", "import": ""})
                else:
                    results.append({"project": pname, "export": "FAIL", "import": ""})
        finally:
            src.logout()

    # ── IMPORT PHASE ─────────────────────────────────────────
    if args.mode in ("full", "import-only"):
        if not args.target_host:
            print("[ERROR] --target-host required for import modes")
            sys.exit(1)

        tgt = MSTRClient(args.target_host, verify_ssl=not args.no_ssl_verify)
        print(f"\n  Connecting to target IS (cloud): {args.target_host}")
        if not tgt.login(args.target_user, args.target_pass, int(args.login_mode)):
            sys.exit(1)

        # Collect package files to import
        import_files = []
        if args.mode == "import-only" and args.package_file:
            import_files = [{"project": Path(args.package_file).stem, "file": args.package_file}]
        elif args.mode == "full":
            import_files = downloaded_packages

        try:
            for pkg in import_files:
                print(f"\n  Importing: {pkg['project']}")
                ok = import_package_to_cloud(tgt, pkg["file"])
                # Update results
                for r in results:
                    if r["project"] == pkg["project"]:
                        r["import"] = "PASS" if ok else "FAIL"
                        break
                else:
                    results.append({"project": pkg["project"],
                                    "export": "N/A",
                                    "import": "PASS" if ok else "FAIL"})
        finally:
            tgt.logout()

    # ── WRITE RESULTS ────────────────────────────────────────
    print("\n" + "=" * 64)
    print(f"  {'Project':<40} {'Export':>8} {'Import':>8}")
    print(f"  {'-'*60}")
    for r in results:
        exp_icon = "✓" if r["export"] == "PASS" else "✗" if r["export"] == "FAIL" else " "
        imp_icon = "✓" if r["import"] == "PASS" else "✗" if r["import"] == "FAIL" else "-"
        print(f"  {exp_icon} {r['project']:<40} {r['export']:>8} {imp_icon} {r['import']:>7}")

    pass_count = sum(1 for r in results if r.get("import") == "PASS")
    fail_count = sum(1 for r in results if r.get("import") == "FAIL")
    print(f"\n  Packages exported : {len(downloaded_packages)}")
    print(f"  Imports PASS      : {pass_count}")
    print(f"  Imports FAIL      : {fail_count}")
    if fail_count == 0 and results:
        print("  [PASS] All packages migrated successfully.")
    print("=" * 64)


def main():
    parser = argparse.ArgumentParser(
        description="Migrate MSTR projects via REST API packages — no GUI, no shell access.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source-host",  default=None)
    parser.add_argument("--source-user",  default=None)
    parser.add_argument("--source-pass",  default=None)
    parser.add_argument("--target-host",  default=None)
    parser.add_argument("--target-user",  default=None)
    parser.add_argument("--target-pass",  default=None)
    parser.add_argument("--project-id",   default=None, help="Migrate a single project by GUID")
    parser.add_argument("--all-projects", action="store_true", help="Migrate all projects")
    parser.add_argument("--package-file", default=None, help="Path to .mmp file (import-only mode)")
    parser.add_argument("--mode", default="full",
                        choices=["full", "export-only", "import-only"],
                        help="full=export+import | export-only | import-only")
    parser.add_argument("--login-mode", default="1", choices=["1", "4", "8", "16", "64"])
    parser.add_argument("--no-ssl-verify", action="store_true")
    parser.add_argument("--output-dir", default="./packages")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
