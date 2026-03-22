# API Documentation - FinanzasPersonalesIA

## Base URL
```
http://localhost:8000/api/
```

## Authentication
All API endpoints require authentication. Use the Django admin login credentials.

**Test Credentials:**
- Username: `demo_user`
- Password: `demo1234`

---

## Endpoints

### Gastos (Expenses & Income)

#### 1. Categories (`/gastos/categorias/`)

**List all categories:**
```
GET /api/gastos/categorias/
```

**Response:**
```json
[
  {
    "id": 1,
    "nombre": "Salario",
    "tipo": "INGRESO",
    "orden": 0,
    "activo": true,
    "banco_defecto": null
  },
  {
    "id": 2,
    "nombre": "Netflix",
    "tipo": "SUSCRIPCION",
    "orden": 1,
    "activo": true,
    "banco_defecto": null
  }
]
```

**Create a category:**
```
POST /api/gastos/categorias/
Content-Type: application/json

{
  "nombre": "Tarjeta BCI",
  "tipo": "TDC",
  "orden": 5,
  "activo": true
}
```

**Update a category:**
```
PATCH /api/gastos/categorias/{id}/
Content-Type: application/json

{
  "orden": 10
}
```

**Delete a category:**
```
DELETE /api/gastos/categorias/{id}/
```

**Query Parameters:**
- `search={nombre}` - Search by category name
- `ordering={field}` - Order by: `tipo`, `orden`, `nombre`

---

#### 2. Monthly Records (`/gastos/registros/`)

**List all records for current month:**
```
GET /api/gastos/registros/?year=2026&mes=3
```

**Get formatted monthly data with totals:**
```
GET /api/gastos/registros/by_month/?year=2026&mes=3
```

**Response:**
```json
{
  "year": "2026",
  "mes": "3",
  "registros": {
    "Ingreso": [
      {
        "id": 1,
        "categoria": "Salario",
        "monto": "5000000",
        "moneda": "CLP",
        "tipo": "INGRESO"
      }
    ],
    "Gasto Fijo": [
      {
        "id": 2,
        "categoria": "Móvil",
        "monto": "35000",
        "moneda": "CLP",
        "tipo": "GASTO_FIJO"
      }
    ]
  },
  "totales": {
    "Ingreso": {"clp": "5000000", "usd": "0"},
    "Gasto Fijo": {"clp": "350000", "usd": "0"}
  }
}
```

**Create a monthly record:**
```
POST /api/gastos/registros/
Content-Type: application/json

{
  "year": 2026,
  "mes": 3,
  "categoria": 1,
  "monto": "50000",
  "moneda": "CLP",
  "tipo": "GASTO_FIJO",
  "notas": "Pago móvil marzo"
}
```

**Update multiple records (bulk):**
```
POST /api/gastos/registros/bulk_update/
Content-Type: application/json

{
  "updates": [
    {"id": 1, "monto": "5100000", "notas": "Aumento salario"},
    {"id": 2, "monto": "36000"}
  ]
}
```

**Query Parameters:**
- `year={año}` - Filter by year
- `mes={mes}` - Filter by month (1-12)
- `tipo={tipo}` - Filter by type: `INGRESO`, `GASTO`, `SUSCRIPCION`, `SEGURO`, `TDC`, `COBRO_BANCO`
- `moneda={moneda}` - Filter by currency: `CLP`, `USD`, `UF`
- `search={nombre}` - Search by category name

---

#### 3. Annual Expenses (`/gastos/gastos-anuales/`)

**List annual expenses:**
```
GET /api/gastos/gastos-anuales/
```

**Response:**
```json
[
  {
    "id": 1,
    "nombre": "Póliza Seguros Vehículo",
    "monto": "800000",
    "mes_cobro": 3,
    "activo": true,
    "ahorro_mensual": "66666.67",
    "notas": ""
  }
]
```

**Get total monthly savings needed:**
```
GET /api/gastos/gastos-anuales/total_ahorro_mensual/
```

**Response:**
```json
{
  "total_ahorro_mensual_clp": "1116666.67"
}
```

**Create an annual expense:**
```
POST /api/gastos/gastos-anuales/
Content-Type: application/json

{
  "nombre": "Revisión Técnica Auto",
  "monto": "100000",
  "mes_cobro": 8,
  "activo": true
}
```

---

#### 4. Quarterly Expenses (`/gastos/gastos-trimestrales/`)

**List quarterly expenses:**
```
GET /api/gastos/gastos-trimestrales/
```

**Get total monthly savings needed:**
```
GET /api/gastos/gastos-trimestrales/total_ahorro_mensual/
```

**Create a quarterly expense:**
```
POST /api/gastos/gastos-trimestrales/
Content-Type: application/json

{
  "nombre": "Mantenimiento Edificio",
  "monto": "2000000",
  "trimestre": 1,
  "activo": true
}
```

---

## Error Responses

**401 Unauthorized:**
```json
{
  "detail": "Authentication credentials were not provided."
}
```

**400 Bad Request:**
```json
{
  "error": "year and mes parameters required"
}
```

**404 Not Found:**
```json
{
  "detail": "Not found."
}
```

---

## Usage Examples

### Get all expenses for March 2026
```bash
curl -u demo_user:demo1234 \
  "http://localhost:8000/api/gastos/registros/by_month/?year=2026&mes=3"
```

### Create a new expense category
```bash
curl -u demo_user:demo1234 \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"nombre": "Gym", "tipo": "GASTO_FIJO", "orden": 20, "activo": true}' \
  "http://localhost:8000/api/gastos/categorias/"
```

### Update expense amount
```bash
curl -u demo_user:demo1234 \
  -X PATCH \
  -H "Content-Type: application/json" \
  -d '{"monto": "45000"}' \
  "http://localhost:8000/api/gastos/registros/1/"
```

### Bulk update multiple records
```bash
curl -u demo_user:demo1234 \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "updates": [
      {"id": 1, "monto": "5100000"},
      {"id": 2, "monto": "36000"},
      {"id": 3, "monto": "50000"}
    ]
  }' \
  "http://localhost:8000/api/gastos/registros/bulk_update/"
```

---

## Frontend Integration

### Example: Get monthly summary with HTMX

```html
<div id="monthly-summary" 
     hx-get="/api/gastos/registros/by_month/?year=2026&mes=3"
     hx-trigger="load, change from #month-selector"
     hx-swap="innerHTML">
  Loading...
</div>
```

### Example: Edit cell with Alpine.js + HTMX

```html
<td x-data="{monto: '{{record.monto}}'}" 
    @click="editing = !editing">
  <span x-show="!editing">{{monto}}</span>
  <input x-show="editing" 
         x-model="monto"
         @blur="htmx.ajax('PATCH', '/api/gastos/registros/{{record.id}}/', {values: {monto: monto}})"
         @keydown.enter="htmx.ajax('PATCH', '/api/gastos/registros/{{record.id}}/', {values: {monto: monto}})">
</td>
```

---

## Pagination

Default page size: 20 items

```
GET /api/gastos/registros/?page=2
```

Response includes:
```json
{
  "count": 150,
  "next": "http://localhost:8000/api/gastos/registros/?page=3",
  "previous": "http://localhost:8000/api/gastos/registros/?page=1",
  "results": [...]
}
```

---

## Status Codes

- **200 OK** - Success
- **201 Created** - Resource created
- **204 No Content** - Success with no content (DELETE)
- **400 Bad Request** - Invalid request
- **401 Unauthorized** - Authentication required
- **403 Forbidden** - Permission denied
- **404 Not Found** - Resource not found
- **500 Internal Server Error** - Server error

---

## Rate Limiting

Currently unlimited. To add rate limiting:

```python
# In settings.py
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle'
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour'
    }
}
```

---

## Version History

### v1.0 (Current)
- Core models and database
- Admin interface
- Gastos API endpoints
- Sample data fixtures

### Planned (v1.1+)
- Patrimonio API
- Departamentos API  
- Inversiones API
- Frontend (React/Vue or Django Templates + HTMX)
- Charts and visualizations
- 2FA authentication
- Mobile app
