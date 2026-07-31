#!/usr/bin/env python3
"""
Rentabilidad por SKU.

Junta tres cosas:
  1. el costo del producto (planilla que carga el operador),
  2. el precio de venta actual en MercadoLibre,
  3. los cargos reales por unidad, calculados de las ventas historicas
     (comision, recargo por financiacion, cargo fijo y envio que pago SUPRABOND).

Con eso saca el margen. Los cargos NO se estiman con una tabla teorica: se
promedian de lo que ML efectivamente cobro en cada venta de ese SKU.

    python rentabilidad.py 90        -> baja 90 dias de historia y la cachea
    python rentabilidad.py 90 --envios  -> ademas trae el costo de envio real
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from meli import Meli, MeliError
from ventas import traer_ordenes

DIR = Path(__file__).resolve().parent
CACHE_HIST = DIR / "historico_ventas.json"
CACHE_ENVIOS = DIR / "costos_envio.json"

# El usuario de SUPRABOND: en los costos de envio hay que mirar el sender
# que somos nosotros, no el comprador.
COLS_COSTO = ["costo", "costo unitario", "costo_unitario", "cost",
              "precio costo", "precio_costo", "costo producto"]
COLS_SKU = ["sku", "codigo", "código", "articulo", "artículo"]


# ------------------------------------------------------------------ historico

def traer_historico(ml, dias=90, refrescar=False):
    """Baja las ordenes del periodo y las cachea (son varias llamadas)."""
    if CACHE_HIST.exists() and not refrescar:
        datos = json.loads(CACHE_HIST.read_text(encoding="utf-8"))
        if datos.get("dias") == dias:
            return datos["ordenes"]

    hasta = datetime.now()
    desde = hasta - timedelta(days=dias)
    ordenes = traer_ordenes(ml, desde, hasta)

    CACHE_HIST.write_text(json.dumps(
        {"dias": dias, "bajado": hasta.strftime("%Y-%m-%d %H:%M"),
         "ordenes": ordenes}, ensure_ascii=False), encoding="utf-8")
    return ordenes


def costo_envio_vendedor(costos):
    """
    De la respuesta de /shipments/{id}/costs saca lo que pago el VENDEDOR.
    Si el envio lo paga el comprador, para nosotros el costo es cero.
    """
    total = 0.0
    for s in costos.get("senders") or []:
        total += float(s.get("cost") or 0)
    return total


def traer_costos_envio(ml, ordenes, refrescar=False, callback=None,
                       muestra_por_sku=5):
    """
    Trae el costo de envio de las ordenes. Es UNA llamada por envio, asi que
    pedirlas todas con miles de ordenes tarda muchisimo.

    Por eso muestreamos: alcanza con unas pocas ventas por SKU para sacar un
    promedio representativo del envio. `muestra_por_sku=None` trae todas.
    Todo se cachea por shipment_id, asi que la segunda corrida es gratis.
    """
    cache = {}
    if CACHE_ENVIOS.exists() and not refrescar:
        cache = json.loads(CACHE_ENVIOS.read_text(encoding="utf-8"))

    # Elegimos que envios consultar, repartiendo la muestra entre SKU para no
    # gastar todas las llamadas en el producto que mas vende.
    por_sku = defaultdict(list)
    for o in ordenes:
        sid = (o.get("shipping") or {}).get("id")
        if not sid or str(sid) in cache:
            continue
        for it in o.get("order_items") or []:
            sku = (it["item"].get("seller_sku") or "").strip().upper()
            if sku:
                por_sku[sku].append(str(sid))
                break

    if muestra_por_sku:
        ids = [sid for sids in por_sku.values() for sid in sids[:muestra_por_sku]]
    else:
        ids = [sid for sids in por_sku.values() for sid in sids]
    ids = list(dict.fromkeys(ids))

    for i, sid in enumerate(ids, start=1):
        try:
            cache[sid] = costo_envio_vendedor(ml.get(f"/shipments/{sid}/costs"))
        except Exception:  # noqa: BLE001
            cache[sid] = None       # envio sin costos accesibles
        if callback and i % 25 == 0:
            callback(i, len(ids))

    CACHE_ENVIOS.write_text(json.dumps(cache), encoding="utf-8")
    return cache


# ------------------------------------------------------------------ cargos

def cargos_por_sku(ordenes, costos_envio=None):
    """
    Promedia por SKU lo que costo vender una unidad.

    Solo mira ordenes pagadas: las canceladas no representan una venta real.
    """
    acc = defaultdict(lambda: {"unidades": 0, "ingreso": 0.0, "comision": 0.0,
                               "envio": 0.0, "unid_con_envio": 0,
                               "ordenes": 0, "sin_fee": 0})

    for o in ordenes:
        if o.get("status") not in ("paid", "partially_refunded"):
            continue

        items = o.get("order_items") or []
        # El envio es por orden, no por item: si la orden trae varios SKU lo
        # prorrateamos por unidades para no cargarselo todo al primero.
        sid = str((o.get("shipping") or {}).get("id") or "")
        # Ojo: si esta orden no entro en la muestra de envios, su costo es
        # desconocido, NO cero. Se excluye del promedio en vez de bajarlo.
        envio_orden = (costos_envio or {}).get(sid)
        hay_envio = envio_orden is not None
        envio_orden = envio_orden or 0.0
        unidades_orden = sum(it.get("quantity") or 0 for it in items) or 1

        for it in items:
            sku = (it["item"].get("seller_sku")
                   or it["item"].get("seller_custom_field") or "").strip().upper()
            if not sku:
                continue

            qty = it.get("quantity") or 0
            fee = it.get("sale_fee")
            a = acc[sku]
            a["unidades"] += qty
            a["ingreso"] += (it.get("unit_price") or 0) * qty
            a["ordenes"] += 1
            if fee is None:
                a["sin_fee"] += 1
            else:
                # sale_fee es POR UNIDAD (verificado contra /listing_prices).
                a["comision"] += fee * qty
            if hay_envio:
                a["envio"] += envio_orden * (qty / unidades_orden)
                a["unid_con_envio"] += qty

    filas = []
    for sku, a in acc.items():
        u = a["unidades"] or 1
        # El envio se promedia solo sobre las unidades de las que tenemos dato.
        u_envio = a["unid_con_envio"]
        filas.append({
            "sku": sku,
            "unidades_vendidas": a["unidades"],
            "ordenes": a["ordenes"],
            "precio_prom": a["ingreso"] / u,
            "comision_prom": a["comision"] / u,
            "envio_prom": (a["envio"] / u_envio) if u_envio else 0.0,
            "cobertura_envio": (u_envio / u) if u else 0.0,
            "items_sin_comision": a["sin_fee"],
        })
    return pd.DataFrame(filas)


# ------------------------------------------------------------------ planilla

def leer_costos(archivo):
    """Lee la planilla de costos del operador: una columna SKU y una de costo."""
    nombre = getattr(archivo, "name", str(archivo)).lower()
    df = (pd.read_csv(archivo, dtype=str) if nombre.endswith(".csv")
          else pd.read_excel(archivo, dtype=str))

    normal = {str(c).strip().lower(): c for c in df.columns}
    col_sku = next((normal[c] for c in COLS_SKU if c in normal), None)
    col_costo = next((normal[c] for c in COLS_COSTO if c in normal), None)
    if not col_sku or not col_costo:
        raise ValueError(
            f"La planilla necesita una columna de SKU y una de costo. "
            f"Encontre: {list(df.columns)}")

    from actualizador import _a_numero
    out = pd.DataFrame({
        "sku": df[col_sku].astype(str).str.strip().str.upper(),
        "costo": df[col_costo].map(_a_numero),
    })
    return out[out["sku"].ne("") & out["sku"].ne("NAN")].dropna(subset=["costo"])


# ------------------------------------------------------- costos guardados

HOJA_COSTOS = "costos"
COLS_COSTOS = ["sku", "costo", "fecha", "operador"]


def guardar_costos(costos_df, operador=""):
    """
    Deja la planilla de costos guardada para no tener que subirla cada vez.

    Va a la Google Sheet (o al CSV local si no hay Sheet configurada) por el
    mismo motivo que los tokens: en Streamlit Cloud el disco se borra en cada
    reinicio. Se **reemplaza** entera, no se acumula: la ultima planilla que
    sube el operador es la verdad.
    """
    import almacen

    sello = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    filas = [{"sku": f["sku"], "costo": f["costo"],
              "fecha": sello, "operador": operador}
             for _, f in costos_df.iterrows()]
    ok, detalle = almacen.reescribir_hoja(HOJA_COSTOS, COLS_COSTOS, filas)
    return ok, (detalle or sello)


def costos_guardados():
    """
    (DataFrame sku/costo, cuando se guardo). DataFrame vacio si no hay nada.

    Nunca lanza: si la hoja no existe o falla la lectura, se devuelve vacio
    para que la seccion siga funcionando pidiendo la planilla a mano.
    """
    import almacen

    try:
        filas = almacen.leer_hoja(HOJA_COSTOS, COLS_COSTOS)
    except Exception:
        return pd.DataFrame(columns=["sku", "costo"]), ""

    if not filas:
        return pd.DataFrame(columns=["sku", "costo"]), ""

    from actualizador import _a_numero
    df = pd.DataFrame(filas)
    df["sku"] = df["sku"].astype(str).str.strip().str.upper()
    df["costo"] = df["costo"].map(_a_numero)
    cuando = str(df["fecha"].iloc[0]) if "fecha" in df else ""
    df = df[df["sku"].ne("")].dropna(subset=["costo"])
    return df[["sku", "costo"]], cuando


# ------------------------------------------------------------------ calculo

def items_de_costos(costos_df, pubs):
    """Publicaciones de referencia de los SKU de la planilla de costos."""
    from resolver import indexar_por_sku, resolver_precio
    indice = indexar_por_sku(pubs)
    ids = []
    for sku in costos_df["sku"]:
        res = resolver_precio(sku, indice)
        if res.ok:
            ids.append(res.destinos[0]["id"])
    return ids


def precios_reales(ml, item_ids, callback=None):
    """
    Devuelve el precio que REALMENTE paga el comprador de cada publicacion.

    Hace falta porque `price` del item es el precio de lista, y ~12% de las
    publicaciones activas tienen una promocion encima. Si se calcula el margen
    con el precio de lista, sale mas optimista de lo que es.
    """
    salida = {}
    ids = list(dict.fromkeys(item_ids))
    for i, iid in enumerate(ids, start=1):
        try:
            r = ml.get(f"/items/{iid}/sale_price")
            salida[iid] = float(r["amount"]) if r.get("amount") is not None else None
        except Exception:  # noqa: BLE001
            salida[iid] = None      # nos quedamos con el de lista
        if callback and i % 20 == 0:
            callback(i, len(ids))
    return salida


# Costos de estructura que no vienen de MercadoLibre y hay que cargarle a cada
# venta igual. Se aplican como porcentaje del ingreso **sin IVA**.
OTROS_CONCEPTOS = {"impuestos": 0.10, "logistico": 0.10, "general": 0.05}

# El logistico tiene tope: es 10% **o $9.000, lo que sea menor**. Mover una
# caja no cuesta el doble porque el producto valga el doble, asi que arriba de
# cierto precio el porcentaje deja de representar el costo real. El tope
# empieza a jugar cuando el ingreso sin IVA pasa de $90.000.
TOPE_LOGISTICO = 9000.0


def otros_conceptos_monto(ingreso, otros=None, tope_logistico=TOPE_LOGISTICO):
    """
    Cuanto se lleva cada concepto de estructura para un ingreso dado.

    Devuelve (dict por concepto, total). El unico con tope es el logistico.
    """
    o = dict(OTROS_CONCEPTOS)
    if otros:
        o.update(otros)
    detalle = {
        "impuestos": ingreso * o["impuestos"],
        "logistico": min(ingreso * o["logistico"], tope_logistico),
        "general": ingreso * o["general"],
    }
    return detalle, sum(detalle.values())


def calcular(costos_df, cargos_df, pubs, iva=0.0, precios_venta=None,
             otros_conceptos=None):
    """
    Cruza costos + cargos + precio actual y devuelve la rentabilidad por SKU.

    `iva`: si los precios de ML son con IVA y el costo esta sin IVA, pasar
    0.22 para descontarlo del ingreso antes de comparar. Por defecto 0 (se
    asume que costo y precio estan en la misma base).

    `precios_venta`: dict item_id -> precio real (de `precios_reales()`). Si
    se pasa, el margen se calcula sobre lo que efectivamente paga el
    comprador en vez del precio de lista.

    `otros_conceptos`: costos de estructura que no cobra MercadoLibre pero
    igual hay que cargarle a cada venta, como porcentaje del ingreso
    (impuestos, logistico, general). Por defecto los de `OTROS_CONCEPTOS`.
    Se calculan sobre el **ingreso ya sin IVA**, o sea la misma base contra la
    que se compara el costo, no sobre el precio de lista.
    """
    from resolver import indexar_por_sku, resolver_precio

    indice = indexar_por_sku(pubs)
    precios_venta = precios_venta or {}
    otros = dict(OTROS_CONCEPTOS)
    if otros_conceptos is not None:
        otros.update(otros_conceptos)

    filas = []
    for _, fila in costos_df.iterrows():
        sku, costo = fila["sku"], float(fila["costo"])

        res = resolver_precio(sku, indice)
        precio_lista = precio_actual = None
        item_id = tipo = ""
        if res.ok:
            # Si hay varias, tomamos la de referencia (la primera del destino).
            pub = res.destinos[0]
            item_id = pub["id"]
            precio_lista = pub.get("price")
            # El precio real manda: puede haber una promocion activa.
            precio_actual = precios_venta.get(item_id) or precio_lista
            tipo = "Premium" if pub.get("listing_type_id") == "gold_pro" else "Clasica"

        h = cargos_df[cargos_df["sku"] == sku]
        if len(h):
            h = h.iloc[0]
            comision, envio = float(h["comision_prom"]), float(h["envio_prom"])
            unidades = int(h["unidades_vendidas"])
            base = "historico"
        else:
            comision = envio = 0.0
            unidades = 0
            base = "sin_ventas"

        ingreso_neto = None
        margen = margen_pct = None
        margen_sin_otros = otros_monto = None
        detalle_otros = {}
        if precio_actual:
            ingreso_neto = float(precio_actual) / (1 + iva)
            margen_sin_otros = ingreso_neto - comision - envio - costo
            detalle_otros, otros_monto = otros_conceptos_monto(
                ingreso_neto, otros)
            margen = margen_sin_otros - otros_monto
            margen_pct = margen / float(precio_actual)

        en_promo = (precio_lista is not None and precio_actual is not None
                    and abs(precio_lista - precio_actual) > 0.01)

        filas.append({
            "sku": sku,
            "item_id": item_id,
            "tipo": tipo,
            "precio_ml": precio_actual,
            "precio_lista": precio_lista,
            "en_promo": en_promo,
            "costo": costo,
            "comision_prom": comision,
            "envio_prom": envio,
            "cargos_totales": comision + envio,
            "impuestos": detalle_otros.get("impuestos"),
            "logistico": detalle_otros.get("logistico"),
            "general": detalle_otros.get("general"),
            # Se avisa cuando el logistico quedo topeado: arriba de ese precio
            # el 10% ya no representa lo que cuesta mover la caja.
            "logistico_topeado": bool(
                ingreso_neto and detalle_otros.get("logistico", 0)
                >= TOPE_LOGISTICO - 0.01),
            "otros_conceptos": otros_monto,
            "margen_sin_otros": margen_sin_otros,
            "margen": margen,
            "margen_pct": margen_pct,
            "unidades_90d": unidades,
            "base_cargos": base,
            "estado": ("ok" if precio_actual else res.estado),
            "detalle": res.motivo,
        })

    df = pd.DataFrame(filas)
    return df.sort_values("margen_pct", na_position="last")


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 90
    con_envios = "--envios" in sys.argv

    ml = Meli(verbose=False)
    print(f"Trayendo ordenes de los ultimos {dias} dias...")
    ordenes = traer_historico(ml, dias)
    print(f"  {len(ordenes)} ordenes.")

    envios = None
    if con_envios:
        print("Trayendo costos de envio (una llamada por envio, tarda)...")
        envios = traer_costos_envio(
            ml, ordenes,
            callback=lambda i, t: print(f"  {i}/{t}...", end="\r"))
        print()

    cargos = cargos_por_sku(ordenes, envios)
    cargos = cargos.sort_values("unidades_vendidas", ascending=False)

    print(f"\nCargos calculados para {len(cargos)} SKU con ventas.\n")
    print(cargos.head(15).to_string(index=False,
          float_format=lambda x: f"{x:,.0f}"))

    cargos.to_csv(DIR / "cargos_por_sku.csv", index=False)
    print(f"\nGuardado en cargos_por_sku.csv")
    return cargos


if __name__ == "__main__":
    try:
        main()
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
