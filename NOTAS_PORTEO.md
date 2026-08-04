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
  histórico de respuestas propias, y acá hay 33 en toda la historia de la
  cuenta. No hay con qué. Dejar `ia_activa` apagado.
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

Ya está cargado en `tramos.py`.

## El segundo escalón: el envío, y por qué invierte el consejo

Desde **$1.000** el envío deja de pagarlo el comprador y pasa a pagarlo el
vendedor. Medido sobre 125 órdenes de 90 días, cruzando por **precio unitario**
(no por total de la orden, que ensucia el corte):

| Precio unitario | Órdenes | Con costo de envío para el vendedor |
|---|---|---|
| menos de $1.000 | 100 | **0** |
| $1.000 o más | 25 | **25** |

Mediana de lo que paga el vendedor: **$160** (rango $125-$232, promedio $183).
El cache queda en `costos_envio.json`.

**Los dos escalones caen en el mismo precio y van en direcciones opuestas:**

| Al cruzar $1.000 | |
|---|---|
| Cargo fijo | $40 → $0 · **ahorrás $40** |
| Envío | comprador → vos · **te cuesta $160** |
| Neto | **perdés ~$154 por unidad** |

Para que cruzar $1.000 empate habría que llegar a **$1.178** (+18% sobre $999),
muy por encima del 8% que el análisis se permite subir.

Por eso en Uruguay **el escalón de $1.000 es una trampa, no una oportunidad**,
al revés que en Argentina. `tramos.py` ahora modela el envío dentro de `neto()`
y busca en las dos direcciones. Resultado sobre el catálogo real: **19
sugerencias, todas de BAJA** hacia $999, y **cero** de suba. Las de baja son
doblemente buenas: dejan más neto y encima el producto se vende más barato.

### Bugs encontrados al portar (los dos afectan también a CRAFTERS Argentina)

**Bug 1 — cruzar hacia un cargo fijo más caro.** `analizar()` sugería cruzar un
escalón cada vez que el neto mejoraba, sin verificar que el cargo fijo bajara.
Cruzar hacia arriba a un tramo más caro mejora el neto igual (subiste el
precio), pero quedarse un peso por debajo deja todavía más.

Corregido con el filtro `coste(tope) < coste(precio)`, donde `coste` es cargo
fijo **más** envío. Aplicado también en el repo de Argentina, donde eliminó
casi la mitad de las sugerencias que había.

**Bug 2 — el envío tratado como constante por SKU.** Más profundo.
`precio_minimo()` recibía el envío como un número fijo por SKU (el promedio
histórico), no como una función escalonada del precio. Para un producto que hoy
está debajo del umbral ese promedio es ~0, así que cuando la herramienta lo
empuja por encima sigue calculando con envío cero — justo cuando el envío
aparece.

El patrón se repite en los dos países: **el umbral de envío gratis coincide con
el escalón donde el cargo fijo se hace cero**, y el envío pesa bastante más que
el cargo que se ahorra. O sea que cruzar ese umbral hacia arriba cuesta plata,
y lo que conviene es quedarse justo debajo.

Corregido en los dos repos: el envío pasa por `tramos.envio_a_cargo()`, que lo
evalúa al precio candidato, en `precio_minimo.py`, `ventana.py`, `plata.py` y
`buybox.py`.

**Rentabilidad nunca estuvo afectada**: mide el envío realmente pagado. El
error estaba en las proyecciones, no en la medición de lo que ya pasó.

> Los números concretos de la corrida sobre el catálogo argentino no van acá:
> este repo es público y son de otra empresa. Están en el repo de CRAFTERS y en
> los mensajes de sus commits.

## Costos por SKU — resuelto, y no era donde parecía

Los costos **no están en Contabilium**. De los 596 productos del ERP, sólo
**3 tienen `CostoInterno` cargado** (0,5%, medido jul 2026); los otros 593
están en cero. El propio proyecto *Contabilidad - Claude* ya lo dice en
`productos.py` y por eso usa `costo_efectivo = costo_sheet.fillna(erp)`.

La verdad vive en la hoja **`costos_historico`** de la Google Sheet de ese
proyecto: append-only, con `fecha_vigencia_desde` por fila, validada contra el
catálogo de Contabilium al cargarse. Son **netos sin IVA**, en las mismas
unidades que `PrecioFinal/1.22`.

`costos_gsu.py` lee esa hoja y resuelve la vigencia por SKU (el costo más
nuevo que ya empezó; las vigencias futuras se ignoran a propósito).
`rentabilidad.costos_guardados()` lo usa primero y cae a la hoja `costos`
local si no está configurado, así que **todas las pantallas lo toman solas**:
Rentabilidad, Precio óptimo, Buy Box y Plata sobre la mesa.

**Cobertura al 31/07/2026: 266 de los 293 SKU activos en ML (91%).** Los 27
que faltan están listados por `python costos_gsu.py`.

Configuración: sección `[gsheets_costos]` en los secrets, apuntando a la
planilla de Contabilidad. Es **otra** planilla que la de `[gsheets]`, que
guarda tokens y auditoría — por eso `costos_gsu.py` abre la suya y no reusa
`almacen._abrir()`.

## Pendiente

### 1. Cifras de escala de CRAFTERS en la prosa — HECHO

**Hecho.** Reemplazadas por las uruguayas en `full.py`, `conciliacion.py`,
`precio_minimo.py`, `preguntas.py`, `buybox.py`, `espejos.py`,
`conversion.py`, `promociones.py`, `tutorial_suprabond.py` y la app.

Quedó todo medido, no estimado: 438 activas, 447 SKU, 216 compitiendo en
página de catálogo, 33 preguntas históricas, ML factura ~$21.000/mes.

### 2. Tabla de familias de SKU (`mayoristas.py`)

`_assets/sku_familia_subgrupo.xlsx` **se borró a propósito**: era la tabla de
CRAFTERS, con SKU tipo `CR016000000CDBAR40`. La sección Mayoristas no va a
funcionar hasta que exista la tabla equivalente de Suprabond, o hasta que se
cambie la regla de familia por otra que sirva para los SKU de Contabilium.

### 3. Assets de marca — HECHO

Generados desde el logo institucional de GSU, el mismo que usan Contabilidad y
Gestión de Vendedores (`assets/logo.png`, 2000×730 RGBA, idéntico en los tres
proyectos).

- `logo_suprabond.png` — 560×204, recortado al contenido con 5% de margen.
- `icono_suprabond.png` — 256×256, solo la "s" de *suprabond*.

**Los dos van compuestos sobre BLANCO opaco, no sobre transparente.** El
original es tinta negra sobre fondo transparente: en tema oscuro "suprabond"
desaparece contra el `#0e1117` de Streamlit. Es el mismo problema que en
CRAFTERS pero al revés — allá la tinta era clara y había que componer sobre
negro.

**Trampa del recorte del ícono:** en la columna de la "s" (x 45-278) la palabra
*grupo* y la palabra *suprabond* **se tocan verticalmente**, así que recortar
por posición mete la panza de la "o" de *grupo* dentro del favicon. Se separan
por color, que sí es limpio: *grupo* es gris (luminancia ~170) y *suprabond*
negro (~10), sin nada en el medio. El umbral está en 90.

Para regenerarlos: la "s" sola mide 233×429 en el original.

### 4. Credenciales y Google Sheet — HECHO

`credentials.txt` está cargado, la autorización OAuth hecha, y **la Google
Sheet ya está configurada y andando**: los tokens y la auditoría viven en la
hoja `tokens_ml` y `auditoria`, no en archivos locales. Verificado el
03/08/2026 leyendo y escribiendo contra la planilla.

`secrets_para_streamlit_cloud.txt` está armado con el service account
**inline**, que es lo que hace falta en la nube — un `service_account_json_path`
apunta a un archivo que en Streamlit Cloud no existe. Probado abriendo la
planilla con esa credencial tal cual está en el archivo.

`[gsheets]` y `[gsheets_costos]` apuntan **a propósito al mismo
spreadsheet_id** (`gsu-contabilidad`): cada uno usa hojas distintas adentro
(`tokens_ml` y `auditoria` por un lado, `costos_historico` por el otro) y la
planilla ya estaba compartida solo con Mariano y el service account, así que
el refresh token no queda expuesto. Si algún día conviene separarlas, alcanza
con cambiar el `spreadsheet_id` de `[gsheets]` y migrar esas dos hojas.

**Trampa del redirect_uri**: `https://www.suprabond.com.uy` **no sirve**. Ese
dominio hace un 301 a `https://www.suprabond.com` y en el camino se pierde el
`?code=`, así que la autorización nunca se completa (el navegador queda en la
home, sin código a la vista). El que funciona es `https://www.suprabond.com`,
que devuelve 200 y conserva el query string.

### 5. Preguntas con IA — no da

Descartado con datos: la cuenta tiene **33 preguntas en toda su historia**.
El motor arma su base con BM25 sobre las respuestas propias, y con 33 no hay
con qué. Dejar `ia_activa` apagado y no deployar
`responder_preguntas.yml`.
