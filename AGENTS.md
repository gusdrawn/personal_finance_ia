# 🤖 AI Agent Guide - FinanzasPersonalesIA

This guide is designed for AI coding assistants (like you!) to quickly grasp the project structure, domain models, and technical patterns.

## 🛠 Technical Overview

- **Framework:** Django 5.1 (Python 3.11+)
- **API Engine:** Django Rest Framework (DRF)
- **Frontend Stack:** Tailwind CSS, Chart.js, HTMX, Alpine.js
- **Database:** SQLite (dev) / PostgreSQL (prod-ready)
- **Local Timezone:** `America/Santiago`
- **Currency Support:** Multi-currency with historical exchange rates (CLP, USD, UF).

## 📊 Domain Models (22 Models)

The system is split into specialized apps:

### 🏠 App: `departamentos` (Rental Properties)
- `Departamento`: Property metadata (value in UF, purchase info).
- `Arrendatario`: Tenant information linked to a property.
- `CreditoHipotecario`: Mortgage tracking with French amortization logic.
- `Estacionamiento`: Ancillary property assets.

### 💰 App: `gastos` (Expenses & Income)
- `CategoriaIngreso`: User-defined expense/income categories (e.g., "Food", "Rent").
- `RegistroMensual`: Actual monthly amounts spent/received.
- `GastoAnual`: Annual expenses broken down into monthly savings pro-rata.
- `GastoTrimestral`: Quarterly expenses broken down into monthly savings.

### 📈 App: `patrimonio` (Assets & Liabilities)
- `Activo`: Cash, investments, properties (Liquid/Non-Liquid classification).
- `Pasivo`: Debt, credit cards, mortgages.
- `SnapshotPatrimonio`: Monthly total net worth snapshots for historical charting.
- `MiniSesion` & `LineaMiniSesion`: Utilities for calculating and sharing specific loan/financial scenarios.

### ⚙️ App: `configuracion` (Settings)
- `Banco`: Centralized bank directory.
- `Producto`: Financial products (Credit Cards, Accounts) per bank.
- `TipoCambio`: Historical USD/UF exchange rates.
- `ConfiguracionGeneral`: User-specific system settings.

### 🔑 App: `core` (Foundation)
- `UserProfile`: Extended user data.
- `Periodo`: Global context for "current month/year".
- `AuditoriaChange`: Built-in audit trail for all model changes.

## 🔌 Primary Integration Points

- **Admin Interface:** `/admin/` (Full CRUD for all 22 models).
- **REST API:** `/api/` (Documented in `API_DOCUMENTATION.md`).
- **Development Shell:** `python manage.py shell`.
- **Sample Data:** `python manage.py populate_fixtures`.

## 🧠 Business Logic Patterns

### 1. Currency Conversion
Most financial metrics are calculated using `TipoCambio` for the relevant date. The application prioritizes **UF** for long-term assets (properties) and **CLP** for daily expenses.

### 2. ROI Calculations
Located in `departamentos.models.Departamento.get_roi_metrics()`:
- **Cap Rate**: (Annual Rent / Current Value)
- **Cash Flow**: (Rent - Mortgage - Costs)
- **Capital Gains**: (Current Value - Purchase Value)

### 3. Pro-Rata Savings
Located in `gastos.models.GastoAnual.get_monthly_equivalent()`:
- Divides annual/quarterly costs by 12 or 3 to calculate needed monthly "savings" buffer.

## 📝 Coding Standards

- **Templates:** Base layout is `templates/base.html`.
- **Interactivity:** Use **HTMX** for partial page updates (in-line edits) and **Alpine.js** for client-side state (dropdowns, modals).
- **Charts:** Use `Chart.js` for all visualizations.
- **Precision:** Use `Decimal` for all financial fields (never float).
- **Separation:** Keep API logic in `viewsets.py` and UI logic in `views.py`.

---

*FinanzasPersonalesIA | Built with Precision for Financial Control*
