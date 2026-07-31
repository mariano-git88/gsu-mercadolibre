#!/usr/bin/env python3
"""
Corrida diaria del monitor de competencia (la usa GitHub Actions).

Va en un archivo aparte y no inline en el YAML: un script de Python con
comillas y dos puntos adentro de un `run:` rompe el parseo del workflow.
"""
import sys

from meli import Meli
import competencia as comp


# Cuantos de los mas vendidos se vigilan si no hay una lista cargada a mano.
TOP_POR_DEFECTO = 50


def main():
    ml = Meli(verbose=False)

    # Si nadie cargo EAN a mano, se vigilan los mas vendidos del ultimo mes:
    # asi el monitor sirve desde el primer dia sin configurar nada.
    if not comp.eans_vigilados():
        eans, detalle, _ = comp.eans_mas_vendidos(ml, n=TOP_POR_DEFECTO)
        if not eans:
            print("No hay EAN para vigilar y ninguno de los más vendidos "
                  "tiene código de barras cargado.")
            return 0
        comp.cargar_vigilados(eans)
        print(f"Primera corrida: {detalle}")

    r = comp.monitorear(ml)
    if r.get("error"):
        print(r["error"])
        return 0
    print(f"vigilados: {r['vigilados']} | alertas nuevas: {len(r['alertas'])}")
    for a in r["alertas"]:
        print(f"  [{a['tipo']}] {a['producto'][:50]} — {a['detalle']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
