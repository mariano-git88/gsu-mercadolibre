#!/usr/bin/env python3
"""
Respuesta automatica de preguntas de compradores, con IA.

    python preguntas.py --simular     -> redacta sin publicar (recomendado para probar)
    python preguntas.py --publicar    -> redacta Y PUBLICA en MercadoLibre

De donde saca el contexto, en orden de peso:

  1. **El historico de respuestas propias.** Es la fuente mas valiosa: mismo
     producto, mismo tono, respuestas ya validadas por el equipo. Se busca con
     BM25.

     **OJO en Uruguay: esta cuenta tiene 33 preguntas en toda su historia**
     (la de Argentina tiene ~1.585). Con esa base el BM25 no tiene de donde
     agarrarse y el modelo va a abstenerse casi siempre. Dejar `ia_activa`
     apagado hasta que haya volumen.
  2. **Los datos de la publicacion**: titulo, descripcion, atributos, precio,
     stock, y las preguntas anteriores del MISMO articulo.
  3. **Documentos y sitios** que cargue el operador (fichas tecnicas, etc.).

Sobre el modo automatico: publica sin intervencion, como se pidio. Dos cosas
que si estan puestas, porque son parte de responder bien y no un limite al
alcance:

  - El modelo puede **abstenerse**. Si el contexto no alcanza para responder
    con certeza, marca la pregunta para que la vea una persona en vez de
    inventar. Una respuesta equivocada queda publica.
  - Hay un **interruptor** en la Sheet (`ia_activa`) y queda registro de todo
    lo publicado, para poder medir la calidad y frenar si hace falta.
"""

import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import almacen
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent
CACHE_HIST = DIR / "preguntas_historico.json"

HOJA_RESPUESTAS = "respuestas_ia"
HOJA_FUENTES = "fuentes_ia"
HOJA_CONFIG = "config_ia"
HOJA_HISTORIAL = "historial_preguntas"

COLS_RESPUESTAS = ["fecha", "question_id", "item_id", "pregunta", "respuesta",
                   "estado", "confianza", "fuentes", "modelo", "motivo"]
COLS_FUENTES = ["tipo", "nombre", "url", "contenido", "cargado", "operador"]
COLS_CONFIG = ["clave", "valor", "nota"]
COLS_HISTORIAL = ["question_id", "fecha_pregunta", "item_id", "publicacion",
                  "comprador", "pregunta", "respuesta", "respondida_por",
                  "estado_ml", "sincronizado"]

MODELO = "claude-opus-5"

# Cuantos ejemplos del historico se le pasan al modelo.
K_HISTORICO = 10


# ------------------------------------------------------------------ config

def config():
    filas = almacen.leer_hoja(HOJA_CONFIG, COLS_CONFIG)
    if not filas:
        iniciales = [
            {"clave": "ia_activa", "valor": "si",
             "nota": "Poner 'no' para que deje de responder automaticamente"},
            {"clave": "firma", "valor": "Equipo SUPRABOND",
             "nota": "Como firma la respuesta"},
            {"clave": "min_confianza", "valor": "media",
             "nota": "alta | media -> por debajo de esto no publica, deja para revisar"},
        ]
        almacen.append_hoja(HOJA_CONFIG, COLS_CONFIG, iniciales)
        filas = iniciales
    return {f["clave"]: str(f.get("valor", "")).strip() for f in filas}


def ia_activa():
    return config().get("ia_activa", "si").lower() in ("si", "sí", "1", "true")


# ------------------------------------------------------------------ historico

def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ ]+", " ", s)


TOPE_OFFSET_PREGUNTAS = 1000    # ML rechaza offset > 1000 con un 400


def bajar_historico(ml, limite=1000):
    """
    Trae las preguntas ya respondidas (las mas recientes primero) y las cachea.

    ML corta el offset en 1000, asi que nos quedamos con las ultimas ~1000.
    Alcanza de sobra: son las mas representativas del tono actual.
    """
    respondidas, offset = [], 0
    while len(respondidas) < limite and offset < TOPE_OFFSET_PREGUNTAS:
        r = ml.get("/questions/search", seller_id=ml.user_id, limit=50,
                   offset=offset, sort_fields="date_created", sort_types="DESC")
        lote = r.get("questions") or []
        if not lote:
            break
        for q in lote:
            texto_r = ((q.get("answer") or {}).get("text") or "").strip()
            if texto_r:
                respondidas.append({
                    "item_id": q.get("item_id"),
                    "pregunta": (q.get("text") or "").strip(),
                    "respuesta": texto_r,
                    "fecha": (q.get("date_created") or "")[:10],
                })
        offset += 50
        if offset >= (r.get("total") or 0):
            break

    CACHE_HIST.write_text(json.dumps(respondidas, ensure_ascii=False),
                          encoding="utf-8")
    return respondidas


def cargar_historico(ml=None, refrescar=False):
    if CACHE_HIST.exists() and not refrescar:
        return json.loads(CACHE_HIST.read_text(encoding="utf-8"))
    if ml is None:
        return []
    return bajar_historico(ml)


class Buscador:
    """
    BM25 sobre el historico de preguntas y los documentos cargados.

    Se usa BM25 y no vectores a proposito: son textos cortos con vocabulario
    muy repetido (nombres de producto, medidas, marcas), donde el match por
    palabra funciona bien, y evita depender de otra API de embeddings.
    """

    def __init__(self, historico, documentos=None):
        from rank_bm25 import BM25Okapi

        self.docs = []
        for h in historico:
            self.docs.append({
                "tipo": "historico",
                "texto": f"{h['pregunta']} {h['respuesta']}",
                "datos": h,
            })
        for d in documentos or []:
            # Los documentos largos se parten para que el match sea util.
            for i, trozo in enumerate(_partir(d.get("contenido", ""))):
                self.docs.append({
                    "tipo": "documento",
                    "texto": trozo,
                    "datos": {"nombre": d.get("nombre"), "url": d.get("url"),
                              "fragmento": i, "contenido": trozo},
                })

        self.bm25 = (BM25Okapi([_norm(d["texto"]).split() for d in self.docs])
                     if self.docs else None)

    def buscar(self, consulta, k=K_HISTORICO):
        if not self.bm25:
            return []
        puntajes = self.bm25.get_scores(_norm(consulta).split())
        orden = sorted(range(len(puntajes)), key=lambda i: -puntajes[i])
        return [self.docs[i] for i in orden[:k] if puntajes[i] > 0]


def _partir(texto, tamano=900):
    texto = re.sub(r"\s+", " ", str(texto or "")).strip()
    return [texto[i:i + tamano] for i in range(0, len(texto), tamano)] or []


# ------------------------------------------------------------------ fuentes

def fuentes():
    return almacen.leer_hoja(HOJA_FUENTES, COLS_FUENTES)


def agregar_fuente(tipo, nombre, contenido, url="", operador=""):
    return almacen.append_hoja(HOJA_FUENTES, COLS_FUENTES, [{
        "tipo": tipo, "nombre": nombre, "url": url,
        "contenido": str(contenido)[:45000],
        "cargado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operador": operador}])


def leer_pdf(archivo):
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(archivo).pages)


def bajar_web(url, max_paginas=1):
    """Trae el texto de una URL. Simple a proposito: una pagina por fuente."""
    import requests
    from bs4 import BeautifulSoup

    r = requests.get(url, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (compatible; SuprabondBot/1.0)"})
    r.raise_for_status()
    sopa = BeautifulSoup(r.text, "html.parser")
    for t in sopa(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    principal = sopa.find("main") or sopa.find("article") or sopa.body or sopa
    titulo = (sopa.title.string if sopa.title else url) or url
    return titulo.strip(), re.sub(r"\s+", " ", principal.get_text(" ")).strip()


# ------------------------------------------------------------------ contexto

def contexto_publicacion(ml, item_id):
    """Datos del articulo sobre el que preguntan."""
    try:
        it = ml.get(f"/items/{item_id}")
    except Exception:  # noqa: BLE001
        return {}
    try:
        desc = (ml.get(f"/items/{item_id}/description").get("plain_text") or "")[:2500]
    except Exception:  # noqa: BLE001
        desc = ""

    atributos = {a.get("name"): a.get("value_name")
                 for a in it.get("attributes") or []
                 if a.get("value_name") and a.get("name")}
    return {
        "item_id": item_id,
        "titulo": it.get("title"),
        "precio": it.get("price"),
        "stock": it.get("available_quantity"),
        "vendidos": it.get("sold_quantity"),
        "estado": it.get("status"),
        "descripcion": desc,
        "atributos": atributos,
        "envio_gratis": (it.get("shipping") or {}).get("free_shipping"),
    }


def preguntas_del_item(ml, item_id, excluir=None):
    """Q&A previas del MISMO articulo: el contexto mas directo que hay."""
    try:
        r = ml.get("/questions/search", item=item_id, limit=30)
    except Exception:  # noqa: BLE001
        return []
    salida = []
    for q in r.get("questions") or []:
        if excluir and str(q.get("id")) == str(excluir):
            continue
        resp = ((q.get("answer") or {}).get("text") or "").strip()
        if resp:
            salida.append({"pregunta": (q.get("text") or "").strip(),
                           "respuesta": resp})
    return salida[:12]


# ------------------------------------------------------------------ redaccion

ESQUEMA = {
    "type": "object",
    "properties": {
        "responder": {
            "type": "boolean",
            "description": ("true si el contexto alcanza para responder con "
                            "certeza; false si hay que derivar a una persona"),
        },
        "respuesta": {
            "type": "string",
            "description": "El texto a publicar. Vacio si responder es false.",
        },
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
        "motivo": {
            "type": "string",
            "description": ("Por que se responde eso, o que falta para poder "
                            "responder. Para el registro, no se publica."),
        },
    },
    "required": ["responder", "respuesta", "confianza", "motivo"],
    "additionalProperties": False,
}

INSTRUCCIONES = """\
Sos quien responde las preguntas de los compradores en la cuenta de \
MercadoLibre de SUPRABOND (Uruguay), una empresa que vende herramientas, \
adhesivos, selladores, candados, griferia y articulos de ferreteria.

Tu respuesta se PUBLICA AUTOMATICAMENTE, sin que nadie la revise antes. \
Escribi solamente lo que puedas sostener con el contexto que te dan.

Como escribir:
- En castellano rioplatense, breve y concreto. Dos o tres oraciones.
- Segui el estilo de las respuestas anteriores de la cuenta que te paso: \
suelen abrir agradeciendo la consulta y cerrar invitando a la compra.
- Respondé lo que preguntaron. Nada de vender lo que no preguntaron.
- Nunca inventes medidas, compatibilidades, materiales, plazos de entrega ni \
disponibilidad. Si el dato no está en el contexto, no lo afirmes.

Cuándo NO responder (poné responder=false):
- La pregunta necesita un dato que no tenés (una compatibilidad puntual, una \
medida que no figura, si sirve para un uso muy especifico).
- Piden algo que no podés resolver: cambiar el precio, hacer un descuento, \
facturacion especial, un reclamo, un envio fuera de lo normal.
- La pregunta es sobre una compra ya hecha, una demora o un problema.
- Cualquier caso donde una respuesta equivocada le haría perder plata o \
confianza a la empresa.

Es mejor derivar a una persona que arriesgar una respuesta incorrecta: la \
respuesta queda publica en la publicacion.

Las reglas de MercadoLibre no permiten dar datos de contacto (telefono, mail, \
redes) ni derivar la venta fuera de la plataforma. No lo hagas nunca.\
"""


def redactar(pregunta, item, similares, previas, firma, cliente=None):
    """Le pide a Claude la respuesta. Devuelve el dict del esquema."""
    import anthropic

    cliente = cliente or anthropic.Anthropic(api_key=_api_key())

    partes = [f"PREGUNTA DEL COMPRADOR ({pregunta.get('nick') or 'comprador'}):",
              pregunta["texto"], ""]

    if item:
        partes += [
            "DATOS DE LA PUBLICACION:",
            f"  Titulo: {item.get('titulo')}",
            f"  Precio: ${item.get('precio')}",
            f"  Stock disponible: {item.get('stock')}",
            f"  Unidades vendidas: {item.get('vendidos')}",
            f"  Envio gratis: {item.get('envio_gratis')}",
        ]
        if item.get("atributos"):
            partes.append("  Ficha del producto:")
            for k, v in list(item["atributos"].items())[:25]:
                partes.append(f"    - {k}: {v}")
        if item.get("descripcion"):
            partes += ["  Descripcion publicada:", f"    {item['descripcion']}"]
        partes.append("")

    if previas:
        partes.append("PREGUNTAS YA RESPONDIDAS EN ESTA MISMA PUBLICACION:")
        for p in previas:
            partes += [f"  P: {p['pregunta']}", f"  R: {p['respuesta']}"]
        partes.append("")

    hist = [d for d in similares if d["tipo"] == "historico"]
    docs = [d for d in similares if d["tipo"] == "documento"]

    if hist:
        partes.append("RESPUESTAS ANTERIORES DE LA CUENTA EN CASOS PARECIDOS "
                      "(seguí este tono):")
        for d in hist:
            partes += [f"  P: {d['datos']['pregunta']}",
                       f"  R: {d['datos']['respuesta']}"]
        partes.append("")

    if docs:
        partes.append("DOCUMENTACION CARGADA POR LA EMPRESA:")
        for d in docs:
            partes.append(f"  [{d['datos'].get('nombre')}] "
                          f"{d['datos'].get('contenido', '')[:900]}")
        partes.append("")

    partes.append(f"Firmá como: {firma}")

    resp = cliente.messages.create(
        model=MODELO,
        max_tokens=4096,
        system=[{"type": "text", "text": INSTRUCCIONES,
                 # Las instrucciones son iguales en cada pregunta: cachearlas
                 # abarata mucho el procesamiento de una tanda.
                 "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": ESQUEMA}},
        messages=[{"role": "user", "content": "\n".join(partes)}],
    )

    # El modelo puede declinar por politica: hay que mirarlo antes del contenido.
    if resp.stop_reason == "refusal":
        return {"responder": False, "respuesta": "", "confianza": "baja",
                "motivo": "El modelo declinó la solicitud por sus políticas."}

    texto = next((b.text for b in resp.content if b.type == "text"), "{}")
    salida = json.loads(texto)
    salida["_fuentes"] = ([f"historico:{d['datos']['pregunta'][:40]}" for d in hist[:3]]
                          + [f"doc:{d['datos'].get('nombre')}" for d in docs[:3]])
    return salida


# Nombres bajo los que puede estar la clave, para no depender de uno solo.
NOMBRES_CLAVE = ["anthropic_api_key", "ANTHROPIC_API_KEY", "anthropic_key",
                 "claude_api_key"]


def _api_key():
    """
    Busca la clave de Anthropic en todas las formas razonables: top-level en
    secrets, dentro de una seccion [anthropic], en variable de entorno, o en
    el secrets.toml local.

    Si no la encuentra, el error dice que claves SI hay configuradas (solo los
    nombres, nunca los valores) para poder ver de un vistazo si hay un typo.
    """
    import os

    def _limpiar(v):
        v = str(v or "").strip().strip('"').strip("'")
        return v if v.startswith("sk-") else ""

    # 1. Seccion [anthropic] con api_key adentro
    seccion = almacen._seccion("anthropic") or {}
    for n in ["api_key"] + NOMBRES_CLAVE:
        if _limpiar(seccion.get(n)):
            return _limpiar(seccion[n])

    # 2. Top-level en los secrets de Streamlit
    disponibles = []
    try:
        import streamlit as st
        try:
            disponibles = sorted(st.secrets.keys())
        except Exception:
            disponibles = []
        for n in NOMBRES_CLAVE:
            try:
                if _limpiar(st.secrets.get(n)):
                    return _limpiar(st.secrets.get(n))
            except Exception:
                pass
    except Exception:
        pass

    # 3. Variable de entorno (util para GitHub Actions)
    for n in NOMBRES_CLAVE:
        if _limpiar(os.environ.get(n)):
            return _limpiar(os.environ[n])

    # 4. secrets.toml local
    if almacen.SECRETS_LOCAL.exists():
        import tomllib
        with open(almacen.SECRETS_LOCAL, "rb") as f:
            local = tomllib.load(f)
        if not disponibles:
            disponibles = sorted(local.keys())
        for n in NOMBRES_CLAVE:
            if _limpiar(local.get(n)):
                return _limpiar(local[n])

    pista = (f" Claves que sí veo en los secrets: {', '.join(disponibles)}."
             if disponibles else "")
    raise MeliError(
        "No encuentro la clave de Anthropic. Tiene que estar en los secrets "
        "como `anthropic_api_key = \"sk-ant-...\"`, sin corchetes ni sección, "
        "y el valor tiene que empezar con `sk-`." + pista)


# ------------------------------------------------------------------ publicar

def publicar(ml, question_id, texto):
    """
    Publica la respuesta en MercadoLibre.

    Captura `Exception` y no `MeliError` a proposito: Streamlit cachea el
    objeto `ml` con `st.cache_resource`, asi que tras una recarga del script
    la clase MeliError del objeto cacheado puede no ser la misma clase que
    importa este modulo — y un `except MeliError` no la atrapa. Un fallo al
    publicar tiene que quedar registrado, nunca tumbar la app.
    """
    try:
        ml._request("POST", "/answers",
                    json_body={"question_id": int(question_id), "text": texto})
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:250]}"


def pendientes(ml):
    r = ml.get("/questions/search", seller_id=ml.user_id,
               status="UNANSWERED", limit=50)
    return r.get("questions") or []


def pendientes_respondibles(ml):
    """
    Solo las preguntas que **se pueden** contestar.

    MercadoLibre marca como `UNANSWERED` preguntas de publicaciones que ya no
    estan activas, y **no deja responderlas**. Mostrarlas es peor que inutil:
    el tablero decia "3 sin responder" cuando la unica accionable era una, y
    las otras dos eran de enero sobre publicaciones pausadas por falta de
    stock. No son trabajo pendiente, son ruido.

    Todo el circuito —tablero, procesamiento y bandeja— usa esta.
    """
    respondibles, _ = pendientes_detalle(ml)
    return respondibles


def pendientes_detalle(ml):
    """
    Las pendientes separadas en (se pueden responder, bloqueadas).

    Se mantiene separado de `pendientes_respondibles()` para poder mirar las
    bloqueadas si alguna vez hace falta diagnosticar, pero la app no las
    muestra.
    """
    respondibles, bloqueadas = [], []
    for q in pendientes(ml):
        estado, titulo = None, ""
        try:
            it = ml.get(f"/items/{q.get('item_id')}",
                        attributes="status,title,sub_status")
            estado = it.get("status")
            titulo = it.get("title") or ""
            sub = it.get("sub_status") or []
        except Exception:  # noqa: BLE001
            sub = []
        fila = {**q, "item_status": estado, "item_titulo": titulo,
                "item_sub_status": ", ".join(sub) if sub else ""}
        (respondibles if estado == "active" else bloqueadas).append(fila)
    return respondibles, bloqueadas


def registrar(fila):
    almacen.append_hoja(HOJA_RESPUESTAS, COLS_RESPUESTAS, [fila])


# --------------------------------------------------- bandeja de pendientes

# Estados en los que la pregunta quedó SIN responder y espera a una persona.
# `publicacion_inactiva` quedo fuera a proposito: esas preguntas ya no entran
# al circuito, asi que no pueden aparecer como trabajo pendiente.
ESTADOS_ABIERTOS = ("para_revisar", "error_tecnico", "error_al_publicar")


def bandeja(ml):
    """
    Preguntas que la IA no resolvió y siguen esperando respuesta.

    Cruza el registro con lo que MercadoLibre dice AHORA: si alguien ya la
    respondió desde el panel, deja de aparecer. Así la bandeja refleja trabajo
    real pendiente y no un historial de casos viejos.
    """
    try:
        registro = almacen.leer_hoja(HOJA_RESPUESTAS, COLS_RESPUESTAS)
    except Exception:
        registro = []

    # El ultimo estado registrado de cada pregunta es el que vale.
    ultimo = {}
    for f in registro:
        ultimo[str(f.get("question_id"))] = f

    sin_responder = {str(q.get("id")): q for q in pendientes_respondibles(ml)}

    salida = []
    for qid, q in sin_responder.items():
        f = ultimo.get(qid)
        if f and str(f.get("estado", "")).strip() not in ESTADOS_ABIERTOS:
            continue
        salida.append({
            "question_id": qid,
            "item_id": q.get("item_id"),
            "pregunta": (q.get("text") or "").strip(),
            "comprador": (q.get("from") or {}).get("nickname", ""),
            "fecha": (q.get("date_created") or "")[:16].replace("T", " "),
            # Lo que la IA llegó a redactar, si redactó algo: sirve de borrador.
            "borrador": str((f or {}).get("respuesta", "")),
            "motivo": str((f or {}).get("motivo", "")),
            "estado": str((f or {}).get("estado", "sin_procesar")),
        })
    salida.sort(key=lambda x: x["fecha"])
    return salida


def borrador(ml, question_id, item_id, texto_pregunta, nick=""):
    """
    Redacta un borrador para UNA pregunta puntual, a pedido del operador.

    No publica ni registra nada: devuelve el texto para que la persona lo
    edite y decida. Sirve para pedirle ayuda a la IA en una pregunta que ella
    no habia podido resolver sola, o simplemente para arrancar de algo escrito.
    """
    cfg = config()
    try:
        item = contexto_publicacion(ml, item_id)
        previas = preguntas_del_item(ml, item_id, excluir=question_id)
        buscador = Buscador(cargar_historico(ml), fuentes())
        similares = buscador.buscar(f"{item.get('titulo','')} {texto_pregunta}")
    except Exception as e:
        return "", f"No pude armar el contexto: {str(e)[:200]}"

    try:
        r = redactar({"texto": texto_pregunta, "nick": nick}, item, similares,
                     previas, cfg.get("firma", "Equipo SUPRABOND"))
    except Exception as e:
        return "", f"No pude redactar: {str(e)[:200]}"

    if not r.get("respuesta"):
        return "", (f"La IA sigue sin poder responderla: {r.get('motivo','')}"
                    [:300])
    ok_txt, detalle_txt = revisar_texto(r["respuesta"])
    if not ok_txt:
        return r["respuesta"], (f"OJO, revisá el texto antes de publicar: {detalle_txt}")
    aviso = ""
    if r.get("confianza") == "baja" or not r.get("responder"):
        aviso = (f"Ojo, la IA tiene poca certeza: {r.get('motivo','')[:200]}. "
                 "Revisá bien antes de publicar.")
    return r["respuesta"], aviso


def responder_a_mano(ml, question_id, texto, operador, item_id="",
                     pregunta="", motivo_previo=""):
    """
    Publica una respuesta escrita por una persona y cierra el caso.

    Deja el registro con `publicada_por_persona` para que el contador
    distinga lo que resolvió la IA de lo que resolvió el equipo.
    """
    texto = (texto or "").strip()
    if not texto:
        return False, "La respuesta está vacía."
    if not operador.strip():
        return False, "Falta tu nombre."

    ok, detalle = publicar(ml, question_id, texto)
    if not ok:
        return False, detalle

    try:
        registrar({
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "question_id": question_id, "item_id": item_id,
            "pregunta": str(pregunta)[:400], "respuesta": texto[:900],
            "estado": "publicada_por_persona", "confianza": "",
            "fuentes": "", "modelo": "",
            "motivo": (f"Respondida a mano por {operador.strip()}."
                       + (f" La IA no lo hizo porque: {motivo_previo[:200]}"
                          if motivo_previo else "")),
        })
    except Exception as e:
        return True, f"Publicada, pero no pude registrarla: {str(e)[:150]}"
    return True, ""


# Estados que NO bloquean un reintento: si algo falló por configuración o por
# un error de red, la pregunta tiene que poder procesarse de nuevo. Solo una
# decision real del modelo (publicada / derivada) cierra el caso.
ESTADOS_REINTENTABLES = ("error_tecnico", "error_al_publicar",
                         "publicacion_inactiva")


def ya_procesadas():
    """
    question_id que no hay que volver a procesar.

    Una pregunta que quedó en error se excluye a proposito: de lo contrario un
    fallo transitorio la dejaria sin responder para siempre.
    """
    filas = almacen.leer_hoja(HOJA_RESPUESTAS, COLS_RESPUESTAS)
    return {str(f.get("question_id")) for f in filas
            if str(f.get("estado", "")).strip() not in ESTADOS_REINTENTABLES}


# ---------------------------------------------------------------------------
# Control de calidad del texto antes de publicar
# ---------------------------------------------------------------------------
#
# El modelo a veces escribe mal la marca y se corrige a si mismo a mitad de la
# frase: "lo determina Mercado Ubre... perdon, Mercado Libre". Medido en
# Uruguay: 3 de 10 corridas de la misma pregunta. NO viene del contexto (no hay
# ningun "Ubre" en el historial ni en las fuentes), lo genera el modelo solo, y
# se nota mas donde el historial es flaco: en Argentina, con 1.068 preguntas de
# base, 0 de 38 publicadas lo tienen.
#
# Como acá se publica sin que nadie mire, un texto asi le llega al comprador.
# El guard lo detecta, se reintenta una vez y si vuelve a salir mal la pregunta
# queda para una persona.
#
# Ojo con los falsos positivos: "¡Perdon por la demora en responder!" es una
# frase legitima y usada. Por eso no se prohibe "perdon" suelto, sino el patron
# de AUTOCORRECCION (puntos suspensivos + perdon, "quise decir", "corrijo") y
# la marca escrita mal.

PATRONES_SOSPECHOSOS = [
    # "Mercado" seguido de algo que NO sea una marca real de ML. Pago, Envios,
    # Shops y Puntos son productos legitimos que pueden aparecer en una
    # respuesta; cualquier otra cosa es la marca escrita mal.
    (r"Mercado\s+(?!Libre\b|Pago\b|Env[ií]os\b|Shops\b|Puntos\b|Cr[eé]dito\b|Play\b|Ads\b)"
     r"[A-Za-zÁÉÍÓÚáéíóúñÑ]+", "escribió mal 'Mercado Libre'"),
    (r"(\.\.\.|…)\s*perd[oó]n", "se corrige a mitad de la frase"),
    (r"\bquise decir\b", "se corrige a mitad de la frase"),
    (r"\bcorrijo\b", "se corrige a mitad de la frase"),
]


def revisar_texto(texto):
    """
    (ok, motivo) — ¿este texto se puede publicar tal cual?

    Solo mira defectos de forma evidentes. No juzga si la respuesta es
    correcta: de eso se ocupa el propio modelo con `responder`/`confianza`.
    """
    t = str(texto or "")
    if not t.strip():
        return False, "el texto vino vacío"
    for patron, motivo in PATRONES_SOSPECHOSOS:
        m = re.search(patron, t, re.I)
        if m:
            return False, f"{motivo}: «{t[max(0, m.start() - 30):m.end() + 30].strip()}»"
    return True, ""


def procesar(ml, publicar_de_verdad=False, callback=None):
    """
    Toma las preguntas sin responder, redacta y (si corresponde) publica.

    Es idempotente: una pregunta ya registrada no se vuelve a procesar.
    """
    if publicar_de_verdad and not ia_activa():
        return {"error": "La IA está desactivada en la configuración "
                         "(config_ia → ia_activa)."}

    cfg = config()
    firma = cfg.get("firma", "Equipo SUPRABOND")
    minima = cfg.get("min_confianza", "media").lower()
    orden = {"alta": 3, "media": 2, "baja": 1}

    # Solo las que se pueden contestar: las de publicaciones inactivas no son
    # trabajo pendiente y no tiene sentido gastarles una llamada al modelo.
    preguntas = pendientes_respondibles(ml)
    hechas = ya_procesadas()
    nuevas = [q for q in preguntas if str(q.get("id")) not in hechas]

    historico = cargar_historico(ml)
    buscador = Buscador(historico, fuentes())

    resultados = []
    for i, q in enumerate(nuevas, start=1):
        if callback:
            callback(i, len(nuevas), q)

        texto_p = (q.get("text") or "").strip()
        item_id = q.get("item_id")
        try:
            item = contexto_publicacion(ml, item_id)
            previas = preguntas_del_item(ml, item_id, excluir=q.get("id"))
            similares = buscador.buscar(f"{item.get('titulo','')} {texto_p}")
        except Exception as e:
            # Que no se caiga toda la tanda por una publicacion problematica.
            item, previas, similares = {}, [], []
            print(f"[preguntas] aviso: sin contexto para {item_id}: {e}")

        # ML rechaza responder preguntas de publicaciones que no estan activas
        # ("Item must be active"). Se detecta antes de llamar al modelo, que
        # cuesta plata y no serviria de nada.
        if publicar_de_verdad and item and item.get("estado") != "active":
            fila = {
                "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "question_id": q.get("id"), "item_id": item_id,
                "pregunta": texto_p[:400], "respuesta": "",
                "estado": "publicacion_inactiva", "confianza": "",
                "fuentes": "", "modelo": "",
                "motivo": (f"La publicación está '{item.get('estado')}': "
                           "MercadoLibre no permite responder preguntas de "
                           "publicaciones que no están activas. Reactivala o "
                           "respondé desde el panel."),
            }
            try:
                registrar(fila)
            except Exception:
                pass
            resultados.append(fila)
            continue

        fallo = None
        pregunta_ctx = {"texto": texto_p,
                        "nick": (q.get("from") or {}).get("nickname")}
        try:
            r = redactar(pregunta_ctx, item, similares, previas, firma)
            # Control de forma. Si el texto sale con un artefacto de
            # redaccion se reintenta UNA vez: el modelo no es determinista y
            # la segunda suele salir limpia. Si vuelve a fallar, no se
            # publica — queda para una persona.
            if r.get("responder") and r.get("respuesta"):
                ok_txt, detalle_txt = revisar_texto(r["respuesta"])
                if not ok_txt:
                    print(f"[preguntas] reintento por texto sospechoso "
                          f"({q.get('id')}): {detalle_txt}")
                    r2 = redactar(pregunta_ctx, item, similares, previas, firma)
                    ok2, detalle2 = revisar_texto(r2.get("respuesta", ""))
                    if r2.get("responder") and ok2:
                        r = r2
                    else:
                        r = dict(r)
                        r["responder"] = False
                        r["motivo"] = ("El borrador salió con un problema de "
                                       f"redacción en los dos intentos ({detalle_txt}). "
                                       "Lo dejo para que lo escriba una persona.")
        except Exception as e:
            # Un fallo tecnico NO es lo mismo que "el contexto no alcanzaba":
            # confundirlos hace que un problema de configuracion parezca una
            # decision del modelo.
            fallo = str(e)[:300]
            r = {"responder": False, "respuesta": "", "confianza": "",
                 "motivo": fallo, "_fuentes": []}

        suficiente = orden.get(r.get("confianza", "baja"), 1) >= orden.get(minima, 2)
        if fallo:
            estado = "error_tecnico"
        elif r.get("responder") and suficiente:
            estado = "publicada" if publicar_de_verdad else "simulada"
        else:
            estado = "para_revisar"

        if estado == "publicada":
            ok, detalle = publicar(ml, q["id"], r["respuesta"])
            if not ok:
                estado, r["motivo"] = "error_al_publicar", detalle

        fila = {
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "question_id": q.get("id"), "item_id": item_id,
            "pregunta": texto_p[:400], "respuesta": (r.get("respuesta") or "")[:900],
            "estado": estado, "confianza": r.get("confianza", ""),
            "fuentes": " | ".join(r.get("_fuentes") or [])[:300],
            "modelo": MODELO, "motivo": (r.get("motivo") or "")[:400],
        }
        # Solo se registran las que se publicaron o quedaron para revisar: una
        # simulacion no debe bloquear el procesamiento real posterior.
        if estado != "simulada":
            try:
                registrar(fila)
            except Exception as e:
                print(f"[preguntas] aviso: no pude registrar {q.get('id')}: {e}")
        resultados.append(fila)

    return {"pendientes": len(preguntas), "procesadas": len(resultados),
            "resultados": resultados}


# ------------------------------------------------------------------ historial

def _respondidas_por_ia():
    """question_id de las preguntas que efectivamente publicó la IA."""
    filas = almacen.leer_hoja(HOJA_RESPUESTAS, COLS_RESPUESTAS)
    return {str(f.get("question_id")) for f in filas
            if str(f.get("estado", "")).strip() == "publicada"}


def sincronizar_historial(ml, callback=None):
    """
    Sube a la Sheet **todas** las preguntas de la cuenta con su respuesta,
    hayan sido respondidas por la IA o por una persona.

    Es idempotente por `question_id`: correrlo de nuevo solo agrega lo nuevo.
    Las que estaban sin responder y ya se respondieron se actualizan, para que
    el historial no quede con huecos.
    """
    if callback:
        callback("Trayendo preguntas de MercadoLibre...")

    crudas, offset = [], 0
    while offset < TOPE_OFFSET_PREGUNTAS:
        r = ml.get("/questions/search", seller_id=ml.user_id, limit=50,
                   offset=offset, sort_fields="date_created", sort_types="DESC")
        lote = r.get("questions") or []
        if not lote:
            break
        crudas.extend(lote)
        offset += 50
        if offset >= (r.get("total") or 0):
            break

    por_ia = _respondidas_por_ia()
    # str() no es decorativo: gspread devuelve los question_id de la hoja como
    # ENTEROS, y aca se comparan contra `str(q["id"])`. Sin normalizar, ninguna
    # existente matchea, todas se toman como nuevas y la hoja se duplica entera
    # en cada corrida. Asi llego a tener 4.000 filas para 1.006 preguntas.
    existentes = {str(f.get("question_id")): f
                  for f in almacen.leer_hoja(HOJA_HISTORIAL, COLS_HISTORIAL)}
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Cache de títulos: varias preguntas caen sobre la misma publicación.
    titulos = {}
    if callback:
        callback(f"{len(crudas)} preguntas. Armando el historial...")

    nuevas, actualizadas = [], 0
    for q in crudas:
        qid = str(q.get("id"))
        item_id = q.get("item_id")
        respuesta = ((q.get("answer") or {}).get("text") or "").strip()

        if item_id not in titulos:
            try:
                titulos[item_id] = (ml.get(f"/items/{item_id}",
                                           attributes="title").get("title") or "")
            except Exception:  # noqa: BLE001
                titulos[item_id] = ""

        fila = {
            "question_id": qid,
            "fecha_pregunta": (q.get("date_created") or "")[:19].replace("T", " "),
            "item_id": item_id,
            "publicacion": titulos.get(item_id, "")[:70],
            "comprador": (q.get("from") or {}).get("nickname", ""),
            "pregunta": (q.get("text") or "").strip()[:500],
            "respuesta": respuesta[:900],
            "respondida_por": ("IA" if qid in por_ia
                               else ("persona" if respuesta else "")),
            "estado_ml": q.get("status", ""),
            "sincronizado": ahora,
        }

        vieja = existentes.get(qid)
        if vieja is None:
            nuevas.append(fila)
        elif not str(vieja.get("respuesta", "")).strip() and respuesta:
            # Estaba sin responder y ahora tiene respuesta: hay que actualizarla.
            existentes[qid] = fila
            actualizadas += 1

    ok, detalle = True, ""
    if actualizadas:
        # Reescribimos completo: gspread no tiene update-por-clave.
        todas = list(existentes.values()) + nuevas
        todas.sort(key=lambda f: f.get("fecha_pregunta") or "")
        ok, detalle = almacen.reescribir_hoja(HOJA_HISTORIAL, COLS_HISTORIAL, todas)
    elif nuevas:
        nuevas.sort(key=lambda f: f.get("fecha_pregunta") or "")
        ok, detalle = almacen.append_hoja(HOJA_HISTORIAL, COLS_HISTORIAL, nuevas)

    return {"revisadas": len(crudas), "nuevas": len(nuevas),
            "actualizadas": actualizadas,
            "total": len(existentes) + len(nuevas),
            "ok": ok, "detalle": detalle}


def historial():
    return almacen.leer_hoja(HOJA_HISTORIAL, COLS_HISTORIAL)


VACIAS = {"respondidas_ia": 0, "resueltas_a_mano": 0,
          "derivadas_a_persona": 0, "con_error": 0,
          "pendientes_verificados": False, "preguntas_unicas": 0,
          "filas_registro": 0,
          "tasa_automatica": 0.0, "historial_total": 0,
          "historial_respondidas": 0, "historial_por_ia": 0,
          "historial_por_persona": 0, "error": ""}


def metricas(incluir_historial=False, ml=None):
    """
    Contadores acumulados. `respondidas_ia` es el numero que interesa: cuantas
    preguntas contesto la IA sola desde que se activo.

    **Se cuenta por pregunta, no por fila.** El registro guarda una fila por
    intento, asi que una pregunta que fallo y despues se publico deja dos —y
    la vieja seguia contando como abierta para siempre. Medido el 18/08/2026:
    109 filas para **73 preguntas**, y el contador de pendientes decia 40
    donde habia 6. Vale el ultimo estado de cada `question_id`, igual que en
    `bandeja()`. (Los numeros son de la cuenta argentina, donde aparecio.)

    **Y `derivadas_a_persona` se cruza contra MercadoLibre si se pasa `ml`.**
    Una pregunta que alguien contesto desde el panel de ML sigue abierta en el
    registro, porque nadie la vuelve a tocar. Sin ese cruce la app mostraba
    "Esperando respuesta: 40" arriba y "No queda ninguna pregunta pendiente"
    abajo, en la misma pantalla: las 6 que quedaban ya estaban respondidas
    afuera. Sin `ml` se devuelve lo que dice el registro y
    `pendientes_verificados` queda en False, para que quien muestre el numero
    sepa que no esta confirmado.

    Por defecto **no** lee el historial completo: son ~1.000 filas y esto se
    llama en cada render de la seccion, lo que ademas de lento hace pegarle al
    limite de lecturas de la API de Sheets. Las metricas del historial se piden
    aparte, solo en la vista que las muestra.

    Nunca lanza: si la Sheet no responde devuelve ceros con el motivo en
    `error`, para que la app avise en vez de caerse.
    """
    m = dict(VACIAS)
    try:
        reg = almacen.leer_hoja(HOJA_RESPUESTAS, COLS_RESPUESTAS)
    except Exception as e:
        m["error"] = f"No pude leer el registro de la IA: {str(e)[:200]}"
        return m

    # El ultimo estado de cada pregunta es el que vale.
    ultimo = {}
    for f in reg:
        ultimo[str(f.get("question_id"))] = f

    estados = [str(f.get("estado", "")).strip() for f in ultimo.values()]
    publicadas = estados.count("publicada")
    a_mano = estados.count("publicada_por_persona")
    abiertas = [q for q, f in ultimo.items()
                if str(f.get("estado", "")).strip() in ESTADOS_ABIERTOS]
    # La tasa es sobre **todo lo que la IA proceso**, no sobre publicadas mas
    # abiertas: si no, cada vez que una persona cierra un caso derivado el
    # denominador baja y la tasa sube sola.
    procesadas = len(ultimo)

    verificados = False
    if ml is not None:
        try:
            vivas = {str(q.get("id")) for q in pendientes_respondibles(ml)}
            abiertas = [q for q in abiertas if q in vivas]
            verificados = True
        except Exception:
            # Si ML no contesta se muestra lo del registro, avisando que no
            # esta confirmado. Es peor no mostrar nada.
            pass

    m.update({
        "respondidas_ia": publicadas,
        "resueltas_a_mano": a_mano,
        "derivadas_a_persona": len(abiertas),
        "pendientes_verificados": verificados,
        "preguntas_unicas": len(ultimo),
        "filas_registro": len(reg),
        "con_error": estados.count("error_al_publicar") + estados.count("error_tecnico"),
        "tasa_automatica": (publicadas / procesadas) if procesadas else 0.0,
    })

    if incluir_historial:
        try:
            hist = historial()
            con_resp = [f for f in hist if str(f.get("respuesta", "")).strip()]
            por_ia = sum(1 for f in con_resp
                         if str(f.get("respondida_por", "")) == "IA")
            m.update({"historial_total": len(hist),
                      "historial_respondidas": len(con_resp),
                      "historial_por_ia": por_ia,
                      "historial_por_persona": len(con_resp) - por_ia})
        except Exception as e:
            m["error"] = f"No pude leer el historial: {str(e)[:200]}"

    return m


def main():
    if "--historial" in sys.argv:
        ml = Meli(verbose=False)
        r = sincronizar_historial(ml, callback=lambda m: print(f"  {m}"))
        print(f"\n  revisadas   {r['revisadas']:>6}")
        print(f"  nuevas      {r['nuevas']:>6}")
        print(f"  actualizadas{r['actualizadas']:>6}")
        print(f"  total en la Sheet {r['total']:>6}")
        if not r["ok"]:
            print(f"\n  ERROR: {r['detalle']}")
            return 1
        m = metricas()
        print(f"\n  respondidas por la IA:      {m['historial_por_ia']}")
        print(f"  respondidas por una persona: {m['historial_por_persona']}")
        return 0

    publicar_real = "--publicar" in sys.argv
    ml = Meli(verbose=False)

    if publicar_real:
        print("MODO PUBLICAR: las respuestas se van a publicar en "
              "MercadoLibre.\n")
    else:
        print("MODO SIMULACION: redacta pero no publica nada.\n")

    r = procesar(ml, publicar_de_verdad=publicar_real,
                 callback=lambda i, t, q: print(f"  {i}/{t} pregunta {q['id']}..."))
    if "error" in r:
        print(f"\n{r['error']}")
        return 1

    print(f"\npendientes: {r['pendientes']} | procesadas: {r['procesadas']}\n")
    for f in r["resultados"]:
        print(f"  [{f['estado']}] confianza={f['confianza']}")
        print(f"    P: {f['pregunta'][:100]}")
        print(f"    R: {f['respuesta'][:180] or '(no responde)'}")
        print(f"    motivo: {f['motivo'][:140]}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
