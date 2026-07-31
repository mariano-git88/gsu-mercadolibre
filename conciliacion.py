#!/usr/bin/env python3
"""
Conciliar lo que MercadoLibre factura contra lo que deberia cobrar.

    python conciliacion.py          -> ultimos periodos cerrados
    python conciliacion.py 6        -> ultimos 6 periodos

ML factura entre $22M y $35M por mes a SUPRABOND. Por otro lado, cada orden
trae el `sale_fee` que ML se cobro por esa venta. **Nadie compara las dos
cosas.** Con esos montos, un 2% de diferencia son medio millon por mes.

Que hace: por cada periodo cerrado de facturacion, suma las comisiones de las
ordenes de esas mismas fechas y las compara contra el monto facturado.

Que NO es: una auditoria contable. La factura de ML incluye conceptos que no
salen de las ordenes (publicidad, cargos por publicacion, ajustes), asi que
**es normal que la factura sea mayor** que la suma de comisiones. Lo que hay
que mirar es si esa brecha se mantiene estable entre periodos: un salto
repentino es lo que amerita revisar.
"""

import sys
from datetime import datetime

import pandas as pd

from meli import Meli, MeliError
from ventas import traer_ordenes


def periodos_facturados(ml, cuantos=6):
    """Periodos de facturacion de ML, del mas reciente al mas viejo."""
    r = ml.get("/billing/integration/monthly/periods",
               group="ML", document_type="BILL")
    salida = []
    for p in (r.get("results") or [])[:cuantos + 1]:
        salida.append({
            "clave": p.get("key"),
            "desde": p["period"]["date_from"],
            "hasta": p["period"]["date_to"],
            "facturado": float(p.get("amount") or 0),
            "impago": float(p.get("unpaid_amount") or 0),
        })
    return salida


def comisiones_del_periodo(ml, desde, hasta):
    """Suma de `sale_fee` de las ordenes creadas en el periodo."""
    d = datetime.strptime(desde, "%Y-%m-%d")
    h = datetime.strptime(hasta, "%Y-%m-%d")
    ordenes = traer_ordenes(ml, d, h)

    comision, unidades, ordenes_ok = 0.0, 0, 0
    for o in ordenes:
        if o.get("status") == "cancelled":
            continue
        ordenes_ok += 1
        for it in o.get("order_items") or []:
            q = it.get("quantity") or 0
            unidades += q
            if it.get("sale_fee") is not None:
                comision += float(it["sale_fee"]) * q
    return {"comisiones": comision, "unidades": unidades,
            "ordenes": ordenes_ok}


def conciliar(ml, cuantos=4, callback=None):
    """
    Compara periodo por periodo. Salta el periodo en curso: su monto todavia
    no esta consolidado y daria una brecha enorme que no significa nada.
    """
    hoy = datetime.now().strftime("%Y-%m-%d")
    filas = []
    for p in periodos_facturados(ml, cuantos):
        if p["hasta"] >= hoy:
            continue                     # periodo abierto: no comparable
        if callback:
            callback(f"Período {p['desde']} a {p['hasta']}...")
        c = comisiones_del_periodo(ml, p["desde"], p["hasta"])
        brecha = p["facturado"] - c["comisiones"]
        filas.append({
            "periodo": f"{p['desde']} a {p['hasta']}",
            "facturado_ml": round(p["facturado"], 2),
            "comisiones_calculadas": round(c["comisiones"], 2),
            "otros_conceptos": round(brecha, 2),
            "proporcion_otros": (brecha / p["facturado"]) if p["facturado"] else 0,
            "ordenes": c["ordenes"],
            "unidades": c["unidades"],
            "impago": round(p["impago"], 2),
        })
        if len(filas) >= cuantos:
            break

    df = pd.DataFrame(filas)
    if len(df) > 1:
        # Lo que importa no es la brecha en si, sino que se mantenga estable.
        media = df["proporcion_otros"].mean()
        df["desvio_vs_promedio"] = (df["proporcion_otros"] - media).round(4)
        df["alerta"] = df["desvio_vs_promedio"].abs() > 0.10
    return df


def main():
    cuantos = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    ml = Meli(verbose=False)
    print(f"Conciliando los últimos {cuantos} períodos cerrados...\n")
    df = conciliar(ml, cuantos, callback=lambda m: print(f"  {m}"))

    if not len(df):
        print("No hay períodos cerrados para comparar.")
        return 0

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    print()
    for _, f in df.iterrows():
        marca = "  ⚠" if f.get("alerta") else "   "
        print(f"{marca} {f['periodo']}")
        print(f"      ML facturó          {pes(f['facturado_ml']):>14}")
        print(f"      comisiones de ventas{pes(f['comisiones_calculadas']):>14}")
        print(f"      otros conceptos     {pes(f['otros_conceptos']):>14}"
              f"  ({f['proporcion_otros']:.0%} del total)")
        print(f"      {int(f['ordenes'])} órdenes, {int(f['unidades'])} unidades")

    if "alerta" in df and df["alerta"].any():
        print("\n  ⚠ Los períodos marcados se desvían más de 10 puntos del "
              "promedio. Vale revisar qué cambió: publicidad nueva, cargos "
              "por publicación, o ajustes.")
    else:
        print("\n  La proporción de otros conceptos se mantiene estable entre "
              "períodos: no hay señales de un cobro fuera de lo normal.")

    df.to_csv("conciliacion.csv", index=False)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
