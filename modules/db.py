"""Shared Databricks SQL connection + app-config loader.

By default (``query_identity: app`` in ``config/app_config.yaml``) every SQL
query runs as the **app service principal**: viewers need no grants of their
own on ``system.access`` / ``information_schema``, and access to what UCViz
shows is controlled by who holds ``CAN_USE`` on the app (every app user sees
what the SP can see — see README, "v2 — per-group catalog scoping").

``query_identity: viewer`` switches to **on-behalf-of-user** auth instead:
Databricks Apps forward the viewer's OAuth token in the
``X-Forwarded-Access-Token`` header, so queries run as the person viewing the
app and reflect exactly the objects they can see. With no forwarded token
(e.g. local dev) the SP identity is used either way.

The SQL warehouse is chosen via the ``DATABRICKS_WAREHOUSE_ID`` environment
variable declared in ``app.yaml``.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from functools import lru_cache

import pandas as pd
import yaml
from databricks import sql
from databricks.sql import exc as sql_exc
from databricks.sdk.core import Config

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "app_config.yaml"
)

_cfg = Config()


# --------------------------------------------------------------------------- #
# Viewer identity (on-behalf-of-user)
# --------------------------------------------------------------------------- #
def _headers() -> dict:
    """Return the current request headers, or {} when unavailable."""
    try:
        import streamlit as st

        return dict(st.context.headers or {})
    except Exception:
        return {}


def user_access_token() -> str | None:
    """The viewer's forwarded OAuth token, if the app runs with user auth."""
    h = _headers()
    return h.get("X-Forwarded-Access-Token") or h.get(
        "x-forwarded-access-token"
    )


def viewer_email() -> str:
    """Best-effort viewer identity for display and per-user cache keys."""
    h = _headers()
    for key in ("X-Forwarded-Email", "x-forwarded-email",
                "X-Forwarded-Preferred-Username",
                "x-forwarded-preferred-username", "X-Forwarded-User"):
        if h.get(key):
            return h[key]
    # Fall back to the service-principal identity in non-app contexts.
    try:
        return _cfg.username or "service-principal"
    except Exception:
        return "service-principal"


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #
def _connect(token: str | None):
    """Open a Databricks SQL connection (viewer token, or the app SP)."""
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not warehouse_id:
        raise RuntimeError(
            "DATABRICKS_WAREHOUSE_ID is not set — bind a SQL warehouse "
            "resource in app.yaml (see README)."
        )
    hostname = _cfg.host.replace("https://", "")
    http_path = f"/sql/1.0/warehouses/{warehouse_id}"
    if token:
        return sql.connect(
            server_hostname=hostname, http_path=http_path, access_token=token
        )
    return sql.connect(
        server_hostname=hostname,
        http_path=http_path,
        credentials_provider=lambda: _cfg.authenticate,
    )


def _query_token() -> str | None:
    return None if query_identity() == "app" else user_access_token()


@contextmanager
def get_connection():
    """Yield a fresh Databricks SQL connection, closing it on exit.

    Authenticates as the app service principal, unless ``query_identity`` is
    ``viewer`` and a forwarded viewer token is present (on-behalf-of-user).
    """
    conn = _connect(_query_token())
    try:
        yield conn
    finally:
        conn.close()


# One open connection per thread and identity. Streamlit runs each browser
# session's script on its own thread, so connections are never shared across
# sessions, and a page that issues ~30 metadata queries pays the connection
# handshake once instead of ~30 times.
_local = threading.local()


def _cached_connection(token: str | None):
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    key = token or "__app_sp__"
    if key not in conns:
        conns[key] = _connect(token)
    return key, conns


def _drop_connection(key, conns) -> None:
    conn = conns.pop(key, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def run_query(query: str, params=None) -> pd.DataFrame:
    """Run ``query`` and return a pandas DataFrame (empty on no rows).

    ``params`` is a dict for native named parameters (``:name`` markers).
    Reuses this thread's connection; if it has gone stale the query is
    retried once on a new one. SQL errors (``ServerOperationError``, e.g.
    permission denied) are raised as-is, without a retry.
    """
    token = _query_token()
    for attempt in (0, 1):
        key, conns = _cached_connection(token)
        try:
            with conns[key].cursor() as cur:
                cur.execute(query, params or {})
                try:
                    return cur.fetchall_arrow().to_pandas()
                except Exception:
                    rows = cur.fetchall()
                    cols = [d[0] for d in (cur.description or [])]
                    return pd.DataFrame([tuple(r) for r in rows],
                                        columns=cols)
        except sql_exc.ServerOperationError:
            raise
        except Exception:
            _drop_connection(key, conns)
            if attempt:
                raise
    raise RuntimeError("unreachable")


def viewer_in_any_group(groups: list[str]) -> bool | None:
    """Whether the *signed-in user* belongs to any of ``groups``.

    Runs ``is_member`` (workspace groups, e.g. ``admins``) and
    ``is_account_group_member`` (account groups) in SQL with the viewer's own
    forwarded token, whatever ``query_identity`` is — so it needs no SCIM
    rights for the app SP. Returns None when there is no forwarded token
    (outside a Databricks App) or the check fails.
    """
    token = user_access_token()
    names = [g for g in groups if g]
    if not token or not names:
        return None
    params = {f"g{i}": g for i, g in enumerate(names)}
    checks = " OR ".join(
        f"is_member(:g{i}) OR is_account_group_member(:g{i})"
        for i in range(len(names)))
    try:
        conn = _connect(token)
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT ({checks}) AS m", params)
                row = cur.fetchone()
        finally:
            conn.close()
        return bool(row[0]) if row else False
    except Exception:
        return None


def _forwarded_email() -> str | None:
    """The signed-in viewer's email from the Apps proxy headers, if any."""
    h = _headers()
    for key in ("X-Forwarded-Email", "x-forwarded-email"):
        if h.get(key):
            return h[key]
    return None


def current_user() -> dict:
    """Return the *viewer's* identity (username + direct group memberships).

    Groups are looked up in SCIM for the forwarded viewer email (the app SP
    does the lookup), so this works with either ``query_identity``. Outside a
    Databricks App (no forwarded email) it reflects the SP itself. Groups may
    be empty if SCIM can't be read — callers must treat that as "not admin".
    """
    from modules.principals import user_groups

    email = _forwarded_email()
    if email:
        return {"user_name": email, "display_name": email,
                "groups": user_groups(email)}
    display = viewer_email()
    groups: list[str] = []
    try:
        from databricks.sdk import WorkspaceClient

        me = WorkspaceClient().current_user.me()
        display = me.display_name or me.user_name or display
        groups = [g.display for g in (me.groups or []) if g.display]
        return {"user_name": me.user_name or display, "display_name": display,
                "groups": groups}
    except Exception:
        return {"user_name": display, "display_name": display, "groups": []}


# --------------------------------------------------------------------------- #
# App config
# --------------------------------------------------------------------------- #
_DEFAULT_CONFIG = {
    "catalogs": [],
    "query_identity": "app",
    "max_hops": 5,
    "max_graph_tables": 50,
    "layer_rules": [],
    "tag_roles": {"domain": [], "sensitivity": [], "abac": []},
    "high_sensitivity_values": [],
    "admin_groups": ["account admins"],
    "enable_external_source_writes": False,
}


def load_config(path: str = CONFIG_PATH) -> dict:
    """Load the app config YAML, filling defaults for any missing keys."""
    cfg = dict(_DEFAULT_CONFIG)
    try:
        with open(path, "r") as f:
            loaded = yaml.safe_load(f) or {}
        cfg.update({k: v for k, v in loaded.items() if v is not None})
    except FileNotFoundError:
        pass
    # Ensure tag_roles always has the three expected buckets.
    roles = dict(_DEFAULT_CONFIG["tag_roles"])
    roles.update(cfg.get("tag_roles") or {})
    cfg["tag_roles"] = roles
    return cfg


def save_config(cfg: dict, path: str = CONFIG_PATH) -> bool:
    """Best-effort write of the config YAML. Returns False if not writable.

    Databricks Apps may serve source files read-only; callers should fall back
    to session state and offer the YAML as a download when this returns False.
    """
    try:
        with open(path, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)
        return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def query_identity() -> str:
    """``app`` (service principal, default) or ``viewer`` (on-behalf-of-user).

    Read once per process — it's a deploy-time choice, not a per-session one.
    """
    value = str(load_config().get("query_identity") or "app").lower()
    return "app" if value in ("app", "sp", "service_principal") else "viewer"
