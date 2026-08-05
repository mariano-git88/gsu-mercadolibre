#!/usr/bin/env python3
"""
Las reglas de precio que no se pueden romper.

    python test_precios.py

Correr esto despues de tocar `tramos.py`, `ventana.py`, `precio_minimo.py`,
`buybox.py`, `plata.py` o `lista_gsu.py`.

**Por que existe este archivo.** El 31/07/2026 se corrigio el bug del envio
—tratarlo como constante por SKU en vez de como escalon del precio— y las
notas del porteo lo dieron por aplicado en cuatro modulos. Estaba aplicado en
uno. Los otros tres siguieron cuatro dias calculando mal, sin que nada
fallara: los numeros seguian saliendo, solo que equivocados. Un test que mire
resultados no lo hubiera visto tampoco, porque el resultado *parece* bien.

Por eso hay dos clases de chequeo aca:

  - los de **comportamiento**, que corren la funcion y miran el numero;
  - los de **estructura**, que leen el fuente con `inspect` y verifican que el
    envio pase por `envio_a_cargo()`. Si alguien "simplifica" y vuelve a
    restar el promedio del SKU, la cuenta va a dar un numero plausible y nadie
    se va a enterar. Este es el unico chequeo que lo agarra.
"""

import inspect
import sys

fallas = []


def chequear(condicion, titulo, detalle=""):
    print(f"  {'OK ' if condicion else 'MAL'} {titulo}")
    if not condicion:
        fallas.append(titulo)
        if detalle:
            print(f"       {detalle}")
    return condicion


def test_envio_es_escalon():
    from tramos import ENVIO_VENDEDOR, UMBRAL_ENVIO_GRATIS, envio_a_cargo

    print("\nEl envio es un escalon del precio, no una constante por SKU")
    chequear(envio_a_cargo(UMBRAL_ENVIO_GRATIS - 1) == 0.0,
             "debajo del umbral lo paga el comprador")
    chequear(envio_a_cargo(UMBRAL_ENVIO_GRATIS) == ENVIO_VENDEDOR,
             "en el umbral ya lo paga el vendedor")
    # El dato propio del SKU le gana a la mediana, pero SOLO arriba del umbral.
    chequear(envio_a_cargo(UMBRAL_ENVIO_GRATIS + 500, 250.0) == 250.0,
             "arriba del umbral usa el envio medido del SKU")
    chequear(envio_a_cargo(UMBRAL_ENVIO_GRATIS - 500, 250.0) == 0.0,
             "debajo del umbral ignora el promedio historico",
             "un SKU que vendio caro alguna vez no arrastra ese envio "
             "cuando se lo evalua barato")


def test_el_envio_pasa_por_la_funcion():
    """
    Chequeo de ESTRUCTURA: que ningun modulo reste el envio a mano.

    Es el que hubiera atrapado la regresion de julio. Se lee el fuente porque
    el resultado numerico de restar el promedio del SKU es perfectamente
    plausible: no hay numero que delate el error.
    """
    import buybox
    import precio_minimo as pm
    import ventana as vt

    print("\nEl envio pasa por envio_a_cargo() en todas las pantallas")
    casos = [
        (vt._neto_por_unidad, "ventana._neto_por_unidad"),
        (pm.precio_minimo, "precio_minimo.precio_minimo"),
        (buybox.con_costos, "buybox.con_costos"),
        (buybox.analizar, "buybox.analizar"),
    ]
    for fn, nombre in casos:
        chequear(_llama_a(fn, "envio_a_cargo"),
                 f"{nombre} llama a envio_a_cargo()",
                 "resta el envio directo: vuelve a tratarlo como constante")


def _llama_a(fn, nombre_funcion):
    """
    True si `fn` **llama** a `nombre_funcion`, no si apenas la nombra.

    Se mira el arbol sintactico y no el texto porque las dos formas de
    "nombrarla sin usarla" son justo las que aparecieron en este repo: tenerla
    importada y no llamarla, y mencionarla en el docstring. Las dos hacian
    pasar un chequeo de texto sobre codigo roto.
    """
    import ast
    import textwrap

    arbol = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return any(isinstance(n, ast.Call)
               and getattr(n.func, "id", getattr(n.func, "attr", None))
               == nombre_funcion
               for n in ast.walk(arbol))


def test_no_cruzar_a_un_escalon_mas_caro():
    import ventana as vt

    print("\nCruzar un escalon solo si abarata lo que cobra ML")
    # 700 -> 750 sube el cargo fijo de $25 a $40: quedarse deja mas.
    r = vt._mejor_con_escalon(700.0, 0.13, 0.0, 200.0, 0.22, None)
    chequear(r == 700.0, "no cruza a un tramo con cargo fijo mas caro",
             f"devolvio {r}, deberia quedarse en 700")

    # 960 -> 1000 hace aparecer el envio a cargo del vendedor.
    r = vt._mejor_con_escalon(960.0, 0.13, 0.0, 200.0, 0.22, None)
    chequear(r == 960.0, "no empuja por encima del umbral de envio",
             f"devolvio {r}, deberia quedarse en 960")


def test_bajar_para_esquivar_el_envio():
    import ventana as vt

    print("\nLa jugada uruguaya: bajar a 999 y sacarse el envio de encima")
    r = vt._mejor_con_escalon(1050.0, 0.13, 160.0, 400.0, 0.22, None)
    chequear(r == 999.0, "baja al borde cuando eso saca el envio",
             f"devolvio {r}, deberia bajar a 999")


def test_piso_de_marca():
    import pandas as pd

    import buybox
    import plata
    import tramos

    print("\nEl piso de marca no lo abre ninguna pantalla")

    df = pd.DataFrame([
        {"item_id": "A", "sku": "X", "marca": "Bulit", "titulo": "t",
         "diagnostico": "perdés por precio", "precio_actual": 1000.0,
         "precio_para_ganar": 500.0, "piso_marca": 800.0, "bajar_pct": 0.5,
         "unidades": 10, "costo": 100.0, "margen_al_ganar_pct": 0.3,
         "margen_al_ganar": 50.0, "cruza_escalon": False, "veredicto": "ok"},
        {"item_id": "B", "sku": "Y", "marca": "Bulit", "titulo": "t",
         "diagnostico": "perdés por precio", "precio_actual": 1000.0,
         "precio_para_ganar": 900.0, "piso_marca": 800.0, "bajar_pct": 0.10,
         "unidades": 10, "costo": 100.0, "margen_al_ganar_pct": 0.3,
         "margen_al_ganar": 50.0, "cruza_escalon": False, "veredicto": "ok"},
    ])
    sel = buybox.seleccionar(df, margen_minimo=0.0, baja_maxima=0.6,
                             unidades_minimas=0)
    chequear(list(sel["item_id"]) == ["B"],
             "buybox.seleccionar deja afuera la que perfora",
             f"selecciono {list(sel['item_id'])}")

    # aplicar() tiene su propio candado: la seleccion puede venir armada a mano.
    class MLQueNoDeberiaEscribir:
        def actualizar_publicacion(self, *a, **k):
            raise AssertionError("no tendria que haber escrito")

    res = buybox.aplicar(MLQueNoDeberiaEscribir(), df.iloc[[0]], operador="test")
    chequear(len(res) == 1 and res.iloc[0]["resultado"] == "ERROR",
             "buybox.aplicar rechaza la que perfora, sin escribir",
             f"resultado: {res.to_dict('records') if len(res) else 'vacio'}")

    dfp = pd.DataFrame([
        {"sku": "A", "accion": "correr_escalon", "ejecutable": True,
         "cambio_pct": -0.05, "precio_sugerido": 999.0, "plata_mes": 100.0,
         "conflicto": False},
        {"sku": "B", "accion": "correr_escalon", "ejecutable": True,
         "cambio_pct": -0.05, "precio_sugerido": 999.0, "plata_mes": 50.0,
         "conflicto": False},
    ])
    e = plata.ejecutables(dfp, pisos_sku={"A": 1100.0})
    chequear(list(e["sku"]) == ["B"],
             "plata.ejecutables deja afuera la que perfora",
             f"selecciono {list(e['sku'])}")

    class MLFalso:
        def items_detalle(self, ids, atributos=None):
            return [{"id": "A", "price": 1050.0, "status": "active",
                     "shipping": {"free_shipping": True}}]

    sel_t = pd.DataFrame([{"item_id": "A", "sku": "X", "titulo": "t",
                           "vendidos": 5, "precio_actual": 1050.0}])
    p = tramos.plan(MLFalso(), sel_t, pisos={"A": 1100.0})
    chequear(len(p) == 1 and p.iloc[0]["accion"] == "omitir",
             "tramos.plan omite la que quedaria bajo el piso",
             f"accion: {p.iloc[0]['accion'] if len(p) else 'vacio'}")


def test_topes_duros():
    import buybox
    import plata
    import tramos

    print("\nLos topes duros existen y son los esperados")
    chequear(0 < tramos.TECHO_DE_CAMBIO <= 0.20,
             f"tramos.TECHO_DE_CAMBIO = {tramos.TECHO_DE_CAMBIO:.0%}")
    chequear(0 < plata.TECHO_DE_CAMBIO <= 0.50,
             f"plata.TECHO_DE_CAMBIO = {plata.TECHO_DE_CAMBIO:.0%}")
    chequear(0 < buybox.TECHO_DE_BAJA <= 0.50,
             f"buybox.TECHO_DE_BAJA = {buybox.TECHO_DE_BAJA:.0%}")
    chequear(buybox.PISO_DE_MARGEN < 0,
             "buybox.PISO_DE_MARGEN admite perdida acotada a proposito")


def test_marcas_propias():
    import lista_gsu

    print("\nLa regla de marca aplica a quien tiene que aplicar")
    chequear(lista_gsu.MULTIPLICADOR == 1.85,
             f"el multiplicador es {lista_gsu.MULTIPLICADOR}")
    for m in ("Suprabond", "Bulit", "Somerset", "SUPRABOND SOMERSET"):
        chequear(lista_gsu.es_marca_propia(
            {"attributes": [{"id": "BRAND", "value_name": m}]}),
            f"'{m}' es marca propia")
    for m in ("Bosch", "Aqualaf", "Dremel"):
        chequear(not lista_gsu.es_marca_propia(
            {"attributes": [{"id": "BRAND", "value_name": m}]}),
            f"'{m}' NO tiene piso de marca")
    chequear(lista_gsu.piso_de("X", "Bosch", {"X": 100.0}) is None,
             "una marca de reventa no recibe piso aunque tenga precio")
    chequear(lista_gsu.piso_de("X", "Bulit", {"X": 100.0}) == 185.0,
             "una marca propia recibe costo x 1,85")


def main():
    print("=" * 66)
    print("REGLAS DE PRECIO QUE NO SE PUEDEN ROMPER")
    print("=" * 66)

    test_envio_es_escalon()
    test_el_envio_pasa_por_la_funcion()
    test_no_cruzar_a_un_escalon_mas_caro()
    test_bajar_para_esquivar_el_envio()
    test_piso_de_marca()
    test_topes_duros()
    test_marcas_propias()

    print("\n" + "=" * 66)
    if fallas:
        print(f"{len(fallas)} REGLAS ROTAS:")
        for f in fallas:
            print(f"  - {f}")
        return 1
    print("Todo en orden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
