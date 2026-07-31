#!/usr/bin/env python3
"""
Contesta las preguntas nuevas sola. La invoca GitHub Actions.

    python preguntas_cron.py            -> responde y sincroniza el historial
    python preguntas_cron.py --simular  -> redacta sin publicar (para probar)

Es lo que convierte la seccion Preguntas en un producto de verdad: hasta ahora
alguien tenia que abrir la app y apretar un boton, asi que una pregunta de un
sabado a la noche esperaba al lunes. La regla del negocio es simple —**apenas
entra una pregunta, la IA responde; si no puede, la deja para una persona**— y
eso solo se cumple si corre solo.

No hay logica nueva: `preguntas.procesar()` ya decide bien, es idempotente por
`question_id` y detecta las publicaciones inactivas antes de gastar una llamada
al modelo. Aca solo se lo llama seguido y se deja registro.

Dos cosas que hace ademas de responder:

  - **Sincroniza el historial** en la misma corrida. Antes habia que apretar un
    boton aparte, con lo cual el historial quedaba viejo justo cuando mas se lo
    necesitaba: es la fuente con la que la IA redacta las respuestas nuevas.
  - **Respeta el interruptor** `ia_activa` de la planilla. Si esta apagado no
    publica nada, y lo dice en el log en vez de fallar.
"""

import sys
import traceback
from datetime import datetime

import preguntas as preg
from meli import Meli, MeliError


def main():
    simular = "--simular" in sys.argv
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ahora}] preguntas_cron — {'SIMULACION' if simular else 'en vivo'}")

    ml = Meli(verbose=False)

    if not simular and not preg.ia_activa():
        # No es un error: es el interruptor de la planilla, y esta puesto a
        # proposito. Se avisa y se sale con exito.
        print("  La IA esta APAGADA (config_ia -> ia_activa). No se publica "
              "nada. El historial igual se sincroniza.")
    else:
        pend = preg.pendientes(ml)
        print(f"  Preguntas sin responder segun ML: {len(pend)}")

        if pend:
            r = preg.procesar(
                ml, publicar_de_verdad=not simular,
                callback=lambda i, t, q: print(f"    {i}/{t} · {q.get('id')}"))

            if isinstance(r, dict) and r.get("error"):
                print(f"  ERROR: {r['error']}")
            else:
                filas = r if isinstance(r, list) else r.get("resultados", [])
                conteo = {}
                for f in filas:
                    conteo[f.get("estado", "?")] = conteo.get(
                        f.get("estado", "?"), 0) + 1
                if not filas:
                    print("  Nada nuevo: todas ya estaban procesadas.")
                for estado, n in sorted(conteo.items(), key=lambda x: -x[1]):
                    print(f"    {estado:<24} {n}")
                # Lo que quedo para una persona se hace visible en el log:
                # si nadie mira la app, este es el unico lugar donde aparece.
                para_persona = [f for f in filas
                                if f.get("estado") in preg.ESTADOS_ABIERTOS]
                for f in para_persona:
                    print(f"    -> queda para una persona: {f.get('question_id')} "
                          f"({f.get('estado')}) {str(f.get('motivo',''))[:90]}")

    # ---------------------------------------------------------- historial
    print("  Sincronizando el historial...")
    try:
        res = preg.sincronizar_historial(ml, callback=lambda m: print(f"    {m}"))
        if isinstance(res, dict):
            print(f"    {res}")
    except Exception as e:
        # Que falle el historial no puede tirar abajo la corrida: las
        # respuestas ya se publicaron.
        print(f"    AVISO: no se pudo sincronizar el historial: {str(e)[:200]}")

    print("  Listo.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"ERROR de MercadoLibre: {e}")
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
