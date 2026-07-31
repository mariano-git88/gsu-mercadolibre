#!/usr/bin/env python3
"""
Optimizador de precios por tramo de comision.

    python tramos.py            -> analiza el catalogo activo
    python tramos.py --umbrales -> vuelve a medir los umbrales contra la API

MercadoLibre cobra un porcentaje **mas un cargo fijo por unidad**, y ese cargo
fijo salta en escalones de precio.

**En Uruguay el escalon de $1.000 es una trampa, no una oportunidad**, y esto
es al reves que en Argentina. Ahi arriba pasan DOS cosas a la vez:

  - el cargo fijo cae de $40 a cero            -> te ahorras $40
  - el envio deja de pagarlo el comprador y
    pasa a pagarlo el vendedor (~$160)         -> te cuesta $160

Medido sobre 125 ordenes de 90 dias, el corte es limpio: con precio unitario
por debajo de $1.000, 100 ordenes y **ninguna** con costo de envio para el
vendedor. Desde $1.000, **25 de 25** lo paga el vendedor.

O sea que cruzar $1.000 cuesta ~$154 por unidad. Para empatar habria que
llegar a $1.178 (+18% sobre $999), muy por encima de lo que este analisis se
permite subir. Por eso el modulo modela el envio como parte del neto: si no,
recomienda exactamente lo contrario de lo que conviene.

Lo que si encuentra:

  - productos apenas ARRIBA de $1.000 que dejarian mas neto bajando a $999
    (y ademas se venden mas baratos, o sea que probablemente vendan mas)
  - subas chicas que cruzan un escalon donde el cargo fijo baja **sin**
    activar el envio a cargo del vendedor

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

# Envio gratis obligatorio: desde este precio unitario lo paga el vendedor.
# Medido sobre 125 ordenes de 90 dias (jul 2026), corte limpio: 0 de 100
# ordenes por debajo, 25 de 25 por arriba. La mediana de lo que paga el
# vendedor es $160 (rango $125-$232, promedio $183).
UMBRAL_ENVIO_GRATIS = 1000
ENVIO_VENDEDOR = 160.0

# Cuanto se acepta subir un precio con tal de cruzar un escalon.
MARGEN_SUBIDA = 0.08       # 8%

# Cuanto se acepta BAJAR un precio para esquivar el envio obligatorio. Bajar
# el precio tambien deberia ayudar al volumen, asi que se permite mas margen.
MARGEN_BAJADA = 0.12       # 12%


def cargo_fijo(precio):
    for tope, fijo in TRAMOS:
        if precio < tope:
            return fijo
    return 0.0


def envio_a_cargo(precio):
    """Lo que paga el VENDEDOR de envio a ese precio. Es un escalon, no una
    constante por SKU: abajo del umbral lo paga el comprador."""
    return ENVIO_VENDEDOR if precio >= UMBRAL_ENVIO_GRATIS else 0.0


def neto(precio, pct=PORCENTAJE, con_envio=True):
    """
    Lo que queda por unidad despues de TODO lo que cobra ML: comision
    porcentual, cargo fijo y —esto es lo que se olvidaba— el envio, que arriba
    de `UMBRAL_ENVIO_GRATIS` lo paga el vendedor.

    `con_envio=False` deja el calculo viejo (solo comisiones). Sirve para
    comparar, pero no para decidir precios.
    """
    base = precio - (precio * pct + cargo_fijo(precio))
    return base - envio_a_cargo(precio) if con_envio else base


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


def _candidatos(precio):
    """
    Precios que vale la pena evaluar para una publicacion que hoy esta en
    `precio`. Devuelve (precio_candidato, motivo).

    Dentro de un tramo el neto crece con el precio, asi que los unicos optimos
    locales estan en los bordes: justo EN un escalon (si cruzar mejora) o justo
    DEBAJO de uno (si conviene no cruzarlo).
    """
    salidas = []
    coste = lambda x: cargo_fijo(x) + envio_a_cargo(x)

    # Hacia arriba: el proximo escalon, siempre que no se pase del margen Y
    # que cruzarlo abarate de verdad lo que cobra ML.
    #
    # El filtro por coste es imprescindible. Sin el, cualquier escalon cercano
    # "mejora el neto", pero solo porque subiste el precio: si el escalon te
    # deja pagando MAS (cargo fijo mas caro, o envio que antes pagaba el
    # comprador), quedarse un peso abajo deja todavia mas plata. Recomendar
    # cruzarlo seria recomendar la peor de dos subas.
    for tope, _ in TRAMOS:
        if tope == float("inf") or tope <= precio:
            continue
        if tope <= precio * (1 + MARGEN_SUBIDA) and coste(tope) < coste(precio):
            salidas.append((float(tope), "sube al escalón"))
        break

    # Hacia abajo: el ultimo peso antes del escalon que ya cruzo. Es la salida
    # para los que estan apenas arriba de $1.000 pagando envio.
    bordes = [t for t, _ in TRAMOS if t != float("inf") and t <= precio]
    if bordes:
        candidato = float(max(bordes)) - 1.0
        if candidato >= precio * (1 - MARGEN_BAJADA):
            salidas.append((candidato, "baja para esquivar el envío"))

    return salidas


def analizar(pubs=None):
    """
    Para cada publicacion activa busca el precio cercano —arriba o abajo— que
    deje MAS neto que el actual, contando comision, cargo fijo y envio.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    filas = []
    for p in pubs:
        if p.get("status") != "active" or not p.get("price"):
            continue
        precio = float(p["price"])
        neto_actual = neto(precio)

        sugerido, neto_sug, motivo = None, neto_actual, ""
        for candidato, razon in _candidatos(precio):
            n = neto(candidato)
            if n > neto_sug:
                sugerido, neto_sug, motivo = candidato, n, razon

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
            "motivo": motivo,
            "cambia_precio": round((sugerido - precio) / precio, 4),
            "gana_por_unidad": round(neto_sug - neto_actual, 2),
            "cargo_fijo_actual": cargo_fijo(precio),
            "cargo_fijo_nuevo": cargo_fijo(sugerido),
            "envio_actual": envio_a_cargo(precio),
            "envio_nuevo": envio_a_cargo(sugerido),
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
    for razon, g in df.groupby("motivo"):
        print(f"  {razon}: {len(g)}")
    print("\nLas 12 de mayor impacto:")
    for _, f in df.head(12).iterrows():
        print(f"  {f['sku'] or f['item_id']}  [{f['motivo']}]")
        print(f"     {pes(f['precio_actual'])} -> {pes(f['precio_sugerido'])} "
              f"({f['cambia_precio']:+.1%})  |  neto por unidad "
              f"{pes(f['neto_actual'])} -> {pes(f['neto_sugerido'])} "
              f"(+{pes(f['gana_por_unidad'])})")
        print(f"     cargo fijo {pes(f['cargo_fijo_actual'])} -> "
              f"{pes(f['cargo_fijo_nuevo'])} | envío "
              f"{pes(f['envio_actual'])} -> {pes(f['envio_nuevo'])} | "
              f"vendidas {int(f['vendidos'])}")

    df.to_csv(DIR / "tramos.csv", index=False)
    print(f"\nGuardado en tramos.csv ({len(df)} publicaciones)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
