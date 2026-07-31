#!/usr/bin/env python3
"""
Prueba una bateria de endpoints contra la cuenta real de SUPRABOND y reporta
cuales andan, cuales no y que datos devuelve cada uno.

    python explorar.py

Deja el detalle completo en exploracion.json para poder revisarlo despues.
La idea es no adivinar: la documentacion de ML no siempre esta al dia,
asi que la fuente de verdad es lo que responde la cuenta.
"""

import json
import sys
from datetime import datetime, timedelta, timezone

from meli import Meli, MeliError, SITE_ID


def resumir(dato, nivel=0):
    """Devuelve una descripcion corta de la forma del dato, sin volcar todo."""
    if isinstance(dato, dict):
        claves = list(dato.keys())
        return f"dict con {len(claves)} campos: {', '.join(claves[:12])}" + \
               ("..." if len(claves) > 12 else "")
    if isinstance(dato, list):
        if not dato:
            return "lista vacia"
        return f"lista de {len(dato)} -> primer elemento: {resumir(dato[0], nivel + 1)}"
    return f"{type(dato).__name__}: {str(dato)[:80]}"


class Explorador:
    def __init__(self):
        self.ml = Meli(verbose=False)
        self.resultados = []
        self.contexto = {}

    def probar(self, grupo, nombre, path, **params):
        etiqueta = f"[{grupo}] {nombre}"
        try:
            data = self.ml.get(path, **params)
        except MeliError as e:
            msg = str(e)
            corto = msg.split("HTTP", 1)[-1][:120] if "HTTP" in msg else msg[:120]
            print(f"  FALLA  {etiqueta}\n           {corto.strip()}")
            self.resultados.append({"grupo": grupo, "nombre": nombre, "path": path,
                                    "params": params, "ok": False, "error": msg[:600]})
            return None

        print(f"  OK     {etiqueta}\n           {resumir(data)}")
        self.resultados.append({"grupo": grupo, "nombre": nombre, "path": path,
                                "params": params, "ok": True,
                                "muestra": data if len(json.dumps(data, default=str)) < 20000
                                           else "(respuesta muy grande, truncada)"})
        return data

    # ---------------------------------------------------------------- secciones

    def cuenta(self):
        print("\n--- CUENTA -------------------------------------------------")
        yo = self.probar("cuenta", "datos del vendedor", "/users/me")
        if not yo:
            print("\nNo se pudo leer /users/me. Revisa la autorizacion.")
            sys.exit(1)
        self.contexto["user_id"] = yo["id"]
        self.contexto["nickname"] = yo["nickname"]
        rep = yo.get("seller_reputation", {})
        print(f"           -> {yo['nickname']} | site {yo.get('site_id')} | "
              f"nivel {rep.get('level_id', 'n/d')} | "
              f"ventas completadas: {rep.get('transactions', {}).get('completed', 'n/d')}")

    def publicaciones(self):
        print("\n--- PUBLICACIONES Y PRECIOS --------------------------------")
        uid = self.contexto["user_id"]

        listado = self.probar("items", "listado de publicaciones",
                              f"/users/{uid}/items/search", limit=5)
        if listado and listado.get("results"):
            self.contexto["item_id"] = listado["results"][0]
            print(f"           -> total publicaciones: "
                  f"{listado.get('paging', {}).get('total', '?')}")

        self.probar("items", "solo activas", f"/users/{uid}/items/search",
                    status="active", limit=1)
        self.probar("items", "pausadas", f"/users/{uid}/items/search",
                    status="paused", limit=1)

        item_id = self.contexto.get("item_id")
        if item_id:
            det = self.probar("items", "detalle de una publicacion", f"/items/{item_id}")
            if det:
                print(f"           -> {det.get('title', '')[:60]} | "
                      f"${det.get('price')} | stock {det.get('available_quantity')} | "
                      f"vendidos {det.get('sold_quantity')} | {det.get('listing_type_id')}")
            self.probar("items", "descripcion", f"/items/{item_id}/description")
            self.probar("items", "multiget (varios de una)", "/items",
                        ids=item_id, attributes="id,title,price,available_quantity,sold_quantity")

        # Cuanto se lleva ML de comision segun tipo de publicacion.
        self.probar("costos", "comisiones por tipo de publicacion",
                    f"/sites/{SITE_ID}/listing_prices", price=10000)

    def ventas(self):
        print("\n--- VENTAS Y FACTURACION -----------------------------------")
        uid = self.contexto["user_id"]
        desde = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000-00:00")

        ordenes = self.probar("ventas", "ordenes ultimos 30 dias", "/orders/search",
                              seller=uid, sort="date_desc", limit=5,
                              **{"order.date_created.from": desde})
        if not ordenes or not ordenes.get("results"):
            ordenes = self.probar("ventas", "ordenes pagas (sin filtro de fecha)",
                                  "/orders/search", seller=uid,
                                  sort="date_desc", limit=5, **{"order.status": "paid"})

        if ordenes and ordenes.get("results"):
            print(f"           -> total en el periodo: "
                  f"{ordenes.get('paging', {}).get('total', '?')}")
            orden = ordenes["results"][0]
            self.contexto["order_id"] = orden["id"]

            det = self.probar("ventas", "detalle de una orden", f"/orders/{orden['id']}")
            if det:
                items = det.get("order_items", [])
                if items:
                    it = items[0]
                    # sale_fee es la comision de ML por unidad: clave para margen.
                    print(f"           -> {it['item'].get('title', '')[:50]} | "
                          f"precio ${it.get('unit_price')} | cant {it.get('quantity')} | "
                          f"comision ML ${it.get('sale_fee')}")
                print(f"           -> total ${det.get('total_amount')} | "
                      f"status {det.get('status')} | pack {det.get('pack_id')}")

            # Si tira 404 "discount_not_found" es que esa orden no tuvo
            # descuentos, no que el endpoint este roto.
            self.probar("ventas", "descuentos de la orden", f"/orders/{orden['id']}/discounts")

            envio = (det or {}).get("shipping", {}).get("id")
            if envio:
                self.contexto["shipment_id"] = envio
                self.probar("envios", "detalle del envio", f"/shipments/{envio}")
                self.probar("envios", "costos del envio", f"/shipments/{envio}/costs")

        # Facturacion de ML: lo que ML te cobra a vos (comisiones, envios, cargos).
        # Ojo: la ruta correcta es monthly/periods y group + document_type son
        # obligatorios, si no tira 422.
        periodos = self.probar("facturacion", "periodos de facturacion de ML",
                               "/billing/integration/monthly/periods",
                               group="ML", document_type="BILL")
        if periodos and periodos.get("results"):
            p = periodos["results"][0]
            print(f"           -> periodo {p['period']['date_from']} a {p['period']['date_to']}: "
                  f"${p.get('amount'):,.2f} (impago ${p.get('unpaid_amount'):,.2f})")
            self.contexto["periodo_billing"] = p["key"]
            self.probar("facturacion", "documentos del periodo",
                        f"/billing/integration/periods/key/{p['key']}/group/ML/documents")

    def visitas(self):
        print("\n--- VISITAS Y CONVERSION -----------------------------------")
        uid = self.contexto["user_id"]
        hasta = datetime.now(timezone.utc)
        desde = hasta - timedelta(days=30)
        # OJO: los endpoints de visitas quieren fecha simple YYYY-MM-DD.
        # Si les mandas ISO completo (como pide /orders/search) tiran 400.
        f_desde = desde.strftime("%Y-%m-%d")
        f_hasta = hasta.strftime("%Y-%m-%d")

        v = self.probar("visitas", "visitas totales del vendedor",
                        f"/users/{uid}/items_visits", date_from=f_desde, date_to=f_hasta)
        if v:
            print(f"           -> {v.get('total_visits'):,} visitas en 30 dias")
        self.probar("visitas", "visitas del vendedor por ventana",
                    f"/users/{uid}/items_visits/time_window", last=30, unit="day")

        item_id = self.contexto.get("item_id")
        if item_id:
            self.probar("visitas", "visitas de una publicacion",
                        f"/items/{item_id}/visits/time_window", last=30, unit="day")
            self.probar("visitas", "visitas multiget", "/items/visits",
                        ids=item_id, date_from=f_desde, date_to=f_hasta)

    def extras(self):
        print("\n--- OTROS (por si sirven mas adelante) ---------------------")
        uid = self.contexto["user_id"]
        self.probar("preguntas", "preguntas recibidas", "/questions/search",
                    seller_id=uid, limit=3)
        self.probar("preguntas", "preguntas sin responder", "/questions/search",
                    seller_id=uid, status="UNANSWERED", limit=3)
        # El buscador publico por seller_id ahora da 403; con nickname suele andar.
        self.probar("catalogo", "buscador publico por nickname",
                    f"/sites/{SITE_ID}/search", nickname=self.contexto["nickname"], limit=1)

    # ---------------------------------------------------------------- reporte

    def correr(self):
        print("=" * 62)
        print("EXPLORACION DE LA API DE MERCADOLIBRE - SUPRABOND")
        print("=" * 62)

        self.cuenta()
        self.publicaciones()
        self.ventas()
        self.visitas()
        self.extras()

        ok = [r for r in self.resultados if r["ok"]]
        fallas = [r for r in self.resultados if not r["ok"]]

        print("\n" + "=" * 62)
        print(f"RESUMEN: {len(ok)} endpoints OK, {len(fallas)} con error")
        if fallas:
            print("\nCon error:")
            for r in fallas:
                print(f"  - [{r['grupo']}] {r['nombre']}")
        print("=" * 62)

        salida = {"contexto": self.contexto,
                  "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                  "resultados": self.resultados}
        with open("exploracion.json", "w", encoding="utf-8") as f:
            json.dump(salida, f, indent=2, ensure_ascii=False, default=str)
        print("\nDetalle completo guardado en exploracion.json")


if __name__ == "__main__":
    try:
        Explorador().correr()
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
