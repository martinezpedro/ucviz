"""Principal-type resolver — label a grantee as user / group / service principal.

UC's ``information_schema`` privilege views give only a ``grantee`` *string*, not
its identity type. To show "who has access" broken down by user vs group vs
service principal, we resolve types against the account's SCIM directory via the
SDK (``WorkspaceClient``), then fall back to a name-pattern heuristic when SCIM
can't be read (SP identity, limited scope) — mirroring how ``db.current_user``
degrades gracefully.

We deliberately list only **groups** and **service principals** (usually a
manageable count) rather than every account user (which can be very large): any
grantee that isn't a known group/SP and looks like an email is treated as a
user. No group-to-member expansion.
"""

from __future__ import annotations

import re

TYPE_USER = "user"
TYPE_GROUP = "group"
TYPE_SP = "service_principal"
TYPE_UNKNOWN = "unknown"

# Grantees that are UC built-in/broad principals — always groups.
_BUILTIN_GROUPS = {"account users", "users", "public", "admins",
                   "account admins", "metastore_admins"}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def list_directory_principals() -> list[dict]:
    """Groups + service principals from SCIM as ``{name, grantee, type}``.

    ``grantee`` is the string UC uses in grants: a group's display name, or a
    service principal's application id. Never raises — returns what it could
    read (possibly empty) on any SDK/permission error.
    """
    out: list[dict] = []
    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        try:
            for g in w.groups.list(attributes="displayName"):
                if g.display_name:
                    out.append({"name": g.display_name,
                                "grantee": g.display_name, "type": TYPE_GROUP})
        except Exception:
            pass
        try:
            for sp in w.service_principals.list(
                attributes="applicationId,displayName"
            ):
                if sp.application_id:
                    out.append({"name": sp.display_name or sp.application_id,
                                "grantee": sp.application_id, "type": TYPE_SP})
        except Exception:
            pass
    except Exception:
        pass
    return out


def build_principal_lookup(directory: list[dict] | None = None) -> dict:
    """Return ``{normalized_grantee: type}`` from account groups + SPs.

    Pass ``directory`` (from ``list_directory_principals``) to skip a second
    SCIM listing. Never raises: on any SDK/permission error it returns whatever
    it managed to collect (possibly empty), and ``classify_principal`` falls
    back to heuristics.
    """
    lookup: dict[str, str] = {g: TYPE_GROUP for g in _BUILTIN_GROUPS}
    if directory is None:
        directory = list_directory_principals()
    for p in directory:
        lookup[p["grantee"].lower()] = p["type"]
        if p["type"] == TYPE_SP:
            lookup[p["name"].lower()] = TYPE_SP
    return lookup


def user_groups(user_name: str) -> list[str]:
    """Direct group memberships of one user (SCIM).

    Nested groups are not expanded, and the implicit ``account users`` group
    is left to the caller (it matches most of a shared metastore). Returns
    ``[]`` when SCIM can't be read.
    """
    groups: list[str] = []
    if not user_name:
        return groups
    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        safe = user_name.replace('"', "")
        for u in w.users.list(filter=f'userName eq "{safe}"',
                              attributes="groups"):
            for g in u.groups or []:
                if g.display and g.display not in groups:
                    groups.append(g.display)
    except Exception:
        pass
    return groups


def classify_principal(grantee, lookup: dict | None = None) -> str:
    """Classify ``grantee`` as user / group / service_principal / unknown.

    Uses the SCIM-derived ``lookup`` first, then a name heuristic:
    a UUID-shaped name is a service principal, an ``@``-bearing name is a user,
    and anything else is treated as a group.
    """
    if grantee is None:
        return TYPE_UNKNOWN
    name = str(grantee).strip()
    if not name:
        return TYPE_UNKNOWN
    key = name.lower()
    if lookup and key in lookup:
        return lookup[key]
    if _UUID_RE.match(name):
        return TYPE_SP
    if "@" in name:
        return TYPE_USER
    return TYPE_GROUP


def type_counts(types) -> dict:
    """Tally an iterable of type strings into per-type counts."""
    counts = {TYPE_USER: 0, TYPE_GROUP: 0, TYPE_SP: 0, TYPE_UNKNOWN: 0}
    for t in types:
        counts[t] = counts.get(t, 0) + 1
    return counts
