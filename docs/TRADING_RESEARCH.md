# Centro de Investigación de Mercado

## Alcance

El módulo `/trading/` sirve para investigar activos y comparar evidencia antes de
tomar una decisión. No conecta brokers, no envía órdenes, no ejecuta trading automático
y no convierte una ausencia de datos en una recomendación.

## Navegación

- `/trading/`: visión general, fuentes, actualización, activos destacados, eventos y
  asistente de investigación.
- `/trading/research/`: buscador y screener por símbolo, clase, sector y temática.
- `/trading/strategies/`: definiciones de ideas de investigación. Los rendimientos de
  la antigua demo no se presentan como resultados actuales.
- `/trading/calendar/`: eventos reales disponibles para los próximos 45 días.
- `/trading/instrument/<id>/`: reporte de empresa con precio, retorno, drawdown,
  capitalización, dividendos, P/E, márgenes, crecimiento, estados financieros y
  gráficas de precio, ingresos, yield anual, P/E anual y EPS.
- Watchlist y descartados: preferencias aisladas por usuario.

## Fuentes actuales

La configuración básica no necesita API keys:

- **Yahoo Finance**: búsqueda, precios e histórico diario mediante su feed público.
- **SEC EDGAR**: estados financieros XBRL oficiales de emisores estadounidenses.

Proveedores opcionales:

- `TRADING_TWELVE_DATA_API_KEY`: reemplaza Yahoo como fuente de mercado.
- `TRADING_FMP_API_KEY`: agrega perfil, consenso, objetivos y calendario según el plan.
- `TRADING_FRED_API_KEY`: deja disponible el adaptador macro.

La pantalla excluye siempre barras, métricas, estados y eventos marcados como `DEMO`.
Los campos sin fuente quedan vacíos. Cada reporte conserva `source`, fecha de mercado,
fecha del periodo financiero y fecha de presentación SEC.

## Operación visual

Un usuario staff puede usar **Actualizar información ahora** en el dashboard. El botón
ejecuta la misma operación que:

```bash
venv/bin/python manage.py sync_trading_data --mode full
```

Para diagnosticar un activo concreto:

```bash
venv/bin/python manage.py sync_trading_data --mode full --symbols MCD
```

El botón **Vincular proyectos** relaciona proyectos del Altcoin Scanner con sus fichas.
La operación web que sembraba la demo fue retirada.

## Búsqueda e importación

`GET /trading/api/search/?q=MCD` combina coincidencias locales con el catálogo público.
`POST /trading/api/import/` guarda el histórico y, si corresponde a una acción de EE.
UU., sincroniza también SEC EDGAR. Si una fuente falla se informa el error; no existe
un reemplazo sintético.

## Asistente de investigación

El asistente interpreta símbolos, nombres, sectores, temáticas y umbrales como
`QuantPro mayor a 70`, `P/E menor a 25` o `dividendo sobre 2`. Cada resultado incluye
precio, variación, P/E, yield, QuantPro, fecha, fuente y una explicación breve. Solo
consulta datos persistidos que no sean DEMO y deja registro por usuario en
`AIResearchRun`.

## Pruebas

```bash
venv/bin/python manage.py test trading
venv/bin/python manage.py test
```

La suite cubre proveedores herméticos, normalización Yahoo, resolución SEC, estados y
métricas oficiales, exclusión de datos demo, reporte MCD, asistente con fuente/fecha,
gráficas, operaciones staff y aislamiento por usuario.
