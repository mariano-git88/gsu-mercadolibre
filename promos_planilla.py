#!/usr/bin/env python3
"""
Carga masiva de descuentos a una campaña propia, desde una planilla.

    python promos_planilla.py                    -> lista las campañas propias
    python promos_planilla.py C-MLU815824        -> que publicaciones son elegibles
    python promos_planilla.py C-MLU815824 lista.xlsx   -> simula la planilla

La planilla trae una columna con **SKU o EAN** (o el codigo MLU directo) y otra
con el **descuento en porcentaje**. Cada fila se resuelve a las publicaciones
activas de ese producto y se le calcula el precio con descuento.

Flujo obligatorio, igual que en `actualizador.py`: leer -> simular -> revisar
-> aplicar. `aplicar()` consume el DataFrame que produjo `simular()`.

--------------------------------------------------------------------------
Como funcionan de verdad las promociones propias en MercadoLibre
--------------------------------------------------------------------------

Todo lo de aca abajo esta **medido contra la cuenta real de Suprabond Uruguay
el 03/08/2026**, con una prueba controlada sobre una publicacion de cero
ventas que despues se revirtio. Nada sale de la documentacion.

**El vehiculo es la campaña propia** (`SELLER_CAMPAIGN` con sub_type
`FLEXIBLE_PERCENTAGE`). Es el unico tipo donde el descuento lo elige el
vendedor por publicacion. Los otros tipos (`DEAL`, `SMART`, `PRICE_MATCHING`)
son ofertas que ML arma y estan en `promociones.py`.

**La campaña hay que crearla desde el panel de MercadoLibre.** Por API no se
puede: `POST /seller-promotions/users/{user_id}` contesta **200 con cuerpo
vacio y no crea nada**. Es un falso positivo — si uno se guia por el codigo de
respuesta, cree que la creo. Verificado listando las campañas despues.

**El alta** es `POST /seller-promotions/items/{item_id}?app_version=v2` con
`{promotion_type, promotion_id, deal_price}`. Diferencias con los otros tipos:

  - **No lleva `offer_id`.** En `SELLER_CAMPAIGN` el GET no devuelve `ref_id`,
    que es de donde sale el `offer_id` en los demas tipos.
  - **La respuesta no trae `offer_id` tampoco**, solo `{price, original_price}`.
    Asi que no hay identificador de oferta que guardar.
  - El campo es `deal_price`. Con `price` contesta 400 FINAL_PRICE_LOWER_THAN_ZERO,
    porque lee `deal_price` y lo ve vacio.

**El mismo POST corrige un alta ya activa**: mandarlo de nuevo con otro
`deal_price` cambia el precio de la promocion. No hace falta dar de baja para
corregir un porcentaje mal cargado.

**El rango de descuento lo fija ML por publicacion, no por campaña.** Cada
oferta candidata trae `min_discounted_price` y `max_discounted_price`, y **no
son un porcentaje fijo**: sobre un articulo de $164 el descuento minimo era
10,1% y sobre uno de $11.314 era 5%. Por eso el rango se lee de ML y no se
calcula. Pasarse contesta 400 `ERROR_CREDIBILITY_DISCOUNTED_PRICE` — "the
discounted price is not credible" — que es un mensaje engañoso: no es que el
precio sea raro, es que quedo fuera del rango permitido.

**El paginado ignora `offset` en silencio.** `/seller-promotions/promotions/
{id}/items` devuelve siempre la misma pagina si se le manda `offset`, sin
error. Hay que pasar el token `searchAfter` que viene en `paging`. Sin eso uno
ve 50 publicaciones elegibles y en realidad hay 458.

**Todo lo que se lee tarda ~30 segundos en reflejarse.** Despues de un alta
exitosa el listado sigue diciendo `candidate` con `price: 0`, y despues de una
baja sigue diciendo `started`. No es que fallo: es retraso. Por eso
`verificar()` existe aparte y conviene llamarlo despues de un rato.

**La baja necesita que la oferta este consolidada.** Un DELETE a los pocos
segundos del alta contesta 400 "No offers found for item" o 200 sin hacer
nada. Con la oferta ya consolidada, `DELETE .../items/{id}` con
`promotion_type` y `promotion_id` funciona.
"""

import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from meli import PREFIJO_ITEM, Meli, MeliError
from resolver import normalizar_sku

DIR = Path(__file__).resolve().parent

TIPO = "SELLER_CAMPAIGN"

# Pausa entre publicaciones al escribir en lote: baja los 429 de ML. Es la
# misma que usa `promociones.py`, por el mismo motivo.
PAUSA_ENTRE_ITEMS = 0.25

# Nombres de columna que aceptamos para el descuento.
COLS_DESCUENTO = ["descuento", "descuento %", "descuento%", "%", "porcentaje",
                  "porcentaje descuento", "pct", "off", "dto", "rebaja"]

# Los mismos que usa `actualizador.py`, mas EAN.
COLS_CLAVE = ["sku", "ean", "gtin", "codigo de barras", "código de barras",
              "codigo", "código", "mlu", "mla", "publicacion", "publicación",
              "item", "item_id", "id"]


# --------------------------------------------------------------- campañas

def campanas_propias(ml, solo_vigentes=True):
    """
    Las campañas propias de la cuenta, donde el descuento lo elegis vos.

    `solo_vigentes` deja afuera las terminadas: cargar una planilla a una
    campaña vencida no hace nada y no da error.
    """
    r = ml.get(f"/seller-promotions/users/{ml.user_id}", app_version="v2")
    filas = []
    for c in (r.get("results") or []):
        if c.get("type") != TIPO:
            continue
        if solo_vigentes and c.get("status") not in ("started", "pending"):
            continue
        filas.append({
            "campana_id": c.get("id"),
            "nombre": c.get("name") or "",
            "estado": c.get("status"),
            "sub_tipo": c.get("sub_type") or "",
            "desde": (c.get("start_date") or "")[:10],
            "hasta": (c.get("finish_date") or "")[:10],
        })
    return pd.DataFrame(filas)


def elegibles(ml, campana_id, callback=None):
    """
    Las publicaciones que ML acepta en esa campaña, con su rango de descuento.

    Devuelve {item_id: {...}}. Hace falta traerlo entero: el rango permitido
    es **por publicacion** y no se puede calcular.

    Se pagina con el token `searchAfter`, no con `offset` — ver el docstring
    del modulo. Se piden los dos estados por separado porque el listado sin
    filtro no trae todos los que ya estan dados de alta.
    """
    salida = {}
    for estado in ("candidate", "started"):
        token, vueltas = None, 0
        while True:
            params = {"promotion_type": TIPO, "app_version": "v2",
                      "limit": 50, "status": estado}
            if token:
                params["search_after"] = token
            r = ml.get(f"/seller-promotions/promotions/{campana_id}/items",
                       **params)
            res = r.get("results") or []
            if not res:
                break
            for x in res:
                # El primero gana: `candidate` se recorre antes y es el que
                # trae el rango permitido, que el `started` no trae.
                salida.setdefault(x["id"], {
                    "item_id": x["id"],
                    "estado_promo": x.get("status"),
                    "original_price": x.get("original_price"),
                    "precio_promo_actual": x.get("price") or None,
                    "min_precio": x.get("min_discounted_price"),
                    "max_precio": x.get("max_discounted_price"),
                    "precio_sugerido": x.get("suggested_discounted_price"),
                })
            vueltas += 1
            if callback:
                callback(f"Elegibles {estado}: {len(salida)}...")
            token = (r.get("paging") or {}).get("searchAfter")
            # Corte de seguridad: si ML dejara de mandar el token pero
            # siguiera devolviendo resultados, esto no gira para siempre.
            if not token or vueltas > 60:
                break
    return salida


def rango_del_item(ml, item_id):
    """
    El rango permitido de una publicacion que ya esta dada de alta.

    Las ofertas en `started` no traen `min_discounted_price` ni
    `max_discounted_price`, pero las otras ofertas del mismo item si, y son los
    mismos numeros. Se usa solo para las pocas que hagan falta.
    """
    try:
        ofertas = ml.get(f"/seller-promotions/items/{item_id}",
                         app_version="v2")
    except Exception:  # noqa: BLE001
        return None, None
    for o in (ofertas or []):
        if o.get("min_discounted_price") and o.get("max_discounted_price"):
            return o["min_discounted_price"], o["max_discounted_price"]
    return None, None


# --------------------------------------------------------------- planilla

def ean_del_atributo(pub):
    """El EAN cargado como atributo GTIN. Lo tiene el 97% de las activas."""
    for a in pub.get("attributes") or []:
        if a.get("id") == "GTIN":
            return a.get("value_name") or a.get("value_id")
    return None


def indexar(pubs, solo_activas=True):
    """
    Devuelve (por_sku, por_ean, por_id). Una clave puede caer en varias
    publicaciones: son los espejos.
    """
    por_sku, por_ean, por_id = defaultdict(list), defaultdict(list), {}
    for p in pubs:
        por_id[p["id"]] = p
        if solo_activas and p.get("status") != "active":
            continue
        sku = normalizar_sku(sku_del_atributo(p))
        if sku:
            por_sku[sku].append(p)
        ean = normalizar_sku(ean_del_atributo(p))
        if ean:
            por_ean[ean].append(p)
    return dict(por_sku), dict(por_ean), por_id


def detectar_columnas(df):
    """Adivina cual columna es la clave y cual el descuento."""
    normal = {str(c).strip().lower(): c for c in df.columns}
    col_clave = next((normal[c] for c in COLS_CLAVE if c in normal), None)
    col_pct = next((normal[c] for c in COLS_DESCUENTO if c in normal), None)

    # Si el descuento no matcheo por nombre, buscamos una columna numerica
    # cuyos valores caigan casi todos en un rango de porcentaje razonable.
    if col_pct is None:
        for c in df.columns:
            if c == col_clave:
                continue
            vals = [_a_porcentaje(v) for v in df[c].dropna().head(30)]
            vals = [v for v in vals if v is not None]
            if vals and sum(0.01 <= v <= 0.90 for v in vals) / len(vals) > 0.8:
                col_pct = c
                break
    return col_clave, col_pct


def _a_porcentaje(valor):
    """
    Lee el descuento tolerando como se escribe en una planilla.

    `30`, `30%`, `0,30` y `0.3` son todos 30%. La regla es: **arriba de 1 es
    porcentaje, de 1 para abajo es fraccion**. Un `0,5` se lee como 50% y no
    como medio por ciento, que a nadie le sirve — el minimo que acepta ML
    ronda el 5%.
    """
    if valor is None:
        return None
    s = str(valor).strip().replace("%", "").replace(" ", "").replace("\xa0", "")
    if not s or s.lower() in ("nan", "none", "-"):
        return None
    if "," in s and "." in s:
        s = (s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".")
             else s.replace(",", ""))
    elif "," in s:
        s = s.replace(",", ".")
    try:
        n = float(s)
    except ValueError:
        return None
    if n <= 0:
        return None
    return n / 100 if n > 1 else n


def leer_planilla(archivo):
    """Lee un Excel o CSV y devuelve el DataFrame crudo."""
    nombre = getattr(archivo, "name", str(archivo)).lower()
    if nombre.endswith(".csv"):
        return pd.read_csv(archivo, dtype=str)
    return pd.read_excel(archivo, dtype=str)


# --------------------------------------------------------------- simulacion

COLUMNAS_SIM = ["clave", "item_id", "sku", "titulo", "descuento",
                "precio_actual", "precio_promo", "precio_promo_vigente",
                "min_permitido", "max_permitido", "accion", "motivo"]


def simular(df, pubs, elegibles_camp, col_clave=None, col_pct=None, ml=None):
    """
    Una fila por publicacion a tocar (o por problema detectado), sin escribir
    nada en MercadoLibre.

    `elegibles_camp` es lo que devuelve `elegibles()`. `ml` es opcional: si se
    pasa, se usa para recuperar el rango de las que ya estan dadas de alta.

    Se manda **una llamada por publicacion**, incluidas las espejo. El alta se
    propaga entre publicaciones que comparten `user_product_id`, pero cada una
    tiene su propio precio original, asi que el mismo porcentaje da precios
    distintos. Repetir la llamada sobre una que ya quedo cubierta no hace daño:
    el POST es el mismo que corrige.
    """
    if col_clave is None or col_pct is None:
        detectadas = detectar_columnas(df)
        col_clave = col_clave or detectadas[0]
        col_pct = col_pct or detectadas[1]

    if not col_clave or not col_pct:
        raise ValueError(
            f"No pude identificar las columnas. Encontre: {list(df.columns)}. "
            "Necesito una de SKU/EAN/publicacion y una de descuento.")

    por_sku, por_ean, por_id = indexar(pubs)
    filas, vistos = [], set()

    for _, fila in df.iterrows():
        clave_cruda = str(fila.get(col_clave, "") or "").strip()
        if not clave_cruda or clave_cruda.lower() == "nan":
            continue

        clave = normalizar_sku(clave_cruda)
        pct = _a_porcentaje(fila.get(col_pct))

        if clave in vistos:
            filas.append(_problema(clave_cruda, pct, "duplicado_en_planilla",
                                   "La clave aparece mas de una vez en la "
                                   "planilla. Se usa la primera."))
            continue
        vistos.add(clave)

        if pct is None:
            filas.append(_problema(clave_cruda, None, "descuento_invalido",
                                   f"No pude leer el descuento de la columna "
                                   f"'{col_pct}'."))
            continue
        if pct >= 1:
            filas.append(_problema(clave_cruda, pct, "descuento_invalido",
                                   "El descuento tiene que ser menor al 100%."))
            continue

        # La clave puede ser el codigo de publicacion, el SKU o el EAN.
        if clave.startswith(PREFIJO_ITEM) and clave[len(PREFIJO_ITEM):].isdigit():
            pub = por_id.get(clave)
            destinos = [pub] if pub else []
            if not destinos:
                filas.append(_problema(clave_cruda, pct, "no_encontrado",
                                       "Esa publicacion no esta en la cuenta."))
                continue
            if pub.get("status") != "active":
                filas.append(_problema(clave_cruda, pct, "no_activa",
                                       f"La publicacion esta "
                                       f"'{pub.get('status')}'."))
                continue
        else:
            destinos = por_sku.get(clave) or por_ean.get(clave) or []
            if not destinos:
                filas.append(_problema(
                    clave_cruda, pct, "no_encontrado",
                    "No hay ninguna publicacion activa con ese SKU ni ese EAN."))
                continue

        for pub in destinos:
            filas.append(_evaluar(pub, clave_cruda, pct, elegibles_camp, ml))

    return pd.DataFrame(filas, columns=COLUMNAS_SIM)


def _problema(clave, pct, accion, motivo):
    return {"clave": clave, "item_id": "", "sku": "", "titulo": "",
            "descuento": pct, "precio_actual": None, "precio_promo": None,
            "precio_promo_vigente": None, "min_permitido": None,
            "max_permitido": None, "accion": accion, "motivo": motivo}


def _evaluar(pub, clave, pct, elegibles_camp, ml=None):
    """Decide que pasa con una publicacion concreta."""
    base = {"clave": clave, "item_id": pub["id"],
            "sku": normalizar_sku(sku_del_atributo(pub)),
            "titulo": (pub.get("title") or "")[:70], "descuento": pct}

    eleg = elegibles_camp.get(pub["id"])
    if not eleg:
        return {**base, "precio_actual": pub.get("price"), "precio_promo": None,
                "precio_promo_vigente": None, "min_permitido": None,
                "max_permitido": None, "accion": "no_elegible",
                "motivo": "MercadoLibre no admite esta publicacion en esta "
                          "campaña."}

    original = float(eleg.get("original_price") or pub.get("price") or 0)
    if original <= 0:
        return {**base, "precio_actual": original, "precio_promo": None,
                "precio_promo_vigente": None, "min_permitido": None,
                "max_permitido": None, "accion": "sin_precio",
                "motivo": "La publicacion no tiene precio."}

    deal = round(original * (1 - pct), 2)
    mn, mx = eleg.get("min_precio"), eleg.get("max_precio")
    if (mn is None or mx is None) and ml is not None:
        mn, mx = rango_del_item(ml, pub["id"])

    vigente = eleg.get("precio_promo_actual")
    fila = {**base, "precio_actual": original, "precio_promo": deal,
            "precio_promo_vigente": vigente, "min_permitido": mn,
            "max_permitido": mx}

    # El rango lo fija ML por publicacion. Pasarse da 400 al aplicar, asi que
    # se atrapa antes y se dice de cuanto a cuanto se puede.
    if mn is not None and mx is not None:
        if deal > mx:
            return {**fila, "accion": "fuera_de_rango",
                    "motivo": (f"El {pct:.1%} es poco para esta publicacion: "
                               f"MercadoLibre pide entre {1 - mx / original:.1%} "
                               f"y {1 - mn / original:.1%} de descuento.")}
        if deal < mn:
            return {**fila, "accion": "fuera_de_rango",
                    "motivo": (f"El {pct:.1%} es demasiado: MercadoLibre "
                               f"permite hasta {1 - mn / original:.1%} en esta "
                               f"publicacion.")}
    else:
        fila["motivo"] = "Sin rango informado por ML: puede rebotar al aplicar."

    if vigente is not None and abs(float(vigente) - deal) < 0.01:
        return {**fila, "accion": "sin_cambio",
                "motivo": f"Ya esta en la campaña a ${deal:,.2f}."}

    if vigente is not None:
        return {**fila, "accion": "actualizar",
                "motivo": (f"Ya esta en la campaña a ${vigente:,.2f}; "
                           f"pasa a ${deal:,.2f}.")}

    return {**fila, "accion": "alta",
            "motivo": fila.get("motivo") or "Alta nueva en la campaña."}


def resumen(sim):
    """Conteos por accion, para mostrar antes de aplicar."""
    if sim.empty:
        return {}
    return sim["accion"].value_counts().to_dict()


# --------------------------------------------------------------- aplicacion

ACCIONES_QUE_ESCRIBEN = ("alta", "actualizar")


def aplicar(ml, sim, campana_id, operador="", callback=None, omitir=None):
    """
    Da de alta (o corrige) las publicaciones de la simulacion en la campaña.

    **Escribe en la cuenta de verdad**: cambia el precio que ve el comprador.
    Cada movimiento queda en la auditoria.

    `omitir` es un conjunto de item_id ya aplicados, para retomar una corrida
    que se corto sin repetir lo que ya se hizo.

    Ninguna publicacion puede llevarse puesta la corrida: cada una se resuelve
    a OK o ERROR y se sigue. Si una excepcion se escapara, se perderia el
    registro de todo lo que ya se aplico bien.
    """
    import almacen

    omitir = set(omitir or ())
    pendientes = sim[sim["accion"].isin(ACCIONES_QUE_ESCRIBEN)].copy()
    pendientes = pendientes[~pendientes["item_id"].isin(omitir)]

    filas = []
    total = len(pendientes)
    nota_base = f"carga masiva promo {campana_id} {datetime.now():%Y-%m-%d %H:%M}"

    for n, (_, f) in enumerate(pendientes.iterrows(), start=1):
        deal = round(float(f["precio_promo"]), 2)
        try:
            ml.post(f"/seller-promotions/items/{f['item_id']}",
                    payload={"promotion_type": TIPO,
                             "promotion_id": campana_id,
                             "deal_price": deal},
                    app_version="v2")
            resultado, detalle = "OK", ""
        except Exception as e:  # noqa: BLE001
            resultado, detalle = "ERROR", f"{type(e).__name__}: {str(e)[:220]}"

        almacen.append_auditoria([{
            "fecha": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "item_id": f["item_id"],
            "campo": f"promocion:{TIPO}:{campana_id}",
            "valor_anterior": (f["precio_promo_vigente"]
                               if pd.notna(f["precio_promo_vigente"])
                               else f["precio_actual"]),
            "valor_nuevo": deal,
            "resultado": resultado if resultado == "OK" else f"ERROR: {detalle}",
            "operador": operador,
            "nota": f"{nota_base} — {f['descuento']:.1%} off ({f['accion']})",
        }])

        filas.append({**f.to_dict(), "resultado": resultado,
                      "detalle": detalle})
        if callback:
            callback(n, total, f["item_id"])
        time.sleep(PAUSA_ENTRE_ITEMS)

    return pd.DataFrame(filas)


def verificar(ml, campana_id, item_ids):
    """
    Que quedo realmente activo en la campaña.

    Hay que llamarlo **despues de un rato** (medio minuto alcanza): el listado
    tarda en reflejar lo que se acaba de escribir, y consultado enseguida
    devuelve el estado viejo. Si se lee al toque, un alta que funciono parece
    no haber funcionado.
    """
    activos = {}
    token, vueltas = None, 0
    while True:
        params = {"promotion_type": TIPO, "app_version": "v2", "limit": 50,
                  "status": "started"}
        if token:
            params["search_after"] = token
        r = ml.get(f"/seller-promotions/promotions/{campana_id}/items", **params)
        res = r.get("results") or []
        if not res:
            break
        for x in res:
            activos[x["id"]] = x.get("price")
        vueltas += 1
        token = (r.get("paging") or {}).get("searchAfter")
        if not token or vueltas > 60:
            break

    return pd.DataFrame([{"item_id": i, "activa": i in activos,
                          "precio_promo": activos.get(i)}
                         for i in item_ids])


def dar_de_baja(ml, item_id, campana_id):
    """
    Saca una publicacion de la campaña. Devuelve (ok, detalle).

    Solo funciona con la oferta ya consolidada: llamado a los pocos segundos
    del alta contesta "No offers found for item" o no hace nada.
    """
    try:
        ml.delete(f"/seller-promotions/items/{item_id}", promotion_type=TIPO,
                  promotion_id=campana_id, app_version="v2")
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:250]


# --------------------------------------------------------------- terminal

def main():
    ml = Meli(verbose=False)
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if not args:
        camp = campanas_propias(ml)
        print("=" * 70)
        print("CAMPAÑAS PROPIAS (el descuento lo elegis vos)")
        print("=" * 70)
        if not len(camp):
            print("\nNo hay ninguna vigente. Se crean desde el panel de "
                  "MercadoLibre:\n  Publicaciones -> Promociones -> Crear "
                  "campaña propia.\n(Por API no se pueden crear.)")
            return 0
        for _, c in camp.iterrows():
            print(f"\n  {c['campana_id']}  {c['nombre']}")
            print(f"     {c['estado']} · del {c['desde']} al {c['hasta']}")
        print(f"\nPara ver los elegibles:  python {Path(__file__).name} "
              f"{camp.iloc[0]['campana_id']}")
        return 0

    campana_id = args[0]
    print(f"Trayendo elegibles de {campana_id}...")
    eleg = elegibles(ml, campana_id,
                     callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 60)
    print(f"  {len(eleg)} publicaciones elegibles.")

    activas = [e for e in eleg.values() if e["estado_promo"] == "started"]
    print(f"  {len(activas)} ya estan con descuento en esta campaña.")

    if len(args) < 2:
        rangos = [(1 - e["max_precio"] / e["original_price"],
                   1 - e["min_precio"] / e["original_price"])
                  for e in eleg.values()
                  if e.get("min_precio") and e.get("original_price")]
        if rangos:
            print(f"\n  Descuento minimo que pide ML: entre "
                  f"{min(r[0] for r in rangos):.1%} y "
                  f"{max(r[0] for r in rangos):.1%} segun la publicacion.")
            print(f"  Descuento maximo permitido:   hasta "
                  f"{max(r[1] for r in rangos):.1%}.")
        return 0

    ruta = Path(args[1])
    if not ruta.exists():
        print(f"\nNo existe {ruta}")
        return 1

    from catalogo import cargar_catalogo
    pubs = cargar_catalogo(ml)
    df = leer_planilla(ruta)
    col_clave, col_pct = detectar_columnas(df)
    print(f"\nColumnas detectadas: clave='{col_clave}' descuento='{col_pct}'")

    sim = simular(df, pubs, eleg, col_clave, col_pct, ml=ml)
    print("\n" + "=" * 70)
    print("SIMULACION")
    print("=" * 70)
    for accion, n in resumen(sim).items():
        print(f"  {accion:<24} {n:>5}")

    problemas = sim[~sim["accion"].isin(ACCIONES_QUE_ESCRIBEN)]
    if len(problemas):
        print(f"\n  No se van a aplicar ({len(problemas)}):")
        for _, f in problemas.head(15).iterrows():
            print(f"    {f['clave']:<22} {f['accion']:<20} {f['motivo'][:70]}")

    salida = DIR / "promos_planilla_simulacion.csv"
    sim.to_csv(salida, index=False)
    print(f"\nSimulacion guardada en {salida.name}. "
          "Para aplicar, usar la app (seccion Promociones por planilla).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
