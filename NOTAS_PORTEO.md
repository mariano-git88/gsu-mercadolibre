# Porteo de CRAFTERS (Argentina) a Suprabond (Uruguay)

Este proyecto es una copia de `crafters-mercadolibre` apuntada a MercadoLibre
Uruguay. Acá queda registrado **qué ya se cambió** y, sobre todo, **qué todavía
tiene números argentinos** y no se puede dar por bueno hasta medirlo contra la
cuenta real.

## Ya cambiado

| Qué | Dónde |
|---|---|
| Sitio `MLA` → `MLU` y dominio de autorización `.com.ar` → `.com.uy` | `meli.py` |
| Prefijo del código de publicación, ahora derivado del sitio (`PREFIJO_ITEM`) | `meli.py`, `actualizador.py` |
| IVA de las pantallas: 21% / 10,5% → **22% / 10%** | `suprabond_app.py`, `plata.py`, `precio_minimo.py`, `ventana.py`, `rentabilidad.py` |
| Moneda por defecto al escribir precios mayoristas: `ARS` → `UYU` | `mayoristas.py` |
| Link "Ver la publicación": `articulo.mercadolibre.com.ar` → `.com.uy` | `suprabond_app.py` |
| Marca CRAFTERS → SUPRABOND, y los identificadores derivados | todo el repo |
| `crafters_app.py` → `suprabond_app.py`, `tutorial_crafters.py` → `tutorial_suprabond.py` | — |
| Secret de GitHub Actions: `CRAFTERS_SECRETS_TOML` → `GSU_ML_SECRETS_TOML` | `.github/workflows/` |
| Contraseña de la app: `crafters_password` → `suprabond_password` | `secrets.toml.example` |

Los crones de GitHub Actions **no se tocaron**: Uruguay y Argentina están los
dos en UTC-3 todo el año, así que los horarios siguen siendo correctos.

## Pendiente — números argentinos todavía en el código

### 1. Escalones del cargo fijo (`tramos.py`)

```python
TRAMOS = [(16000, 1250.0), (24000, 2505.0), (33000, 3005.0), (inf, 0.0)]
PORCENTAJE = 0.13
```

Esto es **Argentina, en pesos argentinos**. Hay que volver a medirlo contra
`/sites/MLU/listing_prices`:

```
python tramos.py --umbrales
```

Hasta que eso se corra, todo lo que dependa del escalón está mal: `tramos.py`,
`precio_minimo.py`, `plata.py`, `ventana.py`, `full.py`.

**No es seguro que Uruguay tenga la misma estructura.** Puede que el cargo fijo
no exista, que no se anule arriba de ningún precio, o que los porcentajes de
comisión sean otros. Primero medir, después escribir.

### 2. La narrativa del "$33.000"

El hallazgo argentino —arriba de $33.000 el cargo fijo es cero y el envío lo
paga el vendedor— está escrito **a mano** en la explicación de varias pantallas:

- `suprabond_app.py` (Precio óptimo, Oportunidades, Full, la tabla de tramos)
- `tutorial_suprabond.py`
- `full.py`, `precio_minimo.py`, `plata.py`, `buybox.py`

Todos esos textos hay que reescribirlos con los umbrales uruguayos reales.
Ninguno afecta el cálculo, pero todos le mienten al operador si quedan así.

### 3. Cifras de escala de CRAFTERS en la prosa

Aparecen números de la cuenta argentina que no son de Uruguay: "997 SKU",
"3.713 publicaciones", "20 SKU en Full", "ML factura entre $22M y $35M por
mes", "197 SKU vendieron a pérdida", "~1.585 preguntas". Están en docstrings y
en textos de ayuda de `full.py`, `conciliacion.py`, `precio_minimo.py`,
`preguntas.py`, `buybox.py`, `tutorial_suprabond.py` y la app. Se reemplazan
después de la radiografía.

### 4. Tabla de familias de SKU (`mayoristas.py`)

`_assets/sku_familia_subgrupo.xlsx` **se borró a propósito**: era la tabla de
CRAFTERS, con SKU tipo `CR016000000CDBAR40`. La sección Mayoristas no va a
funcionar hasta que exista la tabla equivalente de Suprabond, o hasta que se
cambie la regla de familia por otra que sirva para los SKU de Contabilium.

### 5. Assets de marca

Faltan `_assets/logo_suprabond.png` (horizontal, ~560×138) e
`_assets/icono_suprabond.png` (cuadrado, 256×256, para el favicon). La app
degrada sola: si no están, muestra el texto "SUPRABOND" y un emoji de changuito.

### 6. Credenciales

`credentials.txt` y `.streamlit/secrets.toml` no existen todavía. Hay que
cargarlos con los datos de la app de **MercadoLibre Uruguay** (panel distinto
al de Argentina) y correr `python autorizar.py` una vez.

### 7. Base de conocimiento de Preguntas

La sección Preguntas se apoya en el histórico de respuestas propias
(BM25 sobre ~1.000 respuestas de CRAFTERS). En Uruguay ese histórico es otro y
puede ser mucho más chico. Hasta ver cuántas preguntas contestadas tiene la
cuenta, conviene dejar el interruptor `ia_activa` en apagado.
