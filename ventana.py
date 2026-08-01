#!/usr/bin/env python3
"""
Ventana de precio: entre lo que necesitas cobrar y lo que podes cobrar.

    python ventana.py            -> margen objetivo 15%
    python ventana.py 20

Las herramientas que ya existen contestan media pregunta cada una:

  - `precio_minimo.py` da el **piso**: abajo de ahi no llegas al margen.
  - `buybox.py` da el **techo util**: arriba de ahi perdes la pagina de
    catalogo, que es donde se lleva las ventas el que gana.
  - `tramos.py` avisa que el **cargo fijo salta en escalones**, asi que dentro
    de la ventana no todos los precios rinden igual.

Ninguna sola contesta lo unico que importa: **que precio le pongo**. Esto
junta las tres y devuelve un precio sugerido por SKU, con el motivo.

Los cuatro casos que salen, y por que piden cosas distintas:

  - **Ventana amplia** (piso <= precio para ganar): existe un precio que te
    deja el margen Y te da la pagina de catalogo. Es el mejor escenario y hay
    que ir a buscarlo.
  - **Sin ventana** (piso > precio para ganar): ganar el catalogo implica
    vender por debajo de tu piso. No es un problema de precio sino de costo o
    de a que competidor te estas midiendo. Ahi la decision es explicita:
    resignar el catalogo, o entrar a perdida a proposito.
  - **Ya ganas**: tenes la pagina. Lo unico que hay que mirar es si podes
    subir hasta el piso (o mas) sin perderla.
  - **Fuera de catalogo**: no hay pagina que ganar, manda el piso.

Sobre el escalon: cuando el precio sugerido queda cerca de un umbral, se
evalua correrlo hasta el borde. **En Uruguay el umbral de $1.000 conviene
esquivarlo, no cruzarlo**: ahi el cargo fijo cae de $40 a cero pero el envio
pasa a pagarlo el vendedor (~$160), asi que cruzarlo cuesta ~$120 netos. Ver
`tramos.py`, que tiene los dos escalones medidos.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

# Cuanto se acepta correr el precio hacia arriba con tal de cruzar un escalon
# de cargo fijo, si eso deja mas neto. Mismo criterio que tramos.py.
MARGEN_CRUCE = 0.08


def _neto_por_unidad(precio, pct, envio, costo, iva, otros):
    """Lo que queda por unidad a un precio dado, despues de todo."""
    from rentabilidad import otros_conceptos_monto
    from tramos import cargo_fijo

    ingreso = precio / (1 + iva)
    _, otros_monto = otros_conceptos_monto(ingreso, otros)
    return ingreso - otros_monto - precio * pct - cargo_fijo(precio) - envio - costo


def _mejor_con_escalon(precio, pct, envio, costo, iva, otros,
                       techo=None, margen_cruce=MARGEN_CRUCE):
    """
    Si correr el precio hasta el proximo umbral de cargo fijo deja mas neto,
    devuelve ese precio. Si no, el original.

    `techo` limita hasta donde se puede llegar (por ejemplo, el precio para
    ganar el Buy Box: no sirve cruzar el escalon si eso te saca de la pagina).
    """
    from tramos import TRAMOS

    mejor, neto_mejor = precio, _neto_por_unidad(precio, pct, envio, costo,
                                                 iva, otros)
    for tope, _ in TRAMOS:
        if tope == float("inf") or tope <= precio:
            continue
        if tope > precio * (1 + margen_cruce):
            break
        if techo is not None and tope > techo:
            break
        neto = _neto_por_unidad(tope, pct, envio, costo, iva, otros)
        if neto > neto_mejor:
            mejor, neto_mejor = float(tope), neto
    return mejor


def analizar(costos_df, cargos_df, pubs, iva=0.22, otros_conceptos=None,
             objetivo=0.15, ptw_por_item=None):
    """
    Devuelve una fila por SKU con la ventana y el precio sugerido.

    `ptw_por_item` es el cache de `buybox.traer_price_to_win()`: item_id ->
    respuesta. Si no viene, se trabaja solo con el piso.
    """
    from buybox import marca as marca_de
    from precio_minimo import precio_minimo
    from rentabilidad import OTROS_CONCEPTOS
    from resolver import indexar_por_sku, resolver_precio
    from tramos import PORCENTAJE, cargo_fijo

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

    ptw_por_item = ptw_por_item or {}
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

        p = pct.get(sku, PORCENTAJE)
        e = envio.get(sku, 0.0)

        piso = precio_minimo(costo, p, e, iva=iva, otros=otros,
                             objetivo=objetivo)

        # El Buy Box se pelea POR PUBLICACION, y la publicacion de catalogo
        # casi nunca es la que elige el resolver para el precio: de los 721
        # SKU con pagina de catalogo, el resolver apunta a esa misma en solo
        # 186. Asi que se busca la de catalogo aparte.
        candidatas = [x for x in (indice.get(sku) or [])
                      if x.get("catalog_listing")]
        pub_cat = next((x for x in candidatas if x["id"] in ptw_por_item),
                       candidatas[0] if candidatas else None)
        datos_bb = ptw_por_item.get(pub_cat["id"]) if pub_cat else {}
        datos_bb = datos_bb or {}
        estado_bb = datos_bb.get("status")
        ptw = datos_bb.get("price_to_win")
        gana_hoy = estado_bb in ("winning", "sharing_first_place")
        en_catalogo = pub_cat is not None

        # Y ojo: cambiar el precio del SKU toca las publicaciones que elige
        # el resolver. Si la de catalogo no esta entre ellas, el consejo de
        # Buy Box es informativo, **no se aplica solo** cambiando este precio.
        alcanzable = bool(pub_cat and any(d["id"] == pub_cat["id"]
                                          for d in res.destinos))

        # ---------------------------------------------------- recomendacion
        if piso is None:
            caso, sugerido, motivo = (
                "no cierra", None,
                "No hay precio que llegue al margen objetivo: el costo, la "
                "comisión y los otros conceptos se comen todo el ingreso.")
        elif not en_catalogo or ptw is None:
            caso = "fuera de catálogo"
            sugerido = _mejor_con_escalon(max(actual, piso), p, e, costo, iva,
                                          otros)
            motivo = ("No compite en una página de catálogo, así que manda el "
                      "piso: no hay pelea que ganar bajando.")
        elif not alcanzable:
            # El precio del SKU no llega a la publicacion de catalogo, asi que
            # el Buy Box no se resuelve desde aca.
            caso = "catálogo aparte"
            sugerido = _mejor_con_escalon(max(actual, piso), p, e, costo, iva,
                                          otros)
            motivo = ("La página de catálogo la pelea otra publicación de "
                      "este SKU, que este cambio de precio **no toca**. Acá "
                      "manda el piso; el Buy Box se resuelve desde la sección "
                      "Ganar la venta, publicación por publicación.")
        elif gana_hoy:
            caso = "ya ganás"
            # Se puede subir hasta el piso sin perder la pagina solo si el
            # piso no supera el precio para ganar.
            if piso <= ptw:
                sugerido = _mejor_con_escalon(max(actual, piso), p, e, costo,
                                              iva, otros, techo=ptw)
                motivo = ("Ya tenés la página y hay lugar para acomodar el "
                          "precio sin perderla.")
            else:
                sugerido = actual
                motivo = ("Ya tenés la página pero tu piso está por encima "
                          "del precio para ganarla: subir te la hace perder.")
        elif piso <= ptw and actual <= ptw:
            # Se puede acomodar el precio HACIA ARRIBA y ademas ganar la
            # pagina. Es el unico caso donde no hay que resignar nada.
            caso = "ventana amplia"
            sugerido = _mejor_con_escalon(max(actual, piso), p, e, costo, iva,
                                          otros, techo=ptw)
            motivo = ("Podés acomodar el precio **y** quedarte con la página "
                      "de catálogo. Es el único caso donde no se resigna "
                      "nada: no hay que elegir entre margen y volumen.")
        elif piso <= ptw:
            # Ganar la pagina exige BAJAR. Es viable para el margen, pero
            # cuesta neto por unidad: solo conviene si el volumen lo paga, y
            # eso la API no lo sabe.
            caso = "bajar para ganar"
            sugerido = _mejor_con_escalon(ptw, p, e, costo, iva, otros)
            motivo = ("Ganar la página exige bajar el precio. El margen lo "
                      "aguanta, pero **se resigna neto por unidad**: solo "
                      "conviene si el volumen extra lo compensa, y eso no "
                      "sale de ningún dato de la API.")
        else:
            caso = "sin ventana"
            sugerido = _mejor_con_escalon(piso, p, e, costo, iva, otros)
            motivo = ("Ganar la página exige vender por debajo de tu piso. "
                      "No es un problema de precio: o se resigna el catálogo, "
                      "o se entra a pérdida a propósito.")

        neto_actual = _neto_por_unidad(actual, p, e, costo, iva, otros)
        neto_sug = (_neto_por_unidad(sugerido, p, e, costo, iva, otros)
                    if sugerido else None)
        u = unidades.get(sku, 0)

        filas.append({
            "sku": sku,
            "item_id": pub["id"],
            "marca": marca_de(pub),
            "titulo": (pub.get("title") or "")[:60],
            "caso": caso,
            "precio_actual": actual,
            "piso": piso,
            "precio_para_ganar": ptw,
            "precio_sugerido": sugerido,
            "cambio_pct": ((sugerido - actual) / actual
                           if (sugerido and actual) else None),
            "neto_actual": neto_actual,
            "neto_sugerido": neto_sug,
            "gana_neto": (neto_sug - neto_actual) if neto_sug is not None else None,
            # Lo que cambiaria en el periodo si se vendiera lo mismo. Es una
            # referencia de tamaño, no una proyeccion: cambiar el precio
            # cambia el volumen.
            "impacto_periodo": ((neto_sug - neto_actual) * u
                                if neto_sug is not None else None),
            "unidades": u,
            "en_catalogo": en_catalogo,
            "buybox_alcanzable": alcanzable,
            "item_catalogo": pub_cat["id"] if pub_cat else "",
            "gana_buybox_hoy": gana_hoy,
            "cruza_escalon": (cargo_fijo(actual) != cargo_fijo(sugerido)
                              if sugerido else False),
            "motivo": motivo,
        })

    df = pd.DataFrame(filas)
    if not len(df):
        return df
    orden = {"ventana amplia": 0, "bajar para ganar": 1, "sin ventana": 2,
             "ya ganás": 3, "catálogo aparte": 4, "fuera de catálogo": 5,
             "no cierra": 6}
    df["_o"] = df["caso"].map(orden)
    return df.sort_values(["_o", "impacto_periodo"],
                          ascending=[True, False]).drop(columns=["_o"])


def resumen(df):
    if not len(df):
        return {}
    mejoran = df[df["gana_neto"].fillna(0) > 0]
    return {
        "total": len(df),
        "ventana_amplia": int((df["caso"] == "ventana amplia").sum()),
        "bajar_para_ganar": int((df["caso"] == "bajar para ganar").sum()),
        "sin_ventana": int((df["caso"] == "sin ventana").sum()),
        "ya_ganan": int((df["caso"] == "ya ganás").sum()),
        "catalogo_aparte": int((df["caso"] == "catálogo aparte").sum()),
        "fuera": int((df["caso"] == "fuera de catálogo").sum()),
        "no_cierran": int((df["caso"] == "no cierra").sum()),
        "mejoran": len(mejoran),
        "impacto": float(mejoran["impacto_periodo"].fillna(0).sum()),
        "cruzan_escalon": int(df["cruza_escalon"].sum()),
    }


def seleccionar(df, casos=None, cambio_maximo=0.30, unidades_minimas=1,
                marcas=None, items=None, solo_mejoran=True):
    """Las publicaciones a las que aplicarles el precio sugerido."""
    if not len(df):
        return df
    sel = df[df["precio_sugerido"].notna()].copy()
    # Mismo cuidado que en plata.juntar(): si la columna quedo `object` porque
    # ninguna fila tenia precio, `.abs()` tira TypeError aunque el filtro de
    # al lado ya la hubiera vaciado. El `&` de pandas no corta.
    sel["cambio_pct"] = pd.to_numeric(sel["cambio_pct"], errors="coerce")
    sel = sel[sel["cambio_pct"].notna() & (sel["cambio_pct"].abs() > 0.001)]
    sel = sel[sel["cambio_pct"].abs() <= cambio_maximo]
    sel = sel[sel["unidades"] >= unidades_minimas]
    if solo_mejoran:
        sel = sel[sel["gana_neto"].fillna(0) > 0]
    if casos:
        sel = sel[sel["caso"].isin(casos)]
    if marcas:
        sel = sel[sel["marca"].isin(marcas)]
    if items is not None:
        sel = sel[sel["item_id"].isin(list(items))]
    return sel.sort_values("impacto_periodo", ascending=False)


def planilla_de_precios(seleccion):
    """La planilla que consume `actualizador.simular()`."""
    return pd.DataFrame({
        "sku": seleccion["sku"],
        "precio": seleccion["precio_sugerido"].round(2),
    })


def main():
    objetivo = float(sys.argv[1]) / 100 if len(sys.argv) > 1 else 0.15
    ml = Meli(verbose=False)

    import buybox
    import rentabilidad as rent

    costos, cuando = rent.costos_guardados()
    if not len(costos):
        print("No hay planilla de costos guardada.")
        return 1
    print(f"Costos: {len(costos)} SKU (actualizada {cuando})")

    ordenes = rent.traer_historico(ml, 90)
    envios = rent.traer_costos_envio(ml, ordenes, muestra_por_sku=5)
    cargos = rent.cargos_por_sku(ordenes, envios)
    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    # Se reusa el cache del Buy Box: si esta fresco no cuesta nada.
    cat = [p["id"] for p in pubs
           if p.get("status") == "active" and p.get("catalog_listing")]
    print(f"Buy Box: {len(cat)} publicaciones de catálogo...")
    ptw = buybox.traer_price_to_win(
        ml, cat, callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 60)

    df = analizar(costos, cargos, pubs, objetivo=objetivo, ptw_por_item=ptw)
    r = resumen(df)
    pes = lambda v: "—" if v is None or pd.isna(v) else f"${v:,.0f}".replace(",", ".")

    print("=" * 72)
    print(f"VENTANA DE PRECIO  (margen objetivo {objetivo:.0%})")
    print("=" * 72)
    print(f"  SKU analizados          {r['total']:>6}")
    print(f"  Ventana amplia          {r['ventana_amplia']:>6}  <- margen Y catálogo")
    print(f"  Bajar para ganar        {r['bajar_para_ganar']:>6}  <- decision, no automatico")
    print(f"  Sin ventana             {r['sin_ventana']:>6}")
    print(f"  Ya ganan la página      {r['ya_ganan']:>6}")
    print(f"  Catálogo en otra pub.   {r['catalogo_aparte']:>6}")
    print(f"  Fuera de catálogo       {r['fuera']:>6}")
    print(f"  No cierran a ningún precio {r['no_cierran']:>3}")
    print(f"  Mejoran con el sugerido {r['mejoran']:>6}")
    print(f"  Impacto en el período   {pes(r['impacto']):>14}")
    print(f"  Cruzan escalón          {r['cruzan_escalon']:>6}")

    amplia = df[df["caso"] == "ventana amplia"]
    if len(amplia):
        print(f"\n  VENTANA AMPLIA — los 10 de mayor impacto:")
        for _, f in amplia.head(10).iterrows():
            print(f"    {f['sku']:<24} {pes(f['precio_actual'])} -> "
                  f"{pes(f['precio_sugerido'])} ({f['cambio_pct']:+.0%})"
                  f"{'  [cruza escalón]' if f['cruza_escalon'] else ''}")
            print(f"       {f['titulo']}")
            print(f"       piso {pes(f['piso'])} · para ganar "
                  f"{pes(f['precio_para_ganar'])} · neto/u "
                  f"{pes(f['neto_actual'])} -> {pes(f['neto_sugerido'])} · "
                  f"{int(f['unidades'])} u")

    df.to_csv(DIR / "ventana.csv", index=False)
    print(f"\nGuardado en ventana.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
