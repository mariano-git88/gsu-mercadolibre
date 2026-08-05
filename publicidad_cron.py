#!/usr/bin/env python3
"""
Apaga solo los anuncios que no se bancan. Pensado para correr por GitHub
Actions.

    python publicidad_cron.py            -> dice que haria, no toca nada
    python publicidad_cron.py --aplicar  -> apaga

**Solo apaga. Nunca prende, nunca agrega a una campana.** Encender gasta
plata y esa decision se toma mirando; apagar deja de gastarla. Si algo de
esto falla o se desmadra, que se desmadre en la direccion de gastar menos.

Se vio por que hace falta esa regla: durante las pruebas, una accion
aparentemente inocente reactivo un anuncio de $182.000 al mes sin que nadie
lo pidiera.

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

# Cuantos anuncios como maximo apaga una corrida. No es por rendimiento: es
# para que un error de datos —un catalogo viejo, una metrica rara— no pueda
# apagar la cuenta entera de una. Si la lista da mas, apaga los de mayor
# gasto y el resto queda para la corrida siguiente, con el aviso en el log.
TOPE_POR_CORRIDA = 25

# Debajo de este gasto en el periodo no vale la pena tocar nada: son
# centavos y el anuncio puede estar recien arrancando.
GASTO_MINIMO = 3000.0

DIAS = 30


def correr(aplicar=False, verbose=True):
    ml = Meli(verbose=False)
    hasta = date.today() - timedelta(days=1)
    desde = hasta - timedelta(days=DIAS - 1)

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
    plan = publicidad.analizar(df, pubs)

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    log(f"\n{len(plan)} anuncios · gasto {pes(plan['gasto'].sum())} · "
        f"facturado {pes(plan['facturado'].sum())}")

    apagar = plan[(plan["accion"] == "pausar")
                  & (plan["gasto"] >= GASTO_MINIMO)
                  & (plan["ad_group_id"].notna())].copy()
    apagar = apagar.sort_values("gasto", ascending=False)

    if not len(apagar):
        log("Nada para apagar: todo dentro de los topes.")
        return 0

    if len(apagar) > TOPE_POR_CORRIDA:
        log(f"\n*** {len(apagar)} superan el tope de {TOPE_POR_CORRIDA} por "
            f"corrida. Se apagan los {TOPE_POR_CORRIDA} de mayor gasto; el "
            "resto queda para la próxima. ***")
        apagar = apagar.head(TOPE_POR_CORRIDA)

    log(f"\nA apagar: {len(apagar)} · gasto {pes(apagar['gasto'].sum())}")
    for _, r in apagar.iterrows():
        log(f"  {r['item_id']:<15} {pes(r['gasto']):>12}  "
            f"ACOS {r['acos']:>3.0f}%  {r['motivo'][:52]}")

    if not aplicar:
        log("\n(simulación: corré con --aplicar para apagarlos)")
        return 0

    if not panel_ads.hay_sesion():
        log("\nERROR: no hay sesión del panel cargada. Falta [ads] ssid en "
            "los secrets — sin eso no se puede escribir.")
        return 1

    res = panel_ads.aplicar(panel_ads.leer_sesion(), ml, apagar,
                            accion="pausar",
                            callback=lambda i, t, d: log(f"  {i}/{t} {d}"))
    ok = int((res["resultado"] == "OK").sum())
    log(f"\nApagados {ok} de {len(res)}.")

    # A la auditoria, que es donde queda como estaba antes.
    filas = [{
        "fecha": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "item_id": r["item_id"], "campo": "ad_status",
        "valor_anterior": "active", "valor_nuevo": "paused",
        "resultado": r["resultado"], "operador": "cron",
        "nota": str(r.get("motivo", ""))[:180],
    } for _, r in res.iterrows()]
    guardado, detalle = almacen.append_auditoria(filas)
    if not guardado:
        log(f"AVISO: no se pudo escribir la auditoría: {detalle}")

    if ok < len(res):
        log("\nLos que fallaron suelen ser benignos: anuncios en `hold` que "
            "ML deshabilitó, o que el listado trae desactualizados.")
    return 0


def publicidad_catalogo(ml):
    from catalogo import bajar_catalogo
    return bajar_catalogo(ml)


def main():
    return correr(aplicar="--aplicar" in sys.argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
