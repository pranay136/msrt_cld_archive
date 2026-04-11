#!/usr/bin/env python3
"""
================================================================================
  MicroStrategy Metadata Harvester v2.0
  ======================================
  Comprehensive automated metadata collection for MicroStrategy on-prem instances.
  Designed for cloud migration discovery (Phase 1) and post-migration validation.

  USAGE:
    python mstr_harvester.py \
        --host https://your-mstr-server/MicroStrategyLibrary \
        --username Administrator \
        --password yourpassword \
        --output-dir ./discovery_output \
        [--all-projects] \
        [--project-id PROJECT_ID] \
        [--no-ssl-verify]

  OUTPUT:
    01_server_info.csv          - Server version, OS, build, ports
    02_projects.csv             - All projects and their properties
    03_users.csv                - All users with privileges and login info
    04_usergroups.csv           - All user groups and descriptions
    05_group_membership.csv     - User-to-group membership mappings
    06_security_roles.csv       - Security roles and privilege sets
    07_security_filters.csv     - Security filter assignments
    08_datasources.csv          - Database connection definitions
    09_reports.csv              - All reports per project
    10_documents_dossiers.csv   - All documents and dossiers per project
    11_metrics.csv              - All metric definitions
    12_attributes.csv           - All attribute definitions
    13_facts.csv                - All fact definitions
    14_filters.csv              - All filter definitions
    15_prompts.csv              - All prompt definitions
    16_schedules.csv            - All schedule definitions
    17_subscriptions.csv        - All subscription configurations
    18_caches.csv               - Cache statistics per project
    19_security_config.csv      - Auth modes, LDAP config, trusted auth
    20_email_config.csv         - SMTP and delivery configuration
    21_licenses.csv             - License and activation details
    SUMMARY_REPORT.txt          - Human-readable full instance summary

  REQUIREMENTS:
    Python 3.8+
    pip install requests pyyaml

  AUTHOR: MicroStrategy Admin Automation Toolkit
  VERSION: 2.0
  DATE: 2026-04-11
================================================================================
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

try:
    import requests
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("[ERROR] 'requests' library not found. Run: pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────
# MicroStrategy Object Type Codes
# ─────────────────────────────────────────────────────────────
MSTR_OBJECT_TYPES = {
    1:   "Filter",
    3:   "Report",
    4:   "Metric",
    5:   "Attribute",
    6:   "Fact",
    7:   "Hierarchy",
    8:   "Prompt",
    11:  "Subtotal",
    12:  "Transformation",
    13:  "Unknown",
    14:  "Consolidation",
    15:  "Custom Group",
    16:  "Function",
    17:  "Database Instance",
    18:  "Database Role",
    19:  "Database Login",
    20:  "Database Connection",
    21:  "Project",
    22:  "Link",
    23:  "Element",
    25:  "Shortcut to Report",
    26:  "Shortcut to Object",
    28:  "User",
    29:  "User Group",
    30:  "Table",
    32:  "Catalog",
    33:  "Catalog Definition",
    34:  "Report Writing Object",
    36:  "Script",
    38:  "Configuration",
    39:  "Document",
    47:  "Search",
    48:  "Search Result",
    55:  "Dossier",
    776: "Timezone",
    778: "Palette",
}

HARVEST_OBJECT_TYPES = [3, 4, 5, 6, 7, 8, 12, 14, 15, 39, 55]


# ─────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────
def setup_logging(output_dir: str) -> logging.Logger:
    log_path = os.path.join(output_dir, "harvester.log")
    logger = logging.getLogger("mstr_harvester")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s  %(message)s", "%H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ─────────────────────────────────────────────────────────────
# CSV Writer Helper
# ─────────────────────────────────────────────────────────────
def write_csv(filepath: str, rows: List[Dict], fieldnames: List[str] = None, logger=None):
    """Write a list of dicts to a CSV file."""
    if not rows:
        if logger:
            logger.warning(f"No data to write for {os.path.basename(filepath)}")
        # Still write a header-only CSV
        if fieldnames:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
        return

    if fieldnames is None:
        fieldnames = list(rows[0].keys())

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    if logger:
        logger.info(f"  Wrote {len(rows):,} rows → {os.path.basename(filepath)}")


# ─────────────────────────────────────────────────────────────
# MicroStrategy REST API Client
# ─────────────────────────────────────────────────────────────
class MSTRClient:
    """
    Thin wrapper around the MicroStrategy REST API v2.
    Handles authentication, pagination, and error handling.
    """

    def __init__(self, base_url: str, verify_ssl: bool = True, logger=None):
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api"
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.auth_token = None
        self.logger = logger or logging.getLogger("mstr_harvester")

    def _url(self, path: str) -> str:
        return f"{self.api_base}/{path.lstrip('/')}"

    def _headers(self, extra: Dict = None) -> Dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_token:
            h["X-MSTR-AuthToken"] = self.auth_token
        if extra:
            h.update(extra)
        return h

    def _get(self, path: str, params: Dict = None, project_id: str = None) -> Any:
        """Perform a GET request, return parsed JSON or None on error."""
        url = self._url(path)
        headers = self._headers()
        if project_id:
            headers["X-MSTR-ProjectID"] = project_id
        try:
            resp = self.session.get(url, headers=headers, params=params, timeout=60)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 204:
                return {}
            else:
                self.logger.debug(f"GET {path} → HTTP {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            self.logger.debug(f"GET {path} failed: {e}")
            return None

    def _get_paginated(self, path: str, result_key: str = None,
                       params: Dict = None, project_id: str = None,
                       page_size: int = 200) -> List:
        """
        Fetch all pages from a paginated endpoint.
        MicroStrategy paginates via offset/limit query params.
        """
        all_items = []
        offset = 0
        base_params = dict(params or {})
        base_params["limit"] = page_size

        while True:
            base_params["offset"] = offset
            data = self._get(path, params=base_params, project_id=project_id)

            if data is None:
                break

            # Handle both list response and dict-with-key response
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if result_key and result_key in data:
                    items = data[result_key]
                else:
                    # Try common keys
                    for key in ["result", "data", "items", "reports", "objects"]:
                        if key in data:
                            items = data[key]
                            break
                    else:
                        # Single page dict response
                        return [data]
            else:
                break

            if not items:
                break

            all_items.extend(items)

            if len(items) < page_size:
                break  # Last page

            offset += page_size
            time.sleep(0.1)  # Polite rate limiting

        return all_items

    def login(self, username: str, password: str, login_mode: int = 1) -> bool:
        """
        Authenticate with MicroStrategy IS.
        login_mode: 1=Standard, 16=LDAP, 8=SAML
        """
        url = self._url("/auth/login")
        payload = {
            "username": username,
            "password": password,
            "loginMode": login_mode,
            "applicationType": 35,  # REST API application type
        }
        try:
            resp = self.session.post(url, json=payload,
                                     headers={"Content-Type": "application/json"},
                                     verify=self.verify_ssl, timeout=30)
            if resp.status_code == 204:
                self.auth_token = resp.headers.get("X-MSTR-AuthToken")
                self.logger.info(f"  Authenticated successfully as '{username}'")
                return True
            else:
                self.logger.error(f"Login failed: HTTP {resp.status_code} — {resp.text[:300]}")
                return False
        except Exception as e:
            self.logger.error(f"Login error: {e}")
            return False

    def logout(self):
        """Terminate the REST API session."""
        try:
            self.session.post(self._url("/auth/logout"),
                              headers=self._headers(), verify=self.verify_ssl, timeout=10)
            self.logger.info("  Session terminated (logout successful)")
        except Exception:
            pass

    # ─── Server / Environment ────────────────────────────────

    def get_server_info(self) -> Dict:
        """Collect server version, node info, and service status."""
        data = {}

        # Server status / health
        health = self._get("/status")
        if health:
            data["server_status"] = health.get("webVersion", "")
            data["library_version"] = health.get("libraryVersion", "")

        # Intelligence Server info
        is_info = self._get("/iServer/info")
        if is_info:
            data.update({
                "is_version": is_info.get("version", ""),
                "is_build": is_info.get("build", ""),
                "is_port": is_info.get("port", ""),
                "is_hostname": is_info.get("name", ""),
                "is_platform": is_info.get("platform", ""),
            })

        # Cluster nodes
        nodes = self._get("/iServer/nodes")
        if nodes and isinstance(nodes, dict):
            node_list = nodes.get("nodes", [])
            data["cluster_nodes"] = len(node_list)
            data["cluster_node_names"] = "; ".join(
                [n.get("name", "") for n in node_list]
            )
            data["cluster_node_addresses"] = "; ".join(
                [n.get("address", "") for n in node_list]
            )

        return data

    def get_projects(self) -> List[Dict]:
        """List all projects on the Intelligence Server."""
        raw = self._get("/projects") or []
        projects = []
        for p in (raw if isinstance(raw, list) else []):
            projects.append({
                "id": p.get("id", ""),
                "name": p.get("name", ""),
                "description": p.get("description", ""),
                "status": p.get("status", ""),
                "alias": p.get("alias", ""),
                "owner_id": p.get("owner", {}).get("id", ""),
                "owner_name": p.get("owner", {}).get("name", ""),
                "default_timezone": p.get("defaultTimezone", {}).get("name", ""),
                "object_version": p.get("objectVersion", ""),
                "date_created": p.get("dateCreated", ""),
                "date_modified": p.get("dateModified", ""),
            })
        return projects

    # ─── Users & Groups ──────────────────────────────────────

    def get_users(self) -> List[Dict]:
        """Harvest all users with full detail."""
        raw = self._get_paginated("/users", page_size=500)
        users = []
        for u in raw:
            # Get detailed user info
            detail = self._get(f"/users/{u.get('id', '')}") or u
            enabled = detail.get("enabled", True)
            login_modes = detail.get("loginModes", [])
            login_mode_str = "+".join([str(m) for m in login_modes]) if login_modes else "1"

            # Map login mode numbers
            mode_map = {"1": "Standard", "16": "LDAP", "8": "Database",
                        "4": "Kerberos", "64": "SAML"}
            mode_labels = [mode_map.get(str(m), str(m)) for m in (login_modes or [1])]

            users.append({
                "id": u.get("id", ""),
                "username": detail.get("username", ""),
                "full_name": detail.get("fullName", detail.get("name", "")),
                "email": detail.get("emailAddress", ""),
                "enabled": enabled,
                "login_mode_code": login_mode_str,
                "login_mode_label": ", ".join(mode_labels),
                "description": detail.get("description", ""),
                "trust_id": detail.get("trustId", ""),
                "ldap_dn": detail.get("ldapDn", ""),
                "date_created": detail.get("dateCreated", ""),
                "date_modified": detail.get("dateModified", ""),
                "password_expiry": detail.get("passwordExpiry", ""),
                "standard_auth_allowed": detail.get("standardAuth", True),
                "home_server": detail.get("homeServer", ""),
                "initials": detail.get("initials", ""),
            })
            time.sleep(0.05)  # Avoid rate limiting on detail calls

        return users

    def get_usergroups(self) -> List[Dict]:
        """Harvest all user groups."""
        raw = self._get_paginated("/usergroups", page_size=200)
        groups = []
        for g in raw:
            groups.append({
                "id": g.get("id", ""),
                "name": g.get("name", ""),
                "description": g.get("description", ""),
                "members_count": len(g.get("members", [])),
                "date_created": g.get("dateCreated", ""),
                "date_modified": g.get("dateModified", ""),
            })
        return groups

    def get_group_memberships(self) -> List[Dict]:
        """Build a flat user→group membership map."""
        memberships = []
        groups_raw = self._get_paginated("/usergroups", page_size=200)
        for g in groups_raw:
            gid = g.get("id", "")
            gname = g.get("name", "")
            detail = self._get(f"/usergroups/{gid}") or g
            members = detail.get("members", [])
            for m in members:
                memberships.append({
                    "group_id": gid,
                    "group_name": gname,
                    "member_id": m.get("id", ""),
                    "member_name": m.get("name", ""),
                    "member_type": "User" if m.get("type") == 34 else "Group",
                })
        return memberships

    def get_security_roles(self) -> List[Dict]:
        """List all security roles and their project assignments."""
        raw = self._get("/securityRoles") or {}
        roles_list = raw if isinstance(raw, list) else raw.get("securityRoles", [])
        roles = []
        for r in roles_list:
            # Get privilege details
            privs = r.get("privileges", [])
            priv_names = "; ".join([p.get("name", str(p)) if isinstance(p, dict) else str(p)
                                    for p in privs[:20]])  # Cap for CSV readability
            roles.append({
                "id": r.get("id", ""),
                "name": r.get("name", ""),
                "description": r.get("description", ""),
                "privilege_count": len(privs),
                "top_privileges": priv_names,
                "date_created": r.get("dateCreated", ""),
                "date_modified": r.get("dateModified", ""),
            })
        return roles

    def get_security_filters(self, project_id: str, project_name: str) -> List[Dict]:
        """List security filters defined in a project."""
        raw = self._get_paginated(
            f"/objects",
            params={"type": 1, "subtype": 1025},  # subtype 1025 = security filter
            project_id=project_id,
            page_size=200
        )
        filters = []
        for f in raw:
            filters.append({
                "project_id": project_id,
                "project_name": project_name,
                "id": f.get("id", ""),
                "name": f.get("name", ""),
                "description": f.get("description", ""),
                "owner_id": f.get("owner", {}).get("id", ""),
                "owner_name": f.get("owner", {}).get("name", ""),
                "date_created": f.get("dateCreated", ""),
                "date_modified": f.get("dateModified", ""),
                "path": f.get("ancestors", [{}])[-1].get("name", "") if f.get("ancestors") else "",
            })
        return filters

    # ─── Database Connections ─────────────────────────────────

    def get_datasources(self) -> List[Dict]:
        """Harvest all datasource/DB connection definitions."""
        raw = self._get_paginated("/datasources", page_size=200)
        datasources = []
        for d in raw:
            ds_type = d.get("datasourceType", {})
            db_info = d.get("database", {})
            dbDriver = db_info.get("driver", {})

            datasources.append({
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "description": d.get("description", ""),
                "datasource_type": ds_type.get("name", "") if isinstance(ds_type, dict) else str(ds_type),
                "db_type": db_info.get("type", {}).get("name", "") if isinstance(db_info.get("type"), dict) else "",
                "host": db_info.get("host", ""),
                "port": db_info.get("port", ""),
                "database_name": db_info.get("databaseName", ""),
                "driver_name": dbDriver.get("name", "") if isinstance(dbDriver, dict) else "",
                "connection_string": d.get("connectionString", ""),
                "login_mode": d.get("loginMode", ""),
                "default_login_id": d.get("defaultLogin", {}).get("name", ""),
                "charset": db_info.get("charset", ""),
                "owner_id": d.get("owner", {}).get("id", ""),
                "owner_name": d.get("owner", {}).get("name", ""),
                "date_created": d.get("dateCreated", ""),
                "date_modified": d.get("dateModified", ""),
                "acl_restricted": str(bool(d.get("acl"))),
            })
        return datasources

    # ─── Object Inventory ────────────────────────────────────

    def get_objects_by_type(self, object_type: int, project_id: str,
                             project_name: str) -> List[Dict]:
        """
        Generic object harvester for any MSTR object type.
        Returns a standardized list of object records.
        """
        type_name = MSTR_OBJECT_TYPES.get(object_type, f"Type_{object_type}")
        raw = self._get_paginated(
            "/objects",
            params={"type": object_type},
            project_id=project_id,
            page_size=500
        )

        objects = []
        for obj in raw:
            # Build ancestor path string
            ancestors = obj.get("ancestors", [])
            path_parts = [a.get("name", "") for a in ancestors if a.get("name") not in ("", "My Reports")]
            path_str = " > ".join(path_parts) if path_parts else "/"

            objects.append({
                "object_type_code": object_type,
                "object_type_name": type_name,
                "project_id": project_id,
                "project_name": project_name,
                "id": obj.get("id", ""),
                "name": obj.get("name", ""),
                "description": obj.get("description", ""),
                "path": path_str,
                "subtype": obj.get("subtype", ""),
                "owner_id": obj.get("owner", {}).get("id", ""),
                "owner_name": obj.get("owner", {}).get("name", ""),
                "version": obj.get("objectVersion", obj.get("version", "")),
                "date_created": obj.get("dateCreated", ""),
                "date_modified": obj.get("dateModified", ""),
                "acl_restricted": str(bool(obj.get("acl"))),
                "hidden": obj.get("hidden", False),
                "certifications": obj.get("certifications", []),
            })

        return objects

    # ─── Schedules & Subscriptions ───────────────────────────

    def get_schedules(self) -> List[Dict]:
        """List all schedules defined on the IS."""
        raw = self._get_paginated("/schedules", page_size=200)
        schedules = []
        for s in raw:
            stype = s.get("scheduleType", "")
            time_info = s.get("time", {})
            event_info = s.get("event", {})

            schedules.append({
                "id": s.get("id", ""),
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "schedule_type": stype,
                "enabled": s.get("enabled", True),
                "start_date": s.get("startDate", ""),
                "stop_date": s.get("stopDate", ""),
                "next_delivery": s.get("nextDeliveryTime", ""),
                "recurrence_type": time_info.get("scheduleFrequency", ""),
                "recurrence_detail": json.dumps(time_info, ensure_ascii=False)[:200] if time_info else "",
                "event_id": event_info.get("id", "") if isinstance(event_info, dict) else "",
                "event_name": event_info.get("name", "") if isinstance(event_info, dict) else "",
                "date_created": s.get("dateCreated", ""),
                "date_modified": s.get("dateModified", ""),
            })
        return schedules

    def get_subscriptions(self, project_id: str, project_name: str) -> List[Dict]:
        """List all subscriptions in a project."""
        raw = self._get_paginated(
            "/subscriptions",
            project_id=project_id,
            page_size=200
        )
        subs = []
        for s in raw:
            delivery = s.get("delivery", {})
            delivery_type = delivery.get("mode", "")
            recipients = s.get("recipients", [])
            recipient_names = "; ".join([r.get("name", r.get("id", "")) for r in recipients[:10]])

            contents = s.get("contents", [])
            content_names = "; ".join([c.get("name", "") for c in contents[:5]])

            subs.append({
                "project_id": project_id,
                "project_name": project_name,
                "id": s.get("id", ""),
                "name": s.get("name", ""),
                "owner_id": s.get("owner", {}).get("id", ""),
                "owner_name": s.get("owner", {}).get("name", ""),
                "delivery_type": delivery_type,
                "delivery_format": delivery.get("contentType", ""),
                "schedule_id": s.get("schedules", [{}])[0].get("id", "") if s.get("schedules") else "",
                "schedule_name": s.get("schedules", [{}])[0].get("name", "") if s.get("schedules") else "",
                "recipient_count": len(recipients),
                "recipients": recipient_names,
                "content_count": len(contents),
                "content_names": content_names,
                "enabled": s.get("enabled", True),
                "date_created": s.get("dateCreated", ""),
                "date_modified": s.get("dateModified", ""),
            })
        return subs

    # ─── Caches ──────────────────────────────────────────────

    def get_caches(self, project_id: str, project_name: str) -> List[Dict]:
        """Collect cache statistics for a project."""
        cache_data = []

        for cache_type in ["report", "element", "object"]:
            raw = self._get(f"/caches/{cache_type}s",
                            params={"projectId": project_id}) or {}
            if isinstance(raw, dict):
                caches = raw.get("caches", [])
                cache_data.append({
                    "project_id": project_id,
                    "project_name": project_name,
                    "cache_type": cache_type,
                    "total_count": len(caches),
                    "hit_count": sum(c.get("hitCount", 0) for c in caches),
                    "size_kb": sum(c.get("size", 0) for c in caches),
                    "oldest_cache_date": min((c.get("dateCreated", "") for c in caches), default=""),
                    "newest_cache_date": max((c.get("dateCreated", "") for c in caches), default=""),
                })
            else:
                cache_data.append({
                    "project_id": project_id,
                    "project_name": project_name,
                    "cache_type": cache_type,
                    "total_count": 0,
                    "hit_count": 0,
                    "size_kb": 0,
                    "oldest_cache_date": "",
                    "newest_cache_date": "",
                })

        return cache_data

    # ─── Security & Auth Config ──────────────────────────────

    def get_security_config(self) -> List[Dict]:
        """Collect authentication and security configuration."""
        config = []

        # Trust relationship
        trust = self._get("/securitySettings/trustRelationship") or {}
        config.append({
            "setting_category": "Trust Relationship",
            "setting_name": "Trusted Auth Enabled",
            "setting_value": str(trust.get("enabled", "")),
            "setting_detail": json.dumps(trust)[:300],
        })

        # LDAP config
        ldap = self._get("/ldap") or {}
        ldap_servers = ldap.get("ldapServers", [])
        for srv in ldap_servers:
            config.append({
                "setting_category": "LDAP",
                "setting_name": srv.get("name", "LDAP Server"),
                "setting_value": srv.get("host", ""),
                "setting_detail": json.dumps({
                    "host": srv.get("host"),
                    "port": srv.get("port"),
                    "ssl": srv.get("ssl", False),
                    "bindDN": srv.get("bindDn", ""),
                    "searchBase": srv.get("searchBase", ""),
                    "userFilter": srv.get("userFilter", ""),
                    "groupFilter": srv.get("groupFilter", ""),
                    "usernameAttr": srv.get("usernameAttribute", ""),
                    "emailAttr": srv.get("emailAttribute", ""),
                }, ensure_ascii=False)[:500],
            })

        if not ldap_servers:
            config.append({
                "setting_category": "LDAP",
                "setting_name": "LDAP Configured",
                "setting_value": "No",
                "setting_detail": "No LDAP servers configured or accessible via API",
            })

        # Kerberos / SAML settings
        for auth_type in ["kerberos", "saml"]:
            auth_data = self._get(f"/{auth_type}") or {}
            if auth_data:
                config.append({
                    "setting_category": auth_type.upper(),
                    "setting_name": f"{auth_type.upper()} Configuration",
                    "setting_value": "Configured" if auth_data else "Not Configured",
                    "setting_detail": json.dumps(auth_data)[:300],
                })

        return config

    def get_email_config(self) -> List[Dict]:
        """Collect email/SMTP server configuration."""
        data = self._get("/emailSettings") or {}
        rows = []
        if data:
            rows.append({
                "setting_name": "SMTP Server",
                "value": data.get("hostName", ""),
                "detail": str(data.get("portNumber", "")),
            })
            rows.append({
                "setting_name": "From Address",
                "value": data.get("senderDisplayName", ""),
                "detail": data.get("senderAddress", ""),
            })
            rows.append({
                "setting_name": "SMTP Auth Enabled",
                "value": str(data.get("useAuth", False)),
                "detail": "",
            })
            rows.append({
                "setting_name": "SSL/TLS",
                "value": str(data.get("useSSL", False)),
                "detail": f"Port: {data.get('portNumber', 25)}",
            })
        else:
            rows.append({
                "setting_name": "Email Config",
                "value": "Not accessible via REST API",
                "detail": "Check MicroStrategy System Admin → Email Settings manually",
            })
        return rows

    def get_licenses(self) -> List[Dict]:
        """Collect license and activation information."""
        data = self._get("/license") or {}
        activations = data.get("activations", []) if isinstance(data, dict) else []
        rows = []
        if activations:
            for act in activations:
                rows.append({
                    "license_key": act.get("key", ""),
                    "product": act.get("product", {}).get("name", "") if isinstance(act.get("product"), dict) else "",
                    "license_type": act.get("licenseType", ""),
                    "named_users": act.get("namedUsers", ""),
                    "cpu_count": act.get("cpuCount", ""),
                    "expiry_date": act.get("expiryDate", ""),
                    "activated": act.get("activated", ""),
                    "activation_date": act.get("activationDate", ""),
                    "version": act.get("version", ""),
                })
        else:
            rows.append({
                "license_key": "N/A",
                "product": "License info not accessible via REST API",
                "license_type": "Check MicroStrategy Administrator Portal",
                "named_users": "", "cpu_count": "", "expiry_date": "",
                "activated": "", "activation_date": "", "version": "",
            })
        return rows


# ─────────────────────────────────────────────────────────────
# Summary Report Generator
# ─────────────────────────────────────────────────────────────
def generate_summary_report(
    output_dir: str,
    server_info: Dict,
    projects: List[Dict],
    users: List[Dict],
    groups: List[Dict],
    memberships: List[Dict],
    security_roles: List[Dict],
    datasources: List[Dict],
    all_objects: Dict[str, List],
    schedules: List[Dict],
    subscriptions: List[Dict],
    caches: List[Dict],
    harvest_time: str,
    base_url: str,
):
    """Generate a human-readable SUMMARY_REPORT.txt."""
    lines = []
    sep = "=" * 80
    thin = "-" * 80

    def section(title):
        lines.append("")
        lines.append(sep)
        lines.append(f"  {title}")
        lines.append(sep)

    def subsection(title):
        lines.append("")
        lines.append(f"  ── {title}")
        lines.append(thin)

    def row(label, value, width=40):
        lines.append(f"  {label:<{width}} {value}")

    lines.append(sep)
    lines.append("  MICROSTRATEGY INSTANCE DISCOVERY REPORT")
    lines.append("  Generated by MicroStrategy Metadata Harvester v2.0")
    lines.append(sep)
    lines.append(f"  Instance URL  : {base_url}")
    lines.append(f"  Harvest Time  : {harvest_time}")
    lines.append(sep)

    # Server info
    section("1. SERVER & INFRASTRUCTURE")
    if server_info:
        row("Hostname", server_info.get("is_hostname", "N/A"))
        row("IS Version", server_info.get("is_version", "N/A"))
        row("IS Build", server_info.get("is_build", "N/A"))
        row("Platform", server_info.get("is_platform", "N/A"))
        row("IS Port", str(server_info.get("is_port", "N/A")))
        row("Library Version", server_info.get("library_version", "N/A"))
        row("Server Status", server_info.get("server_status", "N/A"))
        row("Cluster Nodes", str(server_info.get("cluster_nodes", 1)))
        if server_info.get("cluster_node_names"):
            row("Node Names", server_info.get("cluster_node_names", ""))
    else:
        lines.append("  [!] Server info not accessible via REST API — check manually")

    # Projects
    section("2. PROJECTS")
    row("Total Projects", str(len(projects)))
    lines.append("")
    if projects:
        lines.append(f"  {'Name':<40} {'ID':<40} {'Status'}")
        lines.append(f"  {'-'*40} {'-'*40} {'-'*10}")
        for p in projects:
            lines.append(f"  {p['name']:<40} {p['id']:<40} {p.get('status', '')}")

    # Users & Groups
    section("3. USERS & GROUPS")
    row("Total Users", str(len(users)))

    # User type breakdown
    standard_users = [u for u in users if "Standard" in u.get("login_mode_label", "Standard")]
    ldap_users = [u for u in users if "LDAP" in u.get("login_mode_label", "")]
    saml_users = [u for u in users if "SAML" in u.get("login_mode_label", "")]
    disabled_users = [u for u in users if not u.get("enabled", True)]

    row("  Standard Auth Users", str(len(standard_users)))
    row("  LDAP Users", str(len(ldap_users)))
    row("  SAML Users", str(len(saml_users)))
    row("  Disabled Users", str(len(disabled_users)))
    row("Total User Groups", str(len(groups)))
    row("Total Memberships", str(len(memberships)))
    row("Total Security Roles", str(len(security_roles)))

    # Datasources
    section("4. DATABASE CONNECTIONS")
    row("Total Datasources", str(len(datasources)))
    lines.append("")
    if datasources:
        db_types = {}
        for ds in datasources:
            dt = ds.get("db_type", "Unknown") or "Unknown"
            db_types[dt] = db_types.get(dt, 0) + 1
        for dt, count in sorted(db_types.items()):
            row(f"  {dt}", str(count))
        lines.append("")
        lines.append(f"  {'Name':<35} {'DB Type':<20} {'Host':<30} {'DB Name'}")
        lines.append(f"  {'-'*35} {'-'*20} {'-'*30} {'-'*20}")
        for ds in datasources:
            lines.append(
                f"  {ds['name'][:34]:<35} "
                f"{ds.get('db_type', '')[:19]:<20} "
                f"{ds.get('host', '')[:29]:<30} "
                f"{ds.get('database_name', '')}"
            )

    # Object Inventory
    section("5. OBJECT INVENTORY (by Project)")
    grand_total = 0
    for proj_name, obj_list in all_objects.items():
        if not obj_list:
            continue
        subsection(f"Project: {proj_name}")
        type_counts = {}
        for obj in obj_list:
            tn = obj.get("object_type_name", "Unknown")
            type_counts[tn] = type_counts.get(tn, 0) + 1
        proj_total = sum(type_counts.values())
        grand_total += proj_total
        row("Total Objects", str(proj_total))
        for tn, cnt in sorted(type_counts.items()):
            row(f"  {tn}", str(cnt))

    lines.append("")
    row("GRAND TOTAL OBJECTS", str(grand_total))

    # Schedules & Subscriptions
    section("6. SCHEDULES & SUBSCRIPTIONS")
    row("Total Schedules", str(len(schedules)))
    enabled_scheds = [s for s in schedules if s.get("enabled", True)]
    row("  Enabled Schedules", str(len(enabled_scheds)))
    row("  Disabled Schedules", str(len(schedules) - len(enabled_scheds)))
    row("Total Subscriptions", str(len(subscriptions)))

    # Delivery type breakdown
    delivery_types = {}
    for sub in subscriptions:
        dt = sub.get("delivery_type", "Unknown")
        delivery_types[dt] = delivery_types.get(dt, 0) + 1
    for dt, cnt in sorted(delivery_types.items()):
        row(f"  Delivery: {dt}", str(cnt))

    # Cache summary
    section("7. CACHE SUMMARY")
    for c in caches:
        lines.append(
            f"  [{c['project_name']}] {c['cache_type'].upper()} cache: "
            f"{c['total_count']:,} entries, {c['size_kb']:,} KB"
        )

    # Risk flags
    section("8. MIGRATION RISK FLAGS  [Review these carefully!]")
    risks = []

    if ldap_users:
        risks.append(f"[HIGH] {len(ldap_users)} LDAP users — LDAP connector must be configured in cloud before user migration.")

    if saml_users:
        risks.append(f"[HIGH] {len(saml_users)} SAML users — SAML IdP must be configured in cloud environment.")

    freeform_count = sum(
        1 for objects in all_objects.values()
        for obj in objects
        if obj.get("subtype") in [776, 777]  # Freeform SQL subtypes
    )
    if freeform_count:
        risks.append(f"[MEDIUM] {freeform_count} potential Freeform SQL objects detected — require manual review in cloud.")

    if not server_info.get("is_version"):
        risks.append("[INFO] Could not determine IS version via REST API — verify version compatibility manually.")

    legacy_db_types = ["Access", "MySQL 3.", "Oracle 9", "SQL Server 2008"]
    for ds in datasources:
        db_type = ds.get("db_type", "")
        for legacy in legacy_db_types:
            if legacy.lower() in db_type.lower():
                risks.append(f"[MEDIUM] Legacy DB driver detected: {ds['name']} ({db_type}) — verify cloud driver support.")

    if len(projects) > 10:
        risks.append(f"[INFO] {len(projects)} projects found — consider phased migration by project complexity.")

    if grand_total > 50000:
        risks.append(f"[HIGH] {grand_total:,} total objects — large instance. Expect extended migration window.")
    elif grand_total > 10000:
        risks.append(f"[MEDIUM] {grand_total:,} total objects — medium instance. Plan for 4–8 hour migration window.")

    if not risks:
        risks.append("[OK] No major risk flags detected automatically. Review CSVs for detailed analysis.")

    for r in risks:
        lines.append(f"  {r}")

    # Footer
    section("END OF DISCOVERY REPORT")
    lines.append(f"  Files written to : {output_dir}")
    lines.append(f"  Harvest completed: {harvest_time}")
    lines.append(f"  Total data files : 21 CSVs + this report")
    lines.append("")
    lines.append("  NEXT STEPS:")
    lines.append("  1. Feed this file to an AI assistant (Claude, GPT-4) for risk analysis.")
    lines.append("  2. Review each CSV for detailed object-level data.")
    lines.append("  3. Run mstr_validator.py post-migration to compare on-prem vs cloud.")
    lines.append(sep)

    report_path = os.path.join(output_dir, "SUMMARY_REPORT.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


# ─────────────────────────────────────────────────────────────
# Main Harvester Orchestrator
# ─────────────────────────────────────────────────────────────
def run_harvest(args):
    # Setup output directory
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    logger = setup_logging(output_dir)
    harvest_start = datetime.now()
    harvest_time = harvest_start.strftime("%Y-%m-%d %H:%M:%S")

    logger.info("=" * 60)
    logger.info("  MicroStrategy Metadata Harvester v2.0")
    logger.info(f"  Target : {args.host}")
    logger.info(f"  Output : {output_dir}")
    logger.info(f"  Started: {harvest_time}")
    logger.info("=" * 60)

    client = MSTRClient(args.host, verify_ssl=not args.no_ssl_verify, logger=logger)

    # ── Authentication ────────────────────────────────────────
    logger.info("\n[1/10] Authenticating...")
    if not client.login(args.username, args.password,
                        login_mode=int(args.login_mode)):
        logger.error("Authentication failed. Check credentials and host URL.")
        sys.exit(1)

    try:
        # ── Server Info ───────────────────────────────────────
        logger.info("[2/10] Collecting server information...")
        server_info = client.get_server_info()
        server_rows = [{"field": k, "value": str(v)} for k, v in server_info.items()]
        write_csv(os.path.join(output_dir, "01_server_info.csv"),
                  server_rows, ["field", "value"], logger)

        # ── Projects ──────────────────────────────────────────
        logger.info("[3/10] Collecting projects...")
        projects = client.get_projects()
        write_csv(os.path.join(output_dir, "02_projects.csv"), projects, logger=logger)
        logger.info(f"  Found {len(projects)} projects")

        # Determine which projects to harvest
        if args.project_id:
            target_projects = [p for p in projects if p["id"] == args.project_id]
            if not target_projects:
                logger.error(f"Project ID '{args.project_id}' not found!")
                target_projects = []
        elif args.all_projects:
            target_projects = projects
        else:
            target_projects = projects[:3]  # Default: first 3 projects
            logger.warning(f"  Harvesting first 3 projects only. Use --all-projects for all {len(projects)} projects.")

        # ── Users & Groups ────────────────────────────────────
        logger.info("[4/10] Collecting users (this may take a few minutes)...")
        users = client.get_users()
        write_csv(os.path.join(output_dir, "03_users.csv"), users, logger=logger)

        logger.info("[5/10] Collecting user groups...")
        groups = client.get_usergroups()
        write_csv(os.path.join(output_dir, "04_usergroups.csv"), groups, logger=logger)

        memberships = client.get_group_memberships()
        write_csv(os.path.join(output_dir, "05_group_membership.csv"), memberships, logger=logger)

        security_roles = client.get_security_roles()
        write_csv(os.path.join(output_dir, "06_security_roles.csv"), security_roles, logger=logger)

        # ── Datasources ───────────────────────────────────────
        logger.info("[6/10] Collecting database connections...")
        datasources = client.get_datasources()
        write_csv(os.path.join(output_dir, "08_datasources.csv"), datasources, logger=logger)

        # ── Per-Project Data ──────────────────────────────────
        logger.info(f"[7/10] Collecting per-project objects ({len(target_projects)} projects)...")

        all_security_filters = []
        all_reports = []
        all_docs_dossiers = []
        all_metrics = []
        all_attributes = []
        all_facts = []
        all_filters = []
        all_prompts = []
        all_misc_objects = []
        all_subscriptions = []
        all_caches = []
        all_objects_by_project = {}

        type_to_list = {
            3:  all_reports,           # Reports
            4:  all_metrics,           # Metrics
            5:  all_attributes,        # Attributes
            6:  all_facts,             # Facts
            7:  all_misc_objects,      # Hierarchies
            8:  all_prompts,           # Prompts
            12: all_misc_objects,      # Transformations
            14: all_misc_objects,      # Consolidations
            15: all_misc_objects,      # Custom Groups
            39: all_docs_dossiers,     # Documents
            55: all_docs_dossiers,     # Dossiers
        }

        for i, proj in enumerate(target_projects):
            pid = proj["id"]
            pname = proj["name"]
            logger.info(f"  [{i+1}/{len(target_projects)}] Project: {pname}")

            proj_objects = []

            for obj_type in HARVEST_OBJECT_TYPES:
                type_name = MSTR_OBJECT_TYPES.get(obj_type, f"Type_{obj_type}")
                objs = client.get_objects_by_type(obj_type, pid, pname)
                target_list = type_to_list.get(obj_type, all_misc_objects)
                target_list.extend(objs)
                proj_objects.extend(objs)
                logger.info(f"    {type_name}: {len(objs):,}")

            all_objects_by_project[pname] = proj_objects

            # Security filters
            sf = client.get_security_filters(pid, pname)
            all_security_filters.extend(sf)

            # Subscriptions
            subs = client.get_subscriptions(pid, pname)
            all_subscriptions.extend(subs)

            # Caches
            cache_stats = client.get_caches(pid, pname)
            all_caches.extend(cache_stats)

        # Write object CSVs
        logger.info("[8/10] Writing object inventory CSVs...")
        obj_files = [
            ("09_reports.csv", all_reports),
            ("10_documents_dossiers.csv", all_docs_dossiers),
            ("11_metrics.csv", all_metrics),
            ("12_attributes.csv", all_attributes),
            ("13_facts.csv", all_facts),
            ("14_filters.csv", all_filters),
            ("15_prompts.csv", all_prompts),
        ]
        for fname, data in obj_files:
            write_csv(os.path.join(output_dir, fname), data, logger=logger)

        write_csv(os.path.join(output_dir, "07_security_filters.csv"), all_security_filters, logger=logger)

        # ── Schedules & Subscriptions ─────────────────────────
        logger.info("[9/10] Collecting schedules and subscriptions...")
        schedules = client.get_schedules()
        write_csv(os.path.join(output_dir, "16_schedules.csv"), schedules, logger=logger)
        write_csv(os.path.join(output_dir, "17_subscriptions.csv"), all_subscriptions, logger=logger)
        write_csv(os.path.join(output_dir, "18_caches.csv"), all_caches, logger=logger)

        # ── Configuration & Licensing ─────────────────────────
        logger.info("[10/10] Collecting security config, email settings, and licenses...")
        security_config = client.get_security_config()
        write_csv(os.path.join(output_dir, "19_security_config.csv"), security_config, logger=logger)

        email_config = client.get_email_config()
        write_csv(os.path.join(output_dir, "20_email_config.csv"), email_config, logger=logger)

        licenses = client.get_licenses()
        write_csv(os.path.join(output_dir, "21_licenses.csv"), licenses, logger=logger)

        # ── Summary Report ────────────────────────────────────
        logger.info("\nGenerating SUMMARY_REPORT.txt...")
        report_path = generate_summary_report(
            output_dir=output_dir,
            server_info=server_info,
            projects=projects,
            users=users,
            groups=groups,
            memberships=memberships,
            security_roles=security_roles,
            datasources=datasources,
            all_objects=all_objects_by_project,
            schedules=schedules,
            subscriptions=all_subscriptions,
            caches=all_caches,
            harvest_time=harvest_time,
            base_url=args.host,
        )

        # ── Done ─────────────────────────────────────────────
        elapsed = (datetime.now() - harvest_start).total_seconds()
        total_objects = sum(len(v) for v in all_objects_by_project.values())

        logger.info("")
        logger.info("=" * 60)
        logger.info("  HARVEST COMPLETE")
        logger.info(f"  Duration  : {elapsed:.1f} seconds")
        logger.info(f"  Projects  : {len(target_projects)}")
        logger.info(f"  Users     : {len(users)}")
        logger.info(f"  Groups    : {len(groups)}")
        logger.info(f"  Objects   : {total_objects:,}")
        logger.info(f"  Schedules : {len(schedules)}")
        logger.info(f"  Files     : {output_dir}/")
        logger.info("=" * 60)
        logger.info(f"\n  NEXT: Feed SUMMARY_REPORT.txt to an AI assistant for risk analysis.")

    finally:
        client.logout()


# ─────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="MicroStrategy Metadata Harvester — Discovery automation for cloud migration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full discovery of all projects
  python mstr_harvester.py --host https://mstr.company.com/MicroStrategyLibrary \\
      --username Administrator --password Admin123 --all-projects

  # Single project only
  python mstr_harvester.py --host https://mstr.company.com/MicroStrategyLibrary \\
      --username Administrator --password Admin123 \\
      --project-id B7CA92F04B9FAE8D941C3E9B7E0CD754

  # Skip SSL verification (self-signed certs on dev instances)
  python mstr_harvester.py --host https://mstr-dev.company.com/MicroStrategyLibrary \\
      --username admin --password admin --all-projects --no-ssl-verify

  # LDAP authentication
  python mstr_harvester.py --host https://mstr.company.com/MicroStrategyLibrary \\
      --username jsmith --password ldappassword --login-mode 16 --all-projects
"""
    )
    parser.add_argument("--host", required=True,
                        help="MicroStrategy Library base URL (e.g. https://server/MicroStrategyLibrary)")
    parser.add_argument("--username", required=True, help="MicroStrategy admin username")
    parser.add_argument("--password", required=True, help="MicroStrategy admin password")
    parser.add_argument("--output-dir", default="./mstr_discovery",
                        help="Directory to write output CSV files (default: ./mstr_discovery)")
    parser.add_argument("--all-projects", action="store_true",
                        help="Harvest all projects (default: first 3 only)")
    parser.add_argument("--project-id", default=None,
                        help="Harvest a single specific project by ID")
    parser.add_argument("--no-ssl-verify", action="store_true",
                        help="Disable SSL certificate verification (for self-signed certs)")
    parser.add_argument("--login-mode", default="1",
                        choices=["1", "4", "8", "16", "64"],
                        help="Login mode: 1=Standard, 4=Kerberos, 8=Database, 16=LDAP, 64=SAML (default: 1)")

    args = parser.parse_args()
    run_harvest(args)


if __name__ == "__main__":
    main()
