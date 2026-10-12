# 💰 FinanzasPersonalesIA

A comprehensive **Django-based personal finance management system** designed to track monthly cash flows, net worth (patrimony), rental properties, investments, and more. Featuring a modern, responsive UI with real-time financial calculations.

---

## 🚀 Quick Start (5 Minutes)

### 1. Start the Server
**Windows Users:** Double-click `run_server.bat` in the project root.  
**Manual Start:**
```bash
python manage.py runserver
```

### 2. Access the Dashboard
Open your browser and navigate to: [http://localhost:8000/dashboard/](http://localhost:8000/dashboard/)

### 3. Login
- **Username:** `demo_user`
- **Password:** `demo1234`

### Quick access menu
The authenticated top navigation includes quick links to the financial institutions and
investment platforms used most often: Santander, Scotiabank, Bci, Banco Falabella,
Itaú, AFP Uno, AFC, CMF deudas, Fintual, DVA, SoyFocus, and BTG Pactual. They are grouped
by category and each link opens the official site in a new tab.

---

## 📊 Core Modules

| Module | Description |
| :--- | :--- |
| **Dashboard** | Real-time overview of net worth, assets, liabilities, and liquidity. |
| **Gastos Mensuales** | Consolidado de entradas confirmadas y salidas facturadas: cuentas por fecha real y tarjetas por vencimiento. Neat explica cargos bancarios sin sumarlos otra vez. |
| **Movimientos** | Subpágina de Gastos para revisar transacciones y categorías, reclasificar manualmente, clasificar pendientes con IA y consultar cargos no facturados. |
| **Patrimonio** | Asset and liability management with multi-currency support (CLP, USD, UF), including accounts receivable. |
| **Departamentos** | Rental property ROI analysis, mortgage amortization, and tenant tracking. |
| **Inversiones** | Portfolio management for stocks, crypto, and mutual funds. |
| **Préstamos** | Gestión de bicicletas personales y de terceros con cálculo de intereses, visible también por bicicleta en cada card. |
| **Configuración** | Centralized management for banks, products, categories, exchange rates, and Apple Pay API keys. |
| **Banca** | Integración de movimientos, deudas, presupuesto, clasificación, sincronización de fuentes, importación masiva de estados de cuenta PDF y POC Apple Pay vía Atajo de iPhone. |
| **Pendientes** | Registro transversal de Aseo, contribuciones, pagos, documentos y sincronizaciones; incluye recordatorios, calendario, Telegram, tareas manuales y asignaciones de pagos por propiedad. |
| **Altcoin Scanner** | Scanner histórico de 45 proyectos más cola de descubrimiento fuera del top por capitalización, dossier auditable de web/redes/tokenomics, score fundamental y grafo verificable de fundadores, operadores, inversores, sponsors e instituciones. |
| **Trading Research Center** | Portal de investigación multi-activo con screener, temáticas, QuantPro, valor justo, consenso, divergencia, estrategias, calendario económico, ficha completa y gráficas. |

---

## 🛠 Technical Setup

### Consolidado, movimientos y presupuesto

En el Dashboard cada tarjeta tiene un asa para arrastrarla y flechas para moverla sin
arrastrar. El orden se guarda en la cuenta del usuario y vuelve a aplicarse al entrar.

En Movimientos, «Cargo» indica dinero que sale y «Abono» dinero que entra; el monto
se muestra positivo para ambos. «Facturado» corresponde a tarjetas, mientras los
movimientos de cuenta corriente se muestran como «Registrado en cuenta». El filtro
«Tarjeta pendiente de cartola» reúne compras `NO_FACTURADO` y `PROVISIONAL` de
Apple Pay; Apple Pay es el origen de la compra, no un segundo movimiento. Se puede
filtrar haciendo clic en un segmento o en el nombre de una categoría de la gráfica.

«Transferencias entre mis cuentas» está disponible en las categorías de ingreso y
gasto al abrir Movimientos, incluso sin volver a sembrar categorías. Al elegirla,
el cargo o abono se marca como transferencia y queda excluido de los ingresos y
gastos reales. Sirve, por ejemplo, para enviar dinero a BCI y devolver el remanente
a la cuenta principal; los pagos de servicios conservan sus categorías de gasto.
La categoría se guarda con el botón ✓. «Más opciones» agrupa bicicletas, cuentas
por cobrar y pagos de tarjeta, y comienza cerrado para simplificar cada fila.
Los traspasos propios se eligen únicamente en el selector de categorías. Estas
decisiones no enseñan una regla por glosa; las sugerencias de IA requieren revisión.

La categoría «Bicicletas» se agrega a la semilla de categorías y a los usuarios
existentes mediante migración. Al elegirla manualmente o vincular un movimiento a
una bicicleta, se marca como transferencia para que no aumente gastos o ingresos;
una sugerencia automática de esta categoría queda pendiente de revisión. El vínculo
con una bicicleta concreta actualiza su avance. En Préstamos, «Monto
vigente desde» registra los cambios de principal. «Historial de montos y correcciones»
permite agregar saldos anteriores con su fecha efectiva; Movimientos muestra el monto
y los cargos/abonos conocidos hasta el día elegido en «Hasta». Los saldos anteriores
al primer registro quedan como desconocidos hasta ingresar una corrección histórica.

Cada bicicleta de terceros tiene un vínculo por ID con su activo patrimonial. El capital
inicial descuenta devoluciones CLP confirmadas hasta la fecha consultada; editar, desvincular
o borrar un abono recalcula el saldo original y sus equivalentes. El saldo no baja de cero.
Los nuevos ajustes e históricos registran un **saldo confirmado al cierre de su fecha**:
incluyen los abonos anteriores, incluso los que se importen más tarde, y sólo descuentan
nuevos abonos de fechas posteriores. Los registros históricos anteriores conservan su
interpretación de capital. Las métricas de rentabilidad de la bicicleta siguen separadas.
Los nuevos desembolsos que cambien el capital deben confirmarse como un ajuste de saldo.
Cambiar nombre conserva la relación; eliminar una bicicleta conserva su activo histórico
inactivo. La migración enlaza activos anteriores sólo cuando usuario, nombre y marca de
autogeneración identifican un único préstamo y activo; los casos ambiguos requieren revisión.

Configuración → Telegram y alertas presenta los últimos meses completos de gasto
por categoría, incluyendo meses sin movimientos en el promedio. Los límites se
configuran en CLP; al alcanzarlos se envía un aviso si Telegram y los avisos por
categoría están activos. Banca → Configuración muestra el dinero disponible para
ocio y el monto CLP de aviso; el porcentaje se edita en escala de 1 a 100.

`/dashboard/gastos/` muestra caja confirmada; las compras de tarjeta se contabilizan una sola vez
mediante el estado facturado al vencimiento. Las transferencias internas entre cuentas se
excluyen; los reintegros vinculados a bicicletas y cuentas por cobrar conservan su entrada
de caja. `/dashboard/gastos/movimientos/` muestra por defecto cargos y abonos
confirmados, para poder corregir también ingresos previamente clasificados. El
filtro «Cargos y abonos» permite elegir un solo sentido; la gráfica sigue mostrando
gastos y no convierte los abonos en consumo. Reclasificar confirma la revisión y,
si no hay una bicicleta vinculada, restaura la naturaleza correspondiente a la
categoría. El selector de cada fila ofrece categorías del sentido del movimiento.
También permite pasar a `NO_FACTURADO` o `PROVISIONAL` para
revisar compras antes de recibir la cartola. Los pagos de Neat se ven como detalle de su
cargo de tarjeta, se pueden reclasificar individualmente y no se convierten en una segunda salida.

Las compras provisionales y no facturadas de tarjeta registradas en el mes se proyectan
orientativamente al mes siguiente. La proyección suma el facturado ya conocido para ese
vencimiento y las compras aún sin cartola; cuando una compra se factura sale de la segunda
parte. La fecha y el monto definitivos llegan con la cartola.
Los cargos no facturados de meses anteriores se muestran aparte para revisión y no se
suponen automáticamente pagaderos el mes siguiente.
El límite mensual CLP se configura en Banca → Configuración → Presupuesto e IA; cero lo
desactiva. Si el uso estimado supera el umbral configurado y los avisos de Ocio están
activos, se envía un aviso de Telegram. Las compras del Atajo de iPhone se intentan
clasificar por reglas e IA al registrarse; las que no se reconocen quedan pendientes y
pueden clasificarse desde la subpágina sin modificar decisiones manuales.

El presupuesto de Ocio utiliza el detalle de consumo de Gastos, incluyendo manuales,
cuotas, devoluciones y conciliaciones. El ingreso medio utiliza caja operacional y divide
por todos los meses de la ventana configurada, también los meses sin ingresos; los reintegros
de activos y las devoluciones de tarjeta no cuentan como ingreso económico. Desactivar
**Contabilizar** en un producto lo excluye de los gastos, estados, cobertura, presupuesto,
cupos y deudas patrimoniales. Se conserva toda su información para consulta y reactivación.
En el Dashboard, Fijos, Suscripciones y Otros distribuyen cada aporte una sola vez.

### Requirements
- Python 3.11+
- Django 5.1+
- SQLite (desarrollo y despliegue actual)

### Installation
1. **Clone the repository**
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Run migrations:**
   ```bash
   python manage.py migrate
   ```
4. **Load sample data:**
   ```bash
   python manage.py populate_fixtures
   ```
5. **Create superuser (Optional):**
   ```bash
   python manage.py createsuperuser
   ```

### Docker dentro de WSL2 (desarrollo local)

El proyecto incluye un Compose para ejecutar Django, Redis y el worker de Celery
directamente dentro de Ubuntu en WSL2. No requiere Docker Desktop. Desde Ubuntu WSL,
ubicado en la carpeta del proyecto:

```bash
docker compose up --build -d
docker compose logs -f web
```

La aplicación queda disponible en <http://localhost:8000/>. El servicio `web` aplica
las migraciones al iniciar, monta el código fuente y conserva la base SQLite del
proyecto; el entorno de trading queda en modo `DEMO` para evitar llamadas externas
durante las pruebas locales. Para ejecutar la suite:

La configuración actual usa SQLite también en el despliegue documentado. PostgreSQL
no está configurado por `settings.py`; no cambies el motor de base de datos sin
preparar y validar por separado una migración de datos.

```bash
docker compose run --rm web python manage.py test
```

Para detener los servicios:

```bash
docker compose down
```

---

## 📁 Project Structure

- `core/`: User profiles, periods, and audit trails.
- `gastos/`: Resumen único de caja/consumo, taxonomía temática, movimientos manuales y gastos programados.
- `patrimonio/`: Asset/liability tracking and net worth snapshots.
- `patrimonio/services/cuentas_por_cobrar.py`: Monthly renewal matching and WhatsApp collection text.
- `departamentos/`: Rental properties, mortgages, and ROI calculations.
- `pendientes/`: Obligaciones recurrentes, ocurrencias, asignaciones de pagos, recordatorios y tareas de actualización.
- `inversiones/`: Investment portfolio and historical tracking.
- `prestamos/`: Módulo de "Bicicletas" (préstamos personales y de terceros).
- `configuracion/`: Global settings, banks, products, and exchange rates.
- `banca/`: Fuente transaccional, reconciliación, presupuesto, analítica y clasificación IA.
- `altcoins/`: Scanner de proyectos cripto, snapshots de mercado, scoring, evidencias y relaciones institucionales.
- `trading/`: Research Center multi-activo, contratos de proveedores, scoring QuantPro, estrategias, calendario, IA y APIs de gráficas.
- `bank_sync/`: Contratos y conectores para bancos, exchanges, wallets e inversiones.
- `templates/`: Modern HTML layout using Tailwind CSS, HTMX, and Alpine.js.

---

## 📚 Extended Documentation

For more specialized information, refer to:

- [REST API Reference](API_DOCUMENTATION.md) - Endpoints and authentication.
- [Frontend Architecture](FRONTEND_DOCUMENTATION.md) - UI components and patterns.
- [Implementation Status](IMPLEMENTATION_STATUS.md) - Roadmap and technical decisions.
- [POC Apple Pay](docs/APPLE_PAY_POC.md) - API key, payload y configuración del Atajo de iPhone.
- [AI Agent Guide](AGENTS.md) - High-density context for AI assistants.

---

## 🔧 Common Commands

```bash
python manage.py runserver        # Start development server
python manage.py test             # Run automated tests
python manage.py shell            # Django interactive shell
python manage.py makemigrations   # Generate database migrations
python manage.py migrate          # Apply database migrations
python manage.py populate_fixtures # Load demo data
python load_real_data.py          # Load real historical data (from images)
python manage.py sync_trading_data --mode full --symbols MCD  # Yahoo/Twelve Data + SEC EDGAR
python manage.py link_altcoins_to_trading      # Migrar proyectos cripto existentes a fichas trading
```

### Pendientes y obligaciones

El centro está disponible en `/dashboard/pendientes/` y en el contador del encabezado.
Las reglas de Aseo y contribuciones se configuran por propiedad y no se activan para
propiedades existentes hasta indicar explícitamente la primera fecha. Las contribuciones
parten con los meses referenciales abril, junio, septiembre y noviembre; cada propiedad
puede cambiar fechas, periodicidad, monto, comuna, rol SII y enlace de pago. El Aseo no
usa un calendario nacional fijo, por lo que la periodicidad municipal queda editable.

Un pago puede vincularse a un movimiento bancario existente o crear un movimiento manual.
Las asignaciones permiten repartir una sola transacción agrupada entre varias propiedades
sin duplicar el gasto. Si el Aseo está incluido en contribuciones, se marca esa regla para
excluirla de la proyección duplicada.

Las reglas automáticas se generan de forma idempotente para productos TDC/CUENTA, fuentes
Binance y operaciones de mercado. Las tareas de documentos se consideran atrasadas luego
de 30 días sin evidencia y pueden resolverse como “Sin cartola” o “Sin movimientos”.

Las fechas iniciales de contribuciones son referenciales y se basan en ChileAtiende/TGR y
SII: [ChileAtiende](https://www.chileatiende.gob.cl/fichas/12176/1/pdf),
[SII](https://www.sii.cl/preguntas_frecuentes/aval_contrib_bbrr/001_165_0310.htm).
El Aseo depende de la ordenanza y portal municipal: [LeyChile](https://www.bcn.cl/leychile/navegar?i=30755),
[Municipalidad de Santiago](https://tramites.munistgo.cl/pagoaseo) y la explicación del
[SII sobre impuesto territorial](https://www.sii.cl/destacados/impuesto_territorial/).

Los documentos municipales no se importan automáticamente. La información de Aseo y
contribuciones se registra manualmente en cada propiedad desde **Pendientes → Configurar
obligación**, y el pago se completa en la ocurrencia correspondiente, vinculando un
movimiento existente o creando uno manual sin duplicar el gasto. Los PDF históricos ya
guardados se conservan únicamente como auditoría.

### Centro de Investigación de Mercado

El portal está disponible en `/trading/`. Incluye `/trading/research/` (screener),
`/trading/strategies/`, `/trading/calendar/`, watchlist, descartados y fichas en
`/trading/instrument/<id>/`. La ficha integral muestra precio, retorno y drawdown,
perfil del negocio, capitalización, dividend yield, valoración, consenso y estados
financieros. También incluye gráficas de precio, ingresos anuales, dividend yield y
P/E calculados al cierre anual, y EPS reportado/estimaciones disponibles. Cada cifra
conserva fuente y fecha.

No requiere API keys para comenzar: usa el feed público de Yahoo Finance para precios e
históricos y la API oficial SEC EDGAR para fundamentales de compañías estadounidenses.
Si existe `TRADING_TWELVE_DATA_API_KEY`, Twelve Data reemplaza a Yahoo como fuente de
mercado; `TRADING_FMP_API_KEY` agrega consenso, objetivos y calendario cuando el plan del
proveedor lo permite. Los faltantes quedan vacíos y la interfaz nunca presenta datos
DEMO como información vigente.

Una búsqueda sin coincidencia local permite guardar el símbolo y sus datos actuales.
Los administradores tienen los botones **Actualizar información ahora** y **Vincular
proyectos** en la misma página. El asistente de investigación entiende símbolos como
`MCD`, empresas, temáticas y filtros de QuantPro, P/E o dividendos; responde con precio,
fecha, fuente y motivos verificables.

El módulo es deliberadamente solo de investigación: no conecta brokers, no envía
órdenes, no automatiza trading y no genera señales de compra/venta. Watchlist y
descartados son preferencias aisladas por usuario.

### Altcoin Scanner

El scanner se abre en `/altcoins/` y conserva separados el `fundamental_score` y la
`institutional_score`. La segunda mide documentación y huella institucional, no
rentabilidad ni seguridad. Un partner comercial, un market maker o un listado en un
exchange no se presentan como inversión.

Los administradores disponen de un **Centro de operaciones** en esa misma página. Desde
sus botones pueden inicializar el universo histórico, actualizar precios, validar metadata
de CoinMarketCap, guardar snapshots de scores y abrir el flujo de descubrimiento. La
búsqueda de candidatos acepta CoinGecko, el catálogo público de Robinhood y Brave Search;
los candidatos no entran al ranking hasta que se investiga y se promueve explícitamente.
Los usuarios normales no ven ni pueden ejecutar estas acciones globales.

Cada candidato puede pasar por tres pasos visibles: **Descubrir**, **Investigar** y
**Promover**. Investigar crea un dossier versionado con identidad, mercado, tokenomics,
enlaces oficiales, redes públicas y evidencias HTTPS limitadas y trazables. Luego el botón
**Evaluar fundamentos** entrega ese dossier al modelo pequeño en JSON estricto; las citas
deben pertenecer al dossier y los datos ausentes quedan como `UNKNOWN`. Supply, FDV/market
cap y distancia al ATH se calculan de forma determinista antes de la IA. El score fundamental
y la huella institucional continúan separados.

Las relaciones de sponsors, fundadores, empresas, fondos, bancos y organismos públicos
se guardan con tipo, fechas, estado y evidencia. Las fuentes se clasifican como primarias,
corroboradas, secundarias o no verificadas; solo las dos primeras pueden publicarse como
relaciones verificadas. Las afirmaciones políticas requieren evidencia primaria y revisión
humana. La IA solo resume evidencias existentes y nunca aprueba relaciones.

CoinGecko aporta el mercado canónico y la búsqueda de candidatos; CoinMarketCap valida
metadata, Tokenomist enriquece supply, rondas y unlocks cuando tiene key, y Brave Search
habilita búsqueda web citable. Sus API keys se administran exclusivamente desde
**Configuración → Integraciones de mercado**, donde se guardan cifradas y solo son
visibles para staff. El Altcoin Scanner no lee estos secretos desde `.env`. CoinGecko
puede operar sin key mientras su API pública lo permita; búsqueda web queda deshabilitada
hasta configurar Brave. Robinhood se usa inicialmente como catálogo público de disponibilidad;
su API autenticada es opcional, está limitada a clientes elegibles de EE. UU. y requiere
credenciales creadas en la cuenta de Robinhood, por lo que no se simula con scraping ni se
presenta como disponibilidad universal.

## Catálogo de entidades financieras

El catálogo de **Configuración → Entidades Financieras** es compartido. Solo los
usuarios staff pueden crear o editar entidades. La eliminación, tanto desde
Configuración como desde el admin, se bloquea si hay productos, credenciales,
preferencias u otras relaciones de cualquier usuario. Para retirar una entidad
en uso, desactívala desde el admin conservando sus datos. Los productos continúan
siendo privados y administrables por su propietario; las notas de la entidad son
información pública del catálogo.

## Saldos originales y conversiones

En **Activos**, los saldos cargados a mano muestran «Manual» y «Editar saldo». Las
posiciones vinculadas a una integración muestran su fuente y el botón **Sincronizar**,
que actualiza únicamente la cuenta asociada y vuelve a Activos con el resultado.
Mientras se ejecuta, el botón muestra «Sincronizando…». Si falta la conexión o está
inactiva, se ofrece **Configurar fuente**. Sus saldos no se editan ni eliminan desde
el formulario manual. Los registros históricos de Binance sólo se reconocen como
integrados cuando un historial y un log exitoso del mismo usuario corroboran su origen;
el nombre del activo por sí solo no basta. Si la fuente no informa una posición, se
conserva su saldo anterior y se muestra un aviso; el cierre de posiciones ausentes
requiere todavía distinguir carteras completas y parciales.

La ingesta de posiciones conserva la precisión de cantidades y precios recibidos;
el valor monetario original se redondea a dos decimales al guardar, igual que sus
equivalentes. Esto permite importar saldos de cripto calculados con muchas cifras
decimales. Si alguna posición del lote no se puede guardar, se revierten todos sus
cambios de activos e historial y la sincronización registra el error.

El admin de Activos y Pasivos edita el **monto y la moneda original**. Los equivalentes
CLP/USD se muestran como valores de referencia y se derivan al guardar un saldo manual.
Al ingresar saldos manuales, se pueden pegar valores con más decimales desde Excel;
el sistema los redondea a dos decimales al guardar.
Los saldos sincronizados desde banca, fuentes de inversiones o cuentas por cobrar se
actualizan desde su fuente. Los registros antiguos sin moneda original conservan sus
valores de compatibilidad hasta una revisión explícita; no se infiere una moneda al
editar sus notas. Las tasas de referencia de gastos y patrimonio se seleccionan de
mindicador.cl con fecha igual o anterior a la operación, nunca desde una fecha futura.

Un importe extranjero sin equivalente conocido se muestra como **CLP pendiente**,
conservando monto y moneda originales. Gastos y proyecciones señalan que sus subtotales
son parciales; la API devuelve `null` para ese equivalente y Excel deja la celda vacía.
El cero informado sigue siendo un valor válido. Una tasa general no sustituye una tasa
bancaria desconocida; volver a importar con la tasa bancaria completa actualiza la misma
transacción. Un snapshot bancario parcial conserva los saldos anteriores de las monedas
que no informa, mientras un cero explícito sí puede cerrar la deuda de esa moneda.

Las hipotecas manuales se valoran directamente desde **CreditoHipotecario.saldo_actual_uf**,
con la UF y dólar oficiales de referencia. Aparecen en Pasivos y Patrimonio sin persistir
una segunda deuda; un saldo bancario del producto, incluido un cero explícito, sustituye
esa valoración. Una propiedad inactiva sale del patrimonio y métricas vigentes, mientras
su crédito sigue siendo deuda hasta cerrarlo por separado. Departamentos permite desactivar,
reactivar y consultar propiedades inactivas conservando sus documentos y snapshots anteriores.
La fecha efectiva de venta/baja y el historial de estados de propiedad siguen pendientes.

Al editar un movimiento manual, una modificación de descripción, notas o categoría
conserva su período y conversión. Cambiar importe, moneda o fecha recalcula la conversión;
el período solo cambia si se envía expresamente. La fusión de categorías exige direcciones
compatibles y rechaza resúmenes del mismo mes con moneda o naturaleza distintas, sin
modificar parcialmente sus movimientos. Resúmenes de la misma moneda conservan la suma
de sus importes originales y equivalentes y una tasa agregada de referencia.

## 🧪 Pruebas de regresión

La suite usa el runner nativo de Django y una base de datos temporal. Cubre reglas
financieras, ingesta idempotente, reconciliación de bicicletas, presupuesto, analítica,
clasificación IA sin red, conectores, snapshots patrimoniales y aislamiento entre usuarios.

```bash
venv/bin/python manage.py test --verbosity 1
```

Los tests deben ser herméticos: las APIs bancarias, Telegram y proveedores de IA se
simulan con datos locales. Cada nueva funcionalidad o modificación de lógica financiera
debe incluir al menos un caso nominal y un caso límite o de aislamiento.

## Gastos Mensuales unificados

`/dashboard/gastos/` abre el mes calendario anterior en **Gastos por movimiento**. Las
compras se muestran en su fecha real y se visualizan en una gráfica de distribución por
categoría; por ejemplo, un estado que vence en septiembre conserva en agosto las compras
realizadas durante agosto. Los estados relacionados se muestran como referencia, pero su
facturado no se vuelve a sumar. Se excluyen pagos, transferencias, inversiones, préstamos
y cuentas por cobrar.

Una compra pagada por un tercero se vincula como **Cuenta por cobrar** desde
**Banca → Movimientos**. Sigue siendo salida de caja y aumenta el activo pendiente, pero
queda fuera del consumo y de su gráfica; el reintegro se registra como abono del activo y
entrada de caja en su fecha efectiva, no como ingreso económico. En cargos agrupados de
NeatPagos la vinculación se hace por destinatario, de
modo que sólo la parte recuperable se excluye y el resto conserva su categoría de gasto.
La taxonomía separa `Financiamiento / Hipoteca` para dividendos hipotecarios de
`Arriendo / Hipoteca`, y usa `Pagos por cuenta de terceros` como etiqueta visible de
los cargos vinculados a una cuenta por cobrar. Esta etiqueta no excluye importes por sí
sola: la relación con la cuenta por cobrar es la fuente contable de esa decisión.

La perspectiva secundaria **Pagos / flujo de caja** conserva el monto facturado del
estado vigente en su mes de vencimiento y muestra cualquier diferencia de conciliación.
Houm aporta una sola vez el depósito neto confirmado de propiedades vinculadas y Neat
desglosa cargos conciliados sin sumar otro egreso. La cobertura de fuentes sigue disponible
en la API, sin alertas persistentes en la pantalla de Gastos.

Para auditar temporalmente ese cálculo, el detalle muestra las columnas **Cálculo** y
**Aporte**. En una TDC, una devolución o abono reduce el egreso facturado y no se presenta
como ingreso; un pago o transferencia de la TDC queda visible, pero aporta cero porque no
es un gasto. Los abonos de una cuenta corriente sí conservan su tratamiento como ingresos
de caja. Los filtros acotan las líneas visibles, categorías y gráficos, pero los totales de
Ingresos, Egresos y Balance se calculan sobre todo el período seleccionado; sólo cambiar el
período o la perspectiva modifica ese cálculo base.

Las entradas manuales viven en `MovimientoManual`: `OPERACIONAL` afecta caja y, si es
egreso, consumo; `ACTIVO_RECUPERABLE` e `INVERSION` sólo caja;
`TRANSFERENCIA_INTERNA` no afecta ninguna perspectiva. Productos financieros son
cuentas aisladas por usuario y `Banco` continúa como catálogo global.

Una coincidencia entre registro manual y banco se presenta como **Posible duplicado**:
fecha, moneda e importe sirven para proponerla, pero sólo **Mismo pago** confirma el
vínculo. **Deshacer vínculo** vuelve a contabilizar el registro manual y conserva el
rechazo al reimportar. La confirmación se conserva mientras sus datos sigan siendo
compatibles. Confirmar toma el ciclo y las fechas de la fuente bancaria: en caja, una
compra de tarjeta se imputa al vencimiento del estado, aunque el registro manual
estuviera en el mes de compra. Las sugerencias sin confirmar conservan el registro
manual; una tarjeta sin facturar no lo oculta automáticamente.

La carga mensual distingue **Total del mes** e **Importe adicional**. Un total incluye
los movimientos ya registrados en su categoría, dirección, naturaleza y mes: si declara
$15.000 y hay $10.000 de detalle, aporta $5.000. Si el detalle supera el total declarado,
se conserva todo el detalle, sin ajustes negativos. Un importe adicional se suma completo.
El detalle y la exportación conservan tanto el importe declarado como su aporte al cálculo.
La API usa `modo_resumen=TOTAL` (predeterminado) o `ADICIONAL`.

Los certificados municipales históricos de pago pueden vincularse al cargo bancario
desde **Banca → Importar → seleccionar certificado → Vincular pago bancario**. Se exige
el mismo usuario y el importe total en CLP; un cargo respalda un solo certificado. El
comprobante conserva sus asignaciones por rol, que explican el cargo sin añadir otro
gasto. El consumo usa la fecha de la operación; una tarjeta en caja usa el vencimiento
de su estado. Un certificado sin enlace conserva su tratamiento como gasto documentado.

Después de actualizar, ejecuta `python manage.py migrate`. Las nuevas columnas preservan
los registros existentes. Las confirmaciones automáticas antiguas vuelven a revisión;
las confirmaciones explícitas del usuario se mantienen.

API autenticada principal:

```text
GET  /api/gastos/resumen/?desde=2026-08&hasta=2026-09&perspectiva=caja
GET  /api/gastos/detalle/?perspectiva=consumo&categoria=12
GET  /api/gastos/exportar.xlsx?<los mismos filtros>
CRUD /api/gastos/manuales/
POST /api/gastos/manuales/carga_mensual/
GET  /api/gastos/manuales/conciliaciones-pendientes/?estado=SUGERIDA
GET  /api/gastos/manuales/conciliaciones-pendientes/?estado=CONFIRMADA
POST /api/gastos/manuales/{id}/resolver-conciliacion/
CRUD /api/gastos/categorias/
POST /api/gastos/categorias/{id}/fusionar/
POST /api/gastos/facturados/{id}/pago-real/
```

Los importes JSON se serializan como cadenas decimales. `CategoriaIngreso` y
`RegistroMensual` fueron eliminados; `GastoProgramado` sigue siendo una previsión y no
se suma como gasto realizado.

## 🌐 Despliegue en cPanel

La aplicación puede ejecutarse con **Application Manager/Passenger** en
`https://finance.gustavoguillen.com/`. El archivo `passenger_wsgi.py` es el punto de
entrada y el código debe residir fuera de `public_html`; los archivos estáticos se
sirven mediante WhiteNoise con `collectstatic`.

Para producción se deben configurar en Passenger `DJANGO_SECRET_KEY`,
`DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
`STATIC_URL=/static/` y `MEDIA_URL=/media/`; `FORCE_SCRIPT_NAME` debe quedar vacío
al publicar en la raíz del subdominio. También se deben configurar los secretos de
Banca. El `.env` real, `.git`, `venv` y las credenciales de cPanel no se empaquetan
ni se exponen bajo `public_html`; el `.env` privado solo se mantiene junto al código
para que Passenger pueda iniciar la aplicación.

Si el servidor usa LiteSpeed, el proveedor debe habilitar la integración de
Python/Passenger a nivel del servidor; registrar la aplicación mediante la API de
cPanel no modifica esa configuración global.

### Despliegue automático con GitHub Actions

El workflow `.github/workflows/deploy-cpanel.yml` se ejecuta al hacer `push` a `main`
o manualmente desde **Actions → Test and deploy to cPanel → Run workflow**. Antes de
publicar instala las dependencias en el runner, ejecuta `manage.py check`, la suite de
tests y `collectstatic`. Después sube un archivo limpio mediante la API de cPanel,
lo extrae en la carpeta de la aplicación, ejecuta las migraciones mediante un cron
temporal y reinicia Passenger.

En GitHub, abre **Settings → Secrets and variables → Actions → New repository secret**
y crea estos secretos:

| Secreto | Valor |
| --- | --- |
| `CPANEL_HOST` | Host de cPanel sin `https://`, por ejemplo `server199.web-hosting.com` |
| `CPANEL_USERNAME` | Usuario de cPanel, por ejemplo `gustzyif` |
| `CPANEL_API_TOKEN` | API Token creado en cPanel (**no** la contraseña de acceso) |
| `CPANEL_APP_ROOT` | `/home/gustzyif/finance_app` |
| `CPANEL_PYTHON_BIN` | `/home/gustzyif/virtualenv/finance_app/3.13/bin/python` |

El token debe permitir File Manager y Cron; no se requiere habilitar SSH. El workflow
no empaqueta `.env`, la base de datos SQLite ni `media/`, por lo que conserva la
configuración y los datos existentes en producción.

## 📄 Importación de estados de cuenta (POC)

Desde **Banca → Importar estados** (`/banca/importar/`) se pueden subir varios PDFs
simultáneamente. La POC detecta el banco, el tipo de producto, su identificador, el período y
los montos del estado. En tarjetas extrae los cupos total/utilizado/disponible y el subcupo de
avances; en cartolas de cuenta corriente extrae cargos, abonos y saldo final. En ambos casos
crea o reutiliza el producto financiero, importa sin duplicar y conserva el PDF como auditoría.

Cada PDF se procesa en una transacción financiera completa. Si falla una etapa posterior,
se revierten movimientos, cambios de vigencia, cierre de provisionales y snapshots, y se
conserva el documento con estado **ERROR** y su diagnóstico. Volver a subirlo reintenta el
mismo registro. Un documento **PROCESADO** permanece idempotente por usuario y huella.
Las pantallas de estados de cuenta y de **Banca → Houm / Neat** incluyen una zona de carga por
arrastre: al pasar archivos sobre ella muestra una animación activa, acepta soltarlos directamente,
informa los nombres y el tamaño seleccionados, permite limpiar la selección y conserva el selector
tradicional como alternativa. También valida en el navegador el formato y el límite de 15 MB antes
de iniciar el procesamiento.
Las obligaciones municipales se registran manualmente desde **Pendientes → Configurar
obligación**. Allí se define la propiedad, el tipo (Aseo o contribuciones), la periodicidad,
la primera fecha y el monto estimado; cada vencimiento permite registrar el pago real sin
duplicar el gasto. Los PDF municipales ya no se envían al importador de Banca.
El cupo queda guardado como snapshot por usuario, banco, tarjeta y fecha: si el banco aumenta
el cupo, el nuevo valor pasa a ser el vigente sin borrar el historial anterior. Al terminar cada lote, los movimientos operacionales
pendientes pasan primero por las reglas aprendidas y luego por la IA configurada para el
usuario actual en **Banca → Configuración** (por ejemplo, `demo_user`). La pantalla informa
cuántos movimientos resolvió cada fuente y qué quedó pendiente.

El cupo vigente se muestra en **Dashboard**, **Banca** y **Banca → Deudas**, con el total,
utilizado, disponible y avance disponible por tarjeta. El estado se vincula al `Banco` y
`Producto` detectados; cuando existe una credencial bancaria del usuario, se prioriza ese
registro para evitar asociar la importación a otra entidad global con un nombre parecido.
La fecha `Pagar hasta` también se guarda en el snapshot y se convierte en un vencimiento
real dentro de **Calendario & Planificación**, asociado a la tarjeta y al monto facturado.
Cada nueva importación reemplaza el vencimiento vigente de ese período, manteniendo los
períodos anteriores para consultar el historial.

La ingesta conserva un identificador estable del banco cuando está disponible. Sin ese
identificador conserva la cantidad de operaciones idénticas dentro de cada lote: dos
compras iguales siguen siendo dos y reimportar el lote no las multiplica. Un lote parcial
no elimina compras anteriores. El PDF y el conector pueden complementar las mismas filas;
si una glosa cambia y ninguna fuente proporciona identidad estable, el caso requiere
revisión y no se fusiona sólo por coincidir el importe.

Al vincular productos se resuelven los identificadores anteriores hacia el producto
canónico. Se conservan documentos, enlaces contables y la mayor cantidad de compras
idénticas de las dos fuentes. Decisiones manuales o enlaces contables incompatibles
detienen la operación completa con un aviso para revisar; no se borran al azar. Se
mantiene un estado vigente por moneda y mes, y los pasivos sincronizados repetidos se
conservan como historial inactivo, sin sumar dos veces el saldo.

Los movimientos se pueden recategorizar desde la misma vista. El usuario puede editar el
nombre estable del comercio antes de guardar; esa relación comercio → categoría queda asociada a
su cuenta, tiene prioridad sobre la IA en futuras cargas y no se comparte con otros usuarios.

En **Banca → Movimientos** (`/banca/movimientos/`) se puede buscar por comercio, glosa,
banco, producto, categoría o huella, además de acotar por fechas y estado. La distribución
visual por categoría respeta búsqueda, fechas, producto y estado; al elegir una categoría
conserva las demás en la gráfica para cambiar de foco. Un clic en el segmento o en el
nombre filtra la tabla automáticamente para agilizar la reclasificación.
Las decisiones de IA también se guardan como memoria para evitar repetir llamadas.
La opción **Revisar uno por uno** abre el pendiente más antiguo y permite confirmarlo como
ingreso, gasto o transferencia; las categorías de ingreso y gasto se muestran según el signo
bancario y su glosa queda como regla privada del usuario. Las transferencias marcadas manualmente
se excluyen del consumo; la detección automática posterior requiere evidencia en la glosa, el
producto o la conciliación con NeatPagos.
Para un abono entrante que devuelve dinero de un tercero, la revisión ofrece **Pago de cuenta por
cobrar**. Al elegir la cuenta, se registra el abono, se reduce su saldo pendiente y el movimiento
queda fuera de ingresos económicos, aunque sigue apareciendo como recuperación en el flujo de caja.

Los cargos genéricos de NeatPagos se concilian con su detalle externo por usuario, monto,
tarjeta disponible y una ventana cercana de fecha. Banca muestra el destinatario real y su
categoría; Gastos sustituye el cargo genérico por ese desglose sin duplicar el total. El cruce
se vuelve a intentar tanto al importar Neat como al importar o reanalizar la tarjeta.
Los abonos de cuenta corriente también se cruzan con pagos Neat previos usando los tokens del
destinatario, el principal pagado (sin la comisión cargada a la TDC), una ventana de cinco días
hábiles y una tolerancia de monto. Cuando un subconjunto de pagos explica el abono, este y su
cargo Neat de origen quedan como transferencia de bicicleta y no como ingreso o gasto. Los
cargos Neat conciliados con una contraparte conocida también quedan como transferencia.
Agrega en **Banca → Configuración → Contrapartes conocidas** los destinatarios que usas con
NeatPagos, por ejemplo Jonathan, Fabiana y Stephany; el alias se compara con el nombre del pago.
Los traspasos desde una cuenta corriente que incluyen el nombre completo del titular se reconocen
como movimientos entre cuentas propias; abonos y traspasos salientes sin evidencia suficiente
quedan en revisión para resolverlos uno por uno.
El panel principal presenta consumo real y excluye transferencias y pagos de tarjeta. En Gastos
Mensuales, la perspectiva **Pagos / flujo de caja** sigue disponible para revisar las salidas de
caja; por eso ese total puede ser mayor que el gasto real.

No es necesario volver a subir un PDF para actualizar la clasificación: **Recalcular este
estado** o **Recalcular todos** reaplican reglas e IA y protegen siempre las decisiones
manuales. El proveedor se consulta en lotes pequeños; las glosas omitidas se reintentan y
las respuestas JSON inválidas se vuelven a pedir con lotes aún más pequeños. Si la IA
rechaza la solicitud, expira o sigue devolviendo una respuesta inválida, la pantalla
muestra el motivo y el recálculo se revierte completo:
las categorías anteriores no quedan borradas ni parcialmente actualizadas. La URL, el
modelo y sus políticas deben ser compatibles entre sí; en OpenCode algunos modelos exigen
una autorización de uso de datos independiente aunque la API key sea válida.
Al usar OpenCode Go, la app envía un identificador de sesión estable durante cada
clasificación y se identifica con su propio `User-Agent`, como requiere ese endpoint;
omite `reasoning_effort`, parámetro que OpenCode Go puede rechazar.

**Reanalizar archivo** vuelve a ejecutar el parser sobre el PDF guardado para
incorporar mejoras de extracción sin borrar movimientos ni correcciones del usuario. Si el
usuario vuelve a subir exactamente el mismo PDF, `usuario + hash del archivo` reutiliza la
importación existente y aplica automáticamente el lector actual: actualiza metadatos,
snapshots y movimientos recién detectados, pero nunca crea una segunda importación. Una
versión distinta crea auditoría nueva y reutiliza movimientos ya conocidos por su huella
bancaria.

La conciliación separa los pagos de tarjeta del gasto operacional: glosas bancarias como
`MONTO CANCELADO` se consideran transferencias y los movimientos ya vinculados a un
préstamo conservan esa naturaleza en recálculos posteriores. Así esos pagos no distorsionan
los totales por categoría ni vuelven a quedar pendientes.
Cuando una TDC se paga usando otra TDC o se mueve dinero entre cuentas propias,
**Banca → Movimientos** permite elegir `Transferencia propia / pago de tarjeta` en el selector
financiero. El movimiento queda en
`Financiamiento / Pago de TDC`, con naturaleza de transferencia y fuera del consumo;
no se crea una cuenta por cobrar ni un préstamo ficticio.

Actualmente se soportan los formatos de texto de **Itaú, Banco Falabella, Santander,
Scotiabank, BCI, Lider Bci y Tenpo**. En Lider Bci la detección usa señales textuales del formato porque el logotipo
del PDF puede venir como elemento gráfico; reconoce tarjeta, período, vencimiento, cupos,
cargos, abonos y el pago de la tarjeta sin importar subtotales ni líneas de monto cero.
Tenpo admite descripciones partidas entre líneas y reutiliza la tarjeta configurada. Los
estados internacionales Santander conservan movimientos, deuda y cupos en USD; se anexan
al mismo snapshot de la tarjeta nacional y no se inventa una conversión CLP cuando el banco
no informa tipo de cambio. La vista de importación y Calendario muestran ambas monedas por
separado.
Las cartolas históricas de cuenta corriente están soportadas para **Santander, Itaú, Banco
Falabella, Scotiabank y BCI Personas**. Cada número de cuenta genera un producto `Cuenta Corriente`
independiente y su saldo final queda versionado por período y usuario. En Scotiabank y BCI se
reconstruye el signo de cargo/abono usando el saldo diario aun cuando el PDF omite la celda vacía.
Las cuentas corrientes se incluyen también en la perspectiva de caja por su fecha de estado (sin
inventar vencimientos ni diferencias contra un monto facturado), por lo que los abonos operacionales
completan los ingresos del período.
No se envían credenciales ni se conecta a los bancos, y los PDFs escaneados sin capa de
texto quedan registrados como no procesables hasta incorporar OCR.

## 🏠 Integración Houm y NeatPagos

El panel **Banca → Houm / Neat** (`/banca/fuentes-externas/`) incorpora información de
administración inmobiliaria y plataformas de pago como una capa explicativa. Los
`MovimientoBancario` continúan siendo la fuente contable: un pago o liquidación externa se
concilia con el cargo o abono existente y nunca crea un segundo gasto o ingreso.

- **Houm:** importa el informe mensual XLSX/CSV/JSON, vincula cada propiedad con un
  `Departamento`, conserva renta, administración, IVA, corretaje, promociones, reparaciones,
  servicios, ajustes y depósito neto. El neto confirmado actualiza `Arriendo {código}`; una
  diferencia con un registro manual queda para revisión. El formato oficial con las columnas
  `ID Propiedad`, `Arriendo`, `Plan Houm`, `Ajustes`, `Depósito` y `Fecha de pago` se reconoce
  aunque el encabezado no esté en la primera fila; unidades como `107F` se enlazan con el código
  interno `F107`.
- **Departamentos:** `/dashboard/departamentos/` usa las liquidaciones vinculadas para mostrar el
  último período importado, arriendo informado, depósito recibido, costo de administración y
  déficit. El gráfico conserva hasta 12 meses y los cálculos de rendimiento usan el ingreso del
  período actual. Si el reporte mensual omite una unidad activa con renta contractual, se presenta
  como **sin ingreso informado / vacante**, su depósito queda en cero, la renta no recibida se
  incorpora al déficit y el dividendo hipotecario continúa como salida del mes.
- **NeatPagos:** importa historial tabular, correos o comprobantes PDF con texto; conserva ID, estado, destinatario, tarjeta,
  principal, comisión, total cargado y comprobante. Admite pagos divididos conciliados con un
  solo cargo bancario y transiciones de estado idempotentes. Si el reporte no trae ID de pago,
  se usa una huella que combina `dia_de_pago` (incluida la hora), monto, cuenta/servicio y,
  cuando existen, destinatario y destino; la hora sola no identifica un pago. La búsqueda de
  cuentas por cobrar señala filas coincidentes entre importaciones y conserva separadas las
  filas repetidas dentro de un mismo CSV. Los registros originales se conservan para revisión.
- **Operación:** siempre existe un botón manual por fuente y uno global. La automatización es
  opcional y configurable por usuario: Neat usa por defecto el día 1; Houm los días 2–10 y se
  detiene al capturar todas las propiedades. CAPTCHA, MFA, sesión vencida o tres errores
  consecutivos detienen la consulta y solicitan intervención.

Los endpoints privados no están codificados: solo se activa la consulta experimental cuando
el usuario dispone de una URL y token autorizados de solo lectura. La importación oficial
permanece disponible sin navegador ni API. Consulta
[docs/INTEGRACION_HOUM_NEAT.md](docs/INTEGRACION_HOUM_NEAT.md) para operación, comandos y el
contrato propuesto para solicitar una API oficial.

## 💸 Cuentas por cobrar

Desde **Activos → Cuentas por cobrar** se pueden crear cuentas con nombre y moneda,
agregar cargos y registrar abonos por línea o generales. La vista inicial muestra el saldo
vigente de cada cuenta y los totales separados por moneda. Al abrir una cuenta se ven su
detalle mensual, historial y herramientas de edición; **Crear cuenta** y **Gestionar**
comienzan colapsados. **Editar** y los errores de validación abren el panel correspondiente.
Las transacciones se encuentran por comercio, fecha o monto; al elegir una fuente bancaria
o **NeatPagos**, se usa su importe y fecha originales.

**Preparar propuesta** busca primero los movimientos **asignados a esa cuenta** desde
Banca y los registros manuales. La asociación identifica a quién cobrar; la categoría
temática puede seguir describiendo el gasto. Se muestran las partidas pendientes, los
movimientos anteriores, los ya contabilizados y los ignorados anteriormente. Las
asociaciones pendientes también se ven en Banca, antes de confirmar el cálculo.
Al asociar un abono bancario desde **Movimientos**, la pantalla conserva sus filtros y
muestra un enlace directo a la partida de revisión. La asociación repetida conserva la
decisión ya guardada. Al recorrer un mes, los pares con igual fecha e importe entre abono
manual y transferencia se destacan con un acceso a conciliarlos; se mantienen separados
hasta que confirmes la revisión.

La revisión ofrece cargar estados de tarjeta, cartolas y reportes o comprobantes
NeatPagos como **fuentes complementarias opcionales**. Se reutilizan los importadores
y archivos existentes. La cobertura se verifica por usuario, moneda, producto y mes
de cierre; sólo los productos de las fuentes relevantes orientan la carga. Una tarjeta
usada en meses anteriores no obliga a subir su cartola cada mes. Los documentos
ausentes no bloquean preparar con movimientos ya asignados; las advertencias consultan
la evidencia actual y no mantienen una lista de faltantes obsoleta del borrador.

El historial sugiere conceptos, contrapartes y fuentes recurrentes, y compara el último
importe con el nuevo importe respaldado. Un valor antiguo sin fuente del mes permanece
como referencia: no se copia automáticamente como un nuevo cargo. Se proponen para
revisión las transferencias de contrapartes ya vinculadas y las coincidencias exactas
con abonos manuales del cálculo anterior; ninguna coincidencia se aprueba por sí sola.

Cada mes muestra su propio **saldo anterior + cargos − abonos + ajustes = total
pendiente**, el desglose y el **Texto para cobrar** correspondiente. Guardar un cargo,
una edición o un abono recalcula inmediatamente la **vista mensual provisional**;
el siguiente mes toma ese saldo, incluso mientras reconstruyes datos pasados sin
confirmarlos. La reconstrucción parte de los registros de cada mes; las referencias
opcionales y los cálculos ya publicados se conservan en el historial. Los movimientos
futuros no modifican un mes anterior. El abono conserva
su fecha bancaria y un mes de aplicación independiente. **Mes anterior** y **Mes
siguiente** permiten recorrer la reconstrucción; consultar no crea ni confirma cálculos.

Para publicar el saldo en Activos se mantiene **Preparar propuesta**, **Guardar
decisiones y recalcular** y **Confirmar cálculo revisado**. Hasta preparar, el estado
**Pendiente de preparar** se refiere a la propuesta de confirmación; la vista mensual
y su texto ya están disponibles. El acceso desde esta vista permite revisar los registros
desde cero antes de la primera confirmación. Al preparar se incorporan los registros
manuales guardados; las fuentes bancarias, sugerencias automáticas y posibles duplicados
requieren revisión. Sólo confirmar cambia el patrimonio y guarda el texto confirmado.
Después de una importación hay un acceso a **Revisar cuentas por cobrar**.

Al editar una línea, el nuevo importe aparece como **Cambio pendiente** junto al
importe registrado y vuelve a cargarse al abrir **Editar**. El acceso **Revisar en
mes/año** lleva a la propuesta correspondiente. También se pueden editar los cargos
y abonos manuales pendientes, sin duplicarlos. Las ediciones, cargos y abonos de agosto
recalculan agosto y el arrastre a septiembre, aunque exista una referencia o un cálculo
confirmado posterior. Se distinguen el mes de reconstrucción y el de revisión patrimonial:
si ya hay un saldo confirmado posterior, la diferencia se confirma desde ese mes vigente.
Confirmarla conserva el mes original de los registros reconstruidos.
Varias confirmaciones del mismo mes conservan sus versiones y el texto mensual completo.
El historial mantiene las fotos anteriores; la vista provisional identifica los registros
que todavía no se han confirmado. Las migraciones conservan importes, relaciones y
valoración existentes; sólo asignan los meses de los abonos y recuperan como entradas
explícitas las propuestas manuales pendientes.

Los cálculos tienen estados **Referencia sin validar**, **Borrador** y **Confirmado**.
Cada confirmación conserva una versión inmutable con importes, desglose, decisiones,
fecha de confirmación y texto para cobrar. Una nueva revisión del mismo mes conserva
la versión anterior. El punto de partida es el último saldo confirmado y sólo se proponen
efectos todavía no contabilizados; antes de la primera confirmación se puede escoger una
referencia histórica opcional. Por defecto se conserva el saldo registrado, sin dar
prioridad a un ejemplo aportado. Antes de la primera confirmación también se puede
elegir **Sin saldo inicial** para revisar las partidas desde cero. Esa selección requiere
confirmar el resultado; nunca reinicia un saldo ya confirmado. La migración congela el
saldo vigente y preserva las relaciones y la valoración anteriores como registros sin validar.

Las referencias aportadas se conservan para consulta y selección; no obligan a reconstruir
su mes antes de preparar uno posterior. Al elegir una referencia se muestran sus importes
conservados, fecha original, mes aplicado y evidencia de Neat, transferencias y cartolas.
Los posibles duplicados y cargos asociados posteriormente se revisan por separado.
Una fuente Neat de otro período se señala con su fecha real; si hay una única
coincidencia del período aplicado, se sugiere corregir la vinculación usando ese importe
como una decisión pendiente. Las fuentes ambiguas requieren selección manual.
Para preparar esa reconstrucción por consola, sin confirmar cobros:

```bash
python manage.py reconstruir_cobro --user-id 123 --cuenta-id 456 --periodo 2026-09
python manage.py reconstruir_cobro --user-id 123 --cuenta-id 456 --periodo 2026-09 --apply
```

El comando simula por defecto; `--apply` conserva un borrador y decisiones pendientes.
Los saldos publicados, referencias y cálculos confirmados permanecen conservados.

Cada partida puede **aplicarse**, **vincularse como respaldo ya incluido**, **conciliarse
con un abono existente** o **excluirse**. La coincidencia de fecha, moneda e importe
entre un abono manual y una transferencia sugiere conciliación, pero nunca la aprueba.
**Ignorar · pasado o no corresponde** conserva la decisión y evita volver a proponer
automáticamente esa fuente. Una fuente pendiente no puede asignarse a dos cuentas.
Al confirmarla se conserva un solo pago vigente y se mantienen ambos registros cuando
ya existían. Si el saldo anterior ya descontaba ambos, la corrección aparece como ajuste;
si sólo incluía el manual, conciliar tiene efecto cero. Los reportes de caja cuentan una
sola recuperación y el consumo no la interpreta como ingreso económico.

Las transferencias antiguas requieren revisión: **respaldo** tiene efecto cero sobre la
deuda; aplicarlas explícitamente propone un ajuste en el mes abierto. Las correcciones y
retiros conservan el registro original y su fecha, sin reescribir meses cerrados. Se puede
corregir el mes de aplicación y registrar ajustes con motivo. La fecha de edición o
importación nunca determina por sí sola el descuento. Al asociar desde Banca se sugiere
el período del movimiento o de su facturación, respetando el último mes confirmado.
Si una asociación quedó en un mes posterior, se puede seleccionar explícitamente para
revisarla en el mes elegido; consultar la pantalla no la traslada. Las partidas sin aprobar
permanecen pendientes. El borrador muestra el exceso de abonos y no permite confirmar un saldo negativo.

El cargo bancario y su desglose Neat son representaciones del mismo pago. Un pago Neat
adelantado puede corresponder a septiembre aunque haya ocurrido a finales de agosto y
recién figure en la cartola de septiembre. Al llegar la cartola se propone **respaldo**
de las obligaciones Neat ya incluidas, con efecto cero sobre la deuda y sin reemplazar
el desglose original. Esto también cubre un cargo bancario que agrupa varios pagos Neat.
Se rechaza aplicarlo como otro cargo, incluso a otra cuenta por cobrar. La fecha original,
el período del cobro y el cierre de la cartola se muestran por separado. Un pago ya
asignado al final de un mes tampoco se reutiliza para el siguiente por caer en su ventana.
Distintos destinatarios pueden seguir siendo obligaciones separadas. La confirmación es
atómica e idempotente, impide reutilizar fuentes y rechaza propuestas cuyos datos hayan
cambiado; en ese caso hay que actualizar la propuesta y revisar las decisiones.

Si el cargo ya ocurrió pero todavía no aparece en un estado importado, la línea se
registra como **Provisional · falta estado** con el período, fecha y monto conocidos. No
se reutiliza el movimiento facturado del mes anterior. Cuando llega el nuevo PDF, se
propone editar esa misma línea para vincular la fuente real. La diferencia se aplica
únicamente al confirmar y nunca crea una segunda obligación.

Cada cuenta se refleja como un activo de tipo **Cuenta por cobrar**, por lo que el saldo
confirmado entra en Dashboard, Patrimonio y Activos sin duplicar el movimiento original.
Preparar reutiliza la búsqueda de cargos recurrentes de la misma tarjeta o contraparte
Neat y la ventana de hasta 10 días anteriores al mes. Las coincidencias inciertas requieren
selección o registro manual. CLP y USD permanecen separados; cuentas, fuentes, períodos,
decisiones y abonos están aislados por usuario.

Para incorporar un cálculo aportado como referencia histórica, sin restaurarlo ni confirmarlo:

```bash
python manage.py registrar_referencia_cobro --user-id 123 --cuenta-id 456 --archivo referencia.json
python manage.py registrar_referencia_cobro --user-id 123 --cuenta-id 456 --archivo referencia.json --apply
```

El JSON incluye `periodo` y `fecha` en formato ISO, `saldo_anterior`, `saldo_final`,
`detalle_ids`, `abono_ids` y `texto`. `cargos` permite separar un saldo arrastrado que
ya estaba registrado como línea. El comando valida cuenta, usuario y total, simula por
defecto y conserva las referencias repetidas sin duplicarlas. La primera revisión resuelve
las diferencias frente a los registros actuales; la referencia nunca cambia por una importación.

Un abono bancario vinculado a una cuenta USD conserva el importe original en USD,
aunque aún no exista conversión CLP. Para revisar abonos anteriores que pudieran haber
guardado el equivalente CLP como dólares, existe una reparación que simula por defecto:

```bash
python manage.py corregir_abonos_usd
python manage.py corregir_abonos_usd --user-id 123
python manage.py corregir_abonos_usd --user-id 123 --apply
```

La reparación solo cambia los casos cuyo abono coincide exactamente con el equivalente
CLP y supera el importe original USD de su movimiento entrante. Conserva los abonos
parciales y comprueba que cuenta y movimiento pertenezcan al mismo usuario. Un cálculo
histórico conserva su saldo y texto; si ya incluía el abono reparado, registra la diferencia
como ajuste en la revisión del mes abierto. Los demás importes requieren revisión individual.

## 📱 POC Apple Pay vía Atajo

El endpoint `POST /banca/api/apple-pay/ingest/` recibe un pago normalizado desde Apple
Shortcuts usando una API key Bearer. Persiste el movimiento, reutiliza las reglas aprendidas
y la clasificación IA de Banca, e intenta alertar por Telegram. Los reintentos son seguros
mediante `event_id`; las keys se crean y revocan desde **Configuración → Apple Pay y Atajos**
y el secreto completo solo se muestra una vez.

La compra de Apple Pay queda `PROVISIONAL` y participa temporalmente en presupuesto y
dashboard. Cuando llega un PDF con período explícito, el sistema confirma en la misma fila
los calces seguros por tarjeta, monto, moneda, fecha y comercio; lo transforma en `FACTURADO`
sin repetir la alerta. Los provisionales que no aparecen al cierre dejan de contabilizarse,
y los calces ambiguos quedan auditados sin elegir al azar. Sin período, el PDF no cierra
provisionales. A los 45 días —o al plazo personal elegido— puede emitirse una alerta única.

En **Configuración → Telegram y alertas** cada usuario guarda token y chat ID cifrados,
prueba la conexión y elige avisos por gasto/monto mínimo, resumen diario, importaciones PDF,
Ocio, límites mensuales por categoría, vencimientos y provisionales atrasados. Cada envío
queda registrado con una clave idempotente; un error de Telegram nunca revierte el gasto.

La plantilla importable v2 extrae el valor numérico del objeto de Wallet mediante
`Amount → Currency Amount` y muestra la respuesta real del endpoint en la notificación.

Consulta [docs/APPLE_PAY_POC.md](docs/APPLE_PAY_POC.md) para crear la llave, importar la
plantilla firmada del Atajo, construir el JSON y conocer los límites actuales de esta prueba.

---

*Built with Django 5.1 | Tailored for Personal Wealth Management*
