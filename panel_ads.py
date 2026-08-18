#!/usr/bin/env python3
"""
Aplica cambios de publicidad por el **endpoint interno del panel**, que es lo
unico que hoy acepta escritura.

    python panel_ads.py                      -> que haria, sin tocar nada
    python panel_ads.py --aplicar            -> pausa lo que marcan las reglas
    python panel_ads.py --activar ID [ID...] -> vuelve a encender ad_groups

**Por que existe esto.** La API publica de Product Ads rechaza toda escritura
para esta cuenta y esta app (`401 User does not have permission to write`, ver
`publicidad.py`). El panel de MercadoLibre si puede, porque **no usa la API
publica**: usa

    PUT https://pa.mercadolibre.<dominio>/pa/api/admin-pads/ajax/ads/actions/status
    {"ids": ["<ad_group_id>", ...], "allSelected": false, "status": "paused"}

con **cookies de sesion** y el header `x-csrf-token`. No hay OAuth.

**Corre solo en una maquina con sesion.** No sirve desde Streamlit Cloud ni
desde GitHub Actions: no hay navegador ni cookies ahi. Es "abro la compu y
aplico", no automatico. Y se rompe cuando ML cambie el panel.

**La sesion se saca del navegador y vence.** En el panel de Publicidad: F12 ->
Network -> pausar cualquier anuncio -> click derecho en la llamada ->
Copy as cURL. De ahi salen `cookie` y `x-csrf-token`, que van a
`sesion_ads.json` (ignorado por git; **nunca commitear esto**).

    {"cookie": "orgnickp=...; ssid=...; _csrf=...",
     "csrf": "Io2QW8bu-..."}

Dos cosas que costaron encontrar:

  - **El lote falla si mezcla campanas o anunciantes.** Mandar 11 ids de dos
    anunciantes devuelve 400 para todos. Hay que agrupar y mandar el referer
    de la campana correspondiente.
  - **El anunciante viaja en una cookie**, `_ma_dsp_account-structure`. La
    sesion queda fijada al que estabas mirando; para tocar otro hay que
    reescribirla.
"""

import json
import sys
import time
import urllib.parse
from pathlib import Path

import pandas as pd

import publicidad
from meli import Meli, SITE_ID

DIR = Path(__file__).resolve().parent
SESION = DIR / "sesion_ads.json"

# El panel vive en un dominio por pais; se deriva del sitio de meli.py.
DOMINIO = {"MLA": "com.ar", "MLU": "com.uy", "MLB": "com.br",
           "MLC": "cl", "MLM": "com.mx"}.get(SITE_ID, "com")

BASE_PANEL = f"https://pa.mercadolibre.{DOMINIO}"
BASE_ADS = f"https://ads.mercadolibre.{DOMINIO}"
URL = f"{BASE_PANEL}/pa/api/admin-pads/ajax/ads/actions/status"
URL_SACAR = f"{BASE_PANEL}/pa/api/admin-pads/ajax/ads/actions/remove-from-campaign"
COOKIE_ADV = "_ma_dsp_account-structure"
SITIO = "MLA"

# El endpoint de sacar de campana no aguanta lotes grandes: 5 entra, 10 no.
LOTE_SACAR = 5

# Cuantos ad_groups por llamada segun la accion.
LOTES = {"sacar": LOTE_SACAR, "agregar": 5, "pausar": 50, "activar": 50}


def leer_sesion():
    """
    La sesion del panel. Sale de los secrets si estan (asi corre en la nube) y
    si no del archivo local.

    **Alcanza con la cookie `ssid`.** Medido el 2026-08-05: sin `_csrf`, sin
    `nsa_rotok`, sin el header `x-csrf-token` y sin `x-requested-with`
    funciona igual. Y el `ssid` trae fecha 2029, asi que se pega una vez y
    dura — que es lo que permite correr esto desde Streamlit Cloud en vez de
    depender de una sesion de navegador abierta.

    En secrets va como:

        [ads]
        ssid = "ghy-073112-...-__-422682314-__-..."
    """
    import almacen
    desde_secrets = almacen._seccion("ads")
    if desde_secrets.get("ssid"):
        return {"ssid": desde_secrets["ssid"]}

    if SESION.exists():
        d = json.loads(SESION.read_text(encoding="utf-8"))
        if d.get("ssid"):
            return {"ssid": d["ssid"]}
        # Compatibilidad: si guardaron la cookie entera, se extrae el ssid.
        for trozo in (d.get("cookie") or "").split("; "):
            if trozo.startswith("ssid="):
                return {"ssid": trozo[5:]}

    raise SystemExit(
        "Falta la sesión del panel. Sacá la cookie `ssid` (F12 → Application "
        "→ Cookies en ads.mercadolibre." + DOMINIO + ") y ponela en los "
        "secrets bajo "
        f'[ads] ssid = "..." o en {SESION.name} como {{"ssid": "..."}}')


def hay_sesion():
    """Si se puede escribir por el panel, sin lanzar."""
    try:
        return bool(leer_sesion().get("ssid"))
    except SystemExit:
        return False


def _cookies(sesion, advertiser_id):
    """
    El anunciante viaja en su propia cookie y **la sesion queda fijada al que
    estabas mirando en el panel**: con la de un anunciante, lo de otro
    contesta 400. Se arma a mano para poder tocar cualquiera.
    """
    adv = urllib.parse.quote(
        json.dumps({"advertiserId": str(advertiser_id), "accountId": "645"},
                   separators=(",", ":")), safe="")
    return f"ssid={sesion['ssid']}; {COOKIE_ADV}={adv}"


def _headers(sesion, advertiser_id, campaign_id):
    ref = (f"{BASE_ADS}/product-ads/admin/campaigns/"
           f"{campaign_id}/dashboard")
    return {"accept": "application/json",
            "content-type": "application/json",
            "cookie": _cookies(sesion, advertiser_id),
            "origin": BASE_ADS,
            "referer": ref, "x-pads-page-href": ref,
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/150.0 Safari/537.36")}


def _resultado(r):
    if r.status_code in (401, 403):
        return [], [{"error": "la sesión venció: volvé a copiar el ssid"}]
    try:
        j = r.json()
    except ValueError:
        return [], [{"error": f"HTTP {r.status_code}: {r.text[:150]}"}]
    return j.get("succeededIds") or [], j.get("failed") or []


def cambiar(sesion, ad_group_ids, advertiser_id, campaign_id, estado):
    """
    Prende o apaga ad_groups **de la misma campana**. (ok_ids, fallidos).
    """
    import requests
    try:
        r = requests.put(
            URL, headers=_headers(sesion, advertiser_id, campaign_id),
            json={"ids": [str(i) for i in ad_group_ids],
                  "allSelected": False, "status": estado}, timeout=90)
    except Exception as e:
        return [], [{"error": f"{type(e).__name__}: {e}"}]
    return _resultado(r)


def sacar(sesion, ad_group_ids, advertiser_id, campaign_id):
    """
    Saca ad_groups de su campana: quedan en IDLE, campaign 0.

    **De a 5 como mucho.** Medido: lotes de 10 en adelante devuelven 400 para
    todos los del lote. Es el limite del endpoint, no un problema de permisos
    — el que llama tiene que trocear.

    **Muchas fallas son benignas y hay que leerlas asi.** Un anuncio en `hold`
    no se puede sacar de la campana (lo deshabilito ML), uno ya en `idle` no
    tiene de donde salir, y el listado de `ads/search` devuelve ad_groups que
    ya no existen. En una corrida de 600 fallaron 360 y casi todas eran de
    esas tres clases: el listado estaba viejo, no habia nada roto.
    """
    import requests
    try:
        r = requests.put(
            URL_SACAR, headers=_headers(sesion, advertiser_id, campaign_id),
            json={"ids": [str(i) for i in ad_group_ids],
                  "campaignId": int(campaign_id)}, timeout=120)
    except Exception as e:
        return [], [{"error": f"{type(e).__name__}: {e}"}]
    return _resultado(r)


def agregar(sesion, ad_group_ids, advertiser_id, campaign_id):
    """
    Suma ad_groups a una campana. **Entran activos**: empiezan a gastar.

    OJO: esta accion va por **otro servicio** que las demas
    (`admin-growth-campaigns` en vez de `admin-pads`), es **POST** en vez de
    PUT, lleva el **anunciante en el path**, y el campo se llama `adGroups`
    en camelCase — no `ids` como las otras tres. Nada de eso se deduce: sale
    de mirar la llamada que hace el panel.
    """
    import requests
    url = (f"{BASE_PANEL}/pa/api/admin-growth-campaigns/rest/campaigns/"
           f"{SITIO}/{advertiser_id}/ad-groups")
    try:
        r = requests.post(
            url, headers=_headers(sesion, advertiser_id, campaign_id),
            json={"campaignId": int(campaign_id),
                  "adGroups": [str(i) for i in ad_group_ids]}, timeout=120)
    except Exception as e:
        return [], [{"error": f"{type(e).__name__}: {e}"}]
    if r.status_code in (401, 403):
        return [], [{"error": "la sesión venció: volvé a copiar el ssid"}]
    if r.status_code >= 400:
        return [], [{"error": f"HTTP {r.status_code}: {r.text[:150]}"}]
    # Este servicio no devuelve succeededIds: si contesta 2xx, entraron.
    return [str(i) for i in ad_group_ids], []


def campana(sesion, advertiser_id, campaign_id, cambios):
    """
    Modifica una campana: `{"status": "paused"}` o
    `{"budget": 110000, "automaticBudget": False}`. Va por PATCH.
    """
    import requests
    try:
        r = requests.patch(
            f"{BASE_PANEL}/pa/api/admin-pads/ajax/campaigns/{campaign_id}",
            headers=_headers(sesion, advertiser_id, campaign_id),
            json=cambios, timeout=90)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:150]}"
    return True, r.text[:200]


def crear_campana(sesion, advertiser_id, nombre, presupuesto, acos_objetivo,
                  estado="paused", strategy="profitability"):
    """
    Crea una campana. Devuelve (ok, id_o_detalle).

    **Nace pausada salvo que se pida lo contrario**: una campana con
    presupuesto empieza a gastar apenas se activa, y eso es una decision
    aparte de crearla.

    Va por el mismo servicio que el alta de anuncios
    (`admin-growth-campaigns`), no por `admin-pads`. Si falta un campo, la API
    contesta **400 con la lista de los que faltan** — no hay que adivinar
    ninguno.

    Ojo con el objetivo: aca el campo es `acosTarget`, pero **al modificar una
    campana existente es `roasTarget`**. No son consistentes entre si.
    """
    import requests
    url = (f"{BASE_PANEL}/pa/api/admin-growth-campaigns/rest/campaigns/"
           f"{SITIO}/{advertiser_id}")
    cuerpo = {"name": nombre, "budget": float(presupuesto),
              "status": estado, "strategy": str(strategy).lower(),
              "acosTarget": float(acos_objetivo), "acosTopSearchTarget": 0,
              "automaticBudget": False, "channel": "marketplace"}
    try:
        r = requests.post(url, headers=_headers(sesion, advertiser_id, ""),
                          json=cuerpo, timeout=90)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:250]}"
    return True, (r.json() or {}).get("id")


def estado_real(ml, ad_group_id):
    """
    El estado que vale. **`ads/{item_id}` viene atrasado**: despues de pausar
    sigue diciendo `active` un buen rato, y hace creer que la escritura no
    entro. `ad_groups/{id}` contesta la verdad (en mayusculas: PAUSED).
    """
    try:
        g = ml.get(publicidad._ruta_ad_group(ad_group_id),
                   _headers=publicidad.CABECERA)
        return (g or {}).get("status")
    except Exception:
        return None


def despertar_campanas(sesion, ml, plan, callback=None):
    """
    Prende las campanas pausadas a las que el plan va a sumar anuncios.

    Sumar una publicacion a una campana pausada no sirve de nada: el anuncio
    entra activo pero la campana no corre, asi que no se muestra ni gasta.

    **Prender una campana enciende TODO lo que ya tiene adentro**, no solo lo
    que estas por agregar. En Argentina una campana general tenia 4.557,
    ~1.550 en estado corrible: prenderla es empezar a gastar en todos ellos
    con un tope de $78.859, no sumar 24. Por eso devuelve el detalle de lo que
    prendio y quien llama tiene que mostrarlo.
    """
    prendidas = []
    for (adv, camp), _ in plan.groupby(["advertiser_id", "campaign_id"]):
        try:
            c = ml.get(publicidad._ruta_campana(int(camp)),
                       _headers=publicidad.CABECERA)
        except Exception:
            continue
        if (c or {}).get("status") == "active":
            continue
        if callback:
            callback(f"Prendiendo la campaña «{c.get('name')}»...")
        ok, detalle = campana(sesion, adv, int(camp), {"status": "active"})
        prendidas.append({"campaign_id": int(camp),
                          "nombre": c.get("name"),
                          "presupuesto": c.get("budget"),
                          "resultado": "OK" if ok else str(detalle)[:150]})
    return prendidas


OBJETIVO = {"pausar": "PAUSED", "activar": "ACTIVE"}


def resolver_para_escribir(ml, plan, accion="pausar", callback=None):
    """
    El `ad_group` que hay que tocar de verdad. Devuelve (plan, descartes).

    **El plan sale de `ads/search`, que viene atrasado.** El estado que trae
    no es el de hoy: se vieron anuncios listados como `delegated` que al
    preguntarle a `ad_groups/{id}` estaban IDLE, HOLD o directamente ya no
    existian. Mandar eso al panel devuelve errores que parecen un problema de
    sesion y son el listado viejo.

    Por eso aca se le pregunta a ML de nuevo, anuncio por anuncio:

      - `ads/{item_id}` da el ad_group de la campana donde el anuncio corre
      - `ad_groups/{id}` da el estado y la campana **de hoy**

    Son dos llamadas por anuncio, pero es justo el dato que el listado tiene
    atrasado. Lo que no se puede tocar sale por `descartes` con el motivo.

    Portado de CRAFTERS (18/08/2026), donde el mismo problema devolvia 409 en
    856 de 1.104 escrituras. Ver `hermanos_arrastrados` alla y
    `publicidad.proteger_companeros()` aca: son la misma familia de cuidados.
    """
    import publicidad

    filas, descartes, total = [], [], len(plan)

    def descartar(f, motivo, ag=None):
        # Se pisa el ad_group con el que ML dice hoy: dejar el del plan hacia
        # que el descarte hablara de un anuncio distinto al que se miro.
        d = f.to_dict()
        if ag is not None:
            d["ad_group_id"] = int(ag)
        d["descarte"] = motivo
        descartes.append(d)

    for n, (_, f) in enumerate(plan.iterrows(), 1):
        if callback and (n % 10 == 0 or n == total):
            callback(n, total, "preguntandole a ML por cada anuncio")

        item = f.get("item_id")
        ad = publicidad.leer_ad(ml, item) if item else None
        ag = (ad or {}).get("ad_group_id") or f.get("ad_group_id")
        if not ag:
            descartar(f, "no tiene anuncio en ML")
            continue

        try:
            g = ml.get(publicidad._ruta_ad_group(ag),
                       _headers=publicidad.CABECERA)
        except Exception:
            # `ads/search` devuelve ad_groups que ya no existen.
            descartar(f, f"ML ya no reconoce el ad_group {ag}", ag)
            continue

        estado = str((g or {}).get("status") or "").upper()
        camp = int((g or {}).get("campaign_id") or 0)
        adv = (g or {}).get("advertiser_id") or f.get("advertiser_id")

        if not camp:
            descartar(f, "no esta en ninguna campana: lo administra ML "
                         "(delegado). Hay que sacarlo desde el panel.", ag)
            continue
        if estado == OBJETIVO.get(accion):
            descartar(f, f"ya estaba {estado.lower()}", ag)
            continue
        if estado == "HOLD":
            descartar(f, "ML lo deshabilito (hold)", ag)
            continue

        nueva = f.to_dict()
        nueva.update(ad_group_id=int(ag), campaign_id=camp,
                     advertiser_id=int(adv), estado_previo=estado)
        filas.append(nueva)

    cols = plan.columns.tolist() + ["estado_previo"]
    return (pd.DataFrame(filas) if filas else pd.DataFrame(columns=cols),
            pd.DataFrame(descartes))


# Que estados de hermana importan segun lo que se va a hacer. Apagando, el
# riesgo es tumbar a una que **corre**; encendiendo, es prender a una que
# esta **quieta** y ponerla a gastar. Mirar la lista equivocada no avisa nada.
ARRASTRE = {"pausar": ("active", "delegated"),
            "agregar": ("idle", "paused"),
            "activar": ("idle", "paused")}


def hermanos_arrastrados(ml, plan, estados=("active", "delegated"),
                         callback=None):
    """
    Las publicaciones que se van a apagar **de arrastre**, sin estar en el plan.

    **Un ad_group no es una publicacion: es una familia.** El estado vive en
    el ad_group, asi que tocar uno toca a todas las publicaciones que viven
    adentro. Medido en esta cuenta el 18/08/2026: de **326 ad_groups, 53
    tienen mas de una publicacion** y el mayor agrupa 8.

    Hermana de `publicidad.proteger_companeros()`, que hace lo mismo sin
    llamadas usando el listado que ya esta en memoria. Se usa esta cuando el
    listado no alcanza: `ads/search` **no devuelve los anuncios sin actividad
    en la ventana**, que son justo los candidatos a sumar.
    """
    import collections
    import publicidad

    if not len(plan) or "ad_group_id" not in plan:
        return pd.DataFrame()

    quiero = {int(a) for a in plan["ad_group_id"].dropna()}
    en_plan = {str(i) for i in plan.get("item_id", pd.Series(dtype=str))}
    advs = sorted({int(a) for a in plan["advertiser_id"].dropna()})

    fuera = []
    for adv in advs:
        if callback:
            callback(f"Buscando hermanos en el anunciante {adv}...")
        base = publicidad.BASE.format(site=publicidad.SITE_ID, adv=adv)
        off = 0
        while True:
            try:
                r = ml.get(f"{base}/ads/search", _headers=publicidad.CABECERA,
                           limit=50, offset=off)
            except Exception:
                break
            res = r.get("results") or []
            if not res:
                break
            for a in res:
                ag = a.get("ad_group_id")
                if (ag and int(ag) in quiero
                        and str(a.get("item_id")) not in en_plan):
                    fuera.append({
                        "ad_group_id": int(ag),
                        "item_id": a.get("item_id"),
                        "titulo": (a.get("title") or "")[:60],
                        "estado_ad": a.get("status"),
                        "anunciante": adv})
            off += 50

    df = pd.DataFrame(fuera)
    # `hold` y `deleted` no se mueven en ningun sentido: contarlos como danio
    # colateral asusta con algo que no pasa.
    if len(df):
        df = df[df["estado_ad"].isin(estados)]
    return df.reset_index(drop=True)


def aplicar(sesion, ml, plan, accion="pausar", callback=None, verificar=True):
    """
    Aplica `accion` ('pausar' / 'activar' / 'sacar') sobre el plan.

    **Agrupa por (anunciante, campana) y trocea.** Mezclar campanas o
    anunciantes en un mismo lote devuelve 400 para todos, no solo para los
    que no corresponden.
    """
    salida = []
    faltan = plan[plan["ad_group_id"].notna()]
    # **Un ad_group va una sola vez.** Como agrupa una familia entera, dos
    # publicaciones del plan pueden compartirlo —en esta cuenta 53 de 326
    # ad_groups tienen mas de una, y el mayor agrupa 8—; mandarlo repetido en
    # el mismo lote es pedir dos veces lo mismo. El resultado despues se
    # reparte a todas las filas que lo comparten.
    total = faltan["ad_group_id"].nunique()
    hechos = 0

    for (adv, camp), g in faltan.groupby(["advertiser_id", "campaign_id"]):
        ids = sorted({int(x) for x in g["ad_group_id"]})
        por_lote = LOTES.get(accion, 50)
        for i in range(0, len(ids), por_lote):
            lote = ids[i:i + por_lote]
            if accion == "sacar":
                ok, fallidos = sacar(sesion, lote, adv, camp)
            elif accion == "agregar":
                ok, fallidos = agregar(sesion, lote, adv, camp)
            else:
                estado = "paused" if accion == "pausar" else "active"
                ok, fallidos = cambiar(sesion, lote, adv, camp, estado)

            # **El error se mapea por id, no se copia del primero.** Antes se
            # tomaba `fallidos[0]` y se pegaba igual en todas las filas del
            # lote, asi que la tabla repetia el mismo texto y no se podia
            # saber por que habia fallado cada uno. Cuando el fallo es del
            # lote entero (un HTTP 4xx, la sesion vencida) no hay ids y ahi
            # si vale para todos, pero se dice que es del lote.
            por_id, del_lote = {}, ""
            for f in (fallidos or []):
                msg = f.get("message") or f.get("error") or ""
                fid = f.get("id") or f.get("adGroupId") or f.get("ad_group_id")
                if fid is not None:
                    por_id[str(fid)] = msg
                elif not del_lote:
                    del_lote = f"todo el lote: {msg}" if msg else ""
            for ag in lote:
                bien = str(ag) in ok
                # Una sola lectura por ad_group aunque lo compartan varias
                # publicaciones: es una llamada a ML, no sale gratis.
                real = estado_real(ml, ag) if verificar and bien else ""
                for _, fila in g[g["ad_group_id"] == ag].iterrows():
                    salida.append({
                        "item_id": fila.get("item_id"), "ad_group_id": ag,
                        "titulo": fila.get("titulo", ""),
                        "gasto": fila.get("gasto", 0),
                        "motivo": fila.get("motivo", ""),
                        "resultado": "OK" if bien else "ERROR",
                        "detalle": "" if bien else str(
                            por_id.get(str(ag))
                            or del_lote
                            or "el panel no lo acepto y no dijo por que")[:200],
                        "estado_real": real,
                    })
            hechos += len(lote)
            if callback:
                callback(hechos, total, f"anunciante {adv}, campaña {camp}")
            time.sleep(1.5)
    return pd.DataFrame(salida)


def main():
    ml = Meli(verbose=False)
    if "--activar" in sys.argv:
        sesion = leer_sesion()
        ids = [a for a in sys.argv[sys.argv.index("--activar") + 1:]
               if a.isdigit()]
        for ag in ids:
            g = ml.get(publicidad._ruta_ad_group(ag),
                       _headers=publicidad.CABECERA)
            ok, fall = cambiar(sesion, [ag], g.get("advertiser_id"),
                               g.get("campaign_id"), "active")
            print(f"  {ag}: {'OK' if ok else fall}")
        return 0

    ruta = DIR / "publicidad_a_pausar.csv"
    if not ruta.exists():
        print("Falta publicidad_a_pausar.csv: generalo desde la app "
              "(Publicidad → Qué haría con los anuncios).")
        return 1
    plan = pd.read_csv(ruta)
    plan = plan[plan.get("gasto", 0) > 0]
    print(f"{len(plan)} anuncios con gasto para pausar.")
    if "--aplicar" not in sys.argv:
        print("Corré con --aplicar para hacerlo.")
        return 0

    res = aplicar(leer_sesion(), ml, plan,
                  callback=lambda m: print(f"  {m}"))
    print(res.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
