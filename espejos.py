#!/usr/bin/env python3
"""
Publicaciones espejo con precios distintos.

    python espejos.py

Casi la mitad del catalogo son duplicados: 997 SKU repartidos en 2.275
publicaciones activas. Cuando dos publicaciones del MISMO producto tienen
precios distintos, la empresa **compite contra si misma**: el comprador que
compara encuentra el producto mas barato en otra publicacion tuya, y la mas
cara no vende nunca.

Detecta esos casos y propone unificar. La regla de que precio dejar es la
misma que ya se acordo para actualizar precios:

  - Si en el grupo conviven Premium (`gold_pro`) y Clasica (`gold_special`),
    se comparan **solo entre las del mismo tipo**: es esperable que la Premium
    valga mas, porque paga ~12 puntos mas de comision.
  - Dentro de cada tipo, se sugiere el precio de la publicacion **que mas
    vendio** — es la que el mercado ya validó.

Es solo lectura: sugiere, no aplica. El CSV que exporta se sube tal cual en
la seccion Precios.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from resolver import CON_FINANCIACION, SIN_FINANCIACION

DIR = Path(__file__).resolve().parent

# Diferencias menores a esto son redondeo, no una desincronizacion real.
MINIMA_DIFERENCIA = 0.005


def analizar(pubs=None):
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    # Se agrupa por SKU **y tipo**: comparar una Premium contra una Clásica
    # marcaria como problema una diferencia que es correcta.
    grupos = defaultdict(list)
    for p in pubs:
        if p.get("status") != "active" or not p.get("price"):
            continue
        sku = (sku_del_atributo(p) or "").strip().upper()
        if sku:
            grupos[(sku, p.get("listing_type_id"))].append(p)

    filas = []
    for (sku, tipo), ps in grupos.items():
        if len(ps) < 2:
            continue
        precios = [float(x["price"]) for x in ps]
        menor, mayor = min(precios), max(precios)
        if menor <= 0 or (mayor - menor) / menor < MINIMA_DIFERENCIA:
            continue

        # La que mas vendio es la referencia: su precio ya lo validó el mercado.
        referencia = max(ps, key=lambda x: x.get("sold_quantity") or 0)
        sugerido = float(referencia["price"])

        for p in ps:
            actual = float(p["price"])
            if abs(actual - sugerido) < 0.01:
                continue
            filas.append({
                "sku": sku,
                "tipo": "Premium" if tipo == CON_FINANCIACION else "Clásica",
                "item_id": p["id"],
                "titulo": (p.get("title") or "")[:55],
                "precio_actual": actual,
                "precio_sugerido": sugerido,
                "diferencia": round((actual - sugerido) / sugerido, 4),
                "vendidas": p.get("sold_quantity") or 0,
                "vendidas_referencia": referencia.get("sold_quantity") or 0,
                "publicaciones_del_grupo": len(ps),
                "spread_del_grupo": round((mayor - menor) / menor, 4),
                # Si esta mas cara que la referencia, casi seguro no vende.
                "riesgo": ("no vende: hay una igual más barata"
                           if actual > sugerido
                           else "está regalando margen"),
            })

    df = pd.DataFrame(filas)
    if len(df):
        df = df.sort_values(["spread_del_grupo", "vendidas_referencia"],
                            ascending=False)
    return df


def main():
    df = analizar()
    if not len(df):
        print("No hay publicaciones espejo con precios distintos.")
        return 0

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    caras = df[df["diferencia"] > 0]
    print(f"Publicaciones a emparejar: {len(df)}")
    print(f"SKU afectados: {df['sku'].nunique()}")
    print(f"  más caras que su gemela (probablemente no venden): {len(caras)}")
    print(f"  más baratas (regalan margen): {len(df) - len(caras)}\n")

    print("Los 10 grupos con mayor diferencia:")
    for _, f in df.head(10).iterrows():
        print(f"  {f['sku']} · {f['tipo']} · grupo de "
              f"{int(f['publicaciones_del_grupo'])}")
        print(f"     {f['item_id']}: {pes(f['precio_actual'])} -> "
              f"{pes(f['precio_sugerido'])} ({f['diferencia']:+.1%})  "
              f"[{f['riesgo']}]")

    df.to_csv(DIR / "espejos.csv", index=False)
    print(f"\nGuardado en espejos.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
