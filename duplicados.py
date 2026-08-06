#!/usr/bin/env python3
"""
Publicaciones duplicadas: dejar la que mejor performa y borrar la otra.

    python duplicados.py          -> analiza y muestra que borraria
    python duplicados.py --borrar -> BORRA de verdad (pide confirmacion)

**En Uruguay, hoy, NO HAY duplicados para borrar.** Medido sobre el catalogo
del 06/08/2026: los SKU con mas de una publicacion activa forman **143 grupos
(288 publicaciones), y los 143 incluyen una publicacion de catalogo**. Esas no
se tocan: ML las crea aparte, con stock compartido y de forma irreversible, y
suelen ser las que ganan el Buy Box. Grupos "limpios" —mismo tipo, sin
catalogo y sin Full—: **cero**.

O sea que esta pantalla hoy no tiene trabajo. Se porta igual porque el
catalogo cambia y el dia que aparezca un duplicado de verdad, la herramienta
ya esta y no hay que acordarse de nada.

Para referencia de como se ve una cuenta donde si hay trabajo: en Argentina,
de 997 SKU activos, 720 tenian mas de una publicacion, pero solo **33 grupos**
eran duplicados de verdad (75 publicaciones, sobraban 42). Aplicar el criterio
sobre los 720 grupos hubiera destruido el catalogo. La leccion vale para las
dos cuentas: **el filtro por clase de grupo es lo que hace util esta pantalla**,
no el conteo de SKU repetidos.

**Borrar en MercadoLibre no tiene vuelta atras.** Antes de borrar, cada
publicacion se guarda entera (el JSON como lo devuelve la API) en la hoja
`duplicados_borrados`. Eso no devuelve el ID, ni la antiguedad, ni las
preguntas, ni las ventas historicas — pero permite volver a publicar el mismo
producto sin reescribirlo de cero.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

import almacen
from catalogo import sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

HOJA_BORRADOS = "duplicados_borrados"
COLUMNAS_BORRADOS = ["fecha", "item_id", "sku", "titulo", "precio", "stock",
                     "permalink", "motivo", "operador", "json"]

# Si la segunda esta dentro de este margen de la primera, **se quedan las
# dos**. Dos publicaciones que venden parecido no son una buena y una mala:
# son dos que funcionan, y borrar una es resignar la mitad de esa venta.
EMPATE = 0.25          # 25%

# Debajo de esto no hay con que decidir: 3 unidades contra 2 no dice nada.
UNIDADES_MINIMAS = 5


def _clasificar(ps):
    """
    Que clase de grupo es. Solo 'limpio' se puede tocar.
    """
    tipos = {p.get("listing_type_id") for p in ps}
    if any(p.get("catalog_listing") for p in ps):
        return "catálogo", ("incluye una publicación de catálogo: ML la creó "
                            "aparte y es la que gana el Buy Box")
    if len(tipos) > 1:
        return "Premium/Clásica", ("mezcla Premium y Clásica: es una decisión "
                                   "de precio, no un duplicado")
    if any((p.get("shipping") or {}).get("logistic_type") == "fulfillment"
           for p in ps):
        return "Full", "alguna está en Full: el stock lo maneja ML"
    return "limpio", ""


def _rendimiento(conv):
    """item_id -> metricas de los ultimos 30 dias, desde conversion.csv."""
    if conv is None:
        return {}
    return {r["item_id"]: {
        "visitas": float(r.get("visitas") or 0),
        "unidades": float(r.get("unidades") or 0),
        "importe": float(r.get("importe") or 0),
        "conversion": float(r.get("conversion") or 0),
    } for _, r in conv.iterrows()}


def _puntaje(m):
    """
    Con que se compara una publicacion contra su duplicada.

    Manda el **importe vendido**, no las unidades ni las visitas: es lo unico
    que mezcla cuanto vendio y a que precio. Una publicacion con muchas visitas
    y pocas ventas no es la que hay que dejar viva.
    """
    return m.get("importe", 0.0)


def analizar(pubs=None, conv=None):
    """
    Devuelve una fila por publicacion de cada grupo duplicado, con la decision
    ('dejar' / 'borrar' / 'dejar - empate') y por que.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    if conv is None:
        ruta = DIR / "conversion.csv"
        conv = pd.read_csv(ruta) if ruta.exists() else None

    rend = _rendimiento(conv)
    activas = [p for p in pubs if p.get("status") == "active"]

    grupos = defaultdict(list)
    for p in activas:
        sku = (sku_del_atributo(p) or p.get("seller_custom_field") or "").strip()
        if sku:
            grupos[sku].append(p)

    filas = []
    for sku, ps in grupos.items():
        if len(ps) < 2:
            continue
        clase, porque = _clasificar(ps)

        marcadas = []
        for p in ps:
            m = rend.get(p["id"], {})
            marcadas.append({
                "sku": sku, "item_id": p["id"],
                "titulo": (p.get("title") or "")[:60],
                "clase": clase,
                "tipo": p.get("listing_type_id"),
                "precio": p.get("price"),
                "stock": p.get("available_quantity"),
                "vendidas_historico": p.get("sold_quantity") or 0,
                "visitas_30d": m.get("visitas", 0),
                "unidades_30d": m.get("unidades", 0),
                "importe_30d": m.get("importe", 0),
                "permalink": p.get("permalink", ""),
                "puntaje": _puntaje(m),
            })

        # Si NINGUNA facturo en el periodo, el puntaje no desempata: quedarian
        # ordenadas al azar y "la mejor" seria la primera que vino. Ahi manda
        # la historia — lo que vendio en su vida y las visitas — que es lo
        # unico que distingue una publicacion viva de una abandonada.
        nadie_vendio = max(f["puntaje"] for f in marcadas) <= 0
        if nadie_vendio:
            marcadas.sort(key=lambda f: (f["vendidas_historico"],
                                         f["visitas_30d"]), reverse=True)
        else:
            marcadas.sort(key=lambda f: f["puntaje"], reverse=True)
        mejor = marcadas[0]

        for i, f in enumerate(marcadas):
            if clase != "limpio":
                f["decision"], f["motivo"] = "dejar", porque
                continue
            if i == 0:
                f["decision"] = "dejar"
                f["motivo"] = (
                    f"ninguna del grupo vendió en 30 días; se deja ésta, que "
                    f"es la de más historia ({int(f['vendidas_historico'])} "
                    f"vendidas, {int(f['visitas_30d'])} visitas)"
                    if nadie_vendio else
                    f"la que más vendió del grupo "
                    f"(${f['importe_30d']:,.0f} en 30 días)".replace(",", "."))
                continue

            # **Duplicados que no vendieron nada: sobra igual.** Dos
            # publicaciones del mismo producto facturando cero no son un caso
            # sin datos: son una duplicada de mas, compitiendo entre si por
            # las mismas visitas. Se deja una.
            if nadie_vendio:
                f["decision"] = "borrar"
                f["motivo"] = (f"ninguna del grupo vendió en 30 días y sobra: "
                               f"tiene {int(f['vendidas_historico'])} vendidas "
                               f"históricas contra "
                               f"{int(mejor['vendidas_historico'])} de la que "
                               "se deja")
                continue

            total = sum(x["unidades_30d"] for x in marcadas)
            if total < UNIDADES_MINIMAS:
                f["decision"] = "dejar"
                f["motivo"] = (f"el grupo vendió {total:.0f} unidades en 30 "
                               "días: no alcanza para decidir")
                continue

            relacion = f["puntaje"] / mejor["puntaje"]
            if relacion >= 1 - EMPATE:
                f["decision"] = "dejar - empate"
                f["motivo"] = (f"vende el {relacion:.0%} de la mejor: las dos "
                               "funcionan")
                continue

            f["decision"] = "borrar"
            f["motivo"] = (f"vende el {relacion:.0%} de la mejor "
                           f"(${f['importe_30d']:,.0f} contra "
                           f"${mejor['importe_30d']:,.0f})".replace(",", "."))

        filas.extend(marcadas)

    df = pd.DataFrame(filas)
    if len(df):
        df = df.sort_values(["clase", "sku", "puntaje"],
                            ascending=[True, True, False])
    return df.reset_index(drop=True)


# -------------------------------------------------- duplicados de catalogo
#
# `analizar()` agrupa **por SKU** y saltea todo grupo que toque catalogo. Eso
# deja afuera un problema que el SKU no ve: **varias publicaciones nuestras
# compitiendo en la MISMA ficha de catalogo**.
#
# Ahi ML modera una por duplicada, la tradicional queda pausada esperando
# volver a competir, y el ciclo se repite. El SKU no lo detecta porque los
# titulos son distintos y porque a veces son SKU distintos.
#
# Medido el 2026-08-06 sobre el catalogo activo:
#   - 262 productos de catalogo con 2+ publicaciones nuestras EN catalogo
#     (987 publicaciones) -> riesgo de moderacion
#   - 377 con una de catalogo + tradicionales -> normal, no se toca
#   - 16 con **SKU distintos** apuntando al mismo producto, que no es un
#     duplicado sino una asociacion mal hecha: un destornillador plano y uno
#     Phillips colgados de la misma ficha.


def por_catalogo(pubs=None, conv=None):
    """
    Publicaciones que comparten producto de catalogo.

    **Es un informe, no una accion.** Salir de catalogo es irreversible y la
    que conviene dejar no siempre es la que mas vendio: a veces la tradicional
    tiene la antiguedad y las preguntas. Se marca el riesgo y se decide
    mirando.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    if conv is None:
        ruta = DIR / "conversion.csv"
        conv = pd.read_csv(ruta) if ruta.exists() else None
    rend = _rendimiento(conv)

    grupos = defaultdict(list)
    for p in pubs:
        if p.get("status") != "active":
            continue
        if p.get("catalog_product_id"):
            grupos[p["catalog_product_id"]].append(p)

    filas = []
    for cat, ps in grupos.items():
        if len(ps) < 2:
            continue
        en_catalogo = [p for p in ps if p.get("catalog_listing")]
        skus = {(sku_del_atributo(p) or p.get("seller_custom_field")
                 or "").strip() for p in ps}
        titulos = {(p.get("title") or "").strip().lower() for p in ps}

        if len(skus) > 1:
            clase = "SKU distintos"
            que = ("dos SKU nuestros cuelgan de la misma ficha: o son el "
                   "mismo producto con dos códigos, o uno está mal asociado")
        elif len(en_catalogo) > 1:
            clase = "varias en catálogo"
            que = (f"{len(en_catalogo)} publicaciones nuestras compiten en la "
                   "misma ficha: ML modera una por duplicada y la tradicional "
                   "queda esperando")
        else:
            clase = "normal"
            que = "una de catálogo y el resto tradicionales"

        for p in ps:
            m = rend.get(p["id"], {})
            filas.append({
                "catalog_product_id": cat,
                "clase": clase,
                "riesgo": que,
                "item_id": p["id"],
                "sku": (sku_del_atributo(p) or p.get("seller_custom_field")
                        or ""),
                "titulo": (p.get("title") or "")[:70],
                "en_catalogo": bool(p.get("catalog_listing")),
                "precio": p.get("price"),
                "unidades_30d": m.get("unidades", 0),
                "importe_30d": m.get("importe", 0),
                "vendidas_historico": p.get("sold_quantity") or 0,
                "titulos_distintos": len(titulos) > 1,
                "permalink": p.get("permalink", ""),
            })

    df = pd.DataFrame(filas)
    if len(df):
        df = df.sort_values(["clase", "catalog_product_id", "importe_30d"],
                            ascending=[True, True, False])
    return df.reset_index(drop=True)


# ---------------------------------------------------------------- escritura

def _respaldar(ml, item_id, fila, operador):
    """
    Guarda la publicacion entera antes de borrarla.

    No devuelve el ID ni la historia —eso no vuelve— pero deja con que
    republicar el producto sin rehacerlo de cero. Si el respaldo falla, **no
    se borra**: perder el dato y la publicacion a la vez no es una opcion.
    """
    try:
        completo = ml.get(f"/items/{item_id}")
    except Exception as e:
        return False, f"no pude leer la publicación para respaldarla: {e}"

    fila_hoja = {
        "fecha": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "item_id": item_id,
        "sku": fila.get("sku", ""),
        "titulo": fila.get("titulo", ""),
        "precio": fila.get("precio", ""),
        "stock": fila.get("stock", ""),
        "permalink": fila.get("permalink", ""),
        "motivo": fila.get("motivo", ""),
        "operador": operador,
        # Las celdas de Sheets topean en 50.000 caracteres.
        "json": json.dumps(completo, ensure_ascii=False)[:45000],
    }
    ok, detalle = almacen.append_hoja(HOJA_BORRADOS, COLUMNAS_BORRADOS,
                                      [fila_hoja])
    return ok, detalle


def borrar(ml, plan, operador="", callback=None, respaldar=True):
    """
    Cierra y borra las publicaciones marcadas.

    En MercadoLibre son **dos pasos**: primero `status: closed`, recien
    despues `deleted: true`. Una publicacion activa no se puede borrar
    directamente.

    **El primer paso ya es irreversible.** Medido el 2026-08-04 sobre
    MLA1139081620: una vez en `closed`, el `PUT {"status": "active"}`
    devuelve **200 y la deja igual** — probado cuatro veces, y tambien
    pasando por `paused` en el medio (el `sub_status` cambia, el `status` no).
    O sea que no hay "cerrar para probar": cerrar es la decision.

    Cada publicacion se respalda antes; si el respaldo falla, esa se saltea.
    """
    if plan is None or not len(plan):
        return pd.DataFrame()

    pendientes = plan[plan["decision"] == "borrar"]
    salida, total = [], len(pendientes)

    for i, (_, f) in enumerate(pendientes.iterrows(), start=1):
        if callback:
            callback(i, total, f)
        item = f["item_id"]
        base = {"item_id": item, "sku": f.get("sku", ""),
                "titulo": f.get("titulo", ""), "motivo": f.get("motivo", "")}

        if respaldar:
            ok, detalle = _respaldar(ml, item, f, operador)
            if not ok:
                salida.append({**base, "resultado": "OMITIDA",
                               "detalle": f"no se respaldó: {detalle}"[:200]})
                continue

        try:
            ok, det = ml.actualizar_publicacion(
                item, {"status": "closed"}, {"status": "active"},
                operador=operador, nota="duplicados - cierre previo al borrado")
            if not ok:
                salida.append({**base, "resultado": "ERROR",
                               "detalle": f"no se pudo cerrar: {det}"[:200]})
                continue

            ok2, det2 = ml.actualizar_publicacion(
                item, {"deleted": True}, {"deleted": False},
                operador=operador, nota=f"duplicados - {f.get('motivo','')}"[:200])
            if not ok2:
                salida.append({
                    **base, "resultado": "CERRADA",
                    "detalle": ("quedó CERRADA y no se pudo borrar. Cerrar no "
                                "se revierte: la publicación ya no vuelve a "
                                f"estar activa. {det2}")[:200]})
                continue

            salida.append({**base, "resultado": "BORRADA", "detalle": ""})

        except Exception as e:
            salida.append({**base, "resultado": "ERROR",
                           "detalle": f"{type(e).__name__}: {str(e)[:180]}"})

    return pd.DataFrame(salida)


def main():
    df = analizar()
    if not len(df):
        print("No hay SKU con más de una publicación activa.")
        return 0

    print("Grupos por clase (solo 'limpio' se puede tocar):")
    for clase, g in df.groupby("clase"):
        print(f"  {clase:<18} {g['sku'].nunique():>4} grupos, "
              f"{len(g):>4} publicaciones")

    borrar_df = df[df["decision"] == "borrar"]
    empates = df[df["decision"] == "dejar - empate"]
    print(f"\nA borrar: {len(borrar_df)}   |   empates que se dejan: "
          f"{len(empates)}")

    if len(borrar_df):
        print("\nLas que borraría:")
        for _, f in borrar_df.iterrows():
            print(f"  {f['sku']:<24} {f['item_id']}  {f['motivo']}")

    df.to_csv(DIR / "duplicados.csv", index=False)
    print(f"\nGuardado en duplicados.csv ({len(df)} filas)")

    if "--borrar" in sys.argv:
        if not len(borrar_df):
            return 0
        print(f"\n*** Vas a BORRAR {len(borrar_df)} publicaciones. "
              "No tiene vuelta atrás. ***")
        if input("Escribí BORRAR para confirmar: ").strip() != "BORRAR":
            print("Cancelado.")
            return 1
        ml = Meli(verbose=False)
        res = borrar(ml, df, operador="consola",
                     callback=lambda i, t, f: print(f"  {i}/{t} {f['item_id']}"))
        print(res.to_string(index=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
