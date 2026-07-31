#!/usr/bin/env python3
"""
Optimizador de precios por tramo de comision.

    python tramos.py            -> analiza el catalogo activo
    python tramos.py --umbrales -> vuelve a medir los umbrales contra la API

MercadoLibre cobra un porcentaje **mas un cargo fijo por unidad**, y ese cargo
fijo salta en escalones. La consecuencia practica es contraintuitiva: hay
precios donde **subir unos pesos deja mas plata neta**, porque cruzar el
escalon baja (o elimina) el cargo fijo.

El caso mas fuerte esta en $1.000: por debajo se pagan $40 de cargo fijo, por
encima **cero**. Un producto a $950 deja menos neto que el mismo producto a
$1.000, aunque se venda mas barato.

Tambien marca lo inverso: productos que estan **apenas por encima** de un
escalon y podrian bajar de tramo, o que estan a punto de cruzarlo hacia arriba
si se les aplica un aumento sin mirar.

Es solo lectura: sugiere, no toca precios.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from meli import Meli, MeliError, SITE_ID

DIR = Path(__file__).resolve().parent

# Escalones del cargo fijo en URUGUAY (pesos uruguayos), medidos contra
# /sites/MLU/listing_prices con busqueda binaria (jul 2026). `hasta` es
# exclusivo.
#   precio < 500 -> $15      500..749  -> $25
#   750..999     -> $40      >= 1000   -> $0
TRAMOS = [(500, 15.0), (750, 25.0), (1000, 40.0),
          (float("inf"), 0.0)]
PORCENTAJE = 0.13          # comision base gold_special (igual que en MLA)

# Cuanto se acepta subir un precio con tal de cruzar un escalon.
MARGEN_SUBIDA = 0.08       # 8%


def cargo_fijo(precio):
    for tope, fijo in TRAMOS:
        if precio < tope:
            return fijo
    return 0.0


def neto(precio, pct=PORCENTAJE):
    """Lo que queda despues de la comision de ML, por unidad."""
    return precio - (precio * pct + cargo_fijo(precio))


def medir_umbrales(ml, desde=50, hasta=2000, paso=50):
    """Vuelve a medir los escalones contra la API, por si ML los cambia."""
    def fijo_api(p):
        r = ml.get(f"/sites/{SITE_ID}/listing_prices", price=p)
        g = [x for x in r if x["listing_type_id"] == "gold_special"][0]
        return g["sale_fee_details"].get("fixed_fee")

    saltos, ant = [], None
    for p in range(desde, hasta + 1, paso):
        f = fijo_api(p)
        if ant is not None and f != ant[1]:
            lo, hi = ant[0], p
            while hi - lo > 1:            # afinamos el borde exacto
                m = (lo + hi) // 2
                if fijo_api(m) == ant[1]:
                    lo = m
                else:
                    hi = m
            saltos.append({"umbral": hi, "fijo_antes": ant[1], "fijo_despues": f})
        ant = (p, f)
    return saltos


def analizar(pubs=None):
    """
    Para cada publicacion activa, busca si existe un precio cercano hacia
    arriba que deje MAS neto que el actual.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    filas = []
    for p in pubs:
        if p.get("status") != "active" or not p.get("price"):
            continue
        precio = float(p["price"])
        neto_actual = neto(precio)

        # El unico precio que vale la pena probar es el del proximo escalon:
        # dentro de un tramo el neto crece con el precio, asi que el optimo
        # local siempre esta justo en el borde.
        sugerido, neto_sug = None, neto_actual
        for tope, _ in TRAMOS:
            if tope == float("inf") or tope <= precio:
                continue
            if tope > precio * (1 + MARGEN_SUBIDA):
                break
            # Cruzar SOLO sirve si el cargo fijo baja. Si sube, el neto puede
            # mejorar igual (subiste el precio), pero entonces quedarse un
            # centavo abajo del escalon deja todavia mas: cobras casi lo mismo
            # y pagas menos cargo. Sin esta condicion el analisis recomienda
            # cruzar hacia arriba escalones que son peores.
            if cargo_fijo(tope) >= cargo_fijo(precio):
                break
            n = neto(tope)
            if n > neto_sug:
                sugerido, neto_sug = float(tope), n
            break

        if sugerido is None:
            continue

        filas.append({
            "item_id": p["id"],
            "sku": sku_del_atributo(p) or "",
            "titulo": (p.get("title") or "")[:60],
            "vendidos": p.get("sold_quantity") or 0,
            "precio_actual": precio,
            "neto_actual": round(neto_actual, 2),
            "precio_sugerido": sugerido,
            "neto_sugerido": round(neto_sug, 2),
            "sube_precio": round((sugerido - precio) / precio, 4),
            "gana_por_unidad": round(neto_sug - neto_actual, 2),
            "cargo_fijo_actual": cargo_fijo(precio),
            "cargo_fijo_nuevo": cargo_fijo(sugerido),
        })

    df = pd.DataFrame(filas)
    if len(df):
        # Lo que mas rinde: mucha ganancia por unidad y producto que rota.
        df["impacto"] = df["gana_por_unidad"] * df["vendidos"].clip(lower=1)
        df = df.sort_values("impacto", ascending=False)
    return df


def main():
    if "--umbrales" in sys.argv:
        ml = Meli(verbose=False)
        print("Midiendo los escalones contra la API...")
        for s in medir_umbrales(ml):
            print(f"   ${s['umbral']:>7,}  fijo ${s['fijo_antes']} -> "
                  f"${s['fijo_despues']}")
        return 0

    df = analizar()
    if not len(df):
        print("Ninguna publicación está cerca de un escalón de comisión.")
        return 0

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    print(f"Publicaciones que convendría reprecificar: {len(df)}\n")
    print("Las 12 de mayor impacto:")
    for _, f in df.head(12).iterrows():
        print(f"  {f['sku'] or f['item_id']}")
        print(f"     {pes(f['precio_actual'])} -> {pes(f['precio_sugerido'])} "
              f"({f['sube_precio']:+.1%})  |  neto por unidad "
              f"{pes(f['neto_actual'])} -> {pes(f['neto_sugerido'])} "
              f"(+{pes(f['gana_por_unidad'])})")
        print(f"     cargo fijo {pes(f['cargo_fijo_actual'])} -> "
              f"{pes(f['cargo_fijo_nuevo'])} | vendidas {int(f['vendidos'])}")

    df.to_csv(DIR / "tramos.csv", index=False)
    print(f"\nGuardado en tramos.csv ({len(df)} publicaciones)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
