# Prueba de apertura de borrador

La prueba debe hacerse desde la aplicacion web, no desde un token escrito a mano.

1. Instalar el conector con `Instalar Planning DDV Outlook Connector.cmd`.
2. Iniciar sesion en Planning DDV con el usuario de la PC.
3. Abrir `Configurar esta PC`.
4. Presionar `Probar Outlook`.
5. Windows debe invocar `planningddv://crear-mail?id=...`.
6. Outlook clasico debe abrir un borrador de prueba.

Resultado esperado:

- El borrador queda abierto y no se envia automaticamente.
- Usa el perfil local de Outlook de esa computadora.
- Adjunta un PDF de prueba.
- No solicita ni guarda credenciales.

Si la app muestra `Para abrir el borrador en Outlook de escritorio debe instalarse el puente Planning DDV.`, descargar el instalador del puente e instalarlo nuevamente.
