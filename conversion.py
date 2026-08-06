#!/usr/bin/env python3
"""
Visitas contra ventas: donde esta la plata dormida.

    python conversion.py           -> ultimos 30 dias
    python conversion.py 60

Cruza cuantas veces vieron cada publicacion contra cuantas unidades vendio, y
las clasifica. Los dos grupos que importan:

  - **Se ve y no vende**: mucha visita, poca o ninguna venta. El producto
    interesa pero algo lo frena: precio, fotos, descripcion o reputacion.
  - **Vende y no se ve**: convierte bien pero casi no recibe visitas. Le falta
    exposicion — candidata a Full, a publicidad o a mejorar el titulo.

Es solo lectura: no toca nada de la cuenta.

Ojo con el formato de fecha: los endpoints de visitas quieren `YYYY-MM-DD`
pelado y tiran 400 si les mandas ISO completo (al reves que /orders/search).
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from meli import Meli, MeliError
from ventas import traer_ordenes

DIR = Path(__file__).resolve().parent

# Debajo de esto no hay muestra suficiente para sacar conclusiones.
VISITAS_MINIMAS = 30


CACHE_VISITAS = DIR / "visitas.json"


def visitas_por_item(ml, item_ids, desde, hasta, callback=None,
                     refrescar=False):
    """
    Visitas por publicacion.

    OJO: `/items/visits` **acepta un solo id por llamada** — con dos o mas
    responde 400 "number of items to query is 1". O sea que es una llamada por
    publicacion: con 438 activas son ~2 minutos. Por eso se cachea en disco
    por (item, rango de fechas).

    Los errores NO se silencian: si una publicacion falla queda como None y no
    como cero, para no confundir "no pude medirla" con "nadie la vio".
    """
    clave_rango = f"{desde:%Y-%m-%d}_{hasta:%Y-%m-%d}"
    cache = {}
    if CACHE_VISITAS.exists() and not refrescar:
        try:
            guardado = json.loads(CACHE_VISITAS.read_text(encoding="utf-8"))
            if guardado.get("rango") == clave_rango:
                cache = guardado.get("visitas", {})
        except Exception:
            cache = {}

    ids = [i for i in item_ids if i not in cache]
    for n, iid in enumerate(ids, start=1):
        try:
            r = ml.get("/items/visits", ids=iid,
                       date_from=desde.strftime("%Y-%m-%d"),
                       date_to=hasta.strftime("%Y-%m-%d"))
            fila = r[0] if isinstance(r, list) and r else (r or {})
            cache[iid] = fila.get("total_visits")
        except Exception:  # noqa: BLE001
            cache[iid] = None       # no medida, distinto de cero visitas
        if callback and n % 25 == 0:
            callback(n, len(ids))
        if n % 200 == 0:            # guardado parcial, por si se corta
            CACHE_VISITAS.write_text(
                json.dumps({"rango": clave_rango, "visitas": cache}),
                encoding="utf-8")

    CACHE_VISITAS.write_text(
        json.dumps({"rango": clave_rango, "visitas": cache}), encoding="utf-8")
    return cache


def ventas_por_item(ordenes):
    """Unidades y facturacion por publicacion, sin contar canceladas."""
    acc = defaultdict(lambda: {"unidades": 0, "importe": 0.0, "ordenes": 0})
    for o in ordenes:
        if o.get("status") == "cancelled":
            continue
        for it in o.get("order_items") or []:
            iid = it["item"].get("id")
            if not iid:
                continue
            q = it.get("quantity") or 0
            acc[iid]["unidades"] += q
            acc[iid]["importe"] += (it.get("unit_price") or 0) * q
            acc[iid]["ordenes"] += 1
    return acc


def clasificar(fila, conv_mediana):
    """
    Etiqueta cada publicacion. La comparacion es contra la mediana de la propia
    cuenta, no contra un numero fijo: lo que es buena conversion depende del
    rubro y del precio.
    """
    v, u, c = fila["visitas"], fila["unidades"], fila["conversion"]

    if not fila.get("medida", True):
        return "sin_medir", "No se pudieron leer las visitas de esta publicación."
    if v < VISITAS_MINIMAS and u == 0:
        return "sin_datos", "Muy pocas visitas para sacar conclusiones."
    if v >= VISITAS_MINIMAS and u == 0:
        return "no_vende", ("La ven pero nadie compra. Revisá precio, fotos y "
                            "descripción.")
    if v >= VISITAS_MINIMAS and c < conv_mediana * 0.5:
        return "convierte_poco", (f"Convierte {c:.1%} contra {conv_mediana:.1%} "
                                  "de tu promedio. Algo la frena.")
    if u > 0 and v < VISITAS_MINIMAS:
        return "falta_exposicion", ("Vende con muy pocas visitas: si la ve más "
                                    "gente, vende más.")
    if c > conv_mediana * 2 and v < 200:
        return "escalar", ("Convierte muy por encima de tu promedio. Vale "
                           "empujarla con exposición o publicidad.")
    return "normal", ""


def analizar(ml, dias=30, callback=None):
    # **No leer catalogo.json directo.** Esta en el .gitignore, asi que en el
    # runner de Actions no existe y la corrida muere con FileNotFoundError.
    # `cargar_catalogo` lo usa si esta y si no lo baja de ML. Importa desde
    # que `publicidad_cron.py` llama aca y corre por GitHub Actions.
    from catalogo import cargar_catalogo
    pubs = cargar_catalogo(ml)
    activas = [p for p in pubs if p.get("status") == "active"]

    hasta = datetime.now()
    desde = hasta - timedelta(days=dias)

    if callback:
        callback("Trayendo ventas del período...")
    ventas = ventas_por_item(traer_ordenes(ml, desde, hasta))

    if callback:
        callback("Trayendo visitas de cada publicación...")
    visitas = visitas_por_item(
        ml, [p["id"] for p in activas], desde, hasta,
        callback=lambda i, t: callback(f"Visitas {i}/{t}...") if callback else None)

    filas = []
    for p in activas:
        iid = p["id"]
        v = visitas.get(iid)
        medida = v is not None
        v = v or 0
        vt = ventas.get(iid, {"unidades": 0, "importe": 0.0, "ordenes": 0})
        filas.append({
            "item_id": iid,
            "sku": sku_del_atributo(p) or "",
            "titulo": (p.get("title") or "")[:65],
            "precio": p.get("price"),
            "stock": p.get("available_quantity"),
            "visitas": v,
            "unidades": vt["unidades"],
            "importe": vt["importe"],
            "conversion": (vt["unidades"] / v) if v else 0.0,
            "medida": medida,
        })

    df = pd.DataFrame(filas)
    if not len(df):
        return df

    # La mediana se calcula solo sobre publicaciones con muestra suficiente.
    con_muestra = df[(df["visitas"] >= VISITAS_MINIMAS) & (df["unidades"] > 0)]
    conv_mediana = float(con_muestra["conversion"].median()) if len(con_muestra) else 0.02

    etiquetas = df.apply(lambda f: clasificar(f, conv_mediana), axis=1)
    df["diagnostico"] = [e[0] for e in etiquetas]
    df["recomendacion"] = [e[1] for e in etiquetas]
    df.attrs["conversion_mediana"] = conv_mediana
    return df.sort_values("visitas", ascending=False)


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    ml = Meli(verbose=False)
    df = analizar(ml, dias, callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 60)

    conv = df.attrs.get("conversion_mediana", 0)
    print(f"Publicaciones activas: {len(df)}")
    print(f"Visitas totales: {df['visitas'].sum():,}".replace(",", "."))
    print(f"Conversión mediana de la cuenta: {conv:.2%}\n")

    print("Diagnóstico:")
    for k, n in df["diagnostico"].value_counts().items():
        print(f"   {k:<20} {n:>5}")

    peores = df[df["diagnostico"] == "no_vende"].nlargest(10, "visitas")
    if len(peores):
        print("\nLas 10 más vistas que NO vendieron nada:")
        print(peores[["sku", "visitas", "precio", "titulo"]]
              .to_string(index=False, float_format=lambda x: f"{x:,.0f}"))

    df.to_csv(DIR / "conversion.csv", index=False)
    print("\nGuardado en conversion.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
