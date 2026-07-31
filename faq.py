#!/usr/bin/env python3
"""
Que preguntan los compradores, y que falta en las publicaciones.

    python faq.py            -> ranking de publicaciones por preguntas
    python faq.py --temas    -> ademas analiza los temas con IA

Una pregunta repetida es informacion que falta en la publicacion. Si veinte
personas preguntan la misma medida, esa medida deberia estar en la ficha: se
vende mas y se responden menos preguntas.

Trabaja sobre el historico que ya esta cacheado (`preguntas.py`), asi que no
gasta llamadas a MercadoLibre salvo para traer los titulos.
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

import preguntas as preg
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

# Publicaciones con menos preguntas que esto no dan señal.
MINIMO_PREGUNTAS = 4


def ranking(ml, historico=None):
    """Publicaciones ordenadas por cuantas preguntas generan."""
    historico = historico if historico is not None else preg.cargar_historico(ml)
    por_item = defaultdict(list)
    for h in historico:
        if h.get("item_id"):
            por_item[h["item_id"]].append(h)

    pubs = {p["id"]: p for p in json.loads(
        (DIR / "catalogo.json").read_text(encoding="utf-8"))}

    filas = []
    for iid, qs in por_item.items():
        p = pubs.get(iid, {})
        filas.append({
            "item_id": iid,
            "titulo": (p.get("title") or "")[:65],
            "estado": p.get("status", "?"),
            "precio": p.get("price"),
            "vendidos": p.get("sold_quantity") or 0,
            "preguntas": len(qs),
            # Muchas preguntas por venta = la publicacion no se explica sola.
            "preguntas_por_venta": (len(qs) / (p.get("sold_quantity") or 1)),
            "ejemplos": " | ".join(q["pregunta"][:70] for q in qs[:5]),
        })

    df = pd.DataFrame(filas)
    return df.sort_values("preguntas", ascending=False) if len(df) else df


def palabras_frecuentes(historico, top=25):
    """Que se pregunta, en crudo. Sirve para ver patrones a ojo."""
    vacias = {"para", "esta", "este", "como", "hola", "buenas", "tienen",
              "tiene", "puede", "sirve", "cual", "cuanto", "gracias", "que",
              "por", "con", "los", "las", "una", "del", "buenos", "dias",
              "tardes", "consulta", "saber", "quisiera", "necesito", "hay",
              "son", "muy", "mas", "pero", "todo", "bien", "usted", "ustedes"}
    c = Counter()
    for h in historico:
        for w in preg._norm(h["pregunta"]).split():
            if len(w) > 3 and w not in vacias:
                c[w] += 1
    return c.most_common(top)


def analizar_temas(ml, item_id, preguntas_item, titulo=""):
    """
    Le pide a Claude que agrupe las preguntas de UNA publicacion y diga que
    deberia agregarse a la descripcion.
    """
    import anthropic

    esquema = {
        "type": "object",
        "properties": {
            "temas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tema": {"type": "string"},
                        "cuantas": {"type": "integer"},
                        "agregar_a_la_publicacion": {"type": "string"},
                    },
                    "required": ["tema", "cuantas", "agregar_a_la_publicacion"],
                    "additionalProperties": False,
                },
            },
            "resumen": {"type": "string"},
        },
        "required": ["temas", "resumen"],
        "additionalProperties": False,
    }

    listado = "\n".join(f"- {q['pregunta']}" for q in preguntas_item[:60])
    cliente = anthropic.Anthropic(api_key=preg._api_key())
    r = cliente.messages.create(
        model=preg.MODELO, max_tokens=4096,
        system=[{"type": "text",
                 "text": ("Analizás preguntas de compradores de MercadoLibre "
                          "para una ferretería uruguaya. Agrupá las preguntas "
                          "por tema y, para cada tema, decí en una frase "
                          "concreta qué habría que agregar al título, la ficha "
                          "o la descripción para que dejen de preguntarlo. "
                          "Escribí en castellano rioplatense, directo. Ignorá "
                          "las preguntas de una sola vez que no revelen un "
                          "patrón."),
                 "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive"},
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": esquema}},
        messages=[{"role": "user",
                   "content": f"Publicación: {titulo}\n\nPreguntas "
                              f"({len(preguntas_item)}):\n{listado}"}],
    )
    if r.stop_reason == "refusal":
        return {"temas": [], "resumen": "El modelo declinó el análisis."}
    return json.loads(next(b.text for b in r.content if b.type == "text"))


def main():
    ml = Meli(verbose=False)
    hist = preg.cargar_historico(ml)
    print(f"Analizando {len(hist)} preguntas históricas...\n")

    df = ranking(ml, hist)
    top = df[df["preguntas"] >= MINIMO_PREGUNTAS]
    print(f"Publicaciones con {MINIMO_PREGUNTAS}+ preguntas: {len(top)}\n")
    print("Las 10 que más preguntas generan:")
    print(top.head(10)[["preguntas", "vendidos", "precio", "titulo"]]
          .to_string(index=False, float_format=lambda x: f"{x:,.0f}"))

    print("\nLo que más se menciona en las preguntas:")
    for w, n in palabras_frecuentes(hist, 18):
        print(f"   {w:<18} {n:>4}")

    if "--temas" in sys.argv and len(top):
        fila = top.iloc[0]
        por_item = defaultdict(list)
        for h in hist:
            por_item[h.get("item_id")].append(h)
        print(f"\n\nAnálisis de temas de: {fila['titulo']}")
        r = analizar_temas(ml, fila["item_id"], por_item[fila["item_id"]],
                           fila["titulo"])
        print(f"\n{r['resumen']}\n")
        for t in r["temas"]:
            print(f"  [{t['cuantas']}x] {t['tema']}")
            print(f"       -> {t['agregar_a_la_publicacion']}")

    df.to_csv(DIR / "faq.csv", index=False)
    print(f"\nGuardado en faq.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
