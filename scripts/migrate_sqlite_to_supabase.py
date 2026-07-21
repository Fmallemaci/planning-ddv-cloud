from __future__ import annotations

import argparse
import os
import re
import sqlite3
from pathlib import Path
from datetime import date, datetime
from typing import Any

psycopg = None

APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE = APP_DIR / "data" / "operations_ddv.db"
SCHEMA_SQL = APP_DIR / "sql" / "supabase_schema.sql"

TABLES = [
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


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    for column in TEMPORAL_COLUMN_NAMES:
        if column in clean:
            clean[column] = normalize_date_value(clean[column], column in TIMESTAMP_COLUMN_NAMES)
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
    target_cols = target_columns(pg, table)
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
    col_sql = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [col for col in columns if col != "id"]
    update_sql = ", ".join(f"{col}=excluded.{col}" for col in update_cols)
    sql = (
        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {update_sql}"
    )
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
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"No existe SQLite: {sqlite_path}")

    source_counts: dict[str, int] = {}
    source_payload: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
    for table in TABLES:
        columns, rows = sqlite_rows(sqlite_path, table)
        source_payload[table] = (columns, rows)
        source_counts[table] = len(rows)

    print("Filas SQLite detectadas:")
    for table, count in source_counts.items():
        print(f"- {table}: {count}")

    if args.dry_run:
        return

    try:
        import psycopg as psycopg_module
    except Exception as exc:  # pragma: no cover
        raise SystemExit("Falta instalar psycopg. Ejecutar: pip install -r requirements.txt") from exc

    db_url = (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Falta SUPABASE_DB_URL o DATABASE_URL para conectar PostgreSQL.")
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SECRET_KEY"):
        print("Aviso: SUPABASE_URL o SUPABASE_SECRET_KEY no están configuradas. La migración usa SUPABASE_DB_URL.")

    with psycopg_module.connect(db_url) as pg:
        execute_schema(pg)
        for table in TABLES:
            columns, rows = source_payload[table]
            inserted = upsert_rows(pg, table, columns, rows)
            reset_identity(pg, table)
            print(f"Migrado {table}: {inserted} filas. Total destino: {count_pg(pg, table)}")
        pg.commit()
    print("Migración finalizada correctamente.")


if __name__ == "__main__":
    main()
