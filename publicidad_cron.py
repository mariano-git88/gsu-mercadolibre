#!/usr/bin/env python3
"""
Apaga solo los anuncios que no se bancan. Pensado para correr por GitHub
Actions.

    python publicidad_cron.py            -> dice que haria, no toca nada
    python publicidad_cron.py --aplicar  -> apaga

Hace dos cosas, las dos automaticas:

  - **apaga** lo que pasa el tope de ACOS o gasta sin vender;
  - **suma** a una campana las publicaciones que el analisis de Visitas vs
    ventas marca como `escalar` o `falta_exposicion`: ya convierten y les
    falta gente que las vea.

Las que le tocarian una campana **pausada** van a una campana propia
(`campana_nuevos` en la config). Sumarlas a una pausada no sirve —entran
activas pero la campana no corre—. En Uruguay hay una sola campana y esta
activa, asi que este caso no se da hoy.

**No hay topes por corrida**: se hace todo lo que califica, de una.

El unico freno que queda del lado nuestro es `GASTO_MINIMO`, que no posterga
nada — evita juzgar un anuncio con poca plata medida. Del lado de gastar, el
freno real es el **tope de presupuesto de cada campana**: por mas anuncios
que entren, no se puede gastar mas que eso.

Vale tenerlo presente porque agregar deja el anuncio ACTIVO y gastando desde
ese momento. Lo aprendimos por las malas: durante las pruebas, capturar esa
accion reactivo un anuncio de $182.000 al mes sin que nadie lo pidiera.

Todo queda en la auditoria con el estado anterior.

**Escribe por el panel, no por la API.** MercadoLibre no habilito la
escritura de Product Ads para la aplicacion (ver `publicidad.py`), asi que
esto usa el endpoint interno con la cookie `ssid` de los secrets. En Actions
hay que tenerla en `GSU_ML_SECRETS_TOML`, bajo `[ads]`.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import almacen
import panel_ads
import publicidad
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

# **Apagar no tiene tope.** Si un anuncio pasa el ACOS objetivo, se apaga en
# la misma corrida: la regla es "siempre que este por arriba, se da de baja".
# Se penso un tope para que un error de datos no pudiera apagar la cuenta
# entera de una, pero eso convertia la regla en "se apaga dentro de algunas
# semanas", que no es lo mismo.
#
# Lo que queda como freno es `GASTO_MINIMO`: nada se juzga con poca plata
# medida, asi que un anuncio nuevo o con ruido no entra igual.
TOPE_APAGAR = None

# Sumar tampoco tiene tope: se hace todo lo que califica en la misma corrida.
#
# Vale saber que esto no es simetrico con apagar. Agregar deja el anuncio
# ACTIVO y gastando desde ese momento, asi que una corrida con muchos
# candidatos sube el gasto de golpe. El freno real pasa a ser el **tope de
# presupuesto de cada campana**, que es lo unico que limita cuanto se puede
# gastar por mas anuncios que entren.
TOPE_SUMAR = None

# Debajo de este gasto en el periodo no vale la pena tocar nada: son
# centavos y el anuncio puede estar recien arrancando.
GASTO_MINIMO = 3000.0

DIAS = 30


def toca_esta_semana():
    """
    Si la corrida programada de hoy va o se saltea.

    **Cada 21 dias, no cada 7.** Con la ventana de 30 dias, un anuncio que se
    prende el martes se juzgaria el martes siguiente con 7 dias propios y 23
    de cuando estaba apagado: se lo puede prender y apagar antes de saber
    como funciona. Feedback del equipo, 2026-08-06.

    Se resuelve con la semana ISO modulo 3 en vez de guardar la fecha de la
    ultima corrida: es deterministico, no necesita estado y no se desincroniza
    si una semana el workflow no corre.
    """
    return date.today().isocalendar().week % 3 == 0


def correr(aplicar=False, verbose=True, log=None, conv=None, ml=None):
    """
    `log` recibe cada linea; sirve para mostrarlo en la app en vez de la
    consola. `conv` es el analisis de Visitas vs ventas ya hecho: medirlo son
    ~10 minutos (una llamada por publicacion), asi que si la app ya lo tiene
    en memoria no se vuelve a medir.
    """
    ml = ml or Meli(verbose=False)
    hasta = date.today() - timedelta(days=1)
    desde = hasta - timedelta(days=DIAS - 1)

    if log is None:
        def log(m):
            if verbose:
                print(m, flush=True)

    log(f"Publicidad del {desde} al {hasta}")
    df, advs, camps = publicidad.traer_todo(
        ml, desde.isoformat(), hasta.isoformat(),
        callback=lambda m: log(f"  {m}"))
    if not len(df):
        log("No hay anuncios.")
        return 0

    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8")) \
        if (DIR / "catalogo.json").exists() else publicidad_catalogo(ml)
    margenes, medidos = publicidad.margenes_por_sku()
    if not margenes:
        log("AVISO: no hay márgenes por SKU guardados, así que el tope de "
            "ACOS es uno solo para todo el catálogo. Corré Rentabilidad en la "
            "app y apretá «Guardar los márgenes para publicidad».")
    elif medidos:
        viejos = (pd.Timestamp.now() - pd.Timestamp(medidos)).days
        log(f"Márgenes de {len(margenes)} SKU, medidos hace {viejos} días.")
        if viejos > publicidad.MARGENES_VIEJOS_DIAS:
            log("AVISO: están viejos. Un margen desactualizado decide mal el "
                "tope de gasto y no se nota.")

    plan = publicidad.analizar(df, pubs, margenes=margenes)
    # Concentrar el presupuesto en los que mejor rinden, y despues frenar lo
    # que tocamos hace poco. El orden importa: el enfriamiento tiene que ser
    # lo ultimo, para que ninguna regla lo pase por encima.
    plan = publicidad.concentrar_presupuesto(plan, camps, dias=DIAS)
    plan = publicidad.aplicar_enfriamiento(plan)

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    log(f"\n{len(plan)} anuncios · gasto {pes(plan['gasto'].sum())} · "
        f"facturado {pes(plan['facturado'].sum())}")

    apagar = plan[(plan["accion"] == "pausar")
                  & (plan["gasto"] >= GASTO_MINIMO)
                  & (plan["ad_group_id"].notna())].copy()
    apagar = apagar.sort_values("gasto", ascending=False)

    # OJO: aca antes se cortaba con `return` cuando no habia nada que apagar,
    # y eso se saltaba la parte de sumar. Una corrida donde ya esta todo
    # dentro del ACOS objetivo es justo cuando conviene sumar.
    if not len(apagar):
        log("Nada para apagar: todo dentro del ACOS objetivo.")

    if TOPE_APAGAR and len(apagar) > TOPE_APAGAR:
        log(f"\n*** {len(apagar)} superan el tope de {TOPE_APAGAR} por "
            "corrida. Se apagan los de mayor gasto; el resto queda para la "
            "próxima. ***")
        apagar = apagar.head(TOPE_APAGAR)

    if len(apagar):
        log(f"\nA apagar: {len(apagar)} · gasto {pes(apagar['gasto'].sum())}")
    for _, r in apagar.iterrows():
        log(f"  {r['item_id']:<15} {pes(r['gasto']):>12}  "
            f"ACOS {r['acos']:>3.0f}%  {r['motivo'][:52]}")

    # ------------------------------------------------ que sumar
    sumar = candidatos_a_sumar(ml, df, pubs, log, conv=conv)
    if TOPE_SUMAR and len(sumar) > TOPE_SUMAR:
        log(f"\n*** {len(sumar)} candidatos superan el tope de {TOPE_SUMAR}. "
            "Entran los de más visitas; el resto queda para la próxima. ***")
        sumar = sumar.head(TOPE_SUMAR)
    if len(sumar):
        log(f"\nA sumar: {len(sumar)} publicaciones que convierten y no se "
            "publicitan")
        for _, r in sumar.head(10).iterrows():
            log(f"  {r['item_id']:<15} campaña {int(r['campaign_id']):<11} "
                f"{r['motivo'][:55]}")

    if not aplicar:
        log("\n(simulación: corré con --aplicar para ejecutarlo)")
        return 0

    if not panel_ads.hay_sesion():
        log("\nERROR: no hay sesión del panel cargada. Falta [ads] ssid en "
            "los secrets — sin eso no se puede escribir.")
        return 1

    sesion = panel_ads.leer_sesion()
    auditoria = []

    ok, res = 0, pd.DataFrame()
    if len(apagar):
        res = panel_ads.aplicar(sesion, ml, apagar, accion="pausar",
                                callback=lambda i, t, d: log(f"  {i}/{t} {d}"))
        ok = int((res["resultado"] == "OK").sum())
        log(f"\nApagados {ok} de {len(res)}.")
        auditoria += _auditar(res, "active", "paused")

    # Los que estan fuera de campana se agregan; los que ya estan adentro
    # pero apagados solo se prenden, para no mudarlos de donde ML los puso.
    for acc, antes in (("agregar", "idle"), ("activar", "paused")):
        # `sumar` viene vacio y **sin columnas** cuando el analisis de
        # conversion falla, asi que filtrar por 'accion' explotaria.
        if not len(sumar) or "accion" not in sumar:
            break
        filas = sumar[sumar["accion"] == acc]
        if not len(filas):
            continue
        res2 = panel_ads.aplicar(sesion, ml, filas, accion=acc,
                                 callback=lambda i, t, d: log(f"  {i}/{t} {d}"))
        ok2 = int((res2["resultado"] == "OK").sum())
        log(f"{acc.capitalize()}: {ok2} de {len(res2)}.")
        auditoria += _auditar(res2, antes, "active")

    guardado, detalle = almacen.append_auditoria(auditoria)
    if not guardado:
        log(f"AVISO: no se pudo escribir la auditoría: {detalle}")

    if ok < len(res):
        log("\nLos que fallaron suelen ser benignos: anuncios en `hold` que "
            "ML deshabilitó, o que el listado trae desactualizados.")
    return 0


def _auditar(res, antes, despues):
    return [{
        "fecha": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "item_id": r["item_id"], "campo": "ad_status",
        "valor_anterior": antes, "valor_nuevo": despues,
        "resultado": r["resultado"], "operador": "cron",
        "nota": str(r.get("motivo", ""))[:180],
    } for _, r in res.iterrows()]


def candidatos_a_sumar(ml, df_ads, pubs, log, conv=None):
    """
    Las publicaciones que convierten y no se publicitan.

    El analisis de visitas es **una llamada por publicacion** (ML no acepta
    mas de un id en `/items/visits`), o sea ~10 minutos para el catalogo. Es
    lo mas caro de la corrida y por eso va al final: si algo falla antes, al
    menos se apago lo que habia que apagar.
    """
    import conversion
    try:
        if conv is None or not len(conv):
            log("\nMidiendo visitas y ventas (tarda unos minutos)...")
            conv = conversion.analizar(ml, dias=DIAS,
                                       callback=lambda m: log(f"  {m}"))
        else:
            log(f"\nUsando el análisis de visitas ya hecho ({len(conv)} "
                "publicaciones).")
    except Exception as e:
        log(f"AVISO: no pude medir conversión ({type(e).__name__}: "
            f"{str(e)[:120]}). Esta corrida solo apaga.")
        return pd.DataFrame()

    advs = publicidad.anunciantes(ml)
    camps = {a["advertiser_id"]: publicidad.campanas(ml, a["advertiser_id"])
             for a in advs}
    c = publicidad.candidatos(conv, pubs, df_ads, advs, camps)
    if not len(c):
        return c
    # El enfriamiento vale para los dos lados: prender algo que apagamos hace
    # una semana es el mismo error que apagarlo de nuevo.
    c = publicidad.aplicar_enfriamiento(c)
    c = c[c["accion"] == "agregar"].sort_values("visitas", ascending=False)
    # Los estados de campana hacen falta para no "activar" un anuncio dentro
    # de una campana pausada, que no lo hace correr.
    estados = {x["id"]: x.get("status") for cs in camps.values() for x in cs}
    # Una llamada por candidato, asi que primero se recorta al tope.
    c = publicidad.resolver_candidatos(ml, c if TOPE_SUMAR is None else c.head(TOPE_SUMAR * 2),
                                       estados_camp=estados, callback=log)
    return c[c["accion"].isin(("agregar", "activar"))
             & c["ad_group_id"].notna()]


def publicidad_catalogo(ml):
    from catalogo import bajar_catalogo
    return bajar_catalogo(ml)


def main():
    # El disparo programado se saltea las semanas que no tocan; el manual
    # corre siempre, porque si alguien lo aprieta es porque lo quiere ahora.
    if "--programado" in sys.argv and not toca_esta_semana():
        print("Esta semana no toca: el ciclo es cada 21 días para que cada "
              "anuncio se juzgue con datos suyos.")
        return 0
    return correr(aplicar="--aplicar" in sys.argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
