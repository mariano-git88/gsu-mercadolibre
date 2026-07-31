# MercadoLibre API — SUPRABOND

Acceso por API a la cuenta de MercadoLibre de SUPRABOND (Argentina / MLA), para
poder pedir análisis desde la Terminal y después construir herramientas encima.

Mismo espíritu que `_exploracion-api-contabilium`: primero mapeamos qué da la
API de verdad, después construimos.

---

## Setup (se hace una sola vez)

### 1. Crear la aplicación en el DevCenter

Entrá a **https://developers.mercadolibre.com.ar/devcenter** logueado con la
cuenta de MercadoLibre de SUPRABOND.

> Importante: tiene que ser el **usuario administrador** de la cuenta, no un
> colaborador/operador. Si entrás con un colaborador, ML rechaza la
> autorización con el error `invalid_operator_user_id`.

Creá una aplicación nueva y completá:

| Campo | Qué poner |
|---|---|
| Nombre | `SUPRABOND Analytics` (o lo que quieras) |
| Descripción | Uso interno: análisis de ventas y publicaciones |
| **Redirect URI** | `https://www.crafters.com.ar` |
| **Flujos Oauth** | tildar `Authorization Code` **y `Refresh Token`** |
| Requiere PKCE | dejar **destildado** (el código está preparado así) |
| Negocios | tildar **`Mercado Libre`** (VIS no aplica) |
| Permisos | acceso de **lectura** a órdenes, ítems y métricas |
| Notificaciones (webhooks) | dejar vacío por ahora |

Dos trampas del panel:

- **`Refresh Token` viene destildado por defecto.** Es el equivalente al scope
  `offline_access`. Si no lo tildás, el acceso se muere a las 6 horas y hay que
  reautorizar a mano cada vez. **Es el checkbox más importante de la pantalla.**
- El **Redirect URI no acepta `localhost`** (tira "La dirección debe ser
  válida"): ML exige un dominio público real. Por eso usamos el de Suprabond.
  No hace falta que la página haga nada — el `code` viaja en la dirección y lo
  copiás de la barra del navegador.

Al guardar te va a mostrar el **App ID** y la **Secret Key**. La Secret Key se
muestra una sola vez — copiala.

### 2. Cargar las credenciales

Copiá `credentials.txt.example` como `credentials.txt` y completá los tres
valores. El `redirect_uri` tiene que ser **idéntico** al del panel (una barra de
más y falla).

### 3. Autorizar

```bash
python autorizar.py
```

Te va a dar un link, lo abrís en el navegador, das "Permitir", y pegás de vuelta
la dirección a la que te redirigió. **Es normal que esa página tire error de
"no se puede acceder al sitio"** — lo único que importa es la dirección.

Listo. A partir de acá el token se renueva solo.

### 4. Verificar

```bash
python explorar.py
```

Prueba todos los endpoints que nos interesan y reporta cuáles andan. Deja el
detalle en `exploracion.json`.

---

## Cómo funciona la autenticación

MercadoLibre usa OAuth 2.0, que es más vueltero que Contabilium:

- El `access_token` **dura 6 horas**.
- El `refresh_token` dura **6 meses** y es de **un solo uso**: cada renovación
  devuelve uno nuevo y mata al anterior.
- `meli.py` guarda ambos en `tokens.json` y renueva solo cuando faltan menos de
  10 minutos para el vencimiento.

Cosas que **invalidan** los tokens y obligan a correr `autorizar.py` de nuevo:

- Cambiar la contraseña de la cuenta de MercadoLibre.
- Regenerar la Secret Key de la app.
- Revocarle los permisos a la app desde el perfil de ML.
- **No usar la API durante 4 meses seguidos.**
- Perder el `tokens.json` (por eso conviene no borrarlo a mano).

---

## Uso desde la Terminal

```python
from meli import Meli

ml = Meli()

# Datos de la cuenta
ml.get("/users/me")

# Órdenes de un período
ml.get("/orders/search", seller=ml.user_id, sort="date_desc",
       **{"order.date_created.from": "2026-07-01T00:00:00.000-00:00"})

# Todas las publicaciones (usa scroll, pasa el límite de 1000)
ids = list(ml.scan_items())

# Detalle en lote (de a 20 por llamada, lo hace solo)
for item in ml.items_detalle(ids, atributos=["id", "title", "price",
                                             "available_quantity", "sold_quantity"]):
    print(item["title"], item["price"])
```

`ml.get()` maneja solo el rate limit (429) y la renovación de token.

---

## Archivos

| Archivo | Qué hace |
|---|---|
| `meli.py` | Cliente: OAuth, renovación automática, paginado, rate limit |
| `autorizar.py` | Autorización inicial (una sola vez) |
| `explorar.py` | Prueba endpoints contra la cuenta real y reporta qué anda |
| `reporte.py` | Reporte semanal: período contra el anterior + qué resolver |
| `alertas_stock.py` | Días de cobertura por SKU y plata semanal en riesgo |
| `reclamos.py` | Reclamos por producto y tasa sobre unidades vendidas |
| `full.py` | Candidatos a Full por plata de envío que queman |
| `buybox.py` | Buy Box del catálogo: quién gana cada página y a qué precio |
| `promociones.py` | Campañas que ML ofrece por publicación, con su aporte |
| `precio_minimo.py` | El **piso**: precio más chico que llega al margen objetivo |
| `ventana.py` | Junta piso + Buy Box + escalón en un precio sugerido por SKU |
| `preguntas_cron.py` | Responde las preguntas nuevas sola; la invocan dos workflows |
| `cambios.py` | Registro de actualizaciones que se muestra en el modal *Novedades* |
| `credentials.txt` | App ID + Secret + Redirect URI (**no se sube a git**) |
| `tokens.json` | Tokens vivos (**no se sube a git**) |

Cada uno corre también suelto desde la Terminal: `python reporte.py`,
`python alertas_stock.py 60`, `python reclamos.py 90`, `python full.py`,
`python buybox.py 150`, `python promociones.py 300`,
`python precio_minimo.py 15`, `python ventana.py 15`.

---

## Endpoints validados contra la cuenta real (28/07/2026)

Cuenta: `SUPRABONDARG` — user_id `422682314` — site MLA — reputación 5_green.

| Qué | Endpoint | Notas |
|---|---|---|
| Datos del vendedor | `/users/me` | |
| Listado de publicaciones | `/users/{uid}/items/search` | `status=active` / `paused`. Offset topa en 1000 → usar `ml.scan_items()` |
| Detalle de publicación | `/items/{id}` | 61 campos |
| Detalle en lote | `/items?ids=...` | máx. 20 por llamada |
| Comisiones por tipo | `/sites/MLA/listing_prices?price=N` | cuánto cobra ML según exposición |
| Órdenes | `/orders/search?seller={uid}` | fecha en **ISO completo** |
| Detalle de orden | `/orders/{id}` | **`order_items[].sale_fee` = comisión de ML** |
| Envíos | `/shipments/{id}` y `/shipments/{id}/costs` | |
| Facturación de ML | `/billing/integration/monthly/periods` | **requiere `group=ML` y `document_type=BILL`**, si no tira 422 |
| Visitas del vendedor | `/users/{uid}/items_visits` | **fecha simple `YYYY-MM-DD`** |
| Visitas por publicación | `/items/{id}/visits/time_window?last=30&unit=day` | |
| Preguntas | `/questions/search?seller_id={uid}` | `status=UNANSWERED` |
| Reclamos | `/post-purchase/v1/claims/search` | **exige al menos un filtro** y solo ordena con `sort=date_desc` |
| Motivo de reclamo | `/post-purchase/v1/claims/reasons/{id}` | traduce `PNR3210` a texto |
| Buy Box | `/items/{id}/price_to_win?version=v2` | precio para ganar, ganador actual y palancas sin usar |
| Producto de catálogo | `/products/{id}` y `/products/{id}/items` | todos los que venden ese producto |
| Promos de la cuenta | `/seller-promotions/users/{uid}?app_version=v2` | campañas abiertas |
| Promos por publicación | `/seller-promotions/items/{id}?app_version=v2` | ofertas `candidate` y en curso |
| Alta en promoción | `POST /seller-promotions/items/{id}?app_version=v2` | **escribe**. `app_version` es obligatorio: sin él, 400 |
| Baja de promoción | `DELETE /seller-promotions/items/{id}` | con `promotion_id` y `promotion_type` |
| Reputación | `/users/{uid}` → `seller_reputation` | métricas de 60 días de ML |

**Trampa de formatos de fecha:** conviven dos formatos y no son intercambiables.
`/orders/search` quiere ISO completo (`2026-07-01T00:00:00.000-00:00`); los de
visitas quieren `YYYY-MM-DD` pelado y tiran 400 con ISO.

### Trampas de reclamos (validadas 30/07/2026)

Este endpoint tiene tres formas de mentirte **sin dar error**, y las tres se
verificaron contra la cuenta:

- **El filtro de fecha se ignora.** `date_created.from` devuelve exactamente el
  mismo total que sin él (18.117 reclamos históricos). La única forma de acotar
  el período es traer ordenado y cortar por fecha en el cliente.
- **Solo `sort=date_desc` ordena.** `date_created_desc`, `-date_created`,
  `sort_by`/`sort_order` no dan error: se ignoran y devuelven del más viejo al
  más nuevo. Con uno de esos se traen reclamos de 2019 creyendo que son de esta
  semana.
- **El reclamo no trae el producto.** Apunta a un `resource` que puede ser
  `order` (directo), `shipment` (una llamada más a `/shipments/{id}` para sacar
  el `order_id`) o `payment`, que **no tiene camino público al pedido** — el
  filtro `payment_id` de `/orders/search` también se ignora y devuelve las
  40.730 órdenes.

`/post-purchase/v1/claims/reasons/{id}` devuelve el código **canónico**, que
puede ser distinto del pedido (`PNR3210` → `PNR9502`). Es el mismo motivo
renumerado, no un error.

### Buy Box — cómo leer `price_to_win`

`price_to_win` **casi nunca coincide con el precio del ganador**, y suele ser
bastante más bajo. No es un error de la API: ML pondera precio y beneficios
juntos (Full, envío gratis, cuotas). Si el ganador los tiene y vos no, para
empatarle tenés que compensar con precio.

**La diferencia `winner.price - price_to_win` es, en pesos, lo que cuesta no
tener esas palancas.** Sobre las 150 publicaciones de catálogo que más venden,
la mediana de esa penalización es **$2.074**.

Hay casos donde `current_price` ya es **menor** que `winner.price` y el estado
sigue siendo `competing`. Ahí bajar el precio no sirve: lo que falta son los
beneficios. `buybox.py` los marca aparte como *perdés estando más barato*.

`version=v2` cambia la forma de `boosts`: en v1 es un dict de booleanos, en v2
una lista de `{id, status, description}` donde `status` es `boosted` (la usás) u
`opportunity` (está disponible y no la usás). Se usa v2 por el texto legible.

### Escrituras masivas — rate limit y corridas cortadas

Con miles de publicaciones (Mayoristas toca 2.281) MercadoLibre corta por rate
limit. Lo que hay puesto:

- **Ninguna publicación puede matar la corrida.** Cada ítem se resuelve a
  OK/ERROR dentro de un `try/except`. Ojo con proteger **todas** las llamadas,
  no solo la escritura: en `mayoristas.aplicar_uno` el `POST` estaba protegido
  pero el `GET` previo de `/items/{id}/prices` no, y por ahí murió una corrida
  entera en la publicación 25.
- **Throttle proactivo:** `PAUSA_ENTRE_ITEMS = 0.25` s entre publicaciones.
  Sale más barato que esperar los backoff.
- **Los 429 tienen presupuesto propio de reintentos** (`intentos_429=8` en
  `meli._request`), aparte de los 5 de los errores comunes, respetando
  `Retry-After`. Antes compartían contador y se agotaba enseguida.
- **Se puede retomar:** `aplicar(..., omitir={item_ids})` saltea lo ya hecho, y
  la app guarda los OK y ofrece reintentar solo los que fallaron.

Si igual una corrida muere, **la auditoría append-only es lo único que dice qué
llegó a aplicarse**.

### Automatizar los cambios por criterio

Las dos herramientas de "Ganar la venta" escriben en la cuenta cuando el
operador confirma. El criterio decide **qué** se toca; la confirmación decide
**cuándo**.

**Bajar precios al del Buy Box** (`buybox.seleccionar` + `buybox.aplicar`).
Candados que no se pueden abrir desde la pantalla:

- `TECHO_DE_BAJA = 0.35`: nunca se baja más de eso, aunque el criterio lo
  permita. Misma idea que `UMBRAL_ALERTA_PRECIO` en `actualizador.py`.
- `PISO_DE_MARGEN = -0.50`: el margen nunca puede quedar por debajo. Un margen
  peor casi siempre es un dato malo, no una decisión.
- Las publicaciones **sin costo cargado se saltean**. Sin costo no se sabe si
  se gana o se pierde.
- Por defecto se excluyen las que **cruzan un escalón de cargo fijo**.

`margen_minimo` **puede ser negativo**: es el piso de rentabilidad aceptado, no
un mínimo de ganancia. Con `-0.05` entran las publicaciones donde ganar el Buy
Box deja hasta 5% de pérdida, algo que puede tener sentido para entrar a una
página de catálogo o para liquidar. El valor se clampea contra `PISO_DE_MARGEN`.

La selección se puede acotar además **por marca** (atributo `BRAND`, presente en
las 1.009 publicaciones de catálogo) y **por lista de `item_id`**, que es lo que
usa la tabla cuando el operador tilda filas a mano.

La comisión al precio nuevo **no** se estima con una regla de tres: el
porcentaje real de cada SKU se despeja de lo que ML cobró
(`(comision_prom - cargo_fijo(precio_prom)) / precio_prom`, así queda bien para
Clásica y Premium) y se le suma el cargo fijo **del tramo del precio nuevo**.

`aplicar()` toca la publicación puntual por `item_id`, **sin pasar por el
resolver de SKU** que usan Precios y Stock. Es a propósito: el Buy Box se gana
por publicación, y dos publicaciones del mismo SKU compiten en páginas de
catálogo distintas.

**Alta en promociones** (`promociones.seleccionar` + `promociones.aplicar`).
Criterio por defecto: SUPRABOND pone ≤ 5% y ML pone más que SUPRABOND. Solo entran
ofertas `candidate`, y si una publicación califica para varias se toma la que
deja más plata por unidad (sumarla a todas sería pisar una con otra).

### Alta en promoción — validado en vivo (30/07/2026)

Prueba controlada sobre `MLA1863956871` (cero ventas históricas, $6.954),
revertida. Nada de esto está en la documentación de ML; salió de la prueba:

- **`offer_id` es obligatorio**, y es el campo que el `GET` devuelve como
  **`ref_id`**. Sin él: `400 "Offer id is required"`.
- **`app_version=v2` es obligatorio** en el POST y en el DELETE. Sin él, `400`.
- La respuesta trae un `offer_id` **nuevo** (`OFFER-…` en vez de `CANDIDATE-…`).
  **Ese es el único con el que se puede dar de baja**, así que hay que
  guardarlo — queda en la auditoría.
- **La baja necesita `offer_id` Y `promotion_id` juntos.** Con solo `offer_id`
  contesta `403 "User doesn't have permissions, you must consume the correct
  access group"`, que es un mensaje engañoso: no falta un permiso, falta el
  otro parámetro. Con solo `promotion_id`, `404`.

**El alta se propaga a los espejos.** Un POST sobre una publicación dio de alta
también a la que comparte su `user_product_id`, en el mismo segundo — igual que
pasa con el stock. Un solo DELETE dio de baja a las dos. Por eso
`seleccionar()` manda **una sola llamada por familia**.

**El listado del item miente.** Después de un alta exitosa,
`/seller-promotions/items/{id}` sigue mostrando la oferta como `candidate` y el
precio viejo. La lectura confiable es
**`/seller-promotions/offers/{offer_id}`**, que devuelve `status.id` =
`started` / `finished`. Es el mismo falso negativo que ya nos pasó con otras
escrituras: si algo "no persistió", sospechar primero del método de lectura.

### Dos tasas de reclamo distintas — no compararlas

`reclamos.py` da **2,80%** y la reputación de ML dice **0,19%**. Las dos están
bien: miden cosas distintas.

- La de `reclamos.py` cuenta **todos** los tipos (`cancel_purchase`,
  `mediations`, `returns`) sobre las unidades vendidas del período. Sirve para
  comparar productos entre sí.
- La de ML (`seller_reputation.metrics.claims.rate`) cuenta solo los reclamos
  en sentido estricto sobre las ventas de 60 días, y es la que afecta la
  reputación. La cuenta está en 5_green / platinum.

Lo que **no** anda (no bloquea nada):

- `/sites/MLA/search?seller_id=` y `?nickname=` → **403**. ML cerró la búsqueda
  pública por vendedor. Se reemplaza con `/users/{uid}/items/search`.
- `/billing/integration/periods/key/{key}/group/ML/documents` → 404, la subruta
  de documentos cambió. Los montos por período igual salen de `monthly/periods`.
- `/orders/{id}/discounts` da 404 cuando esa orden no tuvo descuentos: es
  respuesta normal, no una falla.
- `/items/{id}/health`, `/quality/v1/items/{id}` y `/items/{id}/moderations`
  → 404. No hay score de calidad de publicación por API.
- `/sites/MLA/search?q=` → **403** también para búsqueda por texto: el buscador
  público está cerrado del todo, no solo el filtro por vendedor.
- **No hay endpoint de recomendación de Full.** Se probaron siete rutas
  plausibles (`/users/{uid}/stock/fulfillment`,
  `/users/{uid}/items/fulfillment_recommendations`, `/fbm/recommendations`,
  `/sites/MLA/inventory_recommendations` y variantes): todas 404 o 403. Por eso
  `full.py` no estima ahorro, ordena por tamaño del premio.

## Las herramientas

```bash
streamlit run suprabond_app.py
```

Once secciones:

| Sección | Qué hace | ¿Escribe en ML? |
|---|---|---|
| **Reporte semanal** | La pantalla del lunes: cómo vino la semana contra la anterior y qué hay que resolver | no |
| **Preguntas** | Respuestas a compradores con IA. Destacada en naranja en el selector | **sí** (publica respuestas) |
| **Alertas** | Stock por agotarse y reclamos por producto | no |
| **Ganar la venta** | Buy Box del catálogo y promociones disponibles | **sí** (baja precios y da de alta en promos) |
| **Precios** | Cambio masivo de precios desde planilla | **sí** |
| **Mayoristas** | Precios por cantidad según reglas | **sí** |
| **Stock ML** | Cambio masivo de stock desde planilla | **sí** |
| **Control de stock** | Registro propio de unidades, con historial | no (registro propio) |
| **Rentabilidad** | Margen por SKU con cargos reales | no |
| **Precio óptimo** | Ventana de precio por SKU (piso + Buy Box + escalón) y cambio en lote | **sí** (cambia precios) |
| **Competencia** | Mejor precio por EAN | no |
| **Oportunidades** | Siete análisis de plata sobre la mesa | no |

El resaltado naranja de **Preguntas** se hace por CSS con `nth-of-type(2)` sobre
`[data-testid="stButtonGroup"]`: **si se reordena la lista de secciones, hay que
mover el selector junto con ella.**

Precios y stock siguen siempre el mismo flujo, sin atajos:
subir planilla → **simular** → revisar → confirmar → aplicar.
Todo cambio aplicado queda en `auditoria.csv` con el valor anterior.

### Reglas de resolución SKU → publicación

El SKU que manda es el atributo **SELLER_SKU**. Un SKU puede tener varias
publicaciones (el catálogo tiene muchos espejos), así que:

- **Precio**: si entre las publicaciones del SKU conviven Premium (`gold_pro`) y
  Clásica (`gold_special`), se actualizan **solo las Clásicas**. Si son todas del
  mismo tipo, se actualizan todas.
- **Stock**: se agrupa por `user_product_id`. Si el SKU tiene **varios**, no se
  toca y se reporta: poner el mismo número en cada uno duplicaría el stock.
  Las publicaciones en Full quedan siempre afuera.

### Escritura — verificado contra la cuenta real (28/07/2026)

- `PUT /items/{id}` con `{"price": N}` y con `{"available_quantity": N}` funciona.
- **El stock se propaga solo** a todas las publicaciones que comparten
  `user_product_id`: se actualiza una y las demás se mueven solas. Por eso
  `resolver_stock()` devuelve una sola publicación por grupo — no es un
  descuido.
- Probado sobre publicaciones pausadas y revertido al valor original.

### La planilla de costos se guarda

Se sube una vez y queda en la hoja **`costos`** de la Google Sheet (columnas
`sku`, `costo`, `fecha`, `operador`). La usan **Rentabilidad y Buy Box**, y solo
hay que volver a subirla cuando cambian los costos — se reemplaza entera, no se
acumula.

Va a la Sheet por el mismo motivo que los tokens: en Streamlit Cloud el disco es
efímero. `rentabilidad.costos_guardados()` nunca lanza: si la hoja no existe
devuelve vacío y la sección sigue funcionando pidiendo la planilla.

Antes había que subirla en cada sección y en cada visita, porque el
`file_uploader` de Streamlit no sobrevive al rerun.

### Precio mínimo viable — dos escalones cruzados

`precio_minimo.py` despeja el precio más chico que llega a un margen objetivo.
No se puede resolver con una sola cuenta porque hay **dos escalones**:

- el **cargo fijo de ML**, que salta por tramos de precio y arriba de $33.000
  es cero;
- el **logístico topeado**, que es porcentual hasta $90.000 de ingreso y de ahí
  para arriba es un monto fijo de $9.000.

La ecuación cambia de forma en cada combinación, así que se resuelve en cada
régimen y se toma el menor precio que **de verdad cierra en su propio tramo**
(incluidos los bordes, que a veces son la solución). Verificado con casos de
prueba: el precio devuelto siempre cumple el objetivo y siempre es el mínimo
que lo cumple.

Efecto que parece un bug y no lo es: a veces el precio mínimo cae **justo en
$33.000** aunque el producto valga menos, porque cruzar el escalón elimina
$3.005 que ningún aumento chico compensa.

La escritura **no tiene ruta propia**: `planilla_de_precios()` arma la planilla
que consume `actualizador.simular()`, para reusar el resolver de SKU, el aviso
de variaciones mayores al 50% y la auditoría.



### Responder preguntas sola — y el presupuesto de Actions

La regla del producto es: **apenas entra una pregunta la IA responde; si no
puede, la deja para una persona**. Eso solo se cumple si corre solo, así que
`preguntas_cron.py` se invoca desde dos lados:

- **`sincronizar_stock.yml`**, como un paso más (8-21 AR, L-S, cada 15 min).
  Va pegado ahí **por los minutos de Actions**: ese job ya pagó el checkout y
  el `pip install`, que son la mayor parte del minuto, así que el paso extra
  cuesta segundos. Lleva `continue-on-error` para no tirar abajo el sync.
- **`responder_preguntas.yml`**, que cubre el hueco (noches y domingos) **cada
  hora**.

El presupuesto manda: el plan gratuito da 2.000 min/mes y el sync de stock ya
se lleva ~1.456. Un workflow propio cada 15 minutos costaría ~2.100 y no
entraba. Así queda en ~1.864.

> Contrapartida honesta: una pregunta que entra a las 3 de la mañana puede
> esperar hasta una hora. En horario comercial sale en 15 minutos o menos.

### Preguntas que no se pueden contestar

MercadoLibre marca como `UNANSWERED` preguntas de publicaciones que ya no están
activas y **no deja responderlas**. Antes se contaban como pendientes: el
tablero decía "3 sin responder" cuando la única accionable era una.

`pendientes_respondibles()` es la que usa **todo** el circuito —tablero,
`procesar()` y `bandeja()`—, así que esas preguntas no se muestran, no se
cuentan y no gastan una llamada al modelo. `publicacion_inactiva` salió de
`ESTADOS_ABIERTOS` por el mismo motivo. Si la publicación se reactiva, la
pregunta vuelve al circuito sola.

### El historial se duplicaba en cada sincronización

`sincronizar_historial()` decía ser idempotente por `question_id` pero no lo
era: **gspread devuelve los `question_id` de la hoja como enteros** y se
comparaban contra `str(q["id"])`. Ninguna existente matcheaba, todas entraban
como nuevas. La hoja tenía **4.000 filas para 1.006 preguntas** (cada una hasta
4 veces). Se corrigió normalizando con `str()` y se deduplicó la hoja
conservando, por pregunta, la fila con respuesta más reciente.

### Ventana de precio — la trampa SKU vs publicación

`ventana.py` junta el piso (`precio_minimo`), el techo útil (`price_to_win`) y
el escalón de cargo fijo (`tramos`) en un precio sugerido por SKU.

**El precio se aplica por SKU; el Buy Box se pelea por publicación.** De los
721 SKU con página de catálogo, la publicación que pelea la página es la misma
que toca el cambio de precio en **solo 186**. La primera versión usaba la
publicación del resolver para leer el `price_to_win` y clasificaba 535 SKU como
"fuera de catálogo" sin serlo.

Ahora se busca la publicación de catálogo aparte y se marca `buybox_alcanzable`:
cuando es falso, el caso es **"catálogo aparte"** y el consejo de Buy Box es
informativo — esas se resuelven en *Ganar la venta*, publicación por
publicación.

El otro arreglo: "ventana amplia" al principio incluía los casos donde ganar la
página exige **bajar** el precio, y sugería recortes de hasta 34% en productos
sin ventas. Se separó en **"bajar para ganar"**, que queda fuera de la
selección por defecto: resigna neto por unidad y solo conviene si el volumen lo
paga, cosa que la API no sabe.

### Otros conceptos (impuestos, logístico, general)

Además de lo que cobra MercadoLibre, el margen descuenta tres costos de
estructura como porcentaje del **ingreso sin IVA** — la misma base contra la
que se compara el costo, no el precio de lista:

| Concepto | Por defecto |
|---|---|
| Impuestos | 10% |
| Logístico | 10% |
| General | 5% |

Están en `rentabilidad.OTROS_CONCEPTOS` y se pueden cambiar desde la app.

**Los usa también Buy Box**, y tiene que ser así: esa pantalla baja precios de
verdad, y si calculara el margen sin los costos de estructura aprobaría bajas
que Rentabilidad marca como pérdida.

No es un ajuste menor. Sobre una muestra, sumar el 25% movió el margen promedio
de 33% a 8% y los SKU a pérdida de 1 a 19.

### Rentabilidad

Los cargos no se estiman de una tabla: se promedian de lo que ML **efectivamente
cobró** en cada venta histórica de ese SKU (`sale_fee` por unidad + envío que
pagó SUPRABOND, prorrateado por unidades cuando la orden tiene varios SKU).

Los costos de envío se **muestrean** (5 ventas por SKU por defecto) porque es una
llamada por envío. Las ventas sin dato de envío se excluyen del promedio en vez
de contarse como cero, así el costo no queda subestimado; la columna
`cobertura_envio` dice qué proporción tiene dato real.

## Control de stock

Registro propio de unidades, **paralelo al de MercadoLibre** (no lo modifica).
Vive en `stock_control.py` y en las hojas `stock_inicial`, `movimientos` y
`devoluciones` de la Google Sheet.

Reglas:

- La unidad se descuenta **al pagarse la orden**. Si después se cancela, el
  movimiento se revierte solo.
- Las **devoluciones no vuelven solas**: quedan en una bandeja hasta que
  alguien confirme que la unidad está apta para revenderse.
- Compras y ajustes los carga el operador.

**Es idempotente**, y eso es lo que permite correrlo cada 15 minutos: cada
movimiento lleva una clave derivada de la orden y la publicación
(`v:{order_id}:{item_id}`), así que reprocesar el mismo período no duplica
nada. Verificado: correr dos veces sobre el mismo rango agrega 0 movimientos.

La ventana por defecto son varios días hacia atrás, no solo lo nuevo, para
que las **cancelaciones tardías** se enteren.

### Sincronización automática

`.github/workflows/sincronizar_stock.yml` corre cada 15 minutos, de 8 a 21 hs
de Argentina, de lunes a sábado.

> **Por qué no 24/7:** en un repo privado el plan gratuito da 2000 minutos de
> Actions al mes. Cada 15 minutos todo el día serían ~3450 y se cortaría a
> mitad de mes. Esta ventana usa ~950 (cada corrida tarda ~42 s). No se pierde
> ninguna venta: lo de la madrugada o el domingo entra en la primera corrida
> siguiente.

Requiere el secret **`GSU_ML_SECRETS_TOML`** en el repositorio, con el mismo
contenido que los secrets de Streamlit Cloud (service account **inline**, no
como ruta a un archivo, porque el `sa.json` no está en el repo).

## Deploy en Streamlit Cloud

### Por qué hace falta la Google Sheet

El disco de Streamlit Cloud **se borra en cada reinicio**. Dos cosas no pueden
vivir ahí:

1. **El token de ML.** El `refresh_token` es de un solo uso y rota en cada
   renovación. Si se pierde el último, el anterior ya está invalidado y hay que
   correr `autorizar.py` a mano desde una computadora.
2. **La auditoría.** Es el único registro de quién cambió qué precio.

Por eso `almacen.py` los guarda en una Google Sheet. Sin Sheet configurada la
app funciona igual pero avisa con un cartel, y solo conviene usarla local.

### Pasos

1. **Crear la Google Sheet** (una nueva, vacía). El ID es lo que va entre
   `/d/` y `/edit` en la URL. La app crea sola las hojas `tokens_ml` y
   `auditoria`.
2. **Service account de Google**: en Google Cloud, crear uno con la API de
   Sheets habilitada y bajar el JSON. **Compartir la Sheet como Editor con el
   `client_email` del service account** — si no, no puede escribir.
3. **Subir el repo** a GitHub (privado).
4. En **share.streamlit.io**, apuntar la app a `suprabond_app.py`.
5. Cargar los **Secrets** siguiendo `secrets.toml.example`: la contraseña, la
   sección `[mercadolibre]` y la sección `[gsheets]` con el JSON del service
   account pegado inline (en la nube va embebido, nunca como ruta a un archivo).
6. **Autorizar una vez**: correr `python autorizar.py` en local **con el
   `.streamlit/secrets.toml` apuntando a la misma Sheet**. El token queda
   guardado ahí y la app en la nube lo levanta.

### Limitación conocida

Google Sheets no tiene bloqueo de escritura. Si dos personas usan la app al
mismo tiempo y las dos renuevan el token justo en el mismo momento, una puede
invalidar a la otra y habría que reautorizar. Con un solo operador no pasa.

## Qué sigue

Las tres prioridades originales (ventas y facturación, publicaciones y precios,
visitas y conversión) están cubiertas por las diez secciones.

Lo que queda pendiente:

- **Que el reporte semanal llegue solo.** Hoy hay que abrir la app y apretar un
  botón. El objetivo era que llegara sin que nadie se acuerde: un mail los lunes
  a la mañana. La infraestructura ya existe — `reporte.py` corre solo desde la
  Terminal y hay dos GitHub Actions andando (`sincronizar_stock.yml` y
  `monitor_competencia.yml`) que se pueden copiar.
- **Una cuenta fina de Full.** `full.py` ordena por tamaño del premio pero no
  estima ahorro, porque con 20 SKU en Full no hay muestra. Si SUPRABOND manda más
  productos, el mismo módulo va a poder comparar: la columna `comparable` de la
  tabla por franja avisa cuándo se llega a los 15 SKU de cada lado.
- **Los ~585 reclamos más viejos** no son accesibles por API (el listado de
  preguntas y reclamos topa en los más recientes). Solo se pueden sacar
  exportando desde el panel de ML.
