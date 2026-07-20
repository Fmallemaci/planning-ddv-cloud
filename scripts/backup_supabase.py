from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import psycopg
except Exception as exc:  # pragma: no cover
    raise SystemExit("Falta instalar psycopg. Ejecutar: pip install -r requirements.txt") from exc

APP_DIR = Path(__file__).resolve().parents[1]
BACKUP_DIR = APP_DIR / "backups"
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


def rows_for_table(pg: Any, table: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    with pg.cursor() as cur:
        cur.execute(f"select * from {table} order by id")
        rows = cur.fetchall()
        columns = [col.name for col in cur.description or []]
        return columns, rows


def csv_bytes(columns: list[str], rows: list[tuple[Any, ...]]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def json_bytes(columns: list[str], rows: list[tuple[Any, ...]]) -> bytes:
    payload = [dict(zip(columns, row)) for row in rows]
    return json.dumps(payload, ensure_ascii=False, default=str, indent=2).encode("utf-8")


def main() -> None:
    db_url = (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Falta SUPABASE_DB_URL o DATABASE_URL.")
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"planning_ddv_supabase_{stamp}.zip"
    manifest: dict[str, Any] = {"created_utc": stamp, "tables": {}}
    with psycopg.connect(db_url) as pg, zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for table in TABLES:
            columns, rows = rows_for_table(pg, table)
            manifest["tables"][table] = len(rows)
            archive.writestr(f"{table}.csv", csv_bytes(columns, rows))
            archive.writestr(f"{table}.json", json_bytes(columns, rows))
        archive.writestr("manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    print(backup_path)


if __name__ == "__main__":
    main()
