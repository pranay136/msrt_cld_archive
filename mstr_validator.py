#!/usr/bin/env python3
"""
================================================================================
  MicroStrategy Migration Validator v2.0
  =======================================
  Compares on-prem (baseline) and cloud (target) harvest directories.
  Produces a field-by-field DIFF_REPORT.csv and a VALIDATION_REPORT.txt.

  USAGE:
    python mstr_validator.py \
        --baseline ./onprem_discovery \
        --target   ./cloud_discovery \
        --output-dir ./validation_results

  OUTPUT:
    DIFF_REPORT.csv         Every comparison with status: MATCH/MISSING/EXTRA/CHANGED
    VALIDATION_REPORT.txt   Human-readable pass/fail summary with risk classification

  REQUIREMENTS:
    Python 3.8+ (stdlib only — no pip installs needed)

  AUTHOR: MicroStrategy Admin Automation Toolkit
  VERSION: 2.0
================================================================================
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────
# File comparison configuration
# ─────────────────────────────────────────────────────────────

# For each CSV file: (key_fields, comparison_fields, severity_if_missing)
COMPARISON_CONFIG = {
    "01_server_info.csv": {
        "key": ["field"],
        "compare": ["value"],
        "missing_severity": "WARNING",
        "domain": "Server Infrastructure",
        "description": "Intelligence Server configuration",
    },
    "02_projects.csv": {
        "key": ["id"],
        "compare": ["name", "status"],
        "missing_severity": "CRITICAL",
        "domain": "Projects",
        "description": "Project inventory",
    },
    "03_users.csv": {
        "key": ["id"],
        "compare": ["username", "full_name", "enabled", "login_mode_label"],
        "missing_severity": "CRITICAL",
        "domain": "Users",
        "description": "User accounts",
    },
    "04_usergroups.csv": {
        "key": ["id"],
        "compare": ["name", "description"],
        "missing_severity": "HIGH",
        "domain": "User Groups",
        "description": "User group structure",
    },
    "05_group_membership.csv": {
        "key": ["group_id", "member_id"],
        "compare": ["group_name", "member_name", "member_type"],
        "missing_severity": "HIGH",
        "domain": "Group Memberships",
        "description": "User-to-group membership assignments",
    },
    "06_security_roles.csv": {
        "key": ["id"],
        "compare": ["name", "privilege_count"],
        "missing_severity": "HIGH",
        "domain": "Security Roles",
        "description": "Security role definitions",
    },
    "07_security_filters.csv": {
        "key": ["id", "project_id"],
        "compare": ["name", "owner_name"],
        "missing_severity": "CRITICAL",
        "domain": "Security Filters",
        "description": "Security filter assignments (access control)",
    },
    "08_datasources.csv": {
        "key": ["id"],
        "compare": ["name", "db_type", "host", "database_name"],
        "missing_severity": "CRITICAL",
        "domain": "Database Connections",
        "description": "Datasource and DB connection definitions",
    },
    "09_reports.csv": {
        "key": ["id", "project_id"],
        "compare": ["name", "path"],
        "missing_severity": "HIGH",
        "domain": "Reports",
        "description": "Report objects",
    },
    "10_documents_dossiers.csv": {
        "key": ["id", "project_id"],
        "compare": ["name", "path", "object_type_name"],
        "missing_severity": "HIGH",
        "domain": "Documents & Dossiers",
        "description": "Document and Dossier objects",
    },
    "11_metrics.csv": {
        "key": ["id", "project_id"],
        "compare": ["name"],
        "missing_severity": "HIGH",
        "domain": "Metrics",
        "description": "Metric definitions",
    },
    "12_attributes.csv": {
        "key": ["id", "project_id"],
        "compare": ["name"],
        "missing_severity": "MEDIUM",
        "domain": "Attributes",
        "description": "Attribute definitions",
    },
    "13_facts.csv": {
        "key": ["id", "project_id"],
        "compare": ["name"],
        "missing_severity": "MEDIUM",
        "domain": "Facts",
        "description": "Fact definitions",
    },
    "14_filters.csv": {
        "key": ["id", "project_id"],
        "compare": ["name"],
        "missing_severity": "MEDIUM",
        "domain": "Filters",
        "description": "Filter definitions",
    },
    "15_prompts.csv": {
        "key": ["id", "project_id"],
        "compare": ["name"],
        "missing_severity": "MEDIUM",
        "domain": "Prompts",
        "description": "Prompt definitions",
    },
    "16_schedules.csv": {
        "key": ["id"],
        "compare": ["name", "enabled", "schedule_type"],
        "missing_severity": "HIGH",
        "domain": "Schedules",
        "description": "Schedule configurations",
    },
    "17_subscriptions.csv": {
        "key": ["id", "project_id"],
        "compare": ["name", "owner_name", "delivery_type", "enabled"],
        "missing_severity": "HIGH",
        "domain": "Subscriptions",
        "description": "Subscription delivery configurations",
    },
    "19_security_config.csv": {
        "key": ["setting_category", "setting_name"],
        "compare": ["setting_value"],
        "missing_severity": "HIGH",
        "domain": "Security Config",
        "description": "Authentication and security settings",
    },
    "20_email_config.csv": {
        "key": ["setting_name"],
        "compare": ["value"],
        "missing_severity": "MEDIUM",
        "domain": "Email Config",
        "description": "SMTP and email delivery settings",
    },
    "21_licenses.csv": {
        "key": ["license_key"],
        "compare": ["product", "license_type", "named_users"],
        "missing_severity": "WARNING",
        "domain": "Licensing",
        "description": "License activations",
    },
}

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "WARNING": 3, "INFO": 4, "OK": 5}


# ─────────────────────────────────────────────────────────────
# CSV Loading
# ─────────────────────────────────────────────────────────────

def load_csv(filepath: str) -> List[Dict]:
    """Load a CSV file into a list of dicts."""
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        return list(reader)


def index_rows(rows: List[Dict], key_fields: List[str]) -> Dict[Tuple, Dict]:
    """Index rows by a composite key tuple."""
    index = {}
    for row in rows:
        key_vals = tuple(str(row.get(k, "")).strip() for k in key_fields)
        if any(v for v in key_vals):  # Skip blank keys
            index[key_vals] = row
    return index


# ─────────────────────────────────────────────────────────────
# Comparison Engine
# ─────────────────────────────────────────────────────────────

def compare_csv_files(
    baseline_path: str,
    target_path: str,
    config: Dict,
    filename: str
) -> List[Dict]:
    """
    Compare two CSV files and return a list of diff records.
    Each record has: file, domain, key, field, baseline_value, target_value, status, severity
    """
    key_fields = config["key"]
    compare_fields = config["compare"]
    missing_severity = config["missing_severity"]
    domain = config["domain"]

    baseline_rows = load_csv(baseline_path)
    target_rows = load_csv(target_path)

    baseline_idx = index_rows(baseline_rows, key_fields)
    target_idx = index_rows(target_rows, key_fields)

    diffs = []

    # Check for missing and changed records
    for key, baseline_row in baseline_idx.items():
        key_str = " | ".join(f"{k}={v}" for k, v in zip(key_fields, key))

        if key not in target_idx:
            # Record missing in cloud
            diffs.append({
                "file": filename,
                "domain": domain,
                "record_key": key_str,
                "field_name": "[RECORD]",
                "baseline_value": str(baseline_row.get(compare_fields[0], ""))[:100],
                "target_value": "MISSING",
                "status": "MISSING",
                "severity": missing_severity,
                "remediation": f"Object '{baseline_row.get('name', key_str)}' was not migrated to cloud. Re-run migration for this object.",
            })
        else:
            target_row = target_idx[key]
            for field in compare_fields:
                bval = str(baseline_row.get(field, "")).strip()
                tval = str(target_row.get(field, "")).strip()

                if bval != tval:
                    # Determine severity of field mismatch
                    if field in ("enabled", "status"):
                        severity = "HIGH"
                    elif field in ("name", "username"):
                        severity = "MEDIUM"
                    else:
                        severity = "INFO"

                    diffs.append({
                        "file": filename,
                        "domain": domain,
                        "record_key": key_str,
                        "field_name": field,
                        "baseline_value": bval[:150],
                        "target_value": tval[:150],
                        "status": "CHANGED",
                        "severity": severity,
                        "remediation": f"Field '{field}' differs. Baseline: '{bval[:50]}' → Cloud: '{tval[:50]}'. Verify migration of this object.",
                    })

    # Check for extra records in cloud (not in on-prem)
    for key in target_idx:
        if key not in baseline_idx:
            key_str = " | ".join(f"{k}={v}" for k, v in zip(key_fields, key))
            target_row = target_idx[key]
            diffs.append({
                "file": filename,
                "domain": domain,
                "record_key": key_str,
                "field_name": "[RECORD]",
                "baseline_value": "NOT IN BASELINE",
                "target_value": str(target_row.get(compare_fields[0], ""))[:100],
                "status": "EXTRA",
                "severity": "INFO",
                "remediation": "Object exists in cloud but not in on-prem baseline. This may be a new object — verify it is intentional.",
            })

    # If no diffs — add a MATCH summary record
    if not diffs and (baseline_rows or target_rows):
        diffs.append({
            "file": filename,
            "domain": domain,
            "record_key": f"{len(baseline_rows)} records compared",
            "field_name": "[ALL FIELDS]",
            "baseline_value": str(len(baseline_rows)),
            "target_value": str(len(target_rows)),
            "status": "MATCH",
            "severity": "OK",
            "remediation": "",
        })

    return diffs


# ─────────────────────────────────────────────────────────────
# Validation Report Generator
# ─────────────────────────────────────────────────────────────

def generate_validation_report(
    all_diffs: List[Dict],
    baseline_dir: str,
    target_dir: str,
    output_dir: str,
    report_time: str,
) -> str:
    """Generate VALIDATION_REPORT.txt from the full diff list."""

    lines = []
    sep = "=" * 80
    thin = "-" * 80

    def section(title):
        lines.append("")
        lines.append(sep)
        lines.append(f"  {title}")
        lines.append(sep)

    def row(label, value, width=45):
        lines.append(f"  {label:<{width}} {value}")

    # Header
    lines.append(sep)
    lines.append("  MICROSTRATEGY MIGRATION VALIDATION REPORT")
    lines.append("  Generated by MicroStrategy Validator v2.0")
    lines.append(sep)
    lines.append(f"  Baseline (On-Prem) : {os.path.abspath(baseline_dir)}")
    lines.append(f"  Target (Cloud)     : {os.path.abspath(target_dir)}")
    lines.append(f"  Report Time        : {report_time}")
    lines.append(sep)

    # Severity summary
    severity_counts = defaultdict(int)
    domain_results = defaultdict(lambda: {"MATCH": 0, "MISSING": 0, "CHANGED": 0, "EXTRA": 0})

    for d in all_diffs:
        sev = d["severity"]
        status = d["status"]
        severity_counts[sev] += 1
        domain_results[d["domain"]][status] += 1

    critical = severity_counts.get("CRITICAL", 0)
    high = severity_counts.get("HIGH", 0)
    medium = severity_counts.get("MEDIUM", 0)
    warning = severity_counts.get("WARNING", 0)
    info = severity_counts.get("INFO", 0)
    ok = severity_counts.get("OK", 0)

    # Overall verdict
    if critical > 0:
        verdict = "FAIL — CRITICAL ISSUES MUST BE RESOLVED BEFORE GO-LIVE"
        verdict_indicator = "[!!]"
    elif high > 5:
        verdict = "CONDITIONAL PASS — HIGH-SEVERITY ISSUES REQUIRE REVIEW"
        verdict_indicator = "[!] "
    elif high > 0 or medium > 0:
        verdict = "CONDITIONAL PASS — REVIEW AND REMEDIATE FLAGGED ISSUES"
        verdict_indicator = "[~] "
    else:
        verdict = "PASS — MIGRATION VALIDATED SUCCESSFULLY"
        verdict_indicator = "[OK]"

    section("1. OVERALL VALIDATION VERDICT")
    lines.append(f"  {verdict_indicator} {verdict}")
    lines.append("")
    row("CRITICAL Issues (blocks go-live)", str(critical))
    row("HIGH Issues (degrades experience)", str(high))
    row("MEDIUM Issues (minor impact)", str(medium))
    row("WARNING (informational, review)", str(warning))
    row("INFO (expected differences)", str(info))
    row("MATCH (fully validated)", str(ok))

    # Domain scorecard
    section("2. VALIDATION SCORECARD BY DOMAIN")
    lines.append(f"  {'Domain':<35} {'MATCH':>7} {'MISSING':>8} {'CHANGED':>8} {'EXTRA':>7} {'Status'}")
    lines.append(f"  {thin}")

    for domain, counts in sorted(domain_results.items()):
        missing = counts["MISSING"]
        changed = counts["CHANGED"]
        extra = counts["EXTRA"]
        match = counts["MATCH"]

        if missing > 0 or changed > 5:
            status = "FAIL"
        elif changed > 0 or extra > 0:
            status = "REVIEW"
        else:
            status = "PASS"

        lines.append(
            f"  {domain:<35} {match:>7} {missing:>8} {changed:>8} {extra:>7}   {status}"
        )

    # Critical issues detail
    critical_diffs = [d for d in all_diffs if d["severity"] == "CRITICAL" and d["status"] != "MATCH"]
    if critical_diffs:
        section("3. CRITICAL ISSUES — MUST FIX BEFORE GO-LIVE")
        for i, d in enumerate(critical_diffs[:50], 1):  # Cap at 50
            lines.append(f"\n  [{i:02d}] Domain   : {d['domain']}")
            lines.append(f"       Record   : {d['record_key']}")
            lines.append(f"       Status   : {d['status']}")
            lines.append(f"       Baseline : {d['baseline_value']}")
            lines.append(f"       Cloud    : {d['target_value']}")
            lines.append(f"       Fix      : {d['remediation']}")

    # High issues
    high_diffs = [d for d in all_diffs if d["severity"] == "HIGH" and d["status"] != "MATCH"]
    if high_diffs:
        section(f"4. HIGH SEVERITY ISSUES ({len(high_diffs)} found)")
        for i, d in enumerate(high_diffs[:30], 1):
            lines.append(f"  [{i:02d}] {d['domain']} | {d['record_key'][:50]} | {d['status']} | "
                         f"Baseline: {d['baseline_value'][:40]} | Cloud: {d['target_value'][:40]}")
            lines.append(f"       Fix: {d['remediation'][:100]}")

    # Medium issues summary
    med_diffs = [d for d in all_diffs if d["severity"] == "MEDIUM" and d["status"] != "MATCH"]
    if med_diffs:
        section(f"5. MEDIUM SEVERITY ISSUES ({len(med_diffs)} found — review but not blocking)")
        for i, d in enumerate(med_diffs[:20], 1):
            lines.append(f"  [{i:02d}] {d['domain']} | {d['record_key'][:50]} | {d['status']} | "
                         f"{d['field_name']}: '{d['baseline_value'][:30]}' → '{d['target_value'][:30]}'")

    # Pass sections
    pass_domains = [d for d, c in domain_results.items()
                    if c["MISSING"] == 0 and c["CHANGED"] == 0 and c["MATCH"] > 0]
    if pass_domains:
        section("6. FULLY VALIDATED DOMAINS (PASS)")
        for dom in sorted(pass_domains):
            lines.append(f"  [PASS] {dom}")

    # Sign-off section
    section("7. MIGRATION SIGN-OFF")
    lines.append(f"  Migration Validated By : ________________________")
    lines.append(f"  Date                  : {report_time}")
    lines.append(f"  Environment           : {os.path.abspath(target_dir)}")
    lines.append(f"  Overall Result        : {verdict}")
    lines.append("")
    lines.append("  End User Acceptance:")
    lines.append(f"  Accepted By           : ________________________")
    lines.append(f"  Acceptance Date       : ________________________")
    lines.append(f"  Sign-Off Notes        : ________________________")

    section("8. NEXT STEPS")
    if critical > 0:
        lines.append("  1. Address all CRITICAL issues listed in Section 3 before go-live.")
        lines.append("  2. Re-run mstr_validator.py after each fix to confirm resolution.")
        lines.append("  3. Obtain end-user sign-off only after all CRITICAL issues are resolved.")
    elif high > 0:
        lines.append("  1. Review all HIGH issues in Section 4.")
        lines.append("  2. Remediate issues that affect user access or data delivery.")
        lines.append("  3. Schedule monitoring for the first week post-go-live.")
    else:
        lines.append("  1. Migration is validated. Proceed to end-user communication.")
        lines.append("  2. Schedule go-live window and notify users.")
        lines.append("  3. Monitor cloud IS logs for first 24 hours post go-live.")

    lines.append("")
    lines.append("  AI ASSISTANCE TIP:")
    lines.append("  Feed DIFF_REPORT.csv to Claude or ChatGPT with this prompt:")
    lines.append("  'Review this MSTR migration diff. Classify each issue as Critical/Warning/Info.")
    lines.append("   For Critical items, provide the exact remediation command or REST API call.'")

    lines.append("")
    lines.append(sep)
    lines.append(f"  Report generated: {report_time}")
    lines.append(f"  Full diff data  : DIFF_REPORT.csv")
    lines.append(sep)

    report_path = os.path.join(output_dir, "VALIDATION_REPORT.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def run_validation(args):
    baseline_dir = os.path.abspath(args.baseline)
    target_dir = os.path.abspath(args.target)
    output_dir = os.path.abspath(args.output_dir)

    os.makedirs(output_dir, exist_ok=True)

    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 60)
    print("  MicroStrategy Migration Validator v2.0")
    print(f"  Baseline : {baseline_dir}")
    print(f"  Target   : {target_dir}")
    print(f"  Output   : {output_dir}")
    print("=" * 60)

    all_diffs = []

    for filename, config in COMPARISON_CONFIG.items():
        baseline_path = os.path.join(baseline_dir, filename)
        target_path = os.path.join(target_dir, filename)

        if not os.path.exists(baseline_path):
            print(f"  [SKIP] {filename} — not found in baseline")
            continue

        print(f"  Comparing {filename}...")
        diffs = compare_csv_files(baseline_path, target_path, config, filename)
        all_diffs.extend(diffs)

        issues = [d for d in diffs if d["status"] != "MATCH"]
        print(f"    → {len(issues)} differences found")

    # Sort diffs by severity
    all_diffs.sort(key=lambda d: (SEVERITY_ORDER.get(d["severity"], 99), d["domain"]))

    # Write DIFF_REPORT.csv
    diff_path = os.path.join(output_dir, "DIFF_REPORT.csv")
    fieldnames = ["severity", "status", "domain", "file", "record_key",
                  "field_name", "baseline_value", "target_value", "remediation"]
    with open(diff_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_diffs)
    print(f"\n  Diff report → {diff_path}")

    # Write VALIDATION_REPORT.txt
    report_path = generate_validation_report(
        all_diffs, baseline_dir, target_dir, output_dir, report_time
    )
    print(f"  Validation report → {report_path}")

    # Print summary to console
    critical = sum(1 for d in all_diffs if d["severity"] == "CRITICAL" and d["status"] != "MATCH")
    high = sum(1 for d in all_diffs if d["severity"] == "HIGH" and d["status"] != "MATCH")
    match_count = sum(1 for d in all_diffs if d["status"] == "MATCH")

    print("")
    print("=" * 60)
    if critical > 0:
        print(f"  [FAIL]  {critical} CRITICAL issues found — fix before go-live!")
    elif high > 0:
        print(f"  [WARN]  {high} HIGH issues — review required")
    else:
        print("  [PASS]  Migration validated successfully!")
    print(f"  MATCH   : {match_count}")
    print(f"  CRITICAL: {critical}")
    print(f"  HIGH    : {high}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="MicroStrategy Migration Validator — compare on-prem vs cloud harvest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mstr_validator.py \\
      --baseline ./onprem_discovery \\
      --target   ./cloud_discovery \\
      --output-dir ./validation_results
"""
    )
    parser.add_argument("--baseline", required=True,
                        help="Path to on-prem discovery output directory")
    parser.add_argument("--target", required=True,
                        help="Path to cloud discovery output directory")
    parser.add_argument("--output-dir", default="./validation_results",
                        help="Directory to write validation output (default: ./validation_results)")

    args = parser.parse_args()
    run_validation(args)


if __name__ == "__main__":
    main()
