# Planning DDV V5.7 - Supabase en Render

## 1. Crear tablas en Supabase

1. Abrir el proyecto Supabase de Planning DDV.
2. Entrar a SQL Editor.
3. Ejecutar completo el archivo:
   `sql/supabase_schema.sql`

El script crea las tablas operativas, usuarios, sesiones y auditoría si no existen.

## 2. Variables necesarias en Render

En Render, servicio `planning-ddv-v57`, cargar estas variables de entorno:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SUPABASE_DB_URL`
- `PLANNING_ADMIN_USER`
- `PLANNING_ADMIN_PASSWORD`
- `PLANNING_ADMIN_NAME`

Importante: `SUPABASE_DB_URL` es la connection string PostgreSQL directa de Supabase. Es necesaria para transacciones reales. No guardar estos valores en GitHub.

## 3. Migrar datos actuales desde SQLite

Desde una terminal local en la carpeta del proyecto:

```powershell
python -m pip install -r requirements.txt
$env:SUPABASE_URL="..."
$env:SUPABASE_SECRET_KEY="..."
$env:SUPABASE_DB_URL="..."
python scripts/migrate_sqlite_to_supabase.py --sqlite data/operations_ddv.db
```

El migrador es idempotente: si se ejecuta nuevamente, actualiza por `id` y conserva los datos ya migrados.

## 4. Diagnóstico

Con un usuario administrador logueado, consultar:

```text
/api/diagnostics/storage
```

Debe mostrar:

- `backend`: `supabase_postgres`
- `connection`: `OK`
- recuentos por tabla

## 5. Backup descargable

El botón de backup existente descarga:

- ZIP con SQLite si la app está en modo local.
- ZIP con CSV/JSON exportado desde Supabase si Render tiene `SUPABASE_DB_URL`.

## 6. Backup automático diario

GitHub Actions ejecuta `.github/workflows/supabase-backup.yml` diariamente.

Agregar estos secrets en GitHub:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SUPABASE_DB_URL`

El backup queda como artifact del workflow por 30 días.

## 7. Prueba mínima antes de desplegar

1. Ejecutar el SQL en Supabase.
2. Migrar SQLite.
3. Cargar variables en Render.
4. Desplegar.
5. Ingresar con administrador.
6. Revisar `/api/diagnostics/storage`.
7. Validar empleados, localidades, importación TW/PM, borrador, confirmación, novedades, recargas, kilómetros, histórico, salida diaria, WhatsApp, usuarios, roles y auditoría.
8. Reiniciar el servicio en Render y confirmar que la fecha y datos migrados persisten.
