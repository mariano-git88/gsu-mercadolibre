#!/usr/bin/env python3
"""
Candidatos a Full: por que productos empezar si se agranda el uso de Full.

    python full.py            -> ultimos 90 dias
    python full.py 60

**En la cuenta de Uruguay no hay NINGUNA publicacion en Full.** Las 438
activas estan todas en deposito propio (`xd_drop_off`). O sea que este modulo
no tiene con que comparar y no puede estimar ahorro alguno: queda como una
foto de donde se va la plata de envio, util si algun dia se evalua entrar a
Full, no como recomendador.

**Que si hace.** Ordena los productos por el tamaño del premio: cuanta plata
de envio quema cada uno por mes. Eso es lo que Full ataca, y esta medido de
las ventas reales.

El dato que ordena todo el analisis: el envio se paga **desde $1.000**, y en
Uruguay el corte es limpio. Medido sobre 125 ordenes de 90 dias: de las 100
con precio unitario por debajo de $1.000, **ninguna** tuvo costo de envio para
el vendedor; de las 25 por arriba, **todas**. La mediana de lo que paga el
vendedor es $160. O sea que el problema del envio esta concentrado en los
productos de mas de $1.000.

Antes de decidir sobre un candidato hay que ponerle encima tres cosas que la
API no da: el costo de almacenamiento de Full (por unidad y por tiempo, es lo
que puede dar vuelta el resultado en un producto de rotacion lenta), el costo
de despachar al deposito de ML, y si ML acepta el producto por tamaño y peso.
Por eso la tabla muestra las unidades por mes al lado de la plata: un producto
que quema mucho envio pero rota poco es mal candidato aunque quede primero.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

from catalogo import es_full, sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

# Franjas de precio: siguen los escalones medidos en tramos.py. El corte de
# $1.000 es el que importa: ahi arranca el envio a cargo del vendedor.
CORTES = [0, 500, 750, 1000, 3000, float("inf")]

# Un candidato tiene que rotar lo suficiente como para que el almacenamiento
# de Full no se coma la diferencia.
MINIMO_UNIDADES = 20
# Y tiene que tener dato de envio real, no un promedio armado con dos ventas.
MINIMA_COBERTURA = 0.2
# Piso para animarse a comparar Full contra deposito propio dentro de una
# franja. Hoy no se alcanza en ninguna; queda listo para cuando se alcance.
MINIMO_POR_FRANJA = 15


def franja(precio):
    precio = precio or 0
    for desde, hasta in zip(CORTES, CORTES[1:]):
        if desde <= precio < hasta:
            if hasta == float("inf"):
                return f"{desde:,.0f}+".replace(",", ".")
            return f"{desde:,.0f}-{hasta:,.0f}".replace(",", ".")
    return "?"


def logistica_por_sku(pubs):
    """SKU -> 'full' | 'mixto' | 'propio', mirando solo publicaciones activas."""
    estado = defaultdict(lambda: {"full": 0, "propio": 0, "titulo": ""})
    for p in pubs:
        if p.get("status") != "active":
            continue
        sku = (sku_del_atributo(p) or "").strip().upper()
        if not sku:
            continue
        e = estado[sku]
        e["titulo"] = e["titulo"] or (p.get("title") or "")[:60]
        if es_full(p):
            e["full"] += 1
        else:
            e["propio"] += 1

    salida = {}
    for sku, e in estado.items():
        if e["full"] and e["propio"]:
            tipo = "mixto"
        elif e["full"]:
            tipo = "full"
        else:
            tipo = "propio"
        salida[sku] = {"logistica": tipo, "titulo": e["titulo"]}
    return salida


def foto_por_franja(df):
    """
    Cuanto paga de envio cada franja de precio, y cuantos SKU hay de cada lado.

    La columna `comparable` dice si en esa franja hay suficientes SKU en Full
    como para comparar contra los propios. Mientras diga que no, el ahorro de
    pasar a Full no se puede medir con datos de la casa.
    """
    filas = []
    for f in sorted(df["franja"].unique(), key=lambda x: df[df["franja"] == x]
                    ["precio_prom"].median()):
        sub = df[df["franja"] == f]
        en_full = sub[sub["logistica"] == "full"]
        propio = sub[sub["logistica"] == "propio"]
        comparable = (len(en_full) >= MINIMO_POR_FRANJA
                      and len(propio) >= MINIMO_POR_FRANJA)
        filas.append({
            "franja": f,
            "sku_propios": len(propio),
            "sku_en_full": len(en_full),
            "envio_propio": float(propio["envio_prom"].median()) if len(propio) else None,
            "envio_full": float(en_full["envio_prom"].median()) if len(en_full) else None,
            "paga_envio": int((propio["envio_prom"] > 0).sum()),
            "plata_envio_mes": float(
                (propio["plata_envio_mensual"]).sum()) if len(propio) else 0.0,
            "comparable": comparable,
        })
    return pd.DataFrame(filas)


def analizar(ml, dias=90, muestra=5, pubs=None, callback=None):
    """Devuelve (candidatos, foto_por_franja)."""
    import rentabilidad as rent

    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    if callback:
        callback("Trayendo ventas del período...")
    ordenes = rent.traer_historico(ml, dias)

    if callback:
        callback("Trayendo costos de envío...")
    envios = rent.traer_costos_envio(
        ml, ordenes, muestra_por_sku=muestra,
        callback=lambda i, t: callback(f"Envíos {i}/{t}...") if callback else None)

    cargos = rent.cargos_por_sku(ordenes, envios)
    if not len(cargos):
        return pd.DataFrame(), pd.DataFrame()

    log = logistica_por_sku(pubs)
    cargos["logistica"] = cargos["sku"].map(
        lambda s: log.get(s, {}).get("logistica", "sin publicación activa"))
    cargos["titulo"] = cargos["sku"].map(lambda s: log.get(s, {}).get("titulo", ""))
    cargos["franja"] = cargos["precio_prom"].map(franja)
    cargos["unidades_por_mes"] = cargos["unidades_vendidas"] / dias * 30
    cargos["plata_envio_mensual"] = cargos["envio_prom"] * cargos["unidades_por_mes"]

    medibles = cargos[cargos["cobertura_envio"] >= MINIMA_COBERTURA].copy()
    foto = foto_por_franja(medibles)

    cand = medibles[
        (medibles["logistica"] == "propio")
        & (medibles["unidades_vendidas"] >= MINIMO_UNIDADES)
        & (medibles["envio_prom"] > 0)].copy()

    cand["envio_sobre_precio"] = (cand["envio_prom"]
                                  / cand["precio_prom"].replace(0, 1))
    cand = cand.sort_values("plata_envio_mensual", ascending=False)
    return cand, foto


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    ml = Meli(verbose=False)
    cand, foto = analizar(ml, dias, callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 70)

    if not len(foto):
        print("Sin datos suficientes de envío.")
        return 0

    pes = lambda v: "—" if v is None or pd.isna(v) else f"${v:,.0f}".replace(",", ".")

    print("=" * 74)
    print(f"CANDIDATOS A FULL  (ultimos {dias} dias)")
    print("=" * 74)
    print("\nDonde se paga el envio, por franja de precio:")
    print(f"  {'franja':<16} {'propios':>8} {'en Full':>8} {'pagan envio':>12} "
          f"{'envio/u':>10} {'plata/mes':>13}")
    for _, f in foto.iterrows():
        print(f"  {f['franja']:<16} {f['sku_propios']:>8} {f['sku_en_full']:>8} "
              f"{f['paga_envio']:>12} {pes(f['envio_propio']):>10} "
              f"{pes(f['plata_envio_mes']):>13}")

    if not foto["comparable"].any():
        print(f"\n  Ninguna franja tiene {MINIMO_POR_FRANJA}+ SKU en Full: no hay")
        print("  con que comparar. El ranking de abajo ordena por tamaño del")
        print("  premio, NO estima cuanto se ahorraria.")

    print(f"\nCandidatos (no estan en Full, +{MINIMO_UNIDADES} unidades, "
          f"pagan envio): {len(cand)}")
    if len(cand):
        print(f"Plata de envio que juntan por mes: "
              f"{pes(cand['plata_envio_mensual'].sum())}\n")
        for _, f in cand.head(15).iterrows():
            print(f"  {f['sku']:<24} {pes(f['plata_envio_mensual'])}/mes de envio")
            print(f"     {f['titulo']}")
            print(f"     {f['unidades_por_mes']:.0f} u/mes · precio "
                  f"{pes(f['precio_prom'])} · envio {pes(f['envio_prom'])} "
                  f"({f['envio_sobre_precio']:.0%} del precio) · cobertura "
                  f"{f['cobertura_envio']:.0%}")

    cand.to_csv(DIR / "candidatos_full.csv", index=False)
    print(f"\nGuardado en candidatos_full.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
