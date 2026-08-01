#!/usr/bin/env python3
"""
Plata sobre la mesa: todo lo accionable en una sola lista, ordenado por pesos.

    python plata.py

El problema que resuelve no es de analisis sino de **atencion**. La informacion
ya estaba: los productos agotados en Alertas, el escalon de comision en Precio
optimo, las promos cofinanciadas en Ganar la venta, los que pierden plata en
Rentabilidad. Pero repartida en seis secciones, y por eso los 20 productos
agotados —$1,38M por semana— llevaban dias sin que nadie los tocara.

Aca cada oportunidad es una fila con: cuanta plata es, que hay que hacer, y en
que seccion se hace. Ordenado por plata, sin importar de que analisis salio.

**Como se comparan cosas distintas.** Un agotado se mide en facturacion
semanal perdida; una suba de precio, en neto por unidad por lo que vende. No
son la misma moneda. Para poder ordenarlos todos juntos se lleva todo a
**pesos por mes**, y la columna `base` dice de donde sale cada numero para que
se pueda desconfiar de los que haya que desconfiar.

Ninguna estimacion asume que cambiar el precio no cambia el volumen: eso no se
sabe, y esta dicho en cada fila que corresponde.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

# Las oportunidades se agrupan por que hay que hacer, no por que analisis las
# encontro: al operador le importa la accion.
# Las unicas dos acciones que se resuelven cambiando un precio, o sea las que
# la app puede ejecutar sola. Reponer stock se hace comprando; tomar una promo,
# desde el panel de MercadoLibre.
ACCIONES_EJECUTABLES = ("revisar_perdida", "subir_escalon")

ACCIONES = {
    "reponer": "Reponer stock",
    "subir_escalon": "Subir al escalón de comisión",
    "revisar_perdida": "Revisar: pierde plata",
    "tomar_promo": "Tomar promoción cofinanciada",
}


def _semana_a_mes(v):
    return v * 30 / 7


def _periodo_a_mes(v, dias):
    return v * 30 / max(dias, 1)


def de_stock(df_stock):
    """Productos agotados o por agotarse. La plata es facturacion parada."""
    if df_stock is None or not len(df_stock):
        return []
    import alertas_stock as al

    filas = []
    for _, f in df_stock[df_stock["diagnostico"].isin(al.URGENTES)].iterrows():
        filas.append({
            "accion": "reponer",
            "sku": f["sku"],
            "titulo": f["titulo"],
            "detalle": (f"{f['diagnostico']} · vendía "
                        f"{f['unidades_periodo']} unidades en el período"),
            "plata_mes": _semana_a_mes(f["plata_semanal_en_riesgo"]),
            "base": "facturación que hoy no entra",
            "seccion": "Alertas → Stock crítico",
            "certeza": "alta",
            "precio_actual": None, "precio_sugerido": None,
        })
    return filas


def de_escalon(df_ventana, cambio_max=0.20, unidades_min=3):
    """
    Cambios chicos de precio que aprovechan un escalon de ML.

    **En Uruguay casi siempre son BAJAS, no subas**: el escalon de $1.000
    ahorra $40 de cargo fijo pero activa ~$160 de envio a cargo del vendedor,
    asi que lo que deja plata es quedarse debajo de esa linea, no cruzarla.
    Ver `tramos.py`.

    Solo entran las que **mejoran el neto** y no arriesgan una pagina de
    catalogo que hoy se este ganando.
    """
    if df_ventana is None or not len(df_ventana):
        return []

    sel = df_ventana[
        df_ventana["cruza_escalon"]
        & (df_ventana["gana_neto"].fillna(0) > 0)
        & (df_ventana["cambio_pct"].abs() <= cambio_max)
        & (df_ventana["unidades"] >= unidades_min)
    ]
    filas = []
    for _, f in sel.iterrows():
        filas.append({
            "accion": "subir_escalon",
            "sku": f["sku"],
            "titulo": f["titulo"],
            "detalle": (f"${f['precio_actual']:,.0f} → "
                        f"${f['precio_sugerido']:,.0f} "
                        f"({f['cambio_pct']:+.0%}) · neto por unidad "
                        f"${f['neto_actual']:,.0f} → "
                        f"${f['neto_sugerido']:,.0f}").replace(",", "."),
            "plata_mes": _periodo_a_mes(f["impacto_periodo"], 90),
            "base": "mismo volumen que el período medido",
            "seccion": "Precio óptimo",
            "certeza": "alta",
            "precio_actual": f["precio_actual"],
            "precio_sugerido": f["precio_sugerido"],
        })
    return filas


def de_perdida(df_rent, dias=90, df_ventana=None):
    """
    SKU que pierden plata de caja: costo + comision + envio > precio.

    El precio nuevo sale de la **ventana de precio**, no de aca: esta funcion
    sabe cuanto se pierde pero no a que precio habria que estar. Si un SKU no
    tiene fila en la ventana queda sin `precio_sugerido` y por lo tanto **no
    es ejecutable** — se muestra igual, para revisarlo a mano.
    """
    if df_rent is None or not len(df_rent):
        return []

    sugerido, actual = {}, {}
    if df_ventana is not None and len(df_ventana):
        for _, v in df_ventana.iterrows():
            if pd.notna(v.get("precio_sugerido")):
                sugerido[v["sku"]] = float(v["precio_sugerido"])
                actual[v["sku"]] = float(v["precio_actual"])

    filas = []
    for _, f in df_rent[(df_rent["margen"] < 0)
                        & (df_rent["unidades_90d"] > 0)].iterrows():
        perdida = abs(f["margen"] * f["unidades_90d"])
        p_act = actual.get(f["sku"], f.get("precio_ml"))
        p_sug = sugerido.get(f["sku"])
        filas.append({
            "accion": "revisar_perdida",
            "sku": f["sku"],
            "titulo": "",
            "detalle": (f"pierde ${abs(f['margen']):,.0f} por unidad · "
                        f"{int(f['unidades_90d'])} unidades en "
                        f"{dias} días").replace(",", "."),
            "plata_mes": _periodo_a_mes(perdida, dias),
            "base": "lo que se pierde hoy, si se sigue vendiendo igual",
            "seccion": "Precio óptimo / Rentabilidad",
            "certeza": "alta",
            "precio_actual": p_act,
            "precio_sugerido": p_sug,
        })
    return filas


def de_promos(df_promos):
    """
    Promociones donde ML pone parte del descuento.

    La plata no se puede estimar en pesos sin saber cuanto sube el volumen, y
    eso no se sabe. Se deja `plata_mes` en cero y se ordena aparte: es una
    oportunidad real pero no cuantificable, y mentir un numero seria peor que
    no ponerlo.
    """
    if df_promos is None or not len(df_promos):
        return []

    sel = df_promos[df_promos["diagnostico"] == "ML pone parte"]
    sel = sel.drop_duplicates(subset=["item_id"])
    filas = []
    for _, f in sel.iterrows():
        filas.append({
            "accion": "tomar_promo",
            "sku": f["sku"],
            "titulo": f["titulo"],
            "detalle": (f"{f['promocion']} · ML pone {f['aporte_ml']:.1%} y "
                        f"vos {f['aporte_vendedor']:.1%}"),
            "plata_mes": 0.0,
            "base": "no cuantificable: depende de cuánto suba el volumen",
            "seccion": "Ganar la venta → Promociones",
            "certeza": "media",
            "precio_actual": None, "precio_sugerido": None,
        })
    return filas


# Que mide la plata de cada accion. **No se pueden sumar entre si**: una es
# facturacion que no entra y la otra es margen que se pierde. Un total unico
# que mezcle las dos es un numero lindo y mentiroso.
UNIDAD = {
    "reponer": "facturación",
    "subir_escalon": "margen",
    "revisar_perdida": "margen",
    "tomar_promo": "sin cuantificar",
}


def juntar(stock=None, ventana=None, rentabilidad=None, promos=None,
           dias_rent=90):
    """
    Arma la lista unica, ordenada por plata mensual.

    Dos cosas que se resuelven aca y no en cada fuente:

    **Un SKU puede aparecer en varias acciones, y a veces se contradicen.**
    Reponer un producto que pierde plata en cada unidad **aumenta** la
    perdida. Cuando eso pasa, la fila de reponer se marca `conflicto` y cambia
    de consejo: primero hay que arreglar el precio o el costo.

    **No se devuelve un total unico.** Sumar facturacion que no entra con
    margen que se pierde da un numero grande y sin sentido. El resumen da un
    subtotal por accion, cada uno con su unidad.
    """
    filas = (de_stock(stock) + de_escalon(ventana)
             + de_perdida(rentabilidad, dias_rent, df_ventana=ventana)
             + de_promos(promos))
    df = pd.DataFrame(filas)
    if not len(df):
        return df

    df["accion_nombre"] = df["accion"].map(ACCIONES)
    df["unidad"] = df["accion"].map(UNIDAD)

    pierden = set(df[df["accion"] == "revisar_perdida"]["sku"])
    df["conflicto"] = (df["accion"] == "reponer") & df["sku"].isin(pierden)
    df.loc[df["conflicto"], "accion_nombre"] = "⚠️ Revisar ANTES de reponer"
    df.loc[df["conflicto"], "detalle"] = (
        df.loc[df["conflicto"], "detalle"]
        + " — OJO: este producto pierde plata en cada unidad, reponerlo "
          "aumenta la pérdida")

    # Ejecutable = se puede escribir el precio nuevo de una. Reponer stock y
    # tomar una promo NO lo son: la primera se hace comprando mercadería y la
    # segunda desde el panel de ML.
    # `to_numeric` no es decorativo: si NINGUNA fila tiene precio (pasa cuando
    # lo unico accionable es reponer stock o tomar promos), la lista queda toda
    # en None, pandas la tipa como `object` y `.abs()` revienta con TypeError.
    # Ademas el `&` de pandas NO corta: evalua `.abs()` aunque el `.notna()`
    # de al lado ya sea False. Forzando float, los None pasan a NaN y anda.
    df["cambio_pct"] = pd.to_numeric(
        pd.Series([((s_ - a) / a) if (pd.notna(s_) and pd.notna(a) and a) else None
                   for s_, a in zip(df["precio_sugerido"], df["precio_actual"])],
                  index=df.index),
        errors="coerce")
    df["ejecutable"] = (
        df["accion"].isin(ACCIONES_EJECUTABLES)
        & df["precio_sugerido"].notna()
        & df["cambio_pct"].notna()
        & (df["cambio_pct"].abs() > 0.001)
        # Un producto marcado como conflicto no se toca solo: reponerlo y
        # subirle el precio son decisiones distintas y hay que mirarlo.
        & (~df["conflicto"]))

    df["veces"] = df.groupby("sku")["sku"].transform("size")
    return df.sort_values("plata_mes", ascending=False).reset_index(drop=True)


def resumen(df):
    """
    Subtotales **por acción**, cada uno con su unidad. A propósito no hay un
    total general: ver la nota en `juntar()`.
    """
    if df is None or not len(df):
        return {}
    por_accion = {}
    for accion, g in df.groupby("accion"):
        por_accion[ACCIONES.get(accion, accion)] = {
            "count": len(g),
            "sum": float(g["plata_mes"].sum()),
            "unidad": UNIDAD.get(accion, ""),
        }
    return {
        "oportunidades": len(df),
        "facturacion_parada": float(
            df[df["unidad"] == "facturación"]["plata_mes"].sum()),
        "margen_en_juego": float(
            df[df["unidad"] == "margen"]["plata_mes"].sum()),
        "conflictos": int(df["conflicto"].sum()),
        "por_accion": por_accion,
    }


def main():
    ml = Meli(verbose=False)
    import alertas_stock as al
    import buybox
    import rentabilidad as rent
    import ventana as vt

    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    costos, _ = rent.costos_guardados()
    ordenes = rent.traer_historico(ml, 90)
    envios = rent.traer_costos_envio(ml, ordenes, muestra_por_sku=5)
    cargos = rent.cargos_por_sku(ordenes, envios)

    print("Stock...")
    stock = al.analizar(ml, dias=90, pubs=pubs, ordenes=ordenes)
    print("Rentabilidad...")
    rentab = rent.calcular(costos, cargos, pubs, iva=0.22)
    print("Ventana de precio...")
    cat = [p["id"] for p in pubs
           if p.get("status") == "active" and p.get("catalog_listing")]
    ptw = buybox.traer_price_to_win(ml, cat)
    ven = vt.analizar(costos, cargos, pubs, iva=0.22, objetivo=0.0,
                      ptw_por_item=ptw)

    df = juntar(stock=stock, ventana=ven, rentabilidad=rentab)
    r = resumen(df)
    pes = lambda v: f"${v:,.0f}".replace(",", ".")

    print("\n" + "=" * 74)
    print("PLATA SOBRE LA MESA")
    print("=" * 74)
    print(f"  Oportunidades: {r['oportunidades']}")
    print(f"  Facturación parada:  {pes(r['facturacion_parada']):>14}/mes")
    print(f"  Margen en juego:     {pes(r['margen_en_juego']):>14}/mes")
    print("  (no se suman: una es lo que no entra, la otra lo que se pierde)")
    if r["conflictos"]:
        print(f"\n  {r['conflictos']} productos estan para reponer PERO "
              "pierden plata: revisarlos antes.")
    print()
    for accion, d in sorted(r["por_accion"].items(),
                            key=lambda x: -x[1]["sum"]):
        print(f"  {accion:<32} {int(d['count']):>4} · "
              f"{pes(d['sum']):>14}/mes ({d['unidad']})")

    print("\n  Las 15 más grandes:")
    for _, f in df.head(15).iterrows():
        print(f"    {pes(f['plata_mes']):>14}/mes · {f['accion_nombre']}")
        print(f"       {f['sku']} — {f['detalle']}")

    df.to_csv(DIR / "plata.csv", index=False)
    print(f"\nGuardado en plata.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
