# Planning DDV Cloud V1

Primera versión compartida de Planning DDV:

- Streamlit
- Supabase central
- Carga TW/PM por fecha
- Planning consolidado
- Guardado compartido
- Usuarios Fernando, Kevin y Néstor
- Empleados compartidos
- Dashboard premium aprobado
- Indicadores de flota y drop size

## Importante

No incluya la Secret Key dentro del repositorio.

## Prueba local

1. Copiar `.streamlit/secrets.example.toml` como `.streamlit/secrets.toml`.
2. Completar URL, Secret Key y contraseñas.
3. Instalar:
   `pip install -r requirements.txt`
4. Ejecutar:
   `streamlit run app.py`

## Publicación

1. Subir esta carpeta a un repositorio privado de GitHub.
2. Crear una app en Streamlit Community Cloud.
3. Main file: `app.py`.
4. En Advanced settings / Secrets, pegar el contenido de `secrets.example.toml` con los valores reales.
5. Deploy.

## Estado de esta entrega

Operativo:
- Login simple
- Supabase
- Importar TW/PM
- Guardar asignaciones
- Confirmar división
- Dashboard consolidado
- Empleados
- Histórico

Pendiente para la siguiente entrega:
- Migración automática de empleados desde la base local
- Mail como lámina renderizada
- PDF ejecutivo
- Novedades y recargas cloud completas
- Auditoría detallada