#!/usr/bin/env python3
"""
Alertas de stock critico: que productos estan por quedarse sin stock.

    python alertas_stock.py            -> velocidad de los ultimos 60 dias
    python alertas_stock.py 30

La pregunta que responde no es "cuanto stock tengo" sino **"cuantos dias me
queda"**, que es lo unico accionable: 40 unidades de algo que vende 1 por
semana estan bien; 40 unidades de algo que vende 10 por dia se agotan el
jueves.

Prioriza por **plata en riesgo**: lo que ese producto deja de facturar por
cada semana sin stock. Quedarse sin el producto que factura $2M por semana
importa mucho mas que quedarse sin el que factura $8.000, aunque los dos
tengan la misma cobertura en dias.

Dos precauciones sobre el stock:

  - Se agrupa por `user_product_id`. Las publicaciones espejo comparten el
    stock, asi que sumar `available_quantity` de todas contaria varias veces
    las mismas unidades — es el mismo motivo por el que `resolver_stock()`
    devuelve un solo destino.
  - El stock en **Full** se cuenta aparte. Reponerlo no es lo mismo: hay que
    despachar mercaderia al deposito de ML y eso tarda, asi que un producto
    en Full necesita mas dias de aviso que uno del deposito propio.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from catalogo import es_full, sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

# Dias de cobertura a partir de los cuales hay que hacer algo.
DIAS_CRITICO = 7
DIAS_BAJO = 15
# En Full el reaprovisionamiento tarda mas: se avisa antes.
DIAS_CRITICO_FULL = 15
DIAS_BAJO_FULL = 30
# Arriba de esto hay plata dormida, no un riesgo de quiebre.
DIAS_SOBRESTOCK = 180
# Con menos ventas que esto la velocidad es ruido: 1 venta en 60 dias no
# permite proyectar nada.
MINIMO_UNIDADES = 3

# Lo que hay que resolver esta semana.
URGENTES = ("sin publicación activa", "sin stock", "crítico")


def referencias(pubs):
    """
    SKU -> titulo y precio tomando CUALQUIER publicacion, activa o no.

    Hace falta para los SKU que se quedaron sin ninguna publicacion activa:
    si no, el producto agotado aparece sin nombre ni precio, justo cuando es
    el que mas hay que mirar.
    """
    ref = {}
    for p in pubs:
        sku = (sku_del_atributo(p) or "").strip().upper()
        if not sku:
            continue
        r = ref.setdefault(sku, {"titulo": "", "precio": None})
        r["titulo"] = r["titulo"] or (p.get("title") or "")[:60]
        precio = p.get("price")
        if precio and (r["precio"] is None or precio > r["precio"]):
            r["precio"] = precio
    return ref


def stock_por_sku(pubs):
    """
    SKU -> stock real disponible, sin contar dos veces las publicaciones que
    comparten stock.

    Solo cuenta publicaciones ACTIVAS: el stock de una pausada no se puede
    vender. Los SKU que quedaron sin ninguna activa no aparecen aca — los
    recupera `analizar()` cruzando contra las ventas.
    """
    grupos = defaultdict(lambda: {"propio": {}, "full": {}, "pubs": 0,
                                  "precio": None, "titulo": ""})
    for p in pubs:
        if p.get("status") != "active":
            continue
        sku = (sku_del_atributo(p) or "").strip().upper()
        if not sku:
            continue

        g = grupos[sku]
        g["pubs"] += 1
        g["titulo"] = g["titulo"] or (p.get("title") or "")[:60]
        precio = p.get("price")
        # Como referencia de facturacion usamos el precio mas alto publicado:
        # es el de la publicacion principal, no el de una oferta puntual.
        if precio and (g["precio"] is None or precio > g["precio"]):
            g["precio"] = precio

        destino = g["full"] if es_full(p) else g["propio"]
        clave = p.get("user_product_id") or p["id"]
        # Dentro del grupo el stock es el mismo numero repetido; nos quedamos
        # con el mayor por si alguna publicacion quedo desactualizada.
        destino[clave] = max(destino.get(clave, 0),
                             p.get("available_quantity") or 0)

    salida = {}
    for sku, g in grupos.items():
        salida[sku] = {
            "stock_propio": sum(g["propio"].values()),
            "stock_full": sum(g["full"].values()),
            "publicaciones": g["pubs"],
            "precio": g["precio"],
            "titulo": g["titulo"],
            "en_full": bool(g["full"]),
        }
    return salida


def velocidad_por_sku(ordenes, dias):
    """SKU -> unidades por dia, sobre las ordenes efectivamente pagadas."""
    unidades = defaultdict(int)
    for o in ordenes:
        if o.get("status") not in ("paid", "partially_refunded"):
            continue
        for it in o.get("order_items") or []:
            sku = (it["item"].get("seller_sku") or "").strip().upper()
            if sku:
                unidades[sku] += it.get("quantity") or 0
    return {s: {"unidades": u, "por_dia": u / dias} for s, u in unidades.items()}


def analizar(ml, dias=60, pubs=None, ordenes=None, callback=None):
    """
    Devuelve el DataFrame de cobertura por SKU, ordenado por plata en riesgo.

    `ordenes` permite pasar un historico ya bajado. Importa: el cache de
    `traer_historico()` esta indexado por cantidad de dias, asi que pedirle
    una ventana distinta a la que tiene guardada lo obliga a bajar todo de
    nuevo. El reporte semanal aprovecha esto para reusar sus propias ordenes.
    """
    import rentabilidad as rent

    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    if ordenes is None:
        if callback:
            callback("Trayendo ventas del período...")
        ordenes = rent.traer_historico(ml, dias)

    stocks = stock_por_sku(pubs)
    vel = velocidad_por_sku(ordenes, dias)
    ref = referencias(pubs)

    # MercadoLibre pausa sola la publicacion cuando llega a stock cero, asi
    # que el producto agotado desaparece de las activas. Si vendio en el
    # periodo y hoy no tiene ninguna activa, es exactamente el caso mas
    # urgente: hay que recuperarlo a mano.
    sin_activas = {
        sku: {"stock_propio": 0, "stock_full": 0, "publicaciones": 0,
              "precio": ref.get(sku, {}).get("precio"),
              "titulo": ref.get(sku, {}).get("titulo", ""),
              "en_full": False, "sin_publicacion": True}
        for sku in vel if sku not in stocks}

    filas = []
    for sku, s in {**stocks, **sin_activas}.items():
        v = vel.get(sku, {"unidades": 0, "por_dia": 0.0})
        total = s["stock_propio"] + s["stock_full"]
        por_dia = v["por_dia"]

        cobertura = (total / por_dia) if por_dia > 0 else None
        # La plata en riesgo es lo que deja de facturar por semana sin stock.
        riesgo = por_dia * 7 * (s["precio"] or 0)

        umbral_crit = DIAS_CRITICO_FULL if s["en_full"] else DIAS_CRITICO
        umbral_bajo = DIAS_BAJO_FULL if s["en_full"] else DIAS_BAJO

        if v["unidades"] < MINIMO_UNIDADES:
            diag = "sin ventas" if v["unidades"] == 0 else "pocas ventas"
        elif s.get("sin_publicacion"):
            diag = "sin publicación activa"
        elif total == 0:
            diag = "sin stock"
        elif cobertura is not None and cobertura <= umbral_crit:
            diag = "crítico"
        elif cobertura is not None and cobertura <= umbral_bajo:
            diag = "bajo"
        elif cobertura is not None and cobertura >= DIAS_SOBRESTOCK:
            diag = "sobrestock"
        else:
            diag = "normal"

        filas.append({
            "sku": sku,
            "titulo": s["titulo"],
            "stock": total,
            "stock_propio": s["stock_propio"],
            "stock_full": s["stock_full"],
            "unidades_periodo": v["unidades"],
            "por_dia": por_dia,
            "dias_cobertura": cobertura,
            "precio": s["precio"],
            "plata_semanal_en_riesgo": riesgo,
            "publicaciones": s["publicaciones"],
            "en_full": s["en_full"],
            "diagnostico": diag,
        })

    df = pd.DataFrame(filas)
    if not len(df):
        return df

    orden = {"sin publicación activa": 0, "sin stock": 1, "crítico": 2,
             "bajo": 3, "sobrestock": 4, "normal": 5, "pocas ventas": 6,
             "sin ventas": 7}
    df["_orden"] = df["diagnostico"].map(orden)
    df = df.sort_values(["_orden", "plata_semanal_en_riesgo"],
                        ascending=[True, False]).drop(columns=["_orden"])
    return df


def resumen(df):
    if not len(df):
        return {}
    urgentes = df[df["diagnostico"].isin(URGENTES)]
    return {
        "sin_publicacion": int(
            (df["diagnostico"] == "sin publicación activa").sum()),
        "sin_stock": int((df["diagnostico"] == "sin stock").sum()),
        "criticos": int((df["diagnostico"] == "crítico").sum()),
        "bajos": int((df["diagnostico"] == "bajo").sum()),
        "sobrestock": int((df["diagnostico"] == "sobrestock").sum()),
        "plata_en_riesgo": float(urgentes["plata_semanal_en_riesgo"].sum()),
    }


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    ml = Meli(verbose=False)
    df = analizar(ml, dias, callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 60)

    if not len(df):
        print("Sin datos.")
        return 0

    r = resumen(df)
    pes = lambda v: f"${v:,.0f}".replace(",", ".")

    print("=" * 66)
    print(f"STOCK CRITICO  (velocidad de los ultimos {dias} dias)")
    print("=" * 66)
    print(f"  Vendio y no tiene publicacion activa {r['sin_publicacion']:>4}")
    print(f"  Sin stock y vendiendo   {r['sin_stock']:>6}")
    print(f"  Criticos                {r['criticos']:>6}")
    print(f"  Bajos                   {r['bajos']:>6}")
    print(f"  Sobrestock              {r['sobrestock']:>6}")
    print(f"  Facturacion semanal en riesgo  {pes(r['plata_en_riesgo']):>14}")

    urgentes = df[df["diagnostico"].isin(URGENTES)]
    if len(urgentes):
        print(f"\n  Los {min(15, len(urgentes))} mas urgentes:")
        for _, f in urgentes.head(15).iterrows():
            cob = (f"{f['dias_cobertura']:.0f} días" if f["stock"]
                   else f["diagnostico"])
            print(f"    {f['sku']:<24} {f['stock']:>5} u · {cob:<10} "
                  f"· {pes(f['plata_semanal_en_riesgo'])}/sem"
                  f"{'  [FULL]' if f['en_full'] else ''}")
            print(f"       {f['titulo']}")

    df.to_csv(DIR / "alertas_stock.csv", index=False)
    print(f"\nGuardado en alertas_stock.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
