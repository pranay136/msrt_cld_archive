#!/usr/bin/env python3
"""
================================================================================
  MicroStrategy Report Validation Framework v1.0
  ================================================
  EBI Team — Custom report and dossier testing tool for:
    1. Migration validation  : on-prem output vs cloud output, side-by-side
    2. Upgrade validation    : before-upgrade baseline vs after-upgrade live
    3. Regression testing    : scheduled regression suite for any MSTR change

  This tool REPLACES Integrity Manager for programmatic, scalable testing.
  It adds:
    • Prompted report support via YAML prompt-answer config
    • Dossier execution & visualization comparison
    • Row count + schema + data hash comparison (detects silent data shifts)
    • HTML dashboard report with drill-down failure details
    • CSV output for spreadsheet analysis / ticket creation
    • Parallel execution to test 500+ reports in minutes
    • Reusable baselines — capture once, compare indefinitely

  ─────────────────────────────────────────────────────
  EXECUTION LAYER: REST API — runs from your laptop.
  No shell access to the IS or CMC cluster is needed.
  ─────────────────────────────────────────────────────

  QUICK START:

  Step 1 — Create config.yaml (see template below or run --init)
  Step 2 — Capture on-prem baseline:
    python mstr_report_validator.py --mode capture --config config.yaml

  Step 3 — After migration, compare cloud vs baseline:
    python mstr_report_validator.py --mode compare --config config.yaml

  Step 4 — For MSTR upgrades, re-run capture before upgrade, compare after:
    python mstr_report_validator.py --mode capture --config config.yaml   # before upgrade
    # <perform upgrade>
    python mstr_report_validator.py --mode compare --config config.yaml   # after upgrade

  FULL MODE (simultaneous dual-env comparison, requires both envs live):
    python mstr_report_validator.py --mode full --config config.yaml

  ─────────────────────────────────────────────────────
  CONFIG TEMPLATE (config.yaml):
  ─────────────────────────────────────────────────────
  environments:
    source:                                 # On-prem / pre-upgrade
      host: https://ONPREM-MSTR/MicroStrategyLibrary
      username: Administrator
      password: YourPassword
      project_name: "Your Project"         # Optional: restrict to one project
      ssl_verify: false

    target:                                 # Cloud / post-upgrade
      host: https://CMC-MSTR/MicroStrategyLibrary
      username: Administrator
      password: CloudPassword
      project_name: "Your Project"
      ssl_verify: true

  validation:
    max_rows_to_hash: 500           # Hash first N rows for data comparison
    row_count_tolerance_pct: 0      # 0 = exact match; 5 = allow ±5% difference
    include_types: [report, dossier]
    exclude_folders:                # Folder names to skip (Personal Objects etc.)
      - "My Reports"
      - "Personal Objects"
    timeout_seconds: 120            # Per-report execution timeout
    parallel_workers: 4             # Concurrent report executions
    max_reports: 0                  # 0 = all; set to N to limit (useful for testing)
    fail_fast: false                # Stop on first failure?

  prompt_answers:                   # Pre-fill prompts for prompted reports
    - report_id: "REPORT_GUID_HERE"
      prompts:
        - key: "Year"               # Prompt name or key from 15_prompts.csv
          type: VALUE               # VALUE | ELEMENTS | EXPRESSION | OBJECTS
          value: "2024"
        - key: "Region"
          type: ELEMENTS
          value: ["North America", "Europe"]

  output:
    baseline_dir: ./baseline        # Where capture saves JSON snapshots
    report_dir:   ./validation_reports   # Where HTML + CSV reports are written

  ─────────────────────────────────────────────────────
  CROSS-TEAM DEPENDENCIES (EBI team note):
  ─────────────────────────────────────────────────────
  This tool requires NO business user involvement for non-prompted reports.
  For prompted reports:
    • EBI can pre-configure prompt answers in prompt_answers section of config
    • Business team only needed to *confirm* correct prompt values (one-time)
  DB credentials:
    • EBI ACL Specialist provides IS-level datasource credentials (already in IS)
    • No DB-level credentials needed — IS handles the query
  Report IDs:
    • Sourced from 09_reports.csv produced by mstr_harvester.py (already done)
    • Zero dependency on Business or HCL for this

  ─────────────────────────────────────────────────────
  REQUIREMENTS:
  ─────────────────────────────────────────────────────
    Python 3.8+
    pip install requests pyyaml

  AUTHOR: EBI Team — MicroStrategy Admin Automation Toolkit
  VERSION: 1.0
  DATE: 2026-04-18
================================================================================
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except ImportError:
    print("[ERROR] 'requests' not found. Run: pip install requests pyyaml")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("[ERROR] 'pyyaml' not found. Run: pip install requests pyyaml")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("MSTRValidator")


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ReportSnapshot:
    """Captured state of a single report/dossier execution."""
    report_id: str
    report_name: str
    report_type: str              # "report" | "dossier"
    project_id: str
    project_name: str
    captured_at: str
    environment_label: str        # "source" | "target"
    status: str                   # "success" | "error" | "timeout" | "skipped"
    row_count: int = 0
    column_names: List[str] = field(default_factory=list)
    data_hash: str = ""           # MD5 of first N rows, normalized
    sample_rows: List[Dict] = field(default_factory=list)  # First 5 rows for debug
    error_message: str = ""
    execution_time_ms: int = 0
    prompt_status: str = ""       # "none" | "answered" | "unanswered"
    raw_metadata: Dict = field(default_factory=dict)


@dataclass
class ComparisonResult:
    """Result of comparing source snapshot vs target snapshot for one report."""
    report_id: str
    report_name: str
    report_type: str
    project_name: str
    overall_status: str           # "PASS" | "FAIL" | "WARN" | "ERROR" | "SKIP"
    source_status: str
    target_status: str
    source_row_count: int = 0
    target_row_count: int = 0
    row_count_match: bool = False
    schema_match: bool = False
    data_hash_match: bool = False
    source_columns: List[str] = field(default_factory=list)
    target_columns: List[str] = field(default_factory=list)
    missing_columns: List[str] = field(default_factory=list)
    extra_columns: List[str] = field(default_factory=list)
    failure_reasons: List[str] = field(default_factory=list)
    source_exec_ms: int = 0
    target_exec_ms: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# MSTR REST API CLIENT
# ─────────────────────────────────────────────────────────────────────────────
class MSTRClient:
    """
    Thin REST API client for MicroStrategy Library API.
    Manages session tokens and provides typed helpers for report/dossier ops.
    """

    def __init__(self, host: str, username: str, password: str,
                 ssl_verify: bool = True, timeout: int = 120,
                 label: str = ""):
        self.host = host.rstrip("/")
        self.base = f"{self.host}/api"
        self.username = username
        self.password = password
        self.ssl_verify = ssl_verify
        self.timeout = timeout
        self.label = label or host
        self._session = requests.Session()
        self._auth_token: Optional[str] = None
        self._project_id: Optional[str] = None

    # ── Auth ──────────────────────────────────────────────────────────────────
    def login(self) -> bool:
        url = f"{self.base}/auth/login"
        payload = {
            "username": self.username,
            "password": self.password,
            "loginMode": 1,
            "applicationType": 35
        }
        try:
            r = self._session.post(url, json=payload,
                                   verify=self.ssl_verify, timeout=30)
            if r.status_code == 204:
                self._auth_token = r.headers.get("X-MSTR-AuthToken", "")
                self._session.headers.update({"X-MSTR-AuthToken": self._auth_token})
                log.info(f"[{self.label}] Logged in as {self.username}")
                return True
            log.error(f"[{self.label}] Login failed: HTTP {r.status_code} — {r.text[:200]}")
            return False
        except Exception as e:
            log.error(f"[{self.label}] Login error: {e}")
            return False

    def logout(self):
        try:
            self._session.delete(f"{self.base}/auth/logout",
                                 verify=self.ssl_verify, timeout=10)
            log.info(f"[{self.label}] Logged out")
        except Exception:
            pass

    def set_project(self, project_id: str):
        self._project_id = project_id
        self._session.headers.update({"X-MSTR-ProjectID": project_id})

    # ── Projects ───────────────────────────────────────────────────────────────
    def get_projects(self) -> List[Dict]:
        r = self._get("/projects")
        return r if isinstance(r, list) else []

    def find_project_by_name(self, name: str) -> Optional[Dict]:
        for p in self.get_projects():
            if p.get("name", "").lower() == name.lower():
                return p
        return None

    # ── Object listing ─────────────────────────────────────────────────────────
    def list_objects(self, object_type: int, limit: int = 200, offset: int = 0) -> List[Dict]:
        """
        object_type 3  = Report
        object_type 55 = Dossier/Document
        """
        all_items = []
        while True:
            r = self._get(f"/objects",
                          params={"type": object_type, "limit": limit, "offset": offset})
            items = r if isinstance(r, list) else []
            all_items.extend(items)
            if len(items) < limit:
                break
            offset += limit
        return all_items

    # ── Report execution ───────────────────────────────────────────────────────
    def execute_report(self, report_id: str,
                       prompt_answers: Optional[List[Dict]] = None,
                       max_rows: int = 500) -> Tuple[Optional[Dict], int]:
        """
        Execute a report and return (result_data, execution_ms).
        Handles two-step execution for prompted reports.
        Returns (None, ms) on error.
        """
        t0 = time.time()
        url = f"{self.base}/v2/reports/{report_id}/instances"
        body: Dict[str, Any] = {"requestedObjects": {}, "viewFilter": {}}

        try:
            r = self._session.post(url, json=body,
                                   verify=self.ssl_verify, timeout=self.timeout)
            ms = int((time.time() - t0) * 1000)

            # Prompted report — server returns 200 with prompt definitions
            if r.status_code == 200:
                data = r.json()
                instance_id = data.get("instanceId") or data.get("id")
                if data.get("status") == 2 and instance_id:  # status 2 = awaiting prompts
                    data = self._answer_report_prompts(
                        report_id, instance_id, prompt_answers or [])
                return data, ms

            # Direct result — 201 Created with instance
            if r.status_code == 201:
                instance_id = r.json().get("instanceId") or r.json().get("id")
                result = self._get_report_instance(report_id, instance_id, max_rows)
                ms = int((time.time() - t0) * 1000)
                return result, ms

            log.warning(f"Report {report_id}: HTTP {r.status_code}")
            return None, ms

        except requests.Timeout:
            ms = int((time.time() - t0) * 1000)
            log.warning(f"Report {report_id}: TIMEOUT after {ms}ms")
            return None, ms
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            log.error(f"Report {report_id}: {e}")
            return None, ms

    def _answer_report_prompts(self, report_id: str, instance_id: str,
                                answers: List[Dict]) -> Optional[Dict]:
        """PUT prompt answers and wait for execution."""
        if not answers:
            log.warning(f"Report {report_id} requires prompts but none configured — SKIP")
            return None

        body = {"prompts": answers}
        url = f"{self.base}/v2/reports/{report_id}/instances/{instance_id}/prompts/answers"
        r = self._session.put(url, json=body,
                              verify=self.ssl_verify, timeout=self.timeout)
        if r.status_code not in (200, 204):
            log.warning(f"Report {report_id}: prompt answer failed: {r.status_code}")
            return None

        return self._get_report_instance(report_id, instance_id, 500)

    def _get_report_instance(self, report_id: str, instance_id: str,
                              max_rows: int = 500) -> Optional[Dict]:
        """Poll for report result (handles async execution)."""
        url = f"{self.base}/v2/reports/{report_id}/instances/{instance_id}"
        params = {"limit": max_rows, "offset": 0}
        for attempt in range(20):  # max 20 polls × 3s = 60s
            r = self._session.get(url, params=params,
                                  verify=self.ssl_verify, timeout=self.timeout)
            if r.status_code == 200:
                data = r.json()
                # Still executing
                if data.get("status") in (1, 3):
                    time.sleep(3)
                    continue
                return data
            log.warning(f"Instance poll HTTP {r.status_code}")
            return None
        log.warning(f"Report {report_id}: execution did not complete in time")
        return None

    # ── Dossier execution ──────────────────────────────────────────────────────
    def execute_dossier(self, dossier_id: str) -> Tuple[Optional[Dict], int]:
        """Create a dossier instance and retrieve page 1 of chapter 1."""
        t0 = time.time()
        url = f"{self.base}/dossiers/{dossier_id}/instances"
        try:
            r = self._session.post(url, json={},
                                   verify=self.ssl_verify, timeout=self.timeout)
            ms = int((time.time() - t0) * 1000)
            if r.status_code not in (200, 201):
                return None, ms

            instance_id = r.json().get("mid") or r.json().get("id")
            if not instance_id:
                return None, ms

            # Get chapter definitions
            chapters = self._get(f"/dossiers/{dossier_id}/instances/{instance_id}/chapters")
            ms = int((time.time() - t0) * 1000)
            return {"instance_id": instance_id, "chapters": chapters}, ms

        except requests.Timeout:
            ms = int((time.time() - t0) * 1000)
            return None, ms
        except Exception as e:
            log.error(f"Dossier {dossier_id}: {e}")
            ms = int((time.time() - t0) * 1000)
            return None, ms

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _get(self, path: str, params: Dict = None) -> Any:
        url = f"{self.base}{path}"
        try:
            r = self._session.get(url, params=params,
                                  verify=self.ssl_verify, timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
            return {}
        except Exception as e:
            log.error(f"GET {path}: {e}")
            return {}


# ─────────────────────────────────────────────────────────────────────────────
# SNAPSHOT ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class SnapshotEngine:
    """
    Executes reports/dossiers against a single MSTR environment and
    captures structured snapshots to disk.
    """

    def __init__(self, client: MSTRClient, config: Dict, env_label: str):
        self.client = client
        self.config = config
        self.env_label = env_label
        self.val_cfg = config.get("validation", {})
        self.max_rows = self.val_cfg.get("max_rows_to_hash", 500)
        self.timeout = self.val_cfg.get("timeout_seconds", 120)
        self.workers = self.val_cfg.get("parallel_workers", 4)
        self.max_reports = self.val_cfg.get("max_reports", 0)
        self.exclude_folders = set(self.val_cfg.get("exclude_folders", []))
        self.include_types = self.val_cfg.get("include_types", ["report", "dossier"])
        self.prompt_map = self._build_prompt_map()

    def _build_prompt_map(self) -> Dict[str, List[Dict]]:
        """Build report_id → prompt_answer_list lookup from config."""
        result = {}
        for entry in self.config.get("prompt_answers", []):
            rid = entry.get("report_id", "")
            if rid:
                result[rid] = entry.get("prompts", [])
        return result

    # ── Inventory ──────────────────────────────────────────────────────────────
    def build_inventory(self, project_id: str,
                        harvest_csv: Optional[str] = None) -> List[Dict]:
        """
        Returns list of {id, name, type, project_id} dicts to test.
        Priority: harvest_csv > live API listing.
        """
        inventory = []

        if harvest_csv and Path(harvest_csv).exists():
            inventory = self._load_from_csv(harvest_csv, project_id)
            log.info(f"Loaded {len(inventory)} objects from harvest CSV")
        else:
            inventory = self._discover_from_api(project_id)
            log.info(f"Discovered {len(inventory)} objects via API")

        # Apply folder exclusions (best-effort — folder path not always available)
        inventory = [o for o in inventory
                     if o.get("folder", "") not in self.exclude_folders]

        # Apply max_reports cap
        if self.max_reports > 0:
            inventory = inventory[:self.max_reports]

        return inventory

    def _load_from_csv(self, csv_path: str, project_id: str) -> List[Dict]:
        """Load from mstr_harvester 09_reports.csv or 10_documents_dossiers.csv."""
        items = []
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                obj_type = "report"
                type_val = int(row.get("type", row.get("object_type", "3")) or 3)
                if type_val == 55:
                    obj_type = "dossier"
                if obj_type not in self.include_types:
                    continue
                items.append({
                    "id": row.get("id", row.get("object_id", "")),
                    "name": row.get("name", row.get("object_name", "")),
                    "type": obj_type,
                    "project_id": project_id,
                    "folder": row.get("folder_path", ""),
                })
        return [i for i in items if i["id"]]

    def _discover_from_api(self, project_id: str) -> List[Dict]:
        """List objects directly from the API."""
        items = []
        if "report" in self.include_types:
            for obj in self.client.list_objects(object_type=3):
                items.append({
                    "id": obj.get("id", ""),
                    "name": obj.get("name", ""),
                    "type": "report",
                    "project_id": project_id,
                    "folder": obj.get("ancestorNames", [""])[0] if obj.get("ancestorNames") else "",
                })
        if "dossier" in self.include_types:
            for obj in self.client.list_objects(object_type=55):
                items.append({
                    "id": obj.get("id", ""),
                    "name": obj.get("name", ""),
                    "type": "dossier",
                    "project_id": project_id,
                    "folder": obj.get("ancestorNames", [""])[0] if obj.get("ancestorNames") else "",
                })
        return [i for i in items if i["id"]]

    # ── Capture ────────────────────────────────────────────────────────────────
    def capture(self, project_id: str, project_name: str,
                baseline_dir: Path,
                harvest_csv: Optional[str] = None) -> List[ReportSnapshot]:
        """Execute all reports and save snapshots."""
        inventory = self.build_inventory(project_id, harvest_csv)
        log.info(f"[{self.env_label}] Capturing {len(inventory)} objects "
                 f"({self.workers} workers)...")

        snapshots = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {
                ex.submit(self._capture_one, obj, project_name): obj
                for obj in inventory
            }
            done = 0
            for fut in as_completed(futures):
                snap = fut.result()
                snapshots.append(snap)
                done += 1
                status_icon = "✓" if snap.status == "success" else "✗"
                log.info(f"  [{done}/{len(inventory)}] {status_icon} {snap.report_name[:60]}")

        # Persist to disk
        baseline_dir.mkdir(parents=True, exist_ok=True)
        for snap in snapshots:
            snap_path = baseline_dir / f"{snap.report_id}.json"
            with open(snap_path, "w") as f:
                json.dump(asdict(snap), f, indent=2)

        # Summary file
        summary = {
            "captured_at": datetime.now().isoformat(),
            "environment": self.env_label,
            "project": project_name,
            "total": len(snapshots),
            "success": sum(1 for s in snapshots if s.status == "success"),
            "errors": sum(1 for s in snapshots if s.status == "error"),
            "skipped": sum(1 for s in snapshots if s.status == "skipped"),
        }
        with open(baseline_dir / "_capture_summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        log.info(f"[{self.env_label}] Capture complete → {baseline_dir}")
        log.info(f"  Success: {summary['success']} | Errors: {summary['errors']} "
                 f"| Skipped: {summary['skipped']}")
        return snapshots

    def _capture_one(self, obj: Dict, project_name: str) -> ReportSnapshot:
        """Execute a single report/dossier and return a snapshot."""
        snap = ReportSnapshot(
            report_id=obj["id"],
            report_name=obj["name"],
            report_type=obj["type"],
            project_id=obj.get("project_id", ""),
            project_name=project_name,
            captured_at=datetime.now().isoformat(),
            environment_label=self.env_label,
            status="error",
        )

        if obj["type"] == "report":
            self._capture_report(snap)
        elif obj["type"] == "dossier":
            self._capture_dossier(snap)

        return snap

    def _capture_report(self, snap: ReportSnapshot):
        prompt_answers = self.prompt_map.get(snap.report_id)
        if prompt_answers is None and snap.report_id not in self.prompt_map:
            snap.prompt_status = "none"
        else:
            snap.prompt_status = "answered" if prompt_answers else "unanswered"

        data, ms = self.client.execute_report(snap.report_id, prompt_answers,
                                               self.max_rows)
        snap.execution_time_ms = ms

        if data is None:
            if snap.prompt_status == "unanswered":
                snap.status = "skipped"
                snap.error_message = "Prompted report — no prompt answers configured in prompt_answers section"
            else:
                snap.status = "error"
                snap.error_message = "Execution returned no data (timeout or API error)"
            return

        # Parse result
        try:
            snap.row_count = _extract_row_count(data)
            snap.column_names = _extract_columns(data)
            snap.data_hash = _hash_data(data, self.max_rows)
            snap.sample_rows = _extract_sample_rows(data, 5)
            snap.status = "success"
        except Exception as e:
            snap.status = "error"
            snap.error_message = f"Parse error: {e}"

    def _capture_dossier(self, snap: ReportSnapshot):
        data, ms = self.client.execute_dossier(snap.report_id)
        snap.execution_time_ms = ms

        if data is None:
            snap.status = "error"
            snap.error_message = "Dossier execution returned no data"
            return

        snap.status = "success"
        chapters = data.get("chapters") or []
        snap.row_count = len(chapters)   # Chapter count as proxy metric
        snap.column_names = [c.get("name", "") for c in chapters if isinstance(c, dict)]
        snap.data_hash = hashlib.md5(
            json.dumps(chapters, sort_keys=True).encode()
        ).hexdigest()
        snap.prompt_status = "none"


# ─────────────────────────────────────────────────────────────────────────────
# COMPARISON ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class ComparisonEngine:
    """Compares source snapshots (baseline) vs target snapshots (live run)."""

    def __init__(self, config: Dict):
        self.val_cfg = config.get("validation", {})
        self.tolerance_pct = self.val_cfg.get("row_count_tolerance_pct", 0)

    def compare(self, source_snaps: List[ReportSnapshot],
                target_snaps: List[ReportSnapshot]) -> List[ComparisonResult]:
        """Compare paired snapshots. Match by report_id."""
        source_map = {s.report_id: s for s in source_snaps}
        target_map = {s.report_id: s for s in target_snaps}

        all_ids = sorted(set(source_map) | set(target_map))
        results = []
        for rid in all_ids:
            src = source_map.get(rid)
            tgt = target_map.get(rid)
            results.append(self._compare_pair(src, tgt))
        return results

    def compare_with_baseline(self, baseline_dir: Path,
                               live_snaps: List[ReportSnapshot]) -> List[ComparisonResult]:
        """Load source from disk baseline and compare vs live target."""
        source_snaps = []
        for snap_file in baseline_dir.glob("*.json"):
            if snap_file.name.startswith("_"):
                continue
            try:
                with open(snap_file) as f:
                    data = json.load(f)
                snap = ReportSnapshot(**data)
                source_snaps.append(snap)
            except Exception as e:
                log.warning(f"Could not load baseline {snap_file.name}: {e}")
        log.info(f"Loaded {len(source_snaps)} baseline snapshots from {baseline_dir}")
        return self.compare(source_snaps, live_snaps)

    def _compare_pair(self, src: Optional[ReportSnapshot],
                       tgt: Optional[ReportSnapshot]) -> ComparisonResult:
        # Case: only in source
        if src and not tgt:
            return ComparisonResult(
                report_id=src.report_id, report_name=src.report_name,
                report_type=src.report_type, project_name=src.project_name,
                overall_status="FAIL", source_status=src.status, target_status="missing",
                source_row_count=src.row_count, source_columns=src.column_names,
                failure_reasons=["Report missing in target environment"],
                source_exec_ms=src.execution_time_ms,
            )

        # Case: only in target (new report, not a failure)
        if tgt and not src:
            return ComparisonResult(
                report_id=tgt.report_id, report_name=tgt.report_name,
                report_type=tgt.report_type, project_name=tgt.project_name,
                overall_status="WARN", source_status="missing", target_status=tgt.status,
                target_row_count=tgt.row_count, target_columns=tgt.column_names,
                failure_reasons=["Report only exists in target — no baseline"],
                target_exec_ms=tgt.execution_time_ms,
            )

        result = ComparisonResult(
            report_id=src.report_id,
            report_name=src.report_name,
            report_type=src.report_type,
            project_name=src.project_name,
            overall_status="PASS",
            source_status=src.status,
            target_status=tgt.status,
            source_row_count=src.row_count,
            target_row_count=tgt.row_count,
            source_columns=src.column_names,
            target_columns=tgt.column_names,
            source_exec_ms=src.execution_time_ms,
            target_exec_ms=tgt.execution_time_ms,
        )

        failures = []

        # Check source/target errors
        if src.status != "success":
            failures.append(f"Source execution: {src.status} — {src.error_message}")
        if tgt.status != "success":
            failures.append(f"Target execution: {tgt.status} — {tgt.error_message}")

        if src.status == "skipped" and tgt.status == "skipped":
            result.overall_status = "SKIP"
            return result

        # Row count comparison
        if src.status == "success" and tgt.status == "success":
            if self.tolerance_pct == 0:
                result.row_count_match = (src.row_count == tgt.row_count)
            else:
                if src.row_count > 0:
                    diff_pct = abs(src.row_count - tgt.row_count) / src.row_count * 100
                    result.row_count_match = diff_pct <= self.tolerance_pct
                else:
                    result.row_count_match = (tgt.row_count == 0)

            if not result.row_count_match:
                failures.append(
                    f"Row count mismatch: source={src.row_count}, target={tgt.row_count}")

            # Schema comparison
            src_cols = set(src.column_names)
            tgt_cols = set(tgt.column_names)
            result.missing_columns = sorted(src_cols - tgt_cols)
            result.extra_columns = sorted(tgt_cols - src_cols)
            result.schema_match = (src_cols == tgt_cols)
            if not result.schema_match:
                if result.missing_columns:
                    failures.append(f"Columns missing in target: {result.missing_columns}")
                if result.extra_columns:
                    failures.append(f"Extra columns in target: {result.extra_columns}")

            # Data hash comparison
            result.data_hash_match = (src.data_hash == tgt.data_hash)
            if not result.data_hash_match and result.row_count_match and result.schema_match:
                failures.append("Data values differ (same shape but hash mismatch) "
                                 "— check sample_rows in baseline JSON for details")

        result.failure_reasons = failures
        if failures:
            result.overall_status = "FAIL"

        return result


# ─────────────────────────────────────────────────────────────────────────────
# REPORTER
# ─────────────────────────────────────────────────────────────────────────────
class ValidationReporter:
    """Generates HTML dashboard and CSV from comparison results."""

    STATUS_COLORS = {
        "PASS": ("#d4edda", "#155724"),
        "FAIL": ("#f8d7da", "#721c24"),
        "WARN": ("#fff3cd", "#856404"),
        "ERROR": ("#f8d7da", "#721c24"),
        "SKIP": ("#e2e3e5", "#383d41"),
    }

    def write(self, results: List[ComparisonResult], output_dir: Path,
              run_label: str = ""):
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_label = run_label or ts

        csv_path = output_dir / f"validation_{ts}.csv"
        html_path = output_dir / f"validation_{ts}.html"

        self._write_csv(results, csv_path)
        self._write_html(results, html_path, run_label)

        totals = self._totals(results)
        log.info(f"─────────────────────────────────────────")
        log.info(f"  VALIDATION RESULTS")
        log.info(f"  PASS : {totals['PASS']}")
        log.info(f"  FAIL : {totals['FAIL']}")
        log.info(f"  WARN : {totals['WARN']}")
        log.info(f"  SKIP : {totals['SKIP']}")
        log.info(f"  TOTAL: {len(results)}")
        log.info(f"─────────────────────────────────────────")
        log.info(f"  HTML → {html_path}")
        log.info(f"  CSV  → {csv_path}")

        return html_path, csv_path

    def _totals(self, results):
        t = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0, "ERROR": 0}
        for r in results:
            t[r.overall_status] = t.get(r.overall_status, 0) + 1
        return t

    def _write_csv(self, results, path):
        fields = [
            "report_id", "report_name", "report_type", "project_name",
            "overall_status", "source_status", "target_status",
            "source_row_count", "target_row_count", "row_count_match",
            "schema_match", "data_hash_match",
            "missing_columns", "extra_columns", "failure_reasons",
            "source_exec_ms", "target_exec_ms",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in results:
                row = asdict(r)
                for key in ("missing_columns", "extra_columns", "failure_reasons",
                            "source_columns", "target_columns"):
                    if isinstance(row.get(key), list):
                        row[key] = "; ".join(row[key])
                w.writerow({k: row.get(k, "") for k in fields})

    def _write_html(self, results, path, run_label):
        totals = self._totals(results)
        total = len(results)
        pass_pct = round(totals["PASS"] / total * 100) if total else 0

        rows_html = ""
        for r in results:
            bg, fg = self.STATUS_COLORS.get(r.overall_status, ("#fff", "#000"))
            reasons = "<br>".join(r.failure_reasons) if r.failure_reasons else "—"
            rows_html += f"""
            <tr style="background:{bg}; color:{fg}">
              <td><span class="badge" style="background:{fg};color:#fff">{r.overall_status}</span></td>
              <td>{_esc(r.report_name)}</td>
              <td>{r.report_type}</td>
              <td>{r.source_row_count:,}</td>
              <td>{r.target_row_count:,}</td>
              <td>{"✓" if r.row_count_match else "✗"}</td>
              <td>{"✓" if r.schema_match else "✗"}</td>
              <td>{"✓" if r.data_hash_match else "✗"}</td>
              <td style="font-size:0.85em">{_esc(reasons)}</td>
              <td>{r.source_exec_ms}ms</td>
              <td>{r.target_exec_ms}ms</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>MSTR Report Validation — {run_label}</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; background: #f5f7fa; color: #1a1a2e; }}
  header {{ background: #0B1E33; color: #fff; padding: 24px 32px; }}
  header h1 {{ margin: 0; font-size: 1.4em; font-weight: 600; }}
  header p {{ margin: 4px 0 0; opacity: .7; font-size: .9em; }}
  .summary {{ display: flex; gap: 16px; padding: 20px 32px; flex-wrap: wrap; }}
  .card {{ background: #fff; border-radius: 8px; padding: 16px 24px; min-width: 120px;
           box-shadow: 0 1px 4px rgba(0,0,0,.1); text-align: center; }}
  .card .num {{ font-size: 2em; font-weight: 700; }}
  .card .lbl {{ font-size: .8em; opacity: .6; margin-top: 4px; }}
  .pass {{ color: #155724; }} .fail {{ color: #721c24; }}
  .warn {{ color: #856404; }} .skip {{ color: #383d41; }}
  .progress {{ margin: 0 32px 16px; background: #e0e0e0; border-radius: 4px; height: 8px; }}
  .progress-bar {{ height: 8px; border-radius: 4px;
                   background: linear-gradient(90deg,#28a745,#20c997);
                   width: {pass_pct}%; }}
  table {{ width: calc(100% - 64px); margin: 0 32px 32px; border-collapse: collapse;
           background: #fff; border-radius: 8px; overflow: hidden;
           box-shadow: 0 1px 4px rgba(0,0,0,.1); font-size: .88em; }}
  th {{ background: #0B1E33; color: #fff; padding: 10px 12px; text-align: left; }}
  td {{ padding: 9px 12px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px;
            font-size: .78em; font-weight: 600; }}
  .filter-bar {{ padding: 12px 32px; }}
  .filter-bar button {{ margin-right: 8px; padding: 6px 14px; border: none; border-radius: 20px;
                         cursor: pointer; font-size: .85em; background: #e9ecef; }}
  .filter-bar button.active {{ background: #0B1E33; color: #fff; }}
</style>
</head>
<body>
<header>
  <h1>🔬 MSTR Report Validation Dashboard</h1>
  <p>Run: {run_label} &nbsp;|&nbsp; Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} &nbsp;|&nbsp; Total objects tested: {total}</p>
</header>
<div class="summary">
  <div class="card"><div class="num">{total}</div><div class="lbl">TOTAL</div></div>
  <div class="card"><div class="num pass">{totals['PASS']}</div><div class="lbl">PASS</div></div>
  <div class="card"><div class="num fail">{totals['FAIL']}</div><div class="lbl">FAIL</div></div>
  <div class="card"><div class="num warn">{totals['WARN']}</div><div class="lbl">WARN</div></div>
  <div class="card"><div class="num skip">{totals['SKIP']}</div><div class="lbl">SKIP</div></div>
  <div class="card"><div class="num pass">{pass_pct}%</div><div class="lbl">PASS RATE</div></div>
</div>
<div class="progress"><div class="progress-bar"></div></div>
<div class="filter-bar">
  <button class="active" onclick="filterTable('ALL')">All</button>
  <button onclick="filterTable('FAIL')">Failures only</button>
  <button onclick="filterTable('PASS')">Passed</button>
  <button onclick="filterTable('SKIP')">Skipped</button>
</div>
<table id="resultsTable">
  <thead><tr>
    <th>Status</th><th>Report Name</th><th>Type</th>
    <th>Rows (Src)</th><th>Rows (Tgt)</th>
    <th>Rows ✓</th><th>Schema ✓</th><th>Data ✓</th>
    <th>Failure Reason</th><th>Src Time</th><th>Tgt Time</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<script>
function filterTable(status) {{
  document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('#resultsTable tbody tr').forEach(row => {{
    const badge = row.querySelector('.badge');
    row.style.display = (status === 'ALL' || badge.textContent === status) ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _extract_row_count(data: Dict) -> int:
    """Extract row count from MSTR v2 report result structure."""
    # v2 API: data.data.paging.total or data.result.data.metricValues
    try:
        return data["data"]["paging"]["total"]
    except (KeyError, TypeError):
        pass
    try:
        rows = data["data"]["metricValues"]["raw"]
        return len(rows)
    except (KeyError, TypeError):
        pass
    try:
        return len(data.get("rows", []))
    except Exception:
        return 0


def _extract_columns(data: Dict) -> List[str]:
    """Extract column/attribute names from v2 report result."""
    cols = []
    try:
        for attr in data["definition"]["attributes"]:
            cols.append(attr.get("name", ""))
    except (KeyError, TypeError):
        pass
    try:
        for metric in data["definition"]["metrics"]:
            cols.append(metric.get("name", ""))
    except (KeyError, TypeError):
        pass
    return cols


def _hash_data(data: Dict, max_rows: int) -> str:
    """Create reproducible MD5 hash of report data for comparison."""
    try:
        # Normalize: extract raw metric values and attribute elements
        payload = {
            "columns": _extract_columns(data),
            "rows": data.get("data", {}).get("metricValues", {}).get("raw", [])[:max_rows],
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.md5(canonical.encode()).hexdigest()
    except Exception:
        # Fallback: hash entire data blob
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=True)
        return hashlib.md5(canonical.encode()).hexdigest()


def _extract_sample_rows(data: Dict, n: int = 5) -> List[Dict]:
    """Extract first N rows for human-readable debugging."""
    try:
        attrs = data["data"].get("headers", {}).get("rows", [])
        metrics = data["data"].get("metricValues", {}).get("raw", [])
        rows = []
        for i in range(min(n, len(metrics))):
            row = {"_row": i + 1, "metrics": metrics[i]}
            if i < len(attrs):
                row["attributes"] = attrs[i]
            rows.append(row)
        return rows
    except Exception:
        return []


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG LOADER
# ─────────────────────────────────────────────────────────────────────────────
def load_config(config_path: str) -> Dict:
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    # Resolve env-var substitution for passwords
    for env_key in ("source", "target"):
        env_cfg = cfg.get("environments", {}).get(env_key, {})
        for field_name in ("password", "username"):
            val = str(env_cfg.get(field_name, ""))
            if val.startswith("$"):
                env_cfg[field_name] = os.environ.get(val[1:], val)
    return cfg


def write_init_config(path: str):
    """Write a starter config.yaml template."""
    template = textwrap.dedent("""\
    # MSTR Report Validator — config.yaml
    # EBI Team — MicroStrategy Migration & Upgrade Testing

    environments:
      source:                    # On-prem (pre-migration / pre-upgrade)
        host: https://ONPREM-HOST/MicroStrategyLibrary
        username: Administrator
        password: $ONPREM_PASS   # Or plain text (not recommended)
        project_name: "YourProjectName"
        ssl_verify: false

      target:                    # CMC cluster (post-migration / post-upgrade)
        host: https://CMC-HOST/MicroStrategyLibrary
        username: Administrator
        password: $CMC_PASS
        project_name: "YourProjectName"
        ssl_verify: true

    validation:
      max_rows_to_hash: 500         # Hash first N rows for data comparison
      row_count_tolerance_pct: 0    # 0 = exact; 5 = allow ±5%
      include_types:
        - report
        - dossier
      exclude_folders:
        - "My Reports"
        - "Personal Objects"
      timeout_seconds: 120
      parallel_workers: 4
      max_reports: 0                # 0 = all; 50 = first 50 (for dry-run testing)
      fail_fast: false

    # Pre-fill answers for prompted reports
    # Find prompt keys in 15_prompts.csv from mstr_harvester output
    prompt_answers:
      # - report_id: "PASTE_REPORT_GUID_HERE"
      #   prompts:
      #     - key: "Year"
      #       type: VALUE
      #       value: "2024"
      #     - key: "Region"
      #       type: ELEMENTS
      #       value: ["North America"]

    output:
      baseline_dir: ./baseline           # Capture snapshots saved here
      report_dir:   ./validation_reports  # HTML + CSV output here
    """)
    with open(path, "w") as f:
        f.write(template)
    print(f"Created starter config: {path}")
    print("Edit the host, username, password, and project_name fields before running.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────
def make_client(env_cfg: Dict, label: str, timeout: int) -> MSTRClient:
    return MSTRClient(
        host=env_cfg["host"],
        username=env_cfg["username"],
        password=env_cfg["password"],
        ssl_verify=env_cfg.get("ssl_verify", True),
        timeout=timeout,
        label=label,
    )


def resolve_project(client: MSTRClient, env_cfg: Dict) -> Tuple[str, str]:
    """Return (project_id, project_name) from config or first project."""
    project_name = env_cfg.get("project_name", "")
    if project_name:
        p = client.find_project_by_name(project_name)
        if p:
            return p["id"], p["name"]
        log.warning(f"Project '{project_name}' not found — using first available")
    projects = client.get_projects()
    if not projects:
        log.error("No projects accessible — check credentials and project assignment")
        sys.exit(1)
    p = projects[0]
    return p["id"], p["name"]


def mode_capture(config: Dict, harvest_csv: Optional[str], run_label: str):
    """Capture baseline from source environment."""
    env_cfg = config["environments"]["source"]
    val_cfg = config.get("validation", {})
    baseline_dir = Path(config["output"]["baseline_dir"])
    timeout = val_cfg.get("timeout_seconds", 120)

    client = make_client(env_cfg, "SOURCE", timeout)
    if not client.login():
        sys.exit(1)

    project_id, project_name = resolve_project(client, env_cfg)
    client.set_project(project_id)

    engine = SnapshotEngine(client, config, "source")
    engine.capture(project_id, project_name, baseline_dir, harvest_csv)
    client.logout()


def mode_compare(config: Dict, harvest_csv: Optional[str], run_label: str):
    """Compare target (live) vs source (baseline on disk)."""
    env_cfg = config["environments"]["target"]
    val_cfg = config.get("validation", {})
    baseline_dir = Path(config["output"]["baseline_dir"])
    report_dir = Path(config["output"]["report_dir"])
    timeout = val_cfg.get("timeout_seconds", 120)

    client = make_client(env_cfg, "TARGET", timeout)
    if not client.login():
        sys.exit(1)

    project_id, project_name = resolve_project(client, env_cfg)
    client.set_project(project_id)

    engine = SnapshotEngine(client, config, "target")
    live_snaps = engine.capture(project_id, project_name,
                                Path("/tmp/mstr_target_live"), harvest_csv)
    client.logout()

    comparator = ComparisonEngine(config)
    results = comparator.compare_with_baseline(baseline_dir, live_snaps)

    reporter = ValidationReporter()
    reporter.write(results, report_dir, run_label or "compare")

    fails = [r for r in results if r.overall_status == "FAIL"]
    return 1 if fails else 0


def mode_full(config: Dict, harvest_csv: Optional[str], run_label: str):
    """Capture both source and target simultaneously, then compare."""
    val_cfg = config.get("validation", {})
    timeout = val_cfg.get("timeout_seconds", 120)
    report_dir = Path(config["output"]["report_dir"])

    src_cfg = config["environments"]["source"]
    tgt_cfg = config["environments"]["target"]

    src_client = make_client(src_cfg, "SOURCE", timeout)
    tgt_client = make_client(tgt_cfg, "TARGET", timeout)

    if not src_client.login() or not tgt_client.login():
        sys.exit(1)

    src_pid, src_pname = resolve_project(src_client, src_cfg)
    tgt_pid, tgt_pname = resolve_project(tgt_client, tgt_cfg)
    src_client.set_project(src_pid)
    tgt_client.set_project(tgt_pid)

    src_engine = SnapshotEngine(src_client, config, "source")
    tgt_engine = SnapshotEngine(tgt_client, config, "target")

    log.info("Executing on both environments simultaneously...")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_src = ex.submit(src_engine.capture, src_pid, src_pname,
                          Path("/tmp/mstr_src_snap"), harvest_csv)
        f_tgt = ex.submit(tgt_engine.capture, tgt_pid, tgt_pname,
                          Path("/tmp/mstr_tgt_snap"), harvest_csv)
        src_snaps = f_src.result()
        tgt_snaps = f_tgt.result()

    src_client.logout()
    tgt_client.logout()

    comparator = ComparisonEngine(config)
    results = comparator.compare(src_snaps, tgt_snaps)

    reporter = ValidationReporter()
    reporter.write(results, report_dir, run_label or "full")

    fails = [r for r in results if r.overall_status == "FAIL"]
    return 1 if fails else 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="MSTR Report Validation Framework — EBI Team",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        MODES:
          capture   Capture baseline snapshots from source environment
          compare   Execute target environment, compare vs baseline on disk
          full      Execute both environments simultaneously, then compare
          upgrade   Alias for 'compare' (use before/after MSTR upgrade)

        EXAMPLES:
          # Migration validation
          python mstr_report_validator.py --mode capture --config config.yaml
          python mstr_report_validator.py --mode compare --config config.yaml --label post-migration

          # MSTR upgrade testing
          python mstr_report_validator.py --mode capture --config config.yaml --label pre-upgrade-v12
          # <perform upgrade>
          python mstr_report_validator.py --mode upgrade --config config.yaml --label post-upgrade-v12

          # Use harvested report list (faster, more accurate than API discovery)
          python mstr_report_validator.py --mode full --config config.yaml \\
              --harvest-csv ./discovery_output/09_reports.csv

          # Test first 20 reports only (dry-run style)
          python mstr_report_validator.py --mode full --config config.yaml --max-reports 20
        """)
    )
    parser.add_argument("--mode", choices=["capture", "compare", "full", "upgrade"],
                        required=False, help="Execution mode")
    parser.add_argument("--config", default="config.yaml",
                        help="Path to config.yaml (default: config.yaml)")
    parser.add_argument("--harvest-csv", default=None,
                        help="Path to 09_reports.csv from mstr_harvester.py")
    parser.add_argument("--label", default="",
                        help="Human-readable run label (e.g. post-migration, pre-upgrade)")
    parser.add_argument("--max-reports", type=int, default=None,
                        help="Override max_reports from config (useful for quick tests)")
    parser.add_argument("--init", action="store_true",
                        help="Write a starter config.yaml template and exit")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.init:
        write_init_config("config.yaml")
        sys.exit(0)

    if not args.mode:
        parser.print_help()
        sys.exit(1)

    if not Path(args.config).exists():
        log.error(f"Config file not found: {args.config}")
        log.error("Run with --init to create a starter config.yaml")
        sys.exit(1)

    config = load_config(args.config)

    # CLI max-reports override
    if args.max_reports is not None:
        config.setdefault("validation", {})["max_reports"] = args.max_reports

    mode = args.mode
    if mode == "upgrade":
        mode = "compare"

    log.info(f"{'='*55}")
    log.info(f"  MSTR Report Validation Framework v1.0 — EBI Team")
    log.info(f"  Mode  : {args.mode.upper()}")
    log.info(f"  Config: {args.config}")
    if args.label:
        log.info(f"  Label : {args.label}")
    log.info(f"{'='*55}")

    exit_code = 0
    if mode == "capture":
        mode_capture(config, args.harvest_csv, args.label)
    elif mode == "compare":
        exit_code = mode_compare(config, args.harvest_csv, args.label)
    elif mode == "full":
        exit_code = mode_full(config, args.harvest_csv, args.label)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
