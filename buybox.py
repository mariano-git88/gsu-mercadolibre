#!/usr/bin/env python3
"""
Buy Box del catalogo: en que publicaciones ganas la venta y en cuales no.

    python buybox.py            -> todas las publicaciones de catalogo
    python buybox.py 200        -> solo las 200 que mas vendieron (mas rapido)

**Por que esto importa mas que cualquier otro analisis.** 216 de las 438
publicaciones activas compiten en una pagina de catalogo (otras 158 tienen
producto de catalogo asociado sin competir todavia). En esas
paginas todos los vendedores comparten la MISMA publicacion y MercadoLibre
elige a uno solo para mostrar: el que gana se lleva practicamente todas las
ventas, y el resto queda escondido detras de "otras opciones de compra". No es
una diferencia de posicion, es vender o no vender.

`/items/{id}/price_to_win` dice, para cada publicacion: si estas ganando, a que
precio ganarias, a que precio esta vendiendo el que gana hoy, y que palancas
tenes sin usar (Full, envio gratis, cuotas).

**La lectura que no es obvia.** El `price_to_win` casi nunca es igual al precio
del ganador. Suele ser bastante mas bajo. Eso NO es un error: MercadoLibre
pondera el precio junto con los beneficios de la publicacion, asi que si el
ganador tiene Full y vos no, para empatarle tenes que compensar con precio. La
diferencia entre lo que cobra el ganador y lo que tendrias que cobrar vos es,
literalmente, **lo que te cuesta en pesos no tener esas palancas**.

De ahi salen dos diagnosticos que piden cosas opuestas:

  - **Perdes por precio**: el ganador esta mas barato. Se arregla con precio.
  - **Perdes estando mas barato**: ya cobras menos que el ganador y aun asi
    perdes. Bajar mas el precio es tirar plata — lo que falta son las palancas.
    Aca es donde Full deja de ser una idea y se vuelve una cuenta concreta.

El calculo de lo que queda al precio para ganar usa los cargos reales de cada
SKU (comision y envio medidos de las ventas, no una tabla teorica). Es **antes
del costo de la mercaderia**, que la API no conoce: sirve para descartar los
casos donde ganar el Buy Box directamente da negativo.
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent
CACHE = DIR / "buybox_cache.json"

# Los competidores mueven precios todo el tiempo: mas viejo que esto, no sirve.
VIGENCIA_HORAS = 12

# Si alcanza con bajar menos que esto, es una decision facil.
BAJA_CHICA = 0.05
# Arriba de esto, ganar el Buy Box probablemente no valga la pena.
BAJA_GRANDE = 0.20

ESTADOS = {
    "winning": "ganando",
    "sharing_first_place": "compartiendo",
    "competing": "compitiendo",
    "not_listed": "no compite",
}


def _leer_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _vigente(entrada):
    if not entrada or "bajado" not in entrada:
        return False
    edad = time.time() - entrada["bajado"]
    return edad < VIGENCIA_HORAS * 3600


def traer_price_to_win(ml, item_ids, refrescar=False, callback=None):
    """
    item_id -> respuesta de /items/{id}/price_to_win (version v2).

    Es una llamada por publicacion (~0,3 s), asi que con el catalogo entero son
    unos 5 minutos. Se cachea 12 horas: los precios de los competidores se
    mueven, pero no cada media hora.
    """
    cache = _leer_cache()
    pendientes = [i for i in item_ids
                  if refrescar or not _vigente(cache.get(i))]

    for n, iid in enumerate(pendientes, start=1):
        try:
            r = ml.get(f"/items/{iid}/price_to_win", version="v2")
            r["bajado"] = time.time()
            cache[iid] = r
        except Exception:  # noqa: BLE001
            cache[iid] = {"status": "error", "bajado": time.time()}
        if callback and n % 20 == 0:
            callback(f"Buy Box {n}/{len(pendientes)}...")

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {i: cache.get(i, {}) for i in item_ids}


def marca(pub):
    """La marca cargada como atributo BRAND. Las 216 de catálogo la tienen."""
    for a in pub.get("attributes") or []:
        if a.get("id") == "BRAND" and a.get("value_name"):
            return str(a["value_name"]).strip()
    return ""


def palancas(entrada):
    """(las que ya usa, las que tiene disponibles sin usar)."""
    usadas, libres = [], []
    for b in (entrada.get("boosts") or []):
        if not isinstance(b, dict):
            continue
        nombre = b.get("description") or b.get("id")
        if b.get("status") == "boosted":
            usadas.append(nombre)
        else:
            libres.append(nombre)
    return usadas, libres


def analizar(ml, pubs=None, tope=None, cargos=None, unidades=None,
             refrescar=False, callback=None, pisos=None):
    """
    Devuelve el DataFrame de publicaciones de catalogo.

    `cargos` es el DataFrame de `rentabilidad.cargos_por_sku()`: si viene, se
    calcula que queda por unidad al precio para ganar. `unidades` es un dict
    SKU -> unidades del periodo, para priorizar por lo que realmente vende.

    `pisos` es {item_id: piso} de `lista_gsu.traer_pisos()`. Es el piso duro
    de las marcas propias (Costo x 1,85): cuando el precio para ganar queda
    por debajo, la publicacion se diagnostica **"no se puede sin perforar el
    piso"** y queda fuera de toda seleccion. Es una regla comercial, no una
    cuenta de margen, asi que no la abre ningun criterio de la pantalla.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    cat = [p for p in pubs
           if p.get("status") == "active" and p.get("catalog_listing")]

    # Priorizamos por ventas: si hay que cortar, que se corte por lo que menos
    # importa. `sold_quantity` es historico de toda la vida de la publicacion,
    # sirve para ordenar cuando no hay dato del periodo, pero **no se mezcla
    # con el en la misma columna**: son dos medidas distintas y ponerlas juntas
    # hace parecer que una publicacion sin ventas recientes vende muchisimo.
    def peso(p):
        sku = (sku_del_atributo(p) or "").strip().upper()
        if unidades and sku in unidades:
            return (1, unidades[sku])
        return (0, p.get("sold_quantity") or 0)

    cat.sort(key=peso, reverse=True)
    if tope:
        cat = cat[:tope]

    if callback:
        callback(f"Consultando el Buy Box de {len(cat)} publicaciones...")
    datos = traer_price_to_win(ml, [p["id"] for p in cat],
                               refrescar=refrescar, callback=callback)

    from tramos import envio_a_cargo

    # Cargos por SKU para saber que queda al precio para ganar.
    tasa_comision, envio_fijo = {}, {}
    if cargos is not None and len(cargos):
        for _, f in cargos.iterrows():
            precio = f["precio_prom"] or 0
            if precio > 0:
                tasa_comision[f["sku"]] = f["comision_prom"] / precio
            envio_fijo[f["sku"]] = f["envio_prom"] or 0.0

    filas = []
    for p in cat:
        d = datos.get(p["id"]) or {}
        estado_api = d.get("status")
        sku = (sku_del_atributo(p) or "").strip().upper()

        actual = d.get("current_price")
        ptw = d.get("price_to_win")
        ganador = (d.get("winner") or {}).get("price")
        usadas, libres = palancas(d)

        bajar = (actual - ptw) if (actual is not None and ptw is not None) else None
        bajar_pct = (bajar / actual) if (bajar is not None and actual) else None
        # Lo que cuesta no tener las palancas del ganador: el ganador puede
        # cobrar mas caro que lo que vos necesitas cobrar para empatarle.
        penalizacion = ((ganador - ptw)
                        if (ganador is not None and ptw is not None) else None)

        queda = None
        if ptw is not None and sku in envio_fijo:
            queda = (ptw * (1 - tasa_comision.get(sku, 0.0))
                     - envio_a_cargo(ptw, envio_fijo[sku]))

        piso_marca = (pisos or {}).get(p["id"])

        if estado_api == "winning":
            diag = "ganando"
        elif estado_api == "sharing_first_place":
            diag = "compartiendo"
        elif estado_api == "not_listed":
            diag = "no compite"
        elif estado_api == "error":
            diag = "sin dato"
        elif (piso_marca is not None and ptw is not None
              and ptw < piso_marca):
            # Piso duro de marca propia: ganar costaria perforarlo. Se dice
            # antes que cualquier diagnostico de precio para que no aparezca
            # como "alcanza con bajar poco" algo que no se va a poder hacer.
            diag = "no se puede sin perforar el piso"
        elif (actual is not None and ganador is not None
              and actual <= ganador):
            # Ya sos mas barato y perdes igual: el problema no es el precio.
            diag = "perdés estando más barato"
        elif bajar_pct is not None and bajar_pct <= BAJA_CHICA:
            diag = "alcanza con bajar poco"
        elif bajar_pct is not None and bajar_pct >= BAJA_GRANDE:
            diag = "habría que bajar mucho"
        else:
            diag = "perdés por precio"

        filas.append({
            "item_id": p["id"],
            "sku": sku,
            "marca": marca(p),
            "titulo": (p.get("title") or "")[:60],
            "diagnostico": diag,
            "precio_actual": actual,
            "precio_para_ganar": ptw,
            "precio_ganador": ganador,
            "bajar": bajar,
            "bajar_pct": bajar_pct,
            "penalizacion_palancas": penalizacion,
            "piso_marca": piso_marca,
            "queda_al_precio_para_ganar": queda,
            "palancas_sin_usar": ", ".join(libres),
            "palancas_activas": ", ".join(usadas),
            "competidores_primeros": d.get("competitors_sharing_first_place"),
            "share_de_visitas": d.get("visit_share"),
            "unidades": (unidades or {}).get(sku, 0),
            "vendidas_historico": p.get("sold_quantity") or 0,
            "producto_catalogo": p.get("catalog_product_id"),
        })

    df = pd.DataFrame(filas)
    if not len(df):
        return df

    orden = {"perdés estando más barato": 0, "alcanza con bajar poco": 1,
             "perdés por precio": 2, "habría que bajar mucho": 3,
             "no se puede sin perforar el piso": 4,
             "compartiendo": 5, "ganando": 6, "no compite": 7, "sin dato": 8}
    df["_orden"] = df["diagnostico"].map(orden)
    df = df.sort_values(["_orden", "unidades", "vendidas_historico"],
                        ascending=[True, False, False]).drop(columns=["_orden"])
    return df


# ------------------------------------------------------------------ margen

def con_costos(df, costos_df, cargos_df, iva=0.0, margen_minimo=0.0,
               otros_conceptos=None):
    """
    Agrega a cada fila el margen que quedaria vendiendo al precio para ganar.

    Responde la pregunta concreta: **en que publicaciones puedo bajar hasta el
    precio del Buy Box y seguir ganando plata.**

    Dos precauciones que cambian el resultado:

    1. La comision NO se estima con una regla de tres sobre la comision
       actual. MercadoLibre cobra un porcentaje **mas un cargo fijo por
       unidad**, y ese cargo salta en escalones (ver `tramos.py`). El
       porcentaje real de cada SKU se despeja de lo que ML cobro de verdad
       — asi queda bien tanto para Clasica como para Premium — y despues se
       aplica al precio nuevo con el cargo fijo **del tramo del precio
       nuevo**.

    2. Por eso mismo se marca `cruza_escalon`: bajar el precio puede meterte
       en un tramo con cargo fijo mas caro. En Uruguay el salto grande esta en
       $1.000, y va **a favor** de bajar: por debajo de esa linea el envio lo
       paga el comprador. Bajar de $1.050 a $999 sale mas barato que los $51
       de diferencia, porque te saca ~$160 de envio de encima.

    3. Se descuentan los **otros conceptos** (impuestos, logistico, general)
       con los mismos porcentajes que usa Rentabilidad. Tiene que ser asi:
       esta pantalla **baja precios de verdad**, y si calculara el margen sin
       los costos de estructura aprobaria bajas que Rentabilidad marca como
       perdida.
    """
    from tramos import cargo_fijo, envio_a_cargo
    from rentabilidad import OTROS_CONCEPTOS, otros_conceptos_monto

    otros = dict(OTROS_CONCEPTOS)
    if otros_conceptos is not None:
        otros.update(otros_conceptos)

    if not len(df):
        return df

    costos = {}
    if costos_df is not None and len(costos_df):
        costos = dict(zip(costos_df["sku"].str.upper(), costos_df["costo"]))

    # Porcentaje real de comision por SKU, despejado de lo que ML cobro.
    pct, envio = {}, {}
    if cargos_df is not None and len(cargos_df):
        for _, f in cargos_df.iterrows():
            p = f["precio_prom"] or 0
            if p > 0:
                variable = (f["comision_prom"] or 0) - cargo_fijo(p)
                pct[f["sku"]] = max(variable / p, 0.0)
            envio[f["sku"]] = f["envio_prom"] or 0.0

    def cargos_a(precio, sku):
        if precio is None or sku not in pct:
            return None
        return precio * pct[sku] + cargo_fijo(precio)

    def margen_a(precio, sku):
        if precio is None or sku not in costos or sku not in pct:
            return None
        com = cargos_a(precio, sku)
        ingreso = precio / (1 + iva)
        _, otros_monto = otros_conceptos_monto(ingreso, otros)
        # El envio es un escalon del precio, no una constante del SKU:
        # abajo de $1.000 lo paga el comprador. Usar el promedio
        # historico tal cual hacia ver como neutro un cambio de precio
        # que en realidad cruza el umbral del envio.
        return (ingreso - com - envio_a_cargo(precio, envio.get(sku, 0.0))
                - costos[sku] - otros_monto)

    out = df.copy()
    out["costo"] = out["sku"].map(lambda s: costos.get(s))
    out["comision_al_precio_para_ganar"] = [
        cargos_a(r["precio_para_ganar"], r["sku"]) for _, r in out.iterrows()]
    out["margen_hoy"] = [margen_a(r["precio_actual"], r["sku"])
                         for _, r in out.iterrows()]
    out["margen_al_ganar"] = [margen_a(r["precio_para_ganar"], r["sku"])
                              for _, r in out.iterrows()]
    out["margen_al_ganar_pct"] = [
        (m / p) if (m is not None and p) else None
        for m, p in zip(out["margen_al_ganar"], out["precio_para_ganar"])]
    out["cruza_escalon"] = [
        (cargo_fijo(a) != cargo_fijo(b))
        if (a is not None and b is not None) else False
        for a, b in zip(out["precio_actual"], out["precio_para_ganar"])]

    def veredicto(f):
        if f["diagnostico"] in ("ganando", "compartiendo", "no compite",
                                "sin dato"):
            return f["diagnostico"]
        if f["costo"] is None or pd.isna(f["costo"]):
            return "sin costo cargado"
        if f["margen_al_ganar"] is None or pd.isna(f["margen_al_ganar"]):
            return "sin datos de cargos"
        if f["margen_al_ganar_pct"] is not None and \
                f["margen_al_ganar_pct"] >= margen_minimo and \
                f["margen_al_ganar"] > 0:
            return "podés ganar y seguir ganando plata"
        if f["margen_al_ganar"] > 0:
            return "ganás pero con margen flaco"
        return "ganar el Buy Box da pérdida"

    out["veredicto"] = out.apply(veredicto, axis=1)

    orden = {"podés ganar y seguir ganando plata": 0,
             "ganás pero con margen flaco": 1,
             "ganar el Buy Box da pérdida": 2, "sin costo cargado": 3,
             "sin datos de cargos": 4, "compartiendo": 5, "ganando": 6,
             "no compite": 7, "sin dato": 8}
    out["_o"] = out["veredicto"].map(orden)
    return out.sort_values(["_o", "unidades", "margen_al_ganar"],
                           ascending=[True, False, False]).drop(columns=["_o"])


def resumen_costos(df):
    """Resumen de la vista con costos."""
    if not len(df) or "veredicto" not in df:
        return {}
    ganables = df[df["veredicto"] == "podés ganar y seguir ganando plata"]
    return {
        "ganables": len(ganables),
        "unidades_ganables": int(ganables["unidades"].sum()),
        "margen_promedio": float(ganables["margen_al_ganar"].mean())
        if len(ganables) else 0.0,
        "flacas": int((df["veredicto"] == "ganás pero con margen flaco").sum()),
        "perdida": int((df["veredicto"] == "ganar el Buy Box da pérdida").sum()),
        "sin_costo": int((df["veredicto"] == "sin costo cargado").sum()),
        "cruzan_escalon": int(df["cruza_escalon"].sum()),
    }


def resumen(df):
    if not len(df):
        return {}
    perdiendo = df[df["diagnostico"].isin(
        ["perdés estando más barato", "alcanza con bajar poco",
         "perdés por precio", "habría que bajar mucho"])]
    return {
        "publicaciones": len(df),
        "ganando": int((df["diagnostico"] == "ganando").sum()),
        "compartiendo": int((df["diagnostico"] == "compartiendo").sum()),
        "perdiendo": len(perdiendo),
        "mas_barato_y_perdiendo": int(
            (df["diagnostico"] == "perdés estando más barato").sum()),
        "baja_chica": int((df["diagnostico"] == "alcanza con bajar poco").sum()),
        "unidades_perdiendo": int(perdiendo["unidades"].sum()),
        "penalizacion_mediana": float(
            perdiendo["penalizacion_palancas"].median())
        if perdiendo["penalizacion_palancas"].notna().any() else None,
    }


# --------------------------------------------------------- bajar precios

# Nunca se baja mas que esto de una sola vez, aunque el criterio lo permita.
# Es la misma idea que `UMBRAL_ALERTA_PRECIO` en actualizador.py: atrapar el
# caso donde un dato raro de la API pide una baja absurda.
TECHO_DE_BAJA = 0.35

# Piso duro del margen. Se puede pedir vender a perdida a proposito (para
# entrar a una pagina de catalogo, para liquidar), pero no mas alla de esto:
# un margen peor casi siempre es un dato malo, no una decision.
PISO_DE_MARGEN = -0.50

# Pausa entre publicaciones al escribir en lote: baja los 429 de ML.
PAUSA_ENTRE_ITEMS = 0.25

# Diagnosticos donde hay Buy Box para ganar. El resto no se toca.
PERDIENDO = ("perdés estando más barato", "alcanza con bajar poco",
             "perdés por precio", "habría que bajar mucho")


def seleccionar(df, margen_minimo=0.10, baja_maxima=0.15, unidades_minimas=1,
                permitir_cruzar_escalon=False, marcas=None, items=None):
    """
    Aplica el criterio y devuelve las publicaciones a las que bajarles el
    precio.

    `margen_minimo` **puede ser negativo**: es el piso de rentabilidad que se
    esta dispuesto a aceptar. Con `-0.05` entran las publicaciones donde ganar
    el Buy Box deja hasta 5% de perdida, algo que puede tener sentido para
    entrar a una pagina de catalogo o para liquidar.

    `marcas` filtra por marca (lista). `items` restringe a una lista de
    `item_id`, para cuando el operador elige a mano en la tabla.

    Cuatro candados que no se pueden abrir desde afuera:

      - nunca se baja mas de `TECHO_DE_BAJA`, pase lo que pase;
      - el margen nunca puede quedar debajo de `PISO_DE_MARGEN`;
      - se saltean las que no tienen costo cargado. Sin costo no se sabe si
        se gana o se pierde, y adivinar eso con plata real no corresponde;
      - **nunca se baja por debajo del piso de marca** (`piso_marca`, o sea
        Costo x 1,85 de Suprabond, Bulit y Somerset). Ese piso es una decision
        comercial y no lo abre ningun criterio de la pantalla.
    """
    if not len(df) or "veredicto" not in df:
        return df.iloc[0:0] if len(df) else df

    piso = max(margen_minimo, PISO_DE_MARGEN)

    sel = df[
        df["diagnostico"].isin(PERDIENDO)
        & df["costo"].notna()
        & df["margen_al_ganar_pct"].notna()
        & (df["margen_al_ganar_pct"] >= piso)
        & (df["bajar_pct"] > 0)
        & (df["bajar_pct"] <= min(baja_maxima, TECHO_DE_BAJA))
        & (df["unidades"] >= unidades_minimas)
    ].copy()

    if not permitir_cruzar_escalon:
        # Bajar de tramo puede sumar un cargo fijo que no estaba. El margen ya
        # lo contempla, pero por defecto ni se ofrecen: es plata que se
        # regala por cruzar un escalon.
        sel = sel[~sel["cruza_escalon"]]

    # Piso de marca. Se aplica aca ademas del diagnostico porque el
    # diagnostico se calcula una sola vez, al analizar, y la seleccion puede
    # correrse con un DataFrame que venga de un cache anterior.
    if "piso_marca" in sel:
        perfora = (sel["piso_marca"].notna()
                   & (sel["precio_para_ganar"] < sel["piso_marca"]))
        sel = sel[~perfora]

    if marcas:
        sel = sel[sel["marca"].isin(marcas)]
    if items is not None:
        sel = sel[sel["item_id"].isin(list(items))]

    return sel.sort_values("unidades", ascending=False)


def aplicar(ml, seleccion, operador="", callback=None):
    """
    Baja el precio de las publicaciones elegidas al precio para ganar.

    **Escribe en la cuenta de verdad.** Cada cambio queda en la auditoria con
    el precio anterior, asi que se puede revertir a mano.

    Se toca la publicacion puntual por `item_id`, sin pasar por el resolver de
    SKU que usan Precios y Stock. Es a proposito: el Buy Box se gana o se
    pierde **por publicacion**, no por SKU. Dos publicaciones del mismo SKU
    compiten en paginas de catalogo distintas y pueden necesitar precios
    distintos.
    """
    filas = []
    total = len(seleccion)
    for n, (_, f) in enumerate(seleccion.iterrows(), start=1):
        nuevo = round(float(f["precio_para_ganar"]), 2)

        # Ultimo candado antes de escribir. `seleccionar()` ya filtra, pero
        # `aplicar()` tambien se puede llamar con una seleccion armada a mano
        # desde la tabla, y el piso de marca no puede depender de que quien
        # llama se haya acordado.
        piso = f.get("piso_marca") if hasattr(f, "get") else None
        if piso is not None and pd.notna(piso) and nuevo < float(piso):
            filas.append({
                "item_id": f["item_id"], "sku": f["sku"],
                "titulo": f["titulo"],
                "precio_anterior": f["precio_actual"],
                "precio_nuevo": nuevo,
                "margen_al_ganar": f.get("margen_al_ganar"),
                "resultado": "ERROR",
                "detalle": f"Perfora el piso de marca (${float(piso):,.2f}). "
                           "No se aplico.",
            })
            if callback:
                callback(n, total, f["item_id"])
            continue

        try:
            ok, detalle = ml.actualizar_publicacion(
                f["item_id"], {"price": nuevo},
                valores_previos={"price": f["precio_actual"]},
                operador=operador,
                nota=f"Buy Box: bajar para ganar (margen "
                     f"{f['margen_al_ganar_pct']:.1%})")
        except Exception as e:                     # noqa: BLE001
            # Una publicacion no puede llevarse puesta la corrida entera.
            ok, detalle = False, f"{type(e).__name__}: {str(e)[:200]}"
        filas.append({
            "item_id": f["item_id"],
            "sku": f["sku"],
            "titulo": f["titulo"],
            "precio_anterior": f["precio_actual"],
            "precio_nuevo": nuevo,
            "margen_al_ganar": f["margen_al_ganar"],
            "resultado": "OK" if ok else "ERROR",
            "detalle": "" if ok else str(detalle)[:200],
        })
        if callback:
            callback(n, total, f["item_id"])
        time.sleep(PAUSA_ENTRE_ITEMS)

    return pd.DataFrame(filas)


def main():
    tope = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    ml = Meli(verbose=False)

    import rentabilidad as rent
    print("Trayendo cargos reales por SKU...")
    try:
        ordenes = rent.traer_historico(ml, 90)
        envios = rent.traer_costos_envio(ml, ordenes, muestra_por_sku=5)
        cargos = rent.cargos_por_sku(ordenes, envios)
        unidades = dict(zip(cargos["sku"], cargos["unidades_vendidas"]))
    except MeliError:
        cargos, unidades = None, None

    df = analizar(ml, tope=tope, cargos=cargos, unidades=unidades,
                  callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 70)

    if not len(df):
        print("No hay publicaciones de catálogo.")
        return 0

    r = resumen(df)
    pes = lambda v: "—" if v is None or pd.isna(v) else f"${v:,.0f}".replace(",", ".")

    print("=" * 74)
    print("BUY BOX DEL CATÁLOGO")
    print("=" * 74)
    print(f"  Publicaciones de catálogo   {r['publicaciones']:>6}")
    print(f"  Ganando                     {r['ganando']:>6}")
    print(f"  Compartiendo primer lugar   {r['compartiendo']:>6}")
    print(f"  Perdiendo                   {r['perdiendo']:>6}")
    print(f"    ... estando más barato    {r['mas_barato_y_perdiendo']:>6}  <- no es precio")
    print(f"    ... con bajar poco        {r['baja_chica']:>6}  <- lo más fácil")
    print(f"  Unidades que venden las que pierden: {r['unidades_perdiendo']:>6}")
    if r["penalizacion_mediana"] is not None:
        print(f"  Penalización mediana por falta de palancas: "
              f"{pes(r['penalizacion_mediana'])}")

    facil = df[df["diagnostico"] == "alcanza con bajar poco"]
    if len(facil):
        print(f"\n  LO MÁS FÁCIL — bajar menos de {BAJA_CHICA:.0%} y ganar "
              f"({len(facil)}):")
        for _, f in facil.head(10).iterrows():
            print(f"    {f['item_id']}  {pes(f['precio_actual'])} -> "
                  f"{pes(f['precio_para_ganar'])} ({f['bajar_pct']:.1%}) · "
                  f"{int(f['unidades'])} u")
            print(f"       {f['titulo']}")
            if pd.notna(f["queda_al_precio_para_ganar"]):
                print(f"       queda por unidad antes del costo: "
                      f"{pes(f['queda_al_precio_para_ganar'])}")

    barato = df[df["diagnostico"] == "perdés estando más barato"]
    if len(barato):
        print(f"\n  PERDÉS ESTANDO MÁS BARATO ({len(barato)}) — acá bajar el "
              f"precio no sirve:")
        for _, f in barato.head(10).iterrows():
            print(f"    {f['item_id']}  vos {pes(f['precio_actual'])} vs "
                  f"ganador {pes(f['precio_ganador'])} · {int(f['unidades'])} u")
            print(f"       {f['titulo']}")
            print(f"       te falta: {f['palancas_sin_usar'] or '—'}")

    df.to_csv(DIR / "buybox.csv", index=False)
    print(f"\nGuardado en buybox.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
