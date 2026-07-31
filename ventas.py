#!/usr/bin/env python3
"""
Ventas de un periodo: trae las ordenes y las resume.

    python ventas.py            -> ultimos 30 dias
    python ventas.py 90         -> ultimos 90 dias

Sirve tambien como libreria:

    from ventas import traer_ordenes
    ordenes = traer_ordenes(ml, desde, hasta)

Detalle importante: el listado de ordenes YA trae `sale_fee` por item, asi que
no hace falta pedir el detalle de cada orden (que serian cientos de llamadas).
"""

import sys
from collections import defaultdict
from datetime import datetime, timedelta

from meli import Meli, MeliError

# ML corta la paginacion por offset en 1000. Cuando un rango tiene mas ordenes
# que eso, lo partimos al medio y pedimos cada mitad por separado.
TOPE_OFFSET = 1000


def iso(dt):
    """Formato de fecha que quiere /orders/search (hora de Uruguay, UTC-3)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")


def traer_ordenes(ml, desde, hasta, _nivel=0):
    """
    Devuelve todas las ordenes creadas entre `desde` y `hasta`, partiendo el
    rango automaticamente si hay mas de 1000.
    """
    sonda = ml.get("/orders/search", seller=ml.user_id, limit=1,
                   **{"order.date_created.from": iso(desde),
                      "order.date_created.to": iso(hasta)})
    total = sonda.get("paging", {}).get("total", 0)

    if total == 0:
        return []

    if total > TOPE_OFFSET:
        # Rango demasiado grande: lo partimos al medio.
        medio = desde + (hasta - desde) / 2
        sangria = "  " * _nivel
        print(f"{sangria}[ventas] {desde:%d/%m} a {hasta:%d/%m}: {total} ordenes, "
              f"partiendo el rango...")
        return (traer_ordenes(ml, desde, medio, _nivel + 1) +
                traer_ordenes(ml, medio, hasta, _nivel + 1))

    ordenes, offset = [], 0
    while offset < total:
        pagina = ml.get("/orders/search", seller=ml.user_id,
                        limit=50, offset=offset, sort="date_asc",
                        **{"order.date_created.from": iso(desde),
                           "order.date_created.to": iso(hasta)})
        lote = pagina.get("results", [])
        if not lote:
            break
        ordenes.extend(lote)
        offset += 50

    return ordenes


def resumir(ordenes):
    """Arma los totales del periodo separando por estado."""
    r = {
        "cantidad": len(ordenes),
        "por_estado": defaultdict(lambda: {"ordenes": 0, "monto": 0.0}),
        "bruto": 0.0,          # suma de total_amount (productos)
        "cobrado": 0.0,        # suma de paid_amount (incluye envio que paga el comprador)
        "comisiones": 0.0,     # sale_fee: lo que se queda ML
        "unidades": 0,
        "sin_comision": 0,
    }

    for o in ordenes:
        estado = o.get("status", "?")
        total = o.get("total_amount") or 0.0
        r["por_estado"][estado]["ordenes"] += 1
        r["por_estado"][estado]["monto"] += total

        # Las canceladas no son venta: no suman a los totales.
        if estado == "cancelled":
            continue

        r["bruto"] += total
        r["cobrado"] += o.get("paid_amount") or 0.0

        for it in o.get("order_items", []):
            r["unidades"] += it.get("quantity") or 0
            fee = it.get("sale_fee")
            if fee is None:
                r["sin_comision"] += 1
            else:
                r["comisiones"] += fee * (it.get("quantity") or 1)

    return r


def imprimir(r, desde, hasta):
    pes = lambda n: f"${n:,.0f}".replace(",", ".")

    print("\n" + "=" * 60)
    print(f"VENTAS  {desde:%d/%m/%Y} a {hasta:%d/%m/%Y}")
    print("=" * 60)
    print(f"  Ordenes            {r['cantidad']:>16,}".replace(",", "."))
    print(f"  Unidades           {r['unidades']:>16,}".replace(",", "."))
    print()
    # El panel de ML muestra el total SIN descontar canceladas: lo mostramos
    # aparte para poder conciliar contra la pantalla.
    con_canceladas = sum(d["monto"] for d in r["por_estado"].values())
    print(f"  Bruto s/canceladas {pes(r['bruto']):>16}")
    print(f"  Bruto c/canceladas {pes(con_canceladas):>16}   <- compara con el panel de ML")
    print(f"  Cobrado (c/envio)  {pes(r['cobrado']):>16}")
    print(f"  Comisiones ML      {pes(r['comisiones']):>16}")
    if r["bruto"]:
        print(f"  Comision promedio  {r['comisiones'] / r['bruto'] * 100:>15.1f}%")
        print(f"  Ticket promedio    {pes(r['bruto'] / max(r['cantidad'], 1)):>16}")
        print(f"  Neto post-comision {pes(r['bruto'] - r['comisiones']):>16}")

    print("\n  Por estado:")
    for estado, d in sorted(r["por_estado"].items(),
                            key=lambda x: -x[1]["monto"]):
        print(f"    {estado:<20} {d['ordenes']:>6} ordenes   {pes(d['monto']):>16}")

    if r["sin_comision"]:
        print(f"\n  AVISO: {r['sin_comision']} items sin sale_fee informado "
              f"(la comision real es un poco mayor a la calculada).")
    print("=" * 60)


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    hasta = datetime.now()
    desde = hasta - timedelta(days=dias)

    ml = Meli(verbose=False)
    print(f"Trayendo ordenes de los ultimos {dias} dias...")
    ordenes = traer_ordenes(ml, desde, hasta)
    imprimir(resumir(ordenes), desde, hasta)
    return ordenes


if __name__ == "__main__":
    try:
        main()
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
