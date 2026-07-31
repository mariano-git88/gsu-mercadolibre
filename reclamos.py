#!/usr/bin/env python3
"""
Reclamos por producto.

    python reclamos.py            -> ultimos 90 dias
    python reclamos.py 30

Un reclamo cuesta plata dos veces: la devolucion concreta y la reputacion. Lo
que interesa no es el numero total sino **que productos concentran los
reclamos**: un SKU que reclama el 8% de sus ventas cuando el promedio de la
cuenta es 1,5% tiene un problema de producto, de ficha o de embalaje.

Por eso la columna que manda es `tasa` (reclamos sobre unidades vendidas del
mismo periodo), no el conteo.

Trampas de la API, todas verificadas contra la cuenta real:

  - `/post-purchase/v1/claims/search` **exige al menos un filtro**: sin
    parametros tira 400. Se pide un `status` por vez.
  - **El filtro de fecha se ignora**: pedir `date_created.from` devuelve
    exactamente el mismo total que sin el (18.117 reclamos historicos). La
    unica forma de acotar el periodo es traer ordenado y cortar por fecha.
  - El unico orden que funciona es **`sort=date_desc`**. `date_created_desc`,
    `-date_created` y `sort_by/sort_order` **no dan error: se ignoran** y
    devuelven del mas viejo al mas nuevo. Si se usa uno de esos, se traen
    reclamos de 2019 creyendo que son de esta semana.
  - El reclamo no trae el producto. Apunta a un `resource` que puede ser
    `order` (directo), `shipment` (una llamada mas para sacar el `order_id`)
    o `payment` (**no hay forma publica de llegar al pedido**: el filtro
    `payment_id` de /orders/search tambien se ignora y devuelve todo). Esos
    quedan contados aparte como "sin producto identificado".
  - Los codigos de motivo se traducen con
    `/post-purchase/v1/claims/reasons/{id}`. Devuelve el codigo **canonico**,
    que puede ser distinto del pedido (PNR3210 -> PNR9502): es el mismo
    motivo renumerado, no un error.
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent
CACHE = DIR / "reclamos_cache.json"

BASE = "/post-purchase/v1/claims/search"
ESTADOS = ("opened", "closed", "cancelled")

# Una tasa de reclamo por encima de esto sobre un producto que vende
# bastante es un problema concreto, no ruido estadistico.
TASA_ALTA = 0.05
TASA_MEDIA = 0.02
# Debajo de esto la tasa no significa nada: un reclamo sobre 3 ventas da 33%.
MINIMO_UNIDADES = 10


def _fecha(claim):
    """'2026-01-12T16:33:24.000-04:00' -> datetime naive (hora local de ML)."""
    txt = claim.get("date_created") or ""
    try:
        return datetime.strptime(txt[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"envio_a_orden": {}, "orden_a_items": {}, "motivos": {}}


def _guardar_cache(datos):
    CACHE.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ traer

def traer_reclamos(ml, dias=90, callback=None):
    """
    Reclamos creados en los ultimos `dias`, de los tres estados.

    Como la API no filtra por fecha, se pide `sort=date_desc` y se corta en
    cuanto aparece uno mas viejo que el corte.
    """
    corte = datetime.now() - timedelta(days=dias)
    salida = []

    for estado in ESTADOS:
        offset = 0
        while True:
            pagina = ml.get(BASE, status=estado, sort="date_desc",
                            limit=50, offset=offset)
            filas = pagina.get("data") or []
            if not filas:
                break

            viejo = False
            for c in filas:
                f = _fecha(c)
                if f is None:
                    continue
                if f < corte:
                    viejo = True
                    break
                c["_fecha"] = f
                salida.append(c)

            if viejo:
                break
            offset += 50
            if callback:
                callback(f"Reclamos {estado}: {len(salida)}...")
            if offset >= (pagina.get("paging") or {}).get("total", 0):
                break

    return salida


def traducir_motivos(ml, codigos, cache=None):
    """Codigo de motivo -> texto legible. Se cachea: no cambia nunca."""
    cache = cache if cache is not None else _cache()
    motivos = cache.setdefault("motivos", {})
    for cod in {c for c in codigos if c}:
        if cod in motivos:
            continue
        try:
            r = ml.get(f"/post-purchase/v1/claims/reasons/{cod}")
            motivos[cod] = r.get("detail") or r.get("name") or cod
        except Exception:  # noqa: BLE001
            motivos[cod] = cod
    return motivos


def mapear_a_productos(ml, claims, ordenes_conocidas=None, callback=None):
    """
    claim_id -> lista de SKU del pedido reclamado.

    `ordenes_conocidas` es el historico ya bajado (evita pedir de nuevo las
    ordenes que ya tenemos). El resto se resuelve contra la API y se cachea.
    """
    cache = _cache()
    envio_a_orden = cache.setdefault("envio_a_orden", {})
    orden_a_items = cache.setdefault("orden_a_items", {})

    for o in (ordenes_conocidas or []):
        oid = str(o.get("id"))
        if oid not in orden_a_items:
            orden_a_items[oid] = [
                {"sku": (it["item"].get("seller_sku") or "").strip().upper(),
                 "titulo": it["item"].get("title") or ""}
                for it in (o.get("order_items") or [])]

    salida, sin_producto = {}, 0
    for i, c in enumerate(claims, start=1):
        recurso, rid = c.get("resource"), str(c.get("resource_id"))

        if recurso == "shipment":
            if rid not in envio_a_orden:
                try:
                    envio_a_orden[rid] = str(
                        ml.get(f"/shipments/{rid}").get("order_id") or "")
                except Exception:  # noqa: BLE001
                    envio_a_orden[rid] = ""
            oid = envio_a_orden[rid]
        elif recurso == "order":
            oid = rid
        else:
            # resource=payment: no hay forma publica de llegar al pedido.
            oid = ""

        if not oid:
            sin_producto += 1
            salida[c["id"]] = []
            continue

        if oid not in orden_a_items:
            try:
                o = ml.get(f"/orders/{oid}")
                orden_a_items[oid] = [
                    {"sku": (it["item"].get("seller_sku") or "").strip().upper(),
                     "titulo": it["item"].get("title") or ""}
                    for it in (o.get("order_items") or [])]
            except Exception:  # noqa: BLE001
                orden_a_items[oid] = []

        items = orden_a_items[oid]
        if not items:
            sin_producto += 1
        salida[c["id"]] = items

        if callback and i % 20 == 0:
            callback(f"Identificando productos {i}/{len(claims)}...")

    _guardar_cache(cache)
    return salida, sin_producto


# ------------------------------------------------------------------ analisis

def analizar(ml, dias=90, pubs=None, ordenes=None, callback=None):
    """
    Devuelve (df_por_sku, resumen).

    Las unidades vendidas del periodo salen del historico de ordenes, asi que
    la tasa compara reclamos contra ventas de la misma ventana. `ordenes`
    permite pasar un historico ya bajado y evitar que `traer_historico()`
    tenga que rehacer la descarga por pedirle otra ventana.
    """
    import rentabilidad as rent

    if callback:
        callback("Trayendo reclamos...")
    claims = traer_reclamos(ml, dias, callback=callback)

    if ordenes is None:
        if callback:
            callback("Trayendo ventas del período...")
        ordenes = rent.traer_historico(ml, dias)

    mapa, sin_producto = mapear_a_productos(
        ml, claims, ordenes_conocidas=ordenes, callback=callback)

    if callback:
        callback("Traduciendo motivos...")
    cache = _cache()
    motivos = traducir_motivos(ml, [c.get("reason_id") for c in claims], cache)
    _guardar_cache(cache)

    # Unidades vendidas por SKU en la misma ventana.
    vendidas = defaultdict(int)
    for o in ordenes:
        if o.get("status") not in ("paid", "partially_refunded"):
            continue
        for it in o.get("order_items") or []:
            sku = (it["item"].get("seller_sku") or "").strip().upper()
            if sku:
                vendidas[sku] += it.get("quantity") or 0

    titulos = {}
    for p in (pubs or []):
        sku = (sku_del_atributo(p) or "").strip().upper()
        if sku and sku not in titulos:
            titulos[sku] = (p.get("title") or "")[:60]

    acc = defaultdict(lambda: {"reclamos": 0, "abiertos": 0, "titulo": "",
                               "tipos": Counter(), "motivos": Counter(),
                               "ultimo": None})
    for c in claims:
        items = mapa.get(c["id"]) or []
        # Un reclamo puede tocar un pedido con varios SKU: se cuenta en cada
        # uno. No se prorratea: el reclamo es sobre el pedido entero y no
        # sabemos cual de los productos lo motivo.
        for it in {i["sku"]: i for i in items if i["sku"]}.values():
            a = acc[it["sku"]]
            a["reclamos"] += 1
            a["titulo"] = a["titulo"] or titulos.get(it["sku"]) or it["titulo"][:60]
            if c.get("status") == "opened":
                a["abiertos"] += 1
            a["tipos"][c.get("type") or "?"] += 1
            a["motivos"][motivos.get(c.get("reason_id"), c.get("reason_id"))] += 1
            f = c.get("_fecha")
            if f and (a["ultimo"] is None or f > a["ultimo"]):
                a["ultimo"] = f

    filas = []
    for sku, a in acc.items():
        u = vendidas.get(sku, 0)
        tasa = (a["reclamos"] / u) if u else None
        filas.append({
            "sku": sku,
            "titulo": a["titulo"],
            "reclamos": a["reclamos"],
            "abiertos": a["abiertos"],
            "unidades_vendidas": u,
            "tasa": tasa,
            "tipo_principal": a["tipos"].most_common(1)[0][0] if a["tipos"] else "",
            "motivo_principal": (a["motivos"].most_common(1)[0][0]
                                 if a["motivos"] else ""),
            "ultimo_reclamo": a["ultimo"].strftime("%Y-%m-%d") if a["ultimo"] else "",
            # Sin ventas suficientes la tasa no dice nada: se marca aparte en
            # vez de mostrar un 100% que asusta sin motivo.
            "confiable": u >= MINIMO_UNIDADES,
        })

    df = pd.DataFrame(filas)
    if len(df):
        def diagnostico(f):
            if not f["confiable"]:
                return "pocas ventas"
            if f["tasa"] is None:
                return "sin ventas"
            if f["tasa"] >= TASA_ALTA:
                return "tasa alta"
            if f["tasa"] >= TASA_MEDIA:
                return "para mirar"
            return "normal"

        df["diagnostico"] = df.apply(diagnostico, axis=1)
        df = df.sort_values(["reclamos", "tasa"], ascending=False)

    total_unidades = sum(vendidas.values())
    resumen = {
        "reclamos": len(claims),
        "abiertos": sum(1 for c in claims if c.get("status") == "opened"),
        "sin_producto": sin_producto,
        "unidades": total_unidades,
        "tasa_cuenta": (len(claims) / total_unidades) if total_unidades else 0.0,
        "dias": dias,
        "por_tipo": Counter(c.get("type") or "?" for c in claims),
        "por_motivo": Counter(motivos.get(c.get("reason_id"),
                                          c.get("reason_id")) for c in claims),
    }
    return df, resumen


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    ml = Meli(verbose=False)
    df, r = analizar(ml, dias, callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 70)

    print("=" * 66)
    print(f"RECLAMOS  ultimos {dias} dias")
    print("=" * 66)
    print(f"  Reclamos                {r['reclamos']:>8}")
    print(f"  Abiertos hoy            {r['abiertos']:>8}")
    print(f"  Sin producto identificado {r['sin_producto']:>6}")
    print(f"  Unidades vendidas       {r['unidades']:>8}")
    print(f"  Tasa de la cuenta       {r['tasa_cuenta']:>7.2%}")

    print("\n  Por tipo:")
    for k, n in r["por_tipo"].most_common():
        print(f"    {k:<22} {n:>5}")
    print("\n  Motivos mas frecuentes:")
    for k, n in r["por_motivo"].most_common(8):
        print(f"    {str(k)[:44]:<46} {n:>4}")

    if len(df):
        graves = df[df["diagnostico"] == "tasa alta"]
        if len(graves):
            print(f"\n  {len(graves)} productos con tasa alta "
                  f"(>{TASA_ALTA:.0%} y +{MINIMO_UNIDADES} ventas):")
            for _, f in graves.head(12).iterrows():
                print(f"    {f['sku']:<22} {f['reclamos']:>3} reclamos / "
                      f"{f['unidades_vendidas']:>4} u = {f['tasa']:.1%}")
                print(f"       {f['titulo']}")
                print(f"       motivo: {f['motivo_principal']}")

        df.to_csv(DIR / "reclamos.csv", index=False)
        print(f"\nGuardado en reclamos.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
