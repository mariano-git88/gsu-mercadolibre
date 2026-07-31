#!/usr/bin/env python3
"""
Precios mayoristas (precio por cantidad) por reglas.

En vez de cargar los tramos publicacion por publicacion, se definen reglas
—por familia, por SKU o una general— y la herramienta agarra el precio
publicado de cada item y arma los tramos con el descuento que corresponda.

Como se identifica la familia de un producto, en orden de confianza:

  1. **SKU exacto**: para excepciones puntuales.
  2. **Codigo de familia**: los SKU de SUPRABOND traen el codigo embebido
     (CR016000000**CDB**AR40 -> familia CDB = candados). Sale de la tabla
     sku_familia_subgrupo.xlsx.
  3. **Categoria de MercadoLibre**: descriptiva y confiable donde el SKU
     no alcanza.
  4. **Titulo**: ultimo recurso, para marcas o lineas (ej: Somerset).
  5. **General**: todo lo que no matchee nada.

Gana la regla de menor `orden`, asi que lo especifico pisa a lo generico.

    python mayoristas.py            -> simula y muestra que haria
    python mayoristas.py --resumen  -> solo el conteo por regla
"""

import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd

import almacen
from catalogo import sku_del_atributo
from meli import MeliError

DIR = Path(__file__).resolve().parent
CACHE_CATEGORIAS = DIR / "categorias.json"
TABLA_FAMILIAS = DIR / "_assets" / "sku_familia_subgrupo.xlsx"

HOJA_REGLAS = "reglas_mayoristas"
COLS_REGLAS = ["orden", "nombre", "criterio", "patron", "q1_unidades",
               "q1_descuento", "q2_unidades", "q2_descuento", "activa"]

# ML permite hasta 5 tramos por publicacion; nosotros usamos 2.
MAX_TRAMOS = 5

# Contexto del precio mayorista: "exclusivo negocios" en el panel de ML.
# `channel_marketplace` es obligatorio: sin el, la API rechaza con
# "Marketplace context is mandatory".
CONTEXTO_B2B = ["channel_marketplace", "user_type_business"]


# Reglas que definio SUPRABOND. Se cargan una sola vez a la Sheet y despues se
# editan desde ahi (o desde la app) sin tocar el codigo.
REGLAS_INICIALES = [
    # Descuentos fuertes primero: son los mas especificos.
    dict(orden=10, nombre="Bolsas y toallas", criterio="titulo",
         patron="bolsa|toalla", q1_unidades=3, q1_descuento=20,
         q2_unidades=6, q2_descuento=50, activa="si"),
    dict(orden=20, nombre="Griferia", criterio="categoria",
         patron="griferia|ducha|canilla|griferias", q1_unidades=2,
         q1_descuento=20, q2_unidades=5, q2_descuento=40, activa="si"),
    dict(orden=21, nombre="Losa sanitaria", criterio="categoria",
         patron="inodoro|bacha|lavatorio|bidet|sanitario|loza",
         q1_unidades=2, q1_descuento=20, q2_unidades=5, q2_descuento=40,
         activa="si"),
    dict(orden=22, nombre="Bombas", criterio="categoria", patron="bomba",
         q1_unidades=2, q1_descuento=20, q2_unidades=5, q2_descuento=40,
         activa="si"),

    # Adhesivos y Somerset: tramos mas largos.
    dict(orden=30, nombre="Somerset", criterio="titulo", patron="somerset",
         q1_unidades=12, q1_descuento=10, q2_unidades=18, q2_descuento=15,
         activa="si"),
    dict(orden=31, nombre="Adhesivos (familia)", criterio="familia",
         patron="SBD|SBDT|ARN|CHM|PEG", q1_unidades=12, q1_descuento=10,
         q2_unidades=18, q2_descuento=15, activa="si"),
    dict(orden=32, nombre="Adhesivos (categoria)", criterio="categoria",
         patron="pegamento|barras de silicona|encoladora", q1_unidades=12,
         q1_descuento=10, q2_unidades=18, q2_descuento=15, activa="si"),

    # Linternas e infladores.
    dict(orden=40, nombre="Linternas e infladores (familia)",
         criterio="familia", patron="LNA|INF", q1_unidades=3, q1_descuento=10,
         q2_unidades=6, q2_descuento=15, activa="si"),
    dict(orden=41, nombre="Linternas e infladores (categoria)",
         criterio="categoria", patron="linterna|farol|inflador|compresor",
         q1_unidades=3, q1_descuento=10, q2_unidades=6, q2_descuento=15,
         activa="si"),

    # Selladores, candados y herramientas.
    dict(orden=50, nombre="Selladores (familia)", criterio="familia",
         patron="GFX|GMX|PBD|SLT", q1_unidades=6, q1_descuento=10,
         q2_unidades=12, q2_descuento=15, activa="si"),
    dict(orden=51, nombre="Selladores (categoria)", criterio="categoria",
         patron="sellador|silicona", q1_unidades=6, q1_descuento=10,
         q2_unidades=12, q2_descuento=15, activa="si"),
    dict(orden=52, nombre="Candados (familia)", criterio="familia",
         patron="CDB|CAD|CDBSEG", q1_unidades=6, q1_descuento=10,
         q2_unidades=12, q2_descuento=15, activa="si"),
    dict(orden=53, nombre="Candados (categoria)", criterio="categoria",
         patron="candado|traba|linga|cadena de seguridad", q1_unidades=6,
         q1_descuento=10, q2_unidades=12, q2_descuento=15, activa="si"),
    dict(orden=60, nombre="Herramientas", criterio="categoria",
         patron="herramienta|destornillador|llave|pinza|martillo|serrucho|"
                "espatula|cutter|engrapadora|disco",
         q1_unidades=6, q1_descuento=10, q2_unidades=12, q2_descuento=15,
         activa="si"),

    # Todo lo demas.
    dict(orden=99, nombre="RESTO", criterio="general", patron="",
         q1_unidades=3, q1_descuento=10, q2_unidades=6, q2_descuento=15,
         activa="si"),
]


# ------------------------------------------------------------------ apoyo

def _norm(s):
    """Minusculas y sin acentos, para comparar sin sorpresas."""
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def cargar_categorias():
    if CACHE_CATEGORIAS.exists():
        return json.loads(CACHE_CATEGORIAS.read_text(encoding="utf-8"))
    return {}


def cargar_codigos_familia():
    """Codigos de familia de la tabla, del mas largo al mas corto."""
    if not TABLA_FAMILIAS.exists():
        return []
    df = pd.read_excel(TABLA_FAMILIAS, dtype=str)
    col = next((c for c in df.columns if "familia" in c.lower()), None)
    if not col:
        return []
    codigos = {str(f).strip().upper() for f in df[col].dropna() if str(f).strip()}
    # Del mas largo al mas corto: si no, "CDB" le gana a "CDBSEG".
    return sorted(codigos, key=len, reverse=True)


def familia_del_sku(sku, codigos):
    """
    Saca el codigo de familia de un SKU de SUPRABOND.
    Formato tipico: CR + digitos + ceros + CODIGO + resto.
    """
    s = str(sku or "").strip().upper()
    m = re.match(r"^CR\d+?0*([A-Z].*)$", s)
    nucleo = m.group(1) if m else s
    return next((c for c in codigos if nucleo.startswith(c)), None)


# ------------------------------------------------------------------ reglas

def reglas():
    """Lee las reglas de la Sheet. Si no hay ninguna, carga las iniciales."""
    filas = almacen.leer_hoja(HOJA_REGLAS, COLS_REGLAS)
    if not filas:
        almacen.append_hoja(HOJA_REGLAS, COLS_REGLAS, REGLAS_INICIALES)
        filas = REGLAS_INICIALES
    salida = []
    for f in filas:
        if str(f.get("activa", "si")).strip().lower() not in ("si", "sí", "1", "true"):
            continue
        try:
            salida.append({
                "orden": int(float(f.get("orden") or 99)),
                "nombre": f.get("nombre", ""),
                "criterio": str(f.get("criterio", "general")).strip().lower(),
                "patron": str(f.get("patron", "")).strip(),
                "q1_unidades": int(float(f.get("q1_unidades") or 0)),
                "q1_descuento": float(f.get("q1_descuento") or 0),
                "q2_unidades": int(float(f.get("q2_unidades") or 0)),
                "q2_descuento": float(f.get("q2_descuento") or 0),
            })
        except (TypeError, ValueError):
            continue
    return sorted(salida, key=lambda r: r["orden"])


def regla_para(pub, regs, cats, codigos):
    """Devuelve la primera regla que aplica a la publicacion."""
    sku = (sku_del_atributo(pub) or "").strip().upper()
    cat = _norm(cats.get(pub.get("category_id"), ""))
    tit = _norm(pub.get("title"))
    fam = familia_del_sku(sku, codigos)

    for r in regs:
        crit, patron = r["criterio"], r["patron"]
        if crit == "general":
            return r
        if not patron:
            continue
        if crit == "sku":
            if sku in {p.strip().upper() for p in patron.split("|")}:
                return r
        elif crit == "familia":
            if fam and fam in {p.strip().upper() for p in patron.split("|")}:
                return r
        elif crit == "categoria":
            if any(_norm(p) in cat for p in patron.split("|") if p.strip()):
                return r
        elif crit == "titulo":
            if any(_norm(p) in tit for p in patron.split("|") if p.strip()):
                return r
    return None


def tramos(precio, regla):
    """
    Arma los tramos mayoristas a partir del precio publicado.
    Devuelve [(unidades_minimas, precio_unitario), ...].
    """
    salida = []
    for u, d in ((regla["q1_unidades"], regla["q1_descuento"]),
                 (regla["q2_unidades"], regla["q2_descuento"])):
        if u and u > 1 and d:
            salida.append((int(u), round(float(precio) * (1 - d / 100.0), 2)))

    # ML exige que a mayor cantidad, menor precio.
    salida.sort(key=lambda x: x[0])
    limpio = []
    for u, p in salida:
        if limpio and (u <= limpio[-1][0] or p >= limpio[-1][1]):
            continue
        limpio.append((u, p))
    return limpio[:MAX_TRAMOS]


# ------------------------------------------------------------------ simulacion

def simular(pubs, regs=None, cats=None, codigos=None, solo_activas=True):
    """Calcula que tramos quedarian en cada publicacion, sin tocar nada."""
    regs = regs if regs is not None else reglas()
    cats = cats if cats is not None else cargar_categorias()
    codigos = codigos if codigos is not None else cargar_codigos_familia()

    filas = []
    for p in pubs:
        if solo_activas and p.get("status") != "active":
            continue
        precio = p.get("price")
        if not precio:
            continue

        r = regla_para(p, regs, cats, codigos)
        if not r:
            filas.append({"item_id": p["id"], "sku": sku_del_atributo(p) or "",
                          "titulo": (p.get("title") or "")[:60], "regla": "",
                          "precio": precio, "q1_unidades": None, "q1_precio": None,
                          "q2_unidades": None, "q2_precio": None,
                          "accion": "sin_regla",
                          "motivo": "Ninguna regla aplica y no hay regla general."})
            continue

        t = tramos(precio, r)
        if not t:
            filas.append({"item_id": p["id"], "sku": sku_del_atributo(p) or "",
                          "titulo": (p.get("title") or "")[:60], "regla": r["nombre"],
                          "precio": precio, "q1_unidades": None, "q1_precio": None,
                          "q2_unidades": None, "q2_precio": None,
                          "accion": "sin_tramos",
                          "motivo": "La regla no define cantidades o descuentos validos."})
            continue

        fila = {"item_id": p["id"], "sku": sku_del_atributo(p) or "",
                "titulo": (p.get("title") or "")[:60], "regla": r["nombre"],
                "precio": precio, "accion": "aplicar", "motivo": ""}
        for i, (u, pr) in enumerate(t[:2], start=1):
            fila[f"q{i}_unidades"] = u
            fila[f"q{i}_precio"] = pr
        fila.setdefault("q1_unidades", None); fila.setdefault("q1_precio", None)
        fila.setdefault("q2_unidades", None); fila.setdefault("q2_precio", None)
        filas.append(fila)

    cols = ["item_id", "sku", "titulo", "regla", "precio", "q1_unidades",
            "q1_precio", "q2_unidades", "q2_precio", "accion", "motivo"]
    return pd.DataFrame(filas, columns=cols)


# ------------------------------------------------------------------ aplicacion

def leer_tramos(ml, item_id, cantidades=(2, 3, 4, 6, 12, 18)):
    """
    Devuelve los tramos mayoristas vigentes: [(unidades, precio), ...].

    OJO: `/items/{id}/prices` **NO lista los nodos de precio por cantidad**.
    La unica forma de verlos es preguntando el precio de venta para una
    cantidad, con el contexto de negocios:

        /items/{id}/sale_price?context=user_type_business&quantity=N

    y mirando `metadata.is_price_per_quantity`. Verificar con el endpoint de
    precios da un falso negativo: parece que no se guardo nada cuando en
    realidad si.
    """
    vistos, salida = {}, []
    for q in sorted(cantidades):
        try:
            r = ml.get(f"/items/{item_id}/sale_price",
                       context="user_type_business", quantity=q)
        except Exception:  # noqa: BLE001
            continue
        if not (r.get("metadata") or {}).get("is_price_per_quantity"):
            continue
        pid = r.get("price_id")
        if pid in vistos:      # el mismo tramo aplica a varias cantidades
            continue
        vistos[pid] = True
        salida.append((q, float(r["amount"])))
    return salida


def borrar_tramos(ml, item_id):
    """
    Quita los tramos mayoristas dejando solo el precio estandar.
    Funciona justamente porque la API borra los nodos que no se envian.
    """
    actuales = ml.get(f"/items/{item_id}/prices")
    base = [{"id": p["id"]} for p in actuales.get("prices", [])
            if p.get("type") == "standard"
            and not (p.get("conditions") or {}).get("min_purchase_unit")]
    if not base:
        return False, "No encontre el precio estandar."
    try:
        ml._request("POST", f"/items/{item_id}/prices/standard/quantity",
                    json_body={"prices": base})
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def aplicar_uno(ml, item_id, tramos_item, operador=""):
    """
    Carga los tramos mayoristas de UNA publicacion.

    OJO: la API borra los nodos de precio que no se envien. Por eso primero
    leemos los precios actuales y reenviamos el id del estandar: si no, se
    borraria el precio de venta de la publicacion.
    """
    # Esta lectura tambien puede fallar (rate limit, publicacion borrada). Si
    # se escapa, se lleva puesta la corrida entera y se pierden los
    # resultados de todo lo que ya se habia aplicado bien.
    try:
        actuales = ml.get(f"/items/{item_id}/prices")
    except Exception as e:  # noqa: BLE001
        return False, f"No pude leer los precios actuales: {str(e)[:200]}"

    ids_a_mantener = [
        {"id": p["id"]} for p in actuales.get("prices", [])
        if p.get("type") == "standard"
        and not (p.get("conditions") or {}).get("min_purchase_unit")
    ]
    if not ids_a_mantener:
        return False, ("No encontre el precio estandar de la publicacion. "
                       "No sigo: mandar los tramos sin el lo borraria.")

    moneda = next((p.get("currency_id") for p in actuales.get("prices", [])
                   if p.get("type") == "standard"), "UYU")

    nuevos = [{"amount": precio, "currency_id": moneda,
               "conditions": {"context_restrictions": CONTEXTO_B2B,
                              "min_purchase_unit": unidades}}
              for unidades, precio in tramos_item]

    payload = {"prices": ids_a_mantener + nuevos}
    try:
        resp = ml._request("POST", f"/items/{item_id}/prices/standard/quantity",
                           json_body=payload)
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:250]

    aplicados = [(p.get("conditions", {}).get("min_purchase_unit"), p.get("amount"))
                 for p in resp.get("prices", [])
                 if (p.get("conditions") or {}).get("min_purchase_unit")]
    almacen.append_auditoria([{
        "fecha": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "item_id": item_id, "campo": "precio_mayorista",
        "valor_anterior": "", "valor_nuevo": str(aplicados),
        "resultado": "OK", "operador": operador,
        "nota": "carga masiva de precios mayoristas"}])
    return True, aplicados


# Con 2.000+ publicaciones son 4.000+ llamadas seguidas y MercadoLibre corta
# por rate limit. Una pausa chica entre publicaciones evita la mayoria de los
# 429, y sale mucho mas barata que esperar los backoff.
PAUSA_ENTRE_ITEMS = 0.25


def aplicar(ml, sim, operador="", callback=None, omitir=None,
            pausa=PAUSA_ENTRE_ITEMS):
    """
    Aplica los tramos de la simulacion a todas las publicaciones.

    **Nunca lanza excepcion por una publicacion.** Con miles de items, dejar
    que una falla corte la corrida significa perder el resultado de todo lo
    que ya se aplico bien y no saber por donde se quedo. Cada fila se resuelve
    a OK o ERROR y la corrida sigue.

    `omitir` es un conjunto de `item_id` que ya se aplicaron: sirve para
    retomar una corrida cortada sin repetir lo hecho.
    """
    pendientes = sim[sim["accion"] == "aplicar"]
    if omitir:
        pendientes = pendientes[~pendientes["item_id"].isin(set(omitir))]

    resultados = []
    total = len(pendientes)

    for i, (_, fila) in enumerate(pendientes.iterrows(), start=1):
        t = [(int(fila[f"q{n}_unidades"]), float(fila[f"q{n}_precio"]))
             for n in (1, 2)
             if pd.notna(fila.get(f"q{n}_unidades"))
             and pd.notna(fila.get(f"q{n}_precio"))]
        try:
            ok, detalle = aplicar_uno(ml, fila["item_id"], t, operador=operador)
        except Exception as e:                     # noqa: BLE001
            # Red que se cae, respuesta rara, lo que sea: se anota y se sigue.
            ok, detalle = False, f"{type(e).__name__}: {str(e)[:200]}"

        resultados.append({**fila.to_dict(),
                           "resultado": "OK" if ok else "ERROR",
                           "detalle": "" if ok else str(detalle)[:200]})
        if callback:
            callback(i, total, fila)
        if pausa:
            time.sleep(pausa)

    return pd.DataFrame(resultados)


def main():
    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    regs = reglas()
    sim = simular(pubs, regs)

    print(f"Reglas activas: {len(regs)}")
    print(f"Publicaciones alcanzadas: {len(sim)}\n")
    print("Por regla:")
    for nombre, n in sim["regla"].value_counts().items():
        print(f"   {nombre:<36} {n:>5}")
    print("\nPor acción:", dict(sim["accion"].value_counts()))

    if "--resumen" not in sys.argv:
        print("\nEjemplos:")
        cols = ["sku", "regla", "precio", "q1_unidades", "q1_precio",
                "q2_unidades", "q2_precio"]
        print(sim[sim.accion == "aplicar"][cols].head(12)
              .to_string(index=False, float_format=lambda x: f"{x:,.0f}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
