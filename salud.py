#!/usr/bin/env python3
"""
Salud del catalogo: que hay que arreglar para que las demas herramientas
funcionen bien.

    python salud.py

Cada problema de datos rompe algo concreto:

  - **Sin SKU**: la publicacion es invisible para las herramientas de precio,
    stock, rentabilidad y espejos. No se puede tocar ni analizar.
  - **SKU contradictorio** entre `SELLER_SKU` y `seller_custom_field`: se
    resuelve por SELLER_SKU, pero la discrepancia suele indicar carga
    descuidada y puede apuntar al producto equivocado.
  - **Sin EAN**: no se puede comparar contra la competencia.
  - **Pausada con stock**: no vende y tiene mercaderia inmovilizada.
  - **Activa sin stock**: ocupa lugar y no puede vender.

Prioriza por lo que cada publicacion vendio: arreglar la ficha de algo que
vende 300 unidades vale mas que la de algo que nunca vendio.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo

DIR = Path(__file__).resolve().parent


def gtin(p):
    for a in p.get("attributes") or []:
        if a.get("id") == "GTIN" and a.get("value_name"):
            return str(a["value_name"]).strip()
    return None


def analizar(pubs=None):
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    filas = []
    for p in pubs:
        estado = p.get("status")
        if estado not in ("active", "paused"):
            continue

        sku_attr = (sku_del_atributo(p) or "").strip()
        sku_libre = str(p.get("seller_custom_field") or "").strip()
        stock = p.get("available_quantity") or 0
        vendidas = p.get("sold_quantity") or 0
        problemas = []

        if not sku_attr and not sku_libre:
            problemas.append("sin SKU")
        elif sku_attr and sku_libre and sku_attr.upper() != sku_libre.upper():
            problemas.append("SKU contradictorio")
        if not gtin(p):
            problemas.append("sin código de barras")
        if estado == "paused" and stock > 0:
            problemas.append("pausada con stock")
        if estado == "active" and stock == 0:
            problemas.append("activa sin stock")

        if not problemas:
            continue
        filas.append({
            "item_id": p["id"],
            "sku": sku_attr or sku_libre or "",
            "titulo": (p.get("title") or "")[:55],
            "estado": estado,
            "stock": stock,
            "vendidas": vendidas,
            "precio": p.get("price"),
            "problemas": ", ".join(problemas),
            "cuantos": len(problemas),
            # Lo que vendio es la mejor señal de cuanto importa arreglarla.
            "prioridad": vendidas * len(problemas),
        })

    df = pd.DataFrame(filas)
    return df.sort_values("prioridad", ascending=False) if len(df) else df


def resumen(df):
    conteo = {}
    for fila in df["problemas"]:
        for p in fila.split(", "):
            conteo[p] = conteo.get(p, 0) + 1
    return conteo


def main():
    df = analizar()
    if not len(df):
        print("El catálogo no tiene problemas de datos. 👌")
        return 0

    print(f"Publicaciones con algo para arreglar: {len(df)}\n")
    print("Por tipo de problema:")
    for k, n in sorted(resumen(df).items(), key=lambda x: -x[1]):
        print(f"   {k:<24} {n:>5}")

    print("\nLas 12 más urgentes (por lo que vendieron):")
    for _, f in df.head(12).iterrows():
        print(f"  {f['item_id']} · {int(f['vendidas'])} vendidas · "
              f"{f['estado']}")
        print(f"     {f['titulo']}")
        print(f"     -> {f['problemas']}")

    df.to_csv(DIR / "salud.csv", index=False)
    print(f"\nGuardado en salud.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
