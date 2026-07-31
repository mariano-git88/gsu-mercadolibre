#!/usr/bin/env python3
"""
Mejor precio de la competencia por EAN.

Se sube una planilla con EAN (codigo de barras) y devuelve, para cada uno,
quien lo vende mas barato en MercadoLibre y a cuanto.

    python competencia.py            -> prueba con EAN del propio catalogo
    python competencia.py 7793300230309 7793300423084

Como funciona y que limite tiene:

  El buscador libre de ML (`/sites/MLU/search`) devuelve **403**: ML lo cerro
  para aplicaciones. La via que si funciona es el **catalogo**:

      EAN -> /products/search -> catalog_product_id -> /products/{id}/items

  O sea que vemos a todos los que venden ese producto **dentro del catalogo**
  de ML. Si alguien lo publica por fuera del catalogo, no aparece. Para
  productos con codigo de barras conocido la cobertura es buena (11 de 15 en
  las pruebas), pero no es literalmente "todo MercadoLibre".
"""

import re
import sys
from pathlib import Path

import pandas as pd

from meli import Meli, MeliError, SITE_ID

DIR = Path(__file__).resolve().parent

COLS_EAN = ["ean", "gtin", "codigo de barras", "codigo_barras", "barcode",
            "codigo", "código"]

# Los nicknames se repiten muchisimo entre EAN: sin cache seria una llamada
# por competidor por producto.
_cache_nicks = {}


def limpiar_eans(valor):
    """
    Un EAN por elemento. Las publicaciones de SUPRABOND a veces traen varios
    separados por coma ("779...220,779...723"), y asi no matchean con nada.
    """
    if valor is None:
        return []
    crudo = str(valor).strip()
    if not crudo or crudo.lower() in ("nan", "none"):
        return []
    return [t for t in re.split(r"[,;/\s]+", crudo) if t.isdigit() and len(t) >= 8]


def nickname(ml, seller_id):
    if seller_id in _cache_nicks:
        return _cache_nicks[seller_id]
    try:
        u = ml.get(f"/users/{seller_id}")
        nick = u.get("nickname") or str(seller_id)
        rep = (u.get("seller_reputation") or {}).get("level_id") or ""
    except Exception:  # noqa: BLE001
        nick, rep = str(seller_id), ""
    _cache_nicks[seller_id] = (nick, rep)
    return _cache_nicks[seller_id]


def producto_de_ean(ml, ean):
    """EAN -> producto de catalogo. Devuelve (id, nombre) o (None, None)."""
    try:
        r = ml.get("/products/search", site_id=SITE_ID, q=ean)
    except Exception:  # noqa: BLE001
        return None, None
    res = r.get("results") or []
    if not res:
        return None, None
    return res[0].get("id"), res[0].get("name")


def competidores(ml, product_id):
    """Publicaciones que venden ese producto de catalogo."""
    try:
        r = ml.get(f"/products/{product_id}/items")
    except Exception:  # noqa: BLE001
        return []
    return r.get("results") or []


def analizar(ml, eans, callback=None):
    """
    Para cada EAN devuelve una fila con el mejor precio, quien lo tiene y
    donde estamos nosotros.
    """
    filas = []
    total = len(eans)

    for i, ean in enumerate(eans, start=1):
        if callback:
            callback(i, total, ean)

        pid, nombre = producto_de_ean(ml, ean)
        if not pid:
            filas.append({"ean": ean, "producto": "", "product_id": "",
                          "competidores": 0, "mejor_precio": None,
                          "mejor_vendedor": "", "reputacion": "",
                          "nuestro_precio": None, "diferencia": None,
                          "posicion": None, "estado": "sin_catalogo",
                          "detalle": "El EAN no tiene producto en el catálogo de ML."})
            continue

        items = competidores(ml, pid)
        if not items:
            filas.append({"ean": ean, "producto": nombre or "", "product_id": pid,
                          "competidores": 0, "mejor_precio": None,
                          "mejor_vendedor": "", "reputacion": "",
                          "nuestro_precio": None, "diferencia": None,
                          "posicion": None, "estado": "sin_vendedores",
                          "detalle": "El producto existe pero nadie lo está vendiendo."})
            continue

        con_precio = sorted((x for x in items if x.get("price")),
                            key=lambda x: x["price"])
        if not con_precio:
            filas.append({"ean": ean, "producto": nombre or "", "product_id": pid,
                          "competidores": len(items), "mejor_precio": None,
                          "mejor_vendedor": "", "reputacion": "",
                          "nuestro_precio": None, "diferencia": None,
                          "posicion": None, "estado": "sin_precios",
                          "detalle": "Ninguna publicación informa precio."})
            continue

        mejor = con_precio[0]
        nick, rep = nickname(ml, mejor.get("seller_id"))

        nuestras = [x for x in con_precio if x.get("seller_id") == ml.user_id]
        nuestro = nuestras[0]["price"] if nuestras else None
        posicion = (con_precio.index(nuestras[0]) + 1) if nuestras else None
        diferencia = ((nuestro - mejor["price"]) / mejor["price"]
                      if nuestro else None)

        somos_nosotros = mejor.get("seller_id") == ml.user_id
        filas.append({
            "ean": ean,
            "producto": (nombre or "")[:70],
            "product_id": pid,
            "competidores": len(con_precio),
            "mejor_precio": mejor["price"],
            "mejor_vendedor": "NOSOTROS" if somos_nosotros else nick,
            "reputacion": rep,
            "nuestro_precio": nuestro,
            "diferencia": diferencia,
            "posicion": posicion,
            "estado": "ok",
            "detalle": ("Somos los más baratos." if somos_nosotros
                        else ("No publicamos este producto en catálogo."
                              if nuestro is None
                              else f"Estamos {diferencia:+.1%} sobre el más barato.")),
        })

    cols = ["ean", "producto", "mejor_precio", "mejor_vendedor", "reputacion",
            "nuestro_precio", "diferencia", "posicion", "competidores",
            "estado", "detalle", "product_id"]
    return pd.DataFrame(filas, columns=cols)


def leer_planilla_eans(archivo):
    """Lee la planilla y devuelve la lista de EAN, ya separados y limpios."""
    nombre = getattr(archivo, "name", str(archivo)).lower()
    df = (pd.read_csv(archivo, dtype=str) if nombre.endswith(".csv")
          else pd.read_excel(archivo, dtype=str))

    normal = {str(c).strip().lower(): c for c in df.columns}
    col = next((normal[c] for c in COLS_EAN if c in normal), None)
    if col is None:
        # Sin encabezado reconocible, buscamos la columna con mas numeros largos.
        mejor, puntaje = None, 0
        for c in df.columns:
            p = df[c].dropna().astype(str).str.match(r"^\d{8,14}$").mean()
            if p > puntaje:
                mejor, puntaje = c, p
        if puntaje < 0.3:
            raise ValueError(
                f"No encontré una columna de EAN. Columnas: {list(df.columns)}")
        col = mejor

    eans = []
    for v in df[col]:
        eans.extend(limpiar_eans(v))
    return list(dict.fromkeys(eans)), col


def main():
    ml = Meli(verbose=False)
    if len(sys.argv) > 1:
        eans = [e for a in sys.argv[1:] for e in limpiar_eans(a)]
    else:
        import json
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
        eans = []
        for p in pubs:
            if p.get("status") != "active":
                continue
            for a in p.get("attributes") or []:
                if a.get("id") == "GTIN":
                    eans.extend(limpiar_eans(a.get("value_name")))
                    break
            if len(eans) >= 8:
                break

    print(f"Analizando {len(eans)} EAN...\n")
    df = analizar(ml, eans,
                  callback=lambda i, t, e: print(f"  {i}/{t} {e}", end="\r"))
    print(" " * 40)
    cols = ["ean", "mejor_precio", "mejor_vendedor", "nuestro_precio",
            "diferencia", "posicion", "competidores", "estado"]
    print(df[cols].to_string(index=False, float_format=lambda x: f"{x:,.0f}"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)


# ------------------------------------------------------------------ monitoreo

HOJA_MONITOR = "monitor_competencia"
HOJA_ALERTAS = "alertas_competencia"
COLS_MONITOR = ["ean", "producto", "mejor_precio", "mejor_vendedor",
                "nuestro_precio", "posicion", "competidores", "medido"]
COLS_ALERTAS = ["fecha", "ean", "producto", "tipo", "detalle",
                "antes", "ahora"]

# Un cambio de precio menor a esto es ruido de redondeo, no una jugada.
CAMBIO_MINIMO = 0.02


def eans_vigilados():
    """Los EAN que se monitorean: los que ya están en la hoja del monitor."""
    import almacen
    return [f["ean"] for f in almacen.leer_hoja(HOJA_MONITOR, COLS_MONITOR)
            if f.get("ean")]


def monitorear(ml, eans=None, callback=None):
    """
    Compara la foto de hoy contra la anterior y **solo reporta lo que cambió**.

    Sin esto, un informe completo cada día se mira dos veces y se abandona.
    Lo que importa es enterarse de que un competidor bajó el precio, apareció
    uno nuevo, o pasamos de ganar a perder.
    """
    import almacen
    from datetime import datetime

    previo = {f["ean"]: f for f in almacen.leer_hoja(HOJA_MONITOR, COLS_MONITOR)}
    eans = eans or list(previo) or []
    if not eans:
        return {"alertas": [], "error": "No hay EAN cargados para monitorear."}

    hoy = analizar(ml, eans, callback=callback)
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
    alertas, filas = [], []

    def _f(v):
        try:
            return float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return None

    for _, r in hoy.iterrows():
        if r["estado"] != "ok":
            continue
        ean = str(r["ean"])
        ant = previo.get(ean)
        filas.append({
            "ean": ean, "producto": r["producto"],
            "mejor_precio": r["mejor_precio"],
            "mejor_vendedor": r["mejor_vendedor"],
            "nuestro_precio": r["nuestro_precio"] or "",
            "posicion": r["posicion"] or "", "competidores": r["competidores"],
            "medido": ahora,
        })
        if not ant:
            continue

        p_ant, p_hoy = _f(ant.get("mejor_precio")), _f(r["mejor_precio"])
        if p_ant and p_hoy and abs(p_hoy - p_ant) / p_ant >= CAMBIO_MINIMO:
            alertas.append({
                "fecha": ahora, "ean": ean, "producto": r["producto"],
                "tipo": "bajo_el_precio" if p_hoy < p_ant else "subio_el_precio",
                "detalle": (f"El más barato ({r['mejor_vendedor']}) "
                            f"{'bajó' if p_hoy < p_ant else 'subió'} "
                            f"{abs(p_hoy - p_ant) / p_ant:.0%}"),
                "antes": p_ant, "ahora": p_hoy})

        if str(ant.get("mejor_vendedor")) == "NOSOTROS" and \
                r["mejor_vendedor"] != "NOSOTROS":
            alertas.append({
                "fecha": ahora, "ean": ean, "producto": r["producto"],
                "tipo": "perdimos_el_primer_puesto",
                "detalle": f"Nos pasó {r['mejor_vendedor']}",
                "antes": ant.get("mejor_precio"), "ahora": r["mejor_precio"]})
        elif str(ant.get("mejor_vendedor")) != "NOSOTROS" and \
                r["mejor_vendedor"] == "NOSOTROS":
            alertas.append({
                "fecha": ahora, "ean": ean, "producto": r["producto"],
                "tipo": "ganamos_el_primer_puesto",
                "detalle": "Pasamos a ser los más baratos",
                "antes": ant.get("mejor_precio"), "ahora": r["mejor_precio"]})

        c_ant, c_hoy = _f(ant.get("competidores")), _f(r["competidores"])
        if c_ant and c_hoy and c_hoy > c_ant:
            alertas.append({
                "fecha": ahora, "ean": ean, "producto": r["producto"],
                "tipo": "competidores_nuevos",
                "detalle": f"Entraron {int(c_hoy - c_ant)} vendedores nuevos",
                "antes": int(c_ant), "ahora": int(c_hoy)})

    almacen.reescribir_hoja(HOJA_MONITOR, COLS_MONITOR, filas)
    guardar_comparacion(hoy, origen="monitor")
    if alertas:
        almacen.append_hoja(HOJA_ALERTAS, COLS_ALERTAS, alertas)
    return {"alertas": alertas, "vigilados": len(filas), "error": ""}


def cargar_vigilados(eans):
    """Deja los EAN a vigilar (la primera medición se hace al monitorear)."""
    import almacen
    return almacen.reescribir_hoja(
        HOJA_MONITOR, COLS_MONITOR,
        [{"ean": e, "producto": "", "mejor_precio": "", "mejor_vendedor": "",
          "nuestro_precio": "", "posicion": "", "competidores": "",
          "medido": ""} for e in dict.fromkeys(eans)])


# ------------------------------------------------------- top de mas vendidos

def eans_mas_vendidos(ml, n=50, dias=30, callback=None):
    """
    EAN de los N articulos que MAS VENDIERON en el periodo.

    Se usan las ventas reales del periodo y no el `sold_quantity` historico:
    lo que importa es contra quien competis hoy, no lo que vendia hace dos
    años. Solo entran los que tienen EAN cargado — sin EAN no hay forma de
    encontrarlos en el catalogo de ML.

    Devuelve (eans, detalle) donde `detalle` explica cuantos quedaron afuera
    y por que, para que no parezca que el top esta incompleto por un error.
    """
    import json
    from collections import defaultdict
    from datetime import datetime, timedelta

    from ventas import traer_ordenes

    if callback:
        callback("Trayendo las ventas del período...")
    hasta = datetime.now()
    ordenes = traer_ordenes(ml, hasta - timedelta(days=dias), hasta)

    # Unidades por publicacion, sin contar canceladas.
    unidades = defaultdict(int)
    for o in ordenes:
        if o.get("status") == "cancelled":
            continue
        for it in o.get("order_items") or []:
            iid = it["item"].get("id")
            if iid:
                unidades[iid] += it.get("quantity") or 0

    pubs = {p["id"]: p for p in json.loads(
        (DIR / "catalogo.json").read_text(encoding="utf-8"))}

    def gtin(p):
        for a in p.get("attributes") or []:
            if a.get("id") == "GTIN":
                return a.get("value_name")
        return None

    ordenados = sorted(unidades.items(), key=lambda x: -x[1])
    eans, vistos, sin_ean, filas = [], set(), 0, []

    for iid, u in ordenados:
        if len(eans) >= n:
            break
        p = pubs.get(iid)
        if not p:
            continue
        limpios = limpiar_eans(gtin(p))
        if not limpios:
            sin_ean += 1
            continue
        e = limpios[0]
        if e in vistos:          # varias publicaciones del mismo producto
            continue
        vistos.add(e)
        eans.append(e)
        filas.append({"ean": e, "item_id": iid, "unidades": u,
                      "titulo": (p.get("title") or "")[:60]})

    detalle = (f"{len(eans)} productos con EAN entre los más vendidos de los "
               f"últimos {dias} días.")
    if sin_ean:
        detalle += (f" Quedaron afuera {sin_ean} que vendieron pero no tienen "
                    "código de barras cargado.")
    return eans, detalle, pd.DataFrame(filas)


# ------------------------------------------------------------------ historial

HOJA_HISTORIAL = "historial_competencia"
COLS_HISTORIAL = ["fecha", "origen", "ean", "producto", "mejor_precio",
                  "mejor_vendedor", "reputacion", "nuestro_precio",
                  "diferencia", "posicion", "competidores", "estado"]


def guardar_comparacion(df, origen="manual"):
    """
    Deja una foto de la comparacion en la planilla. Append-only: cada corrida
    suma filas en vez de pisar la anterior, asi se puede ver como evoluciono
    el precio de un competidor en el tiempo.

    `origen` distingue lo que se corrio a mano de lo que corrio el monitor
    diario, para poder filtrar despues.

    No lanza: si la Sheet falla, la comparacion ya sirve igual en pantalla.
    """
    import almacen
    from datetime import datetime

    if df is None or not len(df):
        return False, "No hay nada para guardar."

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filas = []
    for _, r in df.iterrows():
        if r.get("estado") != "ok":
            continue          # las que no se pudieron medir no son historial
        filas.append({
            "fecha": ahora, "origen": origen, "ean": str(r["ean"]),
            "producto": r["producto"],
            "mejor_precio": r["mejor_precio"],
            "mejor_vendedor": r["mejor_vendedor"],
            "reputacion": r.get("reputacion", ""),
            "nuestro_precio": r["nuestro_precio"] if pd.notna(
                r["nuestro_precio"]) else "",
            "diferencia": (round(float(r["diferencia"]), 4)
                           if pd.notna(r["diferencia"]) else ""),
            "posicion": (int(r["posicion"]) if pd.notna(r["posicion"]) else ""),
            "competidores": r["competidores"], "estado": r["estado"],
        })

    if not filas:
        return False, "Ninguna comparación tuvo datos utilizables."
    try:
        ok, det = almacen.append_hoja(HOJA_HISTORIAL, COLS_HISTORIAL, filas)
        return ok, det or f"{len(filas)} comparaciones guardadas."
    except Exception as e:
        return False, f"No pude guardar en la planilla: {str(e)[:200]}"


def historial(ean=None):
    """Historial de comparaciones, opcionalmente filtrado por EAN."""
    import almacen
    filas = almacen.leer_hoja(HOJA_HISTORIAL, COLS_HISTORIAL)
    if ean:
        filas = [f for f in filas if str(f.get("ean")) == str(ean)]
    return pd.DataFrame(filas)
