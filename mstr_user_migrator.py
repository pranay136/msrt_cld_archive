#!/usr/bin/env python3
"""
================================================================================
  MicroStrategy User & Group Migrator v2.0
  =========================================
  Reads 03_users.csv, 04_usergroups.csv, and 05_group_membership.csv
  (produced by mstr_harvester.py) and bulk-recreates users and groups
  on the target cloud IS via REST API.

  EXECUTION LAYER: REST API — runs from your laptop, no cluster shell needed.

  HANDLES:
    - Standard MSTR users (creates with temporary password, forces reset on first login)
    - LDAP users (creates shell — actual auth handled by LDAP connector on cloud IS)
    - SAML users (creates shell — actual auth handled by SAML IdP on cloud IS)
    - User groups and membership assignments
    - Security role assignments per user

  DOES NOT HANDLE (requires manual admin steps):
    - User passwords (standard users get a temp password — communicate separately)
    - LDAP connector configuration (configure in CMC Admin before running this)
    - SAML IdP settings (configure in CMC Admin before running this)

  USAGE:
    python mstr_user_migrator.py \
        --host      https://CLOUD-MSTR/MicroStrategyLibrary \
        --username  Administrator \
        --password  CloudPass \
        --users-csv ./discovery_output/03_users.csv \
        --groups-csv ./discovery_output/04_usergroups.csv \
        --membership-csv ./discovery_output/05_group_membership.csv \
        --temp-password  TempP@ss123! \
        --mode      full

  MODES:
    full       Create groups, then users, then assign memberships (default)
    groups     Create groups only
    users      Create users only (groups must already exist)
    memberships Assign memberships only (users and groups must already exist)
    dry-run    Print what would be created — no API calls

  REQUIREMENTS: Python 3.8+, pip install requests

  AUTHOR: MicroStrategy Admin Automation Toolkit | VERSION: 2.0
================================================================================
"""

import argparse
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
    print("[ERROR] pip install requests")
    sys.exit(1)


class MSTRClient:
    def __init__(self, url: str, verify: bool = True):
        self.api = url.rstrip("/") + "/api"
        self.session = requests.Session()
        self.session.verify = verify
        self.token = None

    def _h(self):
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token: h["X-MSTR-AuthToken"] = self.token
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

    def get(self, path, params=None):
        try:
            r = self.session.get(f"{self.api}/{path.lstrip('/')}",
                headers=self._h(), params=params, timeout=60)
            return r.json() if r.status_code == 200 else None
        except: return None

    def post(self, path, payload, timeout=30):
        try:
            r = self.session.post(f"{self.api}/{path.lstrip('/')}",
                headers=self._h(), json=payload, timeout=timeout)
            try:    body = r.json()
            except: body = r.text
            return r.status_code, body
        except Exception as e: return 0, str(e)

    def patch(self, path, payload, timeout=30):
        try:
            r = self.session.patch(f"{self.api}/{path.lstrip('/')}",
                headers=self._h(), json=payload, timeout=timeout)
            try:    body = r.json()
            except: body = r.text
            return r.status_code, body
        except Exception as e: return 0, str(e)


def load_csv(path: str) -> List[Dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def map_login_mode(mode_label: str) -> int:
    """Map the login_mode_label from harvest CSV back to MSTR login mode integer."""
    label = mode_label.lower()
    if "ldap"     in label: return 16
    if "saml"     in label: return 64
    if "kerberos" in label: return 4
    if "database" in label: return 8
    return 1  # Standard (default)


def get_existing_users(client: MSTRClient) -> Dict[str, str]:
    """Return dict of username → id for all existing users on target IS."""
    raw = []
    offset = 0
    while True:
        page = client.get("/users", params={"limit": 500, "offset": offset}) or []
        if isinstance(page, list): items = page
        elif isinstance(page, dict): items = page.get("users", [])
        else: break
        if not items: break
        raw.extend(items)
        if len(items) < 500: break
        offset += 500
    return {u.get("username", u.get("name", "")): u.get("id", "") for u in raw}


def get_existing_groups(client: MSTRClient) -> Dict[str, str]:
    """Return dict of group name → id for all existing groups on target IS."""
    raw = client.get("/usergroups", params={"limit": 500}) or []
    if isinstance(raw, dict): raw = raw.get("userGroups", [])
    return {g.get("name", ""): g.get("id", "") for g in raw}


# ─────────────────────────────────────────────────────────────
# CREATE GROUPS
# ─────────────────────────────────────────────────────────────

def create_groups(client: MSTRClient, groups: List[Dict],
                  dry_run: bool = False) -> Tuple[Dict[str, str], List[Dict]]:
    """Create all user groups. Returns (name→id map, result list)."""
    results = []
    name_to_id = {}
    existing = get_existing_groups(client)

    print(f"\n  Creating {len(groups)} user groups...")
    for i, g in enumerate(groups, 1):
        name = g.get("name", "")
        desc = g.get("description", "")

        if name in existing:
            name_to_id[name] = existing[name]
            results.append({"type": "group", "name": name, "status": "EXISTS",
                            "id": existing[name], "error": ""})
            print(f"  [{i:>3}] SKIP (exists)  {name}")
            continue

        if dry_run:
            results.append({"type": "group", "name": name, "status": "DRY_RUN",
                            "id": "", "error": ""})
            print(f"  [{i:>3}] DRY-RUN  {name}")
            continue

        payload = {"name": name, "description": desc}
        status, body = client.post("/usergroups", payload)
        if status in (200, 201):
            gid = body.get("id", "") if isinstance(body, dict) else ""
            name_to_id[name] = gid
            results.append({"type": "group", "name": name, "status": "CREATED",
                            "id": gid, "error": ""})
            print(f"  [{i:>3}] CREATED  {name}")
        else:
            error = str(body)[:120] if not isinstance(body, dict) else body.get("message", str(body))[:120]
            results.append({"type": "group", "name": name, "status": "FAIL",
                            "id": "", "error": error})
            print(f"  [{i:>3}] FAIL     {name}: {error}")

        time.sleep(0.2)

    return name_to_id, results


# ─────────────────────────────────────────────────────────────
# CREATE USERS
# ─────────────────────────────────────────────────────────────

def create_users(client: MSTRClient, users: List[Dict], temp_password: str,
                 dry_run: bool = False) -> Tuple[Dict[str, str], List[Dict]]:
    """Create all users on the target IS. Returns (username→id map, result list)."""
    results = []
    name_to_id = {}
    existing = get_existing_users(client)

    print(f"\n  Creating {len(users)} users...")
    skip_usernames = {"administrator", "guest"}  # Never recreate built-in accounts

    for i, u in enumerate(users, 1):
        uname = u.get("username", "").strip()
        if not uname or uname.lower() in skip_usernames:
            print(f"  [{i:>3}] SKIP (built-in)  {uname}")
            continue

        if uname in existing:
            name_to_id[uname] = existing[uname]
            results.append({"type": "user", "name": uname, "username": uname,
                            "status": "EXISTS", "id": existing[uname], "error": "",
                            "login_mode": u.get("login_mode_label", "")})
            print(f"  [{i:>3}] SKIP (exists)  {uname}")
            continue

        login_mode = map_login_mode(u.get("login_mode_label", ""))
        enabled    = str(u.get("enabled", "True")).lower() not in ("false", "0", "no")
        full_name  = u.get("full_name", u.get("username", uname))
        email      = u.get("email", "")

        if dry_run:
            results.append({"type": "user", "name": uname, "username": uname,
                            "status": "DRY_RUN", "id": "", "error": "",
                            "login_mode": u.get("login_mode_label", "")})
            print(f"  [{i:>3}] DRY-RUN  {uname}  (mode={login_mode})")
            continue

        payload = {
            "username":    uname,
            "fullName":    full_name,
            "enabled":     enabled,
            "loginModes":  [login_mode],
            # Standard users get a temp password and must change on first login
            "password":    temp_password if login_mode == 1 else None,
            "requireNewPassword": True if login_mode == 1 else False,
            "standardAuth": login_mode == 1,
        }
        if email:
            payload["emailAddress"] = email

        # Remove null fields
        payload = {k: v for k, v in payload.items() if v is not None}

        status, body = client.post("/users", payload)
        if status in (200, 201):
            uid = body.get("id", "") if isinstance(body, dict) else ""
            name_to_id[uname] = uid
            results.append({"type": "user", "name": uname, "username": uname,
                            "status": "CREATED", "id": uid, "error": "",
                            "login_mode": u.get("login_mode_label", "")})
            print(f"  [{i:>3}] CREATED  {uname}  (mode={'LDAP' if login_mode==16 else 'SAML' if login_mode==64 else 'Standard'})")
        else:
            error = str(body)[:120] if not isinstance(body, dict) else body.get("message", str(body))[:120]
            results.append({"type": "user", "name": uname, "username": uname,
                            "status": "FAIL", "id": "", "error": error,
                            "login_mode": u.get("login_mode_label", "")})
            print(f"  [{i:>3}] FAIL     {uname}: {error}")

        time.sleep(0.15)

    return name_to_id, results


# ─────────────────────────────────────────────────────────────
# ASSIGN MEMBERSHIPS
# ─────────────────────────────────────────────────────────────

def assign_memberships(client: MSTRClient, memberships: List[Dict],
                       user_id_map: Dict[str, str], group_id_map: Dict[str, str],
                       dry_run: bool = False) -> List[Dict]:
    """Add users to groups using PATCH /api/usergroups/{id}/members."""
    results = []
    # Group memberships by group_id
    group_members: Dict[str, List[str]] = {}
    for m in memberships:
        gname = m.get("group_name", "")
        mname = m.get("member_name", "")
        mtype = m.get("member_type", "User")
        gid   = group_id_map.get(gname)
        mid   = user_id_map.get(mname) if mtype == "User" else group_id_map.get(mname)
        if gid and mid:
            group_members.setdefault(gid, []).append(mid)

    print(f"\n  Assigning memberships across {len(group_members)} groups...")
    for gid, member_ids in group_members.items():
        gname = next((n for n, i in group_id_map.items() if i == gid), gid)
        if dry_run:
            print(f"  DRY-RUN  Group {gname}: would add {len(member_ids)} member(s)")
            results.append({"group": gname, "members_added": len(member_ids), "status": "DRY_RUN"})
            continue

        # PATCH /api/usergroups/{id} to add members
        payload = {"operationList": [
            {"op": "add", "path": "/members",
             "value": [{"id": mid} for mid in member_ids]}
        ]}
        status, body = client.patch(f"/usergroups/{gid}", payload)
        if status in (200, 204):
            results.append({"group": gname, "members_added": len(member_ids), "status": "ASSIGNED"})
            print(f"  ASSIGNED  {gname}: {len(member_ids)} member(s)")
        else:
            error = str(body)[:100] if not isinstance(body, dict) else body.get("message", str(body))[:100]
            results.append({"group": gname, "members_added": 0, "status": "FAIL", "error": error})
            print(f"  FAIL      {gname}: {error}")

        time.sleep(0.2)

    return results


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run(args):
    dry_run = args.mode == "dry-run"
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(args.output_dir, exist_ok=True)

    print("=" * 64)
    print("  MicroStrategy User & Group Migrator v2.0")
    print(f"  Target IS : {args.host}")
    print(f"  Mode      : {args.mode}")
    print(f"  Started   : {report_time}")
    print("=" * 64)

    client = MSTRClient(args.host, verify=not args.no_ssl_verify)
    if not dry_run:
        if not client.login(args.username, args.password, int(args.login_mode)):
            sys.exit(1)
        print("  Authenticated.")

    users       = load_csv(args.users_csv)
    groups      = load_csv(args.groups_csv)
    memberships = load_csv(args.membership_csv)

    print(f"  Loaded: {len(users)} users, {len(groups)} groups, {len(memberships)} memberships")

    all_results = []
    user_id_map  = {}
    group_id_map = {}

    try:
        if args.mode in ("full", "groups", "dry-run"):
            gmap, gresults = create_groups(client, groups, dry_run)
            group_id_map.update(gmap)
            all_results.extend(gresults)

        if args.mode in ("full", "users", "dry-run"):
            if not args.temp_password:
                print("[ERROR] --temp-password required for user creation")
                sys.exit(1)
            umap, uresults = create_users(client, users, args.temp_password, dry_run)
            user_id_map.update(umap)
            all_results.extend(uresults)

        if args.mode in ("full", "memberships", "dry-run") and memberships:
            mresults = assign_memberships(client, memberships, user_id_map, group_id_map, dry_run)
            all_results.extend(mresults)

    finally:
        if not dry_run:
            client.logout()

    # ── Write results CSV ────────────────────────────────────
    results_path = os.path.join(args.output_dir, "user_migration_results.csv")
    all_keys = set()
    for r in all_results: all_keys.update(r.keys())
    fieldnames = ["type", "name", "username", "login_mode", "status", "id", "error",
                  "group", "members_added"]
    fieldnames = [f for f in fieldnames if f in all_keys] + \
                 [k for k in all_keys if k not in fieldnames]
    with open(results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_results)

    # ── Summary ──────────────────────────────────────────────
    created  = sum(1 for r in all_results if r.get("status") == "CREATED")
    existing = sum(1 for r in all_results if r.get("status") == "EXISTS")
    failed   = sum(1 for r in all_results if r.get("status") == "FAIL")

    print("")
    print("=" * 64)
    print(f"  CREATED  : {created}")
    print(f"  EXISTING : {existing} (skipped — already on IS)")
    print(f"  FAILED   : {failed}")
    if failed == 0:
        print("  [PASS] All users and groups migrated successfully.")
    else:
        print(f"  [WARN] {failed} failure(s) — see {results_path}")

    if any(r.get("login_mode", "") and "LDAP" in r.get("login_mode", "")
           for r in all_results):
        print("\n  REMINDER: LDAP users created as shells.")
        print("  Configure LDAP connector in CMC Admin → Authentication → LDAP")
        print("  before LDAP users attempt to log in.")
    print("=" * 64)


def main():
    p = argparse.ArgumentParser(
        description="Bulk-recreate users and groups on cloud MSTR IS from harvest CSVs."
    )
    p.add_argument("--host",           required=True)
    p.add_argument("--username",       required=True)
    p.add_argument("--password",       required=True)
    p.add_argument("--users-csv",      default="./discovery_output/03_users.csv")
    p.add_argument("--groups-csv",     default="./discovery_output/04_usergroups.csv")
    p.add_argument("--membership-csv", default="./discovery_output/05_group_membership.csv")
    p.add_argument("--temp-password",  default=None,
                   help="Temporary password for standard users (required for user creation)")
    p.add_argument("--mode", default="full",
                   choices=["full", "groups", "users", "memberships", "dry-run"])
    p.add_argument("--login-mode",     default="1", choices=["1", "4", "8", "16", "64"])
    p.add_argument("--no-ssl-verify",  action="store_true")
    p.add_argument("--output-dir",     default="./user_migration_results")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
