# MercadoLibre — Suprabond Uruguay

Herramientas de análisis y gestión de precios, stock y publicaciones para la
cuenta de MercadoLibre **Uruguay** (sitio `MLU`) de Grupo Suprabond.

Es una app de Streamlit más un conjunto de módulos que también corren sueltos
desde la terminal. Todo lo que decide precios está medido contra la API y las
ventas reales, no contra tablas teóricas.

Es un porteo del equivalente argentino
([crafters-mercadolibre](https://github.com/mariano-git88/crafters-mercadolibre)).
Qué cambió, qué se volvió a medir y qué falta está en
[`NOTAS_PORTEO.md`](NOTAS_PORTEO.md).

---

## El hallazgo que ordena casi todo

MercadoLibre cobra una comisión porcentual **más un cargo fijo por unidad**, y
ese cargo salta en escalones de precio. Medido contra
`/sites/MLU/listing_prices` con búsqueda binaria:

| Precio | Cargo fijo | Envío |
|---|---|---|
| menos de $500 | $15 | lo paga el comprador |
| $500 a $749 | $25 | lo paga el comprador |
| $750 a $999 | $40 | lo paga el comprador |
| **$1.000 o más** | **$0** | **lo paga el vendedor (~$160)** |

Los dos umbrales caen en **el mismo precio** y tiran para lados opuestos.
Cruzar $1.000 ahorra $40 de cargo fijo y carga ~$160 de envío: **cuesta unos
$154 por unidad**. Para empatar habría que llegar a $1.178, un 18% más caro.

O sea que **el escalón de $1.000 es una trampa, no una oportunidad**, y la
jugada rentable es la inversa: bajar a $999 los productos que están apenas por
encima. Dejan más neto *y* se venden más baratos.

El umbral del envío está medido, no supuesto: de 100 órdenes con precio
unitario por debajo de $1.000, **ninguna** tuvo costo de envío para el
vendedor; de las 25 por encima, **todas**.

---

## Setup

### 1. Dependencias

```bash
pip install -r requirements.txt
```

### 2. La aplicación en el DevCenter

Entrar a **https://developers.mercadolibre.com.uy/devcenter** logueado con la
cuenta de MercadoLibre Uruguay, como **usuario administrador** — con un
colaborador, ML rechaza la autorización con `invalid_operator_user_id`.

| Campo | Qué poner |
|---|---|
| Redirect URI | un HTTPS propio que **no redirija** (ver abajo) |
| Flujos OAuth | tildar `Authorization Code` **y `Refresh Token`** |
| Permisos | tildar **`offline_access`** |
| Requiere PKCE | dejar **destildado** |

> **La Redirect URI tiene que conservar el query string.** Si el dominio
> redirige a otro —por ejemplo un 301 de `.com.uy` a `.com`— el `?code=` se
> pierde en el camino y la autorización nunca se completa: el navegador te deja
> en la home sin código a la vista, y parece que falló ML cuando en realidad el
> código se emitió. Probalo antes:
>
> ```bash
> curl -sIL "https://tu-dominio/?code=TEST" | grep -i "^HTTP/\|^location:"
> ```
>
> Si aparece un `location:`, ese dominio no sirve.

Sin `offline_access`, ML devuelve el access token pero no el refresh token: en
vez de andar seis meses solo, habría que reautorizar a mano cada 6 horas.

### 3. Credenciales

Copiar `credentials.txt.example` a `credentials.txt` y completar `app_id`,
`secret_key` y `redirect_uri` (idéntica a la del panel, carácter por carácter).

### 4. Autorización — una sola vez

```bash
python autorizar.py                     # muestra el link
python autorizar.py "URL_QUE_QUEDO"     # canjea el código
```

El código dura pocos minutos. Después los tokens se renuevan solos.

### 5. Configuración de la app

Copiar `secrets.toml.example` a `.streamlit/secrets.toml`:

- **`suprabond_password`** — contraseña para entrar a la app.
- **`[gsheets]`** — dónde viven el token y la auditoría. Sin esto, en Streamlit
  Cloud se pierde el refresh token en cada reinicio: el disco es efímero y el
  refresh token es de un solo uso.
- **`[gsheets_costos]`** — la planilla de costos del sistema de Contabilidad.

---

## De dónde salen los costos

**No de Contabilium.** El ERP tiene el campo `CostoInterno`, pero en la
práctica está vacío: 3 de 596 productos lo tienen cargado.

La verdad vive en la hoja `costos_historico` de la Google Sheet del proyecto de
Contabilidad: append-only, con `fecha_vigencia_desde` por fila, validada contra
el catálogo de Contabilium al cargarse, y **neta sin IVA**.

`costos_gsu.py` la lee y resuelve la vigencia por SKU — el costo más nuevo que
ya empezó, ignorando los de vigencia futura. `rentabilidad.costos_guardados()`
lo enchufa solo en Rentabilidad, Precio óptimo, Buy Box y Plata sobre la mesa.

```bash
python costos_gsu.py     # cuántos SKU de ML tienen costo, y cuáles no
```

Los precios de ML vienen **con** IVA y los costos **sin**, así que las
pantallas descuentan 22% antes de comparar. Si algún día se cargan costos con
IVA, poner el selector en "Sin descontar".

---

## El piso de las marcas propias

Una publicación de **Suprabond, Bulit o Somerset** no se vende por debajo de
**1,85 veces el precio de lista de venta de Contabilium**. No es una cuenta de
margen: es una decisión comercial, así que es un **piso duro** — ninguna
pantalla lo perfora, ni siquiera para ganar el Buy Box.

"Costo" acá es el `PrecioFinal` del concepto en Contabilium, o sea lo que
Suprabond le cobra al comercio. No es el `CostoInterno`, que está vacío.
Ejemplo medido: `CDB AR 80 P` está a $328, así que su piso es $606,80.

```bash
python lista_gsu.py     # cobertura y cuántas publicaciones lo violan
```

Medido el 04/08/2026 sobre el catálogo real:

| | |
|---|---|
| Activas de las tres marcas | **419 de 438** (96%) |
| Con piso calculable | **410** (98%) |
| Sin cruce con el ERP | 9 — packs y variantes que no existen con ese código |
| **Ya publicadas por debajo del piso** | **168 de 410** (41%) |

El cruce es directo: el `SELLER_SKU` de MercadoLibre y el `Codigo` de
Contabilium son el mismo string. La **marca sale de MercadoLibre** (atributo
`BRAND`), no del ERP, que no tiene un campo de marca utilizable. Las 19
publicaciones de reventa (Bosch, Aqualaf, Dremel y 4 sin marca) quedan fuera
de la regla a propósito.

Necesita `[contabilium]` en los secrets. Sin eso las pantallas siguen andando
sin piso.

### Subir las que están por debajo

La vista *Subir al piso de marca* (dentro de **Precio óptimo**) las sube en
lote. **Las que están en una promoción activa quedan afuera**: ahí hay un
precio que el comprador está viendo ahora, y subirle el precio de lista por
debajo deja la oferta incoherente — en algunos tipos ML recalcula el descuento
sobre el precio nuevo y la promo se encarece sola.

El estado de promociones se pregunta **publicación por publicación** y no
recorriendo las campañas, que sería más rápido: hay tipos de oferta que no
cuelgan de una campaña (`PRICE_DISCOUNT` viene sin `id`) y por ese camino no
se verían. Si una consulta falla, esa publicación **no se toca**.

Como en Tramos, antes de escribir se relee el precio vivo de cada publicación.

---

## Uso

```bash
streamlit run suprabond_app.py
```

Desde la terminal:

```bash
python explorar.py            # qué endpoints responden contra la cuenta
python catalogo.py            # baja y cachea las publicaciones
python ventas.py 90           # resumen de ventas del período
python tramos.py              # oportunidades de precio por escalón
python tramos.py --umbrales   # vuelve a medir los escalones contra la API
```

Casi todo cachea en disco. `catalogo.json`, `costos_envio.json` y los `.csv` de
salida están en el `.gitignore` y se regeneran solos.

---

## Módulos

| Archivo | Qué hace |
|---|---|
| `meli.py` | Cliente de la API: OAuth con refresh automático, backoff en 429, scroll para pasar el tope de 1000, multiget de a 20 |
| `almacen.py` | Dónde viven el token y la auditoría: Google Sheet o archivos locales |
| `catalogo.py` | Baja las publicaciones y las cachea |
| `resolver.py` | Dado un SKU, decide a qué publicaciones aplicar el cambio |
| `actualizador.py` | Motor de precio y stock desde planilla: leer → simular → aplicar |
| `ventas.py` | Órdenes de un período, con partición recursiva del rango |
| `rentabilidad.py` | Cargos reales por SKU cruzados con costos |
| `costos_gsu.py` | Costos desde el sistema de Contabilidad |
| `lista_gsu.py` | Piso de precio de las marcas propias: Costo × 1,85 |
| `tramos.py` | Optimizador de precios por escalón de comisión **y envío**, y los aplica |
| `test_precios.py` | Las reglas de precio que no se pueden romper |
| `precio_minimo.py` | El piso: precio mínimo viable por SKU |
| `ventana.py` | Piso + Buy Box + escalón en un precio sugerido |
| `plata.py` | Junta lo accionable ordenado por plata |
| `buybox.py` | Buy Box del catálogo |
| `promociones.py` | Campañas que ML ofrece, con su aporte |
| `publicidad.py` | Product Ads: campañas, anuncios y reglas |
| `panel_ads.py` | Escribe publicidad por el panel interno (la API no deja) |
| `publicidad_cron.py` | Apaga los anuncios que no se bancan |
| `promos_planilla.py` | Descuentos en lote a una campaña propia, desde planilla |
| `competencia.py` | Mejor precio por EAN vía catálogo |
| `mayoristas.py` | Precios por cantidad según reglas |
| `stock_control.py` | Control de stock propio e idempotente |
| `alertas_stock.py` | Días de cobertura y plata semanal en riesgo |
| `reclamos.py` | Reclamos por producto y tasa sobre unidades |
| `reporte.py` | El período contra el anterior, y qué resolver |
| `conversion.py` | Visitas vs ventas por publicación |
| `espejos.py` | Publicaciones duplicadas con precios distintos |
| `envios.py` | Productos donde el envío se come el margen |
| `salud.py` | Problemas de datos del catálogo |
| `faq.py` | Qué preguntan los compradores y qué falta en las fichas |
| `conciliacion.py` | Factura de ML contra comisiones calculadas |
| `preguntas.py` | Agente que responde preguntas de compradores |
| `autorizar.py` | Autorización OAuth inicial |
| `explorar.py` | Mapea qué endpoints andan contra la cuenta |

Escriben en MercadoLibre: Precios, Mayoristas, Stock, Preguntas, Ganar la venta,
Precio óptimo y Promos por planilla. El resto es solo lectura; Control de stock
es un registro propio que no toca ML.

---

## Descuentos en lote desde una planilla

`promos_planilla.py` carga un descuento por SKU a una **campaña propia**
(`SELLER_CAMPAIGN` con sub-tipo `FLEXIBLE_PERCENTAGE`), que es el único tipo
donde el porcentaje lo elige el vendedor. La planilla lleva una columna con
**SKU, EAN o código MLU** y otra con el **descuento en porcentaje**.

```bash
python promos_planilla.py                       # las campañas propias
python promos_planilla.py C-MLU815824           # qué publicaciones admite
python promos_planilla.py C-MLU815824 lista.xlsx  # simula la planilla
```

Lo medido contra la cuenta real el 03/08/2026, con una prueba controlada sobre
una publicación de cero ventas que después se revirtió:

- **La campaña se crea desde el panel de MercadoLibre.** Por API no se puede:
  `POST /seller-promotions/users/{user_id}` contesta **200 con cuerpo vacío y no
  crea nada**. Es un falso positivo — quien mire el código de respuesta cree que
  la creó.
- **El rango de descuento lo fija ML por publicación, y no es un porcentaje
  fijo.** Sobre un artículo de $164 el mínimo era 10,1% y sobre uno de $11.314,
  5%. Por eso el rango se lee de ML y no se calcula. Pasarse contesta 400
  `ERROR_CREDIBILITY_DISCOUNTED_PRICE`, que suena a "precio raro" pero significa
  "fuera del rango permitido".
- **El alta no lleva `offer_id` y tampoco lo devuelve**, al revés que las
  promociones que ofrece ML. El campo del precio es `deal_price`.
- **El mismo POST corrige un descuento ya cargado.** No hay que dar de baja para
  cambiar un porcentaje mal puesto.
- **El paginado ignora `offset` en silencio**: hay que pasar el token
  `searchAfter`. Sin eso se ven 50 publicaciones elegibles cuando hay 458.
- **Todo lo que se lee tarda ~30 segundos en reflejarse.** Verificar enseguida
  muestra el estado viejo y hace parecer que la escritura no funcionó.

---

## Decisiones de diseño que no son obvias

- **El envío se modela como escalón del precio, no como constante por SKU.** Es
  el bug que hacía recomendar cruzar el umbral: un producto que hoy vende por
  debajo tiene promedio de envío ~0, y al empujarlo por encima se seguía
  calculando con cero, justo cuando el envío aparece.
- **Cruzar un escalón sólo se sugiere si baja el costo total** (cargo fijo más
  envío). Sin esa condición cualquier escalón cercano "mejora el neto", pero
  sólo porque subiste el precio: quedarse un peso por debajo deja más.
- **La simulación es obligatoria antes de aplicar.** `aplicar()` consume el
  DataFrame que produjo `simular()`, no la planilla cruda.
- **Cambios de precio de más del 50% se marcan y no se aplican** salvo que el
  operador tilde una casilla aparte. Atrapa el cero de más.
- `resolver_stock()` devuelve **una sola** publicación por grupo a propósito: el
  stock se propaga entre las que comparten `user_product_id`.
- La simulación vive en `st.session_state`, porque si no se pierde al tocar
  cualquier widget.
- Se usa `segmented_control` y no `st.tabs`, que derrama contenido en los
  reruns.

---

## Publicidad

La cuenta tiene **un anunciante** (`CRAFTERSUY`, id 72307) y **ninguna campaña
todavía**. Medido el 05/08/2026 por dos vías: `campaigns/search` contesta
`404 advertiser_campaigns_not_found` y `ads/search` devuelve 200 con cero
resultados.

Está portado para que esté listo el día que se prenda la primera campaña.
Hasta entonces la sección se ve vacía, y el workflow **no tiene cron** — una
corrida semanal gastaría minutos de Actions para no encontrar nada. Cuando
haya campañas, descomentar el `schedule` en `.github/workflows/publicidad.yml`.

**Los umbrales de las reglas no están calibrados contra esta cuenta**, porque
no hay con qué. Son los de Argentina; el único valor en pesos (`gasto_minimo`)
se bajó de $5.000 a $500, que son ~1,4 tickets promedio de acá. Cuando haya un
mes de gasto real, revisarlos.

**La escritura no va por la API.** MercadoLibre no habilita la escritura de
Product Ads para la aplicación: contesta `401 User does not have permission to
write` aun con `urn:ml:mktp:ads:/read-write` concedido. `panel_ads.py` usa el
endpoint interno del panel con la cookie `ssid`, que va en los secrets bajo
`[ads]`. Sin esa sección, todo simula y no apaga nada.

En Uruguay pasa lo mismo, **verificado el 05/08/2026**: crear la primera
campaña por API contesta el mismo `401`. O sea que no es de la cuenta argentina
ni de un anunciante en particular — la aplicación no tiene escritura de Product
Ads en ningún sitio.

**El orden encadena y está medido.** Sin campañas, MercadoLibre no genera los
anuncios: `ads/{item_id}` contesta `404 Ads not found` para las 23 candidatas.
Y sin anuncio no hay `ad_group_id`, que es lo único con lo que se puede sumar
una publicación a una campaña. Así que primero va la campaña, después aparecen
los anuncios, y recién ahí se asignan.

La cookie del panel es **por cuenta y por país**: la de Argentina lleva el
user_id argentino adentro y contra `ads.mercadolibre.com.uy` redirige al login.
Para Uruguay hace falta la sesión de la cuenta uruguaya.

Consecuencia práctica: **la campaña se crea desde el panel web**, a mano.
`publicidad.crear_campana()` queda igual, por si ML habilita la escritura algún
día, pero hoy devuelve `(False, "401 ...")`. Los anuncios se agregan después
con `panel_ads.py`, que necesita la cookie `ssid` en `[ads]`.

---

## Qué no aplica en esta cuenta

Verificado con datos, no supuesto:

- **Full**: cero publicaciones, toda la logística es depósito propio.
- **Preguntas con IA**: el motor arma su base con BM25 sobre las respuestas
  propias, y la cuenta tiene 33 preguntas en toda su historia. Dejar
  `ia_activa` apagado.
- **Mayoristas**: falta la tabla de familias para los SKU de Contabilium.

---

## Deploy en Streamlit Cloud

1. Apuntar la app a `suprabond_app.py`.
2. Pegar el contenido de `.streamlit/secrets.toml` en Settings → Secrets. El
   service account de Google va **inline**, nunca como path a un `.json`: ese
   archivo está en el `.gitignore` y no existe en la nube.
3. Configurar `[gsheets]`, si no el refresh token se pierde en cada reinicio.

Los GitHub Actions del repo esperan un secret `GSU_ML_SECRETS_TOML` con el
mismo contenido.

---

## Seguridad

Nunca se suben al repositorio: `credentials.txt`, `tokens.json`,
`.streamlit/secrets.toml` ni `.gsheets/`. Están todos en el `.gitignore`, y
tampoco aparecen en el historial.
