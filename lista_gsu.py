#!/usr/bin/env python3
"""
El piso de precio de las marcas propias: Costo x 1,85.

    python lista_gsu.py            -> cobertura y cuantas publicaciones lo violan
    python lista_gsu.py --refrescar

**La regla, en criollo.** Una publicacion de **Suprabond, Bulit o Somerset**
no se puede vender por debajo de **1,85 veces el precio de lista de venta de
Contabilium**. No es una cuenta de margen: es una decision comercial, y por eso
es un piso duro — ninguna pantalla sugiere ni aplica un precio por debajo,
aunque el margen aguante y aunque haga falta para ganar el Buy Box.

**"Costo" aca es el precio de la lista de venta de Contabilium**, o sea el
`PrecioFinal` del concepto: lo que Suprabond le cobra al comercio. No es el
`CostoInterno` del ERP, que esta vacio (ver `costos_gsu.py`), ni el costo de
fabricacion. Ejemplo medido el 04/08/2026: `CDB AR 80 P` esta en $328, asi que
su piso es $606,80.

**El cruce es directo.** El SKU de MercadoLibre (`SELLER_SKU`) y el `Codigo`
de Contabilium son el mismo string; no hay que transformarlos, al reves que en
la version argentina, donde los codigos no compartian ni un caracter. Medido:
de las 419 publicaciones activas de las tres marcas, **410 cruzan (98%)**. Las
9 que no son packs y variantes (`... X 5 UNIDADES`, `CTA MCA5B 3`) que no
existen con ese codigo en el ERP.

**La marca sale de MercadoLibre, no de Contabilium.** El atributo `BRAND` de
la publicacion ya trae exactamente las tres marcas, y Contabilium no tiene un
campo de marca utilizable (los endpoints de rubros contestan 404). Reparto
medido sobre 438 activas: Bulit 245, Suprabond 171, Somerset 2, "Suprabond
Somerset" 1, y 19 de reventa (Bosch, Aqualaf, Dremel y 4 sin marca) que
**quedan fuera de la regla** a proposito.

Configuracion, en secrets:

    [contabilium]
    client_id = "el-email-del-usuario-admin"
    client_secret = "la-API-Key"

Si no esta configurado, `traer_pisos()` devuelve vacio y las pantallas siguen
andando igual que antes, sin piso de marca.
"""

import json
import sys
import time
from pathlib import Path

import requests

import almacen
from catalogo import sku_del_atributo

DIR = Path(__file__).resolve().parent
CACHE = DIR / "lista_gsu.json"

# La cuenta de Suprabond es la uruguaya: la URL base va con .com.uy. Los
# requests pasan por Cloudflare, que contesta 403 sin User-Agent.
BASE = "https://rest.contabilium.com.uy"
USER_AGENT = "GSU-MercadoLibre/1.0"

MULTIPLICADOR = 1.85

# Las marcas propias, como las escribe el atributo BRAND de MercadoLibre.
# "Suprabond Somerset" es una sola publicacion y entra igual.
MARCAS_PROPIAS = {"SUPRABOND", "BULIT", "SOMERSET", "SUPRABOND SOMERSET"}

# El precio de lista cambia poco; bajarlo son ~7 llamadas.
VIGENCIA_HORAS = 12


class ListaError(RuntimeError):
    pass


def configurado():
    cfg = almacen._seccion("contabilium")
    return bool(cfg.get("client_id") and cfg.get("client_secret"))


def marca_de(pub):
    """La marca de la publicacion, como la escribe MercadoLibre."""
    for a in pub.get("attributes") or []:
        if a.get("id") == "BRAND":
            return a.get("value_name")
    return None


def es_marca_propia(pub):
    return str(marca_de(pub) or "").strip().upper() in MARCAS_PROPIAS


# ------------------------------------------------------------ Contabilium

def _token():
    cfg = almacen._seccion("contabilium")
    if not configurado():
        raise ListaError(
            "Falta la seccion [contabilium] en los secrets, con client_id "
            "(el email del usuario admin) y client_secret (la API Key).")
    r = requests.post(
        f"{BASE}/token",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": USER_AGENT},
        data={"grant_type": "client_credentials",
              "client_id": cfg["client_id"],
              "client_secret": cfg["client_secret"]},
        timeout=60)
    if r.status_code != 200:
        raise ListaError(
            f"Contabilium rechazo las credenciales (HTTP {r.status_code}): "
            f"{r.text[:200]}. El client_id es el email del usuario "
            "administrador y el client_secret es la API Key, no la clave.")
    return r.json()["access_token"]


def _bajar_conceptos():
    """Todos los productos del ERP, paginando."""
    token = _token()
    cabeceras = {"Authorization": f"Bearer {token}",
                 "User-Agent": USER_AGENT}
    salida, pagina = [], 1
    while True:
        r = requests.get(f"{BASE}/api/conceptos/search",
                         headers=cabeceras, params={"page": pagina},
                         timeout=60)
        if r.status_code != 200:
            raise ListaError(f"GET /api/conceptos/search pagina {pagina} -> "
                             f"HTTP {r.status_code}: {r.text[:200]}")
        cuerpo = r.json()
        items = cuerpo.get("Items") or []
        if not items:
            break
        salida += items
        total = cuerpo.get("TotalPage") or 1
        if pagina >= total:
            break
        pagina += 1
        # Contabilium corta por rate limit en syncs seguidos.
        time.sleep(0.3)
    return salida


def traer_lista(refrescar=False):
    """
    {SKU: precio_de_lista} desde Contabilium, cacheado en disco.

    Solo entran los que tienen precio mayor a cero: un producto en $0 no da
    un piso, da un piso de cero, que es peor que no tener piso.
    """
    if CACHE.exists() and not refrescar:
        datos = json.loads(CACHE.read_text(encoding="utf-8"))
        if time.time() - datos.get("cuando", 0) < VIGENCIA_HORAS * 3600:
            return datos["precios"]

    if not configurado():
        return {}

    conceptos = _bajar_conceptos()
    precios = {}
    for c in conceptos:
        codigo = str(c.get("Codigo") or "").strip().upper()
        precio = c.get("PrecioFinal") or c.get("Precio") or 0
        if codigo and float(precio) > 0:
            precios[codigo] = float(precio)

    CACHE.write_text(json.dumps({"cuando": time.time(), "precios": precios},
                                ensure_ascii=False), encoding="utf-8")
    return precios


# ------------------------------------------------------------------ pisos

def piso_de(sku, marca, precios):
    """
    El piso de una publicacion, o None si no le corresponde.

    Devuelve None en dos casos distintos que conviene no confundir: que la
    marca no sea propia (no hay regla) y que el SKU no este en la lista (hay
    regla pero no se puede calcular). Quien llama distingue con `es_propia`.
    """
    if str(marca or "").strip().upper() not in MARCAS_PROPIAS:
        return None
    lista = precios.get(str(sku or "").strip().upper())
    if not lista:
        return None
    return round(float(lista) * MULTIPLICADOR, 2)


def traer_pisos(pubs, refrescar=False):
    """
    {item_id: piso} para las publicaciones que tienen regla y se pudo calcular.

    Es lo que consumen las pantallas: con el item_id alcanza para cortar
    cualquier sugerencia de precio.
    """
    precios = traer_lista(refrescar=refrescar)
    if not precios:
        return {}
    pisos = {}
    for p in pubs:
        piso = piso_de(sku_del_atributo(p), marca_de(p), precios)
        if piso:
            pisos[p["id"]] = piso
    return pisos


def pisos_por_sku(pubs, refrescar=False):
    """
    {SKU: piso} para las pantallas que trabajan por SKU y no por publicacion.

    Plata sobre la mesa y Precios razonan por SKU, porque el precio se aplica
    a todas las publicaciones del producto. Si dos publicaciones del mismo SKU
    dieran pisos distintos se toma el mas alto: el piso es un minimo, y
    quedarse con el menor lo perforaria en la otra.
    """
    precios = traer_lista(refrescar=refrescar)
    if not precios:
        return {}
    pisos = {}
    for p in pubs:
        sku = str(sku_del_atributo(p) or "").strip().upper()
        piso = piso_de(sku, marca_de(p), precios)
        if piso and piso > pisos.get(sku, 0):
            pisos[sku] = piso
    return pisos


def cobertura(pubs, refrescar=False):
    """Cuantas publicaciones tienen regla, cuantas cruzan y cuantas la violan."""
    precios = traer_lista(refrescar=refrescar)
    activas = [p for p in pubs if p.get("status") == "active"]
    propias = [p for p in activas if es_marca_propia(p)]
    cruzan, sin_cruce, violan = [], [], []
    for p in propias:
        piso = piso_de(sku_del_atributo(p), marca_de(p), precios)
        if piso is None:
            sin_cruce.append(p)
            continue
        cruzan.append(p)
        if float(p.get("price") or 0) < piso:
            violan.append((p, piso))
    return {
        "activas": len(activas),
        "de_marca_propia": len(propias),
        "cruzan": len(cruzan),
        "sin_cruce": sin_cruce,
        "violan": violan,
        "en_lista": len(precios),
    }


def main():
    refrescar = "--refrescar" in sys.argv
    if not configurado():
        print("\nNo hay [contabilium] en los secrets. Sin eso no hay piso de "
              "marca y las pantallas siguen andando sin el.\n")
        return 1

    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    c = cobertura(pubs, refrescar=refrescar)

    print("=" * 66)
    print(f"PISO DE MARCA — Costo x {MULTIPLICADOR}")
    print("=" * 66)
    print(f"  Productos con precio en Contabilium  {c['en_lista']:>5}")
    print(f"  Publicaciones activas                {c['activas']:>5}")
    print(f"  De marca propia (aplica la regla)    {c['de_marca_propia']:>5}")
    print(f"  Con piso calculable                  {c['cruzan']:>5}")
    print(f"  Sin cruce con el ERP                 {len(c['sin_cruce']):>5}")

    if c["sin_cruce"]:
        print("\n  No cruzan (no existe ese codigo en Contabilium):")
        for p in c["sin_cruce"][:12]:
            print(f"    {str(sku_del_atributo(p)):<26} "
                  f"{(p.get('title') or '')[:42]}")

    print(f"\n  POR DEBAJO DEL PISO: {len(c['violan'])} de {c['cruzan']}")
    for p, piso in sorted(c["violan"],
                          key=lambda x: (x[0].get("price") or 0) / x[1])[:15]:
        precio = float(p.get("price") or 0)
        print(f"    {str(sku_del_atributo(p)):<20} hoy ${precio:>9,.2f}  "
              f"piso ${piso:>9,.2f}  ({piso / precio - 1:+.0%})  "
              f"{(p.get('title') or '')[:30]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ListaError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
