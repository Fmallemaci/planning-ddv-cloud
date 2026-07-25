# Planning DDV Outlook Connector

Version: 1.0.0

Contenido del paquete:

- `Instalar Planning DDV Outlook Connector.cmd`
- `run_connector.cmd`
- `planning_ddv_outlook_connector.ps1`
- `SHA256SUMS.txt`
- `README_CONNECTOR.md`

Instalacion:

1. Descargar el ZIP desde Planning DDV.
2. Descomprimir el ZIP en una carpeta local.
3. Revisar los archivos visibles y los hashes en `SHA256SUMS.txt`.
4. Ejecutar `Instalar Planning DDV Outlook Connector.cmd`.

Que hace el instalador:

- Copia archivos conocidos a `%LOCALAPPDATA%\PlanningDDVOutlookConnector`.
- Registra el protocolo `planningddv://` solo para el usuario actual en `HKCU`.
- Crea el acceso directo `Planning DDV.url` en el escritorio.

Que no hace:

- No descarga archivos.
- No usa permisos de administrador.
- No crea tareas programadas.
- No modifica el inicio de Windows.
- No desactiva seguridad ni agrega exclusiones.
- No guarda contrasenas ni secretos.

Log tecnico:

`%LOCALAPPDATA%\PlanningDDVOutlookConnector\connector.log`
