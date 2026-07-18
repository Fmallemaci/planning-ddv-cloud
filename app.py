from __future__ import annotations

from datetime import date

import streamlit as st
from supabase import Client, create_client


st.set_page_config(page_title="Planning DDV", page_icon="🚚", layout="wide")


@st.cache_resource
def get_supabase() -> Client:
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"],
    )


supabase = get_supabase()


def restore_session() -> bool:
    access_token = st.session_state.get("access_token")
    refresh_token = st.session_state.get("refresh_token")

    if not access_token or not refresh_token:
        return False

    try:
        supabase.auth.set_session(access_token, refresh_token)
        return True
    except Exception:
        st.session_state.clear()
        return False


def login_screen() -> None:
    st.title("Planning DDV")
    st.caption("Acceso operativo centralizado")

    with st.form("login_form"):
        email = st.text_input("Usuario", placeholder="correo@empresa.com")
        password = st.text_input("Contraseña", type="password")
        submit = st.form_submit_button("Ingresar", use_container_width=True)

    if submit:
        if not email.strip() or not password:
            st.error("Completá usuario y contraseña.")
            return

        try:
            response = supabase.auth.sign_in_with_password(
                {
                    "email": email.strip().lower(),
                    "password": password,
                }
            )

            if not response.session or not response.user:
                st.error("No se pudo iniciar sesión.")
                return

            st.session_state["access_token"] = response.session.access_token
            st.session_state["refresh_token"] = response.session.refresh_token
            st.session_state["user_id"] = response.user.id
            st.rerun()

        except Exception:
            st.error("Usuario o contraseña incorrectos.")


def get_profile(user_id: str) -> dict | None:
    response = (
        supabase.table("perfiles")
        .select("nombre,email,rol,base_asignada,activo")
        .eq("id", user_id)
        .single()
        .execute()
    )
    return response.data


def logout() -> None:
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    st.session_state.clear()
    st.rerun()


def main_app(profile: dict) -> None:
    with st.sidebar:
        st.subheader("Planning DDV")
        st.write(profile["nombre"])
        st.caption(profile["rol"].replace("_", " ").title())

        if st.button("Cerrar sesión", use_container_width=True):
            logout()

    st.title("Planning Operativo DDV")
    st.caption("Base central Supabase · versión inicial")

    col1, col2, col3 = st.columns(3)
    col1.metric("Usuario", profile["nombre"])
    col2.metric("Rol", profile["rol"].replace("_", " ").title())
    col3.metric("Estado", "Activo" if profile["activo"] else "Inactivo")

    st.divider()
    st.subheader("Jornada operativa")

    with st.form("crear_jornada"):
        col_fecha, col_base = st.columns(2)
        fecha_operativa = col_fecha.date_input(
            "Fecha operativa",
            value=date.today(),
            format="DD/MM/YYYY",
        )
        base = col_base.selectbox("Base", ["TRELEW", "PUERTO MADRYN"])
        observaciones = st.text_area(
            "Observaciones generales",
            placeholder="Información relevante de la jornada",
        )
        guardar = st.form_submit_button(
            "Guardar jornada",
            use_container_width=True,
        )

    if guardar:
        try:
            payload = {
                "fecha": fecha_operativa.isoformat(),
                "base": base,
                "estado": "BORRADOR",
                "observaciones_generales": observaciones.strip() or None,
                "creado_por": st.session_state["user_id"],
                "actualizado_por": st.session_state["user_id"],
            }

            (
                supabase.table("dias_operativos")
                .upsert(payload, on_conflict="fecha,base")
                .execute()
            )
            st.success("Jornada guardada correctamente.")
        except Exception as exc:
            st.error(f"No se pudo guardar la jornada: {exc}")

    st.divider()
    st.subheader("Últimas jornadas")

    try:
        response = (
            supabase.table("dias_operativos")
            .select("fecha,base,estado,observaciones_generales,actualizado_en")
            .order("fecha", desc=True)
            .limit(20)
            .execute()
        )

        rows = response.data or []
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("Todavía no hay jornadas guardadas.")
    except Exception as exc:
        st.error(f"No se pudieron consultar las jornadas: {exc}")


if not restore_session():
    login_screen()
    st.stop()

user_id = st.session_state.get("user_id")

try:
    profile = get_profile(user_id)
except Exception:
    profile = None

if not profile:
    st.error("El usuario existe, pero no tiene un perfil habilitado.")
    if st.button("Cerrar sesión"):
        logout()
    st.stop()

if not profile.get("activo", False):
    st.error("El usuario está desactivado.")
    if st.button("Cerrar sesión"):
        logout()
    st.stop()

main_app(profile)
