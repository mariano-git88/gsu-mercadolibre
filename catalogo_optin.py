#!/usr/bin/env python3
"""
Anotar publicaciones en el catalogo de MercadoLibre (opt-in al Buy Box).

    python catalogo_optin.py              -> simula: que se haria
    python catalogo_optin.py --aplicar    -> lo hace
    python catalogo_optin.py --aplicar 1  -> lo hace en UNA sola (prueba)

**ESTO NO SE PUEDE DESHACER. Leer antes de correrlo.**

El opt-in **no es un interruptor** en la publicacion: `catalog_listing` no es
modificable (`PUT /items/{id}` devuelve `field_not_updatable`). El alta va por
`POST /items/catalog_listings` y lo que hace es **crear una publicacion
NUEVA**, ligada a la original:

  - las dos quedan activas y conviven;
  - se sincronizan solas en precio, stock, envio y garantia
    (`item_relations` con `stock_relation: 1`, o sea que el stock NO se
    duplica);
  - **el vendedor no puede deshacer el vinculo.** Lo maximo que se puede
    hacer despues es pausar o borrar la publicacion de catalogo.

Por eso conviene mirar `elegibilidad_catalogo.json` antes: los items que ya
tienen `item_relations` figuran como `ALREADY_OPTED_IN` y **no hay nada que
hacerles** — su mitad de catalogo ya existe y ya compite. Los unicos
accionables son los `READY_FOR_OPTIN`.

Registro: cada alta queda en la auditoria (`almacen.append_auditoria`) con el
id nuevo que devolvio ML, que es lo unico que despues permite encontrar y
pausar la publicacion de catalogo si hiciera falta.
"""

import json
import sys
import time
from pathlib import Path

import pandas as pd

import almacen
from catalogo import sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent
CACHE_ELEGIBILIDAD = DIR / "elegibilidad_catalogo.json"
SALIDA = DIR / "optin_catalogo.csv"

# Entre alta y alta. ML no documenta un limite para este endpoint, asi que se
# va despacio: son pocas y no hay apuro.
PAUSA_SEG = 1.5

# Reintentos propios para el 429, aparte de los del cliente: si ML esta
# apretado, el lote tiene que poder esperar sin abortar.
REINTENTOS_429 = 3


def traer_elegibilidad(ml, pubs, refrescar=False, callback=None):
    """
    item_id -> respuesta de /items/{id}/catalog_listing_eligibility.

    Se cachea en disco: es una llamada por publicacion y el estado no cambia
    de un rato para otro.
    """
    cache = {}
    if CACHE_ELEGIBILIDAD.exists() and not refrescar:
        cache = json.loads(CACHE_ELEGIBILIDAD.read_text(encoding="utf-8"))

    candidatas = [p for p in pubs
                  if p.get("status") == "active" and p.get("catalog_product_id")
                  and not p.get("catalog_listing")]
    faltan = [p for p in candidatas if p["id"] not in cache]
    for n, p in enumerate(faltan, 1):
        try:
            cache[p["id"]] = ml.get(
                f"/items/{p['id']}/catalog_listing_eligibility")
        except Exception as e:  # noqa: BLE001
            cache[p["id"]] = {"_error": str(e)[:120]}
        if callback and n % 20 == 0:
            callback(f"Elegibilidad {n}/{len(faltan)}...")
    if faltan:
        CACHE_ELEGIBILIDAD.write_text(
            json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


def simular(ml, pubs=None, refrescar=False, callback=None):
    """
    DataFrame con lo que se haria. **No escribe nada.**

    Una fila por publicacion candidata, con `accion`:
      - `anotar`            -> se puede y se va a hacer
      - `producto_inactivo` -> ML la da por elegible pero el producto de
                               catalogo esta de baja y el alta falla
      - `ya_anotada`        -> ALREADY_OPTED_IN, su par de catalogo ya existe
      - `no_elegible`       -> ML no la deja por otro motivo
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    eleg = traer_elegibilidad(ml, pubs, refrescar=refrescar, callback=callback)

    # `catalog_listing_eligibility` dice READY_FOR_OPTIN aunque el producto de
    # catalogo este dado de baja, y ahi el alta falla con
    # `catalog_product_id.not_active`. Hay que preguntarle al producto.
    estado_producto = {}
    listos = [p for p in pubs
              if (eleg.get(p["id"]) or {}).get("status") == "READY_FOR_OPTIN"]
    for p in listos:
        pid = p.get("catalog_product_id")
        if pid and pid not in estado_producto:
            try:
                estado_producto[pid] = ml.get(f"/products/{pid}").get("status")
            except Exception:  # noqa: BLE001
                estado_producto[pid] = "desconocido"

    filas = []
    for p in pubs:
        e = eleg.get(p["id"])
        if not e:
            continue
        estado = e.get("status")
        if estado == "READY_FOR_OPTIN" and e.get("buy_box_eligible"):
            est_prod = estado_producto.get(p.get("catalog_product_id"))
            if est_prod == "active":
                accion, detalle = "anotar", ""
            else:
                accion = "producto_inactivo"
                detalle = (f"el producto de catálogo está '{est_prod}': ML lo "
                           f"da por elegible pero el alta falla")
        elif estado == "ALREADY_OPTED_IN":
            accion = "ya_anotada"
            detalle = f"su par de catálogo ya existe ({e.get('reason')})"
        else:
            accion = "no_elegible"
            detalle = f"{estado} · {e.get('reason')}"
        filas.append({
            "item_id": p["id"],
            "sku": sku_del_atributo(p) or "",
            "titulo": (p.get("title") or "")[:60],
            "precio": float(p.get("price") or 0),
            "stock": p.get("available_quantity") or 0,
            "vendidas": p.get("sold_quantity") or 0,
            "catalog_product_id": p.get("catalog_product_id") or "",
            "accion": accion,
            "detalle": detalle,
        })
    df = pd.DataFrame(filas)
    if len(df):
        orden = {"anotar": 0, "producto_inactivo": 1, "no_elegible": 2,
                 "ya_anotada": 3}
        df = df.sort_values(
            ["accion", "vendidas"],
            key=lambda c: c.map(orden) if c.name == "accion" else -c)
    return df


def _anotar_una(ml, item_id, catalog_product_id):
    """
    (ok, id_nuevo_o_error). Reintenta el 429 con presupuesto propio.

    Nunca lanza: quien llama necesita seguir con el resto del lote aunque
    esta falle.
    """
    payload = {"item_id": item_id, "catalog_product_id": catalog_product_id}
    for intento in range(REINTENTOS_429 + 1):
        try:
            r = ml.post("/items/catalog_listings", payload)
            return True, (r or {}).get("id") or ""
        except MeliError as e:
            if "429" in str(e) and intento < REINTENTOS_429:
                time.sleep(5 * (intento + 1))
                continue
            return False, str(e)[:200]
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {str(e)[:180]}"
    return False, "sin reintentos"


def aplicar(ml, df, tope=None, operador="terminal", callback=None):
    """
    Ejecuta las filas con `accion == 'anotar'`. Devuelve el DataFrame con el
    resultado por fila.

    Es **idempotente en la practica**: una publicacion ya anotada deja de ser
    READY_FOR_OPTIN, asi que si se vuelve a correr `simular()` ya no aparece.
    Si se reintenta igual, ML rechaza el alta duplicada y queda como error de
    esa fila, sin romper el lote.
    """
    pendientes = df[df["accion"] == "anotar"].copy()
    if tope:
        pendientes = pendientes.head(tope)

    resultados, auditoria = [], []
    for n, (_, f) in enumerate(pendientes.iterrows(), 1):
        if callback:
            callback(f"Anotando {n}/{len(pendientes)}: {f['sku'] or f['item_id']}")
        ok, dato = _anotar_una(ml, f["item_id"], f["catalog_product_id"])
        if callback:
            callback(f"   -> {'OK ' + str(dato) if ok else 'ERROR ' + str(dato)[:120]}")
        resultados.append({**f.to_dict(),
                           "resultado": "ok" if ok else "error",
                           "item_catalogo": dato if ok else "",
                           "error": "" if ok else dato})
        # append_auditoria espera DICCIONARIOS con las claves de
        # COLUMNAS_AUDITORIA, no listas posicionales.
        auditoria.append({
            "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
            "item_id": f["item_id"],
            "campo": "catalog_listing",
            "valor_anterior": "false",
            "valor_nuevo": f"true (nuevo item {dato})" if ok else "true",
            "resultado": "ok" if ok else "error",
            "operador": operador,
            "nota": (f"opt-in a catálogo · producto {f['catalog_product_id']}"
                     + ("" if ok else f" · {dato}")),
        })
        if n < len(pendientes):
            time.sleep(PAUSA_SEG)

    if auditoria:
        okk, det = almacen.append_auditoria(auditoria)
        if not okk:
            print(f"[optin] AVISO: no se pudo escribir la auditoría — {det}")
    return pd.DataFrame(resultados)


def main():
    aplicar_de_verdad = "--aplicar" in sys.argv
    tope = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)

    ml = Meli(verbose=False)
    print("Consultando elegibilidad...")
    df = simular(ml, callback=lambda m: print(f"  {m}"))
    if not len(df):
        print("No hay publicaciones candidatas.")
        return 0

    print()
    for accion, g in df.groupby("accion"):
        print(f"  {accion:<12} {len(g)}")

    anotar = df[df["accion"] == "anotar"]
    if not len(anotar):
        print("\nNinguna se puede anotar.")
        return 0

    print(f"\nSe anotarían {len(anotar)}"
          + (f" (tope: {tope})" if tope else "") + ":")
    for _, f in anotar.iterrows():
        print(f"  {f['sku'] or f['item_id']:<18} ${f['precio']:>9,.0f}  "
              f"{f['vendidas']:>4} vendidas  {f['titulo'][:38]}")

    if not aplicar_de_verdad:
        print("\nEsto fue una SIMULACIÓN. Para hacerlo: "
              "python catalogo_optin.py --aplicar")
        df.to_csv(SALIDA, index=False)
        print(f"Detalle en {SALIDA.name}")
        return 0

    print("\n*** APLICANDO. Esto no se puede deshacer. ***\n")
    res = aplicar(ml, df, tope=tope, callback=lambda m: print(f"  {m}"))
    print()
    for _, f in res.iterrows():
        if f["resultado"] == "ok":
            print(f"  OK    {f['sku'] or f['item_id']:<18} -> {f['item_catalogo']}")
        else:
            print(f"  ERROR {f['sku'] or f['item_id']:<18} {f['error'][:90]}")
    res.to_csv(SALIDA, index=False)
    print(f"\n{(res['resultado']=='ok').sum()} de {len(res)} anotadas. "
          f"Detalle en {SALIDA.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
