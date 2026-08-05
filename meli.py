#!/usr/bin/env python3
"""
Cliente de la API de MercadoLibre para SUPRABOND.

Se encarga de todo el lio de OAuth 2.0: guarda los tokens en tokens.json,
los renueva solo cuando estan por vencer y reintenta si ML tira 429.

Uso desde cualquier otro script:

    from meli import Meli
    ml = Meli()
    print(ml.get("/users/me"))

Autorizacion inicial: correr `python autorizar.py` una sola vez.
"""

import json
import time
import urllib.parse
from pathlib import Path

import requests

import almacen

BASE = "https://api.mercadolibre.com"
SITE_ID = "MLU"                                  # Uruguay
AUTH_BASE = "https://auth.mercadolibre.com.uy"   # cambia el dominio segun el pais

# Prefijo de los codigos de publicacion del sitio (MLU123456789). Se usa para
# reconocer cuando la planilla trae el codigo directo en vez del SKU.
PREFIJO_ITEM = SITE_ID

DIR = Path(__file__).resolve().parent
CRED_FILE = DIR / "credentials.txt"


def tokens_desde_respuesta(respuesta):
    """Normaliza la respuesta de /oauth/token al formato que guardamos."""
    return {
        "access_token": respuesta["access_token"],
        "refresh_token": respuesta["refresh_token"],
        "user_id": respuesta["user_id"],
        "scope": respuesta.get("scope", ""),
        "expira_en": time.time() + respuesta["expires_in"],
        "renovado": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

# ML acepta la request hasta el segundo cero, pero renovamos con margen
# para que no venza a mitad de un sync largo.
MARGEN_RENOVACION_SEG = 600


class MeliError(RuntimeError):
    """Error devuelto por la API de MercadoLibre."""


def es_error_de_api(e):
    """
    True si `e` es un error de la API, **aunque venga de otra copia del
    modulo**.

    Hace falta por `st.cache_resource`: el cliente cacheado puede quedar
    viviendo en una copia reimportada de `meli`, y entonces el `MeliError` que
    lanza NO es la misma clase que la que importo el modulo que llama. El
    `except MeliError` no matchea, la excepcion se escapa y se lleva puesta la
    corrida entera — pasó en Competencia, donde el `try/except` estaba puesto
    y aun asi la app murio en la publicacion 3 de 50.

    Comparar por nombre es feo pero es lo unico que sobrevive a las dos copias.
    """
    return isinstance(e, MeliError) or type(e).__name__ == "MeliError"


def leer_credenciales():
    """
    Devuelve app_id / secret_key / redirect_uri.

    En la nube vienen de los secrets (credentials.txt no se sube al repo);
    en local, del archivo credentials.txt.
    """
    desde_secrets = almacen.credenciales_meli()
    if desde_secrets:
        faltantes = [c for c in ("app_id", "secret_key", "redirect_uri")
                     if not desde_secrets.get(c)]
        if faltantes:
            raise MeliError(
                f"En los secrets, [mercadolibre] no tiene: {', '.join(faltantes)}")
        return desde_secrets

    if not CRED_FILE.exists():
        raise MeliError(
            f"Falta {CRED_FILE.name}. Copia credentials.txt.example, renombralo a "
            "credentials.txt y completa app_id, secret_key y redirect_uri. "
            "(En Streamlit Cloud, cargalos en Secrets bajo [mercadolibre].)"
        )
    datos = {}
    for linea in CRED_FILE.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        datos[clave.strip()] = valor.strip()

    faltantes = [c for c in ("app_id", "secret_key", "redirect_uri") if not datos.get(c)]
    if faltantes:
        raise MeliError(f"En credentials.txt faltan completar: {', '.join(faltantes)}")
    return datos


class Meli:
    def __init__(self, verbose=True):
        self.cred = leer_credenciales()
        self.verbose = verbose
        self.sesion = requests.Session()
        self.tokens = self._leer_tokens()

    # ------------------------------------------------------------------ tokens

    def _leer_tokens(self):
        tokens = almacen.leer_tokens()
        if not tokens:
            raise MeliError(
                "No hay tokens guardados todavia. Corre `python autorizar.py` "
                "para hacer la autorizacion inicial (se hace una sola vez)."
            )
        return tokens

    def _guardar_tokens(self, respuesta):
        """Guarda la respuesta de /oauth/token agregando cuando vence."""
        datos = tokens_desde_respuesta(respuesta)
        self.tokens = almacen.guardar_tokens(datos)
        return self.tokens

    def _renovar(self):
        if self.verbose:
            print("[meli] Token vencido, renovando...")
        resp = self.sesion.post(
            f"{BASE}/oauth/token",
            headers={"accept": "application/json",
                     "content-type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": self.cred["app_id"],
                "client_secret": self.cred["secret_key"],
                "refresh_token": self.tokens["refresh_token"],
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise MeliError(
                f"No se pudo renovar el token (HTTP {resp.status_code}): {resp.text}\n"
                "Si dice 'invalid_grant', el refresh_token se vencio o se uso dos veces: "
                "hay que correr `python autorizar.py` de nuevo."
            )
        datos = self._guardar_tokens(resp.json())
        if self.verbose:
            print("[meli] Token renovado OK.")
        return datos

    @property
    def token(self):
        if time.time() >= self.tokens["expira_en"] - MARGEN_RENOVACION_SEG:
            self._renovar()
        return self.tokens["access_token"]

    @property
    def user_id(self):
        return self.tokens["user_id"]

    # ------------------------------------------------------------------ llamadas

    def get(self, path, _headers=None, **params):
        """
        GET a la API. `path` arranca con / (ej: '/users/me').

        `_headers` agrega headers a la llamada. Va con guion bajo adelante
        para no chocar con un parametro de query que se llame igual. Lo usa
        Product Ads, que exige `Api-Version: 2` y sin eso contesta 404 o un
        500 con "Type mismatch" que no dice nada.
        """
        return self._request("GET", path, params=params, headers=_headers)

    def put(self, path, payload, _headers=None):
        """
        PUT a la API. Se usa para modificar publicaciones.

        OJO: esto escribe en la cuenta de verdad. Toda la logica de negocio
        (que publicacion tocar, con que valor) va en los modulos de arriba;
        aca solo mandamos el cambio y registramos que paso.
        """
        return self._request("PUT", path, json_body=payload, headers=_headers)

    def post(self, path, payload=None, _headers=None, **params):
        """
        POST a la API. Se usa para sumar publicaciones a una promocion.

        Igual que `put`: esto escribe en la cuenta de verdad. La logica de que
        publicacion tocar vive en los modulos de arriba.
        """
        return self._request("POST", path, params=params, json_body=payload,
                             headers=_headers)

    def delete(self, path, _headers=None, **params):
        """DELETE a la API. Saca una publicacion de una promocion."""
        return self._request("DELETE", path, params=params, headers=_headers)

    def _request(self, metodo, path, params=None, json_body=None, intentos=5,
                 intentos_429=8, headers=None):
        """
        Los 429 tienen su propio presupuesto de reintentos, aparte de
        `intentos`. En una carga masiva (miles de llamadas seguidas) ML corta
        por rate limit varias veces seguidas, y gastar el mismo contador que
        los errores comunes hacia que la corrida muriera a las pocas decenas
        de publicaciones.
        """
        url = path if path.startswith("http") else f"{BASE}{path}"
        espera = 2
        vistos_429 = 0

        for intento in range(1, intentos + intentos_429 + 1):
            cabeceras = {"Authorization": f"Bearer {self.token}",
                         "Accept": "application/json",
                         "Content-Type": "application/json"}
            if headers:
                cabeceras.update(headers)
            resp = self.sesion.request(
                metodo, url,
                headers=cabeceras,
                params=params,
                json=json_body,
                timeout=60,
            )

            if resp.status_code == 429:
                vistos_429 += 1
                if vistos_429 > intentos_429:
                    raise MeliError(
                        f"{metodo} {url} -> MercadoLibre sigue devolviendo 429 "
                        f"despues de {intentos_429} esperas. Es rate limit: "
                        "conviene reintentar mas tarde o bajar el ritmo.")
                # ML avisa cuanto esperar; si no, backoff exponencial.
                pausa = float(resp.headers.get("Retry-After", espera))
                if self.verbose:
                    print(f"[meli] 429 rate limit, esperando {pausa:.0f}s "
                          f"(espera {vistos_429}/{intentos_429})")
                time.sleep(pausa)
                espera = min(espera * 2, 60)
                continue

            if resp.status_code == 401 and intento == 1:
                # El token puede morir antes de tiempo (cambio de clave, etc).
                self._renovar()
                continue

            if resp.status_code >= 400:
                raise MeliError(f"{metodo} {url} -> HTTP {resp.status_code}: {resp.text[:500]}")

            # Un DELETE (y a veces un POST) contesta 204 sin cuerpo: ahi
            # resp.json() reventaria. Se devuelve un dict vacio.
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError:
                return {"_texto": resp.text[:500]}

        raise MeliError(f"{metodo} {url} fallo despues de {intentos} intentos (rate limit).")

    # ------------------------------------------------------------------ paginado

    def paginar(self, path, limite=50, tope=None, **params):
        """
        Recorre un endpoint paginado por offset y va devolviendo los resultados
        de a uno. ML corta el offset en 1000, asi que avisa si se llega al tope.
        """
        offset, traidos = 0, 0
        while True:
            pagina = self.get(path, limit=limite, offset=offset, **params)
            resultados = pagina.get("results", [])
            if not resultados:
                return

            for item in resultados:
                yield item
                traidos += 1
                if tope and traidos >= tope:
                    return

            total = pagina.get("paging", {}).get("total", 0)
            offset += limite
            if offset >= total:
                return
            if offset >= 1000:
                print(f"[meli] AVISO: {path} llego al limite de offset 1000 "
                      f"(hay {total} en total). Hay que partir la consulta por fechas.")
                return

    def scan_items(self):
        """
        Trae TODOS los IDs de publicaciones del vendedor usando scroll,
        que es la unica forma de pasar el limite de 1000 de offset.
        """
        scroll_id = None
        while True:
            params = {"search_type": "scan", "limit": 100}
            if scroll_id:
                params["scroll_id"] = scroll_id
            pagina = self.get(f"/users/{self.user_id}/items/search", **params)
            resultados = pagina.get("results", [])
            if not resultados:
                return
            for item_id in resultados:
                yield item_id
            scroll_id = pagina.get("scroll_id")
            if not scroll_id:
                return

    def items_detalle(self, ids, atributos=None):
        """
        Trae el detalle de varias publicaciones. El multiget de ML acepta
        20 por llamada, asi que cortamos de a 20.
        """
        ids = list(ids)
        for i in range(0, len(ids), 20):
            lote = ids[i:i + 20]
            params = {"ids": ",".join(lote)}
            if atributos:
                params["attributes"] = ",".join(atributos)
            for fila in self.get("/items", **params):
                if fila.get("code") == 200:
                    yield fila["body"]
                else:
                    print(f"[meli] AVISO: no se pudo traer {fila.get('body', {}).get('id', '?')} "
                          f"(code {fila.get('code')})")


    # ------------------------------------------------------------- escritura

    def actualizar_publicacion(self, item_id, cambios, valores_previos=None,
                               operador="", nota=""):
        """
        Aplica `cambios` (ej: {"price": 1234} o {"available_quantity": 10}) a
        una publicacion y deja constancia en auditoria.csv.

        `valores_previos` viene de la simulacion. Se registra tal cual para
        poder revertir a mano si algo sale mal, asi que conviene simular y
        aplicar sin mucho tiempo en el medio.

        Devuelve (ok, detalle).
        """
        previos = valores_previos or {}
        try:
            resp = self.put(f"/items/{item_id}", cambios)
            aplicados = {c: resp.get(c) for c in cambios}
            registrar_auditoria(item_id, cambios, previos, aplicados,
                                "OK", operador, nota)
            return True, aplicados
        except MeliError as e:
            detalle = str(e)[:300]
            registrar_auditoria(item_id, cambios, previos, {},
                                f"ERROR: {detalle}", operador, nota)
            return False, detalle


# ---------------------------------------------------------------- auditoria

def registrar_auditoria(item_id, cambios, previos, aplicados, resultado,
                        operador="", nota=""):
    """
    Deja una fila por campo modificado, en la Google Sheet o en el CSV local
    segun como este configurado. Append-only a proposito: si algo sale mal,
    esto es lo unico que dice como estaba antes.
    """
    filas = [{
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "item_id": item_id,
        "campo": campo,
        "valor_anterior": previos.get(campo, ""),
        "valor_nuevo": aplicados.get(campo, valor),
        "resultado": resultado,
        "operador": operador,
        "nota": nota,
    } for campo, valor in cambios.items()]

    ok, detalle = almacen.append_auditoria(filas)
    if not ok:
        print(f"[meli] AVISO: {detalle}")
    return ok


# ---------------------------------------------------------------- autorizacion

def url_de_autorizacion(app_id, redirect_uri, state="suprabond"):
    params = {
        "response_type": "code",
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{AUTH_BASE}/authorization?{urllib.parse.urlencode(params)}"


def canjear_code(cred, code):
    """Cambia el code de un solo uso por el par access_token / refresh_token."""
    resp = requests.post(
        f"{BASE}/oauth/token",
        headers={"accept": "application/json",
                 "content-type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "client_id": cred["app_id"],
            "client_secret": cred["secret_key"],
            "code": code,
            "redirect_uri": cred["redirect_uri"],
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise MeliError(f"No se pudo canjear el code (HTTP {resp.status_code}): {resp.text}")
    return resp.json()


if __name__ == "__main__":
    ml = Meli()
    yo = ml.get("/users/me")
    print(f"Conectado como {yo['nickname']} (user_id {yo['id']}) - site {yo.get('site_id')}")
