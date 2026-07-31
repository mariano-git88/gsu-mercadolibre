#!/usr/bin/env python3
"""
Reporte semanal: lo que pasó la semana pasada y lo que hay que resolver.

    python reporte.py            -> semana cerrada (lunes a domingo)
    python reporte.py 14         -> ultimos 14 dias corridos

El resto de las herramientas hay que acordarse de abrirlas. Esto es lo
contrario: una sola pantalla que se lee en dos minutos y dice si la semana
estuvo bien o mal y que hay que hacer el lunes.

Tres bloques, en ese orden a proposito:

  1. **Como vino la semana** contra la anterior. Sin comparacion un numero no
     dice nada: $50M de venta puede ser una buena o una mala semana.
  2. **Que hay que resolver**, ordenado por plata. Productos por quedarse sin
     stock, reclamos abiertos, preguntas sin responder.
  3. **Que se movio**: los que mas facturaron y los que se cayeron.

Sobre la ventana: por defecto compara la ultima semana **cerrada** (lunes a
domingo) contra la anterior. Es aposta — comparar una semana a medias contra
una entera siempre da que las ventas se derrumbaron.

Las ordenes se bajan directo con `ventas.traer_ordenes()`, sin pasar por el
cache de `rentabilidad.traer_historico()`: ese cache esta indexado por
cantidad de dias y pedirle 14 cuando tiene 90 lo obliga a rebajar todo. El
reporte le pasa despues sus propias ordenes a los modulos de alertas.
"""

import sys
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

import alertas_stock
from catalogo import sku_del_atributo
from meli import Meli, MeliError
from ventas import resumir, traer_ordenes

# Un producto que vendia y esta semana no vendio nada: se avisa solo si venia
# vendiendo lo suficiente como para que el cero signifique algo.
MINIMO_CAIDA = 5


def semana_cerrada(hoy=None):
    """(desde, hasta) del ultimo lunes-a-domingo completo."""
    hoy = hoy or datetime.now()
    inicio_semana_actual = (hoy - timedelta(days=hoy.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return inicio_semana_actual - timedelta(days=7), inicio_semana_actual


def ventanas(dias=None, hoy=None):
    """Devuelve (desde, hasta, desde_previa, hasta_previa)."""
    hoy = hoy or datetime.now()
    if dias:
        hasta = hoy
        desde = hasta - timedelta(days=dias)
    else:
        desde, hasta = semana_cerrada(hoy)
    largo = hasta - desde
    return desde, hasta, desde - largo, desde


def _por_sku(ordenes):
    """SKU -> unidades y facturacion de las ordenes pagadas."""
    acc = defaultdict(lambda: {"unidades": 0, "monto": 0.0, "titulo": ""})
    for o in ordenes:
        if o.get("status") not in ("paid", "partially_refunded"):
            continue
        for it in o.get("order_items") or []:
            sku = (it["item"].get("seller_sku") or "").strip().upper()
            if not sku:
                continue
            a = acc[sku]
            a["unidades"] += it.get("quantity") or 0
            a["monto"] += (it.get("unit_price") or 0) * (it.get("quantity") or 0)
            a["titulo"] = a["titulo"] or (it["item"].get("title") or "")[:60]
    return acc


def variacion(actual, previo):
    """Cambio porcentual, o None si no hay base con que comparar."""
    if not previo:
        return None
    return (actual - previo) / previo


def generar(ml, dias=None, pubs=None, callback=None, con_reclamos=True):
    """
    Arma el reporte completo. Devuelve un dict con todos los bloques.

    `con_reclamos` se puede apagar: identificar el producto de cada reclamo
    cuesta una llamada por envio y es la parte lenta del reporte.
    """
    desde, hasta, desde_prev, hasta_prev = ventanas(dias)

    if callback:
        callback("Trayendo las ventas del período...")
    ordenes = traer_ordenes(ml, desde, hasta)
    if callback:
        callback("Trayendo las del período anterior...")
    previas = traer_ordenes(ml, desde_prev, hasta_prev)

    r, rp = resumir(ordenes), resumir(previas)

    ventas = {
        "desde": desde, "hasta": hasta,
        "desde_previa": desde_prev, "hasta_previa": hasta_prev,
        "ordenes": r["cantidad"], "ordenes_prev": rp["cantidad"],
        "unidades": r["unidades"], "unidades_prev": rp["unidades"],
        "bruto": r["bruto"], "bruto_prev": rp["bruto"],
        "comisiones": r["comisiones"], "comisiones_prev": rp["comisiones"],
        "neto": r["bruto"] - r["comisiones"],
        "neto_prev": rp["bruto"] - rp["comisiones"],
        "ticket": r["bruto"] / max(r["cantidad"], 1),
        "ticket_prev": rp["bruto"] / max(rp["cantidad"], 1),
    }
    for campo in ("ordenes", "unidades", "bruto", "comisiones", "neto", "ticket"):
        ventas[f"var_{campo}"] = variacion(ventas[campo], ventas[f"{campo}_prev"])
    ventas["comision_pct"] = (r["comisiones"] / r["bruto"]) if r["bruto"] else 0.0

    # ------------------------------------------------------ que se movio
    act, prev = _por_sku(ordenes), _por_sku(previas)

    top = sorted(act.items(), key=lambda x: -x[1]["monto"])[:10]
    top = pd.DataFrame([
        {"sku": s, "titulo": d["titulo"], "unidades": d["unidades"],
         "facturacion": d["monto"],
         "var": variacion(d["monto"], prev.get(s, {}).get("monto", 0))}
        for s, d in top])

    # Los que venian vendiendo y esta semana no vendieron nada.
    caidas = pd.DataFrame([
        {"sku": s, "titulo": d["titulo"], "unidades_previas": d["unidades"],
         "facturacion_previa": d["monto"]}
        for s, d in prev.items()
        if d["unidades"] >= MINIMO_CAIDA and s not in act])
    if len(caidas):
        caidas = caidas.sort_values("facturacion_previa", ascending=False).head(10)

    # ------------------------------------------------------ que resolver
    if callback:
        callback("Revisando el stock...")
    # La velocidad sale de las mismas ordenes del periodo: es la foto mas
    # reciente, que es lo que corresponde para decidir esta semana.
    dias_ventana = max((hasta - desde).days, 1)
    stock = alertas_stock.analizar(ml, dias=dias_ventana, pubs=pubs,
                                   ordenes=ordenes)
    urgentes = (stock[stock["diagnostico"].isin(alertas_stock.URGENTES)]
                if len(stock) else pd.DataFrame())

    if callback:
        callback("Contando preguntas sin responder...")
    try:
        import preguntas as preg
        sin_responder = len(preg.pendientes(ml))
    except Exception as e:                      # la seccion no debe romper el
        sin_responder = None                    # reporte entero si falla
        if callback:
            callback(f"Preguntas: no se pudieron contar ({str(e)[:60]})")

    rec_df, rec_res = pd.DataFrame(), {}
    if con_reclamos:
        if callback:
            callback("Revisando reclamos...")
        try:
            import reclamos
            rec_df, rec_res = reclamos.analizar(
                ml, dias=dias_ventana, pubs=pubs, ordenes=ordenes,
                callback=callback)
        except MeliError as e:
            if callback:
                callback(f"Reclamos: {str(e)[:60]}")

    return {
        "ventas": ventas,
        "top": top,
        "caidas": caidas,
        "stock": stock,
        "stock_urgentes": urgentes,
        "stock_resumen": alertas_stock.resumen(stock) if len(stock) else {},
        "preguntas_sin_responder": sin_responder,
        "reclamos": rec_df,
        "reclamos_resumen": rec_res,
    }


# ------------------------------------------------------------------ salida

def _pes(v):
    try:
        return f"${float(v):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _var(v):
    # Ojo: al meter los None en un DataFrame pandas los convierte en NaN, asi
    # que no alcanza con preguntar por None (salia "+nan%" en los productos
    # que no habian vendido en el periodo anterior).
    if v is None or pd.isna(v):
        return "  nuevo"
    return f"{v:+7.1%}"


def imprimir(rep):
    v = rep["ventas"]
    print("=" * 72)
    print(f"REPORTE  {v['desde']:%d/%m/%Y} a {v['hasta']:%d/%m/%Y}")
    print(f"         contra {v['desde_previa']:%d/%m} a {v['hasta_previa']:%d/%m}")
    print("=" * 72)

    print(f"\n  {'':22} {'periodo':>14} {'anterior':>14} {'var':>8}")
    for etiqueta, campo, plata in [
            ("Órdenes", "ordenes", False), ("Unidades", "unidades", False),
            ("Facturación", "bruto", True), ("Comisiones ML", "comisiones", True),
            ("Neto post-comisión", "neto", True), ("Ticket promedio", "ticket", True)]:
        fmt = _pes if plata else (lambda x: f"{int(x):,}".replace(",", "."))
        print(f"  {etiqueta:<22} {fmt(v[campo]):>14} {fmt(v[f'{campo}_prev']):>14} "
              f"{_var(v[f'var_{campo}']):>8}")
    print(f"\n  Comisión sobre facturación: {v['comision_pct']:.1%}")

    # ------------------------------------------------------ a resolver
    print("\n" + "-" * 72)
    print("PARA RESOLVER ESTA SEMANA")
    print("-" * 72)

    sr = rep["stock_resumen"]
    if sr:
        print(f"  Vendió y no tiene publicación activa: {sr['sin_publicacion']}")
        print(f"  Stock crítico (menos de una semana):  {sr['criticos']}")
        print(f"  Facturación semanal en riesgo:        "
              f"{_pes(sr['plata_en_riesgo'])}")

    u = rep["stock_urgentes"]
    if len(u):
        print(f"\n  Los {min(8, len(u))} de mayor impacto:")
        for _, f in u.head(8).iterrows():
            estado = (f"{f['dias_cobertura']:.0f} días" if f["stock"]
                      else f["diagnostico"])
            print(f"    {f['sku']:<24} {estado:<22} "
                  f"{_pes(f['plata_semanal_en_riesgo'])}/sem")

    if rep["preguntas_sin_responder"] is not None:
        print(f"\n  Preguntas sin responder: {rep['preguntas_sin_responder']}")

    rr = rep["reclamos_resumen"]
    if rr:
        print(f"  Reclamos del período: {rr['reclamos']} "
              f"({rr['abiertos']} abiertos) · tasa {rr['tasa_cuenta']:.2%}")
        rd = rep["reclamos"]
        if len(rd):
            graves = rd[rd["diagnostico"] == "tasa alta"]
            if len(graves):
                print(f"  Productos con tasa de reclamo alta: {len(graves)}")
                for _, f in graves.head(5).iterrows():
                    print(f"    {f['sku']:<24} {f['reclamos']} rec / "
                          f"{f['unidades_vendidas']} u = {f['tasa']:.1%}")

    # ------------------------------------------------------ que se movio
    print("\n" + "-" * 72)
    print("LO QUE MÁS FACTURÓ")
    print("-" * 72)
    for _, f in rep["top"].iterrows():
        print(f"  {_pes(f['facturacion']):>13}  {_var(f['var'])}  "
              f"{int(f['unidades']):>4} u  {f['sku']}")
        print(f"                                   {f['titulo']}")

    c = rep["caidas"]
    if len(c):
        print("\n" + "-" * 72)
        print(f"VENDÍAN Y ESTE PERÍODO NO VENDIERON NADA ({len(c)})")
        print("-" * 72)
        for _, f in c.iterrows():
            print(f"  {_pes(f['facturacion_previa']):>13}  "
                  f"{int(f['unidades_previas']):>4} u antes  {f['sku']}")
            print(f"                                   {f['titulo']}")
    print()


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else None
    ml = Meli(verbose=False)
    rep = generar(ml, dias, callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 70)
    imprimir(rep)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
