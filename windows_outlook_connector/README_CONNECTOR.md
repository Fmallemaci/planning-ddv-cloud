# Planning DDV Outlook Bridge

Version: 1.0.0

Este puente abre un borrador en Outlook clasico de escritorio usando el perfil local de Windows. No guarda credenciales y nunca envia correos automaticamente.

## Contenido

- `Instalar Planning DDV Outlook Connector.cmd`
- `Desinstalar Planning DDV Outlook Connector.cmd`
- `Registrar protocolo planningddv.cmd`
- `planning_ddv_outlook_bridge.exe`
- `planning_ddv_outlook_bridge.cs`
- `run_connector.cmd`
- `PRUEBA_APERTURA_BORRADOR.md`
- `SHA256SUMS.txt`

## Instalacion

1. Descargar el ZIP desde Planning DDV.
2. Descomprimir el ZIP en una carpeta local.
3. Revisar los archivos visibles y los hashes en `SHA256SUMS.txt`.
4. Ejecutar `Instalar Planning DDV Outlook Connector.cmd`.
5. Volver a Planning DDV, abrir `Configurar esta PC` y presionar `Probar Outlook`.

## Funcionamiento

1. La web genera un token temporal de un solo uso.
2. El navegador invoca `planningddv://crear-mail?id=...`.
3. Windows abre `planning_ddv_outlook_bridge.exe`.
4. El puente consulta el paquete autorizado en Planning DDV mediante el id temporal.
5. Outlook clasico crea un borrador con destinatarios, asunto, HTML, imagenes embebidas y PDF adjunto.
6. El correo queda abierto para revision del usuario.

El Nuevo Outlook no expone automatizacion COM compatible. Si Outlook clasico no esta instalado o configurado, el puente muestra un error claro y no abre Outlook Web.

## Seguridad

- No descarga instaladores ni ejecutables.
- No usa permisos de administrador.
- No crea tareas programadas.
- No modifica el inicio de Windows.
- No desactiva seguridad ni agrega exclusiones.
- No guarda usuarios, contrasenas ni secretos.
- Registra el protocolo `planningddv://` solo para el usuario actual en `HKCU`.

## Desinstalacion

Ejecutar `Desinstalar Planning DDV Outlook Connector.cmd`. El script borra el registro `planningddv://`, elimina el acceso directo del escritorio y quita la carpeta local del conector.

## Log tecnico

`%LOCALAPPDATA%\PlanningDDVOutlookConnector\connector.log`
