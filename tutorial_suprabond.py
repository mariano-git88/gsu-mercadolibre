"""
tutorial_suprabond.py — Contenido del tutorial de las herramientas de
MercadoLibre de SUPRABOND.

Se pinta dentro de un st.dialog (modal) cuando el usuario hace click en
el botón "Tutorial". Está pensado para quien opera la herramienta: explica
qué hace cada sección, cómo armar las planillas y qué significan los avisos.
Sin tecnicismos innecesarios.

Si hay que actualizar el contenido, editar acá sin tocar `suprabond_app.py`.
"""

import streamlit as st


def render() -> None:
    """Renderiza el tutorial completo dentro del modal."""

    st.markdown(
        """
### ¿Qué hace esta app?

Trece secciones, cada una para una cosa:

| Sección | Para qué sirve |
|---|---|
| **Plata sobre la mesa** | Todo lo accionable en una lista, ordenado por pesos. Es la que abre la app |
| **Reporte semanal** | Cómo vino la semana y qué hay que resolver. Es la pantalla del lunes |
| **Preguntas** | Responder las consultas de los compradores, con ayuda de IA |
| **Alertas** | Lo que necesita atención: stock por agotarse y reclamos por producto |
| **Ganar la venta** | Buy Box del catálogo y promociones que ML te ofrece |
| **Precios** | Cambiar precios de muchas publicaciones de una, desde una planilla |
| **Mayoristas** | Cargar descuentos por cantidad con reglas por familia de producto |
| **Stock ML** | Lo mismo pero con las unidades publicadas en MercadoLibre |
| **Control de stock** | Llevar tu propia cuenta de unidades, con historial de movimientos |
| **Rentabilidad** | Ver cuánto ganás realmente con cada producto, después de todo lo que se lleva MercadoLibre |
| **Precio óptimo** | Entre lo que necesitás cobrar y lo que podés cobrar: un precio sugerido por SKU |
| **Competencia** | Quién vende más barato cada producto y en qué posición estás |
| **Oportunidades** | Siete análisis para encontrar dónde estás dejando plata |

**Modifican la cuenta de verdad**: Precios, Mayoristas, Stock ML, Ganar la
venta, Precio óptimo y Preguntas (publica las respuestas). Ninguna aplica nada
sin mostrarte antes, en pantalla, exactamente qué va a pasar.

**Solo de consulta**: Reporte semanal, Alertas, Rentabilidad, Competencia y
Oportunidades.

**Control de stock** no toca nada en MercadoLibre: es un registro tuyo, aparte.
"""
    )

    st.divider()
    st.markdown(
        """
### Dónde ver qué cambió

El botón **🆕 Novedades**, arriba a la derecha al lado del Tutorial, abre el
registro de actualizaciones: qué se agregó o se arregló y cuándo. En el
encabezado también figura la fecha de la última actualización.

---

### La regla más importante

**Lo que no está en la planilla, no se toca.**

Si subís una planilla con 20 productos, se modifican esos 20 y nada más.
El resto del catálogo queda intacto. No hace falta que la planilla tenga
todos los productos.
"""
    )

    st.divider()
    st.markdown(
        """
### Cómo armar la planilla

Un Excel o CSV con **dos columnas**:

| SKU | Precio |
|---|---|
| CR0160000000000PAH4B | 132913 |
| CR01600000LLV7CKIT5R | 93224 |

- La primera columna puede llamarse **SKU**, *Código* o *Artículo*.
- La segunda, **Precio** (o *Valor*, *Importe*) para la sección de precios;
  **Stock** (o *Cantidad*, *Unidades*) para la de stock.
- Si los nombres no coinciden, la app te deja elegir a mano qué columna es
  cuál, así que no es grave.

También podés poner el **código de la publicación** (`MLU123456789`) en vez
del SKU, si querés apuntarle a una publicación puntual.

**Los números se escriben como quieras**: `1234`, `1.234,50` o `$ 1234`.
La app los entiende igual.
"""
    )

    st.divider()
    st.markdown(
        """
### Paso a paso

1. **Subí la planilla.**
2. **Apretá "Simular".** No cambia nada todavía: solo calcula.
3. **Mirá la tabla.** Te muestra publicación por publicación el valor actual,
   el nuevo y por qué la eligió.
4. **Escribí tu nombre** y tildá la confirmación.
5. **Aplicar.**

Podés descargar la simulación en CSV antes de aplicar, para revisarla
tranquila o mandársela a alguien.
"""
    )

    st.divider()
    st.markdown(
        """
### Un SKU puede tener varias publicaciones

Esto es lo que más confunde, así que va con detalle.

En el catálogo de SUPRABOND **el mismo producto suele estar publicado varias
veces** (con títulos distintos, para aparecer más en las búsquedas). Casi la
mitad de las publicaciones son duplicados.

Entonces, cuando ponés un SKU en la planilla, la app tiene que decidir a
cuál de todas aplicarle el cambio:

**Para precios:**

- Si entre las publicaciones de ese SKU **hay algunas con cuotas sin interés
  y otras sin cuotas** → se actualizan **solo las que NO tienen cuotas**
  (las "Clásicas"). Las Premium quedan como están.
- Si son **todas iguales** → se actualizan todas.

> **Por qué:** ofrecer cuotas sin interés te cuesta unos 12 puntos más de
> comisión. No conviene mezclar esos precios con los de las publicaciones
> comunes.

**Para stock:**

- MercadoLibre maneja el stock **compartido** entre las publicaciones del
  mismo producto. Si actualizás una, las demás se mueven solas. La app ya
  cuenta con eso.
- Pero algunos SKU tienen el stock **separado en varios lugares**. Ahí la app
  **no toca nada** y te lo marca como *ambiguo*, porque poner el mismo número
  en cada lugar **duplicaría las unidades** y terminarías vendiendo lo que no
  tenés.
"""
    )

    st.divider()
    st.markdown(
        """
### Qué significa cada aviso de la tabla

| Aviso | Qué pasó | Qué hacer |
|---|---|---|
| **actualizar** | Todo bien, se va a aplicar | Nada |
| **revisar** | El precio cambia más de 50% | Fijate que no sea un cero de más. Si está bien, tildá la casilla para incluirlas |
| **sin_cambio** | El valor de la planilla es igual al que ya tiene | Nada, se saltea |
| **no_encontrado** | Ese SKU no existe entre las publicaciones activas | Revisá que esté bien escrito o que la publicación no esté pausada |
| **ambiguo** | El SKU tiene el stock separado en varios lados | Hay que definir cuál corresponde. Avisá para resolverlo |
| **sin_destino** | Está en Full | El stock de Full lo maneja MercadoLibre según lo que tenga en su depósito |
| **valor_invalido** | El número no se entiende, o es negativo | Corregí la planilla |
| **duplicado_en_planilla** | El SKU aparece dos veces | Se usa el primero. Limpiá la planilla si el valor correcto era el otro |

> Los marcados como **revisar** **no se aplican** salvo que tildes la casilla
> que dice "Incluir también las marcadas para revisar". Es la red de seguridad
> contra un error de tipeo.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Plata sobre la mesa

Es la que abre la app. No trae análisis nuevo: junta en **una sola lista** lo
accionable que antes estaba repartido en seis secciones, ordenado por plata.
Cada fila dice cuánto es, qué hay que hacer y en qué sección se hace.

**Dos números que no se suman**, y por eso están separados:

- **Facturación parada** — lo que hoy no entra porque el producto no se puede
  vender (agotados, sin publicación activa).
- **Margen en juego** — lo que se pierde o se deja de ganar vendiendo.

Sumarlos daría un número grande y sin sentido: uno es facturación y el otro es
margen.

> **El aviso más importante de la pantalla** es el de conflicto: un producto
> puede estar a la vez *para reponer* y *perdiendo plata en cada unidad*.
> Reponerlo **aumenta** la pérdida. Esos aparecen marcados y hay que arreglar
> el precio o el costo antes de comprar.

Las estimaciones asumen el mismo volumen que el período medido. Son referencias
de tamaño para priorizar, no proyecciones.

---

### Sección Reporte semanal

Es la pantalla que abre la app, y está pensada para el lunes a la mañana: se
lee en dos minutos y dice si la semana estuvo bien o mal y qué hay que hacer.

Tiene tres bloques, en ese orden a propósito:

1. **Cómo vino la semana**, siempre contra la anterior. Un número solo no dice
   nada: $14 millones puede ser una buena o una mala semana.
2. **Para resolver esta semana**: productos por quedarse sin stock, preguntas
   sin responder y reclamos abiertos, ordenados por plata.
3. **Qué se movió**: lo que más facturó y lo que **vendía y este período no
   vendió nada**.

**Sobre el período.** Por defecto compara la última semana **cerrada** (lunes a
domingo) contra la anterior. Es a propósito: si comparás una semana a medias
contra una entera, siempre parece que las ventas se derrumbaron. También podés
pedir los últimos 14 o 30 días corridos.

Tarda unos 10 segundos. Si destildás *Incluir reclamos* tarda menos, porque
identificar el producto de cada reclamo cuesta una llamada por envío.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Alertas

Dos vistas de lo que necesita atención. Ninguna modifica nada.

**Stock crítico.** La pregunta no es cuánto stock hay sino **cuántos días
queda**: 40 unidades de algo que vende 1 por semana están bien, 40 de algo que
vende 10 por día se agotan el jueves. Está ordenado por **plata en riesgo**, o
sea lo que ese producto deja de facturar por cada semana sin stock.

El estado más importante es **"sin publicación activa"**: MercadoLibre **pausa
sola** la publicación cuando el stock llega a cero, así que el producto que se
agotó desaparece de las activas y deja de aparecer en cualquier listado. Es el
caso del producto que se agotó y nadie repuso.

> El stock se agrupa por producto de MercadoLibre, no por publicación. Las
> publicaciones espejo comparten las mismas unidades, así que sumarlas a todas
> contaría lo mismo varias veces.

**Reclamos.** Qué productos concentran los reclamos. Lo que importa **no es el
total sino la tasa**: un producto que reclama el 8% de lo que vende, cuando la
cuenta promedia menos del 3%, tiene un problema de producto, de ficha o de
embalaje.

Por eso los productos con pocas ventas quedan filtrados por defecto: un reclamo
sobre 3 ventas da 33% y no significa nada.

> Algunos reclamos no se pueden asociar a un producto. Son los que apuntan a un
> pago en vez de a un pedido o a un envío: MercadoLibre no expone ese camino.
> Aparecen contados aparte como *sin producto identificado*.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Ganar la venta

Dos vistas, las dos de consulta.

**Buy Box.** Casi la mitad de tus publicaciones activas compiten en una
**página de catálogo**: ahí todos los vendedores comparten la misma publicación
y MercadoLibre muestra a uno solo. El que gana se lleva casi todas las ventas y
el resto queda escondido detrás de *otras opciones de compra*. No es una
diferencia de posición: es vender o no vender.

Lo que más confunde de esta pantalla es que **el precio para ganar casi nunca
es el precio del que gana hoy**, y suele ser bastante más bajo. No es un error.
MercadoLibre pondera el precio junto con los beneficios de la publicación —
Full, envío gratis y cuotas. Si el ganador los tiene y vos no, para empatarle
tenés que compensar con precio.

> **La diferencia entre lo que cobra el ganador y lo que tendrías que cobrar
> vos es, en pesos, lo que te cuesta no tener esas palancas.** Está en la
> columna *Costo de no tener palancas*.

De ahí salen dos diagnósticos que piden cosas opuestas:

- **Perdés por precio** — el ganador está más barato. Se arregla con precio.
- **Perdés estando más barato** — ya cobrás menos y perdés igual. Bajar más es
  tirar plata: lo que falta son las palancas.

La columna *Te quedaría* dice qué te deja por unidad vender al precio para
ganar, **antes del costo de la mercadería**. Sirve para descartar los casos
donde ganar el Buy Box directamente da negativo.

**Bajar precios en lote.** Usa la planilla de costos guardada —la misma que
subiste en Rentabilidad, no hay que volver a cargarla— para calcular el margen
real al precio del Buy Box y aplicar la baja **solo donde el margen aguanta**. Definís el criterio una
vez —margen mínimo, baja máxima, unidades mínimas— y se selecciona sola.

**Cómo elegir qué se toca.** Tenés tres formas y se combinan:

- **Por criterio** — rentabilidad mínima, baja máxima y unidades mínimas.
- **Por marca** — elegís una o varias y se aplica solo a esas.
- **A mano** — tildás filas en la tabla. Si tildás alguna, se aplica **solo a
  esas**; si no tildás ninguna, van todas las que cumplen.

**Vender a pérdida a propósito.** La rentabilidad mínima **puede ir en
negativo**. Poniéndola en −5% entran las publicaciones donde ganar el Buy Box
deja hasta 5% de pérdida. Puede tener sentido para entrar a una página de
catálogo o para liquidar, y la pantalla te avisa cuántas quedan a pérdida antes
de aplicar.

> Esto **cambia precios de verdad**. Hay tres candados que no se pueden abrir
> desde la pantalla: nunca se baja más del 35%, el margen nunca puede quedar
> por debajo de −50%, y **las publicaciones sin costo cargado se saltean** —
> sin costo no se sabe si se gana o se pierde.

**El escalón del cargo fijo.** MercadoLibre cobra un porcentaje más un cargo
fijo por unidad, y ese cargo salta en escalones. En Uruguay el más grande está
en **\\$1.000**, y acá **juega a favor de bajar**: por encima el cargo fijo es
cero pero el envío pasa a pagarlo el vendedor (~\\$160); por debajo pagás \\$40
de cargo y el envío lo paga el comprador. O sea que bajar de \\$1.050 a \\$999 te
deja **más** plata, no menos. El cálculo del margen ya lo tiene en cuenta.

**Promociones.** MercadoLibre le ofrece a cada publicación un menú de campañas
(relámpago, temporada, descuentos sugeridos). Cada una queda como *candidata*
hasta que la tomás.

Lo primero que hay que mirar es la columna **Pone ML**: en algunos tipos
MercadoLibre pone parte del descuento de su bolsillo, así que al comprador le
baja el precio más de lo que te cuesta a vos. La campaña **¡Gánale a la
competencia!** es la respuesta directa a las publicaciones donde perdés el Buy
Box por precio: en vez de bajarlo vos solo, ML cofinancia la baja.

**Alta automática por criterio.** Definís una regla una vez —por ejemplo
*SUPRABOND pone como máximo 5% y MercadoLibre pone más que SUPRABOND*— y la
herramienta selecciona sola qué publicaciones sumar. Después las revisás y se
dan de alta en lote.

Dos cosas que hace sola para que no te pises:

- Solo toma ofertas **disponibles y sin tomar**: las que ya están activas no se
  vuelven a dar de alta.
- Si una publicación califica para varias promociones, toma **la que deja más
  plata por unidad**. Sumarla a todas sería pisar una con otra.

> El alta **cambia el precio que ve el comprador** y queda registrada en la
> auditoría. También podés tomarlas a mano desde el panel de MercadoLibre;
> esta sección no reemplaza ese camino.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Rentabilidad

**La planilla de costos se sube una sola vez.** Queda guardada en la planilla
de Google y la usan tanto Rentabilidad como Buy Box. Solo hace falta volver a
subirla cuando cambian los costos: ahí tildás *Subir otra* y reemplaza a la
anterior.

Arriba de todo te dice cuántos SKU tiene la guardada y de cuándo es.

**Otros conceptos.** Además de lo que cobra MercadoLibre, el margen descuenta
tres costos de estructura: **impuestos 10%, logístico 10% y general 5%**. Se
aplican como porcentaje del ingreso sin IVA y los podés cambiar arriba de la
tabla.

La tabla muestra las dos cosas: *Margen antes de otros* (solo costo, comisión y
envío) y el *Margen* final. La diferencia entre las dos es grande —en una
muestra el margen promedio pasó de 33% a 8%— así que conviene mirar el final
antes de decidir un precio.

Subís una planilla con el **costo** de cada producto:

| SKU | Costo |
|---|---|
| CR0160000000000PAH4B | 48000 |

La app le suma el precio al que se está vendiendo hoy en MercadoLibre y
**los cargos reales que cobró ML en cada venta de ese producto**: comisión,
recargo por cuotas, cargo fijo y envío. No son estimaciones de una tabla:
son los números de tus ventas.

Con eso te muestra cuánto te queda de cada venta, en pesos y en porcentaje.

**Ojo con el IVA.** Si tus costos están **sin IVA** y los precios de
MercadoLibre lo incluyen, elegí *21%* en el selector. Si no, el margen te va
a dar más alto de lo que realmente es.

**Qué mirar primero:** los productos con margen negativo aparecen arriba de
todo. Son los que se están vendiendo a pérdida.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Control de stock

Lleva **tu propia cuenta** de unidades. No modifica el stock de MercadoLibre:
sirve para saber qué tenés realmente y poder rastrear cada movimiento.

**Cómo arranca:** cargás una vez el **stock inicial** (planilla con SKU y
cantidad). De ahí en más, la app va descontando las ventas sola.

**Cuándo baja una unidad:** apenas MercadoLibre confirma el pago. Si la orden
después se cancela, la unidad **vuelve sola** al stock.

**Las devoluciones no vuelven solas.** Quedan en la pestaña *Devoluciones*
esperando que alguien las revise. Recién cuando marcás que la unidad está
**apta**, se suma al stock. Si está rota o no se puede revender, marcás
*descarte* y no suma nada.

**Cuando entra mercadería** (compra a proveedor, o el conteo físico no
coincide) la cargás en *Ingresos*. Usá **compra** para mercadería nueva y
**ajuste** para corregir diferencias, así después se puede distinguir en el
historial.

**Se actualiza solo** cada 15 minutos, de 8 a 21 hs, de lunes a sábado.
También podés forzarlo con **↻ Sincronizar ventas**. Lo que se venda de noche
o el domingo entra en la primera sincronización siguiente: no se pierde nada,
porque cada corrida revisa los últimos días.

**Si un SKU da negativo** casi siempre es una de dos: nunca se cargó su stock
inicial, o entró mercadería que no se registró en Ingresos.

> Las ventas de Shopify y los pedidos especiales no se leen automáticamente.
> Cargalos como **ajuste** en Ingresos, con cantidad negativa.
"""
    )


    st.divider()
    st.markdown(
        """
### Sección Mayoristas

Carga **descuentos por cantidad** en muchas publicaciones de una. Funciona con
reglas: se define un descuento por familia de producto (linternas, selladores,
grifería…), por SKU puntual o una regla general, y la herramienta toma el
precio publicado de cada artículo y arma los tramos sola.

Las reglas se editan en la hoja `reglas_mayoristas` de la planilla, sin ayuda
técnica. Gana la de **menor orden**, así que lo específico pisa a lo general.

> Si el panel de MercadoLibre te da error al calcular precios mayoristas, la
> carga desde acá funciona igual.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Precio óptimo

Es la que contesta la pregunta que ninguna otra contestaba sola: **qué precio
le pongo a este producto**. Junta tres cuentas que antes estaban en pantallas
distintas:

- el **piso** — abajo de ahí no llegás al margen objetivo;
- el **techo útil** — arriba de ahí perdés la página de catálogo, que es donde
  se lleva las ventas el que gana;
- el **escalón de cargo fijo** — dentro de la ventana no todos los precios
  rinden igual.

**Los seis casos**, que piden cosas bien distintas:

| Caso | Qué significa |
|---|---|
| **Ventana amplia** | Podés acomodar el precio *y* quedarte con la página. El único caso donde no se resigna nada |
| **Bajar para ganar** | Ganar la página exige bajar. El margen aguanta pero se resigna neto por unidad |
| **Sin ventana** | Ganar la página exige vender bajo tu piso. No es problema de precio sino de costo |
| **Ya ganás** | Tenés la página; solo mirar si podés acomodar el precio sin perderla |
| **Catálogo en otra publicación** | La página la pelea otra publicación del mismo SKU |
| **Fuera de catálogo** | No hay página que ganar, manda el piso |

**"Bajar para ganar" queda afuera de la selección por defecto**, y es a
propósito: resigna neto por unidad y solo conviene si el volumen extra lo
compensa. Eso no sale de ningún dato de la API, así que la decisión es tuya.

**"Catálogo en otra publicación" es una trampa que conviene entender.** El
precio se aplica **por SKU** —y toca las publicaciones que manda la regla de
Clásica/Premium— pero el Buy Box se pelea **por publicación**. De los 721 SKU
con página de catálogo, la publicación que pelea la página es la misma que
toca el cambio de precio en solo 186. En el resto, el consejo de Buy Box es
informativo: esas se resuelven desde *Ganar la venta*, una por una.

**Ojo con el escalón de \\$1.000, que en Uruguay es una trampa.** Arriba de
\\$1.000 el cargo fijo de MercadoLibre es cero, y suena a oportunidad, pero
desde ahí el envío lo pagás vos: ahorrás \\$40 y te cargás ~\\$160. Cruzarlo
cuesta unos \\$154 por unidad, y para empatar habría que llegar a \\$1.178.
La columna *Cruza escalón* te marca esos casos para que los mires dos veces.

**Cómo usarla.** Poné un cambio máximo bajo (15-20%), mirá cuántos entran,
tildá filas si querés elegir a mano, simulá y aplicá. La simulación pasa por
el mismo motor que la sección Precios: respeta la regla de las publicaciones
espejo y marca como *revisar* todo lo que supere el 50%.

> **Dos cosas que la herramienta no sabe.** Si el mercado va a pagar el precio
> nuevo, y cuánto volumen se gana o se pierde. El *Impacto* de la tabla asume
> el mismo volumen que el período medido: es una referencia de tamaño, no una
> proyección.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Competencia

Te dice **quién vende más barato cada producto**, a cuánto, y en qué posición
estás vos.

Hay dos formas de usarla:

- **Tus más vendidos** — elegís cuántos y de qué período, y busca solo los
  artículos que más vendiste. No hace falta armar ninguna planilla.
- **Subiendo una planilla** con los códigos de barras que quieras vigilar.

Además **corre sola todos los días** y avisa cuando algo cambia: un competidor
que baja el precio, uno nuevo que aparece, o cuando dejás de ser el más barato.
Cada comparación queda guardada, así podés ver cómo evolucionó un precio.

**Un límite importante:** la comparación es contra el catálogo de MercadoLibre.
Si un competidor publica el producto por fuera del catálogo, no aparece.

**Antes de reaccionar a una diferencia grande**, abrí la publicación del
competidor: puede ser otra presentación (una unidad contra un pack) aunque
compartan el catálogo.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Oportunidades

Siete análisis para encontrar dónde estás dejando plata. **Ninguno modifica
nada**: son todos de consulta.

| Vista | Para qué sirve |
|---|---|
| **Visitas vs ventas** | Qué se ve mucho y no vende, y qué vende sin que casi nadie lo vea |
| **Tramos de comisión** | Productos donde subir el precio unos pesos deja más plata neta |
| **Precios espejo** | Publicaciones duplicadas del mismo producto con precios distintos |
| **Factura de ML** | Verifica que lo que te facturan cierre con lo que deberían cobrarte |
| **Envíos** | Productos donde el costo de envío se come el margen |
| **Candidatos a Full** | Por qué productos empezar si se agranda el uso de Full |
| **Salud del catálogo** | Qué publicaciones tienen datos mal cargados |

**Sobre los tramos de comisión:** MercadoLibre cobra un porcentaje **más un
cargo fijo por unidad**, y ese cargo salta en escalones. En Uruguay el que
importa es el de **\\$1.000**, y va al revés de lo que uno esperaría: arriba de
esa línea el cargo fijo desaparece, pero el envío pasa a pagarlo el vendedor y
sale bastante más caro. Por eso las sugerencias que vas a ver son casi todas
**bajas de precio** hasta \\$999 — dejás más neto y además vendés más barato.

**Sobre los candidatos a Full:** en esta cuenta **no hay ni una publicación en
Full**. Las 438 activas están en depósito propio, así que no hay con qué
comparar y esta vista no te va a decir cuánto ahorrarías. Sirve como foto de
dónde se va la plata de envío, por si algún día se evalúa entrar.

Un dato para tener presente: el envío se paga **desde \\$1.000**, y el corte es
limpio. Sobre 125 órdenes de 90 días, ninguna de las 100 por debajo de \\$1.000
tuvo costo de envío para vos; las 25 de \\$1.000 para arriba lo tuvieron todas.

**Visitas vs ventas tarda unos 10 minutos**: MercadoLibre solo deja consultar
las visitas de a una publicación por vez.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Preguntas

Responde las consultas de los compradores. Para armar la respuesta usa, en
este orden: **las respuestas que ya dio la cuenta antes**, los datos de la
publicación, las preguntas ya contestadas en ese mismo artículo, y los
documentos o sitios que hayas cargado.

**Si no tiene con qué responder, no inventa.** Deja la pregunta sin contestar
y explica qué le faltó. Una respuesta equivocada queda pública en la
publicación, así que es preferible que la vea una persona.

**Pestañas:**

- **Dashboard** — el estado general y el procesamiento de las preguntas
  pendientes. Podés redactar sin publicar (para probar) o redactar y publicar.
- **Gestión manual** — todas las preguntas sin responder. Escribís la respuesta
  y se publica desde acá. También podés pedirle **un borrador a la IA** para
  una pregunta puntual, editarlo y publicarlo.
- **Historial completo** — todas las preguntas de la cuenta con su respuesta,
  marcando si la contestó la IA o una persona.
- **Registro de la IA** — solo lo que procesó ella, con el motivo de cada
  decisión.
- **Fuentes** — subís PDFs (fichas técnicas, manuales) o páginas web para que
  tenga más información con qué responder.

**La IA contesta sola, sin que nadie abra la app.** Corre cada 15 minutos de 8 a
21 de lunes a sábado, y cada hora las noches y los domingos. Apenas entra una
pregunta la redacta y la publica; **si no puede, la deja en Gestión manual**.
El interruptor `ia_activa` de la planilla la apaga por completo.

**Solo se muestra lo que se puede contestar.** MercadoLibre marca como *sin
responder* preguntas de publicaciones que ya no están activas, pero **no deja
responderlas**: no son trabajo pendiente y por eso no aparecen ni se cuentan en
ningún lado. Si reactivás la publicación —normalmente reponiendo stock— vuelven
a entrar al circuito solas.

**Si una pregunta queda sin responder**, mirá el motivo en *Gestión manual*. Suele
ser información que no está en ningún lado: cargando esa ficha en *Fuentes*,
la próxima la resuelve sola.

**Si una publicación está pausada**, MercadoLibre no deja responder sus
preguntas. Hay que reactivarla primero.

Arriba hay un contador de cuántas respondió la IA sola y cuántas quedaron
para el equipo. Se apaga desde la hoja `config_ia` de la planilla, poniendo
`ia_activa = no`.
"""
    )

    st.divider()
    st.markdown(
        """
### Si algo falla

**"No hay conexión con MercadoLibre"** → El permiso de la app venció o se
revocó. Hay que volver a autorizarla desde una computadora. Avisá a Mariano.

**El catálogo parece desactualizado** → Apretá **"↻ Actualizar catálogo"**
arriba a la derecha. La app guarda el catálogo un rato para andar más rápido;
ese botón lo vuelve a bajar de MercadoLibre.

**Alguna publicación dio error al aplicar** → Aparece en la tabla de
resultados con el motivo. Las demás **sí se aplicaron**: no se cancela todo
por una que falle.

**Quiero saber qué se cambió y cuándo** → Todo queda registrado con el valor
anterior, el nuevo, quién lo hizo y a qué hora. Está en la planilla de Google
de la herramienta, en la hoja `auditoria`.
"""
    )
