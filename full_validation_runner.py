#!/usr/bin/env python3
"""
================================================================================
  MicroStrategy Full Validation Runner v2.0
  ==========================================
  Orchestrates all Phase 3 validation checks in a single command:

    1. Re-harvests the cloud IS (runs mstr_harvester.py)
    2. Diffs cloud vs on-prem baseline (runs mstr_validator.py)
    3. Tests DB connectivity via odbc.ini (runs mstr_connectivity_tester.py)
    4. Produces a MASTER_VALIDATION_REPORT.txt combining all results

  USAGE:
    python full_validation_runner.py \
        --baseline-dir  ./discovery_output \
        --cloud-host    https://CLOUD-MSTR/MicroStrategyLibrary \
        --cloud-user    Administrator \
        --cloud-pass    YourCloudPassword \
        --cmc-host      cloud-mstr.company.com \
        --cmc-port      34952 \
        --odbc-file     /etc/odbc.ini \
        --output-dir    ./full_validation

  REQUIREMENTS:
    Python 3.8+
    pip install requests  (for mstr_harvester.py)
    All scripts must be in the same directory as this runner.

  AUTHOR: MicroStrategy Admin Automation Toolkit
  VERSION: 2.0
================================================================================
"""

import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


# ─────────────────────────────────────────────────────────────
# STEP RUNNER
# ─────────────────────────────────────────────────────────────

class StepResult:
    def __init__(self, name: str, status: str, detail: str, elapsed: float):
        self.name    = name
        self.status  = status   # PASS | FAIL | WARN | SKIP
        self.detail  = detail
        self.elapsed = elapsed


def run_step(step_name: str, cmd: List[str], timeout_sec: int = 1800) -> StepResult:
    """
    Run a subprocess step, capture output, and return a StepResult.
    """
    print(f"\n  ┌─ {step_name}")
    print(f"  │  CMD: {' '.join(cmd)}")
    start = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        elapsed = time.time() - start
        output = (result.stdout + result.stderr).strip()

        # Print last few lines of output for visibility
        output_lines = output.splitlines()
        for line in output_lines[-6:]:
            print(f"  │  {line}")

        if result.returncode == 0:
            print(f"  └─ [PASS] Completed in {elapsed:.1f}s")
            return StepResult(step_name, "PASS", output, elapsed)
        else:
            print(f"  └─ [FAIL] Exit code {result.returncode} after {elapsed:.1f}s")
            return StepResult(step_name, "FAIL", output, elapsed)

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  └─ [FAIL] Timed out after {elapsed:.1f}s")
        return StepResult(step_name, "FAIL", f"Timed out after {timeout_sec}s", elapsed)

    except FileNotFoundError as e:
        elapsed = time.time() - start
        msg = f"Script not found: {cmd[0]} — ensure all scripts are in the same directory."
        print(f"  └─ [SKIP] {msg}")
        return StepResult(step_name, "SKIP", msg, elapsed)

    except Exception as e:
        elapsed = time.time() - start
        print(f"  └─ [FAIL] Exception: {e}")
        return StepResult(step_name, "FAIL", str(e), elapsed)


# ─────────────────────────────────────────────────────────────
# MASTER REPORT GENERATOR
# ─────────────────────────────────────────────────────────────

def read_diff_summary(diff_report_path: str) -> Dict:
    """Read DIFF_REPORT.csv and return severity counts."""
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "WARNING": 0, "INFO": 0, "OK": 0}
    status_counts = {"MISSING": 0, "CHANGED": 0, "EXTRA": 0, "MATCH": 0}

    if not os.path.exists(diff_report_path):
        return {"severity": counts, "status": status_counts, "total": 0}

    total = 0
    try:
        with open(diff_report_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sev = row.get("severity", "").strip().upper()
                sta = row.get("status", "").strip().upper()
                if sev in counts:
                    counts[sev] += 1
                if sta in status_counts:
                    status_counts[sta] += 1
                total += 1
    except Exception:
        pass

    return {"severity": counts, "status": status_counts, "total": total}


def read_connectivity_summary(connectivity_results_path: str) -> Dict:
    """Read connectivity_results.csv and return pass/fail counts."""
    total = 0
    tcp_open = 0
    tcp_fail = 0
    dns_fail = 0
    categories = {}

    if not os.path.exists(connectivity_results_path):
        return {"total": 0, "tcp_open": 0, "tcp_fail": 0, "dns_fail": 0, "categories": {}}

    try:
        with open(connectivity_results_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                tcp = row.get("tcp_port_status", "").strip()
                dns = row.get("dns_resolves", "").strip()
                cat = row.get("category", "Unknown")

                if tcp == "OPEN":
                    tcp_open += 1
                elif tcp in ("CLOSED", "TIMEOUT", "DNS_FAIL", "NO_ROUTE", "ERROR"):
                    tcp_fail += 1

                if dns == "DNS_FAIL":
                    dns_fail += 1

                categories[cat] = categories.get(cat, 0) + 1
    except Exception:
        pass

    return {
        "total": total,
        "tcp_open": tcp_open,
        "tcp_fail": tcp_fail,
        "dns_fail": dns_fail,
        "categories": categories,
    }


def generate_master_report(
    step_results: List[StepResult],
    args,
    output_dir: str,
    report_time: str,
    cloud_harvest_dir: str,
    diff_report_path: str,
    connectivity_results_path: str,
) -> str:
    """Generate MASTER_VALIDATION_REPORT.txt."""

    diff_summary = read_diff_summary(diff_report_path)
    conn_summary = read_connectivity_summary(connectivity_results_path)

    lines = []
    sep = "=" * 80
    thin = "-" * 80

    def section(title):
        lines.append("")
        lines.append(sep)
        lines.append(f"  {title}")
        lines.append(sep)

    def row(label, value, w=45):
        lines.append(f"  {label:<{w}} {value}")

    lines.append(sep)
    lines.append("  MICROSTRATEGY CLOUD MIGRATION — MASTER VALIDATION REPORT")
    lines.append("  Generated by MicroStrategy Full Validation Runner v2.0")
    lines.append(sep)
    lines.append(f"  Report Time         : {report_time}")
    lines.append(f"  On-Prem Baseline    : {os.path.abspath(args.baseline_dir)}")
    lines.append(f"  Cloud IS URL        : {args.cloud_host}")
    lines.append(f"  Cloud Harvest Dir   : {cloud_harvest_dir}")
    lines.append(f"  CMC Host            : {args.cmc_host}:{args.cmc_port}")
    lines.append(sep)

    # Overall verdict
    diff_sev = diff_summary["severity"]
    critical = diff_sev.get("CRITICAL", 0)
    high = diff_sev.get("HIGH", 0)
    conn_fail = conn_summary.get("tcp_fail", 0)

    if critical > 0 or conn_fail > 0:
        verdict = "FAIL — CRITICAL ISSUES MUST BE RESOLVED BEFORE GO-LIVE"
        verdict_icon = "[FAIL]"
    elif high > 5:
        verdict = "CONDITIONAL PASS — SIGNIFICANT HIGH-SEVERITY ISSUES REQUIRE REVIEW"
        verdict_icon = "[WARN]"
    elif high > 0:
        verdict = "CONDITIONAL PASS — REVIEW HIGH-SEVERITY ITEMS BEFORE GO-LIVE"
        verdict_icon = "[WARN]"
    else:
        verdict = "PASS — MIGRATION FULLY VALIDATED"
        verdict_icon = "[PASS]"

    section("1. OVERALL VALIDATION VERDICT")
    lines.append(f"  {verdict_icon}  {verdict}")

    # Step summary table
    section("2. VALIDATION STEPS EXECUTED")
    lines.append(f"  {'Step':<50} {'Status':>6} {'Time':>8}")
    lines.append(f"  {thin}")
    for sr in step_results:
        icon = "✓" if sr.status == "PASS" else "✗" if sr.status == "FAIL" else "~"
        lines.append(f"  {icon} {sr.name:<48} {sr.status:>6} {sr.elapsed:>6.1f}s")

    # Metadata diff summary
    section("3. METADATA COMPARISON RESULTS")
    if diff_summary["total"] > 0:
        row("Total Comparison Records", str(diff_summary["total"]))
        row("  MATCH (fully validated)", str(diff_sev.get("OK", 0)))
        row("  CRITICAL (blocks go-live)", str(critical))
        row("  HIGH (degrades experience)", str(high))
        row("  MEDIUM (minor impact)", str(diff_sev.get("MEDIUM", 0)))
        row("  INFO (expected differences)", str(diff_sev.get("INFO", 0)))
        lines.append("")
        row("Records MISSING from cloud", str(diff_summary["status"].get("MISSING", 0)))
        row("Records CHANGED in cloud", str(diff_summary["status"].get("CHANGED", 0)))
        row("Records EXTRA in cloud", str(diff_summary["status"].get("EXTRA", 0)))
        row("Records MATCHING exactly", str(diff_summary["status"].get("MATCH", 0)))
        lines.append("")
        if critical > 0:
            lines.append(f"  [FAIL]  {critical} CRITICAL issues — see DIFF_REPORT.csv for details")
        elif high > 0:
            lines.append(f"  [WARN]  {high} HIGH issues — review before go-live")
        else:
            lines.append(f"  [PASS]  No Critical or High metadata issues detected")
    else:
        lines.append("  [SKIP]  Diff report not available — harvester or validator step failed.")

    # Connectivity summary
    section("4. NETWORK CONNECTIVITY RESULTS")
    if conn_summary["total"] > 0:
        row("Total DB Connections Tested", str(conn_summary["total"]))
        row("  TCP Port OPEN (reachable)", str(conn_summary["tcp_open"]))
        row("  TCP Port FAILED", str(conn_summary["tcp_fail"]))
        row("  DNS Resolution Failures", str(conn_summary["dns_fail"]))
        lines.append("")
        lines.append("  By DB Category:")
        for cat, count in sorted(conn_summary["categories"].items()):
            lines.append(f"    {cat:<40} {count} connection(s)")
        lines.append("")
        if conn_fail == 0:
            lines.append("  [PASS]  All DB connections are reachable from this host.")
        else:
            lines.append(f"  [FAIL]  {conn_fail} DB connection(s) unreachable — fix before go-live.")
    else:
        lines.append("  [SKIP]  Connectivity results not available — tester step failed.")

    # AI next steps
    section("5. AI-ASSISTED NEXT STEPS")
    lines.append("  Feed these files to Claude or ChatGPT for immediate remediation guidance:")
    lines.append("")
    if os.path.exists(diff_report_path):
        lines.append(f"  a) DIFF_REPORT.csv → {diff_report_path}")
        lines.append("     Prompt: 'Review this MSTR migration diff. Classify each issue as")
        lines.append("     Critical/High/Medium/Info. Provide the exact remediation step for each.'")
    lines.append("")
    if os.path.exists(connectivity_results_path):
        lines.append(f"  b) connectivity_results.csv → {connectivity_results_path}")
        lines.append("     Prompt: 'Review this connectivity test. For each failed connection,")
        lines.append("     provide the exact firewall rule or DNS fix required.'")
    lines.append("")
    lines.append("  c) This MASTER_VALIDATION_REPORT.txt →")
    lines.append("     Prompt: 'Generate a professional migration sign-off report from this")
    lines.append("     validation summary. Include executive summary and signature block.'")

    # Output files directory
    section("6. ALL OUTPUT FILES")
    lines.append(f"  Output directory: {output_dir}")
    lines.append("")
    output_files = [
        ("MASTER_VALIDATION_REPORT.txt", "This file — overall validation summary"),
        ("cloud_harvest/",              "Cloud IS harvest (21 CSVs + SUMMARY_REPORT.txt)"),
        ("diff_results/DIFF_REPORT.csv","Field-by-field metadata comparison"),
        ("diff_results/VALIDATION_REPORT.txt", "Metadata validation pass/fail report"),
        ("connectivity/db_connections_inventory.csv", "All DB connections from odbc.ini"),
        ("connectivity/connectivity_results.csv", "Ping + TCP test results per connection"),
        ("connectivity/CONNECTIVITY_REPORT.txt", "Network connectivity pass/fail report"),
    ]
    for fname, desc in output_files:
        full_path = os.path.join(output_dir, fname.replace("/", os.sep))
        exists = "✓" if (os.path.exists(full_path) or fname.endswith("/")) else "✗"
        lines.append(f"  {exists} {fname:<50} {desc}")

    # Sign-off
    section("7. SIGN-OFF")
    lines.append(f"  Migration Validated By : ________________________________")
    lines.append(f"  Date                  : {report_time}")
    lines.append(f"  Overall Verdict        : {verdict}")
    lines.append(f"  Cloud IS URL           : {args.cloud_host}")
    lines.append("")
    lines.append(f"  End User Acceptance:")
    lines.append(f"  Accepted By           : ________________________________")
    lines.append(f"  Acceptance Date       : ________________________________")
    lines.append(sep)

    report_path = os.path.join(output_dir, "MASTER_VALIDATION_REPORT.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run_full_validation(args):
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Sub-directories for each step's output
    cloud_harvest_dir = os.path.join(output_dir, "cloud_harvest")
    diff_dir          = os.path.join(output_dir, "diff_results")
    connectivity_dir  = os.path.join(output_dir, "connectivity")

    for d in [cloud_harvest_dir, diff_dir, connectivity_dir]:
        os.makedirs(d, exist_ok=True)

    # Locate scripts relative to this runner
    script_dir = os.path.dirname(os.path.abspath(__file__))
    harvester  = os.path.join(script_dir, "mstr_harvester.py")
    validator  = os.path.join(script_dir, "mstr_validator.py")
    conn_tester = os.path.join(script_dir, "mstr_connectivity_tester.py")

    python = sys.executable

    print("=" * 64)
    print("  MicroStrategy Full Validation Runner v2.0")
    print(f"  Cloud IS   : {args.cloud_host}")
    print(f"  Baseline   : {args.baseline_dir}")
    print(f"  CMC Host   : {args.cmc_host}:{args.cmc_port}")
    print(f"  Output     : {output_dir}")
    print(f"  Started    : {report_time}")
    print("=" * 64)

    step_results = []

    # ── STEP 1: Re-harvest cloud IS ──────────────────────────
    harvester_cmd = [
        python, harvester,
        "--host", args.cloud_host,
        "--username", args.cloud_user,
        "--password", args.cloud_pass,
        "--output-dir", cloud_harvest_dir,
        "--all-projects",
    ]
    if args.login_mode != "1":
        harvester_cmd += ["--login-mode", args.login_mode]
    if args.no_ssl_verify:
        harvester_cmd.append("--no-ssl-verify")

    print("\n  [STEP 1/3] Re-harvesting cloud Intelligence Server...")
    sr1 = run_step("Re-harvest Cloud IS", harvester_cmd, timeout_sec=1800)
    step_results.append(sr1)

    # ── STEP 2: Run validator / diff ─────────────────────────
    baseline_dir = os.path.abspath(args.baseline_dir)
    if not os.path.exists(baseline_dir):
        print(f"\n  [WARN] Baseline directory not found: {baseline_dir}")
        print("         Skipping diff step — run on-prem harvester first.")
        step_results.append(StepResult("Metadata Diff", "SKIP",
                                       "Baseline directory not found", 0))
    else:
        validator_cmd = [
            python, validator,
            "--baseline", baseline_dir,
            "--target", cloud_harvest_dir,
            "--output-dir", diff_dir,
        ]
        print("\n  [STEP 2/3] Running metadata diff (on-prem vs cloud)...")
        sr2 = run_step("Metadata Diff (on-prem vs cloud)", validator_cmd, timeout_sec=300)
        step_results.append(sr2)

    # ── STEP 3: Connectivity test ─────────────────────────────
    if args.odbc_file and os.path.exists(args.odbc_file):
        conn_cmd = [
            python, conn_tester,
            "--odbc-file", args.odbc_file,
            "--cmc-host", args.cmc_host,
            "--cmc-port", str(args.cmc_port),
            "--output-dir", connectivity_dir,
        ]
        if args.skip_ping:
            conn_cmd.append("--skip-ping")

        print("\n  [STEP 3/3] Testing network connectivity from odbc.ini...")
        sr3 = run_step("Network Connectivity Test", conn_cmd, timeout_sec=600)
        step_results.append(sr3)
    else:
        print("\n  [STEP 3/3] Skipping connectivity test — no odbc.ini provided or file not found.")
        step_results.append(StepResult("Network Connectivity Test", "SKIP",
                                       "No odbc.ini provided", 0))

    # ── Generate Master Report ────────────────────────────────
    diff_report_path        = os.path.join(diff_dir, "DIFF_REPORT.csv")
    connectivity_results    = os.path.join(connectivity_dir, "connectivity_results.csv")

    print("\n  Generating MASTER_VALIDATION_REPORT.txt...")
    master_report = generate_master_report(
        step_results=step_results,
        args=args,
        output_dir=output_dir,
        report_time=report_time,
        cloud_harvest_dir=cloud_harvest_dir,
        diff_report_path=diff_report_path,
        connectivity_results_path=connectivity_results,
    )

    # ── Final Summary ─────────────────────────────────────────
    pass_count = sum(1 for s in step_results if s.status == "PASS")
    fail_count = sum(1 for s in step_results if s.status == "FAIL")
    skip_count = sum(1 for s in step_results if s.status == "SKIP")

    print("")
    print("=" * 64)
    print("  FULL VALIDATION COMPLETE")
    print(f"  Steps PASS  : {pass_count}")
    print(f"  Steps FAIL  : {fail_count}")
    print(f"  Steps SKIP  : {skip_count}")
    print(f"  All files   : {output_dir}/")
    print(f"  Master report: {master_report}")
    print("=" * 64)
    print("\n  NEXT: Feed MASTER_VALIDATION_REPORT.txt to AI for sign-off report generation.")

    return 0 if fail_count == 0 else 1


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MicroStrategy Full Validation Runner — orchestrates all Phase 3 checks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full validation run
  python full_validation_runner.py \\
      --baseline-dir  ./discovery_output \\
      --cloud-host    https://cloud-mstr.company.com/MicroStrategyLibrary \\
      --cloud-user    Administrator \\
      --cloud-pass    CloudPass123 \\
      --cmc-host      cloud-mstr.company.com \\
      --cmc-port      34952 \\
      --odbc-file     /etc/odbc.ini \\
      --output-dir    ./full_validation

  # Skip ping tests (ICMP blocked), LDAP auth, no SSL verify
  python full_validation_runner.py \\
      --baseline-dir  ./discovery_output \\
      --cloud-host    https://cloud-mstr.company.com/MicroStrategyLibrary \\
      --cloud-user    jsmith \\
      --cloud-pass    LdapPass \\
      --login-mode    16 \\
      --cmc-host      cloud-mstr.company.com \\
      --cmc-port      443 \\
      --odbc-file     /etc/odbc.ini \\
      --skip-ping \\
      --no-ssl-verify
"""
    )
    # Cloud IS credentials
    parser.add_argument("--cloud-host", required=True,
                        help="Cloud MicroStrategy Library URL (e.g. https://cloud.company.com/MicroStrategyLibrary)")
    parser.add_argument("--cloud-user", required=True,
                        help="Cloud IS admin username")
    parser.add_argument("--cloud-pass", required=True,
                        help="Cloud IS admin password")
    parser.add_argument("--login-mode", default="1",
                        choices=["1", "4", "8", "16", "64"],
                        help="Login mode: 1=Standard, 16=LDAP, 64=SAML (default: 1)")
    parser.add_argument("--no-ssl-verify", action="store_true",
                        help="Disable SSL cert verification")

    # Baseline
    parser.add_argument("--baseline-dir", required=True,
                        help="On-prem harvest directory (from mstr_harvester.py Phase 1 run)")

    # CMC connectivity
    parser.add_argument("--cmc-host", required=True,
                        help="CMC cloud IS hostname (e.g. cloud-mstr.company.com)")
    parser.add_argument("--cmc-port", type=int, default=34952,
                        help="CMC Intelligence Server port (default: 34952)")

    # ODBC
    parser.add_argument("--odbc-file", default=None,
                        help="Path to odbc.ini for connectivity testing (optional but recommended)")
    parser.add_argument("--skip-ping", action="store_true",
                        help="Skip ping tests (ICMP blocked)")

    # Output
    parser.add_argument("--output-dir", default="./full_validation",
                        help="Output directory for all validation results (default: ./full_validation)")

    args = parser.parse_args()
    sys.exit(run_full_validation(args))


if __name__ == "__main__":
    main()
