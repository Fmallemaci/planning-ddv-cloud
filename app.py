from datetime import date
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Planning DDV", page_icon="🚚", layout="wide")

@st.cache_resource
def get_supabase():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SECRET_KEY"],
    )

supabase = get_supabase()

def login():
    st.title("Planning DDV")
    st.caption("Acceso operativo centralizado")
    with st.form("login"):
        usuario = st.text_input("Usuario")
        clave = st.text_input("Contraseña", type="password")
        entrar = st.form_submit_button("Ingresar", use_container_width=True)

    if entrar:
        if (
            usuario.strip() == st.secrets["APP_USER"]
            and clave == st.secrets["APP_PASSWORD"]
        ):
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")

def cerrar_sesion():
    st.session_state.clear()
    st.rerun()

def app():
    with st.sidebar:
        st.subheader("Planning DDV")
        st.write("Fernando Mallemaci")
        st.caption("Administrador")
        if st.button("Cerrar sesión", use_container_width=True):
            cerrar_sesion()

    st.title("Planning Operativo DDV")
    st.caption("Base central Supabase · versión inicial")

    c1, c2, c3 = st.columns(3)
    c1.metric("Usuario", "Fernando Mallemaci")
    c2.metric("Rol", "Administrador")
    c3.metric("Estado", "Activo")

    st.divider()
    st.subheader("Jornada operativa")

    with st.form("jornada"):
        c1, c2 = st.columns(2)
        fecha = c1.date_input(
            "Fecha operativa",
            value=date.today(),
            format="DD/MM/YYYY",
        )
        base = c2.selectbox(
            "Base",
            ["TRELEW", "PUERTO MADRYN"],
        )
        observaciones = st.text_area("Observaciones generales")
        guardar = st.form_submit_button(
            "Guardar jornada",
            use_container_width=True,
        )

    if guardar:
        try:
            supabase.table("dias_operativos").upsert(
                {
                    "fecha": fecha.isoformat(),
                    "base": base,
                    "estado": "BORRADOR",
                    "observaciones_generales": observaciones.strip() or None,
                },
                on_conflict="fecha,base",
            ).execute()
            st.success("Jornada guardada correctamente.")
        except Exception as exc:
            st.error(f"No se pudo guardar la jornada: {exc}")

    st.divider()
    st.subheader("Últimas jornadas")

    try:
        data = (
            supabase.table("dias_operativos")
            .select("fecha,base,estado,observaciones_generales,actualizado_en")
            .order("fecha", desc=True)
            .limit(20)
            .execute()
            .data
        )

        if data:
            st.dataframe(data, use_container_width=True, hide_index=True)
        else:
            st.info("Todavía no hay jornadas guardadas.")
    except Exception as exc:
        st.error(f"No se pudieron consultar las jornadas: {exc}")

if not st.session_state.get("autenticado", False):
    login()
    st.stop()

app()
