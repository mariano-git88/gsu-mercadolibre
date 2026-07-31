#!/usr/bin/env python3
"""
Baja el catalogo completo de publicaciones y lo deja cacheado en catalogo.json.

    python catalogo.py            -> usa el cache si existe
    python catalogo.py --refrescar -> vuelve a bajar todo

Son ~3700 publicaciones, o sea unas 190 llamadas: tarda un par de minutos.
Por eso se cachea. Cualquier herramienta que necesite el catalogo usa:

    from catalogo import cargar_catalogo
    pubs = cargar_catalogo(ml)
"""

import json
import sys
from pathlib import Path

from meli import Meli, MeliError

CACHE = Path(__file__).resolve().parent / "catalogo.json"

CAMPOS = ["id", "title", "price", "base_price", "original_price",
          "available_quantity", "sold_quantity", "status", "sub_status",
          "listing_type_id", "seller_custom_field", "attributes",
          "variations", "shipping", "category_id", "catalog_listing",
          "catalog_product_id", "permalink", "date_created", "last_updated",
          # Claves para agrupar: las publicaciones que comparten user_product_id
          # comparten el stock. inventory_id aparece cuando esta en Full.
          "user_product_id", "inventory_id"]


def logistica(pub):
    return (pub.get("shipping") or {}).get("logistic_type")


def es_full(pub):
    """En Full el stock lo maneja ML en su deposito, no se toca igual que el propio."""
    return logistica(pub) == "fulfillment"


def sku_del_atributo(pub):
    """El SKU cargado como atributo SELLER_SKU."""
    for a in pub.get("attributes") or []:
        if a.get("id") == "SELLER_SKU":
            return a.get("value_name")
    return None


def bajar_catalogo(ml):
    print("Trayendo IDs de publicaciones...")
    ids = list(ml.scan_items())
    print(f"  {len(ids)} publicaciones. Bajando detalle...")

    pubs, hechas = [], 0
    for pub in ml.items_detalle(ids, atributos=CAMPOS):
        pubs.append(pub)
        hechas += 1
        if hechas % 500 == 0:
            print(f"  {hechas}/{len(ids)}...")

    print(f"  Listo: {len(pubs)} publicaciones.")
    CACHE.write_text(json.dumps(pubs, ensure_ascii=False), encoding="utf-8")
    return pubs


def cargar_catalogo(ml, refrescar=False):
    if CACHE.exists() and not refrescar:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return bajar_catalogo(ml)


def main():
    refrescar = "--refrescar" in sys.argv
    ml = Meli(verbose=False)
    pubs = cargar_catalogo(ml, refrescar=refrescar)

    # ------------------------------------------------ radiografia del catalogo
    from collections import Counter, defaultdict

    print("\n" + "=" * 64)
    print(f"CATALOGO: {len(pubs)} publicaciones")
    print("=" * 64)

    print("\nPor estado:")
    for st, n in Counter(p.get("status") for p in pubs).most_common():
        print(f"  {st:<16} {n:>6}")

    print("\nPor tipo de publicacion:")
    for lt, n in Counter(p.get("listing_type_id") for p in pubs).most_common():
        print(f"  {lt:<16} {n:>6}")

    print("\nDonde esta cargado el SKU:")
    con_scf = sum(1 for p in pubs if p.get("seller_custom_field"))
    con_attr = sum(1 for p in pubs if sku_del_atributo(p))
    ninguno = sum(1 for p in pubs
                  if not p.get("seller_custom_field") and not sku_del_atributo(p))
    coinciden = sum(1 for p in pubs
                    if p.get("seller_custom_field") and sku_del_atributo(p)
                    and p["seller_custom_field"].strip() == sku_del_atributo(p).strip())
    distintos = sum(1 for p in pubs
                    if p.get("seller_custom_field") and sku_del_atributo(p)
                    and p["seller_custom_field"].strip() != sku_del_atributo(p).strip())
    print(f"  seller_custom_field  {con_scf:>6}")
    print(f"  atributo SELLER_SKU  {con_attr:>6}")
    print(f"  los dos y COINCIDEN  {coinciden:>6}")
    print(f"  los dos y DIFIEREN   {distintos:>6}   <- ojo aca")
    print(f"  SIN NINGUN SKU       {ninguno:>6}   <- no se pueden matchear")

    con_var = [p for p in pubs if p.get("variations")]
    print(f"\nPublicaciones con variaciones: {len(con_var)}")

    # ------------------------------------------------ duplicados de SKU
    print("\n" + "-" * 64)
    print("SKU REPETIDOS (el caso que hay que resolver al actualizar)")
    print("-" * 64)

    por_sku = defaultdict(list)
    for p in pubs:
        sku = (sku_del_atributo(p) or p.get("seller_custom_field") or "").strip()
        if sku:
            por_sku[sku].append(p)

    repes = {s: ps for s, ps in por_sku.items() if len(ps) > 1}
    activas_repes = {s: [p for p in ps if p.get("status") == "active"]
                     for s, ps in repes.items()}
    activas_repes = {s: ps for s, ps in activas_repes.items() if len(ps) > 1}

    print(f"  SKU distintos              {len(por_sku):>6}")
    print(f"  SKU en +1 publicacion      {len(repes):>6}")
    print(f"  ... con +1 publicacion ACTIVA {len(activas_repes):>6}  <- los conflictivos")

    # Regla propuesta originalmente: quedarse con la que NO tiene financiamiento
    # (gold_special = Clasica, gold_pro = Premium con cuotas sin interes).
    resueltos = sum(1 for ps in activas_repes.values()
                    if len([p for p in ps if p.get("listing_type_id") == "gold_special"]) == 1)
    print(f"\n  Regla 'la que no tiene financiamiento' resuelve: {resueltos} de {len(activas_repes)}")

    # ------------------------------------------------ agrupacion real
    print("\n" + "-" * 64)
    print("COMO SE AGRUPAN DE VERDAD (user_product_id = stock compartido)")
    print("-" * 64)

    print("\n  Logistica de las publicaciones activas:")
    activas = [p for p in pubs if p.get("status") == "active"]
    for lg, n in Counter(logistica(p) for p in activas).most_common():
        print(f"    {str(lg):<18} {n:>6}")

    print("\n  De los SKU con varias publicaciones activas:")
    un_producto = varios_productos = 0
    mixto_full = 0
    for sku, ps in activas_repes.items():
        productos = {p.get("user_product_id") for p in ps}
        if len(productos) == 1:
            un_producto += 1
        else:
            varios_productos += 1
        if any(es_full(p) for p in ps) and any(not es_full(p) for p in ps):
            mixto_full += 1

    print(f"    todas comparten user_product (mismo stock)   {un_producto:>5}")
    print(f"    tienen VARIOS user_product (stocks distintos) {varios_productos:>5}")
    print(f"    mezclan Full y deposito propio                {mixto_full:>5}  <- cuidado")

    # Lo que realmente importa para actualizar STOCK: cuantos destinos hay.
    print("\n  Para STOCK, agrupando por user_product_id:")
    destinos = defaultdict(set)
    for p in activas:
        sku = (sku_del_atributo(p) or p.get("seller_custom_field") or "").strip()
        if sku:
            destinos[sku].add(p.get("user_product_id"))
    amb_stock = {s: d for s, d in destinos.items() if len(d) > 1}
    print(f"    SKU con un solo destino de stock (sin ambiguedad) {len(destinos) - len(amb_stock):>5}")
    print(f"    SKU con VARIOS destinos (hay que preguntar)       {len(amb_stock):>5}")

    # Lo que importa para PRECIO: cuantas publicaciones activas hay por SKU.
    print("\n  Para PRECIO, cada publicacion tiene su propio precio:")
    precios_distintos = 0
    for sku, ps in activas_repes.items():
        if len({p.get("price") for p in ps}) > 1:
            precios_distintos += 1
    print(f"    SKU cuyas publicaciones YA tienen precios distintos {precios_distintos:>5}")
    print(f"    (en esos casos igualar el precio cambia la oferta actual)")

    print("\n" + "=" * 64)
    print(f"Cache en {CACHE.name}")
    return pubs


if __name__ == "__main__":
    try:
        main()
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
