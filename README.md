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
The authenticated sidebar includes quick links to the financial institutions and
investment platforms used most often: Santander, Scotiabank, Bci, Banco Falabella,
Itaú, AFP Uno, AFC, CMF deudas, Fintual, DVA, SoyFocus, and BTG Pactual. Each link
opens the official site in a new tab.

---

## 📊 Core Modules

| Module | Description |
| :--- | :--- |
| **Dashboard** | Real-time overview of net worth, assets, liabilities, and liquidity. |
| **Gastos** | Monthly income/expense tracking with pro-rata savings for annual/quarterly costs. |
| **Patrimonio** | Asset and liability management with multi-currency support (CLP, USD, UF). |
| **Departamentos** | Rental property ROI analysis, mortgage amortization, and tenant tracking. |
| **Inversiones** | Portfolio management for stocks, crypto, and mutual funds. |
| **Préstamos** | Gestión de bicicletas personales y de terceros con cálculo de intereses. |
| **Configuración** | Centralized management for banks, products, categories, exchange rates, and Apple Pay API keys. |
| **Banca** | Integración de movimientos, deudas, presupuesto, clasificación, sincronización de fuentes, importación masiva de estados de cuenta PDF y POC Apple Pay vía Atajo de iPhone. |

---

## 🛠 Technical Setup

### Requirements
- Python 3.11+
- Django 5.1+
- SQLite (Development) / PostgreSQL (Production)

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

---

## 📁 Project Structure

- `core/`: User profiles, periods, and audit trails.
- `gastos/`: Monthly records, annual and quarterly expense logic.
- `patrimonio/`: Asset/liability tracking and net worth snapshots.
- `departamentos/`: Rental properties, mortgages, and ROI calculations.
- `inversiones/`: Investment portfolio and historical tracking.
- `prestamos/`: Módulo de "Bicicletas" (préstamos personales y de terceros).
- `configuracion/`: Global settings, banks, products, and exchange rates.
- `banca/`: Fuente transaccional, reconciliación, presupuesto, analítica y clasificación IA.
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
```

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
simultáneamente. La POC detecta el banco, la tarjeta, el período y los montos del estado,
crea o reutiliza el producto financiero, importa los movimientos sin duplicarlos y muestra
un resumen gráfico por categoría. Los movimientos se pueden recategorizar desde la misma
vista; el criterio manual queda como regla para futuras cargas.

Actualmente se soportan los formatos de texto de **Itaú, Banco Falabella y Santander**.
No se envían credenciales ni se conecta a los bancos, y los PDFs escaneados sin capa de
texto quedan registrados como no procesables hasta incorporar OCR.

## 📱 POC Apple Pay vía Atajo

El endpoint `POST /banca/api/apple-pay/ingest/` recibe un pago normalizado desde Apple
Shortcuts usando una API key Bearer. Persiste el movimiento, reutiliza las reglas aprendidas
y la clasificación IA de Banca, e intenta alertar por Telegram. Los reintentos son seguros
mediante `event_id`; las keys se crean y revocan desde **Configuración → Apple Pay y Atajos**
y el secreto completo solo se muestra una vez.

La plantilla importable v2 extrae el valor numérico del objeto de Wallet mediante
`Amount → Currency Amount` y muestra la respuesta real del endpoint en la notificación.

Consulta [docs/APPLE_PAY_POC.md](docs/APPLE_PAY_POC.md) para crear la llave, importar la
plantilla firmada del Atajo, construir el JSON y conocer los límites actuales de esta prueba.

---

*Built with Django 5.1 | Tailored for Personal Wealth Management*
