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

## Radiografía de la cuenta (31 jul 2026)

Cuenta `CRAFTERSUY`, user_id 1479612053, sitio MLU, reputación 5_green.

**Catálogo — 715 publicaciones**

| | |
|---|---|
| Activas | 438 |
| Pausadas | 170 |
| **En revisión** | **107** (81 `waiting_for_patch`, 26 `forbidden`) |
| SKU distintos | 447 |
| Tipo de publicación | 100% `gold_special` |
| SKU cargado en | `SELLER_SKU` siempre, `seller_custom_field` nunca |
| Publicaciones en **Full** | **0** |
| Con variaciones | 0 |
| SKU con precios ya distintos entre sus publicaciones | 1 |
| SKU con varios destinos de stock | 1 |

**Ventas — 90 días (2 may a 31 jul 2026)**

| | |
|---|---|
| Órdenes | 135 (~1,5 por día) |
| Unidades | 272 |
| Bruto | $165.109 |
| Comisiones ML | $27.489 (**16,6%** promedio) |
| Ticket promedio | $1.223 |
| Facturación de ML, julio | $20.829 |
| Visitas, 30 días | 2.587 |

**Preguntas de compradores**: 33 en toda la historia de la cuenta, 0 sin
responder.

**Endpoints que fallan** (los mismos dos que en Argentina, no es cosa de UY):
documentos del período de facturación (404) y buscador público por nickname
(403).

### Consecuencias para el alcance

- **Full no aplica**: no hay una sola publicación en Full. La sección entera
  y `full.py` sobran.
- **Preguntas con IA no es viable**: el motor se apoya en BM25 sobre el
  histórico de respuestas propias. En Argentina son ~1.585; acá 33. No hay
  con qué. Dejar `ia_activa` apagado.
- **107 publicaciones en revisión** son el 15% del catálogo y no se pueden
  vender. Es el hallazgo más accionable de la radiografía.
- El volumen es **~3% del argentino**. Buena parte de la maquinaria analítica
  está sobredimensionada para 1,5 órdenes por día.

## Escalones del cargo fijo — MEDIDOS

Uruguay tiene la **misma estructura** que Argentina, en otra escala. Comisión
`gold_special` **13%**, igual que en MLA. Cargo fijo por unidad, medido contra
`/sites/MLU/listing_prices` con búsqueda binaria (jul 2026):

| Precio | Cargo fijo |
|---|---|
| menos de $500 | $15 |
| $500 a $749 | $25 |
| $750 a $999 | $40 |
| **$1.000 o más** | **$0** |

Ya está cargado en `tramos.py`. El escalón fuerte es el de **$1.000**.

### Bug encontrado al portar (afecta también a CRAFTERS Argentina)

`tramos.analizar()` sugería cruzar un escalón cada vez que el neto mejoraba,
**sin verificar que el cargo fijo bajara**. Cruzar hacia arriba a un tramo con
cargo más caro mejora el neto igual (subiste el precio), pero quedarse un peso
por debajo del escalón deja todavía más.

En Argentina casi no se notaba porque el caso estrella ($33.000) sí baja el
cargo a cero. En Uruguay saltó a la vista: dos de los tres escalones lo suben.

Corregido acá con `if cargo_fijo(tope) >= cargo_fijo(precio): break`.
Efecto: 26 sugerencias → 11.

**En el repo de CRAFTERS Argentina el bug sigue.** Son 76 de 164 sugerencias
mal (46%), sobre SKU de mucho volumen. Ejemplo `CR0160000000000ZNODG`
(37.365 unidades vendidas): sugiere $22.817 → $24.000, que gana $529 por
unidad, cuando $23.999 gana $1.028. Deja $499 por unidad sobre la mesa.

## Pendiente — números argentinos todavía en el código

### 1. La narrativa del "$33.000"

El hallazgo argentino —arriba de $33.000 el cargo fijo es cero y el envío lo
paga el vendedor— está escrito **a mano** en la explicación de varias pantallas:

- `suprabond_app.py` (Precio óptimo, Oportunidades, Full, la tabla de tramos)
- `tutorial_suprabond.py`
- `full.py`, `precio_minimo.py`, `plata.py`, `buybox.py`

Todos esos textos hay que reescribirlos con los umbrales uruguayos reales.
Ninguno afecta el cálculo, pero todos le mienten al operador si quedan así.

### 2. Cifras de escala de CRAFTERS en la prosa

Aparecen números de la cuenta argentina que no son de Uruguay: "997 SKU",
"3.713 publicaciones", "20 SKU en Full", "ML factura entre $22M y $35M por
mes", "197 SKU vendieron a pérdida", "~1.585 preguntas". Están en docstrings y
en textos de ayuda de `full.py`, `conciliacion.py`, `precio_minimo.py`,
`preguntas.py`, `buybox.py`, `tutorial_suprabond.py` y la app. Se reemplazan
después de la radiografía.

### 3. Tabla de familias de SKU (`mayoristas.py`)

`_assets/sku_familia_subgrupo.xlsx` **se borró a propósito**: era la tabla de
CRAFTERS, con SKU tipo `CR016000000CDBAR40`. La sección Mayoristas no va a
funcionar hasta que exista la tabla equivalente de Suprabond, o hasta que se
cambie la regla de familia por otra que sirva para los SKU de Contabilium.

### 4. Assets de marca

Faltan `_assets/logo_suprabond.png` (horizontal, ~560×138) e
`_assets/icono_suprabond.png` (cuadrado, 256×256, para el favicon). La app
degrada sola: si no están, muestra el texto "SUPRABOND" y un emoji de changuito.

### 5. Credenciales — hechas, pero falta la Google Sheet

`credentials.txt` ya está cargado y la autorización OAuth está hecha. Los
tokens hoy viven en `tokens.json` **local**, que no sobrevive a un reinicio en
Streamlit Cloud. Antes de deployar hay que crear la Google Sheet y configurar
`[gsheets]` en los secrets, como en CRAFTERS.

**Trampa del redirect_uri**: `https://www.suprabond.com.uy` **no sirve**. Ese
dominio hace un 301 a `https://www.suprabond.com` y en el camino se pierde el
`?code=`, así que la autorización nunca se completa (el navegador queda en la
home, sin código a la vista). El que funciona es `https://www.suprabond.com`,
que devuelve 200 y conserva el query string.

### 6. Preguntas con IA — no da

Descartado con datos: la cuenta tiene **33 preguntas en toda su historia**
(Argentina tiene ~1.585). El motor arma su base con BM25 sobre las respuestas
propias y con 33 no hay con qué. Dejar `ia_activa` apagado y no deployar
`responder_preguntas.yml`.
