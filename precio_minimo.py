#!/usr/bin/env python3
"""
Precio minimo viable: a que precio hay que estar para no perder plata.

    python precio_minimo.py            -> margen objetivo 15%
    python precio_minimo.py 25         -> margen objetivo 25%

Es la herramienta inversa a `buybox.py`. Buy Box pregunta "hasta donde puedo
bajar"; esta pregunta **"desde donde no puedo bajar"**, que con los costos
reales resulto ser el problema grande en Argentina; en Uruguay hay que
volver a medirlo con los costos cargados.

**El detalle que hace toda la diferencia: los escalones.** MercadoLibre cobra
un porcentaje mas un cargo fijo por unidad, y ese cargo salta por tramos de
precio (ver `tramos.py`). Eso hace que el precio minimo NO se pueda despejar
con una sola cuenta: hay que resolverlo por tramo y quedarse con el menor
precio que efectivamente cierra.

**En Uruguay hay un segundo escalon que pesa mas que el cargo fijo**: desde
$1.000 el envio deja de pagarlo el comprador y pasa a pagarlo el vendedor
(~$160, medido sobre las ventas reales). Como el cargo fijo que se ahorra ahi
son $40, cruzar $1.000 encarece el producto en ~$120 netos. El precio minimo
viable de un producto que hoy esta debajo de $1.000 casi nunca conviene
empujarlo por encima de esa linea.

La cuenta, con el ingreso ya sin IVA:

    margen = ingreso*(1 - otros) - precio*pct - cargo_fijo(precio) - envio - costo

y se busca el precio mas chico donde `margen >= objetivo * precio`.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent


def _bandas():
    """Los tramos de cargo fijo como (desde, hasta, fijo)."""
    from tramos import TRAMOS
    bandas, desde = [], 0.0
    for tope, fijo in TRAMOS:
        bandas.append((desde, float(tope), float(fijo)))
        desde = float(tope)
    return bandas


def precio_minimo(costo, pct, envio, iva=0.22, otros=None, objetivo=0.15):
    """
    El precio mas bajo que deja `objetivo` de margen sobre el precio.

    Devuelve None si no hay precio que alcance: pasa cuando la comision
    porcentual mas los conceptos porcentuales mas el objetivo se comen todo
    el ingreso, y ahi subir el precio no arregla nada.

    **Hay dos escalones distintos que resolver, no uno.** El cargo fijo de ML
    salta por tramos de precio, y el costo logistico es 10% **o $9.000, lo que
    sea menor**, asi que arriba de cierto ingreso deja de ser porcentual y
    pasa a ser un monto fijo. La ecuacion cambia de forma en cada combinacion,
    asi que se resuelve en cada una y se toma el menor precio que de verdad
    cierra en su propio tramo.
    """
    from rentabilidad import OTROS_CONCEPTOS, TOPE_LOGISTICO

    o = dict(OTROS_CONCEPTOS)
    if otros:
        o.update(otros)

    # Ingreso a partir del cual el logistico queda topeado.
    corte_log = (TOPE_LOGISTICO / o["logistico"]) if o["logistico"] else float("inf")

    # Cada regimen aporta: (parte porcentual del ingreso, monto fijo extra).
    regimenes = [
        # logistico porcentual: vale mientras el ingreso no pase el corte
        (o["impuestos"] + o["logistico"] + o["general"], 0.0, 0.0, corte_log),
        # logistico topeado: vale de ahi para arriba
        (o["impuestos"] + o["general"], TOPE_LOGISTICO, corte_log, float("inf")),
    ]

    candidatos = []
    for tasa, extra, ing_desde, ing_hasta in regimenes:
        k = (1 - tasa) / (1 + iva) - pct - objetivo
        if k <= 0:
            continue
        for desde, hasta, fijo in _bandas():
            base = fijo + envio + costo + extra
            p = base / k
            ingreso = p / (1 + iva)
            # Solo vale si el precio cae en la banda de cargo fijo Y en el
            # regimen logistico con los que se calculo.
            if desde <= p < hasta and ing_desde <= ingreso < ing_hasta:
                candidatos.append(p)
            # Los bordes tambien son candidatos: cruzar un escalon puede hacer
            # viable un precio que dentro del tramo anterior no cerraba.
            for borde in (desde, ing_desde * (1 + iva)):
                if borde <= 0:
                    continue
                ing_b = borde / (1 + iva)
                tasa_b, extra_b = ((o["impuestos"] + o["logistico"]
                                    + o["general"], 0.0)
                                   if ing_b < corte_log
                                   else (o["impuestos"] + o["general"],
                                         TOPE_LOGISTICO))
                margen_b = (borde * (1 - tasa_b) / (1 + iva) - borde * pct
                            - cargo_fijo_de(borde) - envio - costo - extra_b)
                if margen_b >= objetivo * borde:
                    candidatos.append(borde)

    return min(candidatos) if candidatos else None


def cargo_fijo_de(precio):
    from tramos import cargo_fijo
    return cargo_fijo(precio)


def analizar(costos_df, cargos_df, pubs, iva=0.22, otros_conceptos=None,
             objetivo=0.15):
    """
    Por SKU: precio actual, precio minimo viable y cuanto habria que subir.

    Usa el porcentaje de comision **real** de cada SKU, despejado de lo que ML
    cobro (asi vale igual para Clasica que para Premium), y el envio promedio
    medido de las ventas.
    """
    from rentabilidad import OTROS_CONCEPTOS, otros_conceptos_monto
    from resolver import indexar_por_sku, resolver_precio
    from tramos import cargo_fijo

    otros = dict(OTROS_CONCEPTOS)
    if otros_conceptos:
        otros.update(otros_conceptos)

    pct, envio, unidades = {}, {}, {}
    for _, f in cargos_df.iterrows():
        p = f["precio_prom"] or 0
        if p > 0:
            pct[f["sku"]] = max(((f["comision_prom"] or 0) - cargo_fijo(p)) / p,
                                0.0)
        envio[f["sku"]] = f["envio_prom"] or 0.0
        unidades[f["sku"]] = int(f["unidades_vendidas"] or 0)

    indice = indexar_por_sku(pubs)

    filas = []
    for _, f in costos_df.iterrows():
        sku, costo = f["sku"], float(f["costo"])
        res = resolver_precio(sku, indice)
        if not res.ok:
            continue
        pub = res.destinos[0]
        actual = pub.get("price")
        if not actual:
            continue

        p = pct.get(sku)
        if p is None:
            # Sin ventas no hay comision medida: se usa la base de Clasica.
            from tramos import PORCENTAJE
            p = PORCENTAJE
        e = envio.get(sku, 0.0)

        minimo = precio_minimo(costo, p, e, iva=iva, otros=otros,
                               objetivo=objetivo)

        def margen_a(precio):
            ingreso = precio / (1 + iva)
            _, otros_monto = otros_conceptos_monto(ingreso, otros)
            return (ingreso - otros_monto - precio * p
                    - cargo_fijo(precio) - e - costo)

        m_hoy = margen_a(actual)
        m_min = margen_a(minimo) if minimo else None

        if minimo is None:
            diag = "no cierra a ningún precio"
        elif actual >= minimo:
            diag = "ok"
        else:
            diag = "hay que subir"

        from buybox import marca as marca_de
        filas.append({
            "sku": sku,
            "item_id": pub["id"],
            "marca": marca_de(pub),
            "titulo": (pub.get("title") or "")[:60],
            "diagnostico": diag,
            "precio_actual": actual,
            "precio_minimo": minimo,
            "subir": (minimo - actual) if (minimo and minimo > actual) else 0.0,
            "subir_pct": ((minimo - actual) / actual
                          if (minimo and minimo > actual) else 0.0),
            "costo": costo,
            "envio_prom": e,
            "comision_pct": p,
            "margen_hoy": m_hoy,
            "margen_hoy_pct": m_hoy / actual if actual else None,
            "margen_al_minimo": m_min,
            "unidades": unidades.get(sku, 0),
            # Cruzar el escalon del cargo fijo puede ser justamente el motivo
            # de la suba: conviene que se vea.
            "cruza_escalon": (cargo_fijo(actual) != cargo_fijo(minimo)
                              if minimo else False),
            "perdida_periodo": (m_hoy * unidades.get(sku, 0)
                                if m_hoy < 0 else 0.0),
        })

    df = pd.DataFrame(filas)
    if not len(df):
        return df
    orden = {"hay que subir": 0, "no cierra a ningún precio": 1, "ok": 2}
    df["_o"] = df["diagnostico"].map(orden)
    return df.sort_values(["_o", "perdida_periodo"],
                          ascending=[True, True]).drop(columns=["_o"])


def resumen(df):
    if not len(df):
        return {}
    subir = df[df["diagnostico"] == "hay que subir"]
    return {
        "total": len(df),
        "ok": int((df["diagnostico"] == "ok").sum()),
        "a_subir": len(subir),
        "no_cierran": int((df["diagnostico"] == "no cierra a ningún precio").sum()),
        "perdiendo_hoy": int((df["margen_hoy"] < 0).sum()),
        "perdida_periodo": float(df["perdida_periodo"].sum()),
        "suba_mediana": float(subir["subir_pct"].median()) if len(subir) else 0.0,
        "cruzan_escalon": int(subir["cruza_escalon"].sum()) if len(subir) else 0,
    }


# Tope duro de suba, aunque el criterio pida mas. Con la estructura completa
# el modelo pide subas enormes en muchos SKU; esto obliga a que las mas
# violentas pasen por una decision explicita y no por un lote de 800.
TECHO_DE_SUBA = 1.00


def seleccionar(df, suba_maxima=0.30, unidades_minimas=1, marcas=None,
                items=None, solo_perdida=True):
    """
    Las publicaciones a las que subirles el precio.

    `solo_perdida` deja afuera las que hoy ganan plata pero no llegan al
    objetivo: son las menos urgentes y las que mas ruido meten en un lote.
    """
    if not len(df):
        return df

    sel = df[
        (df["diagnostico"] == "hay que subir")
        & (df["subir_pct"] > 0)
        & (df["subir_pct"] <= min(suba_maxima, TECHO_DE_SUBA))
        & (df["unidades"] >= unidades_minimas)
    ].copy()

    if solo_perdida:
        sel = sel[sel["margen_hoy"] < 0]
    if marcas:
        sel = sel[sel["marca"].isin(marcas)]
    if items is not None:
        sel = sel[sel["item_id"].isin(list(items))]

    return sel.sort_values("perdida_periodo")


def planilla_de_precios(seleccion):
    """
    Convierte la seleccion en la planilla que consume `actualizador.simular()`.

    Se reusa ese motor a proposito: ya tiene el resolver de SKU, el aviso de
    cambios mayores al 50% y la auditoria. No hace falta otra ruta de
    escritura.
    """
    return pd.DataFrame({
        "sku": seleccion["sku"],
        "precio": seleccion["precio_minimo"].round(2),
    })


def main():
    objetivo = (float(sys.argv[1]) / 100
                if len(sys.argv) > 1 else 0.15)
    ml = Meli(verbose=False)

    import rentabilidad as rent
    costos, cuando = rent.costos_guardados()
    if not len(costos):
        print("No hay planilla de costos guardada. Subila desde la app.")
        return 1
    print(f"Costos: {len(costos)} SKU (actualizada {cuando})")

    ordenes = rent.traer_historico(ml, 90)
    envios = rent.traer_costos_envio(ml, ordenes, muestra_por_sku=5)
    cargos = rent.cargos_por_sku(ordenes, envios)
    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    df = analizar(costos, cargos, pubs, objetivo=objetivo)
    r = resumen(df)
    pes = lambda v: "—" if v is None or pd.isna(v) else f"${v:,.0f}".replace(",", ".")

    print("\n" + "=" * 72)
    print(f"PRECIO MINIMO VIABLE  (margen objetivo {objetivo:.0%})")
    print("=" * 72)
    print(f"  SKU analizados            {r['total']:>6}")
    print(f"  Ya estan bien             {r['ok']:>6}")
    print(f"  Hay que subir             {r['a_subir']:>6}")
    print(f"  No cierran a ningun precio{r['no_cierran']:>6}")
    print(f"  Perdiendo plata hoy       {r['perdiendo_hoy']:>6}")
    print(f"  Plata perdida en el periodo  {pes(r['perdida_periodo']):>14}")
    print(f"  Suba mediana necesaria       {r['suba_mediana']:>13.0%}")
    print(f"  De esas, cruzan escalon   {r['cruzan_escalon']:>6}")

    subir = df[df["diagnostico"] == "hay que subir"]
    if len(subir):
        print(f"\n  Los 12 que mas plata pierden:")
        for _, f in subir.head(12).iterrows():
            print(f"    {f['sku']:<24} {pes(f['precio_actual'])} -> "
                  f"{pes(f['precio_minimo'])} ({f['subir_pct']:+.0%})"
                  f"{'  [cruza escalon]' if f['cruza_escalon'] else ''}")
            print(f"       {f['titulo']}")
            print(f"       margen hoy {pes(f['margen_hoy'])}/u · "
                  f"{int(f['unidades'])} u · perdio "
                  f"{pes(abs(f['perdida_periodo']))}")

    df.to_csv(DIR / "precio_minimo.csv", index=False)
    print(f"\nGuardado en precio_minimo.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
