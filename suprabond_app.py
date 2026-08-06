#!/usr/bin/env python3
"""
Herramientas de MercadoLibre para SUPRABOND.

    streamlit run suprabond_app.py

Seis secciones: precios, precios mayoristas por reglas, stock de ML,
control de stock propio, rentabilidad por SKU y precios de la competencia.

Las que escriben en la cuenta real siguen siempre el mismo flujo:
simular -> revisar -> confirmar -> aplicar. Nunca se aplica nada sin pasar
por la simulacion.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

import actualizador as act
import alertas_stock
import almacen
import buybox
import cambios
import plata as plata_mod
import competencia
import conciliacion
import conversion
import envios
import full
import salud
import espejos
import reclamos as rec
import rentabilidad as rent
import mayoristas
import preguntas as preg
import lista_gsu
import publicidad
import promociones
import promos_planilla
import reporte
import stock_control
import tramos
import tutorial_suprabond
import ventana
from catalogo import (CACHE as CACHE_CATALOGO, actualizado_en as catalogo_al,
                      bajar_catalogo)
from meli import Meli, MeliError

_ASSETS = Path(__file__).resolve().parent / "_assets"
LOGO = _ASSETS / "logo_suprabond.png"          # horizontal, para el encabezado
ICONO = _ASSETS / "icono_suprabond.png"        # cuadrado, para la pestaña

st.set_page_config(page_title="MercadoLibre — SUPRABOND",
                   page_icon=str(ICONO) if ICONO.exists() else "🛒",
                   layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
    [data-testid="stMain"] .stButton > button,
    [data-testid="stMain"] .stDownloadButton > button,
    [data-testid="stMain"] [data-testid="stFormSubmitButton"] > button {
        background-color: #C8552F !important;
        color: #FFFFFF !important;
        border-color: #C8552F !important;
        padding: 0.2rem 0.7rem !important;
        font-size: 0.72rem !important;
        letter-spacing: 0.03em;
    }
    [data-testid="stMain"] .stButton > button:hover,
    [data-testid="stMain"] .stDownloadButton > button:hover,
    [data-testid="stMain"] [data-testid="stFormSubmitButton"] > button:hover {
        background-color: #A8451F !important;
        border-color: #A8451F !important;
        color: #FFFFFF !important;
    }
    [data-testid="stMain"] [data-testid="stMetricValue"] {
        font-size: 1.5rem !important; line-height: 1.1 !important;
    }
    [data-testid="stMain"] [data-testid="stMetricLabel"] {
        font-size: 0.75rem !important;
    }

    /* Preguntas va destacada en naranja, como el boton de Tutorial. Es la
       segunda opcion del selector de seccion: si se reordena la lista de
       arriba, hay que mover el nth-of-type junto con ella. */
    [data-testid="stMain"] [data-testid="stButtonGroup"] button:nth-of-type(3),
    [data-testid="stMain"] [data-testid="stButtonGroup"] button:nth-of-type(3):hover,
    [data-testid="stMain"] [data-testid="stButtonGroup"] button:nth-of-type(3):focus,
    [data-testid="stMain"] [data-testid="stButtonGroup"] > div > button:nth-of-type(3) {
        background-color: #C8552F !important;
        color: #FFFFFF !important;
        border-color: #C8552F !important;
    }
    [data-testid="stMain"] [data-testid="stButtonGroup"] button:nth-of-type(3) * {
        color: #FFFFFF !important;
    }
    </style>
""", unsafe_allow_html=True)


# ===================================================================== login

def autenticado():
    # Sin secrets.toml (uso local) st.secrets revienta, no devuelve vacio.
    try:
        clave = st.secrets.get("suprabond_password")
    except Exception:
        clave = None
    if not clave:
        return True          # sin clave configurada, uso local

    if st.session_state.get("auth_suprabond"):
        return True

    izq, centro, der = st.columns([1, 2, 1])
    with centro:
        if LOGO.exists():
            st.image(str(LOGO), width=280)
        st.markdown("<h3 style='margin:0.5rem 0 0.25rem 0;'>Herramientas de "
                    "MercadoLibre</h3>", unsafe_allow_html=True)
        st.caption("Precios, stock y rentabilidad. Acceso restringido.")
        with st.form("login"):
            ingresada = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Ingresar", use_container_width=True):
                if ingresada == clave:
                    st.session_state["auth_suprabond"] = True
                    st.rerun()
                else:
                    st.error("Contraseña incorrecta.")
    return False


if not autenticado():
    st.stop()


# ===================================================================== datos

@st.cache_resource(show_spinner=False)
def conectar():
    return Meli(verbose=False)


@st.cache_data(ttl=1800, show_spinner="Cargando catálogo de MercadoLibre...")
def cargar_catalogo_cacheado(_ml, sello):
    """`sello` fuerza el refresco cuando el operador aprieta el botón."""
    if CACHE_CATALOGO.exists() and sello == 0:
        return json.loads(CACHE_CATALOGO.read_text(encoding="utf-8"))
    return bajar_catalogo(_ml)


try:
    ml = conectar()
except MeliError as e:
    st.error(f"No hay conexión con MercadoLibre: {e}")

    # Qué ve el proceso realmente. Sin esto, "falta credentials.txt" manda a
    # buscar el problema al lugar equivocado cuando lo que pasa es que los
    # secrets no llegaron o no parsean.
    diag = almacen.diagnostico_secrets()
    esperadas = {"mercadolibre", "gsheets", "gsheets_costos"}
    vistas = set(diag["secciones"])

    with st.expander("Ver qué está encontrando la app", expanded=True):
        st.write(f"**Entorno:** `{diag['entorno']}`")
        if diag["secciones"]:
            st.write("**Secciones de secrets que ve:** "
                     + ", ".join(f"`{s}`" for s in diag["secciones"]))
        else:
            st.write("**No ve ninguna sección de secrets.**")
        if diag["error"]:
            st.write(f"**Error al leerlos:** `{diag['error']}`")

        for aviso in diag.get("avisos", []):
            st.warning(aviso, icon="⚠️")

        faltan = esperadas - vistas
        if not diag["secciones"]:
            st.warning(
                "Los secrets no llegaron. En Streamlit Cloud: **⋮ → Settings → "
                "Secrets**, pegar el bloque completo, **Save**, y después "
                "**Reboot app** (no alcanza con Rerun).", icon="⚠️")
        elif faltan:
            st.warning(
                "Los secrets llegaron pero faltan secciones: "
                + ", ".join(f"`{s}`" for s in sorted(faltan))
                + ". Hay que pegar el bloque **entero**, no solo una parte.",
                icon="⚠️")
        else:
            st.info(
                "Las secciones están todas, así que el problema es el "
                "contenido de `[mercadolibre]`: revisá que `app_id`, "
                "`secret_key` y `redirect_uri` tengan valor.", icon="ℹ️")

    st.caption("Si corrés en tu máquina, alcanza con tener `credentials.txt` "
               "en la carpeta del proyecto (`python autorizar.py` una vez).")
    st.stop()

if "sello_catalogo" not in st.session_state:
    st.session_state["sello_catalogo"] = 0

pubs = cargar_catalogo_cacheado(ml, st.session_state["sello_catalogo"])
activas = [p for p in pubs if p.get("status") == "active"]


# ===================================================================== header

@st.dialog("Tutorial — Herramientas de MercadoLibre", width="large")
def _tutorial_dialog():
    tutorial_suprabond.render()


@st.dialog("Novedades — qué cambió en la app", width="large")
def _cambios_dialog():
    cambios.render()


enc_logo, enc_info, enc_btn = st.columns([1.1, 2, 1.3])
with enc_logo:
    if LOGO.exists():
        st.image(str(LOGO), width=190)
    else:
        st.markdown("### SUPRABOND")
with enc_info:
    st.markdown("##### Herramientas de MercadoLibre")
    st.caption(f"{len(pubs):,} publicaciones · {len(activas):,} activas"
               .replace(",", "."))
    # Son dos fechas distintas y el encabezado mostraba solo la de la app,
    # que es cuando se publicó una versión nueva. Lo que casi siempre se
    # quiere saber es cuándo se bajó el catálogo.
    st.caption(f"Catálogo bajado: **{catalogo_al() or 'todavía no'}**")
    st.caption(f"Versión de la app: {cambios.ultima_actualizacion()}")
with enc_btn:
    bt1, bt2 = st.columns(2)
    if bt1.button("📖 Tutorial", use_container_width=True):
        _tutorial_dialog()
    if bt2.button("🆕 Novedades", use_container_width=True):
        _cambios_dialog()
    if st.button("↻ Actualizar catálogo", use_container_width=True):
        st.session_state["sello_catalogo"] += 1
        st.cache_data.clear()
        st.rerun()

seccion = st.segmented_control(
    "Sección", ["Plata sobre la mesa", "Reporte semanal", "Preguntas",
                "Alertas", "Ganar la venta",
                "Precios", "Mayoristas", "Promos por planilla",
                "Stock ML", "Control de stock",
                "Rentabilidad", "Precio óptimo", "Competencia",
                "Publicidad", "Oportunidades"],
    default="Plata sobre la mesa", label_visibility="collapsed",
    # La key la necesitan las pruebas: sin ella el selector no se puede
    # accionar desde `streamlit.testing` y no hay forma de probar una sección.
    key="seccion_actual")

# En la nube el disco se borra en cada reinicio: si no hay Sheet configurada,
# se perderia el refresh_token (habria que reautorizar a mano) y la auditoria.
if not almacen.hay_sheet():
    st.warning(
        "**Sin Google Sheet configurada.** El token y el registro de auditoría "
        "se guardan en archivos locales. Está bien para uso desde tu máquina, "
        "pero en Streamlit Cloud se borran en cada reinicio: habría que volver "
        "a autorizar a mano y se perdería el historial de cambios.", icon="⚠️")

st.divider()


# ===================================================================== helpers

def pesos(v):
    try:
        return f"${float(v):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def pesos_md(v):
    """
    Igual que `pesos()` pero con el `$` escapado, para textos en markdown.

    Streamlit interpreta lo que va **entre dos `$`** como fórmula LaTeX y lo
    renderiza como matemática. Un texto tan común como "de $1.050 a $999"
    se convierte en un engendro ilegible. Con un solo importe no pasa nada;
    con dos o más en el mismo texto, sí. Usar esta en `st.markdown`,
    `st.error`, `st.warning`, `st.info`, `st.caption` y `st.success`.

    En `st.metric` y en las tablas NO hace falta: ahí no se interpreta
    markdown y se vería el backslash.
    """
    return pesos(v).replace("$", "\\$")


def cumplen(n):
    """'1 publicación cumple' / '3 publicaciones cumplen'."""
    return (f"**1 publicación cumple el criterio.**" if n == 1
            else f"**{n} publicaciones cumplen el criterio.**")


@st.cache_data(ttl=300, show_spinner=False)
def _costos_guardados_cache(sello):
    """`sello` fuerza la relectura cuando el operador sube una planilla nueva."""
    return rent.costos_guardados()


@st.cache_data(ttl=3600, show_spinner=False)
def _pisos_cache(sello):
    """
    El piso de marca por publicación, desde la lista de venta de Contabilium.

    Si no está configurado `[contabilium]` devuelve vacío y todas las
    pantallas siguen andando sin piso, igual que antes. Un problema para
    leer la lista **no puede** dejar sin Buy Box a la sección entera.
    """
    try:
        return lista_gsu.traer_pisos(pubs)
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)[:200]}


def pisos_de_marca(_pubs=None):
    """Los pisos listos para pasarle a las secciones, sin la clave de error."""
    datos = _pisos_cache(st.session_state.get("sello_catalogo", 0))
    return {k: v for k, v in datos.items() if k != "_error"}


@st.cache_data(ttl=3600, show_spinner=False)
def _pisos_sku_cache(sello):
    """Igual que el anterior pero por SKU: lo que consumen Plata y Precios."""
    try:
        return lista_gsu.pisos_por_sku(pubs)
    except Exception:  # noqa: BLE001
        return {}


def pisos_sku_de_marca(_pubs=None):
    return _pisos_sku_cache(st.session_state.get("sello_catalogo", 0))


def aviso_piso_de_marca():
    """Explica el piso una vez, donde se use. Devuelve cuántos lo tienen."""
    datos = _pisos_cache(st.session_state.get("sello_catalogo", 0))
    if datos.get("_error"):
        st.warning(
            f"**No pude leer la lista de precios de Contabilium**, así que "
            f"esta corrida va sin el piso de marca. Detalle: "
            f"{datos['_error']}", icon="⚠️")
        return 0
    if not datos:
        st.info(
            "Sin `[contabilium]` en los secrets no hay piso de marca: la "
            "herramienta trabaja solo con el margen.", icon="ℹ️")
        return 0
    # Se cuenta sobre las activas: `traer_pisos` calcula también las pausadas
    # (para que el piso ya esté si alguna se reactiva), pero decir ese número
    # en pantalla confunde contra lo que el operador ve en la tabla.
    en_activas = sum(1 for p in activas if p["id"] in datos)
    st.caption(
        f"**Piso de marca activo en {en_activas} publicaciones.** Suprabond, "
        f"Bulit y Somerset no se publican por debajo de "
        f"**{lista_gsu.MULTIPLICADOR} veces el precio de lista de "
        f"Contabilium**. Es un piso duro: no lo abre ningún criterio de esta "
        "pantalla.")
    return len(datos)


def bloque_costos(clave):
    """
    Planilla de costos: se guarda una vez y la usan Rentabilidad y Buy Box.

    Antes había que subirla en cada sección y en cada visita, porque el
    `file_uploader` no sobrevive al rerun. Ahora se guarda en la planilla
    (mismo motivo que los tokens: en Streamlit Cloud el disco es efímero) y
    solo hace falta volver a subirla cuando cambian los costos.

    Devuelve el DataFrame de costos, o None si todavía no hay ninguno.
    """
    sello = st.session_state.get("sello_costos", 0)
    guardados, cuando = _costos_guardados_cache(sello)

    if len(guardados):
        c1, c2 = st.columns([3, 1.4])
        c1.success(
            f"Usando la planilla de costos guardada: **{len(guardados)} SKU**"
            + (f", actualizada el {cuando}." if cuando else "."), icon="📄")
        with c2:
            st.write("")
            reemplazar = st.toggle("Subir otra", key=f"rep_{clave}")
    else:
        st.info("Todavía no hay una planilla de costos guardada. Subí una y "
                "queda disponible para Rentabilidad y para Buy Box.", icon="📄")
        reemplazar = True

    if reemplazar:
        archivo = st.file_uploader(
            "Planilla de costos (.xlsx o .csv) — una columna de SKU y una de costo",
            type=["xlsx", "xls", "csv"], key=f"up_{clave}")
        quien = st.text_input("Tu nombre (queda en el registro)",
                              key=f"opc_{clave}")
        if archivo and st.button("Guardar la planilla", key=f"save_{clave}",
                                 disabled=not quien.strip()):
            try:
                nuevos = rent.leer_costos(archivo)
            except Exception as e:
                st.error(f"No pude leer la planilla: {e}")
                return guardados if len(guardados) else None
            ok, detalle = rent.guardar_costos(nuevos, operador=quien.strip())
            if ok:
                st.session_state["sello_costos"] = sello + 1
                st.success(f"Guardados {len(nuevos)} costos. Ya los usan "
                           "Rentabilidad y Buy Box.")
                st.rerun()
            else:
                st.error(f"No pude guardar: {detalle}")
                return nuevos      # al menos sirve para esta corrida

    return guardados if len(guardados) else None


def controles_otros_conceptos(clave):
    """
    Los tres costos de estructura que no cobra ML pero hay que cargarle igual
    a cada venta. Se usan los mismos en Rentabilidad y en Buy Box a propósito:
    si no coincidieran, Buy Box aprobaría bajas de precio que Rentabilidad
    marca como pérdida.
    """
    st.caption(
        "**Otros conceptos** — costos que no cobra MercadoLibre pero igual "
        "hay que cargarle a cada venta. Se aplican como porcentaje del "
        f"**ingreso sin IVA**. El logístico es el porcentaje **o "
        f"{pesos_md(rent.TOPE_LOGISTICO)}, lo que sea menor**.")
    # En puntos porcentuales enteros: mostrar "0.10" se lee como 0,1%.
    o1, o2, o3 = st.columns(3)
    return {
        "impuestos": o1.number_input(
            "Impuestos %", 0, 100,
            int(rent.OTROS_CONCEPTOS["impuestos"] * 100), 1,
            key=f"imp_{clave}") / 100,
        "logistico": o2.number_input(
            "Logístico %", 0, 100,
            int(rent.OTROS_CONCEPTOS["logistico"] * 100), 1,
            key=f"log_{clave}") / 100,
        "general": o3.number_input(
            "General %", 0, 100,
            int(rent.OTROS_CONCEPTOS["general"] * 100), 1,
            key=f"gen_{clave}") / 100,
    }


@st.cache_data(ttl=3600, show_spinner=False)
def cargos_cacheados(_ml, dias=90):
    """
    Cargos reales por SKU (comisión y envío medidos de las ventas).

    Lo usan Buy Box y Promociones para saber qué queda por unidad a un precio
    más bajo. Se cachea una hora porque trae el histórico y muestrea envíos.
    """
    ordenes = rent.traer_historico(_ml, dias)
    envios = rent.traer_costos_envio(_ml, ordenes, muestra_por_sku=5)
    return rent.cargos_por_sku(ordenes, envios)


def bloque_carga(operacion):
    """
    UI comun de precios y stock: subir planilla, simular, revisar, aplicar.
    `operacion` es 'precio' o 'stock'.
    """
    etiqueta = "precios" if operacion == "precio" else "stock"
    k = f"sim_{operacion}"          # la simulacion vive en session_state para
    kr = f"res_{operacion}"         # que no se pierda al tocar otro widget

    st.markdown(f"#### Actualización masiva de {etiqueta}")
    st.caption(
        "Subí una planilla con una columna de **SKU** (o el código **MLU**) y otra "
        f"con el **{'precio' if operacion == 'precio' else 'stock'}** nuevo. "
        "Los SKU que no estén en la planilla no se tocan.")

    archivo = st.file_uploader("Planilla (.xlsx o .csv)", type=["xlsx", "xls", "csv"],
                               key=f"up_{operacion}")
    if not archivo:
        st.session_state.pop(k, None)
        st.session_state.pop(kr, None)
        return

    try:
        df = act.leer_planilla(archivo)
    except Exception as e:
        st.error(f"No pude leer la planilla: {e}")
        return

    col_clave_auto, col_valor_auto = act.detectar_columnas(df, operacion)
    cols = list(df.columns)

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        col_clave = st.selectbox(
            "Columna de SKU / MLU", cols,
            index=cols.index(col_clave_auto) if col_clave_auto in cols else 0,
            key=f"ck_{operacion}")
    with c2:
        col_valor = st.selectbox(
            f"Columna de {'precio' if operacion == 'precio' else 'stock'}", cols,
            index=cols.index(col_valor_auto) if col_valor_auto in cols else 0,
            key=f"cv_{operacion}")
    with c3:
        st.metric("Filas", f"{len(df):,}".replace(",", "."))

    with st.expander("Ver la planilla como la leí"):
        st.dataframe(df.head(50), use_container_width=True)

    if st.button(f"Simular cambios de {etiqueta}", key=f"sim_btn_{operacion}"):
        try:
            st.session_state[k] = act.simular(df, pubs, operacion,
                                              col_clave, col_valor)
            st.session_state.pop(kr, None)
        except Exception as e:
            st.error(f"Error al simular: {e}")

    sim = st.session_state.get(k)
    if sim is None:
        return

    if sim.empty:
        st.warning("La simulación no encontró ninguna fila utilizable.")
        return

    # ------------------------------------------------ resultado de la simulacion
    res = act.resumen(sim)
    st.markdown("##### Qué va a pasar")

    m1, m2, m3 = st.columns(3)
    m1.metric("Se actualizan", res.get("actualizar", 0))
    m2.metric("Para revisar", res.get("revisar", 0))
    m3.metric("Sin cambio", res.get("sin_cambio", 0))

    problemas = {kk: v for kk, v in res.items()
                 if kk not in ("actualizar", "revisar", "sin_cambio")}
    if problemas:
        p1, p2, p3 = st.columns(3)
        for col, (nombre, cant) in zip([p1, p2, p3] * 3, problemas.items()):
            col.metric(nombre.replace("_", " ").capitalize(), cant)

    if res.get("revisar"):
        st.warning(
            f"**{res['revisar']} publicaciones tienen un cambio grande** "
            f"(más de {act.UMBRAL_ALERTA_PRECIO:.0%} de variación). "
            "Revisalas antes de incluirlas: suele ser un error de carga.")

    ambiguos = sim[sim["accion"] == "ambiguo"]
    if len(ambiguos):
        st.error(
            f"**{len(ambiguos)} SKU tienen el stock repartido en varios productos "
            "de MercadoLibre.** Poner el mismo número en cada uno duplicaría el "
            "stock, así que quedan sin tocar. Hay que definir a cuál corresponde.")

    filtro = st.multiselect("Filtrar por acción", sorted(sim["accion"].unique()),
                            default=sorted(sim["accion"].unique()),
                            key=f"f_{operacion}")
    vista = sim[sim["accion"].isin(filtro)]

    st.dataframe(
        vista, use_container_width=True, height=340,
        column_config={
            "clave": "SKU / MLU",
            "item_id": "Publicación",
            "titulo": "Título",
            "tipo": "Tipo",
            "logistica": "Logística",
            "valor_actual": st.column_config.NumberColumn(
                "Actual", format="%.0f"),
            "valor_nuevo": st.column_config.NumberColumn(
                "Nuevo", format="%.0f"),
            "variacion": st.column_config.NumberColumn(
                "Variación", format="percent"),
            "accion": "Acción",
            "motivo": "Motivo",
        })

    st.download_button(
        "Descargar la simulación", vista.to_csv(index=False).encode("utf-8"),
        f"simulacion_{operacion}_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv",
        key=f"dl_{operacion}")

    # ------------------------------------------------ aplicar
    st.divider()
    st.markdown("##### Aplicar en MercadoLibre")

    a1, a2 = st.columns([2, 3])
    with a1:
        operador = st.text_input("Tu nombre o iniciales (queda en el registro)",
                                 key=f"op_{operacion}")
    with a2:
        incluir = st.checkbox(
            f"Incluir también las {res.get('revisar', 0)} marcadas para revisar",
            key=f"inc_{operacion}", disabled=not res.get("revisar"))

    a_aplicar = res.get("actualizar", 0) + (res.get("revisar", 0) if incluir else 0)

    if a_aplicar == 0:
        st.info("No hay nada para aplicar.")
        return

    st.warning(f"Se van a modificar **{a_aplicar} publicaciones** en la cuenta real.")
    confirmo = st.checkbox(f"Confirmo que quiero cambiar el {etiqueta} de esas "
                           f"{a_aplicar} publicaciones", key=f"conf_{operacion}")

    if st.button(f"Aplicar {etiqueta}", key=f"go_{operacion}",
                 disabled=not (confirmo and operador.strip())):
        barra = st.progress(0.0, text="Aplicando...")
        def avance(i, total, fila):
            barra.progress(i / total, text=f"Aplicando {i} de {total}...")

        with st.spinner("Escribiendo en MercadoLibre..."):
            st.session_state[kr] = act.aplicar(
                ml, sim, operacion, operador=operador.strip(),
                incluir_revisar=incluir, callback=avance)
        barra.empty()

    resultados = st.session_state.get(kr)
    if resultados is not None and len(resultados):
        ok = (resultados["resultado"] == "OK").sum()
        err = len(resultados) - ok
        if err == 0:
            st.success(f"Listo: {ok} publicaciones actualizadas.")
        else:
            st.error(f"{ok} actualizadas, {err} con error. El detalle está abajo.")
        st.dataframe(resultados, use_container_width=True, height=280)
        st.download_button(
            "Descargar el resultado",
            resultados.to_csv(index=False).encode("utf-8"),
            f"resultado_{operacion}_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv",
            key=f"dlr_{operacion}")
        st.caption(f"Todos los cambios quedaron registrados en {almacen.describir()}.")


# ===================================================================== secciones

if seccion == "Plata sobre la mesa":
    st.markdown("#### Todo lo accionable, ordenado por plata")
    st.caption(
        "La información ya estaba repartida en seis secciones. Acá está junta: "
        "cada fila dice cuánta plata es, qué hay que hacer y dónde se hace.")

    costos_pl = bloque_costos("pl")
    otros_pl = controles_otros_conceptos("pl")

    g1, g2 = st.columns([1.2, 3])
    con_promos = g1.checkbox("Incluir promociones", value=False,
                             help="Suma unos minutos: consulta las ofertas "
                                  "publicación por publicación.")
    g2.write("")
    if costos_pl is not None and g2.button("Buscar la plata",
                                           use_container_width=True):
        estado = st.empty()
        with st.spinner("Cruzando stock, márgenes y Buy Box..."):
            cargos_pl = cargos_cacheados(ml)
            ordenes_pl = rent.traer_historico(ml, 90)

            estado.caption("Revisando el stock...")
            stock_pl = alertas_stock.analizar(ml, dias=90, pubs=pubs,
                                              ordenes=ordenes_pl)
            estado.caption("Calculando márgenes...")
            rent_pl = rent.calcular(costos_pl, cargos_pl, pubs, iva=0.22,
                                    otros_conceptos=otros_pl)
            estado.caption("Consultando el Buy Box...")
            cat_pl = [p["id"] for p in pubs if p.get("status") == "active"
                      and p.get("catalog_listing")]
            ptw_pl = buybox.traer_price_to_win(
                ml, cat_pl, callback=lambda m: estado.caption(str(m)))
            ven_pl = ventana.analizar(costos_pl, cargos_pl, pubs, iva=0.22,
                                      otros_conceptos=otros_pl, objetivo=0.0,
                                      ptw_por_item=ptw_pl)
            promos_pl = None
            if con_promos:
                estado.caption("Buscando promociones...")
                unid_pl = dict(zip(cargos_pl["sku"],
                                   cargos_pl["unidades_vendidas"]))
                promos_pl, _ = promociones.analizar(
                    ml, pubs=pubs, tope=150, cargos=cargos_pl,
                    unidades=unid_pl,
                    callback=lambda m: estado.caption(str(m)))

            st.session_state["plata"] = plata_mod.juntar(
                stock=stock_pl, ventana=ven_pl, rentabilidad=rent_pl,
                promos=promos_pl)
        estado.empty()

    dpl = st.session_state.get("plata")
    if dpl is not None and len(dpl):
        rpl = plata_mod.resumen(dpl)

        h1, h2 = st.columns(2)
        h1.metric("Facturación parada", pesos(rpl["facturacion_parada"]) + "/mes",
                  help="Lo que hoy NO entra porque el producto no se puede "
                       "vender")
        h2.metric("Margen en juego", pesos(rpl["margen_en_juego"]) + "/mes",
                  help="Lo que se pierde o se deja de ganar vendiendo")
        st.caption(
            "**Los dos números no se suman**: uno es facturación que no entra "
            "y el otro es margen que se pierde. Sumarlos daría un número "
            "grande y sin sentido.")

        if rpl["conflictos"]:
            n_conf = rpl["conflictos"]
            st.error(
                (f"**{n_conf} producto está para reponer pero pierde plata "
                 "en cada unidad.**" if n_conf == 1 else
                 f"**{n_conf} productos están para reponer pero pierden "
                 "plata en cada unidad.**")
                + " Reponerlos aumenta la pérdida: primero hay que arreglar "
                  "el precio o el costo. Están marcados en la lista.",
                icon="⚠️")

        st.markdown("##### Por acción")
        st.dataframe(
            pd.DataFrame([
                {"Acción": k, "Casos": v["count"],
                 "Plata por mes": v["sum"], "Mide": v["unidad"]}
                for k, v in sorted(rpl["por_accion"].items(),
                                   key=lambda x: -x[1]["sum"])]),
            use_container_width=True, hide_index=True,
            column_config={"Plata por mes": st.column_config.NumberColumn(
                "Plata por mes", format="%.0f")})

        acciones = sorted(dpl["accion_nombre"].unique())
        filtro_pl = st.multiselect("Filtrar por acción", acciones,
                                   default=acciones, key="f_pl")
        vpl = dpl[dpl["accion_nombre"].isin(filtro_pl)] if filtro_pl else dpl

        st.dataframe(
            vpl[["accion_nombre", "sku", "titulo", "detalle", "plata_mes",
                 "unidad", "seccion", "base"]],
            use_container_width=True, height=440, hide_index=True,
            column_config={
                "accion_nombre": "Qué hacer", "sku": "SKU",
                "titulo": "Título", "detalle": "Detalle",
                "plata_mes": st.column_config.NumberColumn(
                    "Plata/mes", format="%.0f"),
                "unidad": "Mide", "seccion": "Dónde se hace",
                "base": st.column_config.TextColumn(
                    "Sobre qué base", help="De dónde sale el número")})

        st.download_button(
            "Descargar la lista",
            vpl.to_csv(index=False).encode("utf-8"),
            f"plata_{datetime.now():%Y%m%d}.csv", "text/csv")

        st.caption(
            "Las estimaciones asumen **el mismo volumen** que el período "
            "medido. Cambiar un precio cambia el volumen, así que son "
            "referencias de tamaño para priorizar, no proyecciones.")

        # ------------------------------------------- ejecutar desde acá
        #
        # Solo las que se resuelven cambiando un precio. Reponer stock se hace
        # comprando mercadería y tomar una promo, desde el panel de ML: esas
        # se muestran pero no se pueden ejecutar.
        ejec = plata_mod.ejecutables(dpl, pisos_sku=pisos_sku_de_marca(pubs))
        if len(ejec):
            st.divider()
            st.markdown("##### Aplicar los cambios de precio")
            aviso_piso_de_marca()

            frenadas_pl = len(plata_mod.ejecutables(dpl)) - len(ejec)
            if frenadas_pl:
                st.warning(
                    f"**{frenadas_pl} quedan afuera por el piso de marca.** El "
                    "precio que resolvería la pérdida está por debajo de lo "
                    "que se puede publicar.", icon="🚧")

            st.caption(
                f"De las {len(dpl)} oportunidades, **{len(ejec)} se resuelven "
                f"cambiando un precio** y se pueden aplicar desde acá. "
                "Reponer stock y tomar promociones no: esas se hacen "
                "comprando mercadería y desde el panel de MercadoLibre.")

            st.dataframe(
                ejec[["sku", "accion_nombre", "detalle", "precio_actual",
                      "precio_sugerido", "cambio_pct", "plata_mes"]],
                use_container_width=True, hide_index=True, height=260,
                column_config={
                    "sku": "SKU", "accion_nombre": "Qué hacer",
                    "detalle": "Detalle",
                    "precio_actual": st.column_config.NumberColumn(
                        "Precio hoy", format="%.0f"),
                    "precio_sugerido": st.column_config.NumberColumn(
                        "Precio nuevo", format="%.0f"),
                    "cambio_pct": st.column_config.NumberColumn(
                        "Cambia", format="percent"),
                    "plata_mes": st.column_config.NumberColumn(
                        "Plata por mes", format="%.0f")})

            st.caption(
                "Se aplica con el mismo motor que la sección **Precios**: "
                "resuelve a qué publicaciones va cada SKU, marca las "
                "variaciones grandes y deja el precio anterior en la "
                "auditoría. Primero se simula.")

            if st.button("Simular estos cambios", key="sim_pl"):
                try:
                    st.session_state["sim_plata"] = act.simular(
                        plata_mod.planilla_de_precios(ejec), pubs, "precio",
                        "sku", "precio")
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error al simular: {e}")

            sim_pl = st.session_state.get("sim_plata")
            if sim_pl is not None and len(sim_pl):
                res_pl = act.resumen(sim_pl)
                s1, s2, s3 = st.columns(3)
                s1.metric("Se actualizan", res_pl.get("actualizar", 0))
                s2.metric("Para revisar", res_pl.get("revisar", 0))
                s3.metric("Sin cambio", res_pl.get("sin_cambio", 0))

                st.dataframe(sim_pl, use_container_width=True, height=260,
                             hide_index=True)

                a_aplicar_pl = res_pl.get("actualizar", 0)
                if not a_aplicar_pl:
                    st.info("No hay nada para aplicar.")
                else:
                    st.error(
                        f"**Esto cambia el precio de {a_aplicar_pl} "
                        "publicaciones en MercadoLibre de verdad.**",
                        icon="⚠️")
                    pl1, pl2 = st.columns([2, 3])
                    op_pl = pl1.text_input(
                        "Tu nombre (queda en el registro)", key="op_pl")
                    conf_pl = pl2.checkbox(
                        f"Confirmo que quiero cambiar {a_aplicar_pl} precios",
                        key="conf_pl")
                    if st.button("Aplicar los precios", key="go_pl",
                                 disabled=not (conf_pl and op_pl.strip())):
                        barra_pl = st.progress(0.0, text="Aplicando...")
                        st.session_state["res_plata"] = act.aplicar(
                            ml, sim_pl, "precio", operador=op_pl.strip(),
                            callback=lambda i, t, f: barra_pl.progress(
                                i / t, text=f"Aplicando {i} de {t}..."))
                        barra_pl.empty()
                        st.session_state.pop("plata", None)
                        st.session_state.pop("sim_plata", None)

            res_pl_final = st.session_state.get("res_plata")
            if res_pl_final is not None and len(res_pl_final):
                ok_pl = int((res_pl_final["resultado"] == "OK").sum())
                if ok_pl == len(res_pl_final):
                    st.success(f"{ok_pl} precios actualizados.")
                else:
                    st.error(f"{ok_pl} aplicados, "
                             f"{len(res_pl_final) - ok_pl} con error.")
                st.dataframe(res_pl_final, use_container_width=True,
                             hide_index=True)
                st.caption("Volvé a buscar oportunidades para ver el estado "
                           "nuevo.")

elif seccion == "Reporte semanal":
    st.markdown("#### Cómo vino la semana")
    st.caption(
        "Una pantalla para el lunes: qué pasó, contra qué se compara y qué hay "
        "que resolver. El resto de las secciones hay que acordarse de abrirlas; "
        "esto se lee en dos minutos.")

    r1, r2, r3 = st.columns([1.6, 1.4, 1.4])
    periodo = r1.selectbox(
        "Período", ["Semana cerrada (lunes a domingo)", "Últimos 14 días",
                    "Últimos 30 días"],
        help="La semana cerrada se compara contra la anterior completa. "
             "Comparar una semana a medias contra una entera siempre da que "
             "las ventas se derrumbaron.")
    dias_rep = {"Semana cerrada (lunes a domingo)": None,
                "Últimos 14 días": 14, "Últimos 30 días": 30}[periodo]
    con_rec = r2.checkbox("Incluir reclamos", value=True,
                          help="Identificar el producto de cada reclamo cuesta "
                               "una llamada por envío: suma unos segundos.")
    r3.write("")
    if r3.button("Generar reporte", use_container_width=True):
        estado = st.empty()
        with st.spinner("Armando el reporte..."):
            st.session_state["reporte"] = reporte.generar(
                ml, dias_rep, pubs=pubs, con_reclamos=con_rec,
                callback=lambda m: estado.caption(str(m)))
        estado.empty()

    rep = st.session_state.get("reporte")
    if rep is not None:
        v = rep["ventas"]
        st.caption(
            f"**{v['desde']:%d/%m/%Y}** a **{v['hasta']:%d/%m/%Y}** · "
            f"comparado contra {v['desde_previa']:%d/%m} a "
            f"{v['hasta_previa']:%d/%m}")

        def delta(campo):
            x = v.get(f"var_{campo}")
            return None if x is None else f"{x:+.1%}"

        a1, a2, a3 = st.columns(3)
        a1.metric("Facturación", pesos(v["bruto"]), delta("bruto"))
        a2.metric("Neto post-comisión", pesos(v["neto"]), delta("neto"))
        a3.metric("Comisiones ML", pesos(v["comisiones"]), delta("comisiones"),
                  delta_color="inverse")

        b1, b2, b3 = st.columns(3)
        b1.metric("Órdenes", f"{v['ordenes']:,}".replace(",", "."),
                  delta("ordenes"))
        b2.metric("Unidades", f"{v['unidades']:,}".replace(",", "."),
                  delta("unidades"))
        b3.metric("Ticket promedio", pesos(v["ticket"]), delta("ticket"))
        st.caption(f"La comisión se llevó el **{v['comision_pct']:.1%}** de la "
                   "facturación del período.")

        # ------------------------------------------------------ a resolver
        st.divider()
        st.markdown("##### Para resolver esta semana")

        sr = rep["stock_resumen"]
        urg = rep["stock_urgentes"]
        if sr and (sr["sin_publicacion"] or sr["criticos"] or sr["sin_stock"]):
            st.error(
                f"**{sr['sin_publicacion'] + sr['sin_stock'] + sr['criticos']} "
                f"productos con problema de stock** — "
                f"{pesos_md(sr['plata_en_riesgo'])} de facturación semanal en "
                f"riesgo. {sr['sin_publicacion']} vendieron y hoy no tienen "
                f"ninguna publicación activa.", icon="📦")
            st.dataframe(
                urg[["sku", "titulo", "diagnostico", "stock", "dias_cobertura",
                     "plata_semanal_en_riesgo"]].head(15),
                use_container_width=True, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "diagnostico": "Problema", "stock": "Stock",
                    "dias_cobertura": st.column_config.NumberColumn(
                        "Días", format="%.0f"),
                    "plata_semanal_en_riesgo": st.column_config.NumberColumn(
                        "Facturación/semana", format="%.0f")})
        elif sr:
            st.success("Ningún producto en riesgo de quedarse sin stock. 👌")

        c1, c2 = st.columns(2)
        if rep["preguntas_sin_responder"] is not None:
            n = rep["preguntas_sin_responder"]
            c1.metric("Preguntas sin responder", n)
        rr = rep["reclamos_resumen"]
        if rr:
            c2.metric("Reclamos del período", rr["reclamos"],
                      f"{rr['abiertos']} abiertos", delta_color="off")
            rd = rep["reclamos"]
            graves = (rd[rd["diagnostico"] == "tasa alta"]
                      if len(rd) else pd.DataFrame())
            if len(graves):
                st.warning(
                    f"**{len(graves)} productos con tasa de reclamo alta** "
                    f"(la tasa de la cuenta es {rr['tasa_cuenta']:.2%}).",
                    icon="⚠️")
                st.dataframe(
                    graves[["sku", "titulo", "reclamos", "unidades_vendidas",
                            "tasa", "motivo_principal"]].head(10),
                    use_container_width=True, hide_index=True,
                    column_config={
                        "sku": "SKU", "titulo": "Título",
                        "reclamos": "Reclamos",
                        "unidades_vendidas": "Unidades",
                        "tasa": st.column_config.NumberColumn(
                            "Tasa", format="percent"),
                        "motivo_principal": "Motivo más frecuente"})

        # ------------------------------------------------------ que se movio
        st.divider()
        st.markdown("##### Lo que más facturó")
        top = rep["top"]
        if len(top):
            st.dataframe(
                top, use_container_width=True, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título", "unidades": "Unidades",
                    "facturacion": st.column_config.NumberColumn(
                        "Facturación", format="%.0f"),
                    "var": st.column_config.NumberColumn(
                        "vs período anterior", format="percent",
                        help="Vacío = no vendió en el período anterior")})

        caidas = rep["caidas"]
        if len(caidas):
            st.markdown("##### Vendían y este período no vendieron nada")
            st.caption(
                f"Productos con {reporte.MINIMO_CAIDA} o más unidades en el "
                "período anterior y cero en este. Puede ser estacionalidad, "
                "pero también una publicación pausada o sin stock.")
            st.dataframe(
                caidas, use_container_width=True, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "unidades_previas": "Unidades antes",
                    "facturacion_previa": st.column_config.NumberColumn(
                        "Facturaba", format="%.0f")})

        st.download_button(
            "Descargar el detalle de stock",
            rep["stock"].to_csv(index=False).encode("utf-8"),
            f"reporte_stock_{datetime.now():%Y%m%d}.csv", "text/csv")

elif seccion == "Alertas":
    st.markdown("#### Lo que necesita atención")
    al = st.radio("Vista", ["Stock crítico", "Reclamos"],
                  horizontal=True, label_visibility="collapsed")

    if al == "Stock crítico":
        st.caption(
            "La pregunta no es cuánto stock hay sino **cuántos días queda**. "
            "40 unidades de algo que vende 1 por semana están bien; 40 de algo "
            "que vende 10 por día se agotan el jueves.")
        st.caption(
            "Ordenado por **plata en riesgo**: lo que ese producto deja de "
            "facturar por cada semana sin stock.")

        s1, s2 = st.columns([1.2, 3])
        # 90 dias por defecto igual que el resto de la app: el historico de
        # ordenes se cachea por ventana, asi que elegir otra obliga a bajarlo
        # entero de nuevo (son varios minutos).
        dias_st = s1.selectbox("Velocidad medida sobre", [30, 60, 90], index=2,
                               format_func=lambda d: f"{d} días", key="d_stk")
        if s2.button("Revisar el stock", use_container_width=True):
            estado = st.empty()
            with st.spinner("Calculando cobertura..."):
                st.session_state["alertas_stock"] = alertas_stock.analizar(
                    ml, dias_st, pubs=pubs,
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        dfs = st.session_state.get("alertas_stock")
        if dfs is not None and len(dfs):
            res = alertas_stock.resumen(dfs)
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Sin publicación activa", res["sin_publicacion"])
            k2.metric("Sin stock", res["sin_stock"])
            k3.metric("Críticos", res["criticos"])
            k4.metric("Bajos", res["bajos"])
            st.metric("Facturación semanal en riesgo",
                      pesos(res["plata_en_riesgo"]))

            with st.expander("Cómo se calcula y qué significa cada estado"):
                st.markdown(
                    f"- **Sin publicación activa**: el SKU vendió en el "
                    f"período pero hoy no tiene ninguna publicación activa. "
                    f"MercadoLibre **pausa sola** la publicación al llegar a "
                    f"cero, así que este es el caso típico del producto que se "
                    f"agotó y nadie repuso.\n"
                    f"- **Sin stock**: tiene publicación activa pero cero "
                    f"unidades.\n"
                    f"- **Crítico**: menos de {alertas_stock.DIAS_CRITICO} "
                    f"días de cobertura ({alertas_stock.DIAS_CRITICO_FULL} si "
                    f"está en Full, porque reponer allá tarda más).\n"
                    f"- **Bajo**: menos de {alertas_stock.DIAS_BAJO} días "
                    f"({alertas_stock.DIAS_BAJO_FULL} en Full).\n"
                    f"- **Sobrestock**: más de "
                    f"{alertas_stock.DIAS_SOBRESTOCK} días. No es urgente, "
                    f"es plata dormida.\n"
                    f"- **Pocas ventas / sin ventas**: menos de "
                    f"{alertas_stock.MINIMO_UNIDADES} unidades en el período. "
                    f"La velocidad no alcanza para proyectar nada.\n\n"
                    "El stock se agrupa por `user_product_id`: las "
                    "publicaciones espejo comparten unidades y sumarlas todas "
                    "contaría lo mismo varias veces.")

            estados = sorted(dfs["diagnostico"].unique())
            por_defecto = [e for e in estados if e in alertas_stock.URGENTES
                           or e == "bajo"]
            filtro_st = st.multiselect("Filtrar por estado", estados,
                                       default=por_defecto or estados)
            vst = dfs[dfs["diagnostico"].isin(filtro_st)] if filtro_st else dfs

            st.dataframe(
                vst, use_container_width=True, height=420, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título", "stock": "Stock",
                    "stock_propio": "Propio", "stock_full": "Full",
                    "unidades_periodo": "Vendidas",
                    "por_dia": st.column_config.NumberColumn(
                        "Por día", format="%.2f"),
                    "dias_cobertura": st.column_config.NumberColumn(
                        "Días de stock", format="%.0f"),
                    "precio": st.column_config.NumberColumn(
                        "Precio", format="%.0f"),
                    "plata_semanal_en_riesgo": st.column_config.NumberColumn(
                        "Facturación/semana", format="%.0f"),
                    "publicaciones": "Pub.", "en_full": "En Full",
                    "diagnostico": "Estado"})
            st.download_button("Descargar el análisis",
                               vst.to_csv(index=False).encode("utf-8"),
                               f"stock_critico_{datetime.now():%Y%m%d}.csv",
                               "text/csv")

    else:
        st.caption(
            "Qué productos concentran los reclamos. Lo que importa no es el "
            "total sino la **tasa**: un SKU que reclama el 8% de sus ventas "
            "cuando la cuenta promedia 2,8% tiene un problema de producto, de "
            "ficha o de embalaje.")

        q1, q2 = st.columns([1.2, 3])
        dias_rec = q1.selectbox("Período", [30, 60, 90], index=2,
                                format_func=lambda d: f"{d} días", key="d_rec")
        if q2.button("Analizar reclamos", use_container_width=True):
            estado = st.empty()
            with st.spinner("Trayendo reclamos e identificando productos..."):
                st.session_state["reclamos"] = rec.analizar(
                    ml, dias_rec, pubs=pubs,
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        guardado = st.session_state.get("reclamos")
        if guardado is not None:
            dfr, resr = guardado
            n1, n2, n3, n4 = st.columns(4)
            n1.metric("Reclamos", resr["reclamos"])
            n2.metric("Abiertos hoy", resr["abiertos"])
            n3.metric("Tasa de la cuenta", f"{resr['tasa_cuenta']:.2%}")
            n4.metric("Sin producto identificado", resr["sin_producto"])

            if resr["sin_producto"]:
                st.caption(
                    "Los reclamos que apuntan a un pago (no a un pedido ni a "
                    "un envío) no se pueden asociar al producto: la API no "
                    "expone ese camino. Quedan contados aparte.")

            m1, m2 = st.columns(2)
            with m1:
                st.markdown("**Por tipo**")
                st.dataframe(
                    pd.DataFrame(resr["por_tipo"].most_common(),
                                 columns=["Tipo", "Reclamos"]),
                    use_container_width=True, hide_index=True, height=200)
            with m2:
                st.markdown("**Motivos más frecuentes**")
                st.dataframe(
                    pd.DataFrame(resr["por_motivo"].most_common(8),
                                 columns=["Motivo", "Reclamos"]),
                    use_container_width=True, hide_index=True, height=200)

            if len(dfr):
                graves = dfr[dfr["diagnostico"] == "tasa alta"]
                if len(graves):
                    st.warning(
                        f"**{len(graves)} productos con tasa de reclamo por "
                        f"encima del {rec.TASA_ALTA:.0%}** sobre "
                        f"{rec.MINIMO_UNIDADES}+ ventas.", icon="⚠️")

                solo_conf = st.checkbox(
                    "Ver solo los que tienen ventas suficientes", value=True,
                    help=f"Con menos de {rec.MINIMO_UNIDADES} unidades "
                         "vendidas la tasa no significa nada: un reclamo "
                         "sobre 3 ventas da 33%.")
                vr = dfr[dfr["confiable"]] if solo_conf else dfr

                st.dataframe(
                    vr, use_container_width=True, height=420, hide_index=True,
                    column_config={
                        "sku": "SKU", "titulo": "Título",
                        "reclamos": "Reclamos", "abiertos": "Abiertos",
                        "unidades_vendidas": "Unidades",
                        "tasa": st.column_config.NumberColumn(
                            "Tasa", format="percent"),
                        "tipo_principal": "Tipo",
                        "motivo_principal": "Motivo más frecuente",
                        "ultimo_reclamo": "Último",
                        "diagnostico": "Diagnóstico", "confiable": None})
                st.download_button("Descargar el análisis",
                                   vr.to_csv(index=False).encode("utf-8"),
                                   f"reclamos_{datetime.now():%Y%m%d}.csv",
                                   "text/csv")

elif seccion == "Ganar la venta":
    st.markdown("#### Ganar la venta")
    gv = st.radio("Vista", ["Buy Box", "Promociones"],
                  horizontal=True, label_visibility="collapsed")

    if gv == "Buy Box":
        st.caption(
            "**216 de tus publicaciones activas compiten en una página de "
            "catálogo.** En esas páginas todos los vendedores comparten la "
            "misma publicación y MercadoLibre muestra a uno solo: el que gana "
            "se lleva casi todas las ventas y el resto queda escondido detrás "
            "de *otras opciones de compra*. No es una diferencia de posición, "
            "es vender o no vender.")

        with st.expander("Por qué el precio para ganar no es el del ganador"):
            st.markdown(
                "El **precio para ganar** casi nunca coincide con lo que "
                "cobra el que gana hoy, y suele ser bastante más bajo. No es "
                "un error.\n\n"
                "MercadoLibre pondera el precio junto con los beneficios de "
                "la publicación: Full, envío gratis y cuotas. Si el ganador "
                "los tiene y vos no, para empatarle tenés que compensar con "
                "precio. **La diferencia entre lo que cobra el ganador y lo "
                "que tendrías que cobrar vos es, en pesos, lo que te cuesta "
                "no tener esas palancas.**\n\n"
                "De ahí salen dos diagnósticos que piden cosas opuestas:\n\n"
                "- **Perdés por precio**: el ganador está más barato. Se "
                "arregla con precio.\n"
                "- **Perdés estando más barato**: ya cobrás menos y perdés "
                "igual. Bajar más es tirar plata — lo que falta son las "
                "palancas. Acá es donde Full deja de ser una idea y se vuelve "
                "una cuenta concreta.")

        b1, b2 = st.columns([1.4, 3])
        tope_bb = b1.selectbox(
            "Alcance", [150, 400, 0],
            format_func=lambda t: (f"Las {t} que más venden" if t
                                   else "Todas (~5 min)"),
            key="tope_bb")
        b2.write("")
        if b2.button("Revisar el Buy Box", use_container_width=True):
            estado = st.empty()
            with st.spinner("Consultando el Buy Box publicación por publicación..."):
                cargos_bb = cargos_cacheados(ml)
                unidades_bb = dict(zip(cargos_bb["sku"],
                                       cargos_bb["unidades_vendidas"]))
                st.session_state["buybox"] = buybox.analizar(
                    ml, pubs=pubs, tope=tope_bb or None, cargos=cargos_bb,
                    unidades=unidades_bb, pisos=pisos_de_marca(pubs),
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        aviso_piso_de_marca()

        dbb = st.session_state.get("buybox")
        if dbb is not None and len(dbb):
            rb = buybox.resumen(dbb)
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Publicaciones", rb["publicaciones"])
            g2.metric("Ganando", rb["ganando"])
            g3.metric("Compartiendo", rb["compartiendo"])
            g4.metric("Perdiendo", rb["perdiendo"])

            frenadas = int((dbb["diagnostico"]
                            == "no se puede sin perforar el piso").sum())
            if frenadas:
                st.warning(
                    f"**En {frenadas} publicaciones ganar el Buy Box exigiría "
                    "bajar por debajo del piso de marca.** Quedan afuera de "
                    "toda sugerencia: para esas, la palanca no es el precio.",
                    icon="🚧")

            if rb["mas_barato_y_perdiendo"]:
                extra = (f" La penalización mediana por no tener las palancas "
                         f"es {pesos_md(rb['penalizacion_mediana'])}."
                         if rb["penalizacion_mediana"] else "")
                st.error(
                    f"**En {rb['mas_barato_y_perdiendo']} publicaciones ya "
                    f"estás más barato que el ganador y perdés igual.** Ahí "
                    f"bajar el precio no sirve: lo que falta son Full, envío "
                    f"gratis o cuotas.{extra}", icon="🎯")

            if rb["baja_chica"]:
                st.success(
                    f"**{rb['baja_chica']} publicaciones se ganan bajando "
                    f"menos del {buybox.BAJA_CHICA:.0%}.** Es lo más barato "
                    f"que podés hacer hoy.", icon="✅")

            st.caption(
                f"Las publicaciones que están perdiendo venden "
                f"**{rb['unidades_perdiendo']:,}** unidades en el período "
                "medido.".replace(",", "."))

            estados_bb = sorted(dbb["diagnostico"].unique())
            por_defecto_bb = [e for e in estados_bb
                              if e not in ("ganando", "no compite", "sin dato")]
            filtro_bb = st.multiselect("Filtrar por diagnóstico", estados_bb,
                                       default=por_defecto_bb or estados_bb)
            vbb = dbb[dbb["diagnostico"].isin(filtro_bb)] if filtro_bb else dbb

            st.caption(
                "**Tildá filas para bajarles el precio a mano**, sin pasar "
                "por la planilla de costos. El precio nuevo es el que "
                "MercadoLibre pide para ganar la página de catálogo.")
            ev_bb = st.dataframe(
                vbb, use_container_width=True, height=440, hide_index=True,
                key="tabla_bb_principal", on_select="rerun",
                selection_mode="multi-row",
                column_config={
                    "item_id": "Publicación", "sku": "SKU", "titulo": "Título",
                    "diagnostico": "Diagnóstico",
                    "piso_marca": st.column_config.NumberColumn(
                        "Piso de marca", format="%.0f",
                        help="Costo de lista de Contabilium x 1,85. No se "
                             "puede publicar por debajo."),
                    "precio_actual": st.column_config.NumberColumn(
                        "Tu precio", format="%.0f"),
                    "precio_para_ganar": st.column_config.NumberColumn(
                        "Para ganar", format="%.0f"),
                    "precio_ganador": st.column_config.NumberColumn(
                        "Precio del ganador", format="%.0f"),
                    "bajar": st.column_config.NumberColumn(
                        "Hay que bajar", format="%.0f"),
                    "bajar_pct": st.column_config.NumberColumn(
                        "Bajar %", format="percent"),
                    "penalizacion_palancas": st.column_config.NumberColumn(
                        "Costo de no tener palancas", format="%.0f",
                        help="Lo que el ganador puede cobrar de más que vos "
                             "gracias a Full, envío gratis o cuotas"),
                    "queda_al_precio_para_ganar": st.column_config.NumberColumn(
                        "Te quedaría", format="%.0f",
                        help="Por unidad, antes del costo de la mercadería"),
                    "palancas_sin_usar": "Te falta",
                    "palancas_activas": "Ya usás",
                    "competidores_primeros": "Rivales 1°",
                    "share_de_visitas": "Share visitas",
                    "unidades": "Unidades (período)",
                    "vendidas_historico": "Vendidas (histórico)",
                    "producto_catalogo": None})
            st.download_button("Descargar el análisis",
                               vbb.to_csv(index=False).encode("utf-8"),
                               f"buybox_{datetime.now():%Y%m%d}.csv",
                               "text/csv")
            st.caption(
                f"Los precios de los competidores se cachean "
                f"{buybox.VIGENCIA_HORAS} horas. Para forzar la relectura, "
                "volvé a apretar el botón después de ese plazo.")

            # --------------------------------- aplicar solo lo que se tildó
            #
            # Este camino existe aparte del criterio automático de más abajo:
            # ahí hace falta la planilla de costos y calcular márgenes, y a
            # veces uno ya sabe qué publicación quiere mover.
            elegidas_bb = list(getattr(ev_bb.selection, "rows", []) or [])
            if elegidas_bb:
                man = vbb.iloc[elegidas_bb].copy()
                aplicables = man[man["precio_para_ganar"].notna()
                                 & man["bajar"].notna()
                                 & (man["bajar"] > 0)].copy()

                st.divider()
                st.markdown(f"##### Bajar a mano las {len(man)} que tildaste")

                sin_precio = len(man) - len(aplicables)
                if sin_precio:
                    st.caption(
                        f"{sin_precio} de las tildadas no tienen un precio "
                        "para ganar más bajo que el actual (ya ganás, no "
                        "compiten o no hay dato). Esas se ignoran.")

                if "piso_marca" in aplicables:
                    perfora = aplicables[
                        aplicables["piso_marca"].notna()
                        & (aplicables["precio_para_ganar"]
                           < aplicables["piso_marca"])]
                    if len(perfora):
                        st.error(
                            f"**{len(perfora)} de las que tildaste perforan el "
                            "piso de marca y no se van a aplicar.** El piso "
                            "es una regla comercial: no se puede saltear "
                            "desde acá.", icon="🚧")
                        aplicables = aplicables.drop(perfora.index)

                if not len(aplicables):
                    st.info("No queda ninguna para aplicar.")
                else:
                    st.dataframe(
                        aplicables[["item_id", "sku", "marca", "titulo",
                                    "precio_actual", "precio_para_ganar",
                                    "bajar_pct", "piso_marca", "unidades"]],
                        use_container_width=True, hide_index=True, height=220,
                        column_config={
                            "item_id": "Publicación", "sku": "SKU",
                            "marca": "Marca", "titulo": "Título",
                            "precio_actual": st.column_config.NumberColumn(
                                "Precio hoy", format="%.0f"),
                            "precio_para_ganar": st.column_config.NumberColumn(
                                "Precio nuevo", format="%.0f"),
                            "bajar_pct": st.column_config.NumberColumn(
                                "Baja", format="percent"),
                            "piso_marca": st.column_config.NumberColumn(
                                "Piso", format="%.0f"),
                            "unidades": "Unidades"})

                    baja_max_man = aplicables["bajar_pct"].max()
                    if baja_max_man and baja_max_man > buybox.TECHO_DE_BAJA:
                        st.error(
                            f"Una de las tildadas baja "
                            f"{baja_max_man:.0%}, por encima del tope duro "
                            f"del sistema ({buybox.TECHO_DE_BAJA:.0%}). "
                            "Revisala antes de seguir.", icon="⚠️")

                    st.warning(
                        "**Sin la planilla de costos no se sabe con qué "
                        "margen quedan.** El piso de marca sí se respeta. Si "
                        "querés ver el margen antes, usá el criterio "
                        "automático de más abajo.", icon="⚠️")
                    st.error(
                        "**Esto cambia los precios en MercadoLibre de "
                        "verdad.** Cada cambio queda en la auditoría con el "
                        "precio anterior.", icon="⚠️")

                    om1, om2 = st.columns([2, 3])
                    op_man = om1.text_input(
                        "Tu nombre (queda en el registro)", key="op_bbman")
                    conf_man = om2.checkbox(
                        f"Confirmo que quiero bajar el precio de "
                        f"{len(aplicables)} publicaciones", key="conf_bbman")

                    if st.button("Aplicar las bajas que tildé",
                                 key="go_bbman",
                                 disabled=not (conf_man and op_man.strip())):
                        barra = st.progress(0.0, text="Aplicando...")
                        st.session_state["bb_manual"] = buybox.aplicar(
                            ml, aplicables, operador=op_man.strip(),
                            callback=lambda i, t, iid: barra.progress(
                                i / t, text=f"Aplicando {i} de {t}: {iid}"))
                        barra.empty()
                        st.session_state.pop("buybox", None)
                        st.session_state.pop("buybox_costos", None)

            res_man = st.session_state.get("bb_manual")
            if res_man is not None and len(res_man):
                ok_m = int((res_man["resultado"] == "OK").sum())
                if ok_m == len(res_man):
                    st.success(f"{ok_m} precios actualizados.")
                else:
                    st.error(f"{ok_m} aplicados, {len(res_man) - ok_m} con "
                             "error.")
                st.dataframe(res_man, use_container_width=True,
                             hide_index=True)
                st.caption("Volvé a correr el análisis para ver el estado "
                           "nuevo del Buy Box.")

            # ------------------------------------------- bajar precios solo
            st.divider()
            st.markdown("##### Bajar precios y seguir ganando plata")
            st.caption(
                "Con la planilla de costos, la herramienta calcula qué te "
                "quedaría vendiendo al precio del Buy Box y te deja aplicar "
                "la baja en lote, solo en las publicaciones donde el margen "
                "aguanta.")

            costos_bb = bloque_costos("bb")

            i1, i2 = st.columns([1.2, 3])
            iva_bb = i1.selectbox(
                "IVA a descontar", [0.22, 0.10, 0.0],
                format_func=lambda x: f"{x:.1%}" if x else "Sin descontar",
                key="iva_bb",
                help="La planilla de costos está SIN IVA y los precios de ML "
                     "lo incluyen, así que corresponde descontarlo.")

            otros_bb = controles_otros_conceptos("bb")

            if costos_bb is not None and i2.button("Calcular márgenes",
                                                   use_container_width=True):
                with st.spinner("Cruzando con los cargos reales..."):
                    st.session_state["buybox_costos"] = buybox.con_costos(
                        dbb, costos_bb, cargos_cacheados(ml), iva=iva_bb,
                        otros_conceptos=otros_bb)

            dcb = st.session_state.get("buybox_costos")
            if dcb is not None and len(dcb):
                rc = buybox.resumen_costos(dcb)
                w1, w2, w3, w4 = st.columns(4)
                w1.metric("Podés ganar y seguir ganando", rc["ganables"])
                w2.metric("Margen flaco", rc["flacas"])
                w3.metric("Ganar daría pérdida", rc["perdida"])
                w4.metric("Sin costo cargado", rc["sin_costo"])

                if rc["cruzan_escalon"]:
                    st.warning(
                        f"**{rc['cruzan_escalon']} publicaciones cruzarían un "
                        "escalón de cargo fijo al bajar.** MercadoLibre cobra "
                        "un porcentaje más un cargo fijo por unidad, y ese "
                        "cargo salta en escalones. En Uruguay el más grande "
                        "está en \\$1.000, y ahí bajar **te conviene**: por "
                        "debajo de esa línea el envío lo paga el comprador, "
                        "así que bajar de \\$1.050 a \\$999 te saca ~\\$160 de "
                        "envío de encima y solo resigna \\$51 de precio. El "
                        "margen ya lo tiene en cuenta.", icon="🪜")

                st.markdown("###### Criterio para bajar")
                k1, k2, k3 = st.columns(3)
                # Los sliders van en PUNTOS PORCENTUALES enteros y se dividen
                # por 100 abajo. Con floats 0..1 y format printf, Streamlit
                # muestra "0%" en todo el recorrido: el printf no escala a
                # porcentaje, igual que en column_config.
                margen_min = k1.slider(
                    "Rentabilidad mínima aceptada",
                    int(buybox.PISO_DE_MARGEN * 100), 50, 10, 1,
                    format="%d%%", key="mg_bb",
                    help="En negativo aceptás vender a pérdida para ganar la "
                         f"página de catálogo. Piso duro del sistema: "
                         f"{buybox.PISO_DE_MARGEN:.0%}.") / 100
                baja_max = k2.slider(
                    "Baja máxima aceptada", 1,
                    int(buybox.TECHO_DE_BAJA * 100), 15, 1,
                    format="%d%%", key="bj_bb",
                    help=f"Tope duro del sistema: {buybox.TECHO_DE_BAJA:.0%}. "
                         "No se puede bajar más aunque el criterio lo permita."
                    ) / 100
                unid_min = k3.number_input("Unidades mínimas en el período",
                                           min_value=0, value=5, step=1,
                                           key="un_bb")

                j1, j2 = st.columns([2.4, 1.6])
                marcas_bb = j1.multiselect(
                    "Marcas (vacío = todas)",
                    sorted(m for m in dcb["marca"].dropna().unique() if m),
                    key="mk_bb")
                with j2:
                    st.write("")
                    cruzar = st.checkbox(
                        "Permitir las que cruzan escalón", value=False,
                        key="cr_bb",
                        help="Bajar de tramo puede sumar un cargo fijo que no "
                             "estaba. El margen ya lo contempla.")

                if margen_min < 0:
                    st.warning(
                        f"Estás aceptando vender **a pérdida de hasta "
                        f"{abs(margen_min):.0%}** con tal de ganar el Buy Box. "
                        "Puede tener sentido para entrar a una página de "
                        "catálogo o para liquidar, pero conviene mirarlo "
                        "publicación por publicación abajo.", icon="📉")

                sel_bb = buybox.seleccionar(
                    dcb, margen_minimo=margen_min, baja_maxima=baja_max,
                    unidades_minimas=unid_min,
                    permitir_cruzar_escalon=cruzar,
                    marcas=marcas_bb or None)

                st.markdown(cumplen(len(sel_bb)))

                if len(sel_bb):
                    st.caption(
                        "**Tildá filas para elegir a mano.** Si no seleccionás "
                        "ninguna se aplican todas las que cumplen el criterio.")
                    vista_sel = sel_bb[
                        ["item_id", "sku", "marca", "titulo", "precio_actual",
                         "precio_para_ganar", "bajar_pct", "margen_hoy",
                         "margen_al_ganar", "margen_al_ganar_pct", "unidades"]]
                    evento = st.dataframe(
                        vista_sel, use_container_width=True, height=320,
                        hide_index=True, key="tabla_bb",
                        on_select="rerun", selection_mode="multi-row",
                        column_config={
                            "item_id": "Publicación", "sku": "SKU",
                            "marca": "Marca", "titulo": "Título",
                            "precio_actual": st.column_config.NumberColumn(
                                "Precio hoy", format="%.0f"),
                            "precio_para_ganar": st.column_config.NumberColumn(
                                "Precio nuevo", format="%.0f"),
                            "bajar_pct": st.column_config.NumberColumn(
                                "Baja", format="percent"),
                            "margen_hoy": st.column_config.NumberColumn(
                                "Margen hoy", format="%.0f"),
                            "margen_al_ganar": st.column_config.NumberColumn(
                                "Margen nuevo", format="%.0f"),
                            "margen_al_ganar_pct": st.column_config.NumberColumn(
                                "Margen %", format="percent"),
                            "unidades": "Unidades"})

                    elegidas = list(getattr(evento.selection, "rows", []) or [])
                    a_aplicar = sel_bb.iloc[elegidas] if elegidas else sel_bb
                    if elegidas:
                        st.info(f"Vas a aplicar solo las **{len(a_aplicar)}** "
                                "que tildaste.", icon="👉")

                    con_perdida = int((a_aplicar["margen_al_ganar"] < 0).sum())
                    if con_perdida:
                        st.error(
                            f"**{con_perdida} de las {len(a_aplicar)} quedan a "
                            f"pérdida** al precio nuevo.", icon="📉")

                    st.divider()
                    st.error(
                        "**Esto cambia los precios en MercadoLibre de verdad.** "
                        "Cada cambio queda en la auditoría con el precio "
                        "anterior, así que se puede revertir a mano.",
                        icon="⚠️")
                    op_bb = st.text_input("Tu nombre (queda en el registro)",
                                          key="op_bb")
                    conf_bb = st.checkbox(
                        f"Confirmo que quiero bajar el precio de "
                        f"{len(a_aplicar)} publicaciones", key="conf_bb")
                    if st.button("Aplicar las bajas en MercadoLibre",
                                 key="go_bb",
                                 disabled=not (conf_bb and op_bb.strip())):
                        barra = st.progress(0.0, text="Aplicando...")
                        res_bb = buybox.aplicar(
                            ml, a_aplicar, operador=op_bb.strip(),
                            callback=lambda i, t, iid: barra.progress(
                                i / t, text=f"Aplicando {i} de {t}: {iid}"))
                        barra.empty()
                        ok = int((res_bb["resultado"] == "OK").sum())
                        if ok == len(res_bb):
                            st.success(f"{ok} precios actualizados.")
                        else:
                            st.error(f"{ok} aplicados, {len(res_bb) - ok} "
                                     "con error.")
                        st.dataframe(res_bb, use_container_width=True,
                                     hide_index=True)
                        # El cache quedo viejo: los precios cambiaron.
                        st.session_state.pop("buybox", None)
                        st.session_state.pop("buybox_costos", None)
                        st.caption("Volvé a correr el análisis para ver el "
                                   "estado nuevo del Buy Box.")
            elif dcb is not None:
                st.info("Ninguna publicación quedó con margen calculable. "
                        "Revisá que los SKU de la planilla coincidan con los "
                        "de MercadoLibre.")

    else:
        st.caption(
            "MercadoLibre le ofrece a cada publicación un menú de campañas. "
            "Cada oferta queda como **candidata** hasta que la tomás.")
        st.info(
            "**Lo primero que hay que mirar es el aporte de ML.** En algunos "
            "tipos MercadoLibre pone parte del descuento de su bolsillo: al "
            "comprador le baja el precio más de lo que te cuesta a vos. La "
            "campaña **¡Gánale a la competencia!** es la respuesta directa a "
            "las publicaciones donde perdés el Buy Box por precio — en vez de "
            "bajarlo vos solo, ML cofinancia la baja.", icon="💡")

        p1, p2 = st.columns([1.4, 3])
        tope_pr = p1.selectbox(
            "Alcance", [120, 300, 600],
            format_func=lambda t: f"Las {t} que más venden", key="tope_pr")
        p2.write("")
        if p2.button("Buscar promociones", use_container_width=True):
            estado = st.empty()
            with st.spinner("Consultando promociones publicación por publicación..."):
                cargos_pr = cargos_cacheados(ml)
                unidades_pr = dict(zip(cargos_pr["sku"],
                                       cargos_pr["unidades_vendidas"]))
                st.session_state["promos"] = promociones.analizar(
                    ml, pubs=pubs, tope=tope_pr, cargos=cargos_pr,
                    unidades=unidades_pr,
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        guardado_pr = st.session_state.get("promos")
        if guardado_pr is not None:
            dpr, camp = guardado_pr

            if len(camp):
                st.markdown("##### Campañas abiertas en la cuenta")
                st.dataframe(
                    camp, use_container_width=True, hide_index=True,
                    column_config={
                        "id": None, "tipo": None,
                        "nombre_tipo": "Tipo", "nombre": "Campaña",
                        "estado": "Estado", "desde": "Desde", "hasta": "Hasta",
                        "cierra_inscripcion": "Cierra inscripción"})

            if len(dpr):
                rp = promociones.resumen(dpr)
                q1, q2, q3, q4 = st.columns(4)
                q1.metric("Publicaciones con ofertas", rp["publicaciones"])
                q2.metric("Con aporte de ML", rp["con_aporte_ml"])
                q3.metric("Disponibles sin tomar", rp["disponibles"])
                q4.metric("Ya participás", rp["participando"])

                if rp["negativas"]:
                    st.warning(
                        f"**{rp['negativas']} ofertas dan negativo** ya antes "
                        "del costo de la mercadería: el precio de promoción no "
                        "cubre ni la comisión y el envío.", icon="⚠️")

                st.markdown("##### Por tipo de promoción")
                st.dataframe(
                    pd.DataFrame(rp["por_tipo"].most_common(),
                                 columns=["Promoción", "Ofertas"]),
                    use_container_width=True, hide_index=True, height=220)

                estados_pr = sorted(dpr["diagnostico"].unique())
                filtro_pr = st.multiselect(
                    "Filtrar por diagnóstico", estados_pr,
                    default=[e for e in estados_pr if e != "ya participás"]
                    or estados_pr)
                vpr = dpr[dpr["diagnostico"].isin(filtro_pr)] if filtro_pr else dpr

                st.dataframe(
                    vpr, use_container_width=True, height=420, hide_index=True,
                    column_config={
                        "item_id": "Publicación", "sku": "SKU",
                        "titulo": "Título", "campana_id": None, "tipo": None,
                        "promocion": "Promoción", "nombre": "Campaña",
                        "estado": None, "diagnostico": "Diagnóstico",
                        "precio_actual": st.column_config.NumberColumn(
                            "Tu precio", format="%.0f"),
                        "precio_promo": st.column_config.NumberColumn(
                            "Precio con promo", format="%.0f"),
                        "descuento": st.column_config.NumberColumn(
                            "Descuento", format="percent"),
                        "aporte_ml": st.column_config.NumberColumn(
                            "Pone ML", format="percent"),
                        "aporte_vendedor": st.column_config.NumberColumn(
                            "Ponés vos", format="percent"),
                        "queda_por_unidad": st.column_config.NumberColumn(
                            "Te queda", format="%.0f",
                            help="Por unidad, antes del costo de la mercadería"),
                        "unidades": "Unidades (período)",
                        "vendidas_historico": "Vendidas (histórico)",
                        "desde": "Desde", "hasta": "Hasta"})
                st.download_button("Descargar las promociones",
                                   vpr.to_csv(index=False).encode("utf-8"),
                                   f"promociones_{datetime.now():%Y%m%d}.csv",
                                   "text/csv")
                # ------------------------------------------ alta automatica
                st.divider()
                st.markdown("##### Alta automática por criterio")
                st.caption(
                    "Definís una regla una vez y la herramienta selecciona "
                    "sola qué publicaciones sumar. El alta se aplica en lote "
                    "después de que la revises.")

                r1, r2, r3 = st.columns(3)
                # En puntos porcentuales enteros: ver la nota en Buy Box.
                ap_max = r1.slider("SUPRABOND pone como máximo",
                                   0, 30, 5, 1, format="%d%%",
                                   key="ap_max") / 100
                ml_min = r2.slider("MercadoLibre pone al menos",
                                   0, 30, 0, 1, format="%d%%",
                                   key="ml_min") / 100
                un_min_pr = r3.number_input("Unidades mínimas en el período",
                                            min_value=0, value=1, step=1,
                                            key="un_pr")
                ml_super = st.checkbox(
                    "Exigir que MercadoLibre ponga más que SUPRABOND",
                    value=True, key="ml_sup")

                sel_pr = promociones.seleccionar(
                    dpr, aporte_vendedor_max=ap_max,
                    ml_debe_superar=ml_super, aporte_ml_min=ml_min,
                    unidades_minimas=un_min_pr)

                st.markdown(cumplen(len(sel_pr)))
                st.caption(
                    "Solo entran ofertas **disponibles y sin tomar**. Si una "
                    "publicación califica para varias promociones se toma la "
                    "que deja más plata por unidad: sumarla a todas sería "
                    "pisar una con otra.")

                if len(sel_pr):
                    st.dataframe(
                        sel_pr[["item_id", "sku", "titulo", "promocion",
                                "nombre", "precio_actual", "precio_promo",
                                "aporte_ml", "aporte_vendedor",
                                "queda_por_unidad", "unidades"]],
                        use_container_width=True, height=300, hide_index=True,
                        column_config={
                            "item_id": "Publicación", "sku": "SKU",
                            "titulo": "Título", "promocion": "Promoción",
                            "nombre": "Campaña",
                            "precio_actual": st.column_config.NumberColumn(
                                "Precio hoy", format="%.0f"),
                            "precio_promo": st.column_config.NumberColumn(
                                "Precio con promo", format="%.0f"),
                            "aporte_ml": st.column_config.NumberColumn(
                                "Pone ML", format="percent"),
                            "aporte_vendedor": st.column_config.NumberColumn(
                                "Ponés vos", format="percent"),
                            "queda_por_unidad": st.column_config.NumberColumn(
                                "Te queda", format="%.0f"),
                            "unidades": "Unidades"})

                    st.divider()
                    st.error(
                        "**Esto suma las publicaciones a la promoción en "
                        "MercadoLibre de verdad**, o sea que cambia el precio "
                        "que ve el comprador. Queda registrado en la "
                        "auditoría.", icon="⚠️")
                    op_pr = st.text_input("Tu nombre (queda en el registro)",
                                          key="op_pr")
                    conf_pr = st.checkbox(
                        f"Confirmo que quiero sumar {len(sel_pr)} "
                        "publicaciones a su promoción", key="conf_pr")
                    if st.button("Sumar a las promociones", key="go_pr",
                                 disabled=not (conf_pr and op_pr.strip())):
                        barra = st.progress(0.0, text="Dando de alta...")
                        res_pr = promociones.aplicar(
                            ml, sel_pr, operador=op_pr.strip(),
                            callback=lambda i, t, iid: barra.progress(
                                i / t, text=f"Alta {i} de {t}: {iid}"))
                        barra.empty()
                        ok = int((res_pr["resultado"] == "OK").sum())
                        if ok == len(res_pr):
                            st.success(f"{ok} publicaciones sumadas.")
                        else:
                            st.error(f"{ok} sumadas, {len(res_pr) - ok} "
                                     "con error.")
                        st.dataframe(res_pr, use_container_width=True,
                                     hide_index=True)
                        st.session_state.pop("promos", None)
                        st.caption("Volvé a buscar promociones para ver el "
                                   "estado nuevo.")

                st.caption(
                    "También podés tomarlas a mano desde el panel de "
                    "MercadoLibre; esta sección no reemplaza ese camino.")
            else:
                st.info("Ninguna publicación del alcance elegido tiene "
                        "ofertas disponibles.")

elif seccion == "Precios":
    bloque_carga("precio")

elif seccion == "Mayoristas":
    st.markdown("#### Precios mayoristas por reglas")
    st.caption(
        "Define descuentos por cantidad con reglas por familia, por SKU o "
        "generales. La herramienta toma el precio publicado de cada item y "
        "arma los tramos automáticamente.")

    st.caption(
        "Los tramos se cargan como **precio mayorista exclusivo para negocios**, "
        "igual que desde el panel de MercadoLibre.")

    sub = st.radio("Vista", ["Simulación", "Reglas"], horizontal=True,
                   label_visibility="collapsed")

    if sub == "Reglas":
        st.caption(
            "Gana la regla de **menor orden**, así que lo específico pisa a lo "
            "general. Los criterios son: `sku` (código exacto), `familia` "
            "(código dentro del SKU, ej. CDB), `categoria` (texto de la "
            "categoría de ML), `titulo` y `general`. Separá varios valores "
            "con `|`. Se editan en la hoja "
            f"`{mayoristas.HOJA_REGLAS}` de la planilla.")
        regs = pd.DataFrame(almacen.leer_hoja(mayoristas.HOJA_REGLAS,
                                              mayoristas.COLS_REGLAS))
        if not len(regs):
            regs = pd.DataFrame(mayoristas.REGLAS_INICIALES)
        st.dataframe(regs, use_container_width=True, height=460)

    else:
        if st.button("Simular precios mayoristas"):
            with st.spinner("Aplicando las reglas al catálogo..."):
                st.session_state["may"] = mayoristas.simular(pubs)

        sim = st.session_state.get("may")
        if sim is not None and len(sim):
            aplicables = sim[sim["accion"] == "aplicar"]
            m1, m2, m3 = st.columns(3)
            m1.metric("Publicaciones alcanzadas", len(sim))
            m2.metric("Con tramos calculados", len(aplicables))
            m3.metric("Sin regla o sin tramos", len(sim) - len(aplicables))

            st.markdown("##### Cuántas publicaciones toma cada regla")
            st.dataframe(sim["regla"].value_counts().rename_axis("Regla")
                         .reset_index(name="Publicaciones"),
                         use_container_width=True, height=220)

            filtro = st.multiselect("Filtrar por regla",
                                    sorted(sim["regla"].unique()),
                                    default=sorted(sim["regla"].unique()))
            vista_m = sim[sim["regla"].isin(filtro)]

            st.dataframe(
                vista_m, use_container_width=True, height=380,
                column_config={
                    "item_id": "Publicación", "sku": "SKU", "titulo": "Título",
                    "regla": "Regla",
                    "precio": st.column_config.NumberColumn("Precio", format="%.0f"),
                    "q1_unidades": "Desde (Q1)",
                    "q1_precio": st.column_config.NumberColumn("Precio Q1", format="%.0f"),
                    "q2_unidades": "Desde (Q2)",
                    "q2_precio": st.column_config.NumberColumn("Precio Q2", format="%.0f"),
                    "accion": "Acción", "motivo": "Motivo"})

            st.download_button("Descargar la simulación",
                               vista_m.to_csv(index=False).encode("utf-8"),
                               f"mayoristas_{datetime.now():%Y%m%d_%H%M}.csv",
                               "text/csv")

            st.divider()
            op_may = st.text_input("Tu nombre (queda en el registro)", key="op_may")
            conf_may = st.checkbox(
                f"Confirmo que quiero cargar los tramos en {len(aplicables)} "
                "publicaciones", key="conf_may")
            ya_hechas = st.session_state.get("may_ok", set())
            if ya_hechas:
                st.info(
                    f"De una corrida anterior quedaron **{len(ya_hechas)} "
                    "publicaciones ya cargadas**. Se van a saltear para no "
                    "repetirlas.", icon="↩️")

            if st.button("Aplicar en MercadoLibre", key="go_may",
                         disabled=not (conf_may and op_may.strip())):
                barra = st.progress(0.0, text="Aplicando...")
                res = mayoristas.aplicar(
                    ml, sim, operador=op_may.strip(), omitir=ya_hechas,
                    callback=lambda i, t, f: barra.progress(
                        i / t, text=f"Aplicando {i} de {t}..."))
                barra.empty()
                # Se guarda para poder retomar: con miles de publicaciones,
                # una corrida puede cortarse y repetir todo es carísimo.
                st.session_state["may_res"] = res
                st.session_state["may_ok"] = set(ya_hechas) | set(
                    res[res["resultado"] == "OK"]["item_id"])

            res = st.session_state.get("may_res")
            if res is not None and len(res):
                ok = int((res["resultado"] == "OK").sum())
                fallaron = res[res["resultado"] != "OK"]
                if not len(fallaron):
                    st.success(f"{ok} publicaciones con precio mayorista "
                               "cargado.")
                else:
                    st.error(f"{ok} cargadas, {len(fallaron)} con error.")
                    motivos = fallaron["detalle"].str.slice(0, 60).value_counts()
                    st.markdown("**Por qué fallaron**")
                    st.dataframe(
                        motivos.rename_axis("Motivo").reset_index(
                            name="Publicaciones"),
                        use_container_width=True, hide_index=True, height=160)
                    if st.button(f"Reintentar las {len(fallaron)} que fallaron",
                                 key="retry_may"):
                        barra = st.progress(0.0, text="Reintentando...")
                        res2 = mayoristas.aplicar(
                            ml, sim[sim["item_id"].isin(fallaron["item_id"])],
                            operador=op_may.strip() or "reintento",
                            callback=lambda i, t, f: barra.progress(
                                i / t, text=f"Reintentando {i} de {t}..."))
                        barra.empty()
                        st.session_state["may_res"] = pd.concat(
                            [res[res["resultado"] == "OK"], res2])
                        st.session_state["may_ok"] = set(
                            st.session_state.get("may_ok", set())) | set(
                            res2[res2["resultado"] == "OK"]["item_id"])
                        st.rerun()

                st.dataframe(res, use_container_width=True, height=260)
                st.caption(
                    "Los tramos tardan unos segundos en verse en la publicación. "
                    "El editor de MercadoLibre los muestra en el bloque "
                    "*Precios mayoristas*.")

elif seccion == "Promos por planilla":
    st.markdown("#### Descuentos en lote desde una planilla")
    st.caption(
        "Subís una planilla con una columna de **SKU o EAN** (también sirve el "
        "código MLU) y otra con el **descuento en porcentaje**, y cada producto "
        "entra a la campaña con ese descuento. Los que no estén en la planilla "
        "no se tocan.")

    @st.cache_data(ttl=600, show_spinner=False)
    def _campanas_propias(_ml, sello):
        return promos_planilla.campanas_propias(_ml)

    @st.cache_data(ttl=600, show_spinner=False)
    def _elegibles(_ml, campana_id, sello):
        return promos_planilla.elegibles(_ml, campana_id)

    if "sello_promos" not in st.session_state:
        st.session_state["sello_promos"] = 0

    try:
        camps = _campanas_propias(ml, st.session_state["sello_promos"])
    except MeliError as e:
        st.error(f"No pude traer las campañas: {e}")
        st.stop()

    if not len(camps):
        st.warning(
            "**No hay ninguna campaña propia vigente.** Estas campañas se "
            "crean desde el panel de MercadoLibre, en *Publicaciones → "
            "Promociones → Crear campaña propia*. Por la API no se pueden "
            "crear: el pedido contesta que sí y no crea nada.", icon="⚠️")
        st.stop()

    st.info(
        "La campaña se crea desde el panel de MercadoLibre; acá se le cargan "
        "las publicaciones. Una vez creada aparece sola en esta lista.",
        icon="ℹ️")

    etiquetas = {
        f"{c['nombre']}  ·  hasta el {c['hasta']}": c["campana_id"]
        for _, c in camps.iterrows()}
    elegida = st.selectbox("Campaña", list(etiquetas), key="camp_pp")
    campana_id = etiquetas[elegida]

    _, col_releer = st.columns([3, 1])
    col_releer.button(
        "↻ Releer campañas", key="rl_pp", use_container_width=True,
        on_click=lambda: st.session_state.__setitem__(
            "sello_promos", st.session_state["sello_promos"] + 1))

    with st.spinner("Preguntándole a MercadoLibre qué publicaciones acepta..."):
        try:
            eleg = _elegibles(ml, campana_id, st.session_state["sello_promos"])
        except MeliError as e:
            st.error(f"No pude traer las publicaciones elegibles: {e}")
            st.stop()

    ya_activas = [e for e in eleg.values() if e["estado_promo"] == "started"]
    e1, e2, e3 = st.columns(3)
    e1.metric("Publicaciones que acepta", f"{len(eleg):,}".replace(",", "."))
    e2.metric("Ya con descuento", len(ya_activas))

    # El descuento minimo lo fija ML por publicacion y no es un porcentaje
    # fijo: conviene decir el rango real antes de que arme la planilla.
    minimos = [1 - e["max_precio"] / e["original_price"] for e in eleg.values()
               if e.get("max_precio") and e.get("original_price")]
    maximos = [1 - e["min_precio"] / e["original_price"] for e in eleg.values()
               if e.get("min_precio") and e.get("original_price")]
    if minimos:
        e3.metric("Descuento admitido",
                  f"{min(minimos):.0%} a {max(maximos):.0%}")
        st.caption(
            f"MercadoLibre fija el rango **por publicación**, no por campaña: "
            f"el descuento mínimo va de {min(minimos):.1%} a {max(minimos):.1%} "
            f"según el artículo. Lo que quede fuera se marca en la simulación "
            f"y no se aplica.")

    archivo_pp = st.file_uploader("Planilla (.xlsx o .csv)",
                                  type=["xlsx", "xls", "csv"], key="up_pp")
    if not archivo_pp:
        st.session_state.pop("sim_pp", None)
        st.session_state.pop("res_pp", None)
        st.stop()

    try:
        df_pp = promos_planilla.leer_planilla(archivo_pp)
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude leer la planilla: {e}")
        st.stop()

    ck_auto, cp_auto = promos_planilla.detectar_columnas(df_pp)
    cols_pp = list(df_pp.columns)

    p1, p2, p3 = st.columns([2, 2, 1])
    with p1:
        col_clave_pp = st.selectbox(
            "Columna de SKU / EAN / MLU", cols_pp,
            index=cols_pp.index(ck_auto) if ck_auto in cols_pp else 0,
            key="ck_pp")
    with p2:
        col_pct_pp = st.selectbox(
            "Columna de descuento", cols_pp,
            index=cols_pp.index(cp_auto) if cp_auto in cols_pp else 0,
            key="cp_pp")
    with p3:
        st.metric("Filas", f"{len(df_pp):,}".replace(",", "."))

    st.caption(
        "El descuento se lee igual escrito como `30`, `30%`, `0,30` o `0.3`. "
        "De 1 para abajo se toma como fracción.")

    with st.expander("Ver la planilla como la leí"):
        st.dataframe(df_pp.head(50), use_container_width=True)

    if st.button("Simular los descuentos", key="sim_btn_pp"):
        try:
            st.session_state["sim_pp"] = promos_planilla.simular(
                df_pp, pubs, eleg, col_clave_pp, col_pct_pp, ml=ml)
            st.session_state.pop("res_pp", None)
        except Exception as e:  # noqa: BLE001
            st.error(f"Error al simular: {e}")

    sim_pp = st.session_state.get("sim_pp")
    if sim_pp is None:
        st.stop()
    if sim_pp.empty:
        st.warning("La simulación no encontró ninguna fila utilizable.")
        st.stop()

    res_cuenta = promos_planilla.resumen(sim_pp)
    st.markdown("##### Qué va a pasar")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Altas nuevas", res_cuenta.get("alta", 0))
    q2.metric("Cambian de precio", res_cuenta.get("actualizar", 0))
    q3.metric("Ya estaban igual", res_cuenta.get("sin_cambio", 0))
    q4.metric("Fuera de rango", res_cuenta.get("fuera_de_rango", 0))

    otros = {kk: v for kk, v in res_cuenta.items()
             if kk not in ("alta", "actualizar", "sin_cambio",
                           "fuera_de_rango")}
    if otros:
        cols_o = st.columns(max(len(otros), 2))
        for col, (nombre, cant) in zip(cols_o, otros.items()):
            col.metric(nombre.replace("_", " ").capitalize(), cant)

    if res_cuenta.get("fuera_de_rango"):
        st.warning(
            f"**{res_cuenta['fuera_de_rango']} publicaciones tienen un "
            "descuento que MercadoLibre no acepta** para ese artículo. El "
            "motivo de cada una dice entre qué valores se puede. Quedan "
            "afuera.", icon="⚠️")
    if res_cuenta.get("no_elegible"):
        st.warning(
            f"**{res_cuenta['no_elegible']} publicaciones no entran en esta "
            "campaña.** Es MercadoLibre el que decide qué admite; probá con "
            "otra campaña.", icon="⚠️")

    filtro_pp = st.multiselect(
        "Filtrar por acción", sorted(sim_pp["accion"].unique()),
        default=sorted(sim_pp["accion"].unique()), key="f_pp")
    vista_pp = sim_pp[sim_pp["accion"].isin(filtro_pp)]

    st.dataframe(
        vista_pp, use_container_width=True, height=340, hide_index=True,
        column_config={
            "clave": "SKU / EAN",
            "item_id": "Publicación",
            "sku": "SKU",
            "titulo": "Título",
            "descuento": st.column_config.NumberColumn(
                "Descuento", format="percent"),
            "precio_actual": st.column_config.NumberColumn(
                "Precio hoy", format="%.0f"),
            "precio_promo": st.column_config.NumberColumn(
                "Precio con descuento", format="%.2f"),
            "precio_promo_vigente": st.column_config.NumberColumn(
                "Promo vigente", format="%.2f"),
            "min_permitido": None,
            "max_permitido": None,
            "accion": "Acción",
            "motivo": "Motivo"})

    st.download_button(
        "Descargar la simulación",
        vista_pp.to_csv(index=False).encode("utf-8"),
        f"simulacion_promos_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv",
        key="dl_pp")

    # -------------------------------------------------------------- aplicar
    st.divider()
    st.markdown("##### Aplicar en MercadoLibre")

    a_aplicar_pp = (res_cuenta.get("alta", 0) + res_cuenta.get("actualizar", 0))
    if a_aplicar_pp == 0:
        st.info("No hay nada para aplicar.")
        st.stop()

    st.error(
        f"**Esto carga {a_aplicar_pp} publicaciones a la campaña de verdad**, "
        "o sea que cambia el precio que ve el comprador. Queda registrado en "
        "la auditoría.", icon="⚠️")
    st.caption(
        "Para sacar una publicación de la campaña, o cambiarle el descuento, "
        "se puede volver a subir la planilla con otro porcentaje: el mismo "
        "pedido corrige uno ya cargado. Darla de baja del todo se hace desde "
        "el panel de MercadoLibre.")

    ap1, ap2 = st.columns([2, 3])
    with ap1:
        op_pp = st.text_input("Tu nombre (queda en el registro)", key="op_pp")
    with ap2:
        conf_pp = st.checkbox(
            f"Confirmo que quiero cargar {a_aplicar_pp} publicaciones "
            f"a «{camps.set_index('campana_id').loc[campana_id, 'nombre']}»",
            key="conf_pp")

    if st.button("Cargar a la campaña", key="go_pp",
                 disabled=not (conf_pp and op_pp.strip())):
        barra_pp = st.progress(0.0, text="Cargando...")
        with st.spinner("Escribiendo en MercadoLibre..."):
            st.session_state["res_pp"] = promos_planilla.aplicar(
                ml, sim_pp, campana_id, operador=op_pp.strip(),
                callback=lambda i, t, iid: barra_pp.progress(
                    i / t, text=f"Cargando {i} de {t}: {iid}"))
        barra_pp.empty()

    resultados_pp = st.session_state.get("res_pp")
    if resultados_pp is not None and len(resultados_pp):
        ok_pp = int((resultados_pp["resultado"] == "OK").sum())
        err_pp = len(resultados_pp) - ok_pp
        if err_pp == 0:
            st.success(f"Listo: {ok_pp} publicaciones cargadas a la campaña.")
        else:
            st.error(f"{ok_pp} cargadas, {err_pp} con error. El detalle está "
                     "abajo; volver a subir la planilla reintenta solo las "
                     "que faltan.")
        st.dataframe(
            resultados_pp[["clave", "item_id", "titulo", "descuento",
                           "precio_promo", "resultado", "detalle"]],
            use_container_width=True, height=280, hide_index=True,
            column_config={
                "clave": "SKU / EAN", "item_id": "Publicación",
                "titulo": "Título",
                "descuento": st.column_config.NumberColumn(
                    "Descuento", format="percent"),
                "precio_promo": st.column_config.NumberColumn(
                    "Precio con descuento", format="%.2f"),
                "resultado": "Resultado", "detalle": "Detalle"})
        st.download_button(
            "Descargar el resultado",
            resultados_pp.to_csv(index=False).encode("utf-8"),
            f"resultado_promos_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv",
            key="dlr_pp")
        st.caption(f"Todo quedó registrado en {almacen.describir()}.")

        # MercadoLibre tarda cerca de medio minuto en reflejar lo que se
        # acaba de escribir. Verificar al toque muestra el estado viejo y
        # hace parecer que no funciono.
        st.divider()
        st.markdown("##### Verificar contra MercadoLibre")
        st.caption(
            "MercadoLibre tarda alrededor de medio minuto en mostrar lo que "
            "se acaba de cargar. Si verificás enseguida vas a ver el estado "
            "anterior, y parece que no funcionó.")
        if st.button("Verificar qué quedó activo", key="ver_pp"):
            with st.spinner("Consultando..."):
                ids_ok = list(resultados_pp[
                    resultados_pp["resultado"] == "OK"]["item_id"])
                ver = promos_planilla.verificar(ml, campana_id, ids_ok)
            activas_n = int(ver["activa"].sum())
            if activas_n == len(ver):
                st.success(f"Las {activas_n} están activas en la campaña.")
            else:
                st.warning(
                    f"{activas_n} de {len(ver)} figuran activas. Las que "
                    "faltan pueden ser demora de MercadoLibre: probá de nuevo "
                    "en un minuto.")
            st.dataframe(ver, use_container_width=True, hide_index=True,
                         column_config={
                             "item_id": "Publicación", "activa": "Activa",
                             "precio_promo": st.column_config.NumberColumn(
                                 "Precio con descuento", format="%.2f")})

elif seccion == "Stock ML":
    bloque_carga("stock")

elif seccion == "Control de stock":
    st.markdown("#### Control de stock")
    st.caption(
        "Lleva la cuenta de tus unidades a partir de un stock inicial, "
        "descontando las ventas de MercadoLibre. **No modifica el stock de "
        "MercadoLibre**: es solo control interno con historial.")

    vista = st.radio("Vista", ["Stock actual", "Movimientos", "Ingresos",
                               "Devoluciones", "Cargar stock inicial"],
                     horizontal=True, label_visibility="collapsed")

    # ---------------------------------------------------------- stock actual
    if vista == "Stock actual":
        c1, c2, c3 = st.columns([1.4, 1.4, 2])
        with c1:
            dias_sync = st.selectbox("Revisar últimos", [1, 3, 7, 15, 30],
                                     index=2, format_func=lambda d: f"{d} días")
        with c2:
            st.write("")
            sincronizar = st.button("↻ Sincronizar ventas", use_container_width=True)

        if sincronizar:
            estado = st.empty()
            with st.spinner("Leyendo ventas de MercadoLibre..."):
                r = stock_control.sincronizar(
                    ml, dias=dias_sync, operador="app",
                    callback=lambda m: estado.caption(m))
            estado.empty()
            if r["ok"]:
                if r["movimientos_nuevos"]:
                    st.success(
                        f"{r['ventas']} ventas y {r['cancelaciones']} cancelaciones "
                        f"nuevas ({r['unidades']:.0f} unidades) sobre "
                        f"{r['ordenes_revisadas']} órdenes revisadas.")
                else:
                    st.info(f"Sin novedades: las {r['ordenes_revisadas']} órdenes "
                            "del período ya estaban registradas.")
            else:
                st.error(f"No se pudo guardar: {r['detalle']}")
            st.session_state.pop("stock_df", None)

        if "stock_df" not in st.session_state:
            with st.spinner("Calculando stock..."):
                st.session_state["stock_df"] = stock_control.stock_actual()
        df = st.session_state["stock_df"]

        if not len(df):
            st.info("Todavía no hay movimientos. Cargá el stock inicial y "
                    "después sincronizá las ventas.")
        else:
            negativos = df[df["stock_actual"] < 0]
            m1, m2, m3 = st.columns(3)
            m1.metric("SKU con seguimiento", len(df))
            m2.metric("Unidades en stock", f"{df['stock_actual'].sum():,.0f}"
                      .replace(",", "."))
            m3.metric("SKU en negativo", len(negativos))

            if len(negativos):
                st.warning(
                    f"**{len(negativos)} SKU dan negativo.** Normalmente es "
                    "porque falta cargar su stock inicial, o porque entró "
                    "mercadería que no se registró en Ingresos.", icon="⚠️")

            solo_neg = st.checkbox("Ver solo los negativos")
            st.dataframe(df[df["stock_actual"] < 0] if solo_neg else df,
                         use_container_width=True, height=420)
            st.download_button("Descargar el stock",
                               df.to_csv(index=False).encode("utf-8"),
                               f"stock_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv")

    # ---------------------------------------------------------- movimientos
    elif vista == "Movimientos":
        movs = pd.DataFrame(stock_control.movimientos())
        if not len(movs):
            st.info("Todavía no hay movimientos registrados.")
        else:
            f1, f2 = st.columns([2, 2])
            with f1:
                tipos = sorted(movs["tipo"].unique())
                sel = st.multiselect("Tipo", tipos, default=tipos)
            with f2:
                buscar = st.text_input("Buscar SKU").strip().upper()

            vista_m = movs[movs["tipo"].isin(sel)]
            if buscar:
                vista_m = vista_m[vista_m["sku"].str.contains(buscar, na=False)]

            st.caption(f"{len(vista_m)} movimientos")
            st.dataframe(vista_m.iloc[::-1], use_container_width=True, height=440)
            st.download_button("Descargar el historial",
                               vista_m.to_csv(index=False).encode("utf-8"),
                               f"movimientos_{datetime.now():%Y%m%d_%H%M}.csv",
                               "text/csv")

    # ---------------------------------------------------------- ingresos
    elif vista == "Ingresos":
        st.markdown("##### Cargar mercadería que entra")
        st.caption("Compras a proveedores, o ajustes cuando el conteo físico "
                   "no coincide con el sistema.")

        with st.form("form_ingreso"):
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                sku_in = st.text_input("SKU")
            with c2:
                cant_in = st.number_input("Cantidad", step=1.0, value=1.0)
            with c3:
                tipo_in = st.selectbox("Tipo", ["compra", "ajuste"])
            c4, c5 = st.columns(2)
            with c4:
                ref_in = st.text_input("Remito / factura (opcional)")
            with c5:
                op_in = st.text_input("Tu nombre")
            nota_in = st.text_input("Nota (opcional)")

            if st.form_submit_button("Registrar"):
                if not sku_in.strip() or not op_in.strip():
                    st.error("Hacen falta el SKU y tu nombre.")
                elif tipo_in == "compra" and cant_in <= 0:
                    st.error("Una compra tiene que sumar unidades. "
                             "Para restar, usá 'ajuste'.")
                else:
                    ok, det = stock_control.registrar(
                        tipo_in, sku_in, cant_in, referencia=ref_in,
                        detalle=nota_in, operador=op_in.strip())
                    if ok:
                        st.success(f"Registrado: {cant_in:+.0f} de "
                                   f"{sku_in.strip().upper()}")
                        st.session_state.pop("stock_df", None)
                    else:
                        st.error(f"No se pudo registrar: {det}")

        st.divider()
        st.caption("Últimos ingresos cargados")
        movs = pd.DataFrame(stock_control.movimientos())
        if len(movs):
            ing = movs[movs["tipo"].isin(["compra", "ajuste", "devolucion_apta"])]
            st.dataframe(ing.iloc[::-1].head(25), use_container_width=True)

    # ---------------------------------------------------------- devoluciones
    elif vista == "Devoluciones":
        st.caption(
            "Las devoluciones **no vuelven solas al stock**. Cada una espera "
            "acá hasta que alguien confirme si la unidad está apta para "
            "venderse de nuevo.")

        if st.button("↻ Buscar devoluciones nuevas"):
            with st.spinner("Consultando reclamos en MercadoLibre..."):
                r = stock_control.sincronizar_devoluciones(ml, operador="app")
            if r["ok"]:
                st.success(f"{r['nuevas']} devoluciones nuevas en la bandeja."
                           if r["nuevas"] else "Sin devoluciones nuevas.")
            else:
                st.error(f"No se pudo consultar: {r['detalle']}")

        devs = pd.DataFrame(stock_control.devoluciones())
        if not len(devs):
            st.info("No hay devoluciones registradas.")
        else:
            pendientes = devs[devs["resolucion"] == "pendiente"]
            st.metric("Pendientes de revisar", len(pendientes))

            if len(pendientes):
                st.markdown("##### Resolver una devolución")
                with st.form("form_dev"):
                    ids = pendientes["id_dev"].astype(str).tolist()
                    c1, c2 = st.columns([2, 2])
                    with c1:
                        id_sel = st.selectbox("Devolución", ids)
                        sku_dev = st.text_input(
                            "SKU de la unidad devuelta",
                            help="MercadoLibre no siempre informa el SKU en el "
                                 "reclamo: verificalo en la orden.")
                    with c2:
                        res_sel = st.selectbox(
                            "¿Vuelve al stock?",
                            ["apta", "descarte"],
                            format_func=lambda x: ("Sí, está apta para vender"
                                                   if x == "apta"
                                                   else "No, se descarta"))
                        cant_dev = st.number_input("Unidades", step=1.0, value=1.0)
                    op_dev = st.text_input("Tu nombre")

                    if st.form_submit_button("Guardar resolución"):
                        if res_sel == "apta" and not sku_dev.strip():
                            st.error("Para devolver al stock hace falta el SKU.")
                        elif not op_dev.strip():
                            st.error("Poné tu nombre.")
                        else:
                            ok, det = stock_control.resolver_devolucion(
                                id_sel, res_sel, sku=sku_dev, cantidad=cant_dev,
                                operador=op_dev.strip())
                            if ok:
                                st.success("Resolución guardada." + (
                                    f" {cant_dev:.0f} unidad/es volvieron al stock."
                                    if res_sel == "apta" else ""))
                                st.session_state.pop("stock_df", None)
                            else:
                                st.error(f"No se pudo guardar: {det}")

            st.dataframe(devs.iloc[::-1], use_container_width=True, height=300)

    # ---------------------------------------------------------- stock inicial
    elif vista == "Cargar stock inicial":
        st.caption(
            "El punto de partida del conteo. Subí una planilla con **SKU** y "
            "**cantidad**. Se puede cargar de nuevo más adelante: cada carga "
            "se suma a la anterior, así que sirve también para corregir.")

        arch = st.file_uploader("Planilla de stock inicial", type=["xlsx", "xls", "csv"],
                                key="up_stock_ini")
        if arch:
            try:
                df_ini = act.leer_planilla(arch)
                cols = list(df_ini.columns)
                c1, c2 = st.columns(2)
                with c1:
                    col_sku = st.selectbox("Columna de SKU", cols)
                with c2:
                    col_cant = st.selectbox("Columna de cantidad", cols,
                                            index=min(1, len(cols) - 1))
                op_ini = st.text_input("Tu nombre", key="op_ini")

                previa = pd.DataFrame({
                    "sku": df_ini[col_sku].astype(str).str.strip().str.upper(),
                    "cantidad": df_ini[col_cant].map(act._a_numero)}).dropna()
                previa = previa[previa["sku"].ne("") & previa["sku"].ne("NAN")]

                st.caption(f"{len(previa)} filas listas para cargar")
                st.dataframe(previa.head(30), use_container_width=True)

                if st.button("Cargar stock inicial",
                             disabled=not op_ini.strip() or not len(previa)):
                    ok, det = stock_control.cargar_stock_inicial(
                        previa.to_dict("records"), operador=op_ini.strip())
                    if ok:
                        st.success(f"{len(previa)} SKU cargados.")
                        st.session_state.pop("stock_df", None)
                    else:
                        st.error(f"No se pudo cargar: {det}")
            except Exception as e:
                st.error(f"No pude leer la planilla: {e}")

elif seccion == "Precio óptimo":
    vista_po = st.radio(
        "Vista", ["Ventana de precio", "Subir al piso de marca"],
        horizontal=True, label_visibility="collapsed", key="vista_po")

    if vista_po == "Subir al piso de marca":
        st.markdown("#### Publicaciones por debajo del piso de marca")
        st.caption(
            "Suprabond, Bulit y Somerset no se publican por debajo de "
            f"**{lista_gsu.MULTIPLICADOR} veces el precio de lista de "
            "Contabilium**. Acá están las que hoy están por debajo, y se les puede "
            "subir el precio al piso de una.")
        aviso_piso_de_marca()

        st.info(
            "**Las que están en una promoción activa quedan afuera.** Ahí hay un "
            "precio que el comprador está viendo ahora: subirle el precio de lista "
            "por debajo deja la oferta incoherente, y en algunos tipos "
            "MercadoLibre recalcula el descuento sobre el precio nuevo y la promo "
            "se encarece sola. Primero hay que sacarlas de la campaña.", icon="🎟️")

        if st.button("Buscar las que están por debajo"):
            estado_pf = st.empty()
            with st.spinner("Releyendo precios y revisando promociones..."):
                st.session_state["plan_piso"] = lista_gsu.plan_subir_al_piso(
                    ml, pubs, callback=lambda m: estado_pf.caption(str(m)))
            estado_pf.empty()
            st.session_state.pop("res_piso", None)

        plan_pf = st.session_state.get("plan_piso")
        if plan_pf is not None and len(plan_pf):
            rpf = lista_gsu.resumen_plan(plan_pf)
            f1, f2, f3 = st.columns(3)
            f1.metric("Se suben", rpf["suben"])
            f2.metric("En promoción", rpf["en_promo"])
            f3.metric("Suba mediana", f"{rpf['suba_mediana']:.0%}")

            if rpf["grandes"]:
                st.warning(
                    f"**{rpf['grandes']} publicaciones suben más del "
                    f"{lista_gsu.SUBA_QUE_LLAMA_LA_ATENCION:.0%}** — la más fuerte "
                    f"{rpf['suba_maxima']:.0%}. No se bloquean, el piso es el "
                    "piso, pero conviene mirarlas: un salto así puede frenar la "
                    "conversión de golpe.", icon="⚠️")

            if rpf["omitidas"]:
                st.caption(
                    f"{rpf['omitidas']} quedaron afuera porque su precio ya se "
                    "movió desde el último análisis, o la publicación no está "
                    "activa.")

            suben_pf = plan_pf[plan_pf["accion"] == "subir"]
            en_promo_pf = plan_pf[plan_pf["accion"] == "omitir_promo"]

            if len(en_promo_pf):
                with st.expander(f"Las {len(en_promo_pf)} que están en promoción"):
                    st.dataframe(
                        en_promo_pf[["item_id", "sku", "titulo", "precio_actual",
                                     "piso", "motivo"]],
                        use_container_width=True, hide_index=True, height=240,
                        column_config={
                            "item_id": "Publicación", "sku": "SKU",
                            "titulo": "Título",
                            "precio_actual": st.column_config.NumberColumn(
                                "Precio hoy", format="%.0f"),
                            "piso": st.column_config.NumberColumn(
                                "Piso", format="%.0f"),
                            "motivo": "Por qué queda afuera"})

            if not len(suben_pf):
                st.success("No hay ninguna para subir.")
            else:
                st.markdown("##### Las que se van a subir")
                st.caption(
                    "**Tildá filas para subir solo algunas.** Si no seleccionás "
                    "ninguna se suben todas.")
                ev_pf = st.dataframe(
                    suben_pf[["item_id", "sku", "marca", "titulo",
                              "precio_actual", "piso", "sube", "sube_pct"]],
                    use_container_width=True, height=340, hide_index=True,
                    key="tabla_piso", on_select="rerun",
                    selection_mode="multi-row",
                    column_config={
                        "item_id": "Publicación", "sku": "SKU", "marca": "Marca",
                        "titulo": "Título",
                        "precio_actual": st.column_config.NumberColumn(
                            "Precio hoy", format="%.0f"),
                        "piso": st.column_config.NumberColumn(
                            "Precio nuevo", format="%.0f"),
                        "sube": st.column_config.NumberColumn(
                            "Sube", format="%.0f"),
                        "sube_pct": st.column_config.NumberColumn(
                            "Sube %", format="percent")})

                elegidas_pf = list(getattr(ev_pf.selection, "rows", []) or [])
                a_subir = suben_pf.iloc[elegidas_pf] if elegidas_pf else suben_pf
                if elegidas_pf:
                    st.info(f"Vas a subir solo las **{len(a_subir)}** que "
                            "tildaste.", icon="👉")

                st.download_button(
                    "Descargar el plan",
                    plan_pf.to_csv(index=False).encode("utf-8"),
                    f"piso_de_marca_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv",
                    key="dl_pf")

                st.divider()
                st.error(
                    f"**Esto sube el precio de {len(a_subir)} publicaciones en "
                    "MercadoLibre de verdad.** Cada cambio queda en la auditoría "
                    "con el precio anterior.", icon="⚠️")
                pf1, pf2 = st.columns([2, 3])
                op_pf = pf1.text_input("Tu nombre (queda en el registro)",
                                       key="op_pf")
                conf_pf = pf2.checkbox(
                    f"Confirmo que quiero subir {len(a_subir)} precios al piso",
                    key="conf_pf")

                if st.button("Subir al piso", key="go_pf",
                             disabled=not (conf_pf and op_pf.strip())):
                    barra_pf = st.progress(0.0, text="Subiendo...")
                    st.session_state["res_piso"] = lista_gsu.aplicar_subida(
                        ml, a_subir, operador=op_pf.strip(),
                        callback=lambda i, t, iid: barra_pf.progress(
                            i / t, text=f"Subiendo {i} de {t}: {iid}"))
                    barra_pf.empty()
                    st.session_state.pop("plan_piso", None)

        elif plan_pf is not None:
            st.success("Ninguna publicación de marca propia está por debajo del "
                       "piso.")

        res_pf = st.session_state.get("res_piso")
        if res_pf is not None and len(res_pf):
            ok_pf = int((res_pf["resultado"] == "OK").sum())
            if ok_pf == len(res_pf):
                st.success(f"{ok_pf} precios subidos al piso.")
            else:
                st.error(f"{ok_pf} subidos, {len(res_pf) - ok_pf} con error.")
            st.dataframe(res_pf, use_container_width=True, hide_index=True)
            st.caption("Volvé a buscar para ver el estado nuevo.")
        st.stop()

    st.markdown("#### Ventana de precio")
    st.caption(
        "Junta las tres cuentas que hasta ahora estaban separadas: el **piso** "
        "(abajo no llegás al margen), el **techo útil** (arriba perdés la "
        "página de catálogo) y el **escalón de cargo fijo** (dentro de la "
        "ventana no todos los precios rinden igual). Devuelve un precio "
        "sugerido por SKU, con el motivo.")

    with st.expander("Los seis casos y por qué piden cosas distintas"):
        st.markdown(
            "- **Ventana amplia** — podés acomodar el precio *y* quedarte con "
            "la página. Es el único caso donde no se resigna nada.\n"
            "- **Bajar para ganar** — ganar la página exige bajar. El margen "
            "lo aguanta, pero se resigna neto por unidad: **solo conviene si "
            "el volumen extra lo compensa**, y eso no sale de ningún dato de "
            "la API. Por eso no entra en la selección automática.\n"
            "- **Sin ventana** — ganar la página exige vender por debajo de "
            "tu piso. No es problema de precio sino de costo, o de contra "
            "quién te estás midiendo.\n"
            "- **Ya ganás** — tenés la página; lo único a mirar es si podés "
            "acomodar el precio sin perderla.\n"
            "- **Catálogo en otra publicación** — la página la pelea otra "
            "publicación del mismo SKU, que este cambio de precio **no "
            "toca**. El Buy Box de esas se resuelve en *Ganar la venta*.\n"
            "- **Fuera de catálogo** — no hay página que ganar, manda el "
            "piso.")

    costos_vt = bloque_costos("vt")
    otros_vt = controles_otros_conceptos("vt")

    t1, t2, t3 = st.columns(3)
    iva_vt = t1.selectbox(
        "IVA a descontar", [0.22, 0.10, 0.0],
        format_func=lambda x: f"{x:.1%}" if x else "Sin descontar", key="iva_vt")
    objetivo_vt = t2.slider(
        "Margen objetivo", 0, 40, 15, 1, format="%d%%", key="obj_vt",
        help="Alimenta el cálculo: si lo cambiás hay que volver a apretar "
             "«Calcular». El resto de los filtros se aplican al instante."
        ) / 100
    t3.write("")
    if costos_vt is not None and t3.button("Calcular la ventana",
                                           use_container_width=True):
        estado = st.empty()
        with st.spinner("Cruzando piso, Buy Box y escalones..."):
            cat_ids = [p["id"] for p in pubs
                       if p.get("status") == "active"
                       and p.get("catalog_listing")]
            ptw = buybox.traer_price_to_win(
                ml, cat_ids, callback=lambda m: estado.caption(str(m)))
            st.session_state["vent"] = ventana.analizar(
                costos_vt, cargos_cacheados(ml), pubs, iva=iva_vt,
                otros_conceptos=otros_vt, objetivo=objetivo_vt,
                ptw_por_item=ptw)
        estado.empty()
    st.caption(
        f"La primera corrida consulta el Buy Box de cada publicación de "
        f"catálogo y tarda unos minutos; después se cachea "
        f"{buybox.VIGENCIA_HORAS} horas.")

    dvt = st.session_state.get("vent")
    if dvt is not None and len(dvt):
        rv = ventana.resumen(dvt)
        c1, c2, c3 = st.columns(3)
        c1.metric("Ventana amplia", rv["ventana_amplia"])
        c2.metric("Bajar para ganar", rv["bajar_para_ganar"])
        c3.metric("Sin ventana", rv["sin_ventana"])
        c4, c5, c6 = st.columns(3)
        c4.metric("Ya ganás la página", rv["ya_ganan"])
        c5.metric("Catálogo en otra pub.", rv["catalogo_aparte"])
        c6.metric("Fuera de catálogo", rv["fuera"])

        st.metric("Impacto de los que mejoran", pesos(rv["impacto"]))
        st.caption(
            "El impacto asume **el mismo volumen** que el período medido. "
            "Cambiar el precio cambia el volumen, así que es una referencia "
            "de tamaño, no una proyección.")

        if rv["cruzan_escalon"]:
            st.info(
                f"**{rv['cruzan_escalon']} sugerencias cruzan un escalón de "
                "cargo fijo.** Ojo con el de \\$1.000: ahí el cargo fijo de ML "
                "se hace cero, pero el envío pasa a pagarlo el vendedor "
                "(~\\$160), así que cruzarlo hacia arriba cuesta plata. Lo que "
                "conviene es quedarse debajo.", icon="🪜")

        st.markdown("###### Criterio")
        d1, d2 = st.columns(2)
        cambio_max = d1.slider("Cambio máximo de precio", 1, 100, 20, 1,
                               format="%d%%", key="cm_vt") / 100
        unid_vt = d2.number_input("Unidades mínimas en el período",
                                  min_value=0, value=5, step=1, key="un_vt")
        e1, e2 = st.columns([2.4, 1.6])
        casos_vt = e1.multiselect(
            "Casos a incluir", sorted(dvt["caso"].unique()),
            default=[c for c in sorted(dvt["caso"].unique())
                     if c != "bajar para ganar"],
            key="cs_vt",
            help="«Bajar para ganar» queda afuera por defecto: resigna neto "
                 "por unidad y solo conviene si el volumen lo paga.")
        marcas_vt = e2.multiselect(
            "Marcas (vacío = todas)",
            sorted(m for m in dvt["marca"].dropna().unique() if m),
            key="mk_vt")

        sel_vt = ventana.seleccionar(
            dvt, casos=casos_vt or None, cambio_maximo=cambio_max,
            unidades_minimas=unid_vt, marcas=marcas_vt or None)
        st.markdown(cumplen(len(sel_vt)))

        if len(sel_vt):
            st.caption("**Tildá filas para elegir a mano.** Si no seleccionás "
                       "ninguna van todas las que cumplen.")
            ev_vt = st.dataframe(
                sel_vt[["sku", "marca", "titulo", "caso", "precio_actual",
                        "piso", "precio_para_ganar", "precio_sugerido",
                        "cambio_pct", "neto_actual", "neto_sugerido",
                        "impacto_periodo", "unidades", "cruza_escalon"]],
                use_container_width=True, height=360, hide_index=True,
                key="tabla_vt", on_select="rerun", selection_mode="multi-row",
                column_config={
                    "sku": "SKU", "marca": "Marca", "titulo": "Título",
                    "caso": "Caso",
                    "precio_actual": st.column_config.NumberColumn(
                        "Precio hoy", format="%.0f"),
                    "piso": st.column_config.NumberColumn(
                        "Piso", format="%.0f",
                        help="Abajo de acá no llegás al margen objetivo"),
                    "precio_para_ganar": st.column_config.NumberColumn(
                        "Para ganar", format="%.0f"),
                    "precio_sugerido": st.column_config.NumberColumn(
                        "Sugerido", format="%.0f"),
                    "cambio_pct": st.column_config.NumberColumn(
                        "Cambio", format="percent"),
                    "neto_actual": st.column_config.NumberColumn(
                        "Neto hoy", format="%.0f"),
                    "neto_sugerido": st.column_config.NumberColumn(
                        "Neto sugerido", format="%.0f"),
                    "impacto_periodo": st.column_config.NumberColumn(
                        "Impacto", format="%.0f"),
                    "unidades": "Unidades",
                    "cruza_escalon": st.column_config.CheckboxColumn(
                        "Cruza escalón")})

            elegidas_vt = list(getattr(ev_vt.selection, "rows", []) or [])
            aplicar_vt = sel_vt.iloc[elegidas_vt] if elegidas_vt else sel_vt
            if elegidas_vt:
                st.info(f"Vas a aplicar solo las **{len(aplicar_vt)}** que "
                        "tildaste.", icon="👉")

            with st.expander("Ver el motivo de cada sugerencia"):
                for _, f in aplicar_vt.head(20).iterrows():
                    st.markdown(f"**{f['sku']}** · {f['caso']} · "
                                f"{pesos_md(f['precio_actual'])} → "
                                f"{pesos_md(f['precio_sugerido'])}")
                    st.caption(f["motivo"])

            st.divider()
            st.warning(
                "**Cambiar precios cambia lo que ve el comprador.** El "
                "cálculo dice qué precio te conviene según tus costos y la "
                "competencia de catálogo, **no si el mercado lo va a "
                "pagar**.", icon="⚠️")

            if st.button("Simular el cambio de precios", key="sim_vt"):
                st.session_state["vent_sim"] = act.simular(
                    ventana.planilla_de_precios(aplicar_vt), pubs, "precio",
                    col_clave="sku", col_valor="precio")

            sim_vt = st.session_state.get("vent_sim")
            if sim_vt is not None and len(sim_vt):
                revisar = int((sim_vt["accion"] == "revisar").sum())
                f1, f2 = st.columns(2)
                f1.metric("Listas para aplicar",
                          int((sim_vt["accion"] == "actualizar").sum()))
                f2.metric("Marcadas para revisar", revisar)
                if revisar:
                    st.error(
                        f"**{revisar} superan el "
                        f"{act.UMBRAL_ALERTA_PRECIO:.0%} de variación** y no "
                        "se aplican salvo que lo pidas aparte.", icon="🛑")
                st.dataframe(sim_vt, use_container_width=True, height=280,
                             hide_index=True)

                op_vt = st.text_input("Tu nombre (queda en el registro)",
                                      key="op_vt")
                inc_vt = st.checkbox("Incluir también las marcadas para "
                                     "revisar", key="rev_vt")
                conf_vt = st.checkbox(
                    "Confirmo que quiero cambiar estos precios en "
                    "MercadoLibre", key="conf_vt")
                if st.button("Aplicar en MercadoLibre", key="go_vt",
                             disabled=not (conf_vt and op_vt.strip())):
                    barra = st.progress(0.0, text="Aplicando...")
                    res_vt = act.aplicar(
                        ml, sim_vt, "precio", operador=op_vt.strip(),
                        incluir_revisar=inc_vt,
                        callback=lambda i, t, f: barra.progress(
                            i / t, text=f"Aplicando {i} de {t}..."))
                    barra.empty()
                    ok = int((res_vt["resultado"] == "OK").sum())
                    if ok == len(res_vt):
                        st.success(f"{ok} precios actualizados.")
                    else:
                        st.error(f"{ok} aplicados, {len(res_vt) - ok} con error.")
                    st.dataframe(res_vt, use_container_width=True,
                                 hide_index=True)
                    st.session_state.pop("vent", None)
                    st.session_state.pop("vent_sim", None)

        st.download_button(
            "Descargar el análisis completo",
            dvt.to_csv(index=False).encode("utf-8"),
            f"ventana_{datetime.now():%Y%m%d}.csv", "text/csv")

elif seccion == "Competencia":
    st.markdown("#### Mejor precio de la competencia por EAN")
    st.caption(
        "Subí una planilla con los **EAN** (códigos de barras) y te dice quién "
        "vende más barato cada producto, a cuánto, y en qué posición estamos.")

    with st.expander("Qué alcance tiene esta búsqueda"):
        st.markdown(
            "MercadoLibre **cerró el buscador libre** para aplicaciones, así que "
            "la búsqueda va por el **catálogo**: vemos a todos los que venden ese "
            "producto de catálogo.\n\n"
            "- Si alguien publica el producto **por fuera del catálogo**, no "
            "aparece.\n"
            "- Si el EAN no tiene producto de catálogo, se reporta como "
            "`sin_catalogo`.\n\n"
            "Antes de reaccionar a una diferencia grande, conviene abrir la "
            "publicación del competidor: puede tratarse de otra presentación "
            "(unidad contra pack) aunque comparta el catálogo.")

    st.markdown("##### Tus más vendidos")
    st.caption(
        "Toma los artículos que más vendiste en el período, busca su código de "
        "barras y compara contra el catálogo. No hace falta mantener ninguna "
        "planilla: sale de tus ventas reales.")

    t1, t2, t3 = st.columns([1.1, 1.1, 2])
    cuantos = t1.selectbox("Cuántos", [20, 50, 100], index=1)
    dias_top = t2.selectbox("Período", [30, 60, 90], index=0,
                            format_func=lambda d: f"{d} días")
    if t3.button(f"Comparar mis {cuantos} más vendidos",
                 use_container_width=True):
        estado = st.empty()
        with st.spinner("Buscando tus más vendidos..."):
            eans_top, detalle, df_top = competencia.eans_mas_vendidos(
                ml, n=cuantos, dias=dias_top,
                callback=lambda m: estado.caption(str(m)))
        estado.caption(detalle)
        if eans_top:
            barra = st.progress(0.0, text="Consultando la competencia...")
            st.session_state["comp"] = competencia.analizar(
                ml, eans_top,
                callback=lambda i, t_, e: barra.progress(
                    i / t_, text=f"Consultando {i} de {t_}..."))
            barra.empty()
            st.session_state["comp_detalle"] = detalle
            ok_h, det_h = competencia.guardar_comparacion(
                st.session_state["comp"], origen="mas_vendidos")
            st.session_state["comp_guardado"] = (ok_h, det_h)
        else:
            st.warning("Ninguno de tus más vendidos tiene código de barras "
                       "cargado. Sin EAN no se pueden buscar en el catálogo.")

    if st.session_state.get("comp_detalle"):
        st.caption(st.session_state["comp_detalle"])

    st.divider()
    st.markdown("##### O subí una planilla")
    arch_ean = st.file_uploader("Planilla con EAN (.xlsx o .csv)",
                               type=["xlsx", "xls", "csv"], key="up_ean")
    if arch_ean:
        try:
            eans, col_detectada = competencia.leer_planilla_eans(arch_ean)
            st.caption(f"Columna detectada: **{col_detectada}** · "
                       f"{len(eans)} EAN únicos")
        except Exception as e:
            st.error(f"No pude leer la planilla: {e}")
            eans = []

        if eans and st.button(f"Buscar precios de {len(eans)} EAN"):
            barra = st.progress(0.0, text="Consultando MercadoLibre...")
            st.session_state["comp"] = competencia.analizar(
                ml, eans,
                callback=lambda i, t, e: barra.progress(
                    i / t, text=f"Consultando {i} de {t} ({e})..."))
            barra.empty()
            ok_h, det_h = competencia.guardar_comparacion(
                st.session_state["comp"], origen="planilla")
            st.session_state["comp_guardado"] = (ok_h, det_h)

    guardado = st.session_state.get("comp_guardado")
    if guardado:
        ok_h, det_h = guardado
        (st.caption if ok_h else st.warning)(
            f"📋 {det_h}" if ok_h
            else f"La comparación no se guardó en la planilla: {det_h}")

    df = st.session_state.get("comp")
    if df is not None and len(df):
        ok = df[df["estado"] == "ok"]
        perdiendo = ok[ok["diferencia"].notna() & (ok["diferencia"] > 0)]
        ganando = ok[ok["mejor_vendedor"] == "NOSOTROS"]

        m1, m2, m3 = st.columns(3)
        m1.metric("EAN con competencia", len(ok))
        m2.metric("Somos los más baratos", len(ganando))
        m3.metric("Estamos por encima", len(perdiendo))

        if len(perdiendo):
            peor = perdiendo.nlargest(1, "diferencia").iloc[0]
            st.warning(
                f"**En {len(perdiendo)} productos estamos más caros que el "
                f"más barato.** El caso extremo: EAN {peor['ean']}, "
                f"nosotros {pesos_md(peor['nuestro_precio'])} contra "
                f"{pesos_md(peor['mejor_precio'])} de *{peor['mejor_vendedor']}* "
                f"({peor['diferencia']:+.0%}).", icon="📉")

        sin_cat = df[df["estado"] != "ok"]
        if len(sin_cat):
            st.info(f"{len(sin_cat)} EAN sin datos de competencia "
                    "(sin producto de catálogo o sin vendedores activos).")

        st.dataframe(
            df, use_container_width=True, height=420,
            column_config={
                "ean": "EAN", "producto": "Producto",
                "mejor_precio": st.column_config.NumberColumn(
                    "Mejor precio", format="%.0f"),
                "mejor_vendedor": "Lo vende",
                "reputacion": "Reputación",
                "nuestro_precio": st.column_config.NumberColumn(
                    "Nuestro precio", format="%.0f"),
                "diferencia": st.column_config.NumberColumn(
                    "Diferencia", format="percent",
                    help="Cuánto estamos por encima del más barato"),
                "posicion": "Posición",
                "competidores": "Vendedores",
                "estado": "Estado", "detalle": "Detalle",
                "product_id": "Producto ML"})

        st.download_button("Descargar el análisis",
                           df.to_csv(index=False).encode("utf-8"),
                           f"competencia_{datetime.now():%Y%m%d_%H%M}.csv",
                           "text/csv")

    st.divider()
    with st.expander("📋 Historial de comparaciones"):
        st.caption(
            "Cada comparación queda guardada en la hoja "
            f"`{competencia.HOJA_HISTORIAL}`. Sirve para ver cómo evolucionó "
            "el precio de un competidor o el tuyo a lo largo del tiempo.")
        if st.button("Cargar el historial"):
            try:
                st.session_state["comp_hist"] = competencia.historial()
            except Exception as e:
                st.error(f"No pude leer el historial: {e}")

        hcomp = st.session_state.get("comp_hist")
        if hcomp is not None and len(hcomp):
            c1, c2 = st.columns(2)
            c1.metric("Mediciones guardadas", len(hcomp))
            c2.metric("Productos distintos", hcomp["ean"].nunique())

            ean_sel = st.selectbox(
                "Ver la evolución de un producto",
                ["(todos)"] + sorted(hcomp["ean"].unique()),
                format_func=lambda e: e if e == "(todos)" else
                f"{e} · {hcomp[hcomp.ean == e].iloc[-1]['producto'][:45]}")
            v = hcomp if ean_sel == "(todos)" else hcomp[hcomp.ean == ean_sel]

            if ean_sel != "(todos)" and len(v) > 1:
                serie = v.copy()
                for c in ("mejor_precio", "nuestro_precio"):
                    serie[c] = pd.to_numeric(serie[c], errors="coerce")
                st.line_chart(serie.set_index("fecha")[
                    ["mejor_precio", "nuestro_precio"]])

            st.dataframe(v.iloc[::-1], use_container_width=True, height=300)
            st.download_button("Descargar el historial",
                               v.to_csv(index=False).encode("utf-8"),
                               f"historial_competencia_{datetime.now():%Y%m%d}.csv",
                               "text/csv", key="dl_hcomp")
        elif hcomp is not None:
            st.info("Todavía no hay comparaciones guardadas.")

elif seccion == "Publicidad":
    st.markdown("#### Publicidad")
    st.caption(
        "Un anunciante y una campaña. Al crearla, MercadoLibre generó los "
        "anuncios solo y activó una parte sin preguntar, así que el trabajo "
        "no es sumar sino sacar los que no rinden.")

    dias_pub = st.selectbox("Período a medir", [7, 15, 30, 60], index=2,
                            format_func=lambda d: f"últimos {d} días")
    hasta_pub = datetime.now().date() - timedelta(days=1)
    desde_pub = hasta_pub - timedelta(days=dias_pub - 1)

    # Con st.tabs Streamlit ejecuta y renderiza las TRES vistas en cada rerun
    # —incluida la que baja miles de anuncios de la API— y ademas las apila
    # visualmente mientras recalcula. Se elige con un selector para que en el
    # DOM exista solo la vista activa.
    _VISTAS_PUB = ["Cómo va", "Qué haría con los anuncios",
                   "Correr el proceso", "Topes y estratégicos"]
    vista_pub = st.segmented_control(
        "Vista", _VISTAS_PUB, default=_VISTAS_PUB[0],
        key="pub_vista", label_visibility="collapsed") or _VISTAS_PUB[0]

    if vista_pub == "Cómo va":
        if st.button("Traer campañas"):
            try:
                with st.spinner("Leyendo publicidad..."):
                    st.session_state["pub_camp"] = [
                        (a, publicidad.campanas(ml, a["advertiser_id"]))
                        for a in publicidad.anunciantes(ml)]
            except Exception as e:
                st.error(f"No pude leer publicidad: {type(e).__name__}: {e}")
                st.stop()

        camps = st.session_state.get("pub_camp")
        if camps:
            for a, cs in camps:
                st.markdown(f"**{a['advertiser_name']}** · anunciante "
                            f"`{a['advertiser_id']}`")
                for c in cs:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Campaña", c["name"])
                    m2.metric("Estado", c["status"])
                    m3.metric("Presupuesto", pesos(c.get("budget") or 0))
                    m4.metric("ACOS objetivo", f"{c.get('acos_target', 0):.0f}%")
                st.divider()

            with st.expander("Crear una campaña"):
                if not panel_ads.hay_sesion():
                    st.caption("Hace falta la sesión del panel (`[ads] ssid` "
                               "en los secrets).")
                else:
                    nom = st.text_input("Nombre", key="nc_nombre")
                    d1, d2, d3 = st.columns(3)
                    adv_nc = d1.selectbox(
                        "Anunciante", [a["advertiser_id"] for a, _ in camps],
                        format_func=lambda i: next(
                            a["advertiser_name"] for a, _ in camps
                            if a["advertiser_id"] == i), key="nc_adv")
                    pres_nc = d2.number_input("Presupuesto", 1000, 999999,
                                              20000, 1000, key="nc_pres")
                    acos_nc = d3.number_input("ACOS objetivo %", 1, 100, 15,
                                              key="nc_acos")
                    st.caption(
                        "**Nace pausada.** Una campaña con presupuesto "
                        "empieza a gastar apenas se activa, así que "
                        "encenderla es un paso aparte.")
                    if st.button("Crear", key="nc_go",
                                 disabled=not nom.strip()):
                        ok, det = panel_ads.crear_campana(
                            panel_ads.leer_sesion(), adv_nc, nom.strip(),
                            pres_nc, acos_nc)
                        if ok:
                            st.success(f"Creada con id {det}. Está pausada.")
                            st.session_state.pop("pub_camp", None)
                        else:
                            st.error(str(det))

    elif vista_pub == "Qué haría con los anuncios":
        st.caption(
            f"Mide del {desde_pub:%d/%m} al {hasta_pub:%d/%m}. "
            "anuncios por anunciante, así que la lectura tarda unos minutos.")

        cfg = publicidad.config()
        st.markdown("Topes vigentes: **ACOS máx** {:.0f}% · **ROAS mín** "
                    "{:.1f} · se ignora lo que tenga menos de {:.0f} clics"
                    .format(cfg["acos_max"], cfg["roas_min"],
                            cfg["clicks_minimos"]))

        # Los candidatos a entrar en campana salen del mismo analisis de
        # Visitas vs ventas, que tarda varios minutos: se reusa el que ya
        # este en memoria en vez de recalcularlo.
        conv_pub = st.session_state.get("conv")
        if conv_pub is None and (Path(__file__).parent / "conversion.csv").exists():
            conv_pub = pd.read_csv(Path(__file__).parent / "conversion.csv")
        if conv_pub is None:
            st.caption(
                "Para proponer **qué sumar a las campañas** hace falta el "
                "análisis de *Visitas vs ventas* (Oportunidades). Sin eso, "
                "acá solo se evalúan los anuncios que ya existen.")

        if st.button("Analizar los anuncios"):
            paso = st.empty()
            try:
                with st.spinner("Bajando anuncios y métricas..."):
                    df_ads, advs_pub, camps_pub = publicidad.traer_todo(
                        ml, desde_pub.isoformat(), hasta_pub.isoformat(),
                        callback=lambda m: paso.caption(str(m)))
                    plan_ads = publicidad.analizar(df_ads, pubs)
                    nuevos = publicidad.candidatos(
                        conv_pub, pubs, df_ads, advs_pub, camps_pub)
                    if len(nuevos):
                        plan_ads = pd.concat([plan_ads, nuevos],
                                             ignore_index=True)
                    st.session_state["pub_plan"] = plan_ads
            except Exception as e:
                paso.empty()
                st.error(f"No pude analizar: {type(e).__name__}: {e}")
                st.stop()
            paso.empty()

        pl = st.session_state.get("pub_plan")
        if pl is not None and len(pl):
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Anuncios", len(pl))
            g2.metric("Gasto", pesos(pl["gasto"].sum()))
            g3.metric("Facturado", pesos(pl["facturado"].sum()))
            acos_gral = (pl["gasto"].sum() / pl["facturado"].sum() * 100
                         if pl["facturado"].sum() else 0)
            g4.metric("ACOS general", f"{acos_gral:.0f}%")

            resumen_pub = (pl.groupby("accion")
                           .agg(anuncios=("item_id", "size"),
                                gasto=("gasto", "sum"),
                                unidades=("unidades", "sum")).reset_index())
            st.dataframe(resumen_pub, use_container_width=True,
                         hide_index=True,
                         column_config={
                             "accion": "Qué haría",
                             "gasto": st.column_config.NumberColumn(
                                 "Gasto", format="%.0f"),
                             "unidades": st.column_config.NumberColumn(
                                 "Unidades", format="%.0f")})

            apagar = pl[pl["accion"] == "pausar"]
            if len(apagar):
                st.warning(
                    f"**{len(apagar)} anuncios** gastaron "
                    f"{pesos(apagar['gasto'].sum())} y facturaron "
                    f"{pesos(apagar['facturado'].sum())}.", icon="🔥")

            sumar = pl[pl["accion"] == "agregar"]
            if len(sumar) and "campana_activa" in sumar:
                dormidas = int((~sumar["campana_activa"].fillna(False)).sum())
                if dormidas:
                    st.warning(
                        f"**{dormidas} de las {len(sumar)} irían a una "
                        "campaña pausada** y ahí no van a gastar ni a "
                        "mostrarse. La campaña está en "
                        "pausa: si querés que corran, hay que activarla.",
                        icon="😴")
            if len(sumar):
                st.info(
                    f"**{len(sumar)} publicaciones convierten y no se "
                    "publicitan.** Salen de *Visitas vs ventas*: ya "
                    "demostraron que venden, les falta gente que las vea. No "
                    "entran las que tienen visitas y no venden — ahí el "
                    "problema es el precio o las fotos, y pagar clics no lo "
                    "arregla.", icon="🎯")

            ver = st.selectbox("Ver", ["pausar", "agregar", "activar",
                                       "revisar", "ninguna"], index=0,
                               key="pub_ver")
            v = pl[pl["accion"] == ver]
            st.dataframe(
                v[["sku", "titulo", "anunciante", "estado_ad", "gasto",
                   "facturado", "unidades", "acos", "roas", "motivo"]],
                use_container_width=True, height=380, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "anunciante": "Campaña", "estado_ad": "Estado",
                    "gasto": st.column_config.NumberColumn(
                        "Gasto", format="%.0f"),
                    "facturado": st.column_config.NumberColumn(
                        "Facturado", format="%.0f"),
                    "unidades": st.column_config.NumberColumn(
                        "Unid.", format="%.0f"),
                    "acos": st.column_config.NumberColumn(
                        "ACOS %", format="%.0f"),
                    "roas": st.column_config.NumberColumn(
                        "ROAS", format="%.1f"),
                    "motivo": "Por qué"})

            st.download_button(
                "Descargar el plan",
                pl.to_csv(index=False).encode("utf-8"),
                f"publicidad_{datetime.now():%Y%m%d}.csv", "text/csv",
                key="pub_dl")

            st.divider()
            st.markdown("##### Aplicar en MercadoLibre")

            if panel_ads.hay_sesion():
                st.info(
                    "Los cambios se aplican por el **panel de publicidad**, "
                    "no por la API: MercadoLibre no habilitó la escritura de "
                    "Product Ads para esta aplicación. Funciona con la cookie "
                    "`ssid` guardada en los secrets.", icon="🔑")
            else:
                st.error(
                    "**No hay forma de aplicar los cambios ahora mismo.** La "
                    "API de MercadoLibre rechaza toda escritura de publicidad "
                    "para esta aplicación —*«User does not have permission to "
                    "write»*, y falla igual con la cuenta dueña de los "
                    "anunciantes— y tampoco está cargada la sesión del panel, "
                    "que es la vía alternativa. Para habilitarla hay que "
                    "poner la cookie `ssid` en los secrets, bajo "
                    "`[ads]`.", icon="🔒")

            n_apagar = int((pl["accion"] == "pausar").sum())
            n_sumar = int((pl["accion"] == "agregar").sum())
            n_prender = int((pl["accion"] == "activar").sum())

            como_apagar = st.radio(
                f"A los {n_apagar} que hay que apagar",
                ["Pausarlos (quedan en la campaña)",
                 "Sacarlos de la campaña (quedan en idle)",
                 "No tocarlos"],
                key="pub_apagar", horizontal=False)

            ejecutar = pl.copy()
            elegidas = []
            if como_apagar.startswith("Pausar"):
                elegidas.append("pausar")
            elif como_apagar.startswith("Sacar"):
                # Las reglas marcan 'pausar'; sacarlas de la campaña es la
                # misma decision con otra intensidad.
                ejecutar.loc[ejecutar["accion"] == "pausar", "accion"] = "sacar"
                elegidas.append("sacar")

            if n_sumar and st.checkbox(
                    f"Sumar las {n_sumar} que convierten y no se publicitan",
                    key="pub_sumar"):
                elegidas.append("agregar")
            if n_prender and st.checkbox(
                    f"Encender las {n_prender} apagadas que rinden",
                    key="pub_prender"):
                elegidas.append("activar")

            elegidas = tuple(elegidas)
            cuantas = (int(ejecutar["accion"].isin(elegidas).sum())
                       if elegidas else 0)

            if any(a in elegidas for a in ("agregar", "activar")):
                st.warning(
                    "Estás incluyendo acciones que **empiezan a gastar**. Un "
                    "anuncio que entra a una campaña arranca activo.",
                    icon="💸")

            if "agregar" in elegidas and len(sumar):
                dormidas_g = sumar[~sumar.get(
                    "campana_activa", pd.Series(True, index=sumar.index))
                    .fillna(False)]
                if len(dormidas_g):
                    st.error(
                        f"**{len(dormidas_g)} de las que vas a sumar van a "
                        "una campaña pausada, así que se va a prender.** Y "
                        "prender una campaña enciende **todo lo que ya tiene "
                        "adentro**, no solo lo que estás agregando: la "
                        "general de una cuenta grande puede tener miles, "
                        "estado corrible, con un tope de \\$78.859. Eso es "
                        "empezar a gastar en mil quinientos anuncios que "
                        "nadie revisó, no sumar 24.", icon="🚨")

            op_pub = st.text_input("Tu nombre (queda en el registro)",
                                   key="pub_op")
            conf_pub = st.checkbox(
                f"Confirmo que quiero aplicar {cuantas} cambios en la "
                "publicidad de MercadoLibre", key="pub_conf")
            if st.button(f"Aplicar {cuantas} cambios", key="pub_go",
                         disabled=not (conf_pub and op_pub.strip() and cuantas
                                       and panel_ads.hay_sesion())):
                barra = st.progress(0.0, text="Aplicando...")
                try:
                    sesion_ads = panel_ads.leer_sesion()
                    partes = []
                    # Sumar a una campaña pausada no sirve: el anuncio entra
                    # activo pero la campaña no corre.
                    if "agregar" in elegidas:
                        st.session_state["pub_prendidas"] = (
                            panel_ads.despertar_campanas(
                                sesion_ads, ml,
                                ejecutar[ejecutar["accion"] == "agregar"],
                                callback=lambda m: barra.progress(
                                    0.0, text=str(m))))
                    # Cada acción va por separado: el endpoint de sacar de
                    # campaña es otro y acepta lotes mucho más chicos.
                    for acc in elegidas:
                        filas = ejecutar[ejecutar["accion"] == acc]
                        if not len(filas):
                            continue
                        partes.append(panel_ads.aplicar(
                            sesion_ads, ml, filas, accion=acc,
                            callback=lambda i, t, d: barra.progress(
                                min(i / max(t, 1), 1.0),
                                text=f"{acc}: {i} de {t} ({d})")))
                    res_pub = (pd.concat(partes, ignore_index=True)
                               if partes else pd.DataFrame())
                except Exception as e:
                    barra.empty()
                    st.error(f"La corrida se cortó: {type(e).__name__}: {e}")
                    st.stop()
                barra.empty()
                st.session_state["pub_res"] = res_pub

            prendidas = st.session_state.get("pub_prendidas")
            if prendidas:
                st.warning(
                    "Se prendieron campañas para que los anuncios nuevos "
                    "corran: " + ", ".join(
                        f"**{c['nombre']}** (tope {pesos(c['presupuesto'] or 0)})"
                        for c in prendidas)
                    + ". Con eso también arrancó todo lo que ya tenían "
                      "adentro.", icon="🔛")

            res_pub = st.session_state.get("pub_res")
            if res_pub is not None and len(res_pub):
                ok_pub = int((res_pub["resultado"] == "OK").sum())
                if ok_pub == len(res_pub):
                    st.success(f"{ok_pub} anuncios actualizados.")
                else:
                    st.error(f"{ok_pub} aplicados, "
                             f"{len(res_pub) - ok_pub} con error.")
                    if res_pub["detalle"].astype(str).str.contains(
                            "permission|401|503", case=False).any():
                        st.info(
                            "Los errores dicen que falta permiso: es lo de "
                            "arriba, no un problema de esta pantalla.",
                            icon="🔒")
                st.dataframe(res_pub, use_container_width=True,
                             hide_index=True)

    elif vista_pub == "Correr el proceso":
        st.caption(
            "Lo mismo que corre solo los martes a las 9: mide, apaga lo que "
            "pasa el ACOS objetivo y suma lo que convierte y no se publicita. "
            "**Sin topes**: hace todo lo que califica.")

        conv_ya = st.session_state.get("conv")
        if conv_ya is not None and len(conv_ya):
            st.caption(f"Va a reusar el análisis de *Visitas vs ventas* que "
                       f"ya está en memoria ({len(conv_ya)} publicaciones), "
                       "así que tarda unos 2 minutos.")
        else:
            st.warning(
                "No hay análisis de *Visitas vs ventas* en memoria, así que "
                "lo va a medir: es **una llamada por publicación** y tarda "
                "unos 15 minutos. No cierres la pestaña. Si primero corrés "
                "esa sección (en Oportunidades), esto baja a 2 minutos.",
                icon="⏳")

        aplicar_pub = st.checkbox(
            "Aplicar de verdad (sin tildar, solo muestra qué haría)",
            key="cron_aplicar")
        if aplicar_pub:
            st.error(
                "Va a **apagar y encender anuncios de verdad**, sin tope de "
                "cantidad. Encender gasta desde el momento; el único límite "
                "es el presupuesto de cada campaña.", icon="🚨")

        if st.button("Correr el proceso ahora", key="cron_go",
                     type="primary" if aplicar_pub else "secondary",
                     disabled=aplicar_pub and not panel_ads.hay_sesion()):
            caja = st.empty()
            lineas = []

            def _log(m):
                lineas.append(str(m))
                # Solo el final: el log entero son cientos de líneas y
                # repintarlo completo en cada paso vuelve la app un plomo.
                caja.code("\n".join(lineas[-18:]), language=None)

            try:
                with st.spinner("Corriendo..."):
                    publicidad_cron.correr(aplicar=aplicar_pub, log=_log,
                                           conv=conv_ya, ml=ml)
            except Exception as e:
                _log(f"\nSE CORTÓ: {type(e).__name__}: {e}")
                st.error(f"La corrida se cortó: {type(e).__name__}: {e}")
            st.session_state["cron_log"] = "\n".join(lineas)
            if aplicar_pub:
                # Los estados cambiaron: lo que estaba en pantalla quedó viejo.
                st.session_state.pop("pub_plan", None)

        if st.session_state.get("cron_log"):
            st.download_button(
                "Descargar el log completo",
                st.session_state["cron_log"].encode("utf-8"),
                f"publicidad_{datetime.now():%Y%m%d_%H%M}.txt", "text/plain",
                key="cron_dl")

    elif vista_pub == "Topes y estratégicos":
        st.caption(
            "Los topes y la lista de estratégicos viven en la Google Sheet, "
            "no en un archivo: en la nube el disco se borra en cada deploy.")

        cfg = publicidad.config()
        t1, t2 = st.columns(2)
        nuevo_cfg = {}
        with t1:
            nuevo_cfg["acos_max"] = st.number_input(
                "ACOS máximo %", 1.0, 200.0, float(cfg["acos_max"]), 1.0,
                help="Arriba de esto el anuncio se pausa.")
            nuevo_cfg["roas_min"] = st.number_input(
                "ROAS mínimo", 0.1, 50.0, float(cfg["roas_min"]), 0.1)
            nuevo_cfg["gasto_minimo"] = st.number_input(
                "Gasto mínimo para juzgar", 0.0, 999999.0,
                float(cfg["gasto_minimo"]), 500.0)
        with t2:
            nuevo_cfg["acos_bueno"] = st.number_input(
                "ACOS bueno %", 1.0, 200.0, float(cfg["acos_bueno"]), 1.0,
                help="Debajo de esto, un anuncio apagado se propone encender.")
            nuevo_cfg["roas_bueno"] = st.number_input(
                "ROAS bueno", 0.1, 50.0, float(cfg["roas_bueno"]), 0.1)
            nuevo_cfg["clicks_minimos"] = st.number_input(
                "Clics mínimos para juzgar", 0, 10000,
                int(cfg["clicks_minimos"]), 5)

        if st.button("Guardar topes"):
            ok, det = publicidad.guardar_config(nuevo_cfg)
            st.success("Topes guardados.") if ok else st.error(det)

        st.divider()
        st.markdown("##### SKU estratégicos")
        st.caption(
            "Estos SKU **no los toca ninguna regla**, ganen o pierdan. Son "
            "los que se publicitan por decisión comercial: lanzamientos, los "
            "que traen tráfico, los que se defienden de un competidor. Sin "
            "esta lista, la primera corrida los apaga a todos.")

        est = publicidad.estrategicos()
        df_est = pd.DataFrame(
            [{"sku": k, "nota": v} for k, v in est.items()]
            or [{"sku": "", "nota": ""}])
        editado = st.data_editor(df_est, num_rows="dynamic",
                                 use_container_width=True, key="pub_est",
                                 column_config={"sku": "SKU",
                                                "nota": "Por qué"})
        if st.button("Guardar estratégicos"):
            filas = [{"sku": str(r["sku"]).strip().upper(),
                      "nota": str(r["nota"] or "")}
                     for _, r in editado.iterrows()
                     if str(r.get("sku", "")).strip()]
            ok, det = publicidad.guardar_estrategicos(filas)

elif seccion == "Oportunidades":
    st.markdown("#### Dónde hay plata sobre la mesa")
    op = st.radio("Vista", ["Visitas vs ventas", "Tramos de comisión",
                            "Precios espejo", "Factura de ML",
                            "Envíos", "Candidatos a Full",
                            "Salud del catálogo"],
                  horizontal=True, label_visibility="collapsed")

    if op == "Candidatos a Full":
        st.caption(
            "Por qué productos empezar si se agranda el uso de Full, ordenados "
            "por el tamaño del premio: cuánta plata de envío quema cada uno "
            "por mes.")
        st.warning(
            "**En esta cuenta no hay ni una publicación en Full.** Las 438 "
            "activas están en depósito propio (`xd_drop_off`), así que no hay "
            "con qué comparar y esta vista queda como referencia de dónde se "
            "va la plata de envío, nada más. Lo que sí está medido es cuánto "
            "envío paga hoy cada producto.", icon="ℹ️")

        f1, f2 = st.columns([1.2, 3])
        dias_f = f1.selectbox("Período", [30, 60, 90], index=2,
                              format_func=lambda d: f"{d} días", key="d_full")
        if f2.button("Analizar candidatos", use_container_width=True):
            estado = st.empty()
            with st.spinner("Trayendo costos de envío..."):
                st.session_state["full"] = full.analizar(
                    ml, dias_f, pubs=pubs,
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        guardado_f = st.session_state.get("full")
        if guardado_f is not None:
            cand, foto = guardado_f
            if not len(foto):
                st.info("Sin datos suficientes de envío para comparar.")
            else:
                st.markdown("##### Dónde se paga el envío")
                st.caption(
                    "El dato que ordena todo: el envío se paga **desde "
                    "\\$1.000**, y el corte es limpio. Sobre 125 órdenes de 90 "
                    "días, ninguna de las 100 por debajo de \\$1.000 tuvo costo "
                    "de envío para vos; las 25 de \\$1.000 para arriba lo "
                    "tuvieron todas, con una mediana de \\$160.")
                st.dataframe(
                    foto, use_container_width=True, hide_index=True,
                    column_config={
                        "franja": "Franja de precio",
                        "sku_propios": "SKU propios",
                        "sku_en_full": "SKU en Full",
                        "envio_propio": st.column_config.NumberColumn(
                            "Envío/u propio", format="%.0f"),
                        "envio_full": st.column_config.NumberColumn(
                            "Envío/u Full", format="%.0f"),
                        "paga_envio": "Pagan envío",
                        "plata_envio_mes": st.column_config.NumberColumn(
                            "Plata en envío/mes", format="%.0f"),
                        "comparable": st.column_config.CheckboxColumn(
                            "¿Comparable?",
                            help=f"Necesita {full.MINIMO_POR_FRANJA}+ SKU de "
                                 "cada lado para poder comparar")})

                if len(cand):
                    st.metric("Plata de envío que juntan los candidatos",
                              pesos(cand["plata_envio_mensual"].sum()) + "/mes")
                    st.caption(
                        f"Candidatos: no están en Full, vendieron "
                        f"{full.MINIMO_UNIDADES}+ unidades en el período y "
                        f"pagan envío. Mirá la columna **u/mes**: un producto "
                        f"que quema mucho envío pero rota poco es mal "
                        f"candidato, porque el almacenamiento de Full se come "
                        f"la diferencia.")
                    st.dataframe(
                        cand[["sku", "titulo", "unidades_por_mes",
                              "precio_prom", "envio_prom", "envio_sobre_precio",
                              "plata_envio_mensual", "cobertura_envio"]],
                        use_container_width=True, height=420, hide_index=True,
                        column_config={
                            "sku": "SKU", "titulo": "Título",
                            "unidades_por_mes": st.column_config.NumberColumn(
                                "u/mes", format="%.0f"),
                            "precio_prom": st.column_config.NumberColumn(
                                "Precio", format="%.0f"),
                            "envio_prom": st.column_config.NumberColumn(
                                "Envío/u", format="%.0f"),
                            "envio_sobre_precio": st.column_config.NumberColumn(
                                "Envío / precio", format="percent"),
                            "plata_envio_mensual": st.column_config.NumberColumn(
                                "Envío/mes", format="%.0f"),
                            "cobertura_envio": st.column_config.NumberColumn(
                                "Cobertura", format="percent",
                                help="Qué proporción de las unidades tiene "
                                     "dato real de envío")})
                    st.download_button(
                        "Descargar los candidatos",
                        cand.to_csv(index=False).encode("utf-8"),
                        f"candidatos_full_{datetime.now():%Y%m%d}.csv",
                        "text/csv")
                else:
                    st.info("Ningún producto cumple las condiciones de "
                            "candidato en este período.")

    elif op == "Salud del catálogo":
        st.caption(
            "Qué hay que arreglar en los datos para que el resto de las "
            "herramientas funcione bien. Ordenado por lo que cada publicación "
            "vendió: arreglar la ficha de algo que vende 3.000 unidades vale "
            "más que la de algo que nunca vendió.")

        if st.button("Revisar el catálogo"):
            st.session_state["salud"] = salud.analizar(pubs)

        dfs = st.session_state.get("salud")
        if dfs is not None and len(dfs):
            res = salud.resumen(dfs)
            st.metric("Publicaciones con algo para arreglar", len(dfs))

            cols = st.columns(len(res) or 1)
            for c, (k, n) in zip(cols, sorted(res.items(), key=lambda x: -x[1])):
                c.metric(k.capitalize(), n)

            with st.expander("Qué rompe cada problema"):
                st.markdown(
                    "- **Sin SKU**: la publicación es invisible para las "
                    "herramientas de precio, stock, rentabilidad y espejos.\n"
                    "- **SKU contradictorio**: se resuelve por `SELLER_SKU`, "
                    "pero la discrepancia suele indicar carga descuidada y "
                    "puede apuntar al producto equivocado.\n"
                    "- **Sin código de barras**: no se puede comparar contra "
                    "la competencia.\n"
                    "- **Pausada con stock**: no vende y tiene mercadería "
                    "inmovilizada.\n"
                    "- **Activa sin stock**: ocupa lugar y no puede vender.")

            filtro_s = st.multiselect("Filtrar por problema", sorted(res),
                                      default=sorted(res))
            vs = dfs[dfs["problemas"].apply(
                lambda x: any(f in x for f in filtro_s))] if filtro_s else dfs

            st.dataframe(
                vs, use_container_width=True, height=420,
                column_config={
                    "item_id": "Publicación", "sku": "SKU", "titulo": "Título",
                    "estado": "Estado", "stock": "Stock",
                    "vendidas": "Vendidas",
                    "precio": st.column_config.NumberColumn(
                        "Precio", format="%.0f"),
                    "problemas": "Qué arreglar", "cuantos": None,
                    "prioridad": None})
            st.download_button("Descargar la lista",
                               vs.to_csv(index=False).encode("utf-8"),
                               f"salud_catalogo_{datetime.now():%Y%m%d}.csv",
                               "text/csv")
        elif dfs is not None:
            st.success("El catálogo no tiene problemas de datos. 👌")

    elif op == "Envíos":
        st.caption(
            "En qué productos se va la plata de envío. El caso típico es el "
            "producto voluminoso con precio bajo: paga el mismo envío que uno "
            "caro, pero sobre un precio mucho menor.")
        st.caption(
            "El costo es el que paga SUPRABOND (`senders[].cost`), no el "
            "comprador. Se muestrean unas ventas por SKU, así que la columna "
            "**cobertura** dice qué proporción tiene dato real.")

        e1, e2 = st.columns([1.2, 3])
        dias_e = e1.selectbox("Período", [30, 60, 90], index=2,
                              format_func=lambda d: f"{d} días", key="d_env")
        if e2.button("Analizar envíos", use_container_width=True):
            estado = st.empty()
            with st.spinner("Trayendo costos de envío..."):
                st.session_state["envios"] = envios.analizar(
                    ml, dias_e, callback=lambda m: estado.caption(str(m)))
            estado.empty()

        dfv = st.session_state.get("envios")
        if dfv is not None and len(dfv):
            pierde = int((dfv["diagnostico"] == "pierde_plata").sum())
            critico = int((dfv["diagnostico"] == "envio_critico").sum())
            v1, v2, v3 = st.columns(3)
            v1.metric("SKU medidos", len(dfv))
            v2.metric("Envío crítico (+35%)", critico)
            v3.metric("Pierden plata", pierde)

            if pierde:
                peor = dfv[dfv["diagnostico"] == "pierde_plata"].iloc[0]
                st.error(
                    f"**{pierde} productos pierden plata solo con el envío y "
                    f"la comisión**, antes de contar el costo de la "
                    f"mercadería. El peor: `{peor['sku']}` se vende a "
                    f"{pesos_md(peor['precio_prom'])} y el envío cuesta "
                    f"{pesos_md(peor['envio_prom'])}.", icon="🚚")

            solo_probl = st.checkbox("Ver solo los problemáticos", value=True)
            vv = (dfv[dfv["diagnostico"] != "normal"] if solo_probl else dfv)

            st.dataframe(
                vv, use_container_width=True, height=420,
                column_config={
                    "sku": "SKU",
                    "precio_prom": st.column_config.NumberColumn(
                        "Precio", format="%.0f"),
                    "envio_prom": st.column_config.NumberColumn(
                        "Envío", format="%.0f"),
                    "envio_sobre_precio": st.column_config.NumberColumn(
                        "Envío / precio", format="percent"),
                    "comision_prom": st.column_config.NumberColumn(
                        "Comisión", format="%.0f"),
                    "queda_antes_del_costo": st.column_config.NumberColumn(
                        "Queda", format="%.0f",
                        help="Antes de restar el costo de la mercadería"),
                    "margen_bruto": st.column_config.NumberColumn(
                        "Margen bruto", format="percent"),
                    "unidades_vendidas": "Unidades",
                    "cobertura_envio": st.column_config.NumberColumn(
                        "Cobertura", format="percent"),
                    "plata_en_envio": st.column_config.NumberColumn(
                        "Plata en envío", format="%.0f"),
                    "diagnostico": "Diagnóstico",
                    "ordenes": None, "comision_sobre_precio": None,
                    "cargos_totales": None, "items_sin_comision": None})
            st.download_button("Descargar el análisis",
                               vv.to_csv(index=False).encode("utf-8"),
                               f"envios_{datetime.now():%Y%m%d}.csv", "text/csv")

            st.info(
                "Qué hacer con los que pierden: subir el precio, dejar de "
                "ofrecer envío gratis, venderlos solo por cantidad, o "
                "discontinuarlos. Ojo con los de **pocas unidades**: puede ser "
                "un envío puntual al interior y no un patrón.", icon="💡")

    elif op == "Factura de ML":
        st.caption(
            "MercadoLibre te factura entre \\$22M y \\$35M por mes. Cada orden "
            "trae la comisión que ML se cobró por esa venta. Esto compara las "
            "dos cosas, período por período.")
        st.info(
            "**No es una auditoría contable.** La factura incluye conceptos "
            "que no salen de las órdenes (envíos, publicidad, cargos por "
            "publicación), así que es normal que sea mayor. Lo que importa es "
            "si esa proporción **se mantiene estable**: un salto repentino es "
            "lo que amerita revisar.", icon="🧾")

        n_per = st.selectbox("Períodos a comparar", [3, 4, 6], index=0)
        if st.button("Conciliar"):
            estado = st.empty()
            with st.spinner("Trayendo facturación y órdenes..."):
                st.session_state["concil"] = conciliacion.conciliar(
                    ml, n_per, callback=lambda m: estado.caption(str(m)))
            estado.empty()

        dfk = st.session_state.get("concil")
        if dfk is not None and len(dfk):
            ult = dfk.iloc[0]
            n1, n2, n3 = st.columns(3)
            n1.metric("Último período facturado", pesos(ult["facturado_ml"]))
            n2.metric("Son comisiones de venta",
                      pesos(ult["comisiones_calculadas"]))
            n3.metric("Otros conceptos", pesos(ult["otros_conceptos"]),
                      f"{ult['proporcion_otros']:.0%} del total")

            if "alerta" in dfk and dfk["alerta"].any():
                st.warning(
                    "Hay períodos que se desvían más de 10 puntos del "
                    "promedio. Vale revisar qué cambió: publicidad nueva, "
                    "cargos por publicación o ajustes.", icon="⚠️")
            else:
                st.success(
                    "La proporción se mantiene estable entre períodos: no hay "
                    "señales de un cobro fuera de lo normal.")

            st.dataframe(
                dfk, use_container_width=True,
                column_config={
                    "periodo": "Período",
                    "facturado_ml": st.column_config.NumberColumn(
                        "ML facturó", format="%.0f"),
                    "comisiones_calculadas": st.column_config.NumberColumn(
                        "Comisiones de venta", format="%.0f"),
                    "otros_conceptos": st.column_config.NumberColumn(
                        "Otros conceptos", format="%.0f"),
                    "proporcion_otros": st.column_config.NumberColumn(
                        "% otros", format="percent"),
                    "desvio_vs_promedio": st.column_config.NumberColumn(
                        "Desvío", format="percent"),
                    "impago": st.column_config.NumberColumn(
                        "Impago", format="%.0f"),
                    "ordenes": "Órdenes", "unidades": "Unidades",
                    "alerta": "Revisar"})
            st.download_button("Descargar la conciliación",
                               dfk.to_csv(index=False).encode("utf-8"),
                               f"conciliacion_{datetime.now():%Y%m%d}.csv",
                               "text/csv")

    elif op == "Precios espejo":
        st.caption(
            "Casi la mitad del catálogo son publicaciones duplicadas del mismo "
            "producto. Cuando dos tienen precios distintos, **competís contra "
            "vos mismo**: el que compara compra la más barata y la otra no "
            "vende nunca.")
        st.caption(
            "Las Premium se comparan solo contra Premium: es esperable que "
            "valgan más, porque pagan ~12 puntos más de comisión. El precio "
            "sugerido es el de la publicación **que más vendió** del grupo.")

        if st.button("Buscar precios desincronizados"):
            with st.spinner("Comparando..."):
                st.session_state["espejos"] = espejos.analizar(pubs)

        dfe = st.session_state.get("espejos")
        if dfe is not None and len(dfe):
            caras = int((dfe["diferencia"] > 0).sum())
            e1, e2, e3 = st.columns(3)
            e1.metric("Publicaciones a emparejar", len(dfe))
            e2.metric("SKU afectados", dfe["sku"].nunique())
            e3.metric("Más caras que su gemela", caras)

            if caras:
                st.warning(
                    f"**{caras} publicaciones están más caras que otra igual "
                    "tuya.** Salvo que haya un motivo, esas no venden: el "
                    "comprador elige la barata.", icon="🔀")

            st.dataframe(
                dfe, use_container_width=True, height=420,
                column_config={
                    "sku": "SKU", "tipo": "Tipo", "item_id": "Publicación",
                    "titulo": "Título",
                    "precio_actual": st.column_config.NumberColumn(
                        "Precio hoy", format="%.0f"),
                    "precio_sugerido": st.column_config.NumberColumn(
                        "Sugerido", format="%.0f"),
                    "diferencia": st.column_config.NumberColumn(
                        "Diferencia", format="percent"),
                    "vendidas": "Vendidas",
                    "vendidas_referencia": "Vendidas (referencia)",
                    "publicaciones_del_grupo": "En el grupo",
                    "spread_del_grupo": st.column_config.NumberColumn(
                        "Spread", format="percent"),
                    "riesgo": "Qué pasa"})

            st.download_button(
                "Descargar para la sección Precios",
                dfe[["item_id", "precio_sugerido"]].rename(
                    columns={"item_id": "MLU", "precio_sugerido": "Precio"}
                ).to_csv(index=False).encode("utf-8"),
                f"espejos_{datetime.now():%Y%m%d}.csv", "text/csv",
                help="Sale por MLU y no por SKU: acá cada publicación lleva su "
                     "propio precio, no todas el mismo")
        elif dfe is not None:
            st.success("No hay publicaciones espejo con precios distintos. 👌")

    elif op == "Tramos de comisión":
        st.caption(
            "MercadoLibre cobra un porcentaje **más un cargo fijo por unidad**, "
            "y ese cargo salta en escalones de precio. En Uruguay el escalón "
            "de \\$1.000 **es una trampa, no una oportunidad**: ahí el cargo "
            "fijo desaparece, pero el envío pasa a pagarlo el vendedor y "
            "cuesta bastante más de lo que ahorrás.")

        with st.expander("Los escalones de tu cuenta, y por qué el de $1.000 conviene esquivarlo"):
            st.markdown(
                "| Precio | Cargo fijo | Envío |\n|---|---|---|\n"
                "| menos de \\$500 | \\$15 | lo paga el comprador |\n"
                "| \\$500 a \\$749 | \\$25 | lo paga el comprador |\n"
                "| \\$750 a \\$999 | \\$40 | lo paga el comprador |\n"
                "| **\\$1.000 o más** | **\\$0** | **lo pagás vos (~\\$160)** |\n\n"
                "Los cargos fijos están medidos contra la API por búsqueda "
                "binaria. El umbral del envío está medido sobre las ventas "
                "reales de 90 días, y el corte es limpio: por debajo de "
                "\\$1.000, **ninguna** de 100 órdenes tuvo costo de envío para "
                "vos; desde \\$1.000, **las 25 de 25** lo tuvieron.\n\n"
                "Por eso cruzar \\$1.000 cuesta unos \\$154 por unidad: "
                "ahorrás \\$40 de cargo fijo y te hacés cargo de \\$160 de "
                "envío. Para empatar habría que llegar a \\$1.178, un 18% más "
                "caro.\n\n"
                "**La oportunidad está al revés que en Argentina**: los "
                "productos que hoy están apenas por encima de \\$1.000 dejan "
                "más plata bajando a \\$999 — y encima se venden más baratos.")

        if st.button("Analizar el catálogo"):
            with st.spinner("Calculando..."):
                st.session_state["tramos"] = tramos.analizar(pubs)

        dft = st.session_state.get("tramos")
        if dft is not None and len(dft):
            t1, t2, t3 = st.columns(3)
            t1.metric("Publicaciones a reprecificar", len(dft))
            t2.metric("Mejor caso por unidad",
                      pesos(dft["gana_por_unidad"].max()))
            esquivan = int((dft["motivo"] == "baja para esquivar el envío").sum())
            t3.metric("Esquivan el envío bajando", esquivan)

            corte = st.slider("Ver solo las que ganan al menos, por unidad",
                              0, 100, 10, step=5,
                              help="En pesos uruguayos. Con 0 se ven todas.")
            v = dft[dft["gana_por_unidad"] >= corte]

            st.dataframe(
                v, use_container_width=True, height=420,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "precio_actual": st.column_config.NumberColumn(
                        "Precio hoy", format="%.0f"),
                    "precio_sugerido": st.column_config.NumberColumn(
                        "Precio sugerido", format="%.0f"),
                    "cambia_precio": st.column_config.NumberColumn(
                        "Cambia", format="percent"),
                    "motivo": "Por qué",
                    "gana_por_unidad": st.column_config.NumberColumn(
                        "Ganás por unidad", format="%.0f"),
                    "neto_actual": st.column_config.NumberColumn(
                        "Neto hoy", format="%.0f"),
                    "neto_sugerido": st.column_config.NumberColumn(
                        "Neto nuevo", format="%.0f"),
                    "cargo_fijo_actual": st.column_config.NumberColumn(
                        "Fijo hoy", format="%.0f"),
                    "cargo_fijo_nuevo": st.column_config.NumberColumn(
                        "Fijo nuevo", format="%.0f"),
                    "envio_actual": st.column_config.NumberColumn(
                        "Envío hoy", format="%.0f"),
                    "envio_nuevo": st.column_config.NumberColumn(
                        "Envío nuevo", format="%.0f"),
                    "vendidos": "Vendidas", "impacto": None, "item_id": None})

            st.download_button(
                "Descargar para usar en la sección Precios",
                v[["sku", "precio_sugerido"]].rename(
                    columns={"precio_sugerido": "Precio"}
                ).to_csv(index=False).encode("utf-8"),
                f"precios_sugeridos_{datetime.now():%Y%m%d}.csv", "text/csv",
                help="Sale con las columnas SKU y Precio, listo para subir en "
                     "la sección Precios")

            st.info(
                "Antes de aplicar: subir un precio puede bajar la conversión. "
                "Conviene empezar por las que **más ganan por unidad y menos "
                "suben** — las que ya están cerca del escalón.", icon="💡")

            # ------------------------------------------- aplicar desde acá
            st.divider()
            st.markdown("##### Aplicar los precios desde acá")
            aviso_piso_de_marca()
            st.caption(
                "Antes de escribir nada se vuelve a leer el precio actual de "
                "cada publicación en MercadoLibre. El análisis sale del "
                "catálogo cacheado, que puede tener días: si un precio se "
                "movió, la sugerencia de la tabla ya no es la que corresponde.")

            if st.button("Preparar el plan", key="plan_tr"):
                estado_tr = st.empty()
                with st.spinner("Releyendo precios en MercadoLibre..."):
                    st.session_state["plan_tramos"] = tramos.plan(
                        ml, v, pisos=pisos_de_marca(pubs),
                        callback=lambda m: estado_tr.caption(str(m)))
                estado_tr.empty()
                st.session_state.pop("res_tramos", None)

            plan_tr = st.session_state.get("plan_tramos")
            if plan_tr is not None and len(plan_tr):
                van = plan_tr[plan_tr["accion"] == "aplicar"]
                fuera = plan_tr[plan_tr["accion"] == "omitir"]

                pt1, pt2 = st.columns(2)
                pt1.metric("Se aplican", len(van))
                pt2.metric("Quedan afuera", len(fuera))

                if len(fuera):
                    st.markdown("**Por qué quedan afuera**")
                    st.dataframe(
                        fuera[["sku", "titulo", "precio_pantalla",
                               "precio_actual", "motivo"]],
                        use_container_width=True, hide_index=True, height=200,
                        column_config={
                            "sku": "SKU", "titulo": "Título",
                            "precio_pantalla": st.column_config.NumberColumn(
                                "Precio en la tabla", format="%.0f"),
                            "precio_actual": st.column_config.NumberColumn(
                                "Precio real ahora", format="%.0f"),
                            "motivo": "Motivo"})

                if not len(van):
                    st.info("No queda ninguna para aplicar.")
                else:
                    movidas = int((van["precio_pantalla"].round(2)
                                   != van["precio_actual"].round(2)).sum())
                    if movidas:
                        st.warning(
                            f"**{movidas} publicaciones cambiaron de precio "
                            "desde que corriste el análisis.** La sugerencia "
                            "se recalculó sobre el precio de ahora.",
                            icon="🔄")

                    cruzan = int(van["cruza_umbral"].sum())
                    if cruzan:
                        st.caption(
                            f"{cruzan} cruzan el umbral de \\$1.000 hacia "
                            "abajo. En esas, después de bajar el precio se "
                            "vuelve a leer el envío: si MercadoLibre no se lo "
                            "sacó de encima al vendedor, el cambio deja de "
                            "convenir y esa publicación se revierte sola.")

                    st.dataframe(
                        van[["sku", "titulo", "precio_actual", "precio_nuevo",
                             "cambia_precio", "gana_por_unidad", "vendidos",
                             "cruza_umbral"]],
                        use_container_width=True, hide_index=True, height=280,
                        column_config={
                            "sku": "SKU", "titulo": "Título",
                            "precio_actual": st.column_config.NumberColumn(
                                "Precio hoy", format="%.0f"),
                            "precio_nuevo": st.column_config.NumberColumn(
                                "Precio nuevo", format="%.0f"),
                            "cambia_precio": st.column_config.NumberColumn(
                                "Cambia", format="percent"),
                            "gana_por_unidad": st.column_config.NumberColumn(
                                "Ganás por unidad", format="%.0f"),
                            "vendidos": "Vendidas",
                            "cruza_umbral": "Cruza el umbral"})

                    st.error(
                        "**Esto cambia los precios en MercadoLibre de "
                        "verdad.** Cada cambio queda en la auditoría con el "
                        "precio anterior.", icon="⚠️")
                    ot1, ot2 = st.columns([2, 3])
                    op_tr = ot1.text_input("Tu nombre (queda en el registro)",
                                           key="op_tr")
                    conf_tr = ot2.checkbox(
                        f"Confirmo que quiero cambiar el precio de {len(van)} "
                        "publicaciones", key="conf_tr")

                    if st.button("Aplicar los precios", key="go_tr",
                                 disabled=not (conf_tr and op_tr.strip())):
                        barra_tr = st.progress(0.0, text="Aplicando...")
                        st.session_state["res_tramos"] = tramos.aplicar(
                            ml, plan_tr, operador=op_tr.strip(),
                            callback=lambda i, t, f: barra_tr.progress(
                                i / t, text=f"Aplicando {i} de {t}: "
                                            f"{f['item_id']}"))
                        barra_tr.empty()
                        st.session_state.pop("tramos", None)
                        st.session_state.pop("plan_tramos", None)

            res_tr = st.session_state.get("res_tramos")
            if res_tr is not None and len(res_tr):
                ok_tr = int((res_tr["resultado"] == "OK").sum())
                revertidas = int(res_tr["resultado"].str.startswith(
                    "REVERTIDA").sum())
                fallo_vuelta = int((res_tr["resultado"] == "REVERTIR FALLÓ").sum())

                if ok_tr == len(res_tr):
                    st.success(f"{ok_tr} precios actualizados.")
                else:
                    st.error(f"{ok_tr} aplicados de {len(res_tr)}.")
                if revertidas:
                    st.warning(
                        f"**{revertidas} se revirtieron solas**: MercadoLibre "
                        "no le sacó el envío de encima al vendedor, así que "
                        "bajar el precio pasaba a costar plata.", icon="↩️")
                if fallo_vuelta:
                    st.error(
                        f"**{fallo_vuelta} quedaron con el precio nuevo y el "
                        "envío a tu cargo, y la vuelta atrás falló.** Hay que "
                        "corregirlas a mano — están abajo.", icon="🚨")
                st.dataframe(res_tr, use_container_width=True, hide_index=True)
                st.caption("Volvé a analizar el catálogo para ver el estado "
                           "nuevo.")

    else:
        st.caption(
            "Cruza cuántas veces vieron cada publicación contra cuánto vendió. "
            "Detecta lo que se ve y no vende (precio, fotos o descripción) y "
            "lo que vende sin exposición (candidatas a empujar).")
        st.warning(
            "MercadoLibre solo deja consultar las visitas **de a una "
            "publicación por vez**, así que este análisis hace ~438 llamadas "
            "y tarda unos 10 minutos. Queda cacheado por rango de fechas.",
            icon="⏳")

        c1, c2 = st.columns([1.2, 3])
        dias_c = c1.selectbox("Período", [15, 30, 60], index=1,
                              format_func=lambda d: f"{d} días")
        if c2.button("Analizar visitas y ventas"):
            estado = st.empty()
            with st.spinner("Esto tarda varios minutos..."):
                st.session_state["conv"] = conversion.analizar(
                    ml, dias_c, callback=lambda m: estado.caption(str(m)))
            estado.empty()

        dfc = st.session_state.get("conv")
        if dfc is not None and len(dfc):
            conv_med = dfc.attrs.get("conversion_mediana", 0)
            k1, k2, k3 = st.columns(3)
            k1.metric("Visitas del período", f"{int(dfc['visitas'].sum()):,}"
                      .replace(",", "."))
            k2.metric("Conversión mediana", f"{conv_med:.2%}")
            k3.metric("Se ven y no venden",
                      int((dfc["diagnostico"] == "no_vende").sum()))

            perdidas = int(dfc[dfc["diagnostico"] == "no_vende"]["visitas"].sum())
            if perdidas:
                st.warning(f"**{perdidas:,} visitas se fueron sin comprar** en "
                           "publicaciones que no vendieron ni una unidad."
                           .replace(",", "."), icon="📉")

            diag = st.multiselect(
                "Ver", sorted(dfc["diagnostico"].unique()),
                default=[d for d in ["no_vende", "convierte_poco", "escalar",
                                     "falta_exposicion"]
                         if d in dfc["diagnostico"].unique()])
            vc = dfc[dfc["diagnostico"].isin(diag)] if diag else dfc

            st.dataframe(
                vc, use_container_width=True, height=420,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "precio": st.column_config.NumberColumn("Precio", format="%.0f"),
                    "visitas": "Visitas", "unidades": "Vendidas",
                    "conversion": st.column_config.NumberColumn(
                        "Conversión", format="percent"),
                    "importe": st.column_config.NumberColumn(
                        "Facturado", format="%.0f"),
                    "diagnostico": "Diagnóstico",
                    "recomendacion": "Qué hacer",
                    "item_id": None, "medida": None, "stock": "Stock"})
            st.download_button("Descargar el análisis",
                               vc.to_csv(index=False).encode("utf-8"),
                               f"conversion_{datetime.now():%Y%m%d}.csv",
                               "text/csv")

elif seccion == "Preguntas":
    st.markdown("#### Respuestas automáticas con IA")

    # La Sheet se lee una vez por minuto, no en cada interacción: leerla en
    # cada render es lento y hace pegarle al límite de la API de Google.
    @st.cache_data(ttl=60, show_spinner=False)
    def _met(con_historial):
        return preg.metricas(incluir_historial=con_historial)

    try:
        cfg = preg.config()
        activa = preg.ia_activa()
    except Exception as e:
        st.error(f"No pude leer la configuración de la planilla: {e}")
        st.stop()

    met = _met(False)
    if met.get("error"):
        st.warning(f"Los contadores no se pudieron actualizar: {met['error']}",
                   icon="📊")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Respondidas por la IA", met["respondidas_ia"],
              help="Preguntas que la IA contestó y publicó sola")
    m2.metric("Resueltas a mano", met.get("resueltas_a_mano", 0),
              help="Las que respondió una persona desde Gestión manual")
    m3.metric("Esperando respuesta", met["derivadas_a_persona"],
              help="Siguen abiertas: miralas en Gestión manual")
    m4.metric("Se resolvieron solas",
              f"{met['tasa_automatica']:.0%}" if met["respondidas_ia"] +
              met["derivadas_a_persona"] else "—",
              help="Del total que procesó la IA, cuántas pudo cerrar sin ayuda")

    c1, c2, c3 = st.columns([1.3, 1.3, 2])
    c1.metric("Estado", "Activa" if activa else "Apagada")
    c2.metric("Confianza mínima", cfg.get("min_confianza", "media").capitalize())
    c3.caption(f"Firma: **{cfg.get('firma','')}**  \nSe cambia en la hoja "
               f"`{preg.HOJA_CONFIG}` de la planilla.")

    if not activa:
        st.warning("La IA está **apagada**. Poné `ia_activa = si` en la hoja "
                   f"`{preg.HOJA_CONFIG}` para que vuelva a responder.", icon="⏸️")

    vista_p = st.radio("Vista", ["Dashboard", "Gestión manual",
                                 "Historial completo", "Registro de la IA",
                                 "Fuentes"],
                       horizontal=True, label_visibility="collapsed")

    if vista_p == "Dashboard":
        st.caption(
            "Redacta con el historial de respuestas de la cuenta, los datos de "
            "la publicación y las fuentes cargadas. **Si el contexto no alcanza, "
            "no responde**: deja la pregunta para que la vea una persona.")

        # Solo las contestables: las de publicaciones inactivas no entran al
        # circuito en ningún lado.
        pend = preg.pendientes_respondibles(ml)
        st.metric("Preguntas sin responder", len(pend))
        if pend:
            with st.expander("Ver las preguntas pendientes"):
                for q in pend:
                    st.markdown(f"- `{q['id']}` · {(q.get('text') or '')[:160]}")

        b1, b2 = st.columns(2)
        simular = b1.button("Redactar sin publicar", use_container_width=True,
                            disabled=not pend)
        aplicar = b2.button("Redactar y PUBLICAR", use_container_width=True,
                            disabled=not pend or not activa)

        if simular or aplicar:
            barra = st.progress(0.0, text="Trabajando...")
            r = preg.procesar(
                ml, publicar_de_verdad=aplicar,
                callback=lambda i, t_, q: barra.progress(
                    i / max(t_, 1), text=f"Pregunta {i} de {t_}..."))
            barra.empty()
            st.session_state["preg_res"] = r

        r = st.session_state.get("preg_res")
        if r:
            if "error" in r:
                st.error(r["error"])
            else:
                res = pd.DataFrame(r["resultados"])
                if len(res):
                    pub = (res["estado"] == "publicada").sum()
                    rev = (res["estado"] == "para_revisar").sum()
                    sim = (res["estado"] == "simulada").sum()
                    if pub:
                        st.success(f"{pub} respuestas publicadas en MercadoLibre.")
                    if sim:
                        st.info(f"{sim} redactadas (no se publicaron: fue una prueba).")
                    inact = (res["estado"] == "publicacion_inactiva").sum()
                    if inact:
                        st.info(
                            f"**{inact} no se pudieron responder porque la "
                            "publicación está pausada.** MercadoLibre no lo "
                            "permite. Si la reactivás, se responden en la "
                            "próxima corrida.", icon="⏸️")
                    err = (res["estado"] == "error_tecnico").sum()
                    if err:
                        motivo = res[res["estado"] == "error_tecnico"].iloc[0]["motivo"]
                        st.error(
                            f"**{err} fallaron por un problema técnico**, no "
                            "porque faltara contexto. Hay que corregir esto "
                            f"antes de volver a intentar:\n\n> {motivo}",
                            icon="🔧")
                    if rev:
                        st.warning(
                            f"**{rev} quedaron sin responder** porque el "
                            "contexto no alcanzaba. Están en "
                            "**Gestión manual**: ahí las respondés y se "
                            "publican.",
                            icon="👤")
                    for _, f in res.iterrows():
                        with st.container(border=True):
                            st.markdown(f"**{f['estado']}** · confianza "
                                        f"{f['confianza']} · `{f['question_id']}`")
                            st.markdown(f"**P:** {f['pregunta']}")
                            st.markdown(f"**R:** {f['respuesta'] or '_(no respondió)_'}")
                            st.caption(f"Motivo: {f['motivo']}")

    elif vista_p == "Gestión manual":
        st.caption(
            "**Todas** las preguntas sin responder de la cuenta, las haya "
            "tocado la IA o no. Escribí la respuesta y se publica en "
            "MercadoLibre; también podés pedirle un borrador a la IA para esa "
            "pregunta puntual. Las que alguien ya contestó desde el panel de "
            "ML desaparecen solas.")

        if st.button("↻ Actualizar la bandeja"):
            st.session_state.pop("preg_band", None)

        if "preg_band" not in st.session_state:
            try:
                with st.spinner("Buscando pendientes..."):
                    st.session_state["preg_band"] = preg.bandeja(ml)
            except Exception as e:
                st.error(f"No pude leer los pendientes: {e}")
                st.session_state["preg_band"] = []
        band = st.session_state["preg_band"]

        if not band:
            st.success("No queda ninguna pregunta pendiente. 🎉")
        else:
            st.metric("Esperando una respuesta", len(band))
            nombre = st.text_input("Tu nombre (queda en el registro)",
                                   key="op_band")

            for b in band:
                with st.container(border=True):
                    st.markdown(f"**{b['pregunta']}**")
                    st.caption(f"{b['comprador']} · {b['fecha']} · "
                               f"publicación `{b['item_id']}`")

                    if b["estado"] == "publicacion_inactiva":
                        st.info("La publicación está pausada. MercadoLibre no "
                                "deja responder hasta que la reactives.",
                                icon="⏸️")
                    elif b["motivo"]:
                        st.caption(f"La IA no respondió porque: {b['motivo']}")

                    # Un borrador pedido a mano pisa lo que hubiera antes.
                    clave_borr = f"borr_{b['question_id']}"
                    valor = st.session_state.get(clave_borr, b["borrador"])

                    texto = st.text_area(
                        "Tu respuesta", value=valor, height=110,
                        key=f"resp_{b['question_id']}",
                        placeholder="Escribí acá la respuesta que se va a "
                                    "publicar en MercadoLibre...")

                    c_a, c_ia, c_b = st.columns([1, 1.4, 2.6])
                    if c_ia.button("✨ Sugerir con IA",
                                   key=f"ia_{b['question_id']}",
                                   help="Le pide un borrador a la IA para esta "
                                        "pregunta. No publica nada: lo editás vos."):
                        with st.spinner("Redactando..."):
                            txt, aviso = preg.borrador(
                                ml, b["question_id"], b["item_id"],
                                b["pregunta"], b["comprador"])
                        if txt:
                            st.session_state[clave_borr] = txt
                            if aviso:
                                st.warning(aviso, icon="⚠️")
                            st.rerun()
                        else:
                            st.info(aviso or "La IA no pudo redactar nada.")

                    if c_a.button("Publicar", key=f"pub_{b['question_id']}",
                                  disabled=not nombre.strip()
                                  or b["estado"] == "publicacion_inactiva"):
                        ok, det = preg.responder_a_mano(
                            ml, b["question_id"], texto, nombre,
                            item_id=b["item_id"], pregunta=b["pregunta"],
                            motivo_previo=b["motivo"])
                        if ok:
                            st.success("Publicada." + (f" {det}" if det else ""))
                            st.session_state.pop(clave_borr, None)
                            st.session_state.pop("preg_band", None)
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(f"No se pudo publicar: {det}")
                    c_b.markdown(
                        f"[Ver la publicación en MercadoLibre]"
                        f"(https://articulo.mercadolibre.com.uy/"
                        f"{str(b['item_id']).replace('MLU','MLU-')}) ↗")

    elif vista_p == "Historial completo":
        st.caption(
            "Todas las preguntas de la cuenta con su respuesta, hayan sido "
            "contestadas por la IA o por una persona. Vive en la hoja "
            f"`{preg.HOJA_HISTORIAL}` de la planilla.")

        if st.button("↻ Sincronizar con MercadoLibre"):
            estado = st.empty()
            with st.spinner("Trayendo preguntas..."):
                r = preg.sincronizar_historial(
                    ml, callback=lambda m: estado.caption(m))
            estado.empty()
            if r["ok"]:
                st.success(f"{r['nuevas']} preguntas nuevas y "
                           f"{r['actualizadas']} actualizadas. "
                           f"Total en la planilla: {r['total']}.")
            else:
                st.error(f"No se pudo guardar: {r['detalle']}")
            st.session_state.pop("preg_hist", None)

        if "preg_hist" not in st.session_state:
            try:
                st.session_state["preg_hist"] = pd.DataFrame(preg.historial())
            except Exception as e:
                st.error(f"No pude leer el historial de la planilla: {e}")
                st.session_state["preg_hist"] = pd.DataFrame()
        hc = st.session_state["preg_hist"]

        if not len(hc):
            st.info("Todavía no hay historial. Apretá **Sincronizar** para "
                    "traerlo de MercadoLibre.")
        else:
            meth = _met(True)
            h1, h2, h3 = st.columns(3)
            h1.metric("Preguntas", meth["historial_total"])
            h2.metric("Respondidas por la IA", meth["historial_por_ia"])
            h3.metric("Respondidas por una persona",
                      meth["historial_por_persona"])

            f1, f2 = st.columns([2, 2])
            with f1:
                quien = st.multiselect(
                    "Quién respondió",
                    sorted(x for x in hc["respondida_por"].unique() if x),
                    default=sorted(x for x in hc["respondida_por"].unique() if x))
            with f2:
                buscar_q = st.text_input("Buscar en la pregunta o la respuesta")

            vista_h = hc[hc["respondida_por"].isin(quien)] if quien else hc
            if buscar_q:
                m_ = (vista_h["pregunta"].str.contains(buscar_q, case=False, na=False)
                      | vista_h["respuesta"].str.contains(buscar_q, case=False,
                                                          na=False))
                vista_h = vista_h[m_]

            st.caption(f"{len(vista_h)} preguntas")
            st.dataframe(vista_h.iloc[::-1], use_container_width=True, height=440,
                         column_config={
                             "question_id": "ID", "fecha_pregunta": "Fecha",
                             "item_id": "Publicación", "publicacion": "Título",
                             "comprador": "Comprador", "pregunta": "Pregunta",
                             "respuesta": "Respuesta",
                             "respondida_por": "Respondió",
                             "estado_ml": "Estado", "sincronizado": "Sincronizado"})
            st.download_button(
                "Descargar el historial completo",
                vista_h.to_csv(index=False).encode("utf-8"),
                f"historial_preguntas_{datetime.now():%Y%m%d}.csv", "text/csv")

    elif vista_p == "Registro de la IA":
        st.caption("Solo lo que procesó la IA, con el motivo de cada decisión.")
        try:
            hist = pd.DataFrame(almacen.leer_hoja(preg.HOJA_RESPUESTAS,
                                                  preg.COLS_RESPUESTAS))
        except Exception as e:
            st.error(f"No pude leer el registro de la planilla: {e}")
            hist = pd.DataFrame()
        if not len(hist):
            st.info("Todavía no hay respuestas registradas.")
        else:
            st.caption(f"{len(hist)} registros · todo lo publicado queda acá")
            st.dataframe(hist.iloc[::-1], use_container_width=True, height=440)
            st.download_button("Descargar el historial",
                               hist.to_csv(index=False).encode("utf-8"),
                               f"respuestas_ia_{datetime.now():%Y%m%d}.csv",
                               "text/csv")

    else:
        st.caption("Documentos y sitios que la IA usa como referencia, además "
                   "del historial de respuestas de la cuenta.")

        f1, f2 = st.columns(2)
        with f1:
            st.markdown("##### Subir un documento")
            doc = st.file_uploader("Ficha técnica, manual, tabla (.pdf o .txt)",
                                   type=["pdf", "txt", "md"], key="up_doc")
            op_doc = st.text_input("Tu nombre", key="op_doc")
            if doc and op_doc.strip() and st.button("Cargar documento"):
                try:
                    texto = (preg.leer_pdf(doc) if doc.name.lower().endswith(".pdf")
                             else doc.getvalue().decode("utf-8", "ignore"))
                    if not texto.strip():
                        st.error("No pude extraer texto (¿es un PDF escaneado?).")
                    else:
                        preg.agregar_fuente("documento", doc.name, texto,
                                            operador=op_doc.strip())
                        st.success(f"Cargado: {len(texto):,} caracteres."
                                   .replace(",", "."))
                except Exception as e:
                    st.error(f"No pude leer el archivo: {e}")

        with f2:
            st.markdown("##### Agregar un sitio")
            url = st.text_input("URL (ej: una ficha técnica online)")
            op_web = st.text_input("Tu nombre", key="op_web")
            if url and op_web.strip() and st.button("Traer la página"):
                try:
                    titulo, texto = preg.bajar_web(url)
                    preg.agregar_fuente("web", titulo, texto, url=url,
                                        operador=op_web.strip())
                    st.success(f"Cargado «{titulo}»: {len(texto):,} caracteres."
                               .replace(",", "."))
                except Exception as e:
                    st.error(f"No pude traer la página: {e}")

        st.divider()
        try:
            fs = pd.DataFrame(preg.fuentes())
        except Exception as e:
            st.error(f"No pude leer las fuentes: {e}")
            fs = pd.DataFrame()
        if len(fs):
            st.dataframe(fs.drop(columns=["contenido"], errors="ignore"),
                         use_container_width=True)
        else:
            st.info("Todavía no hay fuentes cargadas. El historial de "
                    "respuestas de la cuenta se usa igual.")

elif seccion == "Rentabilidad":
    st.markdown("#### Rentabilidad por SKU")
    st.caption(
        "Subí una planilla con el **costo** de cada SKU. La herramienta le suma "
        "el precio de venta actual en MercadoLibre y los cargos reales que cobró "
        "ML en las ventas históricas de ese SKU (comisión, recargo por "
        "financiación, cargo fijo y envío).")

    costos_rent = bloque_costos("rent")

    c1, c2, c3 = st.columns(3)
    with c1:
        dias = st.selectbox("Historia a considerar", [30, 60, 90, 180],
                            index=2, key="dias_rent")
    with c2:
        con_envios = st.checkbox("Incluir costo de envío", value=True,
                                 help="Consulta el costo real de envío de una "
                                      "muestra de ventas por SKU. Tarda más.")
    with c3:
        # 22% por defecto: los costos de SUPRABOND se cargan SIN IVA y los
        # precios de ML lo incluyen. Con "Sin descontar" el margen sale
        # inflado en 22 puntos, que es muchisimo.
        iva = st.selectbox("IVA a descontar del precio", [0.22, 0.10, 0.0],
                           format_func=lambda x: f"{x:.1%}" if x else "Sin descontar",
                           help="La planilla de costos de SUPRABOND está SIN "
                                "IVA y los precios de ML lo incluyen, así que "
                                "corresponde descontarlo. Ponelo en 'Sin "
                                "descontar' solo si cambiás a costos con IVA.")

    otros_rent = controles_otros_conceptos("rent")

    if costos_rent is not None and st.button("Calcular rentabilidad"):
        costos = costos_rent

        with st.spinner(f"Trayendo ventas de los últimos {dias} días..."):
            ordenes = rent.traer_historico(ml, dias)

        envios = None
        if con_envios:
            barra = st.progress(0.0, text="Trayendo costos de envío...")
            envios = rent.traer_costos_envio(
                ml, ordenes, muestra_por_sku=5,
                callback=lambda i, t: barra.progress(
                    min(i / max(t, 1), 1.0), text=f"Costos de envío {i}/{t}..."))
            barra.empty()

        # El precio de lista no siempre es lo que paga el comprador: ~12% de
        # las publicaciones tiene una promocion encima.
        barra = st.progress(0.0, text="Consultando precios reales de venta...")
        ids = rent.items_de_costos(costos, pubs)
        precios_venta = rent.precios_reales(
            ml, ids,
            callback=lambda i, t: barra.progress(min(i / max(t, 1), 1.0),
                                                 text=f"Precios reales {i}/{t}..."))
        barra.empty()

        cargos = rent.cargos_por_sku(ordenes, envios)
        st.session_state["rent"] = rent.calcular(
            costos, cargos, pubs, iva=iva, precios_venta=precios_venta,
            otros_conceptos=otros_rent)

    df = st.session_state.get("rent")
    if df is not None and len(df):
        con_datos = df[df["margen_pct"].notna()]

        m1, m2, m3 = st.columns(3)
        m1.metric("SKU analizados", len(df))
        m2.metric("Margen promedio",
                  f"{con_datos['margen_pct'].mean():.1%}" if len(con_datos) else "—")
        m3.metric("SKU con margen negativo",
                  int((con_datos["margen_pct"] < 0).sum()) if len(con_datos) else 0)

        negativos = con_datos[con_datos["margen_pct"] < 0]
        if len(negativos):
            st.error(f"**{len(negativos)} SKU se venden a pérdida.** "
                     "Están primeros en la tabla.")

        sin_precio = df[df["precio_ml"].isna()]
        if len(sin_precio):
            st.warning(f"{len(sin_precio)} SKU de la planilla no tienen "
                       "publicación activa en MercadoLibre.")

        en_promo = df[df.get("en_promo", False) == True]  # noqa: E712
        if len(en_promo):
            st.info(
                f"**{len(en_promo)} SKU tienen una promoción activa.** El margen "
                "está calculado sobre lo que realmente paga el comprador, que es "
                "menor al precio de lista. Mirá la columna *Precio lista* para "
                "comparar.", icon="🏷️")

        st.dataframe(
            df, use_container_width=True, height=420,
            column_config={
                "sku": "SKU",
                "item_id": "Publicación",
                "tipo": "Tipo",
                "precio_ml": st.column_config.NumberColumn(
                    "Precio real", format="%.0f",
                    help="Lo que realmente paga el comprador hoy"),
                "precio_lista": st.column_config.NumberColumn(
                    "Precio lista", format="%.0f"),
                "en_promo": st.column_config.CheckboxColumn("En promo"),
                "costo": st.column_config.NumberColumn("Costo", format="%.0f"),
                "comision_prom": st.column_config.NumberColumn("Comisión", format="%.0f"),
                "envio_prom": st.column_config.NumberColumn("Envío", format="%.0f"),
                "cargos_totales": st.column_config.NumberColumn("Cargos", format="%.0f"),
                "impuestos": st.column_config.NumberColumn(
                    "Impuestos", format="%.0f"),
                "logistico": st.column_config.NumberColumn(
                    "Logístico", format="%.0f"),
                "general": st.column_config.NumberColumn(
                    "General", format="%.0f"),
                "otros_conceptos": st.column_config.NumberColumn(
                    "Otros conceptos", format="%.0f",
                    help="Impuestos + logístico + general"),
                "margen_sin_otros": st.column_config.NumberColumn(
                    "Margen antes de otros", format="%.0f",
                    help="Solo descontando costo, comisión y envío"),
                "margen": st.column_config.NumberColumn("Margen $", format="%.0f"),
                "margen_pct": st.column_config.NumberColumn("Margen %", format="percent"),
                "unidades_90d": "Unid. vendidas",
                "base_cargos": "Base",
                "estado": "Estado",
                "detalle": "Detalle",
            })

        st.download_button(
            "Descargar el análisis", df.to_csv(index=False).encode("utf-8"),
            f"rentabilidad_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv")

        st.caption(
            "Los cargos salen del promedio real por unidad de las ventas del "
            "período elegido. Los SKU con `base_cargos = sin_ventas` no "
            "registraron ventas: ahí el margen no descuenta comisión.")
