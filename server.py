from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import html
import io
import json
import os
import platform
import re
import shutil
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
import zlib
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from http import cookies
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from xml.etree import ElementTree as ET

try:
    import psycopg  # type: ignore
except Exception:  # pragma: no cover - optional unless PostgreSQL is configured.
    psycopg = None

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "data" / "operations_ddv.db"
WEB_DIR = APP_DIR / "web"
EXPORTS_DIR = APP_DIR / "exports"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8766"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or "").strip()
SESSION_COOKIE = "planning_ddv_session"
SESSION_HOURS = 10
ACTIVE_SESSION_MINUTES = 10
FAILED_LOGIN_LIMIT = 5
FAILED_LOGIN_WINDOW_SECONDS = 15 * 60
ROLES = {"ADMINISTRADOR", "OPERADOR_TW", "OPERADOR_PM", "CONSULTA"}
ROLE_LABELS = {
    "ADMINISTRADOR": "Administrador",
    "OPERADOR_TW": "Operador Trelew",
    "OPERADOR_PM": "Operador Puerto Madryn",
    "CONSULTA": "Consulta",
}
ROLE_BASES = {
    "ADMINISTRADOR": "TODAS",
    "OPERADOR_TW": "TRELEW",
    "OPERADOR_PM": "PUERTO MADRYN",
    "CONSULTA": "TODAS",
}
FAILED_LOGINS: dict[str, list[float]] = {}
OUTLOOK_PACKAGE_TTL_SECONDS = 5 * 60
OUTLOOK_PACKAGES: dict[str, dict[str, Any]] = {}
OUTLOOK_PACKAGES_LOCK = threading.Lock()
CONNECTOR_DIR = APP_DIR / "connector"

STORAGE_TABLES = [
    "planning_routes",
    "employees",
    "vehicle_people",
    "localities",
    "employee_locality_roles",
    "personnel_novelties",
    "recargas",
    "mail_log",
    "users",
    "user_sessions",
    "audit_log",
]

DATE_COLUMN_NAMES = {
    "planning_date",
    "novelty_date",
    "recarga_date",
    "period_start",
    "period_end",
    "mail_date",
    "operational_date",
}

TIMESTAMP_COLUMN_NAMES = {
    "created_at",
    "updated_at",
    "expires_at",
    "last_activity",
    "last_login",
}

TEMPORAL_COLUMN_NAMES = DATE_COLUMN_NAMES | TIMESTAMP_COLUMN_NAMES

JSON_COLUMN_NAMES = {
    "previous_data",
    "new_data",
    "metadata",
    "details",
}

try:
    import bcrypt  # type: ignore
except Exception:  # pragma: no cover - Render installs it from requirements.
    bcrypt = None

CHESS_COLUMNS = [
    "idCns", "dsCns", "TotPDV", "TotBlt", "TotUPs", "TotVal", "TotFdR",
    "TotUdT", "TotDia", "TotUdM", "TotPes", "TotPkg", "TotDSB", "TotDSV",
    "TotDSU", "TotCrg", "TotCbt",
]

NOVELTY_REASONS = ["FRANCO", "ART", "VACACIONES", "AUSENTE", "SUSPENSIÓN", "PERMISO GREMIAL"]


def canonical(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper()
    text = re.sub(r"\s+", " ", text)
    return text


def route_division_for_db(value: Any) -> str:
    div = canonical(value)
    if div in {"TW", "TRELEW"}:
        return "TW" if postgres_enabled() else "TRELEW"
    if div in {"PM", "PUERTO MADRYN", "PUERTO_MADRYN"}:
        return "PM" if postgres_enabled() else "PUERTO MADRYN"
    return div


def display_division(value: Any) -> str:
    div = canonical(value)
    if div in {"TW", "TRELEW"}:
        return "TRELEW"
    if div in {"PM", "PUERTO MADRYN", "PUERTO_MADRYN"}:
        return "PUERTO MADRYN"
    return div


def normalize_route_output(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    if "planning_date" in output:
        output["planning_date"] = normalize_date_value(output.get("planning_date")) or str(output.get("planning_date") or "")
    if "division" in output:
        output["division"] = display_division(output.get("division"))
    if "id" in output:
        output["id"] = str(output.get("id"))
    return output


PG_ROUTE_EFFECTIVE_DATE = "COALESCE(NULLIF(pr.planning_date::text, '')::date, pd.operational_date, d.fecha)"
PG_ROUTE_BASE_FROM_PR_DIVISION = """
CASE
    WHEN UPPER(TRIM(COALESCE(pr.division, ''))) IN ('TW', 'TRELEW') THEN 'TRELEW'
    WHEN UPPER(TRIM(COALESCE(pr.division, ''))) IN ('PM', 'PUERTO MADRYN', 'PUERTO_MADRYN') THEN 'PUERTO MADRYN'
    ELSE NULL
END
"""
PG_ROUTE_EFFECTIVE_DIVISION = """
CASE
    WHEN UPPER(TRIM(COALESCE(pr.division, ''))) IN ('TW', 'TRELEW') THEN 'TW'
    WHEN UPPER(TRIM(COALESCE(pr.division, ''))) IN ('PM', 'PUERTO MADRYN', 'PUERTO_MADRYN') THEN 'PM'
    WHEN UPPER(TRIM(COALESCE(d.base, ''))) = 'TRELEW' THEN 'TW'
    WHEN UPPER(TRIM(COALESCE(d.base, ''))) IN ('PM', 'PUERTO MADRYN', 'PUERTO_MADRYN') THEN 'PM'
    ELSE UPPER(TRIM(COALESCE(pr.division, d.base, '')))
END
"""
PG_ROUTE_LOCALITY_DIVISION = f"""
CASE
    WHEN ({PG_ROUTE_EFFECTIVE_DIVISION}) = 'TW' THEN 'TRELEW'
    WHEN ({PG_ROUTE_EFFECTIVE_DIVISION}) = 'PM' THEN 'PUERTO MADRYN'
    ELSE ({PG_ROUTE_EFFECTIVE_DIVISION})
END
"""

PG_ROUTE_DAY_JOINS = f"""
LEFT JOIN planning_days pd ON pd.id=pr.planning_day_id
LEFT JOIN dias_operativos d
  ON d.fecha=COALESCE(NULLIF(pr.planning_date::text, '')::date, pd.operational_date)
 AND d.base={PG_ROUTE_BASE_FROM_PR_DIVISION}
"""


def safe_number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def row_dict(row: Any | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def role_label(role: str) -> str:
    return ROLE_LABELS.get(canonical(role), canonical(role).replace("_", " ").title())


def public_user(row: dict[str, Any] | sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    role = canonical(data.get("role"))
    name = str(data.get("display_name") or data.get("username") or "")
    initials = "".join(part[:1] for part in name.split()[:2]).upper() or "U"
    return {
        "id": data.get("id"),
        "username": data.get("username"),
        "display_name": name,
        "initials": initials,
        "role": role,
        "role_label": role_label(role),
        "assigned_base": canonical(data.get("assigned_base")) or ROLE_BASES.get(role, "TODAS"),
        "active": int(data.get("active") or 0),
        "must_change_password": int(data.get("must_change_password") or 0),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "last_login": data.get("last_login"),
        "created_by": data.get("created_by"),
        "is_admin": role == "ADMINISTRADOR",
    }


def hash_password(password: str) -> str:
    if bcrypt is not None:
        return "bcrypt$" + bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000)
    return f"pbkdf2_sha256$260000${salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    if password_hash.startswith("bcrypt$") and bcrypt is not None:
        return bool(bcrypt.checkpw(password.encode("utf-8"), password_hash.split("$", 1)[1].encode("utf-8")))
    parts = password_hash.split("$")
    if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
        _, rounds, salt, expected = parts
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(rounds)).hex()
        return hmac.compare_digest(digest, expected)
    return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "si", "sí", "activo"}


def temporary_password_from_payload(payload: dict[str, Any]) -> str:
    return str(
        payload.get("password")
        or payload.get("temporary_password")
        or payload.get("new_password")
        or payload.get("password_provisoria")
        or ""
    )


def normalize_role(role: str) -> str:
    value = canonical(role)
    if value not in ROLES:
        raise ValueError("Rol no permitido.")
    return value


def normalize_assigned_base(base: str, role: str = "") -> str:
    value = canonical(base) or ROLE_BASES.get(canonical(role), "TODAS")
    aliases = {"TW": "TRELEW", "PM": "PUERTO MADRYN", "PUERTO MADRYN": "PUERTO MADRYN", "TRELEW": "TRELEW", "TODAS": "TODAS"}
    value = aliases.get(value, value)
    if value not in {"TODAS", "TRELEW", "PUERTO MADRYN"}:
        raise ValueError("Base asignada no permitida.")
    return value


def can_edit_division(user: dict[str, Any], division: str) -> bool:
    div = canonical(division)
    role = canonical(user.get("role"))
    base = normalize_assigned_base(str(user.get("assigned_base") or ""), role)
    if role == "ADMINISTRADOR":
        if base == "TODAS":
            return True
        if not div or div == "TODAS":
            return False
        return display_division(div) == base
    if role == "CONSULTA":
        return False
    if not div or div == "TODAS":
        return False
    return display_division(div) == base


def require_role(user: dict[str, Any] | None, *roles: str) -> dict[str, Any]:
    if not user:
        raise PermissionError("Debe iniciar sesión.")
    allowed = {canonical(role) for role in roles}
    if canonical(user.get("role")) not in allowed:
        raise PermissionError("No tiene permiso para esta acción.")
    return user


def require_base_access(user: dict[str, Any], division: str) -> None:
    if not can_edit_division(user, division):
        raise PermissionError("No tiene permiso para modificar esa división.")


def table_columns(con: Any, table: str) -> set[str]:
    if postgres_enabled():
        rows = con.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=?
            """,
            (table,),
        ).fetchall()
        return {str(row[0]) for row in rows}
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def safe_audit_text(value: Any, fallback: str = "N/D") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def normalize_json_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    text = str(value).strip()
    if not text:
        return None
    try:
        json.loads(text)
        return text
    except (TypeError, ValueError):
        return None


def audit_entity_name(user: dict[str, Any] | None, record_type: str = "", record_id: str = "", new_data: Any = None) -> str:
    if isinstance(new_data, dict):
        for key in ("entity_name", "username", "display_name", "name"):
            value = new_data.get(key)
            if str(value or "").strip():
                if key == "username":
                    return f"Usuario {canonical(value)}"
                return str(value).strip()
    if record_type and record_id:
        return f"{canonical(record_type)} {record_id}".strip()
    if user and str(user.get("username") or "").strip():
        return f"Usuario {canonical(user.get('username'))}"
    return "N/D"


def normalize_audit_division_value(division: Any) -> str | None:
    div = canonical(division)

    if div in {"TW", "TRELEW"}:
        return "TW"
    if div in {"PM", "PUERTO MADRYN", "PUERTO_MADRYN"}:
        return "PM"
    return None


def normalize_audit_division(con: Any, division: Any) -> str | None:
    return normalize_audit_division_value(division)


def valid_uuid_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except (TypeError, ValueError):
        return None


def audit_user_id(user: dict[str, Any] | None) -> Any:
    if not user:
        return None
    value = user.get("id")
    if postgres_enabled():
        return valid_uuid_text(value)
    return value


def register_audit_event(
    con: Any | None,
    user: dict[str, Any] | None,
    action: str,
    module: str,
    operational_date: str = "",
    division: str = "",
    record_type: str = "",
    record_id: str = "",
    previous_data: Any = None,
    new_data: Any = None,
    ip_address: str = "",
) -> None:
    if con is None:
        with db() as audit_con:
            register_audit_event(
                audit_con,
                user,
                action,
                module,
                operational_date,
                division,
                record_type,
                record_id,
                previous_data,
                new_data,
                ip_address,
            )
        return
    close_con = False
    try:
        columns = [
            "user_id",
            "username",
            "action",
            "module",
            "operational_date",
            "division",
            "record_type",
            "record_id",
            "previous_data",
            "new_data",
            "created_at",
            "ip_address",
        ]
        values: list[Any] = [
            audit_user_id(user),
            safe_audit_text(user.get("username") if user else ""),
            safe_audit_text(action),
            safe_audit_text(module),
            operational_date,
            normalize_audit_division(con, division),
            str(record_type or ""),
            str(record_id or ""),
            normalize_json_value(previous_data),
            normalize_json_value(new_data),
            now_iso(),
            str(ip_address or ""),
        ]
        if "entity_name" in table_columns(con, "audit_log"):
            columns.insert(4, "entity_name")
            values.insert(4, audit_entity_name(user, record_type, record_id, new_data))
        placeholders = ",".join("?" for _ in columns)
        con.execute(
            f"INSERT INTO audit_log({','.join(columns)}) VALUES({placeholders})",
            tuple(values),
        )
        if close_con:
            con.commit()
    finally:
        if close_con:
            con.close()


def recarga_period(reference: str | date) -> tuple[str, str, str]:
    if isinstance(reference, str):
        ref = datetime.fromisoformat(reference).date()
    else:
        ref = reference
    if ref.day >= 26:
        start = ref.replace(day=26)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month.replace(day=25)
    else:
        first_this = ref.replace(day=1)
        previous_last = first_this - timedelta(days=1)
        start = previous_last.replace(day=26)
        end = ref.replace(day=25)
    label = f"{start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}"
    return start.isoformat(), end.isoformat(), label


class CompatRow(dict):
    def __init__(self, columns: list[str], values: tuple[Any, ...]):
        clean_values = tuple(self._clean_value(value) for value in values)
        super().__init__(zip(columns, clean_values))
        self._values = clean_values

    @staticmethod
    def _clean_value(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class PgCursorCompat:
    def __init__(self, cursor: Any):
        self.cursor = cursor

    def _columns(self) -> list[str]:
        return [col.name for col in (self.cursor.description or [])]

    def fetchone(self) -> CompatRow | None:
        row = self.cursor.fetchone()
        if row is None:
            return None
        return CompatRow(self._columns(), tuple(row))

    def fetchall(self) -> list[CompatRow]:
        columns = self._columns()
        return [CompatRow(columns, tuple(row)) for row in self.cursor.fetchall()]


def postgres_enabled() -> bool:
    return bool(SUPABASE_DB_URL)


_PG_COLUMN_TYPE_CACHE: dict[tuple[str, str], str] = {}


def pg_column_type(table: str, column: str) -> str:
    if not postgres_enabled() or psycopg is None:
        return ""
    key = (table, column)
    if key in _PG_COLUMN_TYPE_CACHE:
        return _PG_COLUMN_TYPE_CACHE[key]
    try:
        with psycopg.connect(SUPABASE_DB_URL, connect_timeout=8) as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s AND column_name=%s
                    LIMIT 1
                    """,
                    (table, column),
                )
                row = cur.fetchone()
    except Exception as exc:
        print(f"No se pudo leer tipo de columna {table}.{column}: {type(exc).__name__}: {exc}")
        row = None
    column_type = str(row[0] if row else "").lower()
    _PG_COLUMN_TYPE_CACHE[key] = column_type
    return column_type


def active_condition(table: str, alias: str = "", column: str = "active", active: bool = True) -> str:
    ref = f"{alias}.{column}" if alias else column
    if not postgres_enabled():
        return f"{ref}={1 if active else 0}"
    column_type = pg_column_type(table, column)
    if column_type == "boolean":
        return f"{ref} IS {'TRUE' if active else 'FALSE'}"
    if column_type in {"smallint", "integer", "bigint", "numeric"}:
        return f"{ref}={1 if active else 0}"
    expected = ("'1','true','t','yes','si'", "'0','false','f','no'")[0 if active else 1]
    return f"LOWER(TRIM(COALESCE({ref}::text,''))) IN ({expected})"


def active_value(table: str, active: bool = True, column: str = "active") -> int | bool:
    if postgres_enabled() and pg_column_type(table, column) == "boolean":
        return bool(active)
    return 1 if active else 0


def translate_sqlite_to_postgres(sql: str) -> str:
    translated = sql
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", translated, flags=re.I)
    translated = re.sub(
        r"INSERT INTO localities\(([^)]+)\) VALUES\(([^)]+)\)(?!\s+ON CONFLICT)",
        r"INSERT INTO localities(\1) VALUES(\2) ON CONFLICT(name,division) DO NOTHING",
        translated,
        flags=re.I | re.S,
    )
    translated = re.sub(
        r"INSERT INTO vehicle_people\(([^)]+)\) VALUES\(([^)]+)\)(?!\s+ON CONFLICT)",
        r"INSERT INTO vehicle_people(\1) VALUES(\2) ON CONFLICT(domain,employee_name,role) DO NOTHING",
        translated,
        flags=re.I | re.S,
    )
    translated = re.sub(
        r"INSERT INTO employee_locality_roles\s*\(([^)]+)\) VALUES\(([^)]+)\)(?!\s+ON CONFLICT)",
        r"INSERT INTO employee_locality_roles(\1) VALUES(\2) ON CONFLICT(employee_name,division,locality) DO NOTHING",
        translated,
        flags=re.I | re.S,
    )
    translated = translated.replace("SELECT last_insert_rowid()", "SELECT lastval()")
    translated = translated.replace("display_name COLLATE NOCASE", "LOWER(display_name)")
    translated = translated.replace("GROUP_CONCAT(DISTINCT r.role) roles", "string_agg(DISTINCT r.role, ',') roles")
    translated = translated.replace("MAX(can_driver,?)", "GREATEST(can_driver,?)")
    translated = translated.replace("MAX(can_helper,?)", "GREATEST(can_helper,?)")
    return translated.replace("?", "%s")


def normalize_date_value(value: Any, timestamp: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds") if timestamp else value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return None

    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1])
    candidates.append(text.replace("/", "-"))
    if " " in text:
        candidates.append(text.replace(" ", "T", 1))

    for candidate in candidates:
        clean = candidate.strip()
        if not clean:
            continue
        try:
            parsed_dt = datetime.fromisoformat(clean)
            return parsed_dt.isoformat(timespec="seconds") if timestamp else parsed_dt.date().isoformat()
        except ValueError:
            pass
        try:
            parsed_date = date.fromisoformat(clean[:10])
            return parsed_date.isoformat()
        except ValueError:
            pass

    day_first = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})(?:\D.*)?$", text)
    if day_first:
        day, month, year = map(int, day_first.groups())
        try:
            parsed_date = date(year, month, day)
            return parsed_date.isoformat()
        except ValueError:
            return None

    return None


def _clean_column_name(column: str) -> str:
    return column.strip().strip('"').split(".")[-1].lower()


def _split_sql_columns(columns_sql: str) -> list[str]:
    return [_clean_column_name(part) for part in columns_sql.split(",") if part.strip()]


def _param_columns_from_insert(sql: str) -> list[str]:
    match = re.search(r"\bINSERT\s+INTO\s+\w+\s*\(([^)]+)\)\s+VALUES\s*\(([^)]*)\)", sql, flags=re.I | re.S)
    if not match:
        return []
    columns = _split_sql_columns(match.group(1))
    placeholders = match.group(2).count("?")
    return columns[:placeholders]


def _param_columns_from_update(sql: str) -> list[str]:
    match = re.search(r"\bUPDATE\s+\w+\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?$", sql, flags=re.I | re.S)
    if not match:
        return []
    columns: list[str] = []
    set_sql = match.group(1)
    for assignment in set_sql.split(","):
        if "?" not in assignment:
            continue
        left = assignment.split("=", 1)[0]
        columns.append(_clean_column_name(left))
    columns.extend(_param_columns_from_where(match.group(2) or ""))
    return columns


def _param_columns_from_where(where_sql: str) -> list[str]:
    columns: list[str] = []
    for match in re.finditer(r"([A-Za-z_][\w.]*)\s+BETWEEN\s+\?\s+AND\s+\?", where_sql, flags=re.I):
        col = _clean_column_name(match.group(1))
        columns.extend([col, col])
    masked = re.sub(r"([A-Za-z_][\w.]*)\s+BETWEEN\s+\?\s+AND\s+\?", "", where_sql, flags=re.I)
    for match in re.finditer(r"([A-Za-z_][\w.]*)\s*(?:=|<>|!=|<=|>=|<|>|LIKE)\s*\?", masked, flags=re.I):
        columns.append(_clean_column_name(match.group(1)))
    return columns


def infer_sql_param_columns(sql: str) -> list[str]:
    compact = " ".join(sql.strip().split())
    if re.match(r"^INSERT\s+INTO\b", compact, flags=re.I):
        return _param_columns_from_insert(compact)
    if re.match(r"^UPDATE\b", compact, flags=re.I):
        return _param_columns_from_update(compact)
    where_match = re.search(r"\bWHERE\s+(.+)$", compact, flags=re.I | re.S)
    return _param_columns_from_where(where_match.group(1) if where_match else compact)


def normalize_sql_params(sql: str, params: Any = None) -> Any:
    if params is None:
        return None
    if isinstance(params, dict) or isinstance(params, (str, bytes)):
        return params
    try:
        values = list(params)
    except TypeError:
        return params

    columns = infer_sql_param_columns(sql)
    if len(columns) < len(values):
        columns.extend([""] * (len(values) - len(columns)))

    normalized = []
    for column, value in zip(columns, values):
        clean_column = _clean_column_name(column)
        if clean_column in TEMPORAL_COLUMN_NAMES:
            normalized.append(normalize_date_value(value, clean_column in TIMESTAMP_COLUMN_NAMES))
        elif clean_column in JSON_COLUMN_NAMES:
            normalized.append(normalize_json_value(value))
        else:
            normalized.append(value)
    return tuple(normalized)


class PgConnectionCompat:
    def __init__(self, connection: Any):
        self.connection = connection

    def execute(self, sql: str, params: Any = None) -> PgCursorCompat:
        cursor = self.connection.cursor()
        cursor.execute(translate_sqlite_to_postgres(sql), normalize_sql_params(sql, params) or ())
        return PgCursorCompat(cursor)

    def executescript(self, script: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(script)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


def load_supabase_schema_sql() -> str:
    return (APP_DIR / "sql" / "supabase_schema.sql").read_text(encoding="utf-8")


@contextmanager
def db():
    if postgres_enabled():
        if psycopg is None:
            raise RuntimeError("Falta instalar psycopg para usar Supabase PostgreSQL.")
        con = psycopg.connect(SUPABASE_DB_URL, connect_timeout=8)
        wrapped = PgConnectionCompat(con)
        try:
            yield wrapped
            wrapped.commit()
        except Exception:
            wrapped.rollback()
            raise
        finally:
            wrapped.close()
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in con.execute(f"PRAGMA table_info({table})"))


def ensure_initial_admin(con: sqlite3.Connection) -> None:
    user_count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count:
        return
    username = (os.getenv("PLANNING_ADMIN_USER") or "").strip()
    password = os.getenv("PLANNING_ADMIN_PASSWORD") or ""
    display_name = (os.getenv("PLANNING_ADMIN_NAME") or username or "").strip()
    if not username or not password:
        print("No hay usuarios creados. Configure PLANNING_ADMIN_USER, PLANNING_ADMIN_PASSWORD y PLANNING_ADMIN_NAME para crear el administrador inicial.")
        return
    con.execute(
        """
        INSERT INTO users(username,display_name,password_hash,role,assigned_base,active,must_change_password,created_at,updated_at,created_by)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (canonical(username), display_name, hash_password(password), "ADMINISTRADOR", "TODAS", 1, 1, now_iso(), now_iso(), "ENV"),
    )
    register_audit_event(con, {"username": canonical(username), "role": "ADMINISTRADOR"}, "Creación administrador inicial", "Usuarios", new_data={"username": canonical(username)})


def get_current_user(handler: BaseHTTPRequestHandler | None = None, token: str = "") -> dict[str, Any] | None:
    session_token = token
    if handler is not None and not session_token:
        cookie_header = handler.headers.get("Cookie", "")
        parsed = cookies.SimpleCookie()
        parsed.load(cookie_header)
        if SESSION_COOKIE in parsed:
            session_token = parsed[SESSION_COOKIE].value
    if not session_token:
        return None
    token_digest = hash_token(session_token)
    now = now_iso()
    with db() as con:
        row = con.execute(
            f"""
            SELECT u.*, s.id session_id
            FROM user_sessions s
            JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND {active_condition('user_sessions', 's')} AND s.expires_at>? AND {active_condition('users', 'u')}
            """,
            (token_digest, now),
        ).fetchone()
        if not row:
            return None
        con.execute("UPDATE user_sessions SET last_activity=? WHERE token_hash=?", (now, token_digest))
        return public_user(row)


def require_login(handler: BaseHTTPRequestHandler | None = None) -> dict[str, Any]:
    user = get_current_user(handler)
    if not user:
        raise PermissionError("Debe iniciar sesión.")
    return user


def init_db() -> None:
    if postgres_enabled():
        with db() as con:
            con.executescript(load_supabase_schema_sql())
            ensure_initial_admin(con)
        return
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS planning_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                planning_date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'CHESS',
                division TEXT NOT NULL,
                unit_id TEXT,
                domain TEXT NOT NULL,
                domain_seq INTEGER NOT NULL DEFAULT 1,
                pdv REAL DEFAULT 0,
                bultos REAL DEFAULT 0,
                pure_pallets REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                out_route REAL DEFAULT 0,
                tot_udt REAL DEFAULT 0,
                tot_dia REAL DEFAULT 0,
                hectoliters REAL DEFAULT 0,
                weight REAL DEFAULT 0,
                picking REAL DEFAULT 0,
                avg_bultos REAL DEFAULT 0,
                avg_value REAL DEFAULT 0,
                avg_hl REAL DEFAULT 0,
                total_loads REAL DEFAULT 0,
                comprobantes REAL DEFAULT 0,
                contact TEXT DEFAULT '',
                rendicion TEXT DEFAULT '',
                driver TEXT DEFAULT '',
                helper1 TEXT DEFAULT '',
                helper2 TEXT DEFAULT '',
                locality TEXT DEFAULT '',
                observations TEXT DEFAULT '',
                recarga_qty INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'BORRADOR',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(planning_date, division, domain, domain_seq)
            );
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_code TEXT DEFAULT '',
                name TEXT NOT NULL UNIQUE,
                division TEXT NOT NULL,
                primary_role TEXT NOT NULL DEFAULT 'AYUDANTE',
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS vehicle_people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                employee_name TEXT NOT NULL,
                role TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 99,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(domain, employee_name, role)
            );
            CREATE TABLE IF NOT EXISTS localities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                division TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 99,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(name, division)
            );
            CREATE TABLE IF NOT EXISTS employee_locality_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_name TEXT NOT NULL,
                division TEXT NOT NULL,
                locality TEXT NOT NULL,
                can_driver INTEGER NOT NULL DEFAULT 0,
                can_helper INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(employee_name, division, locality)
            );
            CREATE TABLE IF NOT EXISTS personnel_novelties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novelty_date TEXT NOT NULL,
                employee_name TEXT NOT NULL,
                division TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(novelty_date, employee_name)
            );
            CREATE TABLE IF NOT EXISTS recargas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recarga_date TEXT NOT NULL,
                route_id INTEGER NOT NULL,
                employee_name TEXT NOT NULL,
                role TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                FOREIGN KEY(route_id) REFERENCES planning_routes(id) ON DELETE CASCADE,
                UNIQUE(route_id, employee_name, role)
            );
            CREATE TABLE IF NOT EXISTS mail_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mail_date TEXT NOT NULL,
                planning_date TEXT NOT NULL,
                recipients TEXT NOT NULL,
                cc TEXT DEFAULT '',
                subject TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                assigned_base TEXT NOT NULL DEFAULT 'TODAS',
                active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login TEXT DEFAULT '',
                created_by TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                ip_address TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT DEFAULT '',
                action TEXT NOT NULL,
                module TEXT NOT NULL,
                operational_date TEXT DEFAULT '',
                division TEXT DEFAULT '',
                record_type TEXT DEFAULT '',
                record_id TEXT DEFAULT '',
                previous_data TEXT DEFAULT '',
                new_data TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ip_address TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_user_sessions_token_hash ON user_sessions(token_hash);
            CREATE INDEX IF NOT EXISTS idx_user_sessions_active ON user_sessions(active,last_activity);
            CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_log_username ON audit_log(username);
            """
        )
        migrations = {
            "base_locality": "TEXT DEFAULT ''",
            "can_driver": "INTEGER NOT NULL DEFAULT 0",
            "can_helper": "INTEGER NOT NULL DEFAULT 0",
        }
        for col, definition in migrations.items():
            if not column_exists(con, "employees", col):
                con.execute(f"ALTER TABLE employees ADD COLUMN {col} {definition}")
        if not column_exists(con, "planning_routes", "whatsapp_observation"):
            con.execute("ALTER TABLE planning_routes ADD COLUMN whatsapp_observation TEXT DEFAULT ''")
        if not column_exists(con, "planning_routes", "kms"):
            con.execute("ALTER TABLE planning_routes ADD COLUMN kms REAL NOT NULL DEFAULT 0")

        # Populate role flags from historical domain-role records.
        people = con.execute("SELECT name, primary_role FROM employees").fetchall()
        for person in people:
            name = canonical(person["name"])
            roles = {canonical(r[0]) for r in con.execute(
                f"SELECT DISTINCT role FROM vehicle_people WHERE employee_name=? AND {active_condition('vehicle_people')}", (name,)
            )}
            can_driver = 1 if "CHOFER" in roles or canonical(person["primary_role"]) == "CHOFER" else 0
            can_helper = 1 if "AYUDANTE" in roles or canonical(person["primary_role"]) == "AYUDANTE" else 0
            if not roles:
                can_helper = 1 if canonical(person["primary_role"]) != "CHOFER" else can_helper
            con.execute(
                "UPDATE employees SET can_driver=MAX(can_driver,?), can_helper=MAX(can_helper,?) WHERE name=?",
                (can_driver, can_helper, name),
            )

        # Infer a base locality from most frequent historical locality, otherwise division.
        for person in con.execute("SELECT name, division, base_locality FROM employees").fetchall():
            if canonical(person["base_locality"]):
                continue
            row = con.execute(
                """
                SELECT locality, COUNT(*) qty
                FROM planning_routes
                WHERE locality<>'' AND (? IN (driver, helper1, helper2))
                GROUP BY locality ORDER BY qty DESC LIMIT 1
                """,
                (person["name"],),
            ).fetchone()
            base_loc = canonical(row["locality"] if row else person["division"])
            con.execute("UPDATE employees SET base_locality=? WHERE name=?", (base_loc, person["name"]))

        # Initial locality-role filters: preserve the current base locality as a starting point.
        filter_count = con.execute("SELECT COUNT(*) FROM employee_locality_roles").fetchone()[0]
        if not filter_count:
            for person in con.execute(f"SELECT name,division,base_locality,can_driver,can_helper,active FROM employees WHERE {active_condition('employees')}").fetchall():
                locality = canonical(person["base_locality"])
                if not locality:
                    continue
                con.execute(
                    """
                    INSERT OR IGNORE INTO employee_locality_roles
                    (employee_name,division,locality,can_driver,can_helper,active) VALUES(?,?,?,?,?,1)
                    """,
                    (canonical(person["name"]), canonical(person["division"]), locality,
                     int(person["can_driver"] or 0), int(person["can_helper"] or 0)),
                )
        ensure_initial_admin(con)


def _col_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref.upper())
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + (ord(char) - 64)
    return value - 1


def parse_xlsx(data: bytes) -> list[dict[str, Any]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iterfind(".//m:t", ns)))
        sheet_name = "xl/worksheets/sheet1.xml"
        root = ET.fromstring(archive.read(sheet_name))
        rows: list[list[Any]] = []
        for row in root.findall(".//m:sheetData/m:row", ns):
            values: dict[int, Any] = {}
            for cell in row.findall("m:c", ns):
                ref = cell.attrib.get("r", "A1")
                idx = _col_index(ref)
                ctype = cell.attrib.get("t", "")
                value_node = cell.find("m:v", ns)
                inline_node = cell.find("m:is", ns)
                raw = value_node.text if value_node is not None else ""
                if ctype == "s" and raw != "":
                    try:
                        value: Any = shared[int(raw)]
                    except (ValueError, IndexError):
                        value = raw
                elif ctype == "inlineStr" and inline_node is not None:
                    value = "".join(t.text or "" for t in inline_node.iterfind(".//m:t", ns))
                elif ctype == "b":
                    value = raw == "1"
                else:
                    if raw == "":
                        value = ""
                    else:
                        try:
                            value = float(raw)
                            if value.is_integer():
                                value = int(value)
                        except ValueError:
                            value = raw
                values[idx] = value
            if values:
                max_idx = max(values)
                rows.append([values.get(i, "") for i in range(max_idx + 1)])
        if not rows:
            return []
        headers = [str(x).strip() for x in rows[0]]
        records: list[dict[str, Any]] = []
        for row in rows[1:]:
            rec = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
            if any(v not in ("", None) for v in rec.values()):
                records.append(rec)
        return records


def validate_chess(data: bytes, division: str, planning_date: str) -> list[dict[str, Any]]:
    rows = parse_xlsx(data)
    if not rows:
        raise ValueError(f"El archivo de {division} no contiene registros.")
    missing = [col for col in CHESS_COLUMNS if col not in rows[0]]
    if missing:
        raise ValueError(f"El archivo de {division} no tiene la estructura esperada. Faltan: {', '.join(missing)}")
    output = []
    seq: dict[str, int] = {}
    for row in rows:
        domain = canonical(row.get("dsCns"))
        if not domain:
            continue
        seq[domain] = seq.get(domain, 0) + 1
        output.append({
            "planning_date": planning_date,
            "source": "CHESS",
            "division": route_division_for_db(division),
            "unit_id": str(row.get("idCns", "") or ""),
            "domain": domain,
            "domain_seq": seq[domain],
            "pdv": safe_number(row.get("TotPDV")),
            "bultos": safe_number(row.get("TotBlt")),
            "pure_pallets": safe_number(row.get("TotUPs")),
            "amount": safe_number(row.get("TotVal")),
            "out_route": safe_number(row.get("TotFdR")),
            "tot_udt": safe_number(row.get("TotUdT")),
            "tot_dia": safe_number(row.get("TotDia")),
            "hectoliters": safe_number(row.get("TotUdM")),
            "weight": safe_number(row.get("TotPes")),
            "picking": safe_number(row.get("TotPkg")),
            "avg_bultos": safe_number(row.get("TotDSB")),
            "avg_value": safe_number(row.get("TotDSV")),
            "avg_hl": safe_number(row.get("TotDSU")),
            "total_loads": safe_number(row.get("TotCrg")),
            "comprobantes": safe_number(row.get("TotCbt")),
        })
    return output


def ensure_route_planning_context(con: Any, planning_date: Any, division: Any) -> str | None:
    if not postgres_enabled():
        return None
    operational_date = normalize_date_value(planning_date)
    route_division = route_division_for_db(division)
    base = display_division(route_division)
    if not operational_date or route_division not in {"TW", "PM"} or base not in {"TRELEW", "PUERTO MADRYN"}:
        raise ValueError(
            f"No se pudo resolver la jornada operativa para {planning_date} / {division}. "
            "La importacion fue cancelada."
        )

    planning_day = con.execute(
        """
        INSERT INTO planning_days(operational_date,general_status)
        VALUES(?,?)
        ON CONFLICT(operational_date) DO UPDATE SET operational_date=excluded.operational_date
        RETURNING id
        """,
        (operational_date, "borrador"),
    ).fetchone()
    planning_day_id = str(planning_day["id"] if isinstance(planning_day, dict) else planning_day[0]) if planning_day else ""

    con.execute(
        """
        INSERT INTO dias_operativos(fecha,base,estado)
        VALUES(?,?,?)
        ON CONFLICT(fecha,base) DO UPDATE SET fecha=excluded.fecha
        RETURNING id
        """,
        (operational_date, base, "BORRADOR"),
    ).fetchone()

    if not planning_day_id:
        raise ValueError(
            f"No se pudo resolver planning_day_id para {operational_date} / {base}. "
            "La importacion fue cancelada."
        )
    return planning_day_id


def import_routes(records: list[dict[str, Any]]) -> dict[str, int]:
    """Importa una fecha y deja cada división exactamente igual al archivo cargado.

    Conserva los campos manuales de las rutas que continúan existiendo y elimina
    registros CHESS obsoletos de esa misma fecha/división cuando se reemplaza el archivo.
    """
    inserted = 0
    updated = 0
    removed = 0
    groups: dict[tuple[str, str], set[tuple[str, int]]] = {}
    for rec in records:
        rec["planning_date"] = normalize_date_value(rec["planning_date"]) or str(rec["planning_date"] or "")
        rec["division"] = route_division_for_db(rec["division"])
        key = (rec["planning_date"], rec["division"])
        groups.setdefault(key, set()).add((rec["domain"], int(rec["domain_seq"])))

    with db() as con:
        planning_day_ids = {
            (planning_date, division): ensure_route_planning_context(con, planning_date, division)
            for planning_date, division in groups
        }
        # El archivo cargado pasa a ser la fuente de verdad de esa fecha/división.
        for (planning_date, division), incoming in groups.items():
            existing = con.execute(
                "SELECT id,domain,domain_seq FROM planning_routes WHERE planning_date=? AND division=? AND source='CHESS'",
                (planning_date, division),
            ).fetchall()
            obsolete = [str(r["id"]) for r in existing if (r["domain"], int(r["domain_seq"])) not in incoming]
            for route_id in obsolete:
                con.execute("DELETE FROM planning_routes WHERE id=?", (route_id,))
            removed += len(obsolete)

        for rec in records:
            exists = con.execute(
                "SELECT id FROM planning_routes WHERE planning_date=? AND division=? AND domain=? AND domain_seq=?",
                (rec["planning_date"], rec["division"], rec["domain"], rec["domain_seq"]),
            ).fetchone()
            con.execute(
                """
                INSERT INTO planning_routes (
                    planning_day_id,planning_date,source,division,unit_id,domain,domain_seq,pdv,bultos,pure_pallets,
                    amount,out_route,tot_udt,tot_dia,hectoliters,weight,picking,avg_bultos,avg_value,
                    avg_hl,total_loads,comprobantes,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(planning_date,division,domain,domain_seq) DO UPDATE SET
                    planning_day_id=excluded.planning_day_id,source=excluded.source,unit_id=excluded.unit_id,
                    pdv=excluded.pdv,bultos=excluded.bultos,
                    pure_pallets=excluded.pure_pallets,amount=excluded.amount,out_route=excluded.out_route,
                    tot_udt=excluded.tot_udt,tot_dia=excluded.tot_dia,hectoliters=excluded.hectoliters,
                    weight=excluded.weight,picking=excluded.picking,avg_bultos=excluded.avg_bultos,
                    avg_value=excluded.avg_value,avg_hl=excluded.avg_hl,total_loads=excluded.total_loads,
                    comprobantes=excluded.comprobantes,updated_at=excluded.updated_at
                """,
                (
                    planning_day_ids[(rec["planning_date"], rec["division"])],
                    rec["planning_date"], rec["source"], rec["division"], rec["unit_id"], rec["domain"],
                    rec["domain_seq"], rec["pdv"], rec["bultos"], rec["pure_pallets"], rec["amount"],
                    rec["out_route"], rec["tot_udt"], rec["tot_dia"], rec["hectoliters"], rec["weight"],
                    rec["picking"], rec["avg_bultos"], rec["avg_value"], rec["avg_hl"], rec["total_loads"],
                    rec["comprobantes"], datetime.now().isoformat(timespec="seconds"),
                ),
            )
            if exists:
                updated += 1
            else:
                inserted += 1
    return {"inserted": inserted, "updated": updated, "removed": removed}


def route_rows(planning_date: str, division: str = "") -> list[dict[str, Any]]:
    if postgres_enabled():
        query = f"""
            SELECT pr.*, {PG_ROUTE_EFFECTIVE_DATE}::text planning_date,
                   {PG_ROUTE_EFFECTIVE_DIVISION} division,
                   COALESCE(l.sort_order,999) locality_order
            FROM planning_routes pr
            {PG_ROUTE_DAY_JOINS}
            LEFT JOIN localities l ON l.name=pr.locality
             AND l.division={PG_ROUTE_LOCALITY_DIVISION}
            WHERE {PG_ROUTE_EFFECTIVE_DATE}=?
        """
    else:
        query = """
            SELECT pr.*, COALESCE(l.sort_order,999) locality_order
            FROM planning_routes pr
            LEFT JOIN localities l ON l.name=pr.locality
             AND l.division=CASE pr.division WHEN 'TW' THEN 'TRELEW' WHEN 'PM' THEN 'PUERTO MADRYN' ELSE pr.division END
            WHERE pr.planning_date=?
        """
    params: list[Any] = [planning_date]
    if division and division != "TODAS":
        query += f" AND {PG_ROUTE_EFFECTIVE_DIVISION if postgres_enabled() else 'pr.division'}=?"
        params.append(route_division_for_db(division))
    if postgres_enabled():
        query += f" ORDER BY CASE WHEN ({PG_ROUTE_EFFECTIVE_DIVISION}) = 'PM' THEN 1 WHEN ({PG_ROUTE_EFFECTIVE_DIVISION}) = 'TW' THEN 2 ELSE 3 END, locality_order, pr.domain, pr.domain_seq"
    else:
        query += " ORDER BY CASE pr.division WHEN 'PM' THEN 1 WHEN 'PUERTO MADRYN' THEN 1 WHEN 'TW' THEN 2 WHEN 'TRELEW' THEN 2 ELSE 3 END, locality_order, pr.domain, pr.domain_seq"
    with db() as con:
        return [normalize_route_output(dict(r)) for r in con.execute(query, params).fetchall()]


def dates_list() -> list[str]:
    with db() as con:
        if postgres_enabled():
            rows = con.execute(
                f"""
                SELECT DISTINCT {PG_ROUTE_EFFECTIVE_DATE}::text planning_date
                FROM planning_routes pr
                {PG_ROUTE_DAY_JOINS}
                WHERE {PG_ROUTE_EFFECTIVE_DATE} IS NOT NULL
                ORDER BY planning_date DESC
                """
            ).fetchall()
            return [normalize_date_value(r[0]) or str(r[0] or "") for r in rows]
        return [
            normalize_date_value(r[0]) or str(r[0] or "")
            for r in con.execute("SELECT DISTINCT planning_date FROM planning_routes ORDER BY planning_date DESC").fetchall()
        ]


def master_payload() -> dict[str, Any]:
    with db() as con:
        employees = [dict(r) for r in con.execute(
            "SELECT id,employee_code,name,division,base_locality,can_driver,can_helper,active FROM employees ORDER BY division,name"
        ).fetchall()]
        localities = [dict(r) for r in con.execute(
            "SELECT id,name,division,sort_order,active FROM localities ORDER BY division,sort_order,name"
        ).fetchall()]
        domain_people = [dict(r) for r in con.execute(
            "SELECT id,domain,employee_name,role,priority,active FROM vehicle_people ORDER BY domain,role,priority,employee_name"
        ).fetchall()]
        employee_filters = [dict(r) for r in con.execute(
            """
            SELECT id,employee_name,division,locality,can_driver,can_helper,active
            FROM employee_locality_roles
            ORDER BY division,locality,employee_name
            """
        ).fetchall()]
    return {"employees": employees, "localities": localities, "domain_people": domain_people,
            "employee_filters": employee_filters}


def normalize_catalog_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if "division" in item:
            item["division"] = display_division(item.get("division"))
        normalized.append(item)
    return normalized


def options_for_routes(planning_date: str) -> dict[str, Any]:
    with db() as con:
        novelty_names = {r[0] for r in con.execute(
            "SELECT employee_name FROM personnel_novelties WHERE novelty_date=?", (planning_date,)
        ).fetchall()}
        employees = [dict(r) for r in con.execute(
            f"SELECT name,division,base_locality,can_driver,can_helper FROM employees WHERE {active_condition('employees')} ORDER BY division,base_locality,name"
        ).fetchall()]
        localities = [dict(r) for r in con.execute(
            f"SELECT name,division,sort_order FROM localities WHERE {active_condition('localities')} ORDER BY division,sort_order,name"
        ).fetchall()]
        preferred = [dict(r) for r in con.execute(
            f"SELECT domain,employee_name,role,priority FROM vehicle_people WHERE {active_condition('vehicle_people')} ORDER BY domain,role,priority"
        ).fetchall()]
        employee_filters = [dict(r) for r in con.execute(
            f"""
            SELECT employee_name,division,locality,can_driver,can_helper
            FROM employee_locality_roles WHERE {active_condition('employee_locality_roles')}
            ORDER BY division,locality,employee_name
            """
        ).fetchall()]
    return {"employees": normalize_catalog_rows(employees), "localities": normalize_catalog_rows(localities),
            "preferred": preferred, "employee_filters": normalize_catalog_rows(employee_filters),
            "novelty_names": sorted(novelty_names)}


def validate_assignments(planning_date: str, routes: list[dict[str, Any]], confirm: bool = False) -> list[str]:
    errors: list[str] = []
    # Regla operativa:
    # - La misma formación puede repetirse en otro camión durante la misma fecha.
    # - Una persona debe mantener el mismo tipo de función durante la fecha
    #   (CHOFER o AYUDANTE).
    # - Una persona no puede ocupar dos puestos dentro del mismo camión.
    assignments: dict[str, dict[str, Any]] = {}
    with db() as con:
        novelty_names = {canonical(r[0]) for r in con.execute(
            "SELECT employee_name FROM personnel_novelties WHERE novelty_date=?", (planning_date,)
        ).fetchall()}
        role_map = {
            canonical(r["name"]): {
                "division": canonical(display_division(r["division"])),
                "can_driver": int(r["can_driver"] or 0),
                "can_helper": int(r["can_helper"] or 0),
                "active": int(r["active"] or 0),
            }
            for r in con.execute("SELECT name,division,can_driver,can_helper,active FROM employees").fetchall()
        }
    for route in routes:
        domain = canonical(route.get("domain"))
        label = f"{domain} ({route.get('division','')})"
        if confirm and not canonical(route.get("driver")):
            errors.append(f"{label}: falta asignar chofer.")
        if confirm and not canonical(route.get("locality")):
            errors.append(f"{label}: falta seleccionar localidad.")

        names_in_route: dict[str, str] = {}
        for field, role in (("driver", "CHOFER"), ("helper1", "AYUDANTE"), ("helper2", "AYUDANTE")):
            name = canonical(route.get(field))
            if not name:
                continue
            if name in novelty_names:
                errors.append(f"{name} tiene una novedad cargada para {planning_date} y no puede asignarse.")

            previous_slot = names_in_route.get(name)
            if previous_slot:
                errors.append(
                    f"{name} ya ocupa el puesto {previous_slot} en {domain}; no puede tener dos funciones dentro del mismo camión."
                )
                continue
            names_in_route[name] = role

            previous = assignments.get(name)
            if previous:
                if previous["role"] != role:
                    errors.append(
                        f"{name} ya está asignado como {previous['role']} en esta fecha y no puede cambiar a {role}."
                    )
                elif domain in previous["domains"]:
                    errors.append(f"{name} ya está asignado como {role} en el camión {domain}.")
                else:
                    previous["domains"].add(domain)
            else:
                assignments[name] = {"role": role, "domains": {domain}}

            flags = role_map.get(name)
            if not flags:
                errors.append(f"{name} no existe en la base de personal.")
            else:
                route_division = canonical(display_division(route.get("division")))
                if not flags["active"]:
                    errors.append(f"{name} está inactivo en la base de personal.")
                elif flags["division"] != route_division:
                    errors.append(
                        f"{name} pertenece a {flags['division']} y no puede asignarse en una salida de {route_division}."
                    )
                elif role == "CHOFER" and not flags["can_driver"]:
                    errors.append(f"{name} no está habilitado como chofer.")
                elif role == "AYUDANTE" and not flags["can_helper"]:
                    errors.append(f"{name} no está habilitado como ayudante.")
    return list(dict.fromkeys(errors))


def sync_recargas(con: sqlite3.Connection, route_id: int) -> None:
    route = con.execute("SELECT * FROM planning_routes WHERE id=?", (route_id,)).fetchone()
    if not route:
        return
    qty = int(route["recarga_qty"] or 0)
    if postgres_enabled():
        recarga_date = normalize_date_value(route["planning_date"]) or str(route["planning_date"] or "")
        base = display_division(route["division"])
        day = con.execute(
            """
            INSERT INTO dias_operativos(fecha,base,estado)
            VALUES(?,?,?)
            ON CONFLICT(fecha,base) DO UPDATE SET fecha=excluded.fecha
            RETURNING id
            """,
            (recarga_date, base, "BORRADOR"),
        ).fetchone()
        dia_operativo_id = str(day["id"] if isinstance(day, dict) else day[0]) if day else ""
        if not dia_operativo_id:
            raise ValueError(f"No se pudo resolver dia_operativo_id para recargas de {recarga_date} / {base}.")
        con.execute("DELETE FROM recargas WHERE recarga_date=? AND dominio=?", (recarga_date, route["domain"]))
        if qty <= 0 and "RECARGA" in canonical(route["observations"]):
            qty = 1
            con.execute("UPDATE planning_routes SET recarga_qty=1 WHERE id=?", (route_id,))
        if qty <= 0:
            return
        start, end, _ = recarga_period(recarga_date)
        for name, role in ((route["driver"], "CHOFER"), (route["helper1"], "AYUDANTE 1"), (route["helper2"], "AYUDANTE 2")):
            name = canonical(name)
            if not name:
                continue
            con.execute(
                """
                INSERT INTO recargas(dia_operativo_id,dominio,recarga_date,employee_name,role,quantity,period_start,period_end)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (dia_operativo_id, route["domain"], recarga_date, name, role, qty, start, end),
            )
        return

    con.execute("DELETE FROM recargas WHERE route_id=?", (route_id,))
    if qty <= 0 and "RECARGA" in canonical(route["observations"]):
        qty = 1
        con.execute("UPDATE planning_routes SET recarga_qty=1 WHERE id=?", (route_id,))
    if qty <= 0:
        return
    start, end, _ = recarga_period(route["planning_date"])
    for name, role in ((route["driver"], "CHOFER"), (route["helper1"], "AYUDANTE 1"), (route["helper2"], "AYUDANTE 2")):
        name = canonical(name)
        if not name:
            continue
        con.execute(
            """
            INSERT INTO recargas(recarga_date,route_id,employee_name,role,quantity,period_start,period_end)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(route_id,employee_name,role) DO UPDATE SET quantity=excluded.quantity,
                period_start=excluded.period_start,period_end=excluded.period_end
            """,
            (route["planning_date"], route_id, name, role, qty, start, end),
        )


def save_routes(planning_date: str, routes: list[dict[str, Any]], confirm: bool, division: str = "TODAS") -> None:
    if not routes:
        raise ValueError("No hay salidas para guardar en la división seleccionada.")

    # Validar la fecha completa, aun cuando se guarde solo TW o PM. Así se conserva
    # la regla de que una persona mantenga el mismo tipo de función durante el día.
    current = route_rows(planning_date)
    incoming = {str(r.get("id") or "").strip(): r for r in routes}
    merged: list[dict[str, Any]] = []
    for row in current:
        replacement = incoming.get(str(row.get("id") or "").strip())
        if replacement:
            updated = dict(row)
            for field in ("rendicion", "driver", "helper1", "helper2", "locality", "observations", "recarga_qty", "kms"):
                updated[field] = replacement.get(field, updated.get(field))
            merged.append(updated)
        else:
            merged.append(row)

    errors = validate_assignments(planning_date, merged, confirm=False)
    if confirm:
        selected_ids = set(incoming)
        selected_rows = [r for r in merged if str(r.get("id") or "").strip() in selected_ids]
        errors.extend(validate_assignments(planning_date, selected_rows, confirm=True))
    errors = list(dict.fromkeys(errors))
    if errors:
        raise ValueError("\n".join(errors))

    allowed_division = canonical(division)
    with db() as con:
        for route in routes:
            route_id = str(route.get("id") or "").strip()
            if not route_id:
                continue
            existing = con.execute(
                "SELECT division FROM planning_routes WHERE id=? AND planning_date=?",
                (route_id, planning_date),
            ).fetchone()
            if not existing:
                continue
            if allowed_division not in ("", "TODAS") and display_division(existing["division"]) != display_division(allowed_division):
                continue
            con.execute(
                """
                UPDATE planning_routes SET rendicion=?,driver=?,helper1=?,helper2=?,locality=?,
                    observations=?,recarga_qty=?,kms=?,status=?,updated_at=? WHERE id=? AND planning_date=?
                """,
                (
                    str(route.get("rendicion", "") or "").strip(), canonical(route.get("driver")),
                    canonical(route.get("helper1")), canonical(route.get("helper2")), canonical(route.get("locality")),
                    str(route.get("observations", "") or "").strip(), int(route.get("recarga_qty", 0) or 0),
                    safe_number(route.get("kms")) if canonical(route.get("locality")) == "SIERRA GRANDE" else 0,
                    "CONFIRMADO" if confirm else "BORRADOR", datetime.now().isoformat(timespec="seconds"),
                    route_id, planning_date,
                ),
            )
            sync_recargas(con, route_id)


def copy_last_assignments(planning_date: str) -> int:
    routes = route_rows(planning_date)
    copied = 0
    used: set[str] = {
        canonical(name)
        for route in routes
        for name in (route.get("driver"), route.get("helper1"), route.get("helper2"))
        if canonical(name)
    }
    with db() as con:
        for route in routes:
            # Preserve anything already completed manually on the current date.
            if any(canonical(route.get(field)) for field in ("driver", "helper1", "helper2")):
                continue
            previous = con.execute(
                """
                SELECT driver,helper1,helper2,locality
                FROM planning_routes
                WHERE planning_date<? AND division=? AND domain=? AND domain_seq=?
                ORDER BY planning_date DESC LIMIT 1
                """,
                (planning_date, route["division"], route["domain"], route["domain_seq"]),
            ).fetchone()
            if not previous:
                continue
            values = [canonical(previous["driver"]), canonical(previous["helper1"]), canonical(previous["helper2"])]
            if any(v and v in used for v in values):
                continue
            for v in values:
                if v:
                    used.add(v)
            con.execute(
                "UPDATE planning_routes SET driver=?,helper1=?,helper2=?,locality=?,status='BORRADOR',updated_at=? WHERE id=?",
                (values[0], values[1], values[2], canonical(previous["locality"]), datetime.now().isoformat(timespec="seconds"), route["id"]),
            )
            copied += 1
    return copied


def summary(planning_date: str) -> dict[str, Any]:
    with db() as con:
        if postgres_enabled():
            row = con.execute(
                f"""
                SELECT COUNT(*) units,COALESCE(SUM(pdv),0) pdv,COALESCE(SUM(bultos),0) bultos,
                       COALESCE(SUM(hectoliters),0) hl,
                       SUM(CASE WHEN COALESCE(driver,'')='' THEN 1 ELSE 0 END) pending_driver,
                       SUM(CASE WHEN COALESCE(locality,'')='' THEN 1 ELSE 0 END) pending_locality,
                       SUM(CASE WHEN status='CONFIRMADO' THEN 1 ELSE 0 END) confirmed
                FROM planning_routes pr
                {PG_ROUTE_DAY_JOINS}
                WHERE {PG_ROUTE_EFFECTIVE_DATE}=?
                """,
                (planning_date,),
            ).fetchone()
        else:
            row = con.execute(
                """
                SELECT COUNT(*) units,COALESCE(SUM(pdv),0) pdv,COALESCE(SUM(bultos),0) bultos,
                       COALESCE(SUM(hectoliters),0) hl,
                       SUM(CASE WHEN driver='' THEN 1 ELSE 0 END) pending_driver,
                       SUM(CASE WHEN locality='' THEN 1 ELSE 0 END) pending_locality,
                       SUM(CASE WHEN status='CONFIRMADO' THEN 1 ELSE 0 END) confirmed
                FROM planning_routes WHERE planning_date=?
                """, (planning_date,)
            ).fetchone()
        recargas = con.execute("SELECT COALESCE(SUM(quantity),0) FROM recargas WHERE recarga_date=?", (planning_date,)).fetchone()[0]
    result = dict(row) if row else {}
    result["recargas"] = recargas
    result["unassigned"] = sum(1 for row in unassigned(planning_date) if not row.get("reason"))
    return result


def unassigned(planning_date: str, division: str = "") -> list[dict[str, Any]]:
    routes = route_rows(planning_date, division)
    assigned = {canonical(name) for r in routes for name in (r["driver"], r["helper1"], r["helper2"]) if canonical(name)}
    with db() as con:
        query = f"SELECT name,division,base_locality,can_driver,can_helper FROM employees WHERE {active_condition('employees')}"
        params: list[Any] = []
        if division and division != "TODAS":
            variants = list(dict.fromkeys([display_division(division), route_division_for_db(division)]))
            query += " AND division IN (" + ",".join("?" for _ in variants) + ")"
            params.extend(variants)
        query += " ORDER BY division,base_locality,name"
        staff = con.execute(query, params).fetchall()
        saved = {canonical(r["employee_name"]): dict(r) for r in con.execute(
            "SELECT * FROM personnel_novelties WHERE novelty_date=?", (planning_date,)
        ).fetchall()}
    output = []
    for p in staff:
        name = canonical(p["name"])
        if name in assigned:
            continue
        existing = saved.get(name, {})
        role = "CHOFER / AYUDANTE" if p["can_driver"] and p["can_helper"] else ("CHOFER" if p["can_driver"] else "AYUDANTE")
        output.append({
            "employee_name": name, "division": display_division(p["division"]), "base_locality": p["base_locality"],
            "role": role, "reason": existing.get("reason", ""), "notes": existing.get("notes", ""),
        })
    return output


def save_novelties(planning_date: str, rows: list[dict[str, Any]]) -> None:
    assigned = {canonical(name) for r in route_rows(planning_date) for name in (r["driver"], r["helper1"], r["helper2"]) if canonical(name)}
    with db() as con:
        for row in rows:
            name = canonical(row.get("employee_name"))
            reason = canonical(row.get("reason"))
            if not name:
                continue
            if name in assigned:
                raise ValueError(f"{name} está asignado a una salida y no puede registrar una novedad.")
            if reason not in NOVELTY_REASONS:
                raise ValueError(f"Debe seleccionar una novedad válida para {name}.")
            con.execute(
                """
                INSERT INTO personnel_novelties(novelty_date,employee_name,division,role,reason,notes)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(novelty_date,employee_name) DO UPDATE SET division=excluded.division,
                    role=excluded.role,reason=excluded.reason,notes=excluded.notes
                """,
                (planning_date, name, canonical(row.get("division")), canonical(row.get("role")), reason, str(row.get("notes", "") or "")),
            )


def novelty_rows(planning_date: str) -> list[dict[str, Any]]:
    with db() as con:
        return [dict(r) for r in con.execute(
            """SELECT * FROM personnel_novelties WHERE novelty_date=? ORDER BY CASE UPPER(TRIM(division)) WHEN 'TRELEW' THEN 1 WHEN 'PUERTO MADRYN' THEN 2 ELSE 3 END, employee_name""", (planning_date,)
        ).fetchall()]


def recarga_rows(start_date: str, end_date: str, division: str = "") -> dict[str, Any]:
    try:
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
    except (TypeError, ValueError):
        raise ValueError("Debe indicar un rango de fechas válido.")
    if start > end:
        raise ValueError("La fecha Desde no puede ser posterior a la fecha Hasta.")

    params: list[Any] = [start.isoformat(), end.isoformat()]
    if postgres_enabled():
        division_sql = ""
        if division and division != "TODAS":
            division_sql = " AND d.base IN (?,?)"
            params.extend([display_division(division), route_division_for_db(division)])

        detail_query = f"""
            SELECT COALESCE(NULLIF(r.recarga_date::text,''), d.fecha::text) recarga_date,
                   r.employee_name,r.role,d.base division,'' domain,'' locality,r.quantity
            FROM recargas r
            JOIN dias_operativos d ON d.id=r.dia_operativo_id
            WHERE COALESCE(NULLIF(r.recarga_date::text,''), d.fecha::text) BETWEEN ? AND ? {division_sql}
            ORDER BY recarga_date DESC,d.base,r.employee_name
        """
        summary_query = f"""
            SELECT r.employee_name,
                   GROUP_CONCAT(DISTINCT r.role) roles,
                   d.base division,
                   SUM(r.quantity) recargas,
                   COUNT(DISTINCT COALESCE(NULLIF(r.recarga_date::text,''), d.fecha::text)) dias_con_recarga
            FROM recargas r
            JOIN dias_operativos d ON d.id=r.dia_operativo_id
            WHERE COALESCE(NULLIF(r.recarga_date::text,''), d.fecha::text) BETWEEN ? AND ? {division_sql}
            GROUP BY r.employee_name,d.base
            ORDER BY recargas DESC,r.employee_name
        """
    else:
        division_sql = ""
        if division and division != "TODAS":
            division_sql = " AND pr.division=?"
            params.append(division)

        detail_query = f"""
            SELECT r.recarga_date,r.employee_name,r.role,pr.division,pr.domain,
                   pr.locality,r.quantity
            FROM recargas r
            JOIN planning_routes pr ON pr.id=r.route_id
            WHERE r.recarga_date BETWEEN ? AND ? {division_sql}
            ORDER BY r.recarga_date DESC,pr.division,r.employee_name,pr.domain
        """
        summary_query = f"""
            SELECT r.employee_name,
                   GROUP_CONCAT(DISTINCT r.role) roles,
                   pr.division,
                   SUM(r.quantity) recargas,
                   COUNT(DISTINCT r.recarga_date) dias_con_recarga
            FROM recargas r
            JOIN planning_routes pr ON pr.id=r.route_id
            WHERE r.recarga_date BETWEEN ? AND ? {division_sql}
            GROUP BY r.employee_name,pr.division
            ORDER BY recargas DESC,r.employee_name
        """
    with db() as con:
        detail = [dict(r) for r in con.execute(detail_query, params).fetchall()]
        summary = [dict(r) for r in con.execute(summary_query, params).fetchall()]
    for row in detail:
        row["recarga_date"] = normalize_date_value(row.get("recarga_date")) or str(row.get("recarga_date") or "")
        row["division"] = display_division(row.get("division"))
    for row in summary:
        row["division"] = display_division(row.get("division"))

    reference_start, reference_end, period_label = recarga_period(start)
    same_period = start.isoformat() == reference_start and end.isoformat() == reference_end
    label = period_label if same_period else f"{start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}"
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "label": label,
        "period_26_25": period_label,
        "detail": detail,
        "summary": summary,
    }


def save_master(table: str, rows: list[dict[str, Any]]) -> None:
    with db() as con:
        if table == "employees":
            clean_names = set()
            for row in rows:
                name = canonical(row.get("name"))
                if not name:
                    continue
                clean_names.add(name)
                con.execute(
                    """
                    INSERT INTO employees(employee_code,name,division,primary_role,active,base_locality,can_driver,can_helper)
                    VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(name) DO UPDATE SET employee_code=excluded.employee_code,division=excluded.division,
                        active=excluded.active,base_locality=excluded.base_locality,can_driver=excluded.can_driver,
                        can_helper=excluded.can_helper,primary_role=excluded.primary_role
                    """,
                    (
                        str(row.get("employee_code", "") or ""), name, canonical(row.get("division")),
                        "CHOFER" if row.get("can_driver") else "AYUDANTE", int(bool(row.get("active", True))),
                        canonical(row.get("base_locality")), int(bool(row.get("can_driver"))), int(bool(row.get("can_helper"))),
                    ),
                )
            if clean_names:
                placeholders = ",".join("?" for _ in clean_names)
                con.execute(f"UPDATE employees SET active=? WHERE name NOT IN ({placeholders})", (active_value("employees", False), *tuple(clean_names)))
        elif table == "localities":
            con.execute("DELETE FROM localities")
            for row in rows:
                name = canonical(row.get("name")); div = canonical(row.get("division"))
                if name and div:
                    con.execute(
                        "INSERT OR IGNORE INTO localities(name,division,sort_order,active) VALUES(?,?,?,?)",
                        (name, div, int(row.get("sort_order", 99) or 99), int(bool(row.get("active", True)))),
                    )
        elif table == "domain_people":
            con.execute("DELETE FROM vehicle_people")
            for row in rows:
                domain = canonical(row.get("domain")); name = canonical(row.get("employee_name")); role = canonical(row.get("role"))
                if domain and name and role in {"CHOFER", "AYUDANTE"}:
                    con.execute(
                        "INSERT OR IGNORE INTO vehicle_people(domain,employee_name,role,priority,active) VALUES(?,?,?,?,?)",
                        (domain, name, role, int(row.get("priority", 99) or 99), int(bool(row.get("active", True)))),
                    )
        elif table == "employee_filters":
            con.execute("DELETE FROM employee_locality_roles")
            for row in rows:
                name = canonical(row.get("employee_name")); div = canonical(row.get("division")); locality = canonical(row.get("locality"))
                if name and div and locality:
                    con.execute(
                        """
                        INSERT OR IGNORE INTO employee_locality_roles
                        (employee_name,division,locality,can_driver,can_helper,active) VALUES(?,?,?,?,?,?)
                        """,
                        (name, div, locality, int(bool(row.get("can_driver"))),
                         int(bool(row.get("can_helper"))), int(bool(row.get("active", True)))),
                    )
        else:
            raise ValueError("Maestro no permitido.")



def delete_master(table: str, row_id: int) -> None:
    if not row_id:
        raise ValueError("No se recibió el registro a eliminar.")
    with db() as con:
        if table == "employees":
            row = con.execute("SELECT name FROM employees WHERE id=?", (row_id,)).fetchone()
            if not row:
                raise ValueError("El empleado ya no existe.")
            name = canonical(row["name"])
            con.execute("DELETE FROM employee_locality_roles WHERE employee_name=?", (name,))
            con.execute("DELETE FROM vehicle_people WHERE employee_name=?", (name,))
            con.execute("DELETE FROM employees WHERE id=?", (row_id,))
        elif table == "employee_filters":
            con.execute("DELETE FROM employee_locality_roles WHERE id=?", (row_id,))
        elif table == "localities":
            row = con.execute("SELECT name,division FROM localities WHERE id=?", (row_id,)).fetchone()
            if not row:
                raise ValueError("La localidad ya no existe.")
            con.execute("DELETE FROM employee_locality_roles WHERE locality=? AND division=?", (row["name"], row["division"]))
            con.execute("DELETE FROM localities WHERE id=?", (row_id,))
        elif table == "domain_people":
            con.execute("DELETE FROM vehicle_people WHERE id=?", (row_id,))
        else:
            raise ValueError("Maestro no permitido.")


def kms_rows(start: str, end: str, division: str = "TODAS") -> dict[str, Any]:
    div = canonical(division)
    query = """
        SELECT planning_date,division,domain,rendicion,driver,helper1,helper2,locality,kms
        FROM planning_routes
        WHERE planning_date BETWEEN ? AND ?
          AND UPPER(TRIM(locality))='SIERRA GRANDE'
          AND COALESCE(kms,0)>0
    """
    params: list[Any] = [start, end]
    if div and div != "TODAS":
        query += " AND division=?"
        params.append(div)
    query += " ORDER BY planning_date DESC,division,domain"
    with db() as con:
        detail = [dict(r) for r in con.execute(query, params).fetchall()]
    grouped: dict[tuple[str,str], dict[str,Any]] = {}
    for row in detail:
        key = (canonical(row.get("driver")) or "SIN CHOFER", canonical(row.get("division")))
        item = grouped.setdefault(key, {"driver": key[0], "division": key[1], "trips": 0, "kms": 0.0})
        item["trips"] += 1
        item["kms"] += safe_number(row.get("kms"))
    return {"summary": sorted(grouped.values(), key=lambda x: (x["division"], x["driver"])), "detail": detail}

def history_rows(params: dict[str, str]) -> dict[str, Any]:
    start = params.get("start", "1900-01-01")
    end = params.get("end", "2999-12-31")
    division = canonical(params.get("division", ""))
    locality = canonical(params.get("locality", ""))
    employee = canonical(params.get("employee", ""))
    if postgres_enabled():
        query = f"""
            SELECT {PG_ROUTE_EFFECTIVE_DATE}::text planning_date,
                   {PG_ROUTE_EFFECTIVE_DIVISION} division,
                   pr.domain,pr.unit_id,pr.pdv,pr.bultos,pr.rendicion,pr.driver,pr.helper1,pr.helper2,
                   pr.locality,pr.observations,pr.recarga_qty,pr.kms,pr.status
            FROM planning_routes pr
            {PG_ROUTE_DAY_JOINS}
            WHERE {PG_ROUTE_EFFECTIVE_DATE} BETWEEN ? AND ?
        """
    else:
        query = """
            SELECT planning_date,division,domain,unit_id,pdv,bultos,rendicion,driver,helper1,helper2,
                   locality,observations,recarga_qty,kms,status
            FROM planning_routes WHERE planning_date BETWEEN ? AND ?
        """
    qparams: list[Any] = [start, end]
    if division and division != "TODAS":
        query += f" AND {PG_ROUTE_EFFECTIVE_DIVISION if postgres_enabled() else 'division'}=?"; qparams.append(route_division_for_db(division))
    if locality:
        query += " AND UPPER(TRIM(locality)) LIKE ?"; qparams.append(f"%{locality}%")
    if employee:
        query += " AND ? IN (driver,helper1,helper2)"; qparams.append(employee)
    query += " ORDER BY planning_date DESC,division,locality,domain LIMIT 5000"
    nov_query = "SELECT novelty_date,employee_name,division,role,reason,notes FROM personnel_novelties WHERE novelty_date BETWEEN ? AND ?"
    nov_params: list[Any] = [start, end]
    if division and division != "TODAS":
        nov_query += " AND division=?"; nov_params.append(division)
    if employee:
        nov_query += " AND employee_name=?"; nov_params.append(employee)
    nov_query += " ORDER BY novelty_date DESC,employee_name LIMIT 5000"
    with db() as con:
        routes = [normalize_route_output(dict(r)) for r in con.execute(query, qparams).fetchall()]
        novelties = [dict(r) for r in con.execute(nov_query, nov_params).fetchall()]
    grouped: dict[tuple[str, str], dict[str, float]] = {}
    capacities = {"TRELEW": 6, "PUERTO MADRYN": 5}
    for r in routes:
        key = (r["planning_date"], canonical(r["division"]))
        g = grouped.setdefault(key, {"fleet_used": 0, "pdv": 0.0, "bultos": 0.0})
        g["fleet_used"] += 1
        g["pdv"] += safe_number(r["pdv"])
        g["bultos"] += safe_number(r["bultos"])
    indicators: list[dict[str, Any]] = []
    dates = sorted({k[0] for k in grouped}, reverse=True)
    for d in dates:
        total_used = total_pdv = total_bultos = 0.0
        for div in ("TRELEW", "PUERTO MADRYN"):
            g = grouped.get((d, div), {"fleet_used": 0, "pdv": 0.0, "bultos": 0.0})
            cap = capacities[div]
            used = int(g["fleet_used"]); pdv = g["pdv"]; bultos = g["bultos"]
            indicators.append({"date": d, "division": div, "fleet_used": used, "fleet_total": cap, "fleet_free": max(cap-used,0), "utilization": (used/cap*100 if cap else 0), "pdv": pdv, "bultos": bultos, "drop_size": (bultos/pdv if pdv else 0)})
            total_used += used; total_pdv += pdv; total_bultos += bultos
        indicators.append({"date": d, "division": "TOTAL DDV", "fleet_used": int(total_used), "fleet_total": 11, "fleet_free": max(11-int(total_used),0), "utilization": total_used/11*100, "pdv": total_pdv, "bultos": total_bultos, "drop_size": (total_bultos/total_pdv if total_pdv else 0)})
    return {"routes": routes, "novelties": novelties, "indicators": indicators}


def is_cyo_route(route: dict[str, Any]) -> bool:
    return bool(re.search(r"(^|\s)CYO($|\s)", f"{route.get('domain','')} {route.get('locality','')} {route.get('observations','')}", re.I))


def _fmt_ar(value: Any, decimals: int = 0) -> str:
    n = safe_number(value)
    return f"{n:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def create_backup_zip() -> Path:
    EXPORTS_DIR.mkdir(exist_ok=True)
    backups_dir = APP_DIR / "Respaldos"
    backups_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = backups_dir / f"Planning_DDV_backup_{stamp}.zip"
    if postgres_enabled():
        with db() as con, zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            manifest: dict[str, Any] = {"storage": "supabase_postgres", "created_at": now_iso(), "tables": {}}
            for table in STORAGE_TABLES:
                rows = [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
                manifest["tables"][table] = len(rows)
                z.writestr(f"{table}.json", json.dumps(rows, ensure_ascii=False, default=str, indent=2))
                csv_output = io.StringIO()
                if rows:
                    writer = csv.DictWriter(csv_output, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
                z.writestr(f"{table}.csv", csv_output.getvalue().encode("utf-8-sig"))
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for asset_name in ("server.py", "web/index.html"):
                p = APP_DIR / asset_name
                if p.exists():
                    z.write(p, asset_name)
    else:
        db_copy = backups_dir / f"operations_ddv_{stamp}.db"
        with db() as con:
            target = sqlite3.connect(db_copy)
            con.backup(target)
            target.close()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(db_copy, db_copy.name)
            for asset_name in ("server.py", "web/index.html"):
                p = APP_DIR / asset_name
                if p.exists():
                    z.write(p, asset_name)
        try:
            db_copy.unlink()
        except OSError:
            pass
    return zip_path


def storage_diagnostics() -> dict[str, Any]:
    result: dict[str, Any] = {
        "backend": "supabase_postgres" if postgres_enabled() else "sqlite_local",
        "supabase_url_configured": bool(SUPABASE_URL),
        "supabase_secret_key_configured": bool(SUPABASE_SECRET_KEY),
        "postgres_dsn_configured": bool(SUPABASE_DB_URL),
        "connection": "ERROR",
        "tables": {},
        "error": "",
    }
    try:
        with db() as con:
            for table in STORAGE_TABLES:
                result["tables"][table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        result["connection"] = "OK"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result



def whatsapp_rows(planning_date: str, division: str = "TODAS") -> list[dict[str, Any]]:
    rows = []
    for source_row in route_rows(planning_date, division):
        row = dict(source_row)
        row["is_cyo"] = bool(is_cyo_route(row))
        rows.append(row)
    order = {"TRELEW": 0, "PUERTO MADRYN": 1}
    # Distribución primero y CYO siempre al final dentro de cada base.
    return sorted(
        rows,
        key=lambda r: (
            order.get(canonical(r.get("division")), 9),
            1 if r.get("is_cyo") else 0,
            canonical(r.get("domain")),
            int(r.get("domain_seq") or 1),
        ),
    )


def save_whatsapp_observations(planning_date: str, rows: list[dict[str, Any]]) -> None:
    if not planning_date:
        raise ValueError("Debe seleccionar una fecha.")
    with db() as con:
        for row in rows:
            route_id = str(row.get("id") or "").strip()
            if not route_id:
                continue
            con.execute(
                "UPDATE planning_routes SET whatsapp_observation=?, updated_at=? WHERE id=? AND planning_date=?",
                (str(row.get("whatsapp_observation", "") or "").strip(), datetime.now().isoformat(timespec="seconds"), route_id, planning_date),
            )


def whatsapp_html(planning_date: str, division: str = "TODAS") -> str:
    rows = whatsapp_rows(planning_date, division)
    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")
    distribution_rows = [r for r in rows if not r.get("is_cyo")]
    cyo_rows = [r for r in rows if r.get("is_cyo")]
    distribution_bultos = sum(safe_number(r.get("bultos")) for r in distribution_rows)

    def esc(v: Any) -> str:
        return html.escape(str(v or "-"))

    blocks = []
    for div, color in (("TRELEW", "#1f7a28"), ("PUERTO MADRYN", "#1167a8")):
        subset = [r for r in rows if canonical(r.get("division")) == div]
        if not subset:
            continue
        dist = [r for r in subset if not r.get("is_cyo")]
        cyo = [r for r in subset if r.get("is_cyo")]
        dist_bultos = sum(safe_number(r.get("bultos")) for r in dist)
        trs = ''.join(
            f"<tr class={'cyo-row' if r.get('is_cyo') else 'distribution-row'}>"
            f"<td><b>{esc(r.get('domain'))}</b>{'<span class=cyo-tag>CYO</span>' if r.get('is_cyo') else ''}</td>"
            f"<td>{esc(r.get('rendicion'))}</td><td>{esc(r.get('locality'))}</td>"
            f"<td>{html.escape(str(r.get('whatsapp_observation') or ''))}</td></tr>"
            for r in subset
        )
        blocks.append(f"""
        <section class='base'><div class='base-title' style='background:{color}'><div>{div}</div><div class='base-summary'><span class='base-summary-left'><b>{len(dist)}</b> Distribución + <b>{len(cyo)}</b> CYO</span><span class='base-summary-right'><b>{_fmt_ar(dist_bultos,0)}</b> bultos Distribución</span></div></div>
        <table><thead style='background:{color}'><tr><th>CAMIÓN</th><th>PLANILLA</th><th>LOCALIDAD</th><th>OBSERVACIONES PREVIAS A LA SALIDA</th></tr></thead><tbody>{trs}</tbody></table></section>""")

    return f"""<!doctype html><html><head><meta charset='utf-8'><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#fff;font-family:Segoe UI,Arial,sans-serif;color:#102a3d}}.sheet{{width:1400px;margin:0 auto;padding:18px 22px 24px;background:#fff}}
    .head{{display:flex;justify-content:space-between;align-items:center;border-bottom:4px solid #0b4266;padding:0 0 14px}}h1{{font-size:34px;margin:0;color:#0b4266}}.sub{{font-size:16px;color:#60778a;margin-top:4px}}.date{{text-align:right;border:1px solid #aac1d0;border-radius:10px;padding:10px 18px}}.date small{{display:block;font-weight:800;color:#61798a}}.date b{{font-size:24px;color:#0b4266}}
    .global-summary{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px}}.global-summary>div{{border:1px solid #aac1d0;border-radius:10px;padding:12px;text-align:center;background:#f6fafc}}.global-summary b{{display:block;font-size:25px;color:#0b4266}}.global-summary small{{font-size:12px;font-weight:800;text-transform:uppercase;color:#61798a}}
    .base{margin-top:20px;border:1px solid #aac1d0;border-radius:10px;overflow:hidden}.base-title{color:white;font-size:24px;font-weight:900;padding:12px 18px;display:flex;justify-content:space-between;align-items:center;gap:16px}.base-summary{font-size:15px;background:rgba(255,255,255,.16);padding:7px 13px;border-radius:18px;font-weight:600;display:flex;align-items:center;justify-content:space-between;gap:22px;min-width:520px}.base-summary b{font-size:17px}.base-summary-left{text-align:left}.base-summary-right{text-align:right;white-space:nowrap}
    table{{width:100%;border-collapse:collapse;font-size:18px}}th{{color:#fff;padding:11px 14px;text-align:left;font-size:14px}}td{{padding:14px;border-top:1px solid #d8e2e8}}tbody tr:nth-child(even){{background:#f3f7f9}}th:nth-child(1){{width:18%}}th:nth-child(2){{width:18%}}th:nth-child(3){{width:22%}}th:nth-child(4){{width:42%}}.cyo-row{{background:#fff5df!important;border-top:3px solid #e2a51b}}.cyo-tag{{display:inline-block;margin-left:8px;background:#e39c0c;color:#fff;border-radius:10px;padding:2px 7px;font-size:11px;font-weight:900}}
    .foot{{margin-top:18px;color:#6c8291;font-size:12px;text-align:right}}
    </style></head><body><div class='sheet'><div class='head'><div><h1>SALIDA DIARIA · WHATSAPP</h1><div class='sub'>Camiones, planillas, localidades y observaciones previas</div></div><div class='date'><small>FECHA DE SALIDA</small><b>{display_date}</b></div></div>
    <div class='global-summary'><div><b>{len(distribution_rows)}</b><small>Camiones Distribución</small></div><div><b>{len(cyo_rows)}</b><small>Camiones CYO</small></div><div><b>{_fmt_ar(distribution_bultos,0)}</b><small>Bultos Distribución</small></div></div>
    {''.join(blocks)}<div class='foot'>Planning DDV · Distribuidora del Valle</div></div></body></html>"""


def render_whatsapp_image(planning_date: str, division: str = "TODAS") -> Path:
    EXPORTS_DIR.mkdir(exist_ok=True)
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path=EXPORTS_DIR/f"whatsapp_{planning_date}_{stamp}.html"
    png_path=EXPORTS_DIR/f"whatsapp_{planning_date}_{stamp}.png"
    html_path.write_text(whatsapp_html(planning_date, division), encoding="utf-8")
    edge=_edge_executable()
    if edge is None:
        raise RuntimeError("No se encontró Microsoft Edge para generar la imagen.")
    cmd=[str(edge),"--headless","--disable-gpu","--hide-scrollbars","--force-device-scale-factor=1","--window-size=1440,2400",f"--screenshot={png_path}",html_path.resolve().as_uri()]
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    if result.returncode!=0 or not png_path.exists():
        raise RuntimeError(result.stderr.strip() or "No se pudo generar la imagen WhatsApp.")
    return png_path


def whatsapp_drivers_html(planning_date: str, division: str = "TODAS") -> str:
    rows = whatsapp_rows(planning_date, division)
    novelties = novelty_rows(planning_date)
    if division and division != "TODAS":
        novelties = [n for n in novelties if canonical(n.get("division")) == canonical(division)]
    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")
    distribution_rows = [r for r in rows if not r.get("is_cyo")]
    cyo_rows = [r for r in rows if r.get("is_cyo")]
    distribution_bultos = sum(safe_number(r.get("bultos")) for r in distribution_rows)

    def esc(v: Any) -> str:
        return html.escape(str(v or "-"))

    blocks = []
    for div, color in (("TRELEW", "#1f7a28"), ("PUERTO MADRYN", "#1167a8")):
        subset = [r for r in rows if canonical(r.get("division")) == div]
        if not subset:
            continue
        dist = [r for r in subset if not r.get("is_cyo")]
        cyo = [r for r in subset if r.get("is_cyo")]
        dist_bultos = sum(safe_number(r.get("bultos")) for r in dist)
        trs = "".join(
            f"<tr class={'cyo-row' if r.get('is_cyo') else 'distribution-row'}>"
            f"<td><b>{esc(r.get('domain'))}</b>{'<span class=cyo-tag>CYO</span>' if r.get('is_cyo') else ''}</td>"
            f"<td>{esc(r.get('rendicion'))}</td>"
            f"<td>{esc(r.get('driver'))}</td>"
            f"<td>{esc(r.get('helper1'))}</td>"
            f"<td>{esc(r.get('helper2'))}</td>"
            f"<td>{_fmt_ar(r.get('pdv'),0)}</td>"
            f"<td>{_fmt_ar(r.get('bultos'),1)}</td>"
            f"<td>{esc(r.get('locality'))}</td>"
            f"<td>{html.escape(str(r.get('observations') or ''))}</td></tr>"
            for r in subset
        )
        blocks.append(f"""
        <section class='base'><div class='base-title' style='background:{color}'><div>{div}</div><div class='base-summary'><span><b>{len(dist)}</b> Distribución + <b>{len(cyo)}</b> CYO</span><span><b>{_fmt_ar(dist_bultos,0)}</b> bultos Distribución</span></div></div>
        <table><thead style='background:{color}'><tr><th>DOMINIO</th><th>PLANILLA</th><th>CHOFER</th><th>AYUDANTE 1</th><th>AYUDANTE 2</th><th>PDV</th><th>BULTOS</th><th>LOCALIDAD</th><th>OBSERVACIONES</th></tr></thead><tbody>{trs}</tbody></table></section>""")

    novelty_rows_html = "".join(
        "<tr>"
        f"<td><b>{esc(n.get('employee_name'))}</b></td>"
        f"<td>{esc(n.get('division'))}</td>"
        f"<td>{esc(n.get('role'))}</td>"
        f"<td><span class='novelty-badge'>{esc(n.get('reason'))}</span></td>"
        f"<td>{html.escape(str(n.get('notes') or ''))}</td></tr>"
        for n in novelties
    )
    if not novelty_rows_html:
        novelty_rows_html = "<tr><td colspan='5' class='empty-novelty'>Sin novedades registradas para la fecha.</td></tr>"
    novelty_block = f"""
    <section class='novelties'>
      <div class='novelties-title'><div>NOVEDADES DEL DÍA</div><div>{len(novelties)} registros</div></div>
      <table><thead><tr><th>EMPLEADO</th><th>DIVISIÓN</th><th>ROL HABILITADO</th><th>NOVEDAD</th><th>DETALLE</th></tr></thead><tbody>{novelty_rows_html}</tbody></table>
    </section>"""

    return f"""<!doctype html><html><head><meta charset='utf-8'><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#fff;font-family:Segoe UI,Arial,sans-serif;color:#102a3d}}.sheet{{width:1800px;margin:0 auto;padding:18px 22px 24px;background:#fff}}
    .head{{display:flex;justify-content:space-between;align-items:center;border-bottom:4px solid #0b4266;padding:0 0 14px}}h1{{font-size:34px;margin:0;color:#0b4266}}.sub{{font-size:16px;color:#60778a;margin-top:4px}}.date{{text-align:right;border:1px solid #aac1d0;border-radius:10px;padding:10px 18px}}.date small{{display:block;font-weight:800;color:#61798a}}.date b{{font-size:24px;color:#0b4266}}
    .global-summary{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px}}.global-summary>div{{border:1px solid #aac1d0;border-radius:10px;padding:12px;text-align:center;background:#f6fafc}}.global-summary b{{display:block;font-size:25px;color:#0b4266}}.global-summary small{{font-size:12px;font-weight:900;text-transform:uppercase;color:#61798a}}
    .base{{margin-top:20px;border:1px solid #aac1d0;border-radius:10px;overflow:hidden}}.base-title{{color:white;font-size:24px;font-weight:900;padding:12px 18px;display:flex;justify-content:space-between;align-items:center;gap:16px}}.base-summary{{font-size:15px;background:rgba(255,255,255,.16);padding:7px 13px;border-radius:18px;font-weight:600;display:flex;justify-content:space-between;gap:30px;min-width:520px}}.base-summary b{{font-size:17px}}
    table{{width:100%;border-collapse:collapse;font-size:14px}}th{{color:#fff;padding:10px 9px;text-align:left;font-size:11px;white-space:nowrap}}td{{padding:11px 9px;border-top:1px solid #d8e2e8}}tbody tr:nth-child(even){{background:#f3f7f9}}.cyo-row{{background:#fff5df!important;border-top:3px solid #e2a51b}}.cyo-tag{{display:inline-block;margin-left:6px;background:#e39c0c;color:#fff;border-radius:10px;padding:2px 7px;font-size:10px;font-weight:900}}.novelties{{margin-top:20px;border:1px solid #c7d5df;border-radius:10px;overflow:hidden}}.novelties-title{{background:#0b4266;color:#fff;font-size:22px;font-weight:900;padding:12px 18px;display:flex;justify-content:space-between;align-items:center}}.novelties-title div:last-child{{font-size:13px;background:rgba(255,255,255,.16);padding:6px 12px;border-radius:16px}}.novelties th{{background:#17631b}}.novelty-badge{{display:inline-block;background:#eef4f7;border:1px solid #b8cbd7;border-radius:12px;padding:4px 9px;font-weight:900;color:#0b4266}}.empty-novelty{{text-align:center;color:#6c8291;padding:18px!important}}.foot{{margin-top:18px;color:#6c8291;font-size:12px;text-align:right}}
    </style></head><body><div class='sheet'><div class='head'><div><h1>SALIDA DIARIA · CHOFERES</h1><div class='sub'>Formación operativa, localidades y detalle de salida</div></div><div class='date'><small>FECHA DE SALIDA</small><b>{display_date}</b></div></div>
    <div class='global-summary'><div><b>{len(distribution_rows)}</b><small>Camiones Distribución</small></div><div><b>{len(cyo_rows)}</b><small>Camiones CYO</small></div><div><b>{_fmt_ar(distribution_bultos,0)}</b><small>Bultos Distribución</small></div></div>
    {''.join(blocks)}{novelty_block}<div class='foot'>Planning DDV · Distribuidora del Valle</div></div></body></html>"""


def render_whatsapp_drivers_image(planning_date: str, division: str = "TODAS") -> Path:
    EXPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = EXPORTS_DIR / f"whatsapp_choferes_{planning_date}_{stamp}.html"
    png_path = EXPORTS_DIR / f"whatsapp_choferes_{planning_date}_{stamp}.png"
    html_path.write_text(whatsapp_drivers_html(planning_date, division), encoding="utf-8")
    edge = _edge_executable()
    if edge is None:
        raise RuntimeError("No se encontró Microsoft Edge para generar la imagen.")
    cmd = [str(edge), "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=1800,2800", f"--screenshot={png_path}", html_path.resolve().as_uri()]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not png_path.exists():
        raise RuntimeError(result.stderr.strip() or "No se pudo generar la imagen para choferes.")
    return png_path

def report_html(planning_date: str, division: str = "TODAS", logo_src: str = "cid:ddv_logo", include_novelties: bool = True) -> str:
    routes = route_rows(planning_date, division)
    novelties = novelty_rows(planning_date) if include_novelties else []
    if division and division != "TODAS":
        novelties = [n for n in novelties if canonical(n.get("division")) == canonical(division)]
    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")
    operational = [r for r in routes if not is_cyo_route(r)]
    total_pdv = sum(safe_number(r.get("pdv")) for r in operational)
    total_bultos = sum(safe_number(r.get("bultos")) for r in operational)
    used = len(operational)
    util_total = used / 11 * 100 if 11 else 0
    drop_total = total_bultos / total_pdv if total_pdv else 0
    hero_bytes = (WEB_DIR / "assets" / "hero_banner_v35.png").read_bytes()
    hero_uri = "data:image/png;base64," + base64.b64encode(hero_bytes).decode("ascii")

    def fmt(v: Any, d: int = 0) -> str:
        return _fmt_ar(v, d)

    def division_block(div: str) -> str:
        rows = [r for r in routes if canonical(r.get("division")) == div]
        minor = [r for r in rows if not is_cyo_route(r)]
        cyo = [r for r in rows if is_cyo_route(r)]
        cap = 6 if div == "TRELEW" else 5
        used_b = len(minor)
        pdv = sum(safe_number(r.get("pdv")) for r in minor)
        bultos = sum(safe_number(r.get("bultos")) for r in minor)
        drop = bultos / pdv if pdv else 0
        util = used_b / cap * 100 if cap else 0
        cls = "tw" if div == "TRELEW" else "pm"
        color = "#4ad53b" if cls == "tw" else "#24a8ff"
        rows_html = "".join(
            "<tr>" +
            f"<td><b>{html.escape(str(r.get('domain') or '-'))}</b></td>" +
            f"<td>{html.escape(str(r.get('rendicion') or '-'))}</td>" +
            f"<td>{html.escape(str(r.get('driver') or '-'))}</td>" +
            f"<td>{html.escape(str(r.get('helper1') or '-'))}</td>" +
            f"<td>{html.escape(str(r.get('helper2') or '-'))}</td>" +
            f"<td>{fmt(r.get('pdv'))}</td>" +
            f"<td>{fmt(r.get('bultos'),1)}</td>" +
            f"<td>{html.escape(str(r.get('locality') or '-'))}</td>" +
            f"<td>{html.escape(str(r.get('observations') or '-'))}</td></tr>"
            for r in minor
        )
        cyo_html = ""
        if cyo:
            cyo_rows = "".join(
                "<tr>" +
                f"<td><b>{html.escape(str(r.get('domain') or '-'))}</b></td>" +
                f"<td>{html.escape(str(r.get('rendicion') or '-'))}</td>" +
                f"<td>{html.escape(str(r.get('driver') or '-'))}</td>" +
                f"<td>{html.escape(str(r.get('helper1') or '-'))}</td>" +
                f"<td>{html.escape(str(r.get('helper2') or '-'))}</td>" +
                f"<td>{fmt(r.get('pdv'))}</td>" +
                f"<td>{fmt(r.get('bultos'),1)}</td>" +
                f"<td>{html.escape(str(r.get('locality') or '-'))}</td>" +
                f"<td>{html.escape(str(r.get('observations') or '-'))}</td></tr>"
                for r in cyo
            )
            cyo_html = f"<div class='cyo-title'>CYO · {div}</div><table><thead><tr><th>Dominio</th><th>Planilla</th><th>Chofer</th><th>Ayudante 1</th><th>Ayudante 2</th><th>PDV</th><th>Bultos</th><th>Localidad</th><th>Observaciones</th></tr></thead><tbody>{cyo_rows}</tbody></table>"
        return f"""
        <section class='base {cls}'>
          <div class='base-head'><h2>📍 {div}</h2><span>{len(rows)} unidades</span></div>
          <div class='base-kpis'>
            <div><i>🚚</i><b>{used_b}</b><small>Camiones sin CYO</small></div>
            <div><i>🏪</i><b>{fmt(pdv)}</b><small>PDV</small></div>
            <div><i>📦</i><b>{fmt(bultos,1)}</b><small>Bultos</small></div>
            <div><i>🎯</i><b>{fmt(drop,1)}</b><small>Drop size</small></div>
            <div><div class='ring' style='--p:{min(util,100)};--c:{color}'><strong>{fmt(util,1)}%</strong></div><small>Utilización flota</small></div>
          </div>
          <table><thead><tr><th>Dominio</th><th>Planilla</th><th>Chofer</th><th>Ayudante 1</th><th>Ayudante 2</th><th>PDV</th><th>Bultos</th><th>Localidad</th><th>Observaciones</th></tr></thead><tbody>{rows_html}</tbody></table>
          {cyo_html}
        </section>"""

    sections = "".join(division_block(div) for div in ("TRELEW", "PUERTO MADRYN") if any(canonical(r.get("division")) == div for r in routes))

    novelty_html = ""
    if novelties:
        novelty_rows_html = "".join(
            "<tr>"
            f"<td><b>{html.escape(str(n.get('employee_name') or '-'))}</b></td>"
            f"<td>{html.escape(str(n.get('division') or '-'))}</td>"
            f"<td>{html.escape(str(n.get('role') or '-'))}</td>"
            f"<td><span class='novelty-badge'>{html.escape(str(n.get('reason') or '-'))}</span></td>"
            f"<td>{html.escape(str(n.get('notes') or '-'))}</td></tr>"
            for n in novelties
        )
        novelty_html = f"""
        <section class='novelties'>
          <div class='novelties-head'><h2>🔔 NOVEDADES DEL DÍA</h2><span>{len(novelties)} registros</span></div>
          <table><thead><tr><th>Empleado</th><th>División</th><th>Rol habilitado</th><th>Novedad</th><th>Detalle</th></tr></thead><tbody>{novelty_rows_html}</tbody></table>
        </section>"""

    return f"""<!doctype html><html><head><meta charset='utf-8'><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#061522;font-family:Segoe UI,Arial,sans-serif;color:#fff;padding:0}}.mail{{width:1440px;margin:auto;background:#061522;padding:8px 10px 10px}}
    .hero{{height:265px;border:1px solid #294a60;border-radius:14px;background:url('{hero_uri}') center/cover no-repeat;position:relative;overflow:hidden}}.hero:after{{content:'';position:absolute;inset:0;background:linear-gradient(90deg,rgba(4,18,29,.04),rgba(4,18,29,.04))}}
    .date{{position:absolute;z-index:2;right:22px;top:22px;background:rgba(4,18,29,.78);border:1px solid #36596f;border-radius:12px;padding:14px 22px;text-align:right}}.date small{{display:block;color:#a8bdca;text-transform:uppercase;font-weight:800;font-size:13px;letter-spacing:.3px}}.date b{{font-size:30px}}
    .summary{{display:grid;grid-template-columns:repeat(7,1fr);margin-top:16px;border:1px solid #2b5068;border-radius:14px;overflow:hidden;background:#0a2233}}.summary>div{{min-height:128px;display:grid;place-items:center;align-content:center;text-align:center;border-right:1px solid #2b5068;padding:10px}}.summary>div:last-child{{border-right:0}}.summary i{{font-style:normal;font-size:34px}}.summary b{{font-size:36px;line-height:1}}.summary small{{font-size:13px;text-transform:uppercase;color:#bdd0dd;font-weight:900;letter-spacing:.3px}}.summary em{{font-style:normal;font-size:12px;color:#7fa1b5;margin-top:6px}}.summary .accent b{{color:#ffc22d}}
    .base{{margin-top:18px;border:1px solid;border-radius:14px;overflow:hidden;background:#071b29}}.base.tw{{border-color:#35962f}}.base.pm{{border-color:#1681d1}}.base-head{{display:flex;justify-content:space-between;align-items:center;padding:16px 22px}}.tw .base-head{{background:linear-gradient(90deg,#17631b,#0c3517)}}.pm .base-head{{background:linear-gradient(90deg,#075fa6,#073458)}}.base-head h2{{margin:0;font-size:28px}}.base-head span{{background:rgba(255,255,255,.12);padding:8px 14px;border-radius:20px;font-size:14px;font-weight:800}}
    .base-kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;padding:16px}}.base-kpis>div{{min-height:132px;border:1px solid;border-radius:12px;display:grid;place-items:center;align-content:center;text-align:center;padding:10px}}.tw .base-kpis>div{{background:linear-gradient(145deg,#123c24,#09291a);border-color:#2d7136}}.pm .base-kpis>div{{background:linear-gradient(145deg,#0d3154,#081f37);border-color:#245e95}}.base-kpis i{{font-style:normal;font-size:38px}}.base-kpis b{{font-size:34px;line-height:1}}.base-kpis small{{font-size:12px;text-transform:uppercase;color:#c2d3de;font-weight:900;letter-spacing:.3px}}
    .ring{{--p:0;--c:#24a8ff;width:96px;height:96px;border-radius:50%;background:conic-gradient(var(--c) calc(var(--p)*1%),rgba(255,255,255,.10) 0);display:grid;place-items:center;position:relative}}.ring:after{{content:'';width:72px;height:72px;border-radius:50%;background:#0a2131;position:absolute}}.tw .ring:after{{background:#0b2918}}.ring strong{{position:relative;z-index:2;font-size:20px}}
    table{{width:calc(100% - 32px);margin:0 16px 16px;border-collapse:collapse;font-size:13px;border:1px solid #24465d}}th{{padding:11px 10px;text-align:left;text-transform:uppercase;color:#fff;font-size:12px;letter-spacing:.3px}}.tw th{{background:#185a20}}.pm th{{background:#075b9d}}td{{padding:10px;border-top:1px solid #17394d;color:#eef6fb}}tr:nth-child(even) td{{background:#0a2232}}
    .cyo-title{{margin:12px 16px 8px;color:#ffc04b;font-size:12px;font-weight:900;text-transform:uppercase}}.novelties{{margin-top:18px;border:1px solid #d49a21;border-radius:14px;overflow:hidden;background:#101e28}}.novelties-head{{display:flex;justify-content:space-between;align-items:center;padding:15px 20px;background:linear-gradient(90deg,#6d4800,#2b2514)}}.novelties-head h2{{margin:0;font-size:23px;color:#ffd56a}}.novelties-head span{{background:rgba(255,213,106,.14);border:1px solid rgba(255,213,106,.35);padding:7px 13px;border-radius:18px;color:#ffe5a3;font-size:13px;font-weight:800}}.novelties table{{border-color:#725821}}.novelties th{{background:#8a5b00;color:#fff4d0}}.novelties td{{background:#122633}}.novelties tr:nth-child(even) td{{background:#0d202c}}.novelty-badge{{display:inline-block;padding:5px 10px;border-radius:14px;background:#fff1cc;color:#8f5b00;font-weight:900;font-size:12px}}.foot{{margin-top:16px;padding:14px 18px;border:1px solid #29495e;border-radius:10px;color:#91a9b8;font-size:12px;display:flex;justify-content:space-between}}
    </style></head><body><div class='mail'>
    <div class='hero'><div class='date'><small>Fecha operativa</small><b>{display_date}</b></div></div>
    <div class='summary'>
      <div><i>🚛</i><b>11</b><small>Flota total</small><em>TW 6 + PM 5</em></div>
      <div><i>🚚</i><b>{used}</b><small>Flota utilizada</small><em>{max(11-used,0)} sin asignación</em></div>
      <div class='accent'><i>◔</i><b>{fmt(util_total,1)}%</b><small>Utilización de flota</small><em>Sobre 11 unidades</em></div>
      <div><i>📦</i><b>{fmt(total_bultos,1)}</b><small>Bultos a repartir</small><em>Sin CYO</em></div>
      <div><i>🏪</i><b>{fmt(total_pdv)}</b><small>PDV</small><em>Total del día</em></div>
      <div><i>🎯</i><b>{fmt(drop_total,1)}</b><small>Drop size</small><em>Bultos / PDV</em></div>
      <div><i>📍</i><b>TW / PM</b><small>Resumen por base</small><em>Trelew arriba</em></div>
    </div>
    {sections}
    {novelty_html}
    <div class='foot'><span>Reporte generado automáticamente por Planning DDV</span><span>Distribuidora del Valle · Control de Distribución</span></div>
    </div></body></html>"""

def outlook_mail_html(planning_date: str, division: str = "TODAS", preview: bool = False) -> str:
    """Mail híbrido: compacto en Outlook clásico y adaptable en móvil.

    Outlook clásico usa el motor de Word y no aplica bien max-width ni media queries.
    El diseño de escritorio queda contenido en 780 px mediante condicionales MSO.
    En clientes móviles compatibles, las tablas se reemplazan por tarjetas verticales.
    """
    routes = route_rows(planning_date, division)
    novelties = novelty_rows(planning_date)
    if division and division != "TODAS":
        novelties = [n for n in novelties if canonical(n.get("division")) == canonical(division)]
    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")
    logo_src = "/assets/ddv_logo.png" if preview else "cid:ddv_logo"
    total_units = len(routes)
    total_pdv = sum(safe_number(r.get("pdv")) for r in routes)
    total_bultos = sum(safe_number(r.get("bultos")) for r in routes)

    def esc(value: Any) -> str:
        return html.escape(str(value if value not in (None, "") else "-"))

    def cell(value: Any, width: str, bold: bool = False, align: str = "left") -> str:
        weight = "font-weight:700;" if bold else ""
        return (
            f'<td width="{width}" align="{align}" valign="middle" '
            f'style="width:{width};padding:6px 4px;border-top:1px solid #e2e9ee;'
            f'font-family:Segoe UI,Arial,sans-serif;font-size:8px;line-height:11px;'
            f'color:#12314b;overflow-wrap:anywhere;word-break:break-word;{weight}">{esc(value)}</td>'
        )

    def desktop_table(rows: list[dict[str, Any]]) -> str:
        headers = [
            ("DOMINIO", "9%"), ("PLAN.", "6%"), ("CHOFER", "14%"),
            ("AYUDANTE 1", "14%"), ("AYUDANTE 2", "14%"), ("PDV", "5%"),
            ("BULTOS", "7%"), ("LOCALIDAD", "12%"), ("OBSERVACIONES", "19%"),
        ]
        head = "".join(
            f'<th width="{w}" align="left" style="width:{w};padding:6px 4px;'
            'background-color:#edf3f6;border-bottom:1px solid #d6e0e7;'
            'font-family:Segoe UI,Arial,sans-serif;font-size:7px;line-height:9px;'
            'color:#36546a;font-weight:700;">' + h + '</th>'
            for h, w in headers
        )
        body = []
        for r in rows:
            body.append(
                '<tr>'
                + cell(r.get("domain"), "9%", True)
                + cell(r.get("rendicion"), "6%")
                + cell(r.get("driver"), "14%")
                + cell(r.get("helper1"), "14%")
                + cell(r.get("helper2"), "14%")
                + cell(_fmt_ar(r.get("pdv")), "5%", False, "right")
                + cell(_fmt_ar(r.get("bultos"), 1), "7%", False, "right")
                + cell(r.get("locality"), "12%")
                + cell(r.get("observations"), "19%")
                + '</tr>'
            )
        return (
            '<div class="desktop-routes" style="display:block;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;border-collapse:collapse;table-layout:fixed;background-color:#ffffff;">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
        )

    def mobile_cards(rows: list[dict[str, Any]]) -> str:
        cards: list[str] = []
        for r in rows:
            cards.append(
                '<table class="route-card" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                'style="width:100%;border-collapse:collapse;margin:0 0 8px 0;border:1px solid #d8e3e9;background:#ffffff;">'
                f'<tr><td colspan="2" style="padding:8px 9px;background:#edf3f6;font:700 12px Segoe UI,Arial;color:#12314b;">{esc(r.get("domain"))}'
                f'<span style="float:right;font-size:10px;color:#5d7485;">Planilla {esc(r.get("rendicion"))}</span></td></tr>'
                f'<tr><td width="34%" style="padding:5px 8px;border-top:1px solid #e3eaf0;font:700 9px Segoe UI,Arial;color:#60778a;">CHOFER</td><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:10px Segoe UI,Arial;color:#12314b;">{esc(r.get("driver"))}</td></tr>'
                f'<tr><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:700 9px Segoe UI,Arial;color:#60778a;">AYUDANTE 1</td><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:10px Segoe UI,Arial;color:#12314b;">{esc(r.get("helper1"))}</td></tr>'
                f'<tr><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:700 9px Segoe UI,Arial;color:#60778a;">AYUDANTE 2</td><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:10px Segoe UI,Arial;color:#12314b;">{esc(r.get("helper2"))}</td></tr>'
                f'<tr><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:700 9px Segoe UI,Arial;color:#60778a;">OPERACIÓN</td><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:10px Segoe UI,Arial;color:#12314b;">{_fmt_ar(r.get("pdv"))} PDV · {_fmt_ar(r.get("bultos"),1)} bultos</td></tr>'
                f'<tr><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:700 9px Segoe UI,Arial;color:#60778a;">LOCALIDAD</td><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:10px Segoe UI,Arial;color:#12314b;">{esc(r.get("locality"))}</td></tr>'
                f'<tr><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:700 9px Segoe UI,Arial;color:#60778a;">OBS.</td><td style="padding:5px 8px;border-top:1px solid #e3eaf0;font:10px Segoe UI,Arial;color:#12314b;">{esc(r.get("observations"))}</td></tr>'
                '</table>'
            )
        return '<div class="mobile-routes" style="display:none;max-height:0;overflow:hidden;mso-hide:all;">' + ''.join(cards) + '</div>'

    def route_content(rows: list[dict[str, Any]]) -> str:
        return desktop_table(rows) + mobile_cards(rows)

    sections: list[str] = []
    for div in ("PUERTO MADRYN", "TRELEW"):
        subset = [r for r in routes if canonical(r.get("division")) == div]
        if not subset:
            continue
        minor = [r for r in subset if not is_cyo_route(r)]
        cyo = [r for r in subset if is_cyo_route(r)]
        minor_pdv = sum(safe_number(r.get("pdv")) for r in minor)
        minor_bultos = sum(safe_number(r.get("bultos")) for r in minor)
        accent = "#07898f" if div == "PUERTO MADRYN" else "#2b78b8"
        pale = "#eaf8f8" if div == "PUERTO MADRYN" else "#edf6fd"
        blocks: list[str] = []
        if minor:
            blocks.append(
                '<tr><td style="padding:10px 10px 4px 10px;">'
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                'style="width:100%;border-collapse:collapse;border:1px solid #cfe1e7;">'
                '<tr><td style="padding:8px 9px;background-color:#edf9f8;color:#08777b;'
                'font-family:Segoe UI,Arial,sans-serif;font-size:11px;font-weight:700;">DISTRIBUCIÓN MINORISTA</td>'
                f'<td align="right" style="padding:8px 9px;background-color:#edf9f8;color:#08777b;'
                f'font-family:Segoe UI,Arial,sans-serif;font-size:9px;font-weight:700;">{len(minor)} unidades</td></tr>'
                f'<tr><td colspan="2">{route_content(minor)}</td></tr></table></td></tr>'
            )
        if cyo:
            blocks.append(
                '<tr><td style="padding:8px 10px 11px 10px;">'
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                'style="width:100%;border-collapse:collapse;border:1px solid #efc58e;">'
                f'<tr><td style="padding:8px 9px;background-color:#fff4e6;color:#b85d00;'
                f'font-family:Segoe UI,Arial,sans-serif;font-size:11px;font-weight:700;">CYO · {div}</td>'
                f'<td align="right" style="padding:8px 9px;background-color:#fff4e6;color:#b85d00;'
                f'font-family:Segoe UI,Arial,sans-serif;font-size:9px;font-weight:700;">{len(cyo)} unidades</td></tr>'
                f'<tr><td colspan="2">{route_content(cyo)}</td></tr></table></td></tr>'
            )
        sections.append(
            '<tr><td class="section-pad" style="padding:12px 10px 0 10px;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;border-collapse:collapse;border:1px solid #c9dce5;background-color:#ffffff;">'
            f'<tr><td style="padding:11px 12px;background-color:{pale};border-bottom:1px solid #c9dce5;'
            f'font-family:Segoe UI,Arial,sans-serif;font-size:17px;color:#0d3150;font-weight:700;"><span class="division-name">{div}</span></td>'
            f'<td align="right" style="padding:11px 12px;background-color:{pale};border-bottom:1px solid #c9dce5;'
            f'font-family:Segoe UI,Arial,sans-serif;font-size:9px;color:{accent};font-weight:700;">{len(subset)} unidades</td></tr>'
            '<tr><td colspan="2" style="padding:0;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;border-collapse:collapse;background-color:#fbfefe;">'
            f'<tr><td class="division-kpi" width="33.33%" align="center" style="padding:9px 4px;border-right:1px solid #dce7ec;'
            f'font-family:Segoe UI,Arial,sans-serif;color:{accent};"><b style="font-size:17px;">{len(minor)}</b><br>'
            '<span style="font-size:8px;color:#5d7485;font-weight:700;">CAMIONES SIN CYO</span></td>'
            f'<td class="division-kpi" width="33.33%" align="center" style="padding:9px 4px;border-right:1px solid #dce7ec;'
            f'font-family:Segoe UI,Arial,sans-serif;color:{accent};"><b style="font-size:17px;">{_fmt_ar(minor_pdv)}</b><br>'
            '<span style="font-size:8px;color:#5d7485;font-weight:700;">PDV</span></td>'
            f'<td class="division-kpi" width="33.33%" align="center" style="padding:9px 4px;font-family:Segoe UI,Arial,sans-serif;color:{accent};">'
            f'<b style="font-size:17px;">{_fmt_ar(minor_bultos,1)}</b><br>'
            '<span style="font-size:8px;color:#5d7485;font-weight:700;">BULTOS</span></td></tr></table></td></tr>'
            f'{"".join(blocks)}</table></td></tr>'
        )

    novelty_section = ""
    if novelties:
        nrows = "".join(
            '<tr>' + cell(n.get("division"), "20%") + cell(n.get("employee_name"), "30%", True)
            + cell(n.get("reason"), "20%", True) + cell(n.get("notes"), "30%") + '</tr>'
            for n in novelties
        )
        novelty_section = (
            '<tr><td class="section-pad" style="padding:12px 10px 0 10px;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;border-collapse:collapse;border:1px solid #d7e2e8;">'
            '<tr><td colspan="4" style="padding:9px 10px;background-color:#eef3f6;'
            'font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#12314b;font-weight:700;">NOVEDADES DE PERSONAL</td></tr>'
            '<tr><th style="padding:6px;background:#f6f8fa;font:700 7px Segoe UI,Arial;color:#36546a;">DIVISIÓN</th>'
            '<th style="padding:6px;background:#f6f8fa;font:700 7px Segoe UI,Arial;color:#36546a;">EMPLEADO</th>'
            '<th style="padding:6px;background:#f6f8fa;font:700 7px Segoe UI,Arial;color:#36546a;">NOVEDAD</th>'
            '<th style="padding:6px;background:#f6f8fa;font:700 7px Segoe UI,Arial;color:#36546a;">DETALLE</th></tr>'
            f'{nrows}</table></td></tr>'
        )

    return f'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<style type="text/css">
  body,table,td,a{{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;}}
  table,td{{mso-table-lspace:0pt;mso-table-rspace:0pt;}}
  img{{-ms-interpolation-mode:bicubic;}}
  @media only screen and (max-width:620px){{
    .email-shell{{width:100%!important;}}
    .outer-pad{{padding:0!important;}}
    .header-copy,.header-logo{{display:block!important;width:100%!important;text-align:left!important;}}
    .header-logo{{padding-top:9px!important;}}
    .logo-img{{width:175px!important;height:auto!important;}}
    .header-title{{font-size:22px!important;line-height:27px!important;}}
    .kpi-cell{{padding:11px 2px!important;}}
    .kpi-value{{font-size:19px!important;}}
    .section-pad{{padding-left:4px!important;padding-right:4px!important;}}
    .division-name{{font-size:15px!important;}}
    .division-kpi{{padding:7px 2px!important;}}
    .desktop-routes{{display:none!important;max-height:0!important;overflow:hidden!important;}}
    .mobile-routes{{display:block!important;max-height:none!important;overflow:visible!important;}}
    .route-card{{display:table!important;}}
    .footer-cell{{display:block!important;width:100%!important;text-align:left!important;padding:3px 0!important;}}
  }}
</style></head>
<body style="margin:0;padding:0;background-color:#eef3f7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#eef3f7" style="width:100%;border-collapse:collapse;background-color:#eef3f7;">
<tr><td class="outer-pad" align="center" style="padding:14px 6px;">
<!--[if mso]><table role="presentation" width="780" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table class="email-shell" role="presentation" width="780" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="width:100%;max-width:780px;border-collapse:collapse;background-color:#ffffff;border:1px solid #d5e1e8;">
<tr><td style="padding:12px 16px;border-bottom:1px solid #dbe4ea;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
<tr><td class="header-copy" valign="middle" style="font-family:Segoe UI,Arial,sans-serif;color:#0b2d4a;"><div style="font-size:10px;color:#60778a;font-weight:700;">DISTRIBUIDORA DEL VALLE · CONTROL DE DISTRIBUCIÓN</div><div class="header-title" style="font-size:25px;line-height:31px;font-weight:700;">SALIDA DIARIA</div><div style="font-size:11px;color:#60778a;">Detalle consolidado de camiones, dotación y localidades</div></td>
<td class="header-logo" width="205" align="right" valign="middle" style="width:205px;"><img class="logo-img" src="{logo_src}" width="190" height="48" alt="Distribuidora del Valle" style="display:block;width:190px;height:48px;border:0;outline:none;text-decoration:none;"></td></tr>
</table></td></tr>
<tr><td bgcolor="#0b3e63" style="background-color:#0b3e63;padding:0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
<tr><td class="kpi-cell" width="33.33%" align="center" style="padding:13px 4px;border-right:1px solid #51728c;font-family:Segoe UI,Arial,sans-serif;color:#ffffff;"><b class="kpi-value" style="font-size:22px;">{_fmt_ar(total_units)}</b><br><span style="font-size:9px;font-weight:700;color:#dceaf3;">CAMIONES</span></td>
<td class="kpi-cell" width="33.33%" align="center" style="padding:13px 4px;border-right:1px solid #51728c;font-family:Segoe UI,Arial,sans-serif;color:#ffffff;"><b class="kpi-value" style="font-size:22px;">{_fmt_ar(total_pdv)}</b><br><span style="font-size:9px;font-weight:700;color:#dceaf3;">PDV</span></td>
<td class="kpi-cell" width="33.33%" align="center" style="padding:13px 4px;font-family:Segoe UI,Arial,sans-serif;color:#ffffff;"><b class="kpi-value" style="font-size:22px;">{_fmt_ar(total_bultos,1)}</b><br><span style="font-size:9px;font-weight:700;color:#dceaf3;">BULTOS</span></td></tr>
</table></td></tr>
<tr><td align="right" style="padding:8px 12px 0 12px;font-family:Segoe UI,Arial,sans-serif;font-size:10px;color:#60778a;">Fecha: <b style="color:#15354f;">{display_date}</b></td></tr>
{''.join(sections)}{novelty_section}
<tr><td bgcolor="#075070" style="background-color:#075070;padding:10px 12px;font-family:Segoe UI,Arial,sans-serif;font-size:9px;color:#e0eef5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;"><tr><td class="footer-cell" style="font-family:Segoe UI,Arial,sans-serif;font-size:9px;color:#e0eef5;">Reporte generado automáticamente · Sistema Planning DDV</td><td class="footer-cell" align="right" style="font-family:Segoe UI,Arial,sans-serif;font-size:9px;color:#e0eef5;">Distribuidora del Valle · Control de Distribución</td></tr></table>
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''

def mail_html(planning_date: str, division: str = "TODAS", preview: bool = False) -> str:
    if preview:
        return planning_ddv_premium_visual_html(planning_date, division)
    return planning_ddv_outlook_hybrid_html(planning_date, division)


def asset_path(filename: str) -> Path:
    for candidate in (WEB_DIR / "assets" / filename, APP_DIR / filename):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No se encontró el recurso {filename}.")


def logo_data_uri() -> str:
    asset = mail_logo_asset()
    if not asset:
        return ""
    _, content_type, data = asset
    return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"


def _image_content_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return None


def mail_logo_asset() -> tuple[str, str, bytes] | None:
    for candidate in (
        APP_DIR / "ddv_logo.jpg",
        WEB_DIR / "ddv_logo.jpg",
        WEB_DIR / "assets" / "ddv_logo.png",
        APP_DIR / "ddv_logo.png",
    ):
        if not candidate.exists():
            continue
        data = candidate.read_bytes()
        content_type = _image_content_type(data)
        if not content_type:
            continue
        name = "ddv_logo.png" if content_type == "image/png" else "ddv_logo.jpg"
        return name, content_type, data
    return None


def mail_logo_data_uri() -> str:
    asset = mail_logo_asset()
    if not asset:
        return ""
    _, content_type, data = asset
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def planning_ddv_premium_visual_html(planning_date: str, division: str = "TODAS") -> str:
    routes = route_rows(planning_date, division)
    novelties = novelty_rows(planning_date)
    if division and division != "TODAS":
        novelties = [n for n in novelties if canonical(n.get("division")) == canonical(division)]
    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")
    division_label = "TW + PM" if not division or division == "TODAS" else display_division(division)
    hero_bytes = (WEB_DIR / "assets" / "hero_banner_v35.png").read_bytes()
    hero_uri = "data:image/png;base64," + base64.b64encode(hero_bytes).decode("ascii")

    def esc(value: Any) -> str:
        return html.escape(str(value if value not in (None, "") else "-"))

    def fmt(value: Any, decimals: int = 0) -> str:
        return _fmt_ar(value, decimals)

    def div_rows(div_name: str) -> list[dict[str, Any]]:
        return [r for r in routes if canonical(r.get("division")) == div_name]

    def minor_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if not is_cyo_route(r)]

    def cyo_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if is_cyo_route(r)]

    def totals(rows: list[dict[str, Any]]) -> dict[str, float]:
        pdv = sum(safe_number(r.get("pdv")) for r in rows)
        bultos = sum(safe_number(r.get("bultos")) for r in rows)
        return {
            "camiones": float(len(rows)),
            "pdv": pdv,
            "bultos": bultos,
            "drop": bultos / pdv if pdv else 0.0,
        }

    tw_all = div_rows("TRELEW")
    pm_all = div_rows("PUERTO MADRYN")
    tw_minor = minor_rows(tw_all)
    pm_minor = minor_rows(pm_all)
    all_minor = tw_minor + pm_minor
    tw_total = totals(tw_minor)
    pm_total = totals(pm_minor)
    valley_total = totals(all_minor)
    flota_total = 11
    flota_usada = int(valley_total["camiones"])
    util_total = flota_usada / flota_total * 100 if flota_total else 0

    def kpi(label: str, value: str, detail: str, icon: str, tone: str = "blue") -> str:
        return f"""
        <div class="kpi {tone}">
          <div class="kpi-icon">{icon}</div>
          <div class="kpi-value">{value}</div>
          <div class="kpi-label">{label}</div>
          <div class="kpi-detail">{detail}</div>
        </div>"""

    def base_chart() -> str:
        tw_b = tw_total["bultos"]
        pm_b = pm_total["bultos"]
        max_b = max(tw_b, pm_b, 1)
        tw_w = min(100, max(2, tw_b / max_b * 100))
        pm_w = min(100, max(2, pm_b / max_b * 100))
        return f"""
        <div class="kpi base-summary">
          <div class="kpi-icon">TW</div>
          <div class="base-line"><b>TRELEW</b><span>{fmt(tw_b,1)}</span><i><em style="width:{tw_w:.1f}%"></em></i></div>
          <div class="base-line pm"><b>PTO. MADRYN</b><span>{fmt(pm_b,1)}</span><i><em style="width:{pm_w:.1f}%"></em></i></div>
          <div class="kpi-label">Resumen por base</div>
        </div>"""

    def row_html(r: dict[str, Any]) -> str:
        helpers = " / ".join([str(v) for v in (r.get("helper1"), r.get("helper2")) if v not in (None, "", "-")]) or "-"
        return (
            "<tr>"
            f"<td class='strong'>{esc(r.get('domain'))}</td>"
            f"<td>{esc(r.get('rendicion'))}</td>"
            f"<td>{esc(r.get('driver'))}</td>"
            f"<td>{esc(helpers)}</td>"
            f"<td class='num'>{fmt(r.get('pdv'))}</td>"
            f"<td class='num'>{fmt(r.get('bultos'),1)}</td>"
            f"<td>{esc(r.get('locality'))}</td>"
            f"<td class='num'>{fmt(r.get('kms'))}</td>"
            f"<td>{esc(r.get('observations'))}</td>"
            "</tr>"
        )

    def rows_table(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "<div class='empty-section'>Sin unidades para esta sección.</div>"
        return (
            "<table><thead><tr><th>Dominio</th><th>Planilla</th><th>Chofer</th><th>Ayudantes</th>"
            "<th>PDV</th><th>Bultos</th><th>Localidad</th><th>KMS</th><th>Observaciones</th></tr></thead>"
            f"<tbody>{''.join(row_html(r) for r in rows)}</tbody></table>"
        )

    def base_block(title: str, rows: list[dict[str, Any]], cls: str, cap: int) -> str:
        minor = minor_rows(rows)
        cyo = cyo_rows(rows)
        m = totals(minor)
        util = len(minor) / cap * 100 if cap else 0
        cyo_block = ""
        if cyo:
            cyo_block = f"""
            <div class="cyo-title">CYO · {esc(title)}</div>
            {rows_table(cyo)}"""
        return f"""
        <section class="base {cls}">
          <div class="base-head">
            <h2>{esc(title)}</h2>
            <span>{len(rows)} unidades totales</span>
          </div>
          <div class="base-kpis">
            <div><b>{len(minor)}</b><small>Camiones sin CYO</small></div>
            <div><b>{fmt(m["pdv"])}</b><small>PDV</small></div>
            <div><b>{fmt(m["bultos"],1)}</b><small>Bultos</small></div>
            <div><b>{fmt(m["drop"],1)}</b><small>Drop size</small></div>
            <div><b>{fmt(util,1)}%</b><small>Utilización flota</small></div>
          </div>
          <div class="table-title">Distribución minorista</div>
          {rows_table(minor)}
          {cyo_block}
        </section>"""

    novelty_html = ""
    if novelties:
        novelty_rows_html = "".join(
            "<tr>"
            f"<td class='strong'>{esc(n.get('employee_name'))}</td>"
            f"<td>{esc(n.get('division'))}</td>"
            f"<td>{esc(n.get('role'))}</td>"
            f"<td><span class='novelty-badge'>{esc(n.get('reason'))}</span></td>"
            f"<td>{esc(n.get('notes'))}</td>"
            "</tr>"
            for n in novelties
        )
        novelty_html = f"""
        <section class="novelties">
          <div class="novelties-head"><h2>NOVEDADES DEL DÍA</h2><span>{len(novelties)} registros</span></div>
          <table><thead><tr><th>Empleado</th><th>Base</th><th>Rol habilitado</th><th>Novedad</th><th>Detalle</th></tr></thead><tbody>{novelty_rows_html}</tbody></table>
        </section>"""

    sections = "".join(
        block for block in (
            base_block("TRELEW", tw_all, "tw", 6) if tw_all else "",
            base_block("PUERTO MADRYN", pm_all, "pm", 5) if pm_all else "",
        )
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}body{{margin:0;background:#061522;font-family:Segoe UI,Arial,sans-serif;color:#fff;padding:0}}
.mail{{width:1440px;margin:0 auto;background:#061522;padding:10px 12px 12px}}
.hero{{height:265px;border:1px solid #294a60;border-radius:14px;background:url('{hero_uri}') center/cover no-repeat;position:relative;overflow:hidden}}
.date{{position:absolute;right:24px;top:22px;background:rgba(4,18,29,.84);border:1px solid #36596f;border-radius:12px;padding:14px 22px;text-align:right;box-shadow:0 10px 24px rgba(0,0,0,.28)}}
.date small{{display:block;color:#9db4c2;text-transform:uppercase;font-weight:900;font-size:13px;letter-spacing:.5px}}.date b{{font-size:31px;line-height:38px}}.date em{{display:block;font-style:normal;color:#6de65a;font-weight:800;font-size:13px}}
.summary{{display:grid;grid-template-columns:.9fr .9fr 1fr 1fr .75fr .9fr 1.45fr;gap:1px;margin-top:16px;border:1px solid #2b5068;border-radius:14px;overflow:hidden;background:#2b5068}}
.kpi{{min-height:128px;background:linear-gradient(145deg,#112f45,#071b29);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:10px}}
.kpi.green{{background:linear-gradient(145deg,#113f24,#082719)}}.kpi.gold{{background:linear-gradient(145deg,#49370a,#251d08)}}.kpi.cyan{{background:linear-gradient(145deg,#093d49,#061f2a)}}
.kpi-icon{{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:rgba(255,255,255,.08);font-size:20px;font-weight:900;color:#9feaff;margin-bottom:7px}}.green .kpi-icon{{color:#6ee35a}}.gold .kpi-icon{{color:#ffc847}}.cyan .kpi-icon{{color:#4bd5ef}}
.kpi-value{{font-size:36px;line-height:40px;font-weight:900;color:#fff}}.kpi-label{{font-size:12px;line-height:15px;text-transform:uppercase;color:#d3e4ef;font-weight:900;letter-spacing:.45px}}.kpi-detail{{font-size:12px;color:#86a4b7;margin-top:5px}}
.base-summary{{align-items:stretch;padding:12px 14px}}.base-summary .kpi-icon{{margin:0 auto 8px}}.base-line{{display:grid;grid-template-columns:76px 74px 1fr;gap:8px;align-items:center;color:#d7e8f1;font-size:11px;margin:4px 0}}.base-line span{{text-align:right;font-weight:900;color:#fff}}.base-line i{{height:9px;background:#16384d;border:1px solid #355b70;border-radius:8px;overflow:hidden}}.base-line em{{display:block;height:100%;background:linear-gradient(90deg,#41c931,#9df174)}}.base-line.pm em{{background:linear-gradient(90deg,#1187d7,#5cc8ff)}}
.base{{margin-top:18px;border:1px solid;border-radius:14px;overflow:hidden;background:#071b29}}.base.tw{{border-color:#35962f}}.base.pm{{border-color:#1681d1}}.base-head{{display:flex;justify-content:space-between;align-items:center;padding:17px 22px}}.tw .base-head{{background:linear-gradient(90deg,#17631b,#0c3517)}}.pm .base-head{{background:linear-gradient(90deg,#075fa6,#073458)}}.base-head h2{{margin:0;font-size:30px;letter-spacing:.5px}}.base-head span{{background:rgba(255,255,255,.13);padding:8px 14px;border-radius:20px;font-size:14px;font-weight:900}}
.base-kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;padding:16px}}.base-kpis>div{{min-height:112px;border:1px solid;border-radius:12px;display:grid;place-items:center;align-content:center;text-align:center;padding:10px}}.tw .base-kpis>div{{background:linear-gradient(145deg,#123c24,#09291a);border-color:#2d7136}}.pm .base-kpis>div{{background:linear-gradient(145deg,#0d3154,#081f37);border-color:#245e95}}.base-kpis b{{font-size:33px;line-height:38px}}.base-kpis small{{font-size:12px;text-transform:uppercase;color:#c2d3de;font-weight:900;letter-spacing:.3px}}
.table-title,.cyo-title{{margin:4px 16px 9px;color:#d7e9f4;font-size:13px;font-weight:900;text-transform:uppercase;letter-spacing:.6px}}.cyo-title{{margin-top:18px;color:#ffc04b}}
table{{width:calc(100% - 32px);margin:0 16px 16px;border-collapse:collapse;font-size:13px;border:1px solid #24465d}}th{{padding:11px 10px;text-align:left;text-transform:uppercase;color:#fff;font-size:12px;letter-spacing:.3px;background:#123c58}}td{{padding:10px;border-top:1px solid #17394d;color:#eef6fb}}tr:nth-child(even) td{{background:#0a2232}}.strong{{font-weight:900;color:#fff}}.num{{text-align:right}}.empty-section{{margin:0 16px 16px;padding:18px;border:1px solid #24465d;color:#91a9b8}}
.novelties{{margin-top:18px;border:1px solid #d49a21;border-radius:14px;overflow:hidden;background:#101e28}}.novelties-head{{display:flex;justify-content:space-between;align-items:center;padding:15px 20px;background:linear-gradient(90deg,#6d4800,#2b2514)}}.novelties-head h2{{margin:0;font-size:23px;color:#ffd56a}}.novelties-head span{{background:rgba(255,213,106,.14);border:1px solid rgba(255,213,106,.35);padding:7px 13px;border-radius:18px;color:#ffe5a3;font-size:13px;font-weight:900}}.novelties th{{background:#8a5b00;color:#fff4d0}}.novelty-badge{{display:inline-block;padding:5px 10px;border-radius:14px;background:#fff1cc;color:#8f5b00;font-weight:900;font-size:12px}}
.foot{{margin-top:16px;padding:15px 18px;border:1px solid #29495e;border-radius:10px;color:#91a9b8;font-size:12px;display:flex;justify-content:space-between}}
</style></head><body><div class="mail">
<div class="hero"><div class="date"><small>Fecha operativa</small><b>{display_date}</b><em>{esc(division_label)}</em></div></div>
<div class="summary">
{kpi("Flota total", str(flota_total), "TW 6 + PM 5", "FT")}
{kpi("Flota utilizada", fmt(flota_usada), f"{max(flota_total-flota_usada,0)} sin asignación", "FU", "green")}
{kpi("Utilización de flota", f"{fmt(util_total,1)}%", "Sobre 11 unidades", "UF", "gold")}
{kpi("Bultos a repartir", fmt(valley_total["bultos"],1), "Sin CYO", "BL")}
{kpi("PDV", fmt(valley_total["pdv"]), "Total del día", "PDV", "cyan")}
{kpi("Drop size", fmt(valley_total["drop"],1), "Bultos / PDV", "DS")}
{base_chart()}
</div>
{sections}
{novelty_html}
<div class="foot"><span>Reporte generado por Planning DDV</span><span>Distribuidora del Valle · Control de Distribución</span></div>
</div></body></html>"""


def planning_ddv_outlook_image_html(planning_date: str, division: str = "TODAS") -> str:
    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background-color:#061522;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;background-color:#061522;">
    <tr>
      <td align="center" style="padding:0;background-color:#061522;">
        <img src="cid:planning_visual" width="1200" alt="Salida diaria Planning DDV {display_date}" style="display:block;width:1200px;max-width:100%;height:auto;border:0;outline:none;text-decoration:none;">
      </td>
    </tr>
  </table>
</body></html>"""


def planning_ddv_premium_top_html(planning_date: str, division: str = "TODAS") -> str:
    routes = route_rows(planning_date, division)
    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")
    division_label = "TW + PM" if not division or division == "TODAS" else display_division(division)
    hero_bytes = (WEB_DIR / "assets" / "hero_banner_v35.png").read_bytes()
    hero_uri = "data:image/png;base64," + base64.b64encode(hero_bytes).decode("ascii")

    def fmt(value: Any, decimals: int = 0) -> str:
        return _fmt_ar(value, decimals)

    def div_rows(div_name: str) -> list[dict[str, Any]]:
        return [r for r in routes if canonical(r.get("division")) == div_name and not is_cyo_route(r)]

    def totals(rows: list[dict[str, Any]]) -> dict[str, float]:
        pdv = sum(safe_number(r.get("pdv")) for r in rows)
        bultos = sum(safe_number(r.get("bultos")) for r in rows)
        return {
            "camiones": float(len(rows)),
            "pdv": pdv,
            "bultos": bultos,
            "drop": bultos / pdv if pdv else 0.0,
        }

    tw_total = totals(div_rows("TRELEW"))
    pm_total = totals(div_rows("PUERTO MADRYN"))
    all_minor = div_rows("TRELEW") + div_rows("PUERTO MADRYN")
    valley = totals(all_minor)
    flota_total = 11
    flota_usada = int(valley["camiones"])
    util_total = flota_usada / flota_total * 100 if flota_total else 0

    def kpi(label: str, value: str, detail: str, icon: str, tone: str = "blue") -> str:
        return f"""
        <div class="kpi {tone}">
          <div class="kpi-icon">{icon}</div>
          <div class="kpi-value">{value}</div>
          <div class="kpi-label">{label}</div>
          <div class="kpi-detail">{detail}</div>
        </div>"""

    tw_b = tw_total["bultos"]
    pm_b = pm_total["bultos"]
    max_b = max(tw_b, pm_b, 1)
    tw_w = min(100, max(2, tw_b / max_b * 100))
    pm_w = min(100, max(2, pm_b / max_b * 100))
    base_summary = f"""
    <div class="kpi base-summary">
      <div class="kpi-icon">TW</div>
      <div class="base-line"><b>TRELEW</b><span>{fmt(tw_b,1)}</span><i><em style="width:{tw_w:.1f}%"></em></i></div>
      <div class="base-line pm"><b>PTO. MADRYN</b><span>{fmt(pm_b,1)}</span><i><em style="width:{pm_w:.1f}%"></em></i></div>
      <div class="kpi-label">Resumen por base</div>
    </div>"""

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}body{{margin:0;background:#061522;font-family:Segoe UI,Arial,sans-serif;color:#fff;padding:0}}
.top{{width:1480px;background:#061522;padding:0 0 16px}}
.hero{{height:292px;border:1px solid #294a60;border-radius:16px;background:url('{hero_uri}') center/cover no-repeat;position:relative;overflow:hidden}}
.date{{position:absolute;right:26px;top:24px;background:rgba(4,18,29,.86);border:1px solid #36596f;border-radius:14px;padding:16px 24px;text-align:right;box-shadow:0 12px 28px rgba(0,0,0,.32)}}
.date small{{display:block;color:#9db4c2;text-transform:uppercase;font-weight:900;font-size:15px;letter-spacing:.5px}}.date b{{font-size:38px;line-height:44px}}.date em{{display:block;font-style:normal;color:#6de65a;font-weight:900;font-size:15px}}
.summary{{display:grid;grid-template-columns:.9fr .9fr 1fr 1fr .75fr .9fr 1.45fr;gap:1px;margin-top:16px;border:1px solid #2b5068;border-radius:15px;overflow:hidden;background:#2b5068}}
.kpi{{min-height:142px;background:linear-gradient(145deg,#112f45,#071b29);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:12px}}
.kpi.green{{background:linear-gradient(145deg,#113f24,#082719)}}.kpi.gold{{background:linear-gradient(145deg,#49370a,#251d08)}}.kpi.cyan{{background:linear-gradient(145deg,#093d49,#061f2a)}}
.kpi-icon{{width:48px;height:48px;border-radius:50%;display:grid;place-items:center;background:rgba(255,255,255,.09);font-size:22px;font-weight:900;color:#9feaff;margin-bottom:8px}}.green .kpi-icon{{color:#6ee35a}}.gold .kpi-icon{{color:#ffc847}}.cyan .kpi-icon{{color:#4bd5ef}}
.kpi-value{{font-size:40px;line-height:44px;font-weight:900;color:#fff}}.kpi-label{{font-size:15px;line-height:18px;text-transform:uppercase;color:#d3e4ef;font-weight:900;letter-spacing:.45px}}.kpi-detail{{font-size:13px;color:#86a4b7;margin-top:6px}}
.base-summary{{align-items:stretch;padding:14px 15px}}.base-summary .kpi-icon{{margin:0 auto 9px}}.base-line{{display:grid;grid-template-columns:86px 82px 1fr;gap:9px;align-items:center;color:#d7e8f1;font-size:13px;margin:5px 0}}.base-line span{{text-align:right;font-weight:900;color:#fff}}.base-line i{{height:11px;background:#16384d;border:1px solid #355b70;border-radius:9px;overflow:hidden}}.base-line em{{display:block;height:100%;background:linear-gradient(90deg,#41c931,#9df174)}}.base-line.pm em{{background:linear-gradient(90deg,#1187d7,#5cc8ff)}}
</style></head><body><div class="top">
<div class="hero"><div class="date"><small>Fecha operativa</small><b>{display_date}</b><em>{html.escape(division_label)}</em></div></div>
<div class="summary">
{kpi("Flota total", str(flota_total), "TW 6 + PM 5", "FT")}
{kpi("Flota utilizada", fmt(flota_usada), f"{max(flota_total-flota_usada,0)} sin asignacion", "FU", "green")}
{kpi("Utilizacion de flota", f"{fmt(util_total,1)}%", "Sobre 11 unidades", "UF", "gold")}
{kpi("Bultos a repartir", fmt(valley["bultos"],1), "Sin CYO", "BL")}
{kpi("PDV", fmt(valley["pdv"]), "Total del dia", "PDV", "cyan")}
{kpi("Drop size", fmt(valley["drop"],1), "Bultos / PDV", "DS")}
{base_summary}
</div></div></body></html>"""


def planning_ddv_outlook_hybrid_html(planning_date: str, division: str = "TODAS") -> str:
    routes = route_rows(planning_date, division)
    novelties = novelty_rows(planning_date)
    if division and division != "TODAS":
        novelties = [n for n in novelties if canonical(n.get("division")) == canonical(division)]
    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")

    def esc(value: Any) -> str:
        return html.escape(str(value if value not in (None, "") else "-"))

    def fmt(value: Any, decimals: int = 0) -> str:
        return _fmt_ar(value, decimals)

    def mail_cell(value: Any, width: int, bold: bool = False, align: str = "left") -> str:
        weight = "font-weight:800;" if bold else "font-weight:500;"
        return (
            f"<td width='{width}' align='{align}' style='width:{width}px;padding:10px 10px;"
            "font-size:15px;line-height:21px;border-top:1px solid #17394d;"
            f"color:#eef6fb;text-align:{align};vertical-align:top;{weight}'>"
            f"{esc(value)}</td>"
        )

    def route_row(r: dict[str, Any]) -> str:
        return (
            "<tr>"
            + mail_cell(r.get("domain"), 78, True)
            + mail_cell(r.get("rendicion"), 76)
            + mail_cell(r.get("driver"), 212, True)
            + mail_cell(r.get("helper1"), 212)
            + mail_cell(r.get("helper2"), 212)
            + mail_cell(fmt(r.get("pdv")), 72, False, "right")
            + mail_cell(fmt(r.get("bultos"), 1), 92, False, "right")
            + mail_cell(r.get("locality"), 178, True)
            + mail_cell(fmt(r.get("kms")), 72, False, "right")
            + mail_cell(r.get("observations"), 244)
            + "</tr>"
        )

    def route_table(rows: list[dict[str, Any]], empty_label: str) -> str:
        if not rows:
            return f"<tr><td style='padding:18px 20px;color:#9bb7c8;font-size:15px;'>{html.escape(empty_label)}</td></tr>"
        headers = [
            ("Dominio", 78), ("Planilla", 76), ("Chofer", 212), ("Ayudante 1", 212),
            ("Ayudante 2", 212), ("PDV", 72), ("Bultos", 92), ("Localidad", 178),
            ("KMS", 72), ("Observaciones", 244),
        ]
        head = "".join(
            f"<th width='{w}' style='width:{w}px;padding:12px 10px;text-align:left;font-size:15px;line-height:19px;text-transform:uppercase;color:#ffffff;background:#123c58;border-bottom:1px solid #25566f;'>{html.escape(label)}</th>"
            for label, w in headers
        )
        return (
            "<tr><td style='padding:0 16px 18px 16px;'>"
            "<table role='presentation' width='1448' cellpadding='0' cellspacing='0' border='0' style='width:1448px;table-layout:fixed;border-collapse:collapse;border:1px solid #24465d;background:#071b29;'>"
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(route_row(r) for r in rows)}</tbody></table></td></tr>"
        )

    def totals(rows: list[dict[str, Any]]) -> dict[str, float]:
        pdv = sum(safe_number(r.get("pdv")) for r in rows)
        bultos = sum(safe_number(r.get("bultos")) for r in rows)
        return {"pdv": pdv, "bultos": bultos, "drop": bultos / pdv if pdv else 0.0}

    def metric(label: str, value: str) -> str:
        return (
            "<td width='20%' align='center' style='width:20%;padding:18px 8px;border-right:1px solid #244b60;'>"
            f"<div style='font-size:34px;line-height:39px;font-weight:900;color:#ffffff;'>{html.escape(value)}</div>"
            f"<div style='font-size:14px;line-height:18px;text-transform:uppercase;color:#c5d7e2;font-weight:900;'>{html.escape(label)}</div>"
            "</td>"
        )

    def base_block(title: str, rows: list[dict[str, Any]], kind: str, cap: int) -> str:
        minor = [r for r in rows if not is_cyo_route(r)]
        cyo = [r for r in rows if is_cyo_route(r)]
        total = totals(minor)
        util = len(minor) / cap * 100 if cap else 0
        color = "#17631b" if kind == "tw" else "#075fa6"
        color2 = "#0c3517" if kind == "tw" else "#073458"
        border = "#35962f" if kind == "tw" else "#1681d1"
        cyo_html = ""
        if cyo:
            cyo_html = (
                "<tr><td style='padding:2px 16px 10px 16px;color:#ffc04b;font-size:15px;line-height:20px;font-weight:900;text-transform:uppercase;'>"
                f"CYO · {esc(title)}</td></tr>"
                + route_table(cyo, "Sin unidades CYO.")
            )
        return f"""
        <tr><td style="padding:18px 0 0 0;">
          <table role="presentation" width="1480" cellpadding="0" cellspacing="0" border="0" style="width:1480px;border-collapse:collapse;border:1px solid {border};background:#071b29;">
            <tr>
              <td style="padding:18px 24px;background:{color};background:linear-gradient(90deg,{color},{color2});font-size:28px;line-height:34px;font-weight:900;color:#ffffff;">{esc(title)}</td>
              <td align="right" style="padding:18px 24px;background:{color2};font-size:15px;line-height:19px;font-weight:900;color:#d8f4ff;">{len(rows)} unidades totales</td>
            </tr>
            <tr><td colspan="2" style="padding:16px 16px 8px 16px;">
              <table role="presentation" width="1448" cellpadding="0" cellspacing="0" border="0" style="width:1448px;border-collapse:collapse;background:#0a2434;border:1px solid #244b60;">
                <tr>{metric("Camiones sin CYO", fmt(len(minor)))}{metric("PDV", fmt(total["pdv"]))}{metric("Bultos", fmt(total["bultos"],1))}{metric("Drop size", fmt(total["drop"],1))}{metric("Utilizacion flota", f"{fmt(util,1)}%")}</tr>
              </table>
            </td></tr>
            <tr><td colspan="2" style="padding:6px 16px 10px 16px;color:#d7e9f4;font-size:15px;line-height:20px;font-weight:900;text-transform:uppercase;">Distribucion minorista</td></tr>
            {route_table(minor, "Sin unidades de distribucion minorista.")}
            {cyo_html}
          </table>
        </td></tr>"""

    sections = "".join(
        block for block in (
            base_block("TRELEW", [r for r in routes if canonical(r.get("division")) == "TRELEW"], "tw", 6)
            if any(canonical(r.get("division")) == "TRELEW" for r in routes) else "",
            base_block("PUERTO MADRYN", [r for r in routes if canonical(r.get("division")) == "PUERTO MADRYN"], "pm", 5)
            if any(canonical(r.get("division")) == "PUERTO MADRYN" for r in routes) else "",
        ) if block
    )

    novelty_html = ""
    if novelties:
        novelty_rows_html = "".join(
            "<tr>"
            f"<td width='280' style='width:280px;font-weight:800;color:#ffffff;'>{esc(n.get('employee_name'))}</td>"
            f"<td width='180' style='width:180px;'>{esc(n.get('division'))}</td>"
            f"<td width='200' style='width:200px;'>{esc(n.get('role'))}</td>"
            f"<td width='210' style='width:210px;font-weight:800;color:#ffd56a;'>{esc(n.get('reason'))}</td>"
            f"<td width='578' style='width:578px;'>{esc(n.get('notes'))}</td>"
            "</tr>"
            for n in novelties
        )
        novelty_html = f"""
        <tr><td style="padding:18px 0 0 0;">
          <table role="presentation" width="1480" cellpadding="0" cellspacing="0" border="0" style="width:1480px;border-collapse:collapse;border:1px solid #d49a21;background:#101e28;">
            <tr><td style="padding:17px 22px;background:#6d4800;font-size:25px;line-height:31px;font-weight:900;color:#ffd56a;">NOVEDADES DEL DIA</td><td align="right" style="padding:17px 22px;background:#2b2514;font-size:15px;color:#ffe5a3;font-weight:900;">{len(novelties)} registros</td></tr>
            <tr><td colspan="2" style="padding:0 16px 18px 16px;">
              <table role="presentation" width="1448" cellpadding="0" cellspacing="0" border="0" style="width:1448px;table-layout:fixed;border-collapse:collapse;border:1px solid #725821;background:#122633;">
                <thead><tr><th width="280" style="width:280px;padding:12px 10px;text-align:left;font-size:15px;line-height:19px;text-transform:uppercase;color:#fff4d0;background:#8a5b00;">Empleado</th><th width="180" style="width:180px;padding:12px 10px;text-align:left;font-size:15px;line-height:19px;text-transform:uppercase;color:#fff4d0;background:#8a5b00;">Base</th><th width="200" style="width:200px;padding:12px 10px;text-align:left;font-size:15px;line-height:19px;text-transform:uppercase;color:#fff4d0;background:#8a5b00;">Rol habilitado</th><th width="210" style="width:210px;padding:12px 10px;text-align:left;font-size:15px;line-height:19px;text-transform:uppercase;color:#fff4d0;background:#8a5b00;">Novedad</th><th width="578" style="width:578px;padding:12px 10px;text-align:left;font-size:15px;line-height:19px;text-transform:uppercase;color:#fff4d0;background:#8a5b00;">Detalle</th></tr></thead>
                <tbody>{novelty_rows_html}</tbody>
              </table>
            </td></tr>
          </table>
        </td></tr>"""

    return f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background-color:#061522;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;background-color:#061522;">
  <tr><td align="center" style="padding:0;background-color:#061522;">
    <table role="presentation" width="1480" cellpadding="0" cellspacing="0" border="0" style="width:1480px;border-collapse:collapse;background-color:#061522;font-family:Segoe UI,Arial,sans-serif;color:#eef6fb;">
      <tr><td style="padding:0 0 4px 0;">
        <img src="cid:planning_top" width="1480" alt="Planning DDV {display_date}" style="display:block;width:1480px;height:auto;border:0;outline:none;text-decoration:none;">
      </td></tr>
      {sections}
      {novelty_html}
      <tr><td style="padding:18px 4px 4px 4px;color:#91a9b8;font-size:14px;line-height:20px;">Reporte generado por Planning DDV · Distribuidora del Valle · Control de Distribucion</td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def planning_ddv_outlook_mail_html(planning_date: str, division: str = "TODAS", preview: bool = False) -> str:
    routes = route_rows(planning_date, division)
    novelties = novelty_rows(planning_date)
    if division and division != "TODAS":
        novelties = [n for n in novelties if canonical(n.get("division")) == canonical(division)]

    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")
    division_label = "TW + PM" if not division or division == "TODAS" else display_division(division)
    logo_src = mail_logo_data_uri() if preview else ("cid:ddv_logo" if mail_logo_asset() else "")

    def esc(value: Any) -> str:
        return html.escape(str(value if value not in (None, "") else "-"))

    def fmt(value: Any, decimals: int = 0) -> str:
        return _fmt_ar(value, decimals)

    def div_rows(div_name: str) -> list[dict[str, Any]]:
        return [r for r in routes if canonical(r.get("division")) == div_name]

    def minor_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if not is_cyo_route(r)]

    def cyo_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if is_cyo_route(r)]

    def metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
        return {
            "camiones": float(len(rows)),
            "pdv": sum(safe_number(r.get("pdv")) for r in rows),
            "bultos": sum(safe_number(r.get("bultos")) for r in rows),
        }

    tw_all = div_rows("TRELEW")
    pm_all = div_rows("PUERTO MADRYN")
    tw_minor = minor_rows(tw_all)
    pm_minor = minor_rows(pm_all)
    valley_minor = tw_minor + pm_minor
    valley = metrics(valley_minor)
    tw_m = metrics(tw_minor)
    pm_m = metrics(pm_minor)
    cyo_count = len(cyo_rows(tw_all) + cyo_rows(pm_all))

    def drop(m: dict[str, float]) -> str:
        return fmt(m["bultos"] / m["pdv"], 1) if m["pdv"] else "0,0"

    def card(title: str, m: dict[str, float], color: str, subtitle: str) -> str:
        return f"""
        <td width="33.33%" valign="top" style="padding:0 6px 12px 6px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;border:1px solid #D9E2EC;background-color:#FFFFFF;">
            <tr><td style="padding:11px 14px;background-color:{color};font-family:Arial,'Segoe UI',sans-serif;font-size:15px;line-height:19px;color:#FFFFFF;font-weight:700;">{esc(title)}</td></tr>
            <tr><td style="padding:15px 14px 13px 14px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
                <tr>
                  <td width="33%" align="center" style="font-family:Arial,'Segoe UI',sans-serif;color:#1F2937;border-right:1px solid #E5EDF3;"><div style="font-size:24px;line-height:28px;font-weight:700;">{fmt(m["camiones"])}</div><div style="font-size:10px;line-height:13px;color:#60778A;font-weight:700;">CAMIONES</div></td>
                  <td width="33%" align="center" style="font-family:Arial,'Segoe UI',sans-serif;color:#1F2937;border-right:1px solid #E5EDF3;"><div style="font-size:24px;line-height:28px;font-weight:700;">{fmt(m["pdv"])}</div><div style="font-size:10px;line-height:13px;color:#60778A;font-weight:700;">PDV</div></td>
                  <td width="34%" align="center" style="font-family:Arial,'Segoe UI',sans-serif;color:#1F2937;"><div style="font-size:24px;line-height:28px;font-weight:700;">{fmt(m["bultos"],1)}</div><div style="font-size:10px;line-height:13px;color:#60778A;font-weight:700;">BULTOS</div></td>
                </tr>
              </table>
              <div style="padding-top:10px;font-family:Arial,'Segoe UI',sans-serif;font-size:11px;line-height:15px;color:#60778A;">Drop size: <b style="color:#1F2937;">{drop(m)}</b> &nbsp; {esc(subtitle)}</div>
            </td></tr>
          </table>
        </td>"""

    def table_rows(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "<tr><td colspan='9' style='padding:12px;font-family:Arial,sans-serif;font-size:12px;color:#60778A;border-top:1px solid #D9E2EC;'>Sin unidades para esta sección.</td></tr>"
        out: list[str] = []
        for idx, r in enumerate(rows):
            bg = "#FFFFFF" if idx % 2 == 0 else "#F5F7FA"
            helpers = " / ".join([str(v) for v in (r.get("helper1"), r.get("helper2")) if v not in (None, "", "-")]) or "-"
            out.append(
                f"<tr style='background-color:{bg};'>"
                f"<td style='padding:9px 8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#0F2F57;font-weight:700;'>{esc(r.get('domain'))}</td>"
                f"<td style='padding:9px 8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{esc(r.get('rendicion'))}</td>"
                f"<td style='padding:9px 8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{esc(r.get('driver'))}</td>"
                f"<td style='padding:9px 8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{esc(helpers)}</td>"
                f"<td align='right' style='padding:9px 8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{fmt(r.get('pdv'))}</td>"
                f"<td align='right' style='padding:9px 8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{fmt(r.get('bultos'),1)}</td>"
                f"<td style='padding:9px 8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{esc(r.get('locality'))}</td>"
                f"<td align='right' style='padding:9px 8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{fmt(r.get('kms'))}</td>"
                f"<td style='padding:9px 8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{esc(r.get('observations'))}</td>"
                "</tr>"
            )
        return "".join(out)

    def route_table(rows: list[dict[str, Any]]) -> str:
        headers = (
            ("DOMINIO", "left"), ("PLANILLA", "left"), ("CHOFER", "left"),
            ("AYUDANTES", "left"), ("PDV", "right"), ("BULTOS", "right"),
            ("LOCALIDAD", "left"), ("KMS", "right"), ("OBSERVACIONES", "left"),
        )
        head = "".join(
            f"<th align='{align}' style='padding:9px 8px;background-color:#0F2F57;color:#FFFFFF;font-family:Arial,sans-serif;font-size:11px;line-height:14px;'>{label}</th>"
            for label, align in headers
        )
        return (
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' style='width:100%;border-collapse:collapse;border:1px solid #D9E2EC;background-color:#FFFFFF;'>"
            f"<tr>{head}</tr>{table_rows(rows)}</table>"
        )

    def block(title: str, rows: list[dict[str, Any]], color: str) -> str:
        return f"""
        <tr><td style="padding:0 22px 16px 22px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
            <tr><td style="padding:11px 14px;background-color:{color};font-family:Arial,'Segoe UI',sans-serif;font-size:16px;line-height:20px;color:#FFFFFF;font-weight:700;">{esc(title)}</td></tr>
            <tr><td>{route_table(rows)}</td></tr>
          </table>
        </td></tr>"""

    section_html: list[str] = []
    for div_name, rows, color in (("TRELEW", tw_all, "#0D5EA8"), ("PUERTO MADRYN", pm_all, "#07898F")):
        if not rows:
            continue
        section_html.append(
            f"<tr><td style='padding:8px 22px 8px 22px;font-family:Arial,sans-serif;font-size:22px;line-height:28px;color:#0F2F57;font-weight:700;'>{esc(div_name)}</td></tr>"
        )
        section_html.append(block("Distribución minorista", minor_rows(rows), color))
        cyo = cyo_rows(rows)
        if cyo:
            section_html.append(block("CYO", cyo, "#A56E00"))

    novelty_html = ""
    if novelties:
        novelty_rows_html = "".join(
            f"<tr><td style='padding:8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;font-weight:700;'>{esc(n.get('employee_name'))}</td>"
            f"<td style='padding:8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{esc(n.get('division'))}</td>"
            f"<td style='padding:8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{esc(n.get('reason'))}</td>"
            f"<td style='padding:8px;border-top:1px solid #D9E2EC;font-family:Arial,sans-serif;font-size:12px;color:#1F2937;'>{esc(n.get('notes'))}</td></tr>"
            for n in novelties
        )
        novelty_html = (
            "<tr><td style='padding:0 22px 22px 22px;'>"
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' style='width:100%;border-collapse:collapse;border:1px solid #D9E2EC;background-color:#FFFFFF;'>"
            "<tr><td colspan='4' style='padding:11px 14px;background-color:#F1AE17;font-family:Arial,sans-serif;font-size:16px;line-height:20px;color:#0F2F57;font-weight:700;'>Novedades del día</td></tr>"
            "<tr><th align='left' style='padding:8px;background-color:#FFF7E2;font-family:Arial,sans-serif;font-size:11px;color:#0F2F57;'>EMPLEADO</th><th align='left' style='padding:8px;background-color:#FFF7E2;font-family:Arial,sans-serif;font-size:11px;color:#0F2F57;'>BASE</th><th align='left' style='padding:8px;background-color:#FFF7E2;font-family:Arial,sans-serif;font-size:11px;color:#0F2F57;'>NOVEDAD</th><th align='left' style='padding:8px;background-color:#FFF7E2;font-family:Arial,sans-serif;font-size:11px;color:#0F2F57;'>DETALLE</th></tr>"
            f"{novelty_rows_html}</table></td></tr>"
        )

    logo_html = (
        f"<img src='{logo_src}' width='210' alt='Distribuidora del Valle' style='display:block;width:210px;height:auto;border:0;outline:none;text-decoration:none;'>"
        if logo_src else
        "<div style='font-family:Arial,sans-serif;font-size:28px;line-height:34px;color:#FFFFFF;font-weight:700;text-align:right;'>DDV</div>"
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background-color:#EAF0F5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;background-color:#EAF0F5;">
<tr><td align="center" style="padding:18px 10px;">
<table role="presentation" width="1000" cellpadding="0" cellspacing="0" border="0" style="width:1000px;border-collapse:collapse;background-color:#FFFFFF;border:1px solid #D9E2EC;">
  <tr><td style="padding:0;background-color:#0F2F57;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
      <tr>
        <td width="690" valign="middle" style="padding:26px 28px;font-family:Arial,'Segoe UI',sans-serif;color:#FFFFFF;">
          <div style="font-size:13px;line-height:18px;color:#90D15E;font-weight:700;text-transform:uppercase;">Informe operativo diario</div>
          <div style="font-size:40px;line-height:46px;font-weight:700;letter-spacing:.2px;">SALIDA DIARIA DDV</div>
          <div style="font-size:16px;line-height:22px;color:#DCEAF3;">Fecha operativa: <b>{display_date}</b> &nbsp; | &nbsp; División: <b>{esc(division_label)}</b></div>
        </td>
        <td width="310" valign="middle" align="right" style="padding:26px 28px;">{logo_html}</td>
      </tr>
    </table>
  </td></tr>
  <tr><td style="height:5px;line-height:5px;background-color:#4EBC35;font-size:1px;">&nbsp;</td></tr>
  <tr><td style="padding:20px 16px 8px 16px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
      <tr>
        {card("VALLE", valley, "#0F2F57", f"CYO: {cyo_count} unidades")}
        {card("TRELEW", tw_m, "#0D5EA8", "Base TW")}
        {card("PUERTO MADRYN", pm_m, "#07898F", "Base PM")}
      </tr>
    </table>
  </td></tr>
  {''.join(section_html)}
  {novelty_html}
  <tr><td style="padding:13px 22px;background-color:#0F2F57;font-family:Arial,'Segoe UI',sans-serif;font-size:12px;line-height:17px;color:#DCEAF3;">
    Reporte generado por Planning DDV. Datos operativos de la fecha seleccionada, sin envío automático.
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def _pdf_escape(text: Any) -> bytes:
    raw = str(text or "").encode("cp1252", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _pdf_truncate(text: Any, width: float, size: float) -> str:
    value = str(text or "-")
    max_chars = max(3, int(width / max(size * 0.52, 1)))
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def build_pdf_bytes(planning_date: str, division: str = "TODAS") -> bytes:
    routes = route_rows(planning_date, division)
    display_date = datetime.fromisoformat(planning_date).strftime("%d/%m/%Y")
    logo_bytes = asset_path("ddv_logo.jpg").read_bytes()

    PAGE_W, PAGE_H = 842.0, 595.0  # A4 landscape points
    MARGIN = 28.0
    BOTTOM = 30.0
    NAVY = (0.035, 0.22, 0.36)
    NAVY2 = (0.02, 0.30, 0.45)
    TEAL = (0.02, 0.52, 0.55)
    BLUE = (0.10, 0.40, 0.68)
    ORANGE = (0.93, 0.47, 0.06)
    TEXT = (0.04, 0.16, 0.27)
    MUTED = (0.34, 0.45, 0.53)
    LINE = (0.80, 0.86, 0.89)
    LIGHT = (0.94, 0.97, 0.98)

    pages: list[bytes] = []
    commands: list[bytes] = []
    y = PAGE_H - MARGIN

    def cfill(color: tuple[float, float, float]) -> None:
        commands.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg\n".encode())

    def cstroke(color: tuple[float, float, float]) -> None:
        commands.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG\n".encode())

    def rect(x: float, yy: float, w: float, h: float, fill: tuple[float, float, float] | None = None,
             stroke: tuple[float, float, float] | None = None, line_width: float = 0.7) -> None:
        if fill:
            cfill(fill)
        if stroke:
            cstroke(stroke)
        commands.append(f"{line_width:.2f} w {x:.2f} {yy:.2f} {w:.2f} {h:.2f} re ".encode())
        commands.append(b"B\n" if fill and stroke else b"f\n" if fill else b"S\n")

    def line(x1: float, y1: float, x2: float, y2: float, color: tuple[float, float, float] = LINE,
             line_width: float = 0.6) -> None:
        cstroke(color)
        commands.append(f"{line_width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S\n".encode())

    def text(x: float, yy: float, size: float, value: Any, bold: bool = False,
             color: tuple[float, float, float] = TEXT) -> None:
        cfill(color)
        font = "F2" if bold else "F1"
        commands.append(f"BT /{font} {size:.2f} Tf 1 0 0 1 {x:.2f} {yy:.2f} Tm ".encode())
        commands.append(b"(" + _pdf_escape(value) + b") Tj ET\n")

    def image(x: float, yy: float, w: float, h: float) -> None:
        commands.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {yy:.2f} cm /X1 Do Q\n".encode())

    def finish_page() -> None:
        nonlocal commands
        pages.append(b"".join(commands))
        commands = []

    def draw_page_header(first: bool = False) -> float:
        # White heading; logo remains at upper-right, outside the blue KPI band.
        text(MARGIN, PAGE_H - 48, 23 if first else 17, "SALIDA DIARIA", True, NAVY)
        text(MARGIN, PAGE_H - 65, 9.5, "Detalle consolidado de camiones, dotación y localidades.", False, MUTED)
        image(PAGE_W - MARGIN - 205, PAGE_H - 70, 190, 47.5)
        text(PAGE_W - MARGIN - 120, PAGE_H - 84, 8, f"Fecha: {display_date}", True, MUTED)
        if first:
            total_units = len(routes)
            total_pdv = sum(safe_number(r.get("pdv")) for r in routes)
            total_bultos = sum(safe_number(r.get("bultos")) for r in routes)
            bar_y, bar_h = PAGE_H - 145, 48
            rect(MARGIN, bar_y, PAGE_W - 2 * MARGIN, bar_h, NAVY, NAVY)
            widths = [0.0, 1 / 3, 2 / 3, 1.0]
            metrics = [("CAMIONES", _fmt_ar(total_units)), ("PDV", _fmt_ar(total_pdv)), ("BULTOS", _fmt_ar(total_bultos, 1))]
            total_w = PAGE_W - 2 * MARGIN
            for i, (label, value) in enumerate(metrics):
                x0 = MARGIN + total_w * widths[i]
                x1 = MARGIN + total_w * widths[i + 1]
                if i:
                    line(x0, bar_y + 8, x0, bar_y + bar_h - 8, (0.38, 0.58, 0.70), 0.7)
                text(x0 + (x1 - x0) * 0.38, bar_y + 27, 18, value, True, (1, 1, 1))
                text(x0 + (x1 - x0) * 0.38, bar_y + 12, 8, label, True, (0.84, 0.93, 0.97))
            return bar_y - 18
        line(MARGIN, PAGE_H - 96, PAGE_W - MARGIN, PAGE_H - 96, LINE, 0.8)
        return PAGE_H - 112

    y = draw_page_header(first=True)
    # Gráfico simple de bultos por base, conservando fondo blanco.
    tw_b = sum(safe_number(r.get("bultos")) for r in routes if canonical(r.get("division")) == "TRELEW")
    pm_b = sum(safe_number(r.get("bultos")) for r in routes if canonical(r.get("division")) == "PUERTO MADRYN")
    max_b = max(tw_b, pm_b, 1)
    chart_h = 44
    rect(MARGIN, y-chart_h, PAGE_W-2*MARGIN, chart_h, (1,1,1), LINE)
    text(MARGIN+9, y-12, 8.5, "BULTOS POR BASE", True, NAVY)
    bar_x=MARGIN+110; bar_w=PAGE_W-2*MARGIN-190
    for idx,(label,val,color) in enumerate((("TRELEW",tw_b,(0.12,0.55,0.18)),("PUERTO MADRYN",pm_b,BLUE))):
        yy=y-20-idx*15
        text(MARGIN+9,yy,7.5,label,True,color)
        rect(bar_x,yy-2,bar_w,7,(0.91,0.94,0.95),None)
        rect(bar_x,yy-2,bar_w*(val/max_b),7,color,None)
        text(bar_x+bar_w+8,yy,7.5,_fmt_ar(val,1),True,color)
    y -= chart_h + 12

    def new_page() -> None:
        nonlocal y
        finish_page()
        y = draw_page_header(first=False)

    def ensure(required: float) -> None:
        nonlocal y
        if y - required < BOTTOM:
            new_page()

    def draw_division_header(div: str, subset: list[dict[str, Any]], minor: list[dict[str, Any]]) -> None:
        nonlocal y
        ensure(68)
        accent = TEAL if div == "PUERTO MADRYN" else BLUE
        rect(MARGIN, y - 28, PAGE_W - 2 * MARGIN, 28, (0.93, 0.98, 0.98) if div == "PUERTO MADRYN" else (0.93, 0.97, 1.0), accent, 0.8)
        rect(MARGIN, y - 28, 34, 28, accent, accent)
        text(MARGIN + 11, y - 18, 11, "DDV", True, (1, 1, 1))
        text(MARGIN + 44, y - 19, 15, div, True, NAVY)
        text(PAGE_W - MARGIN - 83, y - 18, 9, f"{len(subset)} unidades", True, accent)
        y -= 34
        m_pdv = sum(safe_number(r.get("pdv")) for r in minor)
        m_bultos = sum(safe_number(r.get("bultos")) for r in minor)
        metric_h = 34
        rect(MARGIN, y - metric_h, PAGE_W - 2 * MARGIN, metric_h, (0.985, 0.995, 0.995), LINE)
        cyo_count = len(subset) - len(minor)
        metrics = [("CAMIONES", f"{len(minor)} Distribución + {cyo_count} CYO"), ("PDV", m_pdv), ("BULTOS", m_bultos)]
        cell_w = (PAGE_W - 2 * MARGIN) / 3
        for i, (label, value) in enumerate(metrics):
            x0 = MARGIN + i * cell_w
            if i:
                line(x0, y - metric_h + 5, x0, y - 5, LINE, 0.6)
            formatted = str(value) if label == "CAMIONES" else _fmt_ar(value, 1 if label == "BULTOS" else 0)
            text(x0 + 20, y - 16, 10.5 if label == "CAMIONES" else 15, formatted, True, accent)
            text(x0 + 20, y - 28, 7.5, label, True, MUTED)
        y -= metric_h + 10

    col_widths = [69, 45, 103, 99, 99, 36, 50, 91, 194]
    headers = ["DOMINIO", "PLANILLA", "CHOFER", "AYUDANTE 1", "AYUDANTE 2", "PDV", "BULTOS", "LOCALIDAD", "OBSERVACIONES"]

    def draw_table_header(accent: tuple[float, float, float]) -> None:
        nonlocal y
        h = 18
        rect(MARGIN, y - h, PAGE_W - 2 * MARGIN, h, LIGHT, LINE)
        x = MARGIN
        for label, w in zip(headers, col_widths):
            text(x + 4, y - 12, 6.5, label, True, NAVY)
            x += w
        y -= h

    def draw_table_rows(rows: list[dict[str, Any]], accent: tuple[float, float, float], block_label: str) -> None:
        nonlocal y
        row_h = 17
        for idx, r in enumerate(rows):
            if y - row_h < BOTTOM:
                new_page()
                text(MARGIN, y - 13, 10, block_label + " · continuación", True, accent)
                y -= 20
                draw_table_header(accent)
            if idx % 2:
                rect(MARGIN, y - row_h, PAGE_W - 2 * MARGIN, row_h, (0.985, 0.99, 0.993), None)
            line(MARGIN, y - row_h, PAGE_W - MARGIN, y - row_h, LINE, 0.4)
            values = [
                r.get("domain") or "-", r.get("rendicion") or "-", r.get("driver") or "-",
                r.get("helper1") or "-", r.get("helper2") or "-", _fmt_ar(r.get("pdv")),
                _fmt_ar(r.get("bultos"), 1), r.get("locality") or "-", r.get("observations") or "-",
            ]
            x = MARGIN
            for i, (value, w) in enumerate(zip(values, col_widths)):
                size = 6.8 if i not in (8,) else 6.2
                value = _pdf_truncate(value, w - 7, size)
                text(x + 4, y - 11.5, size, value, bold=(i == 0), color=TEXT)
                x += w
            y -= row_h
        y -= 8

    def draw_block(label: str, rows: list[dict[str, Any]], cyo: bool = False) -> None:
        nonlocal y
        if not rows:
            return
        accent = ORANGE if cyo else TEAL
        ensure(48)
        rect(MARGIN, y - 22, PAGE_W - 2 * MARGIN, 22, (1.0, 0.96, 0.90) if cyo else (0.92, 0.98, 0.98), accent, 0.6)
        text(MARGIN + 9, y - 15, 9.5, label, True, accent)
        text(PAGE_W - MARGIN - 72, y - 15, 7.5, f"{len(rows)} unidades", True, accent)
        y -= 22
        draw_table_header(accent)
        draw_table_rows(rows, accent, label)

    if routes:
        for div in ("PUERTO MADRYN", "TRELEW"):
            subset = [r for r in routes if canonical(r.get("division")) == div]
            if not subset:
                continue
            minor = [r for r in subset if not is_cyo_route(r)]
            cyo = [r for r in subset if is_cyo_route(r)]
            draw_division_header(div, subset, minor)
            draw_block("DISTRIBUCIÓN MINORISTA", minor, False)
            draw_block(f"CYO · {div}", cyo, True)
            y -= 6
    else:
        text(MARGIN, y - 30, 13, "No hay salidas registradas para la selección.", True, MUTED)

    finish_page()

    # Assemble a compact PDF using built-in Type1 fonts and one JPEG image.
    objects: list[bytes] = [b"", b""]
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    image_obj = (
        f"<< /Type /XObject /Subtype /Image /Width 440 /Height 110 /ColorSpace /DeviceRGB "
        f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(logo_bytes)} >>\nstream\n".encode()
        + logo_bytes + b"\nendstream"
    )
    objects.append(image_obj)
    page_ids: list[int] = []
    for content in pages:
        compressed = zlib.compress(content, 9)
        content_id = len(objects) + 1
        objects.append(f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode() + compressed + b"\nendstream")
        page_id = len(objects) + 1
        page_ids.append(page_id)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> /XObject << /X1 5 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode()
        )
    objects[1] = f"<< /Type /Pages /Count {len(page_ids)} /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] >>".encode()
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode())
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(out)


def generate_pdf(planning_date: str, division: str = "TODAS") -> Path:
    EXPORTS_DIR.mkdir(exist_ok=True)
    safe_div = re.sub(r"[^A-Z0-9]+", "_", canonical(division)).strip("_") or "TODAS"
    pdf_path = EXPORTS_DIR / f"salida_diaria_{planning_date}_{safe_div}.pdf"
    pdf_path.write_bytes(build_pdf_bytes(planning_date, division))
    return pdf_path


def _attachment_from_bytes(
    name: str,
    content_type: str,
    data: bytes,
    cid: str = "",
    inline: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "content_type": content_type,
        "content_base64": base64.b64encode(data).decode("ascii"),
        "cid": cid,
        "inline": inline,
    }


def _cleanup_outlook_packages() -> None:
    now_ts = time.time()
    with OUTLOOK_PACKAGES_LOCK:
        expired = [token for token, package in OUTLOOK_PACKAGES.items() if package.get("expires_at", 0) <= now_ts]
        for token in expired:
            OUTLOOK_PACKAGES.pop(token, None)


def _base_url_from_request(handler: BaseHTTPRequestHandler) -> str:
    forwarded_proto = handler.headers.get("X-Forwarded-Proto", "")
    proto = forwarded_proto.split(",")[0].strip() or ("https" if handler.headers.get("X-Forwarded-Host") else "http")
    host = handler.headers.get("X-Forwarded-Host") or handler.headers.get("Host") or f"127.0.0.1:{PORT}"
    return f"{proto}://{host}".rstrip("/")


def create_outlook_connector_package(
    payload: dict[str, Any],
    user: dict[str, Any],
    handler: BaseHTTPRequestHandler,
) -> dict[str, str]:
    planning_date = str(payload.get("date") or "").strip()
    if not planning_date:
        raise ValueError("Debe seleccionar una fecha operativa.")
    division = str(payload.get("division") or "TODAS").strip() or "TODAS"
    require_base_access(user, division)
    subject = str(payload.get("subject") or "").strip() or f"Salida diaria DDV - {datetime.fromisoformat(planning_date).strftime('%d/%m/%Y')}"
    to = str(payload.get("to") or "Planning").strip() or "Planning"
    cc = str(payload.get("cc") or "").strip()
    routes = route_rows(planning_date, division)
    if not routes:
        raise ValueError("No hay salidas cargadas para generar el mail operativo.")
    visual_path = render_mail_top_image(planning_date, division)
    if not visual_path.exists() or visual_path.stat().st_size <= 0:
        raise ValueError("No se pudo generar la lámina visual del mail operativo.")
    html_body = mail_html(planning_date, division, preview=False)
    if not html_body.strip():
        raise ValueError("No se pudo generar el cuerpo real del mail operativo.")
    attachments: list[dict[str, Any]] = []

    attachments.append(_attachment_from_bytes(visual_path.name, "image/png", visual_path.read_bytes(), cid="planning_top", inline=True))

    safe_div = re.sub(r"[^A-Z0-9]+", "_", canonical(division)).strip("_") or "TODAS"
    pdf_path = generate_pdf(planning_date, division)
    if not pdf_path.exists() or pdf_path.stat().st_size <= 0:
        raise ValueError("No se pudo generar el PDF real de la salida seleccionada.")
    pdf_name = pdf_path.name or f"salida_diaria_{planning_date}_{safe_div}.pdf"
    attachments.append(_attachment_from_bytes(pdf_name, "application/pdf", pdf_path.read_bytes()))

    token = secrets.token_urlsafe(32)
    now_ts = time.time()
    _cleanup_outlook_packages()
    with OUTLOOK_PACKAGES_LOCK:
        OUTLOOK_PACKAGES[token] = {
            "token": token,
            "created_at": now_iso(),
            "expires_at": now_ts + OUTLOOK_PACKAGE_TTL_SECONDS,
            "created_by": user.get("username", ""),
            "planning_date": planning_date,
            "division": division,
            "to": to,
            "cc": cc,
            "subject": subject,
            "html_body": html_body,
            "attachments": attachments,
            "visual_path": str(visual_path),
            "pdf_path": str(pdf_path),
        }
    package_url = f"{_base_url_from_request(handler)}/api/mail/package/{token}"
    protocol_url = f"planningddv://mail?token={quote(token, safe='')}&package_url={quote(package_url, safe='')}"
    return {
        "token": token,
        "expires_at": datetime.fromtimestamp(now_ts + OUTLOOK_PACKAGE_TTL_SECONDS).isoformat(timespec="seconds"),
        "protocol_url": protocol_url,
        "package_url": package_url,
    }


def consume_outlook_connector_package(token: str) -> dict[str, Any] | None:
    _cleanup_outlook_packages()
    with OUTLOOK_PACKAGES_LOCK:
        package = OUTLOOK_PACKAGES.pop(token, None)
    if not package or package.get("expires_at", 0) <= time.time():
        return None
    package = dict(package)
    package.pop("expires_at", None)
    return package


def connector_zip_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if CONNECTOR_DIR.exists():
            for path in CONNECTOR_DIR.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(CONNECTOR_DIR).as_posix())
    return output.getvalue()



def _edge_executable() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def render_mail_image(planning_date: str, division: str = "TODAS") -> Path:
    """Renderiza el reporte como una única lámina PNG para Outlook."""
    EXPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = EXPORTS_DIR / f"lamina_{planning_date}_{stamp}.html"
    png_path = EXPORTS_DIR / f"lamina_{planning_date}_{stamp}.png"
    visual_html = planning_ddv_premium_visual_html(planning_date, division)
    # El mail es una lámina: eliminamos sombras/márgenes externos para capturarla limpia.
    html_path.write_text(visual_html, encoding="utf-8")

    edge = _edge_executable()
    if edge is None:
        raise RuntimeError("No se encontró Microsoft Edge para renderizar el mail visual.")
    uri = html_path.resolve().as_uri()
    routes_count = len(route_rows(planning_date, division))
    novelties_count = len(novelty_rows(planning_date))
    if division and division != "TODAS":
        novelties_count = len([n for n in novelty_rows(planning_date) if canonical(n.get("division")) == canonical(division)])
    viewport_height = max(1800, min(5600, 960 + routes_count * 85 + novelties_count * 75))
    command = [
        str(edge), "--headless", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size=1480,{viewport_height}",
        f"--screenshot={png_path}",
        uri,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not png_path.exists():
        raise RuntimeError(result.stderr.strip() or "No se pudo renderizar la lámina del mail.")
    return png_path


def render_mail_top_image(planning_date: str, division: str = "TODAS") -> Path:
    EXPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = EXPORTS_DIR / f"mail_top_{planning_date}_{stamp}.html"
    png_path = EXPORTS_DIR / f"mail_top_{planning_date}_{stamp}.png"
    html_path.write_text(planning_ddv_premium_top_html(planning_date, division), encoding="utf-8")

    edge = _edge_executable()
    if edge is None:
        raise RuntimeError("No se encontrÃ³ Microsoft Edge para renderizar el encabezado del mail.")
    command = [
        str(edge),
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=2",
        "--window-size=1480,455",
        f"--screenshot={png_path}",
        html_path.resolve().as_uri(),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not png_path.exists():
        raise RuntimeError(result.stderr.strip() or "No se pudo renderizar el encabezado del mail.")
    return png_path


def open_outlook_visual_draft(
    to: str,
    cc: str,
    subject: str,
    planning_date: str,
    division: str,
    send_now: bool = False,
) -> None:
    if platform.system() != "Windows":
        raise RuntimeError("La apertura directa en Outlook solo está disponible en Windows.")
    image_path = render_mail_image(planning_date, division)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ps_path = Path(tempfile.gettempdir()) / f"planning_ddv_visual_mail_{stamp}.ps1"

    def ps_escape(text: str) -> str:
        return text.replace("'", "''")

    action = "$mail.Send()" if send_now else "$mail.Display()"
    body = """
    <html><body style="margin:0;padding:0;background:#eef3f7;">
      <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:#eef3f7;">
        <tr><td align="center" style="padding:0;">
          <img src="cid:planning_visual" alt="Salida diaria Planning DDV"
               width="1380" style="display:block;width:100%%;max-width:1380px;height:auto;border:0;margin:0 auto;">
        </td></tr>
      </table>
    </body></html>
    """
    ps = f"""
    $outlook = New-Object -ComObject Outlook.Application
    $mail = $outlook.CreateItem(0)
    $mail.To = '{ps_escape(to)}'
    $mail.CC = '{ps_escape(cc)}'
    $mail.Subject = '{ps_escape(subject)}'
    $img = $mail.Attachments.Add('{ps_escape(str(image_path))}')
    $img.PropertyAccessor.SetProperty('http://schemas.microsoft.com/mapi/proptag/0x3712001F','planning_visual')
    $mail.HTMLBody = @'
{body}
'@
    {action}
    """
    ps_path.write_text(ps, encoding="utf-8-sig")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_path)],
        capture_output=True, text=True, timeout=45,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "No se pudo abrir Outlook.")

def open_outlook_draft(to: str, cc: str, subject: str, body_html: str, send_now: bool = False) -> None:
    if platform.system() != "Windows":
        raise RuntimeError("La apertura directa en Outlook solo está disponible en Windows.")
    EXPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = EXPORTS_DIR / f"mail_{stamp}.html"
    html_path.write_text(body_html, encoding="utf-8")
    ps_path = Path(tempfile.gettempdir()) / f"operations_ddv_mail_{stamp}.ps1"
    def ps_escape(text: str) -> str:
        return text.replace("'", "''")
    action = "$mail.Send()" if send_now else "$mail.Display()"
    ps = f"""
    $outlook = New-Object -ComObject Outlook.Application
    $mail = $outlook.CreateItem(0)
    $mail.To = '{ps_escape(to)}'
    $mail.CC = '{ps_escape(cc)}'
    $mail.Subject = '{ps_escape(subject)}'
    $mail.HTMLBody = [System.IO.File]::ReadAllText('{ps_escape(str(html_path))}', [System.Text.Encoding]::UTF8)
    $logo = $mail.Attachments.Add('{ps_escape(str(WEB_DIR / "assets" / "ddv_logo.png"))}')
    $logo.PropertyAccessor.SetProperty('http://schemas.microsoft.com/mapi/proptag/0x3712001F','ddv_logo')
    {action}
    """
    ps_path.write_text(ps, encoding="utf-8-sig")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "No se pudo abrir Outlook.")



def history_report_html(params: dict[str, str], section: str) -> str:
    data = history_rows(params)
    titles = {"indicators": "Flota y productividad", "routes": "Salidas históricas", "novelties": "Histórico de novedades"}
    title = titles.get(section, "Histórico Planning DDV")
    if section == "indicators":
        headers = ["Fecha","Base","Flota reparto","Flota total","Sin asignación","Utilización","PDV","Bultos","Drop size"]
        rows = [[r["date"],r["division"],r["fleet_used"],r["fleet_total"],r["fleet_free"],f"{_fmt_ar(r['utilization'],1)}%",_fmt_ar(r["pdv"]),_fmt_ar(r["bultos"],1),_fmt_ar(r["drop_size"],1)] for r in data["indicators"]]
    elif section == "novelties":
        headers = ["Fecha","Empleado","División","Rol","Novedad","Detalle"]
        rows = [[r["novelty_date"],r["employee_name"],r["division"],r["role"],r["reason"],r["notes"]] for r in data["novelties"]]
    else:
        headers = ["Fecha","División","Localidad","Dominio","Chofer","Ayudante 1","Ayudante 2","PDV","Bultos","KMS","Estado"]
        rows = [[r["planning_date"],r["division"],r["locality"],r["domain"],r["driver"],r["helper1"],r["helper2"],_fmt_ar(r["pdv"]),_fmt_ar(r["bultos"],1),_fmt_ar(r.get("kms",0)),r["status"]] for r in data["routes"]]
    th = "".join("<th>" + html.escape(str(h)) + "</th>" for h in headers)
    body_rows = "".join("<tr>" + "".join("<td>" + html.escape(str(v if v not in (None, '') else '-')) + "</td>" for v in row) + "</tr>" for row in rows)
    meta = f"Desde {params.get('start','')} hasta {params.get('end','')} · Base {params.get('division','TODAS')} · Localidad {params.get('locality','') or 'Todas'}"
    return "<!doctype html><html><head><meta charset='utf-8'><style>body{font-family:Segoe UI,Arial;background:#eef3f7;margin:0;padding:24px;color:#12314b}.report{background:#fff;padding:22px;border:1px solid #ccdce6}h1{margin:0 0 5px;color:#0b3555}.meta{margin-bottom:18px;color:#5e7484}table{border-collapse:collapse;width:100%;font-size:11px}th{background:#0b5f98;color:#fff;padding:8px;text-align:left}td{padding:7px;border-bottom:1px solid #dce6ec}tr:nth-child(even){background:#f3f7f9}</style></head><body><div class='report'><h1>" + html.escape(title) + "</h1><div class='meta'>" + html.escape(meta) + "</div><table><thead><tr>" + th + "</tr></thead><tbody>" + body_rows + "</tbody></table></div></body></html>"

def generate_history_pdf(params: dict[str, str], section: str) -> Path:
    EXPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = EXPORTS_DIR / f"historico_{section}_{stamp}.html"
    pdf_path = EXPORTS_DIR / f"historico_{section}_{stamp}.pdf"
    html_path.write_text(history_report_html(params, section), encoding="utf-8")
    edge = _edge_executable()
    if edge is None:
        raise RuntimeError("No se encontró Microsoft Edge para generar el PDF.")
    command = [str(edge), "--headless", "--disable-gpu", "--print-to-pdf-no-header", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not pdf_path.exists():
        raise RuntimeError(result.stderr.strip() or "No se pudo generar el PDF.")
    return pdf_path

def open_history_mail(params: dict[str, str], section: str) -> None:
    if platform.system() != "Windows":
        raise RuntimeError("Outlook solo está disponible en Windows.")
    EXPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = EXPORTS_DIR / f"historico_mail_{stamp}.html"
    html_path.write_text(history_report_html(params, section), encoding="utf-8")
    title = {"indicators": "Flota y productividad", "routes": "Salidas históricas", "novelties": "Histórico de novedades"}.get(section, "Histórico Planning DDV")
    ps_path = Path(tempfile.gettempdir()) / f"planning_history_{stamp}.ps1"
    safe_path = str(html_path).replace("'", "''")
    safe_title = title.replace("'", "''")
    ps = f"""$o=New-Object -ComObject Outlook.Application
$m=$o.CreateItem(0)
$m.To='Planning'
$m.Subject='{safe_title} - Planning DDV'
$m.HTMLBody=[IO.File]::ReadAllText('{safe_path}',[Text.Encoding]::UTF8)
$m.Display()"""
    ps_path.write_text(ps, encoding="utf-8-sig")
    result = subprocess.run(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-File",str(ps_path)], capture_output=True, text=True, timeout=45)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "No se pudo abrir Outlook.")


def list_users() -> list[dict[str, Any]]:
    with db() as con:
        rows = con.execute(
            """
            SELECT id,username,display_name,role,assigned_base,active,must_change_password,created_at,updated_at,last_login,created_by
            FROM users ORDER BY active DESC, display_name COLLATE NOCASE
            """
        ).fetchall()
        return [public_user(row) for row in rows if public_user(row)]


def create_user(payload: dict[str, Any], admin: dict[str, Any], ip_address: str = "") -> dict[str, Any]:
    username = canonical(payload.get("username"))
    display_name = str(payload.get("display_name") or username).strip()
    password = temporary_password_from_payload(payload)
    role = normalize_role(str(payload.get("role") or "CONSULTA"))
    assigned_base = normalize_assigned_base(str(payload.get("assigned_base") or ""), role)
    if not username or not display_name or not password:
        raise ValueError("Debe completar usuario, nombre visible y contraseña provisoria.")
    with db() as con:
        params = (
            username,
            display_name,
            hash_password(password),
            role,
            assigned_base,
            int(truthy(payload.get("active"), True)),
            int(truthy(payload.get("must_change_password"), True)),
            now_iso(),
            now_iso(),
            admin.get("username", ""),
        )
        if postgres_enabled():
            user_id = con.execute(
                """
                INSERT INTO users(username,display_name,password_hash,role,assigned_base,active,must_change_password,created_at,updated_at,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                RETURNING id
                """,
                params,
            ).fetchone()[0]
        else:
            con.execute(
                """
                INSERT INTO users(username,display_name,password_hash,role,assigned_base,active,must_change_password,created_at,updated_at,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                params,
            )
            user_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        register_audit_event(con, admin, "Creación de usuario", "Usuarios", record_type="users", record_id=str(user_id), new_data={"username": username, "role": role, "assigned_base": assigned_base}, ip_address=ip_address)
    return {"ok": True}


def update_user(user_id: Any, payload: dict[str, Any], admin: dict[str, Any], ip_address: str = "") -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("Usuario no válido.")
    role = normalize_role(str(payload.get("role") or "CONSULTA"))
    assigned_base = normalize_assigned_base(str(payload.get("assigned_base") or ""), role)
    display_name = str(payload.get("display_name") or "").strip()
    active = int(truthy(payload.get("active"), True))
    must_change = int(truthy(payload.get("must_change_password"), False))
    if not display_name:
        raise ValueError("Debe completar nombre visible.")
    with db() as con:
        previous = row_dict(con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        if not previous:
            raise ValueError("El usuario no existe.")
        if str(previous.get("id")) == str(admin.get("id")) and active == 0:
            active_admins = con.execute(f"SELECT COUNT(*) FROM users WHERE role='ADMINISTRADOR' AND {active_condition('users')} AND id<>?", (user_id,)).fetchone()[0]
            if not active_admins:
                raise ValueError("No se puede desactivar el último administrador activo.")
        con.execute(
            """
            UPDATE users
            SET display_name=?, role=?, assigned_base=?, active=?, must_change_password=?, updated_at=?
            WHERE id=?
            """,
            (display_name, role, assigned_base, active, must_change, now_iso(), user_id),
        )
        register_audit_event(con, admin, "Edición de usuario", "Usuarios", record_type="users", record_id=str(user_id), previous_data=previous, new_data={"display_name": display_name, "role": role, "assigned_base": assigned_base, "active": active}, ip_address=ip_address)
    return {"ok": True}


def reset_user_password(user_id: Any, password: str, admin: dict[str, Any], ip_address: str = "") -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("Usuario no válido.")
    if not password:
        raise ValueError("Debe indicar una contraseña provisoria.")
    with db() as con:
        row = con.execute("SELECT id,username FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("El usuario no existe.")
        con.execute(
            "UPDATE users SET password_hash=?, must_change_password=1, updated_at=? WHERE id=?",
            (hash_password(password), now_iso(), user_id),
        )
        con.execute("UPDATE user_sessions SET active=? WHERE user_id=?", (active_value("user_sessions", False), user_id))
        register_audit_event(con, admin, "Restablecimiento de contraseña", "Usuarios", record_type="users", record_id=str(user_id), new_data={"username": row["username"]}, ip_address=ip_address)
    return {"ok": True}


def change_own_password(user: dict[str, Any], current_password: str, new_password: str, ip_address: str = "") -> None:
    if not new_password or len(new_password) < 8:
        raise ValueError("La nueva contraseña debe tener al menos 8 caracteres.")
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE id=?", (user.get("id"),)).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            raise ValueError("No se pudo cambiar la contraseña.")
        con.execute("UPDATE users SET password_hash=?, must_change_password=0, updated_at=? WHERE id=?", (hash_password(new_password), now_iso(), user.get("id")))
        register_audit_event(con, user, "Cambio de contraseña", "Usuarios", record_type="users", record_id=str(user.get("id")), ip_address=ip_address)


def audit_rows(params: dict[str, str]) -> list[dict[str, Any]]:
    start = params.get("start") or "1900-01-01"
    end = params.get("end") or "2999-12-31"
    values: list[Any] = [start + "T00:00:00", end + "T23:59:59"]
    query = "SELECT created_at,username,action,module,division,operational_date,record_type,record_id,ip_address FROM audit_log WHERE created_at BETWEEN ? AND ?"
    if params.get("username"):
        query += " AND username LIKE ?"; values.append(f"%{params['username']}%")
    if params.get("module"):
        query += " AND module LIKE ?"; values.append(f"%{params['module']}%")
    if params.get("division") and params.get("division") != "TODAS":
        query += " AND division=?"; values.append(canonical(params["division"]))
    if params.get("action"):
        query += " AND action LIKE ?"; values.append(f"%{params['action']}%")
    query += " ORDER BY created_at DESC LIMIT 500"
    with db() as con:
        return [dict(row) for row in con.execute(query, values).fetchall()]


def active_users() -> list[dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(minutes=ACTIVE_SESSION_MINUTES)).isoformat(timespec="seconds")
    with db() as con:
        rows = con.execute(
            f"""
            SELECT u.username,u.display_name,u.role,u.assigned_base,MAX(s.last_activity) last_activity
            FROM user_sessions s JOIN users u ON u.id=s.user_id
            WHERE {active_condition('user_sessions', 's')} AND s.last_activity>=?
            GROUP BY u.id,u.username,u.display_name,u.role,u.assigned_base
            ORDER BY last_activity DESC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(row) | {"role_label": role_label(row["role"]), "state": "Conectado"} for row in rows]

class Handler(BaseHTTPRequestHandler):
    server_version = "PlanningDDV/3.8"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() if forwarded else self.client_address[0]

    def set_session_cookie(self, token: str) -> None:
        secure = " Secure;" if self.headers.get("X-Forwarded-Proto", "") == "https" or os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID") else ""
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_HOURS * 3600};{secure}")

    def clear_session_cookie(self) -> None:
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")

    def send_auth_json(self, payload: Any, status: int = 200, token: str = "", clear_cookie: bool = False) -> None:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if token:
            self.set_session_cookie(token)
        if clear_cookie:
            self.clear_session_cookie()
        self.end_headers()
        self.wfile.write(raw)

    def send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404)
            return
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def login_user(self, payload: dict[str, Any]) -> None:
        username = canonical(payload.get("username"))
        password = str(payload.get("password") or "")
        key = f"{self.client_ip()}:{username}"
        now_ts = time.time()
        attempts = [t for t in FAILED_LOGINS.get(key, []) if now_ts - t < FAILED_LOGIN_WINDOW_SECONDS]
        if len(attempts) >= FAILED_LOGIN_LIMIT:
            self.send_json({"error": "Credenciales inválidas."}, 401)
            return
        with db() as con:
            row = con.execute("SELECT * FROM users WHERE UPPER(TRIM(username))=?", (username,)).fetchone()
            if not row or not int(row["active"] or 0) or not verify_password(password, row["password_hash"]):
                attempts.append(now_ts)
                FAILED_LOGINS[key] = attempts
                register_audit_event(con, {"username": username}, "Intento fallido de inicio de sesión", "Autenticación", ip_address=self.client_ip())
                self.send_json({"error": "Credenciales inválidas."}, 401)
                return
            token = secrets.token_urlsafe(48)
            token_digest = hash_token(token)
            created = now_iso()
            expires = (datetime.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
            con.execute(
                """
                INSERT INTO user_sessions(user_id,token_hash,created_at,expires_at,last_activity,ip_address,user_agent,active)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (row["id"], token_digest, created, expires, created, self.client_ip(), self.headers.get("User-Agent", ""), active_value("user_sessions")),
            )
            con.execute("UPDATE users SET last_login=?, updated_at=? WHERE id=?", (created, created, row["id"]))
            FAILED_LOGINS.pop(key, None)
            user = public_user(row)
            register_audit_event(con, user, "Inicio de sesión", "Autenticación", ip_address=self.client_ip())
        self.send_auth_json({"ok": True, "user": user}, token=token)

    def logout_user(self, user: dict[str, Any] | None) -> None:
        cookie_header = self.headers.get("Cookie", "")
        parsed = cookies.SimpleCookie()
        parsed.load(cookie_header)
        token = parsed[SESSION_COOKIE].value if SESSION_COOKIE in parsed else ""
        with db() as con:
            if token:
                con.execute("UPDATE user_sessions SET active=? WHERE token_hash=?", (active_value("user_sessions", False), hash_token(token)))
            register_audit_event(con, user, "Cierre de sesión", "Autenticación", ip_address=self.client_ip())
        self.send_auth_json({"ok": True}, clear_cookie=True)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        try:
            if path == "/":
                self.send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            elif path.startswith("/assets/"):
                asset = WEB_DIR / path.lstrip("/")
                content_type = "image/png" if asset.suffix.lower() == ".png" else "application/octet-stream"
                self.send_file(asset, content_type)
            elif path == "/downloads/planning_ddv_outlook_connector.zip":
                raw = connector_zip_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", "attachment; filename=planning_ddv_outlook_connector.zip")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
            elif path == "/api/health":
                self.send_json({"ok": True, "status": "ready"})
            elif path == "/api/auth/me":
                user = get_current_user(self)
                with db() as con:
                    user_count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                if not user:
                    self.send_json(
                        {
                            "authenticated": False,
                            "user": None,
                            "setup_required": user_count == 0,
                            "error": "Debe iniciar sesión.",
                        },
                        401,
                    )
                else:
                    self.send_json({"authenticated": True, "user": user, "setup_required": False})
            elif re.fullmatch(r"/api/mail/package/[A-Za-z0-9_-]+", path):
                token = path.rsplit("/", 1)[-1]
                package = consume_outlook_connector_package(token)
                if not package:
                    self.send_json({"error": "El paquete de mail venció o ya fue utilizado."}, 404)
                else:
                    self.send_json(package)
            elif path.startswith("/api/"):
                user = require_login(self)
                if path in {"/api/masters", "/api/backup/download", "/api/audit-log", "/api/audit", "/api/users", "/api/active-users", "/api/storage/diagnostics", "/api/diagnostics/storage"}:
                    require_role(user, "ADMINISTRADOR")
                if path == "/api/users":
                    self.send_json({"users": list_users()})
                elif path in {"/api/audit-log", "/api/audit"}:
                    self.send_json({"rows": audit_rows(query)})
                elif path == "/api/active-users":
                    self.send_json({"rows": active_users()})
                elif path in {"/api/storage/diagnostics", "/api/diagnostics/storage"}:
                    self.send_json(storage_diagnostics())
                elif path == "/api/dates":
                    self.send_json({"dates": dates_list()})
                elif path == "/api/routes":
                    d = query.get("date", "")
                    self.send_json({"routes": route_rows(d, query.get("division", "")), "summary": summary(d) if d else {}})
                elif path == "/api/whatsapp":
                    d = query.get("date", "")
                    self.send_json({"rows": whatsapp_rows(d, query.get("division", "TODAS"))})
                elif path == "/api/export/whatsapp.png":
                    d = query.get("date", "")
                    if not d:
                        raise ValueError("Debe seleccionar una fecha.")
                    png = render_whatsapp_image(d, query.get("division", "TODAS"))
                    register_audit_event(None, user, "Exportación WhatsApp", "WhatsApp salida", d, query.get("division", "TODAS"), ip_address=self.client_ip())
                    raw = png.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Disposition", f"attachment; filename=salida_whatsapp_{d}.png")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers(); self.wfile.write(raw)
                elif path == "/api/export/whatsapp-choferes.png":
                    d = query.get("date", "")
                    if not d:
                        raise ValueError("Debe seleccionar una fecha.")
                    png = render_whatsapp_drivers_image(d, query.get("division", "TODAS"))
                    register_audit_event(None, user, "Exportación WhatsApp choferes", "WhatsApp salida", d, query.get("division", "TODAS"), ip_address=self.client_ip())
                    raw = png.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Disposition", f"attachment; filename=salida_choferes_{d}.png")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers(); self.wfile.write(raw)
                elif path == "/api/options":
                    self.send_json(options_for_routes(query.get("date", "")))
                elif path == "/api/summary":
                    self.send_json(summary(query.get("date", "")))
                elif path == "/api/unassigned":
                    self.send_json({"rows": unassigned(query.get("date", ""), query.get("division", "")), "reasons": NOVELTY_REASONS})
                elif path == "/api/novelties":
                    self.send_json({"rows": novelty_rows(query.get("date", ""))})
                elif path == "/api/recargas":
                    today = date.today().isoformat()
                    start = query.get("start") or query.get("date") or today
                    end = query.get("end") or start
                    self.send_json(recarga_rows(start, end, query.get("division", "")))
                elif path == "/api/kms":
                    today = date.today().isoformat()
                    self.send_json(kms_rows(query.get("start") or today, query.get("end") or today, query.get("division", "TODAS")))
                elif path == "/api/masters":
                    self.send_json(master_payload())
                elif path == "/api/history":
                    self.send_json(history_rows(query))
                elif path == "/api/history/export.pdf":
                    pdf = generate_history_pdf(query, query.get("section", "routes"))
                    register_audit_event(None, user, "Exportación histórico PDF", "Histórico", query.get("start", ""), query.get("division", "TODAS"), ip_address=self.client_ip())
                    raw = pdf.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Disposition", f"attachment; filename={pdf.name}")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                elif path == "/api/mail/capabilities":
                    self.send_json({
                        "direct_outlook": platform.system() == "Windows",
                        "server_platform": platform.system(),
                    })
                elif path == "/api/mail/preview":
                    body = mail_html(query.get("date", ""), query.get("division", "TODAS"), preview=True)
                    self.send_response(200)
                    raw = body.encode("utf-8")
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                elif path == "/api/export/daily.pdf":
                    d = query.get("date", "")
                    if not d:
                        raise ValueError("Debe seleccionar una fecha para generar el PDF.")
                    pdf = generate_pdf(d, query.get("division", "TODAS"))
                    register_audit_event(None, user, "Exportación PDF salida diaria", "Salida diaria", d, query.get("division", "TODAS"), ip_address=self.client_ip())
                    raw = pdf.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Disposition", f"attachment; filename={pdf.name}")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers(); self.wfile.write(raw)
                elif path == "/api/export/mail.png":
                    d = query.get("date", "")
                    if not d:
                        raise ValueError("Debe seleccionar una fecha para generar la imagen.")
                    png = render_mail_image(d, query.get("division", "TODAS"))
                    raw = png.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Disposition", f"attachment; filename={png.name}")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers(); self.wfile.write(raw)
                elif path == "/api/backup/download":
                    backup = create_backup_zip()
                    register_audit_event(None, user, "Generación de backup", "Backup", ip_address=self.client_ip())
                    raw = backup.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f"attachment; filename={backup.name}")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers(); self.wfile.write(raw)
                elif path == "/api/export/daily.csv":
                    d = query.get("date", "")
                    output = io.StringIO()
                    writer = csv.writer(output, delimiter=";")
                    writer.writerow(["FECHA","DIVISION","UNIDAD","DOMINIO","PDV","BULTOS","RENDICION","CHOFER","AYUDANTE 1","AYUDANTE 2","LOCALIDAD","OBSERVACIONES"])
                    for r in route_rows(d):
                        writer.writerow([d,r["division"],r["unit_id"],r["domain"],r["pdv"],r["bultos"],r["rendicion"],r["driver"],r["helper1"],r["helper2"],r["locality"],r["observations"]])
                    register_audit_event(None, user, "Exportación CSV salida diaria", "Salida diaria", d, ip_address=self.client_ip())
                    raw = output.getvalue().encode("utf-8-sig")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", f"attachment; filename=salida_diaria_{d}.csv")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers(); self.wfile.write(raw)
                else:
                    self.send_error(404)
                return
            elif path == "/api/dates":
                self.send_json({"dates": dates_list()})
            elif path == "/api/routes":
                d = query.get("date", "")
                self.send_json({"routes": route_rows(d, query.get("division", "")), "summary": summary(d) if d else {}})
            elif path == "/api/whatsapp":
                d = query.get("date", "")
                self.send_json({"rows": whatsapp_rows(d, query.get("division", "TODAS"))})
            elif path == "/api/export/whatsapp.png":
                d = query.get("date", "")
                if not d:
                    raise ValueError("Debe seleccionar una fecha.")
                png = render_whatsapp_image(d, query.get("division", "TODAS"))
                raw = png.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Disposition", f"attachment; filename=salida_whatsapp_{d}.png")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            elif path == "/api/export/whatsapp-choferes.png":
                d = query.get("date", "")
                if not d:
                    raise ValueError("Debe seleccionar una fecha.")
                png = render_whatsapp_drivers_image(d, query.get("division", "TODAS"))
                raw = png.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Disposition", f"attachment; filename=salida_choferes_{d}.png")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            elif path == "/api/options":
                self.send_json(options_for_routes(query.get("date", "")))
            elif path == "/api/summary":
                self.send_json(summary(query.get("date", "")))
            elif path == "/api/unassigned":
                self.send_json({"rows": unassigned(query.get("date", ""), query.get("division", "")), "reasons": NOVELTY_REASONS})
            elif path == "/api/novelties":
                self.send_json({"rows": novelty_rows(query.get("date", ""))})
            elif path == "/api/recargas":
                today = date.today().isoformat()
                start = query.get("start") or query.get("date") or today
                end = query.get("end") or start
                self.send_json(recarga_rows(start, end, query.get("division", "")))
            elif path == "/api/kms":
                today = date.today().isoformat()
                self.send_json(kms_rows(query.get("start") or today, query.get("end") or today, query.get("division", "TODAS")))
            elif path == "/api/masters":
                self.send_json(master_payload())
            elif path == "/api/history":
                self.send_json(history_rows(query))
            elif path == "/api/history/export.pdf":
                pdf = generate_history_pdf(query, query.get("section", "routes"))
                raw = pdf.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f"attachment; filename={pdf.name}")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif path == "/api/mail/capabilities":
                self.send_json({
                    "direct_outlook": platform.system() == "Windows",
                    "server_platform": platform.system(),
                })
            elif path == "/api/mail/preview":
                body = mail_html(query.get("date", ""), query.get("division", "TODAS"), preview=True)
                self.send_response(200)
                raw = body.encode("utf-8")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif path == "/api/export/daily.pdf":
                d = query.get("date", "")
                if not d:
                    raise ValueError("Debe seleccionar una fecha para generar el PDF.")
                pdf = generate_pdf(d, query.get("division", "TODAS"))
                raw = pdf.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f"attachment; filename={pdf.name}")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif path == "/api/export/mail.png":
                d = query.get("date", "")
                if not d:
                    raise ValueError("Debe seleccionar una fecha para generar la imagen.")
                png = render_mail_image(d, query.get("division", "TODAS"))
                raw = png.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Disposition", f"attachment; filename={png.name}")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            elif path == "/api/backup/download":
                backup = create_backup_zip()
                raw = backup.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f"attachment; filename={backup.name}")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            elif path == "/api/export/daily.csv":
                d = query.get("date", "")
                output = io.StringIO()
                writer = csv.writer(output, delimiter=";")
                writer.writerow(["FECHA","DIVISION","UNIDAD","DOMINIO","PDV","BULTOS","RENDICION","CHOFER","AYUDANTE 1","AYUDANTE 2","LOCALIDAD","OBSERVACIONES"])
                for r in route_rows(d):
                    writer.writerow([d,r["division"],r["unit_id"],r["domain"],r["pdv"],r["bultos"],r["rendicion"],r["driver"],r["helper1"],r["helper2"],r["locality"],r["observations"]])
                raw = output.getvalue().encode("utf-8-sig")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f"attachment; filename=salida_diaria_{d}.csv")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            else:
                self.send_error(404)
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, 401 if "sesión" in str(exc) else 403)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/auth/login":
                self.login_user(payload)
                return
            if path == "/api/auth/logout":
                self.logout_user(get_current_user(self))
                return
            user = require_login(self)
            if path == "/api/auth/change-password":
                change_own_password(user, str(payload.get("current_password") or ""), str(payload.get("new_password") or ""), self.client_ip())
                self.send_json({"ok": True})
                return
            if canonical(user.get("role")) == "CONSULTA":
                raise PermissionError("El rol Consulta es solo lectura.")
            if path in {"/api/users", "/api/users/create"}:
                require_role(user, "ADMINISTRADOR")
                create_user(payload, user, self.client_ip())
                self.send_json({"ok": True, "users": list_users()})
            elif path == "/api/users/update":
                require_role(user, "ADMINISTRADOR")
                update_user(payload.get("id") or "", payload, user, self.client_ip())
                self.send_json({"ok": True, "users": list_users()})
            elif path == "/api/users/reset-password":
                require_role(user, "ADMINISTRADOR")
                reset_user_password(payload.get("id") or "", str(payload.get("password") or ""), user, self.client_ip())
                self.send_json({"ok": True, "users": list_users()})
            elif re.fullmatch(r"/api/users/[^/]+/reset-password", path):
                require_role(user, "ADMINISTRADOR")
                user_id = path.strip("/").split("/")[2]
                reset_user_password(user_id, str(payload.get("password") or ""), user, self.client_ip())
                self.send_json({"ok": True, "users": list_users()})
            elif path == "/api/import":
                planning_date = payload.get("date", "")
                if not planning_date:
                    raise ValueError("Debe indicar la fecha operativa.")
                all_rows: list[dict[str, Any]] = []
                per_division = {}
                for key, division in (("tw", "TRELEW"), ("pm", "PUERTO MADRYN")):
                    content = payload.get(key)
                    if content:
                        require_base_access(user, division)
                        raw = base64.b64decode(content.split(",")[-1])
                        rows = validate_chess(raw, division, planning_date)
                        all_rows.extend(rows)
                        per_division[division] = len(rows)
                if not all_rows:
                    raise ValueError("Debe seleccionar al menos un archivo TW o PM.")
                result = import_routes(all_rows)
                register_audit_event(None, user, "Carga de archivo CHESS", "Planning CHESS", planning_date, ",".join(per_division), new_data={"processed": len(all_rows), "by_division": per_division}, ip_address=self.client_ip())
                self.send_json({"ok": True, "processed": len(all_rows), "by_division": per_division, **result, "routes": route_rows(planning_date), "summary": summary(planning_date)})
            elif path == "/api/whatsapp/save":
                planning_date = payload.get("date", "")
                require_base_access(user, payload.get("division", "TODAS"))
                save_whatsapp_observations(planning_date, payload.get("rows", []))
                register_audit_event(None, user, "Modificación WhatsApp salida", "WhatsApp salida", planning_date, payload.get("division", "TODAS"), new_data={"rows": len(payload.get("rows", []))}, ip_address=self.client_ip())
                self.send_json({"ok": True, "rows": whatsapp_rows(planning_date, payload.get("division", "TODAS"))})
            elif path == "/api/routes/save":
                planning_date = payload.get("date", "")
                require_base_access(user, payload.get("division", "TODAS"))
                for route in payload.get("routes", []):
                    require_base_access(user, route.get("division", ""))
                save_routes(
                    planning_date,
                    payload.get("routes", []),
                    bool(payload.get("confirm")),
                    payload.get("division", "TODAS"),
                )
                register_audit_event(None, user, "Confirmación de jornada" if payload.get("confirm") else "Guardado de borrador", "Planning CHESS", planning_date, payload.get("division", "TODAS"), new_data={"routes": len(payload.get("routes", []))}, ip_address=self.client_ip())
                self.send_json({"ok": True, "routes": route_rows(planning_date), "summary": summary(planning_date)})
            elif path == "/api/routes/copy-last":
                require_role(user, "ADMINISTRADOR")
                count = copy_last_assignments(payload.get("date", ""))
                register_audit_event(None, user, "Copia última asignación", "Planning CHESS", payload.get("date", ""), new_data={"copied": count}, ip_address=self.client_ip())
                self.send_json({"ok": True, "copied": count, "routes": route_rows(payload.get("date", ""))})
            elif path == "/api/novelties/save":
                for row in payload.get("rows", []):
                    require_base_access(user, row.get("division", ""))
                save_novelties(payload.get("date", ""), payload.get("rows", []))
                register_audit_event(None, user, "Modificación de novedades", "Novedades", payload.get("date", ""), new_data={"rows": len(payload.get("rows", []))}, ip_address=self.client_ip())
                self.send_json({"ok": True, "rows": novelty_rows(payload.get("date", ""))})
            elif path == "/api/masters/save":
                require_role(user, "ADMINISTRADOR")
                save_master(payload.get("table", ""), payload.get("rows", []))
                register_audit_event(None, user, "Modificación de configuración", "Configuración", record_type=payload.get("table", ""), new_data={"rows": len(payload.get("rows", []))}, ip_address=self.client_ip())
                self.send_json({"ok": True, **master_payload()})
            elif path == "/api/masters/delete":
                require_role(user, "ADMINISTRADOR")
                delete_master(payload.get("table", ""), int(payload.get("id") or 0))
                register_audit_event(None, user, "Eliminación de configuración", "Configuración", record_type=payload.get("table", ""), record_id=str(payload.get("id") or ""), ip_address=self.client_ip())
                self.send_json({"ok": True, **master_payload()})
            elif path == "/api/history/mail":
                open_history_mail(payload, payload.get("section", "routes"))
                register_audit_event(None, user, "Generación mail histórico", "Histórico", payload.get("start", ""), payload.get("division", "TODAS"), ip_address=self.client_ip())
                self.send_json({"ok": True})
            elif path == "/api/mail/package":
                self.send_json(create_outlook_connector_package(payload, user, self))
            elif path == "/api/mail/open":
                planning_date = payload.get("date", "")
                division = payload.get("division", "TODAS")
                body = report_html(planning_date, division, logo_src="cid:ddv_logo", include_novelties=True)
                recipient = payload.get("to", "") or "Planning"
                open_outlook_visual_draft(recipient, payload.get("cc", ""), payload.get("subject", ""), planning_date, division, bool(payload.get("send_now")))
                with db() as con:
                    con.execute(
                        "INSERT INTO mail_log(mail_date,planning_date,recipients,cc,subject,status) VALUES(?,?,?,?,?,?)",
                        (datetime.now().isoformat(timespec="seconds"), planning_date, recipient, payload.get("cc", ""), payload.get("subject", ""), "ENVIADO" if payload.get("send_now") else "BORRADOR ABIERTO"),
                    )
                    register_audit_event(con, user, "Envío de mail" if payload.get("send_now") else "Generación de borrador mail", "Mail operativo", planning_date, division, new_data={"to": recipient, "subject": payload.get("subject", "")}, ip_address=self.client_ip())
                self.send_json({"ok": True})
            else:
                self.send_error(404)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, 401 if "sesión" in str(exc) else 403)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            user = require_login(self)
            require_role(user, "ADMINISTRADOR")
            match = re.fullmatch(r"/api/users/([^/]+)", path)
            if not match:
                self.send_error(404)
                return
            update_user(match.group(1), payload, user, self.client_ip())
            self.send_json({"ok": True, "users": list_users()})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, 401 if "sesión" in str(exc) else 403)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


def run() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print("=" * 66)
    print("  OPERATIONS DDV - PLANNING OPERATIVO")
    print(f"  Aplicación disponible en: {url}")
    print("  Para cerrar, presione Ctrl+C en esta ventana.")
    print("=" * 66)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
