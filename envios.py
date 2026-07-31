#!/usr/bin/env python3
"""
Productos donde el envio se come el margen.

    python envios.py            -> ultimos 90 dias, muestra de 5 ventas por SKU
    python envios.py 30

Vimos que la factura de ML trae entre $8M y $13M por mes que **no son
comisiones de venta**: buena parte es envio. Esto muestra en que productos
concretos se va esa plata.

El caso tipico es el producto voluminoso o pesado con precio bajo: paga el
mismo envio que uno caro, pero sobre un precio mucho menor. Ahi el envio pesa
mas que la comision y a veces mas que el margen entero.

Sobre los datos: el costo de envio sale de `/shipments/{id}/costs`, mirando el
`senders[].cost` — lo que paga SUPRABOND, no el comprador. Es una llamada por
envio, asi que se muestrean unas pocas ventas por SKU y se cachea. La columna
`cobertura` dice que proporcion de las unidades tiene dato real: con cobertura
baja, el promedio es orientativo.
"""

import sys

import pandas as pd

import rentabilidad as rent
from meli import Meli, MeliError

# A partir de aca el envio deja de ser un costo mas y pasa a ser el problema.
ENVIO_PESADO = 0.20        # 20% del precio
ENVIO_CRITICO = 0.35


def analizar(ml, dias=90, muestra=5, callback=None):
    ordenes = rent.traer_historico(ml, dias)
    if callback:
        callback("Trayendo costos de envío (una llamada por envío)...")
    envios = rent.traer_costos_envio(
        ml, ordenes, muestra_por_sku=muestra,
        callback=lambda i, t: callback(f"Envíos {i}/{t}...") if callback else None)

    cargos = rent.cargos_por_sku(ordenes, envios)
    if not len(cargos):
        return cargos

    df = cargos[cargos["cobertura_envio"] > 0].copy()
    df["envio_sobre_precio"] = df["envio_prom"] / df["precio_prom"].replace(0, 1)
    df["comision_sobre_precio"] = df["comision_prom"] / df["precio_prom"].replace(0, 1)
    df["cargos_totales"] = df["envio_prom"] + df["comision_prom"]
    df["queda_antes_del_costo"] = df["precio_prom"] - df["cargos_totales"]
    df["margen_bruto"] = df["queda_antes_del_costo"] / df["precio_prom"].replace(0, 1)

    def etiqueta(f):
        e = f["envio_sobre_precio"]
        if f["queda_antes_del_costo"] <= 0:
            return "pierde_plata"
        if e >= ENVIO_CRITICO:
            return "envio_critico"
        if e >= ENVIO_PESADO:
            return "envio_pesado"
        return "normal"

    df["diagnostico"] = df.apply(etiqueta, axis=1)
    # Lo que mas duele: mucho envio sobre muchas unidades.
    df["plata_en_envio"] = df["envio_prom"] * df["unidades_vendidas"]
    return df.sort_values("plata_en_envio", ascending=False)


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    ml = Meli(verbose=False)
    df = analizar(ml, dias, callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 60)

    if not len(df):
        print("Sin datos de envío suficientes.")
        return 0

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    print(f"SKU con datos de envío: {len(df)}")
    print(f"Plata en envíos (estimada sobre los medidos): "
          f"{pes(df['plata_en_envio'].sum())}\n")

    print("Diagnóstico:")
    for k, n in df["diagnostico"].value_counts().items():
        print(f"   {k:<16} {n:>4}")

    graves = df[df["diagnostico"].isin(["pierde_plata", "envio_critico"])]
    if len(graves):
        print(f"\nLos {min(10, len(graves))} peores:")
        for _, f in graves.head(10).iterrows():
            print(f"  {f['sku']}")
            print(f"     precio {pes(f['precio_prom'])} | envío "
                  f"{pes(f['envio_prom'])} ({f['envio_sobre_precio']:.0%}) | "
                  f"comisión {pes(f['comision_prom'])}")
            print(f"     queda antes del costo: "
                  f"{pes(f['queda_antes_del_costo'])} "
                  f"({f['margen_bruto']:.0%}) | {int(f['unidades_vendidas'])} "
                  f"unidades | cobertura {f['cobertura_envio']:.0%}")

    df.to_csv("envios.csv", index=False)
    print("\nGuardado en envios.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
