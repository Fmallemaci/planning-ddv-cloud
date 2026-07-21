from __future__ import annotations

import argparse
import os
import re
import sqlite3
import uuid
from pathlib import Path
from datetime import date, datetime
from typing import Any

psycopg = None

APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE = APP_DIR / "data" / "operations_ddv.db"
SCHEMA_SQL = APP_DIR / "sql" / "supabase_schema.sql"
LEGACY_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "planning-ddv-cloud/sqlite-migration")

OPERATIONAL_TABLES = [
    "employees",
    "localities",
    "vehicle_people",
    "employee_locality_roles",
    "personnel_novelties",
    "planning_routes",
    "recargas",
    "mail_log",
]

AUTH_TABLES = [
    "users",
    "user_sessions",
    "audit_log",
]

TABLES = OPERATIONAL_TABLES

CONFLICT_COLUMNS = {
    "planning_routes": ["planning_date", "division", "domain", "domain_seq"],
    "employees": ["name"],
    "vehicle_people": ["domain", "employee_name", "role"],
    "localities": ["name", "division"],
    "employee_locality_roles": ["employee_name", "division", "locality"],
    "personnel_novelties": ["novelty_date", "employee_name"],
    "recargas": ["route_id", "employee_name", "role"],
    "users": ["username"],
    "user_sessions": ["token_hash"],
}

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

DATE_COLUMN_CANDIDATES = ["planning_date", "operational_date", "day_date", "date", "fecha"]
DIVISION_COLUMN_CANDIDATES = ["division", "base", "assigned_base"]
PLANNING_DAY_TABLE_CANDIDATES = ["planning_days", "operational_days", "days"]


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


def normalize_uuid_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except (TypeError, ValueError):
        return None


def deterministic_legacy_uuid(table: str, legacy_id: Any) -> str | None:
    text = str(legacy_id or "").strip()
    if not text:
        return None
    return str(uuid.uuid5(LEGACY_UUID_NAMESPACE, f"{table}:{text}"))


def normalize_json_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False, default=str)
    text = str(value).strip()
    if not text:
        return None
    try:
        import json

        json.loads(text)
        return text
    except (TypeError, ValueError):
        return None


def normalize_bool_value(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "t", "yes", "y", "si", "sí", "activo", "activa"}:
        return True
    if text in {"0", "false", "f", "no", "n", "inactivo", "inactiva"}:
        return False
    return None


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    for column in TEMPORAL_COLUMN_NAMES:
        if column in clean:
            clean[column] = normalize_date_value(clean[column], column in TIMESTAMP_COLUMN_NAMES)
    for column in JSON_COLUMN_NAMES:
        if column in clean:
            clean[column] = normalize_json_value(clean[column])
    return clean


def safe_text(value: Any, fallback: str = "N/D") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def audit_entity_name(row: dict[str, Any]) -> str:
    for key in ("entity_name", "username"):
        value = row.get(key)
        if str(value or "").strip():
            return f"Usuario {str(value).strip()}" if key == "username" else str(value).strip()
    record_type = str(row.get("record_type") or "").strip()
    record_id = str(row.get("record_id") or "").strip()
    if record_type and record_id:
        return f"{record_type} {record_id}"
    return "N/D"


def canonical(value: Any) -> str:
    return str(value or "").strip().upper()


def normalize_division_value(value: Any) -> str:
    div = canonical(value)
    if div in {"TW", "TRELEW"}:
        return "TRELEW"
    if div in {"PM", "PUERTO MADRYN", "PUERTO_MADRYN"}:
        return "PUERTO MADRYN"
    return div


def short_division_value(value: Any) -> str:
    div = normalize_division_value(value)
    if div == "TRELEW":
        return "TW"
    if div == "PUERTO MADRYN":
        return "PM"
    return div


def normalize_audit_division_value(division: Any) -> str | None:
    div = canonical(division)

    if div in {"TW", "TRELEW"}:
        return "TW"
    if div in {"PM", "PUERTO MADRYN", "PUERTO_MADRYN"}:
        return "PM"
    return None


def target_columns(pg: Any, table: str) -> set[str]:
    with pg.cursor() as cur:
        cur.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema='public' and table_name=%s
            """,
            (table,),
        )
        return {str(row[0]) for row in cur.fetchall()}


def target_column_types(pg: Any, table: str) -> dict[str, str]:
    with pg.cursor() as cur:
        cur.execute(
            """
            select column_name, data_type
            from information_schema.columns
            where table_schema='public' and table_name=%s
            """,
            (table,),
        )
        return {str(row[0]): str(row[1]) for row in cur.fetchall()}


def target_column_metadata(pg: Any, table: str) -> dict[str, dict[str, Any]]:
    with pg.cursor() as cur:
        cur.execute(
            """
            select column_name, data_type, is_nullable, column_default
            from information_schema.columns
            where table_schema='public' and table_name=%s
            """,
            (table,),
        )
        return {
            str(row[0]): {
                "data_type": str(row[1]),
                "is_nullable": str(row[2]),
                "column_default": row[3],
            }
            for row in cur.fetchall()
        }


def target_table_exists(pg: Any, table: str) -> bool:
    with pg.cursor() as cur:
        cur.execute(
            """
            select 1
            from information_schema.tables
            where table_schema='public' and table_name=%s
            """,
            (table,),
        )
        return cur.fetchone() is not None


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def foreign_key_target(pg: Any, source_table: str, source_column: str) -> tuple[str, str] | None:
    with pg.cursor() as cur:
        cur.execute(
            """
            select ccu.table_name, ccu.column_name
            from information_schema.table_constraints tc
            join information_schema.key_column_usage kcu
              on tc.constraint_name = kcu.constraint_name
             and tc.table_schema = kcu.table_schema
            join information_schema.constraint_column_usage ccu
              on ccu.constraint_name = tc.constraint_name
             and ccu.table_schema = tc.table_schema
            where tc.constraint_type = 'FOREIGN KEY'
              and tc.table_schema = 'public'
              and tc.table_name = %s
              and kcu.column_name = %s
            limit 1
            """,
            (source_table, source_column),
        )
        row = cur.fetchone()
        return (str(row[0]), str(row[1])) if row else None


def normalize_uuid_columns_for_target(table: str, rows: list[dict[str, Any]], target_types: dict[str, str]) -> None:
    for row in rows:
        for column, data_type in target_types.items():
            if data_type != "uuid" or column not in row:
                continue
            if column == "id":
                row[column] = normalize_uuid_value(row.get(column)) or deterministic_legacy_uuid(table, row.get(column))
            elif table == "recargas" and column == "route_id":
                row[column] = normalize_uuid_value(row.get(column)) or deterministic_legacy_uuid("planning_routes", row.get(column))
            else:
                row[column] = normalize_uuid_value(row.get(column))


def normalize_bool_columns_for_target(rows: list[dict[str, Any]], target_types: dict[str, str]) -> None:
    boolean_columns = {column for column, data_type in target_types.items() if data_type == "boolean"}
    for row in rows:
        for column in boolean_columns:
            if column in row:
                row[column] = normalize_bool_value(row.get(column))


def first_existing_column(metadata: dict[str, dict[str, Any]], candidates: list[str]) -> str | None:
    for column in candidates:
        if column in metadata:
            return column
    return None


def default_value_for_required_column(table: str, column: str, data_type: str) -> Any:
    if data_type == "uuid":
        return deterministic_legacy_uuid(table, column)
    if data_type in {"integer", "bigint", "smallint"}:
        return 0
    if data_type in {"numeric", "double precision", "real"}:
        return 0
    if data_type == "boolean":
        return True
    if "timestamp" in data_type:
        return datetime.now().isoformat(timespec="seconds")
    if data_type == "date":
        return date.today().isoformat()
    if data_type in {"json", "jsonb"}:
        return None
    return ""


def get_planning_day_model(pg: Any) -> dict[str, Any] | None:
    route_types = target_column_types(pg, "planning_routes")
    if "planning_day_id" not in route_types:
        return None

    fk = foreign_key_target(pg, "planning_routes", "planning_day_id")
    if fk:
        day_table, id_column = fk
    else:
        day_table = next((table for table in PLANNING_DAY_TABLE_CANDIDATES if target_table_exists(pg, table)), "")
        id_column = "id"
    if not day_table:
        raise RuntimeError("planning_routes requiere planning_day_id pero no se encontró tabla de jornadas.")

    metadata = target_column_metadata(pg, day_table)
    if id_column not in metadata:
        id_column = "id"
    date_column = first_existing_column(metadata, DATE_COLUMN_CANDIDATES)
    if not date_column:
        raise RuntimeError(f"No se encontró columna de fecha en {day_table}.")
    division_column = first_existing_column(metadata, DIVISION_COLUMN_CANDIDATES)
    return {
        "table": day_table,
        "id_column": id_column,
        "date_column": date_column,
        "division_column": division_column,
        "metadata": metadata,
    }


def planning_day_uuid(planning_date: Any, division: Any = "") -> str | None:
    clean_date = normalize_date_value(planning_date)
    if not clean_date:
        return None
    div = normalize_division_value(division)
    return deterministic_legacy_uuid("planning_days", f"{clean_date}:{div}")


def sqlite_planning_day_keys(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    keys = {
        (normalize_date_value(row.get("planning_date")) or "", normalize_division_value(row.get("division")))
        for row in rows
        if normalize_date_value(row.get("planning_date"))
    }
    return sorted(keys)


def find_planning_day_id(pg: Any, model: dict[str, Any], planning_date: str, division: str) -> Any | None:
    table = qident(model["table"])
    id_column = qident(model["id_column"])
    date_column = qident(model["date_column"])
    division_column = qident(model["division_column"]) if model["division_column"] else None
    if division_column:
        with pg.cursor() as cur:
            candidates = [division, short_division_value(division)]
            for candidate in dict.fromkeys(candidates):
                cur.execute(
                    f"select {id_column} from {table} where {date_column}=%s and {division_column}=%s limit 1",
                    (planning_date, candidate),
                )
                row = cur.fetchone()
                if row:
                    return row[0]
    else:
        with pg.cursor() as cur:
            cur.execute(f"select {id_column} from {table} where {date_column}=%s limit 1", (planning_date,))
            row = cur.fetchone()
            if row:
                return row[0]
    return None


def insert_planning_day(pg: Any, model: dict[str, Any], planning_date: str, division: str) -> Any:
    table = model["table"]
    id_column = model["id_column"]
    date_column = model["date_column"]
    division_column = model["division_column"]
    metadata = model["metadata"]
    row: dict[str, Any] = {}

    if metadata.get(id_column, {}).get("data_type") == "uuid":
        row[id_column] = planning_day_uuid(planning_date, division)
    row[date_column] = planning_date
    if division_column:
        row[division_column] = short_division_value(division)

    for column, info in metadata.items():
        if column in row:
            continue
        if info["is_nullable"] == "NO" and info["column_default"] is None:
            row[column] = default_value_for_required_column(table, column, info["data_type"])

    columns = list(row)
    placeholders = ", ".join(["%s"] * len(columns))
    quoted_columns = ", ".join(qident(column) for column in columns)
    update_cols = [column for column in columns if column != id_column]
    update_sql = ", ".join(f"{qident(column)}=excluded.{qident(column)}" for column in update_cols)
    if id_column in row and update_sql:
        sql = (
            f"insert into {qident(table)} ({quoted_columns}) values ({placeholders}) "
            f"on conflict ({qident(id_column)}) do update set {update_sql} "
            f"returning {qident(id_column)}"
        )
    elif id_column in row:
        sql = (
            f"insert into {qident(table)} ({quoted_columns}) values ({placeholders}) "
            f"on conflict ({qident(id_column)}) do nothing "
            f"returning {qident(id_column)}"
        )
    else:
        sql = f"insert into {qident(table)} ({quoted_columns}) values ({placeholders}) returning {qident(id_column)}"
    with pg.cursor() as cur:
        cur.execute(sql, tuple(row[column] for column in columns))
        result = cur.fetchone()
    return result[0] if result else find_planning_day_id(pg, model, planning_date, division)


def ensure_planning_days(pg: Any, route_rows: list[dict[str, Any]]) -> dict[tuple[str, str], Any]:
    model = get_planning_day_model(pg)
    if not model:
        return {}
    day_map: dict[tuple[str, str], Any] = {}
    for planning_date, division in sqlite_planning_day_keys(route_rows):
        day_id = find_planning_day_id(pg, model, planning_date, division)
        if day_id is None:
            day_id = insert_planning_day(pg, model, planning_date, division)
        day_map[(planning_date, division)] = day_id
    return day_map


def attach_planning_day_ids(rows: list[dict[str, Any]], day_map: dict[tuple[str, str], Any]) -> int:
    unresolved = 0
    if not day_map:
        return 0
    for row in rows:
        planning_date = normalize_date_value(row.get("planning_date")) or ""
        division = normalize_division_value(row.get("division"))
        day_id = day_map.get((planning_date, division))
        row["planning_day_id"] = day_id
        if day_id is None:
            unresolved += 1
    return unresolved


def report_planning_days_dry_run(pg: Any, route_rows: list[dict[str, Any]]) -> None:
    model = get_planning_day_model(pg)
    if not model:
        print("Jornadas operativas: planning_routes no requiere planning_day_id.")
        return
    keys = sqlite_planning_day_keys(route_rows)
    existing = 0
    missing = 0
    for planning_date, division in keys:
        if find_planning_day_id(pg, model, planning_date, division) is None:
            missing += 1
        else:
            existing += 1
    unresolved_routes = sum(
        1
        for row in route_rows
        if not normalize_date_value(row.get("planning_date")) or (normalize_date_value(row.get("planning_date")) or "", normalize_division_value(row.get("division"))) not in set(keys)
    )
    print(f"Jornadas operativas detectadas: {len(keys)}")
    print(f"Jornadas ya existentes en Supabase: {existing}")
    print(f"Jornadas a crear en migración real: {missing}")
    print(f"Rutas sin planning_day_id resoluble: {unresolved_routes}")


def report_uuid_compatibility(table: str, columns: list[str], rows: list[dict[str, Any]], target_types: dict[str, str]) -> None:
    uuid_columns = [column for column in columns if target_types.get(column) == "uuid"]
    if not uuid_columns:
        return
    print(f"Compatibilidad UUID {table}: {', '.join(uuid_columns)}")
    for column in uuid_columns:
        invalid = sum(1 for row in rows if row.get(column) not in (None, "") and normalize_uuid_value(row.get(column)) is None)
        if invalid:
            print(f"  - {column}: {invalid} valores legacy se convertirán a UUID determinístico o NULL según corresponda.")
        else:
            print(f"  - {column}: compatible.")


def report_bool_compatibility(table: str, columns: list[str], rows: list[dict[str, Any]], target_types: dict[str, str]) -> None:
    boolean_columns = [column for column in columns if target_types.get(column) == "boolean"]
    if not boolean_columns:
        return
    print(f"Compatibilidad boolean {table}: {', '.join(boolean_columns)}")
    for column in boolean_columns:
        converted = 0
        pending = 0
        for row in rows:
            value = row.get(column)
            normalized = normalize_bool_value(value)
            if value is not None and str(value).strip() != "" and not isinstance(value, bool):
                if normalized is None:
                    pending += 1
                else:
                    converted += 1
        print(f"  - {column}: {converted} valores convertidos; {pending} valores smallint/text pendientes.")


def sqlite_rows(path: Path, table: str) -> tuple[list[str], list[dict[str, Any]]]:
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        exists = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            return [], []
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        columns = [col[1] for col in con.execute(f"PRAGMA table_info({table})").fetchall()]
        return columns, [dict(row) for row in rows]


def execute_schema(pg: Any) -> None:
    with pg.cursor() as cur:
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def upsert_rows(pg: Any, table: str, columns: list[str], rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    rows = [normalize_row(row) for row in rows]
    target_types = target_column_types(pg, table)
    target_cols = set(target_types)
    columns = [column for column in columns if column in target_cols]
    extra_columns = sorted({column for row in rows for column in row if column in target_cols and column not in columns})
    columns.extend(extra_columns)
    if not columns:
        return 0
    normalize_uuid_columns_for_target(table, rows, target_types)
    normalize_bool_columns_for_target(rows, target_types)
    if table in {"audit_log", "user_sessions"} and "user_id" in columns:
        for row in rows:
            row["user_id"] = normalize_uuid_value(row.get("user_id"))
    if table == "audit_log":
        if "entity_name" in target_cols and "entity_name" not in columns:
            columns = [*columns, "entity_name"]
            for row in rows:
                row["entity_name"] = audit_entity_name(row)
        for row in rows:
            row["username"] = safe_text(row.get("username"))
            row["action"] = safe_text(row.get("action"))
            row["module"] = safe_text(row.get("module"))
            row["division"] = normalize_audit_division_value(row.get("division"))
            if "entity_name" in columns:
                row["entity_name"] = safe_text(row.get("entity_name"))
    conflict_cols = [column for column in CONFLICT_COLUMNS.get(table, ["id"]) if column in columns]
    if not conflict_cols:
        conflict_cols = ["id"] if "id" in columns else []
    col_sql = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [col for col in columns if col not in {"id", *conflict_cols}]
    update_sql = ", ".join(f"{col}=excluded.{col}" for col in update_cols)
    if conflict_cols and update_sql:
        conflict_sql = ", ".join(conflict_cols)
        sql = (
            f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
        )
    elif conflict_cols:
        conflict_sql = ", ".join(conflict_cols)
        sql = (
            f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_sql}) DO NOTHING"
        )
    else:
        sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
    values = [tuple(row.get(col) for col in columns) for row in rows]
    with pg.cursor() as cur:
        cur.executemany(sql, values)
    return len(rows)


def reset_identity(pg: Any, table: str) -> None:
    with pg.cursor() as cur:
        cur.execute(
            """
            select pg_get_serial_sequence(%s, 'id')
            """,
            (table,),
        )
        seq = cur.fetchone()[0]
        if not seq:
            return
        cur.execute(f"select coalesce(max(id), 1) from {table}")
        max_id = int(cur.fetchone()[0] or 1)
        cur.execute("select setval(%s, %s, true)", (seq, max_id))


def count_pg(pg: Any, table: str) -> int:
    with pg.cursor() as cur:
        cur.execute(f"select count(*) from {table}")
        return int(cur.fetchone()[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra Planning DDV de SQLite a Supabase PostgreSQL.")
    parser.add_argument("--sqlite", default=str(DEFAULT_SQLITE), help="Ruta a operations_ddv.db")
    parser.add_argument("--dry-run", action="store_true", help="Solo informa conteos de SQLite.")
    parser.add_argument(
        "--include-auth",
        action="store_true",
        help="Incluye users, user_sessions y audit_log. No usar para recuperar datos operativos si Supabase ya tiene usuarios.",
    )
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"No existe SQLite: {sqlite_path}")

    tables = [*OPERATIONAL_TABLES, *(AUTH_TABLES if args.include_auth else [])]
    source_counts: dict[str, int] = {}
    source_payload: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
    for table in tables:
        columns, rows = sqlite_rows(sqlite_path, table)
        source_payload[table] = (columns, rows)
        source_counts[table] = len(rows)

    print("Filas SQLite detectadas:")
    for table, count in source_counts.items():
        print(f"- {table}: {count}")
    route_rows = source_payload.get("planning_routes", ([], []))[1]
    day_keys = sqlite_planning_day_keys(route_rows)
    routes_without_day_key = sum(1 for row in route_rows if not normalize_date_value(row.get("planning_date")))
    print(f"Jornadas SQLite detectadas: {len(day_keys)}")
    print(f"Rutas SQLite sin fecha para resolver jornada: {routes_without_day_key}")
    if not args.include_auth:
        print("Tablas de usuarios/sesiones/auditoría omitidas para preservar usuarios actuales de Supabase.")

    try:
        import psycopg as psycopg_module
    except Exception as exc:  # pragma: no cover
        if args.dry_run:
            print("Validación PostgreSQL omitida: falta instalar psycopg.")
            return
        raise SystemExit("Falta instalar psycopg. Ejecutar: pip install -r requirements.txt") from exc

    db_url = (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        if args.dry_run:
            print("Validación PostgreSQL omitida: falta SUPABASE_DB_URL o DATABASE_URL.")
            return
        raise SystemExit("Falta SUPABASE_DB_URL o DATABASE_URL para conectar PostgreSQL.")
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SECRET_KEY"):
        print("Aviso: SUPABASE_URL o SUPABASE_SECRET_KEY no están configuradas. La migración usa SUPABASE_DB_URL.")

    if args.dry_run:
        with psycopg_module.connect(db_url) as pg:
            report_planning_days_dry_run(pg, source_payload.get("planning_routes", ([], []))[1])
            for table in tables:
                if not target_table_exists(pg, table):
                    print(f"Validación {table}: tabla ausente en Supabase.")
                    continue
                columns, rows = source_payload[table]
                target_types = target_column_types(pg, table)
                report_uuid_compatibility(table, columns, rows, target_types)
                report_bool_compatibility(table, columns, rows, target_types)
                print(f"Validación {table}: destino actual {count_pg(pg, table)} filas.")
        return

    with psycopg_module.connect(db_url) as pg:
        execute_schema(pg)
        day_map = ensure_planning_days(pg, route_rows)
        unresolved = attach_planning_day_ids(route_rows, day_map)
        if unresolved:
            raise RuntimeError(f"No se pudo resolver planning_day_id para {unresolved} rutas.")
        for table in tables:
            if not target_table_exists(pg, table):
                print(f"Omitido {table}: no existe en Supabase.")
                continue
            columns, rows = source_payload[table]
            inserted = upsert_rows(pg, table, columns, rows)
            reset_identity(pg, table)
            print(f"Migrado {table}: {inserted} filas. Total destino: {count_pg(pg, table)}")
        pg.commit()
    print("Migración finalizada correctamente.")


if __name__ == "__main__":
    main()
