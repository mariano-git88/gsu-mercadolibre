#!/usr/bin/env python3
"""
Donde se guardan los tokens de MercadoLibre y el registro de auditoria.

El problema que resuelve: en Streamlit Cloud el disco es efimero. Como el
refresh_token de ML es de un solo uso y rota en cada renovacion, si se pierde
el archivo hay que reautorizar a mano desde el navegador. Lo mismo con la
auditoria: es el unico registro de quien cambio que precio.

Por eso, si hay una Google Sheet configurada, los dos van ahi. Si no, caen a
archivos locales y todo sigue funcionando igual (uso desde la terminal).

Configuracion (en .streamlit/secrets.toml o en los secrets de Streamlit Cloud):

    [gsheets]
    spreadsheet_id = "1AbC..."
    # en local:
    service_account_json_path = ".gsheets/sa.json"
    # en la nube, pegar el JSON entero:
    # [gsheets.service_account]
    # type = "service_account"
    # ...
"""

import json
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
TOKENS_LOCAL = DIR / "tokens.json"
AUDITORIA_LOCAL = DIR / "auditoria.csv"
SECRETS_LOCAL = DIR / ".streamlit" / "secrets.toml"

HOJA_TOKENS = "tokens_ml"
HOJA_AUDITORIA = "auditoria"

# Ultimo motivo por el que fallo la lectura de secrets, si fallo. Lo llena
# `_seccion()` y lo lee quien muestra el error al operador.
ULTIMO_ERROR_SECRETS = None

COLUMNAS_TOKENS = ["access_token", "refresh_token", "user_id", "scope",
                   "expira_en", "renovado"]
COLUMNAS_AUDITORIA = ["fecha", "item_id", "campo", "valor_anterior",
                      "valor_nuevo", "resultado", "operador", "nota"]


class AlmacenError(RuntimeError):
    pass


# ------------------------------------------------------------------ config

def diagnostico_secrets():
    """
    Que ve el proceso cuando busca los secrets. Sirve para distinguir tres
    fallas que dan el mismo sintoma: que no haya secrets cargados, que esten
    cargados pero sin la seccion que se busca, y que Streamlit no los pueda
    parsear.

    Nunca lanza y **nunca devuelve valores**, solo nombres de secciones: se
    muestra en pantalla y la app puede ser publica.
    """
    info = {"entorno": "terminal", "secciones": [], "error": None,
            "avisos": []}
    try:
        import streamlit as st
        info["entorno"] = "streamlit"
        info["secciones"] = sorted(st.secrets.keys())
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}: {e}"

    if not info["secciones"] and SECRETS_LOCAL.exists():
        import tomllib
        try:
            with open(SECRETS_LOCAL, "rb") as f:
                info["secciones"] = sorted(tomllib.load(f).keys())
                info["entorno"] += " + secrets.toml local"
        except Exception as e:  # noqa: BLE001
            info["error"] = f"secrets.toml local no parsea — {e}"

    # Las secciones pueden estar todas y aun asi no servir: el caso mas comun
    # es haber pegado el secrets.toml local, cuyo service_account_json_path
    # apunta a un archivo que en la nube no existe.
    for nombre in ("gsheets", "gsheets_costos"):
        if nombre in info["secciones"]:
            sirve, motivo = credencial_google_utilizable(_seccion(nombre))
            if not sirve:
                info["avisos"].append(f"`[{nombre}]`: {motivo}")
    return info


def _seccion(nombre):
    """
    Lee una seccion de los secrets. Primero de Streamlit (si la app esta
    corriendo), si no de .streamlit/secrets.toml, para que los scripts de
    terminal usen exactamente la misma configuracion.

    Si Streamlit esta corriendo pero falla al leer los secrets, **no se traga
    el error**: se guarda en `ULTIMO_ERROR_SECRETS` para que quien muestre el
    mensaje pueda decir que paso de verdad. Antes esto hacia `except: pass` y
    cualquier problema de secrets terminaba reportado como "falta
    credentials.txt", que manda a buscar el problema donde no esta.
    """
    global ULTIMO_ERROR_SECRETS

    try:
        import streamlit as st
    except Exception:                      # no hay streamlit: modo terminal
        st = None

    if st is not None:
        try:
            seccion = st.secrets.get(nombre)
            if seccion:
                return dict(seccion)
        except Exception as e:  # noqa: BLE001
            ULTIMO_ERROR_SECRETS = f"{type(e).__name__}: {e}"

    if SECRETS_LOCAL.exists():
        import tomllib
        try:
            with open(SECRETS_LOCAL, "rb") as f:
                return dict(tomllib.load(f).get(nombre) or {})
        except Exception as e:  # noqa: BLE001
            ULTIMO_ERROR_SECRETS = f"secrets.toml local no parsea — {e}"
            return {}
    return {}


def _config():
    return _seccion("gsheets")


def credenciales_meli():
    """Credenciales de la app de ML si estan en secrets; si no, {}."""
    return _seccion("mercadolibre")


def credencial_google_utilizable(cfg):
    """
    (sirve, motivo). Un `service_account_json_path` **que no existe no sirve**,
    aunque este escrito en los secrets.

    Es el caso tipico de pegar en Streamlit Cloud el secrets.toml de la
    maquina local: la ruta apunta a `.gsheets/sa.json`, que esta en el
    .gitignore y por lo tanto no existe en la nube. En la nube el service
    account va **inline**, como `[gsheets.service_account]`.
    """
    if cfg.get("service_account"):
        return True, ""
    ruta = cfg.get("service_account_json_path")
    if not ruta:
        return False, "no hay service_account ni service_account_json_path"
    p = Path(ruta)
    if not p.is_absolute():
        p = DIR / ruta
    if p.exists():
        return True, ""
    return False, (f"`service_account_json_path` apunta a `{ruta}`, que no "
                   f"existe. En la nube el service account va inline, como "
                   f"`[gsheets.service_account]`.")


def hay_sheet():
    cfg = _config()
    if not cfg.get("spreadsheet_id"):
        return False
    return credencial_google_utilizable(cfg)[0]


def _abrir():
    """Abre la planilla. Solo se llama si hay_sheet() dio True."""
    try:
        import gspread
    except ImportError as e:
        raise AlmacenError(
            "Falta la libreria gspread. Instalala con: pip install gspread"
        ) from e

    cfg = _config()
    sa = cfg.get("service_account")
    if sa:
        credenciales = dict(sa)
    else:
        ruta = Path(cfg["service_account_json_path"])
        if not ruta.is_absolute():
            ruta = DIR / ruta
        if not ruta.exists():
            raise AlmacenError(f"No existe el archivo de credenciales: {ruta}")
        credenciales = json.loads(ruta.read_text(encoding="utf-8"))

    cliente = gspread.service_account_from_dict(credenciales)
    try:
        return cliente.open_by_key(cfg["spreadsheet_id"])
    except Exception as e:
        raise AlmacenError(
            f"No pude abrir la Google Sheet ({cfg['spreadsheet_id']}). "
            f"Verifica el ID y que este compartida como Editor con el "
            f"client_email del service account. Detalle: {e}") from e


def _hoja(planilla, titulo, columnas):
    import gspread
    try:
        return planilla.worksheet(titulo)
    except gspread.WorksheetNotFound:
        hoja = planilla.add_worksheet(title=titulo, rows=1000,
                                      cols=max(len(columnas), 8))
        hoja.append_row(columnas)
        return hoja


# ------------------------------------------------------------------ tokens

def leer_tokens():
    """Devuelve el dict de tokens o None si todavia no hay autorizacion."""
    if hay_sheet():
        try:
            hoja = _hoja(_abrir(), HOJA_TOKENS, COLUMNAS_TOKENS)
            filas = hoja.get_all_records()
            if filas:
                d = dict(filas[-1])       # siempre vale el ultimo guardado
                d["expira_en"] = float(d.get("expira_en") or 0)
                d["user_id"] = int(d.get("user_id") or 0)
                return d
            return None
        except AlmacenError:
            raise
        except Exception as e:
            raise AlmacenError(f"No pude leer los tokens de la Sheet: {e}") from e

    if TOKENS_LOCAL.exists():
        return json.loads(TOKENS_LOCAL.read_text(encoding="utf-8"))
    return None


def guardar_tokens(datos):
    """
    Guarda los tokens. En la Sheet reemplaza la fila unica: nos interesa el
    ultimo refresh_token y nada mas, el historial no sirve (los viejos ya
    estan invalidados).
    """
    if hay_sheet():
        try:
            hoja = _hoja(_abrir(), HOJA_TOKENS, COLUMNAS_TOKENS)
            fila = [str(datos.get(c, "")) for c in COLUMNAS_TOKENS]
            hoja.clear()
            hoja.append_row(COLUMNAS_TOKENS)
            hoja.append_row(fila)
            return datos
        except Exception as e:
            raise AlmacenError(f"No pude guardar los tokens en la Sheet: {e}") from e

    # Escritura atomica: si se corta a la mitad no perdemos el refresh_token.
    tmp = TOKENS_LOCAL.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    tmp.replace(TOKENS_LOCAL)
    return datos


# ------------------------------------------------------------------ auditoria

def append_auditoria(filas):
    """
    Agrega filas al registro de auditoria. Append-only a proposito: si algo
    sale mal, esto es lo unico que dice como estaba antes.

    Nunca hace fallar la operacion principal: si el registro no se puede
    escribir, avisa pero el cambio en ML ya esta hecho.
    """
    if not filas:
        return True, ""

    if hay_sheet():
        try:
            hoja = _hoja(_abrir(), HOJA_AUDITORIA, COLUMNAS_AUDITORIA)
            hoja.append_rows([[str(f.get(c, "")) for c in COLUMNAS_AUDITORIA]
                              for f in filas])
            return True, ""
        except Exception as e:
            # Igual dejamos rastro local para no perder el registro.
            _append_local(filas)
            return False, f"No pude escribir la auditoria en la Sheet: {e}"

    _append_local(filas)
    return True, ""


def _append_local(filas):
    import csv
    nuevo = not AUDITORIA_LOCAL.exists()
    with open(AUDITORIA_LOCAL, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_AUDITORIA)
        if nuevo:
            w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in COLUMNAS_AUDITORIA})


# ------------------------------------------------------------------ hojas genericas
#
# Lo de abajo lo usa el control de stock, que necesita varias hojas propias.
# Con fallback a CSV local para poder trabajar sin Sheet configurada.

def _csv_local(titulo):
    return DIR / f"{titulo}.csv"


def leer_hoja(titulo, columnas):
    """Devuelve la hoja como lista de dicts. Si no existe, lista vacia."""
    if hay_sheet():
        try:
            return _hoja(_abrir(), titulo, columnas).get_all_records()
        except Exception as e:
            raise AlmacenError(f"No pude leer la hoja '{titulo}': {e}") from e

    ruta = _csv_local(titulo)
    if not ruta.exists():
        return []
    import csv
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def columna_hoja(titulo, columnas, nombre):
    """
    Devuelve los valores de UNA columna. Se usa para las claves de
    idempotencia: traer solo esa columna es mucho mas liviano que bajar
    todas las filas cada vez que corre la sincronizacion.
    """
    if hay_sheet():
        try:
            hoja = _hoja(_abrir(), titulo, columnas)
            idx = columnas.index(nombre) + 1
            return [v for v in hoja.col_values(idx)[1:] if v]
        except Exception as e:
            raise AlmacenError(f"No pude leer la columna '{nombre}': {e}") from e
    return [str(f.get(nombre, "")) for f in leer_hoja(titulo, columnas)
            if f.get(nombre)]


def append_hoja(titulo, columnas, filas):
    """Agrega filas al final. Devuelve (ok, detalle)."""
    if not filas:
        return True, ""
    if hay_sheet():
        try:
            hoja = _hoja(_abrir(), titulo, columnas)
            hoja.append_rows([[str(f.get(c, "")) for c in columnas] for f in filas])
            return True, ""
        except Exception as e:
            return False, f"No pude escribir en '{titulo}': {e}"

    import csv
    ruta = _csv_local(titulo)
    nuevo = not ruta.exists()
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        if nuevo:
            w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in columnas})
    return True, ""


def reescribir_hoja(titulo, columnas, filas):
    """Reemplaza el contenido completo. Se usa al resolver devoluciones."""
    if hay_sheet():
        try:
            hoja = _hoja(_abrir(), titulo, columnas)
            hoja.clear()
            hoja.append_row(columnas)
            if filas:
                hoja.append_rows([[str(f.get(c, "")) for c in columnas]
                                  for f in filas])
            return True, ""
        except Exception as e:
            return False, f"No pude reescribir '{titulo}': {e}"

    import csv
    with open(_csv_local(titulo), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in columnas})
    return True, ""


# ------------------------------------------------------------------ estado

def describir():
    """Texto corto para mostrar en la app: donde se esta guardando todo."""
    if hay_sheet():
        cfg = _config()
        return f"Google Sheet ({str(cfg.get('spreadsheet_id'))[:12]}...)"
    return "archivos locales (no sobreviven a un reinicio en la nube)"


if __name__ == "__main__":
    print(f"Modo de almacenamiento: {describir()}")
    if hay_sheet():
        try:
            t = leer_tokens()
            print("Tokens en la Sheet:",
                  f"user_id={t['user_id']}, vence {time.ctime(t['expira_en'])}"
                  if t else "todavia no hay (correr autorizar.py)")
        except AlmacenError as e:
            print(f"ERROR: {e}")
