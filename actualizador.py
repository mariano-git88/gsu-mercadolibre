#!/usr/bin/env python3
"""
Motor de actualizacion masiva de precios y stock desde una planilla.

Precio y stock comparten casi toda la logica (leer planilla, resolver a que
publicaciones va cada SKU, simular, aplicar), asi que viven juntos y se
diferencian por el parametro `operacion`.

Flujo obligatorio: leer -> simular -> revisar -> aplicar.
Nunca se aplica nada sin haber simulado antes.
"""

import re
from datetime import datetime

import pandas as pd

from catalogo import es_full, sku_del_atributo
from meli import PREFIJO_ITEM
from resolver import (indexar_por_sku, normalizar_sku, resolver_precio,
                      resolver_stock)

# Si un precio cambia mas que esto, lo marcamos para que el operador lo mire.
# No lo bloqueamos: puede ser legitimo. Pero un error de tipeo (un cero de mas)
# cae siempre aca.
UMBRAL_ALERTA_PRECIO = 0.50   # 50%

# El prefijo sale del sitio configurado en meli.py (MLU en Uruguay), asi
# que no hay que tocar esto si algun dia se apunta a otro pais.
PATRON_ITEM = re.compile(rf"^{PREFIJO_ITEM}\d+$", re.I)

# Nombres de columna que aceptamos, en orden de preferencia.
COLS_CLAVE = ["sku", "codigo", "código", "mla", "mlu", "publicacion", "publicación",
              "item", "item_id", "id"]
COLS_PRECIO = ["precio", "precio nuevo", "precio_nuevo", "valor", "importe",
               "pvp", "precio venta", "precio_venta"]
COLS_STOCK = ["stock", "cantidad", "unidades", "existencia", "disponible",
              "stock nuevo", "stock_nuevo"]


# ------------------------------------------------------------------ planilla

def leer_planilla(archivo):
    """Lee un Excel o CSV y devuelve el DataFrame crudo."""
    nombre = getattr(archivo, "name", str(archivo)).lower()
    if nombre.endswith(".csv"):
        return pd.read_csv(archivo, dtype=str)
    return pd.read_excel(archivo, dtype=str)


def detectar_columnas(df, operacion):
    """
    Adivina cual es la columna de SKU/publicacion y cual la del valor.
    Devuelve (col_clave, col_valor) o (None, None) si no las encuentra.
    """
    normal = {str(c).strip().lower(): c for c in df.columns}
    candidatas_valor = COLS_PRECIO if operacion == "precio" else COLS_STOCK

    col_clave = next((normal[c] for c in COLS_CLAVE if c in normal), None)
    col_valor = next((normal[c] for c in candidatas_valor if c in normal), None)

    # Si no matchea por nombre, probamos por contenido: la columna con mas
    # valores tipo MLU123456 o que parezcan SKU.
    if col_clave is None:
        for c in df.columns:
            muestra = df[c].dropna().astype(str).head(30)
            if len(muestra) and (muestra.str.match(PATRON_ITEM).mean() > 0.5):
                col_clave = c
                break

    return col_clave, col_valor


def _a_numero(valor):
    """
    Convierte a numero tolerando formatos de planilla rioplatense:
    '1.234,56', '$ 1234', '1234.00', etc.
    """
    if valor is None:
        return None
    s = str(valor).strip()
    if not s or s.lower() in ("nan", "none", "-"):
        return None
    s = s.replace("$", "").replace(" ", "").replace("\xa0", "")
    if "," in s and "." in s:
        # El ultimo separador es el decimal.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# ------------------------------------------------------------------ simulacion

def simular(df, pubs, operacion, col_clave=None, col_valor=None):
    """
    Devuelve un DataFrame con una fila por publicacion a modificar (o por
    problema detectado), sin tocar nada en MercadoLibre.
    """
    if col_clave is None or col_valor is None:
        detectadas = detectar_columnas(df, operacion)
        col_clave = col_clave or detectadas[0]
        col_valor = col_valor or detectadas[1]

    if not col_clave or not col_valor:
        raise ValueError(
            f"No pude identificar las columnas. Encontre: {list(df.columns)}. "
            f"Necesito una de SKU/publicacion y una de "
            f"{'precio' if operacion == 'precio' else 'stock'}.")

    indice = indexar_por_sku(pubs)
    por_id = {p["id"]: p for p in pubs}
    resolver = resolver_precio if operacion == "precio" else resolver_stock
    campo_actual = "price" if operacion == "precio" else "available_quantity"

    filas = []
    vistos = set()

    for _, fila in df.iterrows():
        clave_cruda = str(fila.get(col_clave, "") or "").strip()
        if not clave_cruda or clave_cruda.lower() == "nan":
            continue

        valor = _a_numero(fila.get(col_valor))
        clave = normalizar_sku(clave_cruda)

        # Renglon repetido en la planilla: nos quedamos con el primero y avisamos.
        if clave in vistos:
            filas.append(_fila_problema(clave_cruda, valor,
                                        "duplicado_en_planilla",
                                        "El SKU aparece mas de una vez en la planilla. "
                                        "Se usa el primero."))
            continue
        vistos.add(clave)

        if valor is None:
            filas.append(_fila_problema(clave_cruda, None, "valor_invalido",
                                        f"No pude leer el valor de la columna "
                                        f"'{col_valor}'."))
            continue

        if operacion == "precio" and valor <= 0:
            filas.append(_fila_problema(clave_cruda, valor, "valor_invalido",
                                        "El precio tiene que ser mayor a cero."))
            continue

        if operacion == "stock":
            if valor < 0 or valor != int(valor):
                filas.append(_fila_problema(clave_cruda, valor, "valor_invalido",
                                            "El stock tiene que ser un entero >= 0."))
                continue
            valor = int(valor)

        # La planilla puede traer el codigo de publicacion directo en vez del SKU.
        if PATRON_ITEM.match(clave_cruda):
            pub = por_id.get(clave_cruda.upper())
            if not pub:
                filas.append(_fila_problema(clave_cruda, valor, "no_encontrado",
                                            "Esa publicacion no esta en la cuenta."))
                continue
            if pub.get("status") != "active":
                filas.append(_fila_problema(clave_cruda, valor, "no_activa",
                                            f"La publicacion esta '{pub.get('status')}'."))
                continue
            if operacion == "stock" and es_full(pub):
                filas.append(_fila_problema(clave_cruda, valor, "sin_destino",
                                            "Esta en Full: el stock lo maneja ML."))
                continue
            filas.append(_fila_cambio(clave_cruda, pub, valor, campo_actual,
                                      operacion, "Indicada por codigo de publicacion."))
            continue

        res = resolver(clave, indice)
        if not res.ok:
            filas.append(_fila_problema(clave_cruda, valor, res.estado, res.motivo))
            continue

        for pub in res.destinos:
            filas.append(_fila_cambio(clave_cruda, pub, valor, campo_actual,
                                      operacion, res.motivo))

    columnas = ["clave", "item_id", "titulo", "tipo", "logistica",
                "valor_actual", "valor_nuevo", "variacion", "accion", "motivo"]
    return pd.DataFrame(filas, columns=columnas)


def _fila_problema(clave, valor, accion, motivo):
    return {"clave": clave, "item_id": "", "titulo": "", "tipo": "",
            "logistica": "", "valor_actual": None, "valor_nuevo": valor,
            "variacion": None, "accion": accion, "motivo": motivo}


def _fila_cambio(clave, pub, valor, campo_actual, operacion, motivo):
    actual = pub.get(campo_actual)
    variacion = None
    accion = "actualizar"

    if actual is not None and float(actual) == float(valor):
        accion = "sin_cambio"
    elif operacion == "precio" and actual:
        variacion = (valor - float(actual)) / float(actual)
        if abs(variacion) > UMBRAL_ALERTA_PRECIO:
            accion = "revisar"
            motivo = (f"Variacion de {variacion:+.0%} sobre el precio actual. "
                      f"Confirmar que no sea un error de carga. | {motivo}")

    return {"clave": clave, "item_id": pub["id"],
            "titulo": (pub.get("title") or "")[:70],
            "tipo": "Premium" if pub.get("listing_type_id") == "gold_pro" else "Clasica",
            "logistica": (pub.get("shipping") or {}).get("logistic_type", ""),
            "valor_actual": actual, "valor_nuevo": valor,
            "variacion": variacion, "accion": accion, "motivo": motivo}


def resumen(sim):
    """Conteos por accion, para mostrar antes de aplicar."""
    if sim.empty:
        return {}
    return sim["accion"].value_counts().to_dict()


# ------------------------------------------------------------------ aplicacion

def aplicar(ml, sim, operacion, operador="", incluir_revisar=False,
            callback=None):
    """
    Aplica los cambios de la simulacion. Solo toca las filas con accion
    'actualizar' (y 'revisar' si el operador lo pidio explicitamente).

    `callback(i, total, fila)` se llama en cada paso para mostrar progreso.
    Devuelve un DataFrame con el resultado de cada intento.
    """
    campo = "price" if operacion == "precio" else "available_quantity"
    acciones = ["actualizar"] + (["revisar"] if incluir_revisar else [])
    pendientes = sim[sim["accion"].isin(acciones)].copy()

    resultados = []
    total = len(pendientes)
    nota = f"carga masiva {operacion} {datetime.now():%Y-%m-%d %H:%M}"

    for i, (_, fila) in enumerate(pendientes.iterrows(), start=1):
        valor = fila["valor_nuevo"]
        if operacion == "stock":
            valor = int(valor)

        ok, detalle = ml.actualizar_publicacion(
            fila["item_id"], {campo: valor},
            valores_previos={campo: fila["valor_actual"]},
            operador=operador, nota=nota)

        resultados.append({**fila.to_dict(),
                           "resultado": "OK" if ok else "ERROR",
                           "detalle": "" if ok else str(detalle)[:200]})
        if callback:
            callback(i, total, fila)

    return pd.DataFrame(resultados)
