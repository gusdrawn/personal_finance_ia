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
| **Configuración** | Centralized management for banks, products, categories, and exchange rates. |

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
- `templates/`: Modern HTML layout using Tailwind CSS, HTMX, and Alpine.js.

---

## 📚 Extended Documentation

For more specialized information, refer to:

- [REST API Reference](API_DOCUMENTATION.md) - Endpoints and authentication.
- [Frontend Architecture](FRONTEND_DOCUMENTATION.md) - UI components and patterns.
- [Implementation Status](IMPLEMENTATION_STATUS.md) - Roadmap and technical decisions.
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
```

---

*Built with Django 5.1 | Tailored for Personal Wealth Management*
