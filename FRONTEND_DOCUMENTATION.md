# Frontend Documentation - FinanzasPersonalesIA

## Overview

The frontend is built with Django templates using **Tailwind CSS** for styling, **Chart.js** for visualizations, **HTMX** for dynamic updates, and **Alpine.js** for interactivity.

## Architecture

```
templates/
├── base.html                # Main layout with sidebar navigation
├── dashboard.html          # Overview with key metrics
├── gastos_table.html       # Monthly expenses table
├── patrimonio.html         # Assets and liabilities
├── departamentos.html      # Rental properties
├── inversiones.html        # Investment portfolio
└── configuracion.html      # Settings and configuration

static/
├── css/                    # Custom CSS (if needed)
├── js/                     # Custom JavaScript
└── img/                    # Images and assets
```

## Key Pages

### 1. Dashboard (`/dashboard/`)

**Purpose:** Overview of financial situation at a glance

**Components:**
- Key metric cards (Patrimony, Assets, Liabilities, Liquidity %)
- Income vs Expenses bar chart (current month)
- Expense distribution pie chart
- Recent departments list
- Recent investments

**Data Sources:**
- `Activo` and `Pasivo` models
- `RegistroMensual` for current month data
- `Departamento` and `Inversion` models

**Features:**
- Real-time metric calculations
- Interactive charts with Chart.js
- Quick links to other sections

### 2. Gastos Mensuales (`/gastos/`)

**Purpose:** View and manage monthly expenses and income

**Components:**
- Month/Year selector
- Summary cards (Total Income, Total Expenses, Net)
- Interactive table with records grouped by category
- Annual expenses section with pro-rata calculations
- Quarterly expenses section

**Data Sources:**
- `RegistroMensual` model
- `GastoAnual` model
- `GastoTrimestral` model

**API Integration:**
```javascript
GET /api/gastos/registros/by_month/?year=2026&mes=3
PATCH /api/gastos/registros/{id}/
POST /api/gastos/registros/bulk_update/
```

**Features:**
- Inline editing via HTMX
- Quick calculations (totals by category)
- Month comparison
- Savings recommendations for annual/quarterly expenses

### 3. Patrimonio (`/patrimonio/`)

**Purpose:** Track assets and liabilities over time

**Components:**
- Summary metrics (Total Assets, Total Liabilities, Net Worth)
- Assets list with liquidity indicators
- Liabilities list grouped by type
- Historical snapshots table

**Data Sources:**
- `Activo` model
- `Pasivo` model
- `SnapshotPatrimonio` model

**Features:**
- Asset classification (Líquido/No Líquido)
- Multi-currency display (CLP/USD)
- Historical tracking
- Liquidity percentage calculation

### 4. Departamentos (`/departamentos/`)

**Purpose:** Manage rental properties and analyze ROI

**Components:**
- Department cards with key metrics
- Tenant information
- Mortgage details
- Capital gains display
- ROI analysis table

**Data Sources:**
- `Departamento` model
- `Arrendatario` model
- `CreditoHipotecario` model

**Features:**
- Capital gain tracking
- Amortization calculation
- Cap Rate Income display
- Mortgage details
- Tenant information management

### 5. Inversiones (`/inversiones/`)

**Purpose:** Track investment portfolio

**Components:**
- Portfolio summary (Total Invested, Active Investments, Diversification)
- Investment list with type and allocation
- Portfolio composition pie chart
- Historical evolution chart

**Data Sources:**
- `Inversion` model
- `HistorialInversion` model

**Features:**
- Portfolio diversification tracking
- Historical value charts
- Type-based categorization
- Multi-currency support

### 6. Configuración (`/configuracion/`)

**Purpose:** System settings and information

**Components:**
- Current exchange rates (UF, USD)
- Configured banks list
- Active categories overview
- User information
- System status

**Data Sources:**
- `TipoCambio` model
- `Banco` model
- `CategoriaIngreso` model
- Django User model

## Template Inheritance

All pages extend `base.html`:

```html
{% extends 'base.html' %}

{% block title %}Page Title{% endblock %}
{% block page_title %}Display Title{% endblock %}
{% block content %}
    <!-- Page content here -->
{% endblock %}
```

## JavaScript Functionality

### HTMX Integration

Used for dynamic data loading without page refresh:

```html
<!-- Load month data on select change -->
<select id="month-select" onchange="loadMonthData()">
    ...
</select>
```

### Chart.js

Interactive charts on multiple pages:

```javascript
const ctx = document.getElementById('chartId').getContext('2d');
new Chart(ctx, {
    type: 'bar',
    data: { ... },
    options: { ... }
});
```

### Alpine.js

Component interactivity:

```html
<div x-data="{editing: false}">
    <span x-show="!editing">{{ value }}</span>
    <input x-show="editing" x-model="value">
</div>
```

## Styling System

### Tailwind CSS

All styling uses Tailwind utility classes:

```html
<!-- Example card component -->
<div class="bg-white dark:bg-slate-800 rounded-lg shadow-md p-6">
    <p class="text-gray-600 dark:text-gray-400 text-sm">Label</p>
    <p class="text-3xl font-bold text-green-600">$12,345</p>
</div>
```

### Custom CSS Classes

Common component styles defined in `base.html`:

```css
.card { @apply bg-white rounded-lg shadow-md p-6; }
.btn-primary { @apply bg-green-600 hover:bg-green-700 text-white px-4 py-2; }
.input-field { @apply w-full px-3 py-2 border border-gray-300 rounded-lg; }
.table-responsive { @apply w-full border-collapse; }
```

## Color Scheme

```
Primary (Green):     #10b981
Secondary (Blue):    #3b82f6
Danger (Red):        #ef4444
Warning (Orange):    #f59e0b
Success (Green):     #10b981
Info (Purple):       #8b5cf6
Background:          #f3f4f6 (light) / #0f172a (dark)
```

## API Integration

### Authentication

All API calls require authentication. Use CSRF token for state-changing requests:

```javascript
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

// Usage in fetch
fetch('/api/gastos/registros/', {
    method: 'PATCH',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken')
    },
    body: JSON.stringify(data)
});
```

### Common API Calls

```javascript
// Get monthly data with totals
fetch('/api/gastos/registros/by_month/?year=2026&mes=3')
    .then(r => r.json())
    .then(data => {
        // data.registros - grouped by category type
        // data.totales - totals by category type
    });

// Update single record
fetch('/api/gastos/registros/1/', {
    method: 'PATCH',
    body: JSON.stringify({monto: 50000})
});

// Bulk update
fetch('/api/gastos/registros/bulk_update/', {
    method: 'POST',
    body: JSON.stringify({
        updates: [
            {id: 1, monto: 5100000},
            {id: 2, monto: 36000}
        ]
    })
});
```

## Responsive Design

All pages are mobile-responsive:

```html
<!-- Grid adjusts based on screen size -->
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
    ...
</div>
```

Breakpoints:
- `md`: 768px (tablets)
- `lg`: 1024px (desktops)
- `xl`: 1280px (large screens)

## Navigation

### Main Menu (in Sidebar)

- Dashboard
- Gastos Mensuales
- Patrimonio
- Departamentos
- Inversiones
- Configuración

### Top Navigation

- User display
- Quick access to admin panel

## Form Components

### Input Fields

```html
<input type="text" class="input-field" placeholder="Value">
<select class="input-field">
    <option>Option 1</option>
</select>
```

### Buttons

```html
<button class="btn-primary">Primary Action</button>
<button class="btn-secondary">Secondary Action</button>
```

## Accessibility Features

- Semantic HTML structure
- ARIA labels where appropriate
- Keyboard navigation support
- Color contrast compliant
- Mobile touch-friendly

## Performance Considerations

- Lazy loading of charts
- Pagination of large tables (20 items/page)
- CSS and JS minification (production)
- Image optimization
- Database query optimization with `select_related` and `prefetch_related`

## Dark Mode Support

All components include dark mode classes:

```html
<div class="bg-white dark:bg-slate-800 text-gray-900 dark:text-gray-100">
```

Toggle dark mode in browser dev tools:
```javascript
document.documentElement.classList.add('dark')
```

## Development Workflow

### Making Changes

1. Edit template in `templates/` folder
2. Update CSS in `static/css/` if needed
3. Add JavaScript in `static/js/` if needed
4. Refresh browser (usually automatic with Django dev server)

### Adding New Pages

1. Create new template file in `templates/`
2. Add view function in appropriate app's `views.py`
3. Add URL pattern in app's `urls.py`
4. Add navigation link in `base.html`

### Testing

```bash
# Start dev server
python manage.py runserver

# Visit pages
# http://localhost:8000/dashboard/
# http://localhost:8000/gastos/
# etc.
```

## Known Limitations

- Exports (Excel/PDF) not yet implemented
- Advanced filtering on some pages
- Email notifications pending
- 2FA setup not in frontend yet
- Mobile app pending

## Future Enhancements

- [ ] Real-time data refresh with WebSockets
- [ ] Advanced filtering and search
- [ ] Data export (Excel, PDF)
- [ ] Email notifications
- [ ] Mobile app
- [ ] Dark/Light theme toggle
- [ ] Advanced charting (quarterly comparisons, forecasts)
- [ ] Budget alerts
- [ ] Custom dashboards

---

**Last Updated:** March 16, 2026
