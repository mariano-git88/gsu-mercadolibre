#!/usr/bin/env python3
"""
Decide a que publicaciones hay que aplicarle un cambio, dado un SKU.

Este es el corazon de las herramientas de precio y stock: el catalogo de
SUPRABOND tiene muchas publicaciones espejo del mismo producto, asi que
"actualizar el SKU X" no es una sola publicacion.

Reglas acordadas:

  - El SKU que manda es el atributo SELLER_SKU (no seller_custom_field, que
    esta cargado en menos de la mitad del catalogo y tiene datos sucios).
  - Solo se tocan publicaciones ACTIVAS.
  - PRECIO: si entre las publicaciones del SKU hay mas de una condicion de
    financiacion (o sea, conviven gold_pro y gold_special), se tocan SOLO las
    gold_special (las que no ofrecen cuotas sin interes). Si son todas del
    mismo tipo, se tocan todas.
  - STOCK: se agrupa por user_product_id, porque las publicaciones que
    comparten ese id comparten el stock. Si el SKU cae en mas de un
    user_product_id, NO se toca y se marca para que decida el operador
    (aplicar el mismo numero a dos destinos duplicaria el stock).
  - Las publicaciones en Full quedan afuera del cambio de stock: ese stock lo
    controla ML segun lo que haya fisicamente en su deposito.
"""

from collections import defaultdict

from catalogo import es_full, sku_del_atributo

SIN_FINANCIACION = "gold_special"   # Clasica
CON_FINANCIACION = "gold_pro"       # Premium (cuotas sin interes)


def normalizar_sku(valor):
    """Los SKU vienen con espacios y mayusculas inconsistentes."""
    if valor is None:
        return ""
    return str(valor).strip().upper()


def indexar_por_sku(pubs, solo_activas=True):
    """Arma el diccionario SKU -> publicaciones."""
    indice = defaultdict(list)
    for p in pubs:
        if solo_activas and p.get("status") != "active":
            continue
        sku = normalizar_sku(sku_del_atributo(p))
        if sku:
            indice[sku].append(p)
    return dict(indice)


class Resolucion:
    """
    Que hacer con un SKU de la planilla.

    destinos : publicaciones a las que aplicar el cambio (puede ser vacia)
    estado   : 'ok' | 'no_encontrado' | 'ambiguo' | 'sin_destino'
    motivo   : explicacion para mostrarle al operador
    """

    def __init__(self, sku, destinos=None, estado="ok", motivo="", candidatas=None):
        self.sku = sku
        self.destinos = destinos or []
        self.estado = estado
        self.motivo = motivo
        self.candidatas = candidatas or []

    @property
    def ok(self):
        return self.estado == "ok" and bool(self.destinos)

    def __repr__(self):
        return f"<Resolucion {self.sku} {self.estado} destinos={len(self.destinos)}>"


def resolver_precio(sku, indice):
    """A que publicaciones aplicarle un precio nuevo."""
    sku = normalizar_sku(sku)
    pubs = indice.get(sku)

    if not pubs:
        return Resolucion(sku, estado="no_encontrado",
                          motivo="No hay ninguna publicacion activa con ese SKU.")

    if len(pubs) == 1:
        return Resolucion(sku, destinos=pubs, motivo="Publicacion unica.")

    tipos = {p.get("listing_type_id") for p in pubs}

    # Conviven condiciones de financiacion distintas: solo las que NO financian.
    if CON_FINANCIACION in tipos and SIN_FINANCIACION in tipos:
        sin_fin = [p for p in pubs if p.get("listing_type_id") == SIN_FINANCIACION]
        return Resolucion(
            sku, destinos=sin_fin, candidatas=pubs,
            motivo=(f"{len(pubs)} publicaciones con distinta financiacion: "
                    f"se actualizan las {len(sin_fin)} Clasicas y se dejan "
                    f"{len(pubs) - len(sin_fin)} Premium sin tocar."))

    # Todas iguales: no hay criterio para elegir, van todas.
    return Resolucion(
        sku, destinos=pubs, candidatas=pubs,
        motivo=f"{len(pubs)} publicaciones espejo del mismo tipo: se actualizan todas.")


def resolver_stock(sku, indice):
    """
    A que publicaciones aplicarle un stock nuevo.

    Ojo con la trampa: si el SKU vive en dos user_product_id distintos, poner
    el mismo numero en los dos DUPLICA el stock que ve MercadoLibre. En ese
    caso preferimos no tocar nada y avisar.
    """
    sku = normalizar_sku(sku)
    pubs = indice.get(sku)

    if not pubs:
        return Resolucion(sku, estado="no_encontrado",
                          motivo="No hay ninguna publicacion activa con ese SKU.")

    propias = [p for p in pubs if not es_full(p)]
    en_full = [p for p in pubs if es_full(p)]

    if not propias:
        return Resolucion(
            sku, estado="sin_destino", candidatas=pubs,
            motivo=("Todas las publicaciones de este SKU estan en Full: el stock "
                    "lo controla MercadoLibre segun lo que tenga en su deposito."))

    productos = defaultdict(list)
    for p in propias:
        productos[p.get("user_product_id")].append(p)

    if len(productos) > 1:
        detalle = ", ".join(f"{pid} ({len(ps)} pub.)" for pid, ps in productos.items())
        return Resolucion(
            sku, estado="ambiguo", candidatas=propias,
            motivo=(f"El SKU tiene {len(productos)} stocks separados en ML: {detalle}. "
                    "Aplicar el mismo numero a cada uno duplicaria el stock. "
                    "Definir a cual corresponde."))

    # Un solo destino de stock. Basta con actualizar una publicacion del grupo:
    # las que comparten user_product_id se actualizan juntas.
    grupo = list(productos.values())[0]
    aviso = ""
    if en_full:
        aviso = (f" ({len(en_full)} publicacion/es en Full quedan sin tocar, "
                 "ese stock lo maneja ML).")

    return Resolucion(
        sku, destinos=grupo[:1], candidatas=propias,
        motivo=(f"Stock unico compartido por {len(grupo)} publicacion/es." + aviso))


def resolver_lote(skus, indice, operacion="precio"):
    """Resuelve una lista de SKU. `operacion` es 'precio' o 'stock'."""
    fn = resolver_precio if operacion == "precio" else resolver_stock
    return [fn(s, indice) for s in skus]


if __name__ == "__main__":
    # Prueba con el catalogo cacheado: cuantos SKU caen en cada situacion.
    import json
    from pathlib import Path
    from collections import Counter

    ruta = Path(__file__).resolve().parent / "catalogo.json"
    pubs = json.loads(ruta.read_text(encoding="utf-8"))
    indice = indexar_por_sku(pubs)

    print(f"SKU activos indexados: {len(indice)}\n")

    for operacion in ("precio", "stock"):
        res = resolver_lote(list(indice), indice, operacion)
        print(f"--- {operacion.upper()}")
        for estado, n in Counter(r.estado for r in res).most_common():
            print(f"    {estado:<16} {n:>5}")
        tocadas = sum(len(r.destinos) for r in res)
        print(f"    publicaciones que se tocarian: {tocadas}\n")
