#!/usr/bin/env python3
"""
Gestion de Product Ads: campanas, anuncios y reglas automaticas.

    python publicidad.py            -> foto de las campanas y de las reglas
    python publicidad.py --detalle  -> ademas escupe publicidad.csv

La cuenta uruguaya tiene **un solo anunciante** (`CRAFTERSUY`, id 72307) y
**ninguna campana todavia**. Medido el 05/08/2026 por dos vias que dicen lo
mismo: `campaigns/search` contesta 404 `advertiser_campaigns_not_found` y
`ads/search` devuelve 200 con **cero resultados**.

O sea que hoy esta seccion no tiene nada que mostrar. Esta portada para que
este lista el dia que se prenda la primera campana, y **los umbrales de las
reglas no estan calibrados contra datos de esta cuenta** —no hay con que—:
son los de Argentina, con el unico valor en pesos bajado a la escala local.
Cuando haya un mes de gasto real, revisarlos.

**Es solo lectura hasta que se llama `aplicar()`.** `analizar()` propone y
explica; `aplicar()` escribe.

La ruta vieja `/advertising/...` esta deprecada y contesta 404, o un 500 con
"Type mismatch" que no dice nada. La que anda es
`/marketplace/advertising/{site}/advertisers/{id}/product_ads/...` y **exige
el header `Api-Version: 2`**.
"""

import json
import sys
from pathlib import Path

import pandas as pd

import almacen
from catalogo import sku_del_atributo
from meli import Meli, MeliError, SITE_ID

DIR = Path(__file__).resolve().parent

CABECERA = {"Api-Version": "2"}
BASE = "/marketplace/advertising/{site}/advertisers/{adv}/product_ads"

# Las metricas hay que pedirlas por nombre: sin el parametro `metrics` el
# campo viene {} y con `metrics_summary` sin `metrics` tira 400.
METRICAS = ("clicks,prints,cost,acos,ctr,cvr,roas,total_amount,"
            "direct_amount,indirect_amount,units_quantity")

# ------------------------------------------------------------------ config

HOJA_CONFIG = "publicidad_config"
COLUMNAS_CONFIG = ["clave", "valor"]
HOJA_ESTRATEGICOS = "publicidad_estrategicos"
COLUMNAS_ESTRATEGICOS = ["sku", "nota"]

# Los topes viven en la Sheet, no en un archivo: en Streamlit Cloud el disco
# es efimero y cualquier cambio se perderia en el proximo deploy.
POR_DEFECTO = {
    "acos_max": 35.0,        # arriba de esto el anuncio no se banca
    "roas_min": 2.5,         # abajo de esto tampoco
    "acos_bueno": 15.0,      # candidato a empujar
    "roas_bueno": 6.0,
    "clicks_minimos": 30,    # menos que esto es ruido, no una senal
    # El unico umbral que estaba en pesos argentinos. En Uruguay el ticket
    # promedio es $1.223, asi que $5.000 dejaba afuera practicamente todo.
    # $500 son ~1,4 tickets: es el piso para que el dato diga algo.
    # **Sin calibrar contra gasto real**, porque todavia no hay.
    "gasto_minimo": 500.0,
}


def config():
    """Topes vigentes. Lo que falte en la Sheet cae al valor por defecto."""
    valores = dict(POR_DEFECTO)
    try:
        for fila in almacen.leer_hoja(HOJA_CONFIG, COLUMNAS_CONFIG):
            clave = str(fila.get("clave", "")).strip()
            if clave in valores:
                try:
                    valores[clave] = float(str(fila.get("valor")).replace(",", "."))
                except (TypeError, ValueError):
                    pass
    except Exception:
        # Sin Sheet configurada se trabaja con los defaults. No es motivo
        # para tumbar la pantalla.
        pass
    return valores


def guardar_config(valores):
    filas = [{"clave": k, "valor": v} for k, v in valores.items()]
    return almacen.reescribir_hoja(HOJA_CONFIG, COLUMNAS_CONFIG, filas)


def estrategicos():
    """
    SKU que las reglas **no** tocan nunca.

    Son los que se publicitan por decision comercial y no por rentabilidad:
    lanzamientos, productos que traen trafico, lo que se quiere defender de un
    competidor. Sin esta lista, la primera corrida de reglas los apaga a todos
    y nadie se entera hasta que caen las visitas.
    """
    try:
        filas = almacen.leer_hoja(HOJA_ESTRATEGICOS, COLUMNAS_ESTRATEGICOS)
    except Exception:
        return {}
    return {str(f.get("sku", "")).strip().upper(): str(f.get("nota", ""))
            for f in filas if str(f.get("sku", "")).strip()}


def guardar_estrategicos(filas):
    return almacen.reescribir_hoja(HOJA_ESTRATEGICOS, COLUMNAS_ESTRATEGICOS,
                                   filas)


# ------------------------------------------------------------------ lectura

def anunciantes(ml):
    """Los anunciantes de la cuenta. En Uruguay hay uno solo."""
    r = ml.get("/advertising/advertisers", product_id="PADS")
    return [a for a in (r.get("advertisers") or [])
            if a.get("site_id") == SITE_ID]


def campanas(ml, advertiser_id):
    """
    Las campañas del anunciante, o lista vacia si todavia no tiene ninguna.

    **Un anunciante sin campañas no es un error**, y ML lo contesta como si lo
    fuera: `404 advertiser_campaigns_not_found`. Es la situacion normal de una
    cuenta que todavia no arranco con publicidad —hoy, la uruguaya— y dejar
    que ese 404 se propague tumbaba la seccion entera con un traceback en vez
    de decir "no hay campañas".
    """
    base = BASE.format(site=SITE_ID, adv=advertiser_id)
    try:
        r = ml.get(f"{base}/campaigns/search", _headers=CABECERA,
                   limit=50, offset=0)
    except Exception as e:  # noqa: BLE001
        if "advertiser_campaigns_not_found" in str(e):
            return []
        raise
    return r.get("results") or []


def anuncios(ml, advertiser_id, desde, hasta, callback=None, tope=None):
    """
    Todos los anuncios del anunciante con sus metricas del periodo.

    **Deduplica por item_id**: la API repite el mismo anuncio en mas de una
    fila (misma publicacion en varios ad_group), y contarlo dos veces inflaria
    el gasto y haria que una regla lo evalue dos veces con el mismo resultado.
    """
    base = BASE.format(site=SITE_ID, adv=advertiser_id)
    vistos, salida, offset = set(), [], 0

    while True:
        r = ml.get(f"{base}/ads/search", _headers=CABECERA,
                   limit=50, offset=offset,
                   date_from=desde, date_to=hasta, metrics=METRICAS)
        filas = r.get("results") or []
        if not filas:
            break

        for a in filas:
            item = a.get("item_id")
            if not item or item in vistos:
                continue
            vistos.add(item)
            m = a.get("metrics") or {}
            salida.append({
                "item_id": item,
                "advertiser_id": advertiser_id,
                "campaign_id": a.get("campaign_id"),
                # La escritura NO va por item_id: va por ad_group_id. Sin
                # esta columna no se puede aplicar nada.
                "ad_group_id": a.get("ad_group_id"),
                "titulo": (a.get("title") or "")[:60],
                "marca": a.get("brand_value_name") or "",
                "estado_ad": a.get("status"),
                "precio": a.get("price"),
                "catalogo": bool(a.get("catalog_listing")),
                "gana_buybox": bool(a.get("buy_box_winner")),
                "clicks": m.get("clicks") or 0,
                "impresiones": m.get("prints") or 0,
                "gasto": float(m.get("cost") or 0),
                "facturado": float(m.get("total_amount") or 0),
                "unidades": m.get("units_quantity") or 0,
                "acos": float(m.get("acos") or 0),
                "roas": float(m.get("roas") or 0),
                "ctr": float(m.get("ctr") or 0),
                "cvr": float(m.get("cvr") or 0),
            })
            if tope and len(salida) >= tope:
                return salida

        if callback:
            callback(f"Anunciante {advertiser_id}: {len(salida)} anuncios...")

        total = (r.get("paging") or {}).get("total", 0)
        offset += 50
        if offset >= total:
            break

    return salida


def traer_todo(ml, desde, hasta, callback=None, tope=None):
    """
    Anuncios de los tres anunciantes.

    Devuelve (df, advs, camps_por_adv). Los dos ultimos hacen falta para
    saber a que campana mandar una publicacion nueva: ver `mapa_de_campanas`.
    """
    advs = anunciantes(ml)
    nombres, camps_por_adv, filas = {}, {}, []
    for a in advs:
        aid = a["advertiser_id"]
        nombres[aid] = a.get("advertiser_name") or str(aid)
        camps_por_adv[aid] = campanas(ml, aid)

        if callback:
            callback(f"Leyendo {nombres[aid]}...")
        filas.extend(anuncios(ml, aid, desde, hasta, callback=callback,
                              tope=tope))

    df = pd.DataFrame(filas)
    if len(df):
        df["anunciante"] = df["advertiser_id"].map(
            lambda i: nombres.get(i, str(i)))
        df = _sin_repetidos(df)
    return df, advs, camps_por_adv


# Prioridad para elegir con que fila quedarse cuando un anuncio aparece en
# varios anunciantes: manda donde realmente esta corriendo.
_PRIORIDAD = {"active": 0, "delegated": 1, "paused": 2, "idle": 3,
              "hold": 4, "deleted": 5}


def _sin_repetidos(df):
    """
    **Un mismo anuncio aparece bajo varios anunciantes, con las MISMAS
    metricas**, y sumarlas infla el gasto.

    Medido el 2026-08-05: 7.093 filas para 5.707 anuncios unicos —1.386
    aparecian dos veces— y el gasto de 30 dias daba $10.426.194 sumando filas
    contra **$6.281.373 real**. Un 66% de mas, y en un tablero que se usa para
    decidir donde recortar.

    Las metricas son **por publicacion, no por campana**: ML devuelve el mismo
    gasto y la misma facturacion en cada fila. Por eso no se suman ni se
    reparten: se conserva una sola fila, la del anunciante donde el anuncio
    esta corriendo de verdad.
    """
    if "item_id" not in df or not len(df):
        return df
    df = df.copy()
    df["_orden"] = df["estado_ad"].map(lambda e: _PRIORIDAD.get(e, 9))
    # Cuantos anunciantes lo tienen: sirve para entender un anuncio raro sin
    # tener que volver a la API.
    df["anunciantes"] = df.groupby("item_id")["item_id"].transform("size")
    df = (df.sort_values("_orden")
            .drop_duplicates(subset=["item_id"], keep="first")
            .drop(columns=["_orden"]))
    return df.reset_index(drop=True)


# ------------------------------------------------------------------ reglas

# El orden importa: la primera regla que matchea es la que manda. Van de la
# mas dura (no se puede vender) a la mas discutible (rinde poco).
SIN_DATOS = "pocos datos todavía"

# **El estado que trae `ads/search` esta atrasado.** No es solo el item: el
# propio estado del anuncio viene viejo. Sacando una tanda de anuncios de sus
# campanas, el listado decia `delegated` para 594 y al preguntarle a
# `ad_groups/{id}` uno por uno aparecian IDLE, HOLD y hasta ad_groups que ya
# no existen (`ad_group_not_found_exception`). Sirve para analizar; para
# decidir sobre uno en particular hay que preguntarle a `ad_groups/{id}`.
#
# Consecuencia practica: un lote de acciones **siempre va a tener fallas
# benignas** (anuncios que ya estaban idle, o en hold, que no se pueden mover).
# No son errores del lote: son el listado desactualizado.
#
# Estados de anuncio vistos en la cuenta: active, paused, idle, hold, deleted
# y **delegated**.
#
# `delegated` es un anuncio que gestiona MercadoLibre solo, y **esta
# corriendo y gastando**: 13 de esos llevaban $989.000 en 30 dias con ACOS
# 9-15%. Tratar "todo lo que no es active" como apagado hacia que el modulo
# propusiera *encender* anuncios que ya funcionan — y peor, los mostraba como
# plata dormida cuando eran de lo mejor que tiene la cuenta.
CORRIENDO = ("active", "delegated")

# Solo estos se pueden proponer para encender. `hold` lo deshabilito ML,
# `deleted` no existe mas, y `delegated` ya corre.
APAGADOS = ("paused", "idle")


def _vendible(pub):
    """Si la publicacion se puede comprar hoy."""
    if pub is None:
        return False, "no está en el catálogo"
    if pub.get("status") != "active":
        return False, f"la publicación está {pub.get('status')}"
    if not (pub.get("available_quantity") or 0):
        return False, "sin stock"
    return True, ""


def analizar(df_ads, pubs, cfg=None, estrat=None, df_rent=None):
    """
    Marca que hacer con cada anuncio. Devuelve el mismo DataFrame con
    `accion` ('pausar' / 'activar' / 'revisar' / 'ninguna') y `motivo`.

    `df_rent` es la salida de rentabilidad, opcional: si viene, se usa para
    apagar lo que pierde plata de caja.
    """
    cfg = cfg or config()
    estrat = estrat if estrat is not None else estrategicos()
    df = df_ads.copy()
    if not len(df):
        return df

    por_id = {p["id"]: p for p in pubs}
    sku_de = {p["id"]: (sku_del_atributo(p) or
                        p.get("seller_custom_field") or "").strip().upper()
              for p in pubs}
    df["sku"] = df["item_id"].map(lambda i: sku_de.get(i, ""))

    pierde_plata = set()
    if df_rent is not None and len(df_rent) and "sku" in df_rent:
        col = ("gana_por_unidad" if "gana_por_unidad" in df_rent
               else "margen_unitario" if "margen_unitario" in df_rent else None)
        if col:
            pierde_plata = set(
                df_rent[df_rent[col] < 0]["sku"].astype(str).str.upper())

    acciones, motivos = [], []
    for _, a in df.iterrows():
        sku = a["sku"]
        activo = a["estado_ad"] in CORRIENDO

        # 1. Estrategico: no se toca, gane o pierda.
        if sku and sku in estrat:
            acciones.append("ninguna")
            motivos.append(f"SKU estratégico — {estrat[sku] or 'no se toca'}")
            continue

        # 2. `hold` es un anuncio que deshabilito ML. No gasta, no se puede
        #    encender y no se puede mover de campana. Proponer algo sobre el
        #    es prometer una accion que la API va a rechazar.
        if a["estado_ad"] == "hold":
            acciones.append("ninguna")
            motivos.append("deshabilitado por MercadoLibre")
            continue

        # 3. Lo que no se puede comprar no se publicita. Es la unica regla
        #    que no admite discusion: son clics pagos a una pagina sin stock.
        ok, porque = _vendible(por_id.get(a["item_id"]))
        if not ok:
            acciones.append("pausar" if activo else "ninguna")
            motivos.append(porque if activo else f"{porque} (ya pausado)")
            continue

        # 3. Sin datos suficientes no se juzga. Apagar por un ACOS calculado
        #    sobre 4 clics es apagar por ruido.
        flaco = (a["clicks"] < cfg["clicks_minimos"]
                 and a["gasto"] < cfg["gasto_minimo"])

        if activo and not flaco:
            if a["unidades"] == 0 and a["gasto"] >= cfg["gasto_minimo"]:
                acciones.append("pausar")
                motivos.append(f"gastó ${a['gasto']:,.0f} y no vendió nada"
                               .replace(",", "."))
                continue
            if a["acos"] > cfg["acos_max"]:
                acciones.append("pausar")
                motivos.append(f"ACOS {a['acos']:.0f}% supera el tope de "
                               f"{cfg['acos_max']:.0f}%")
                continue
            if 0 < a["roas"] < cfg["roas_min"]:
                acciones.append("pausar")
                motivos.append(f"ROAS {a['roas']:.1f} por debajo de "
                               f"{cfg['roas_min']:.1f}")
                continue
            if sku and sku in pierde_plata:
                acciones.append("pausar")
                motivos.append("el SKU pierde plata de caja")
                continue

        # 4. Apagado pero rinde: candidato a volver a encender. Solo cuenta
        #    lo que esta realmente apagado y se puede prender.
        if a["estado_ad"] in APAGADOS and not flaco:
            if a["acos"] and a["acos"] < cfg["acos_bueno"] and a["unidades"]:
                acciones.append("activar")
                motivos.append(f"ACOS {a['acos']:.0f}%, mejor que "
                               f"{cfg['acos_bueno']:.0f}% — está apagado")
                continue
            if a["roas"] >= cfg["roas_bueno"] and a["unidades"]:
                acciones.append("activar")
                motivos.append(f"ROAS {a['roas']:.1f} — está apagado")
                continue

        # 5. La marca del anuncio no coincide con su anunciante. No se
        #    corrige solo: mover un anuncio de campana cambia el presupuesto
        #    de las dos, y eso lo decide una persona.
        if a["marca"] and a["anunciante"] and \
                a["marca"].strip().lower() not in a["anunciante"].strip().lower():
            acciones.append("revisar")
            motivos.append(f"marca {a['marca']} en la campaña "
                           f"{a['anunciante']}")
            continue

        acciones.append("ninguna")
        motivos.append(SIN_DATOS if flaco else "dentro de los topes")

    df["accion"] = acciones
    df["motivo"] = motivos
    return df.sort_values("gasto", ascending=False).reset_index(drop=True)


# ------------------------------------------------- candidatos a publicitar

# Diagnosticos de `conversion.py` que significan "esto convierte y le falta
# gente que lo vea". Es el caso de manual para publicidad: no se empuja lo que
# tiene visitas y no vende —ahi el problema es el precio o las fotos, y pagar
# clics no lo arregla— sino lo que **ya demostro que convierte**.
CON_POTENCIAL = ("escalar", "falta_exposicion")

# Los anuncios en estos estados no estan gastando, asi que la publicacion
# cuenta como no publicitada.
NO_PUBLICITA = ("idle", "paused", "deleted")


def marca_de(pub):
    for a in pub.get("attributes") or []:
        if a.get("id") == "BRAND":
            return a.get("value_name") or ""
    return ""


def mapa_de_campanas(advs, camps_por_adv):
    """
    marca -> (advertiser_id, campaign_id), armado desde el **nombre del
    anunciante**, no desde los anuncios que ya existen.

    Aprenderlo de los anuncios parece mas elegante y esta mal: una campana
    contiene publicaciones de varias marcas, asi que "la campana mas frecuente
    para la marca X" termina mandando productos de una marca a la campana de
    otra. Con presupuestos separados por marca, eso es gastar del bolsillo
    equivocado. Se vio en la primera prueba, en Argentina.

    **En Uruguay hay un solo anunciante y ninguna campana**, asi que esto
    devuelve vacio hasta que se cree la primera. La logica se deja igual a
    proposito: si algun dia se abren anunciantes por marca, ya funciona, y
    mientras tanto todo cae en la campana **general** —la que se llama asi—,
    que con un anunciante unico es la unica que va a haber.
    """
    mapa, general = {}, None
    for a in advs or []:
        aid = a["advertiser_id"]
        cs = [c for c in (camps_por_adv.get(aid) or [])
              if c.get("status") != "deleted"]
        if not cs:
            continue
        destino = (aid, cs[0]["id"])
        mapa[(a.get("advertiser_name") or "").strip().lower()] = destino
        if "general" in (cs[0].get("name") or "").lower():
            general = destino
    if general is None and mapa:
        general = list(mapa.values())[0]
    return mapa, general


def candidatos(df_conv, pubs, df_ads, advs=None, camps_por_adv=None,
               cfg=None, estrat=None):
    """
    Publicaciones **con potencial de conversion que hoy no se publicitan**.

    Sale de `conversion.py`: son las que ya convierten por encima del promedio
    de la cuenta o que venden con muy pocas visitas. La regla es la misma que
    se usa en Visitas vs ventas, aplicada a publicidad.

    Devuelve filas con `accion='agregar'` listas para sumarse al plan.
    """
    cfg = cfg or config()
    estrat = estrat if estrat is not None else estrategicos()
    if df_conv is None or not len(df_conv):
        return pd.DataFrame()

    por_id = {p["id"]: p for p in pubs}
    mapa, general = mapa_de_campanas(advs, camps_por_adv or {})
    estados_camp = {c["id"]: c.get("status")
                    for cs in (camps_por_adv or {}).values() for c in cs}
    if not mapa and general is None:
        # Sin el mapa no se sabe a que campana mandarlas, y mandarlas a la
        # equivocada gasta del presupuesto de otra marca.
        return pd.DataFrame()

    # Publicaciones que YA estan gastando: esas no son candidatas.
    publicitadas = set()
    if df_ads is not None and len(df_ads):
        publicitadas = set(df_ads[~df_ads["estado_ad"].isin(NO_PUBLICITA)]
                           ["item_id"])
        # `hold` lo deshabilito ML: no se puede agregar a ninguna campana.
        bloqueados = set(df_ads[df_ads["estado_ad"] == "hold"]["item_id"])
    else:
        bloqueados = set()

    filas = []
    for _, r in df_conv.iterrows():
        if r.get("diagnostico") not in CON_POTENCIAL:
            continue
        item = r["item_id"]
        if item in publicitadas or item in bloqueados:
            continue
        pub = por_id.get(item)
        if pub is None or pub.get("status") != "active":
            continue
        if not (pub.get("available_quantity") or 0):
            continue

        sku = str(r.get("sku") or "").strip().upper()
        marca = marca_de(pub)
        m = marca.strip().lower()
        destino = mapa.get(m)
        if destino is None:
            # "Suprabond Somerset" es Suprabond. Sin esto caeria en la
            # campana general y gastaria del presupuesto equivocado.
            for nombre, d in mapa.items():
                if m.startswith(nombre + " "):
                    destino = d
                    break
        destino = destino or general
        if not destino:
            continue
        estado_camp = estados_camp.get(destino[1], "")

        filas.append({
            "item_id": item, "sku": sku,
            "titulo": (r.get("titulo") or "")[:60],
            "marca": marca,
            "advertiser_id": destino[0], "campaign_id": destino[1],
            "campana_activa": estado_camp == "active",
            "estado_ad": "sin anuncio",
            "visitas": r.get("visitas", 0),
            "unidades": r.get("unidades", 0),
            "conversion": r.get("conversion", 0),
            "gasto": 0.0, "facturado": 0.0, "acos": 0.0, "roas": 0.0,
            "accion": "ninguna" if sku in estrat else "agregar",
            "motivo": (f"SKU estratégico — {estrat[sku]}" if sku in estrat else
                       f"{r.get('diagnostico')}: convierte "
                       f"{float(r.get('conversion') or 0):.1%} con solo "
                       f"{int(r.get('visitas') or 0)} visitas"),
        })

    return pd.DataFrame(filas)


# ---------------------------------------------------------------- escritura

# Los recursos van **sin el anunciante en el path**:
# /marketplace/advertising/{site}/product_ads/{recurso}/{id}.
#
# **La escritura de un anuncio va por `ad_groups/{ad_group_id}`, no por
# `ads/{item_id}`.** `ads/{item}` sirve para LEER y no tiene handler de
# escritura: el PUT ahi devuelve un 503 con `Content-Length: 0`, que parece
# un servicio caido y en realidad es el gateway sin nada atras. Se perdio
# bastante tiempo persiguiendo ese 503 como si fuera un problema de permisos.
# La ruta correcta contesta un 401 limpio cuando falta permiso.
def _ruta_ad_group(ad_group_id):
    return (f"/marketplace/advertising/{SITE_ID}/product_ads/ad_groups/"
            f"{ad_group_id}")


def _ruta_campana(campaign_id=None):
    base = f"/marketplace/advertising/{SITE_ID}/product_ads/campaigns"
    return f"{base}/{campaign_id}" if campaign_id else base


def leer_ad(ml, item_id):
    """El anuncio como lo ve ML ahora — de aca sale el `ad_group_id`."""
    try:
        return ml.get(
            f"/marketplace/advertising/{SITE_ID}/product_ads/ads/{item_id}",
            _headers=CABECERA)
    except Exception:
        return None


def _ad_group_de(ml, item_id, ad_group_id=None):
    """El ad_group del anuncio; si no vino en la tabla, se pregunta."""
    if ad_group_id:
        return int(ad_group_id)
    ad = leer_ad(ml, item_id)
    return int(ad["ad_group_id"]) if ad and ad.get("ad_group_id") else None


def cambiar_estado(ml, item_id, estado, ad_group_id=None, campaign_id=None):
    """
    Prende o apaga un anuncio. `estado` es 'active' o 'paused'.

    Devuelve (ok, detalle). No lanza: en un lote de cientos, una falla no
    puede llevarse la corrida.
    """
    ag = _ad_group_de(ml, item_id, ad_group_id)
    if not ag:
        return False, "no pude resolver el ad_group_id"
    cuerpo = {"status": estado}
    if campaign_id:
        cuerpo["campaign_id"] = int(campaign_id)
    try:
        r = ml.put(_ruta_ad_group(ag), cuerpo, _headers=CABECERA)
        return True, (r or {}).get("status", estado)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:180]}"


def agregar_a_campana(ml, item_id, campaign_id, ad_group_id=None):
    """
    Suma la publicacion a una campana. Queda **activa por defecto**, o sea
    que empieza a gastar en cuanto entra.
    """
    return cambiar_estado(ml, item_id, "active", ad_group_id, campaign_id)


def sacar_de_campana(ml, item_id, ad_group_id=None):
    """
    Saca el anuncio de su campana. Queda en `idle`: sigue disponible para
    publicitar pero no gasta.

    **No se manda `status` en la misma llamada**: al salir de la campana el
    anuncio pasa a idle solo, y mandar los dos campos juntos falla.
    """
    ag = _ad_group_de(ml, item_id, ad_group_id)
    if not ag:
        return False, "no pude resolver el ad_group_id"
    try:
        r = ml.put(_ruta_ad_group(ag), {"campaign_id": 0}, _headers=CABECERA)
        return True, (r or {}).get("status", "idle")
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:180]}"


def aplicar(ml, plan, operador="", callback=None, acciones=("pausar",)):
    """
    Ejecuta el plan. Por defecto **solo pausa**: encender un anuncio gasta
    plata y es una decision distinta de dejar de gastarla, asi que 'activar'
    hay que pedirlo explicitamente.

    Cada anuncio va en su propio try. Todo queda en la auditoria.

    **Las rutas son las correctas y aun asi la cuenta no puede escribir.**
    Verificado el 2026-08-05, las dos contestan lo mismo:

        PUT .../product_ads/campaigns/{id}      -> 401 "User does not have
        PUT .../product_ads/ad_groups/{id}         permission to write."

    En Argentina no es de la cuenta ni de los scopes: falla igual con dos
    tokens distintos, los dos con `urn:ml:mktp:ads:/read-write` concedido y
    con la app aprobada entera en el devcenter. Falta que ML habilite la
    escritura de Product Ads para la aplicacion; se arregla del lado de ML.

    **En Uruguay no se pudo verificar**: sin una sola campana no hay nada que
    escribir. Cuando exista la primera, probar esto antes de confiar en el
    boton de aplicar.
    """
    if plan is None or not len(plan):
        return pd.DataFrame()

    pendientes = plan[plan["accion"].isin(acciones)]
    nota = f"publicidad {pd.Timestamp.now():%Y-%m-%d %H:%M}"
    salida, total = [], len(pendientes)

    for i, (_, a) in enumerate(pendientes.iterrows(), start=1):
        if callback:
            callback(i, total, a)

        accion = a["accion"]
        nuevo = {"pausar": "paused", "activar": "active",
                 "agregar": "active", "sacar": "idle"}.get(accion, "")
        fila = {"item_id": a["item_id"], "sku": a.get("sku", ""),
                "titulo": a.get("titulo", ""),
                "anunciante": a.get("anunciante", ""),
                "estado_antes": a["estado_ad"], "estado_nuevo": nuevo,
                "gasto": a.get("gasto", 0), "acos": a.get("acos", 0),
                "motivo": a.get("motivo", "")}

        ag = a.get("ad_group_id")
        if accion == "agregar":
            ok, detalle = agregar_a_campana(ml, a["item_id"],
                                            a.get("campaign_id"), ag)
        elif accion == "sacar":
            ok, detalle = sacar_de_campana(ml, a["item_id"], ag)
        else:
            ok, detalle = cambiar_estado(ml, a["item_id"], nuevo, ag)
        # El anuncio no es una publicacion, pero la auditoria es el unico
        # lugar donde queda como estaba antes.
        from meli import registrar_auditoria
        registrar_auditoria(a["item_id"], {"ad_status": nuevo},
                            {"ad_status": a["estado_ad"]},
                            {"ad_status": detalle if ok else ""},
                            "OK" if ok else f"ERROR: {detalle}",
                            operador, f"{nota} - {a.get('motivo','')}"[:200])

        salida.append({**fila, "resultado": "OK" if ok else "ERROR",
                       "detalle": "" if ok else str(detalle)[:200]})

    return pd.DataFrame(salida)


# ------------------------------------------------------------------ campanas

def acos_a_roas(acos_pct):
    """
    **Desde diciembre de 2025 el objetivo se escribe como `roas_target`, no
    como `acos_target`.** Son la misma cosa dada vuelta: ACOS = 100 / ROAS.

    Un ACOS objetivo de 23% es un ROAS de 4,35. Verificado contra las campanas
    argentinas, donde los dos numeros que devuelve la API coinciden.
    """
    return round(100.0 / float(acos_pct), 4) if acos_pct else 0.0


def cambiar_campana(ml, campaign_id, cambios):
    """
    Modifica una campana: `status` ('active'/'paused'), `budget`,
    `roas_target`, `strategy`, `name`.

    Son tres campanas, asi que se usa poco y a mano — pero **el presupuesto es
    lo unico que topea el gasto de todo lo demas**, y tenerlo aca evita entrar
    al panel.

    Si viene `acos_target` se traduce: la API vieja lo aceptaba y la nueva no.
    """
    cuerpo = dict(cambios)
    if "acos_target" in cuerpo and "roas_target" not in cuerpo:
        cuerpo["roas_target"] = acos_a_roas(cuerpo.pop("acos_target"))
    if "strategy" in cuerpo:
        cuerpo["strategy"] = str(cuerpo["strategy"]).lower()
    try:
        return True, ml.put(_ruta_campana(campaign_id), cuerpo,
                            _headers=CABECERA)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def crear_campana(ml, advertiser_id, nombre, presupuesto, roas_target,
                  strategy="profitability", estado="paused"):
    """
    Crea una campana. **Nace pausada a proposito**: una campana nueva con
    presupuesto empieza a gastar en cuanto se activa, y esa es una decision
    aparte de crearla.

    OJO con la ruta: el **alta lleva el anunciante en el path** y la
    modificacion NO. Son asimetricas y no hay forma de deducirlo:

        POST .../advertisers/{adv}/product_ads/campaigns   -> crear
        PUT  .../product_ads/campaigns/{id}                -> modificar

    El POST sin anunciante contesta 404, no 401, que es lo que confunde.
    """
    cuerpo = {"name": nombre, "budget": float(presupuesto),
              "roas_target": float(roas_target),
              "strategy": str(strategy).lower(), "status": estado}
    ruta = (f"{BASE.format(site=SITE_ID, adv=advertiser_id)}/campaigns")
    try:
        return True, ml.post(ruta, cuerpo, _headers=CABECERA)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def main():
    from datetime import date, timedelta
    ml = Meli(verbose=False)
    hasta = date.today() - timedelta(days=1)
    desde = hasta - timedelta(days=29)

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    print(f"Publicidad del {desde} al {hasta}\n")

    for a in anunciantes(ml):
        print(f"  {a['advertiser_name']} (id {a['advertiser_id']})")
        for c in campanas(ml, a["advertiser_id"]):
            print(f"     campaña «{c['name']}» — {c['status']} · "
                  f"presupuesto {pes(c.get('budget') or 0)} · "
                  f"ACOS objetivo {c.get('acos_target')}%")

    df, _, _ = traer_todo(ml, desde.isoformat(), hasta.isoformat(),
                          callback=lambda m: print(f"   {m}"))
    if not len(df):
        print("\nNo hay anuncios.")
        return 0

    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    plan = analizar(df, pubs)

    print(f"\n{len(plan)} anuncios · gasto {pes(plan['gasto'].sum())} · "
          f"facturado {pes(plan['facturado'].sum())}")
    print("\nQué haría:")
    for acc, g in plan.groupby("accion"):
        print(f"  {acc:<10} {len(g):>5}   (gasto {pes(g['gasto'].sum())})")

    print("\nLos 10 de mayor gasto a pausar:")
    for _, a in plan[plan["accion"] == "pausar"].head(10).iterrows():
        print(f"  {a['sku'] or a['item_id']:<22} {pes(a['gasto']):>12}  "
              f"ACOS {a['acos']:>5.0f}%  {a['motivo']}")

    if "--detalle" in sys.argv:
        plan.to_csv(DIR / "publicidad.csv", index=False)
        print(f"\nGuardado en publicidad.csv ({len(plan)} filas)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
