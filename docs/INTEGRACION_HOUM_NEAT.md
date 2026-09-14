# Integración configurable de Houm y NeatPagos

## Principio contable

Houm y NeatPagos enriquecen movimientos bancarios; no los reemplazan. Cada dato externo se
guarda en `EventoFinancieroExterno` y puede quedar vinculado a uno o varios
`MovimientoBancario` mediante `ConciliacionEventoExterno`. Por eso una reimportación, un
cambio de estado o un informe repetido no suma nuevamente el gasto o ingreso.

## Canales de adquisición

1. Importación oficial XLSX, CSV o JSON desde **Banca → Houm / Neat**. Para Neat también se
   aceptan correos EML/TXT/HTML y comprobantes PDF con texto. Es la vía estable y no necesita
   navegador.
2. Endpoint autorizado de solo lectura. Es experimental porque ninguno de los proveedores
   publica actualmente una API de usuario. La URL y el token no se presuponen ni se codifican.
3. Un futuro adaptador de descarga autenticada puede implementar los mismos contratos y
   priorizar archivos/JSON. CAPTCHA o MFA debe devolver una solicitud de reautenticación, no
   intentar eludir el desafío.

Los contratos son `PaymentPlatformConnector.get_payments(desde, cursor)` y
`RentalManagerConnector.get_properties()/get_settlements(periodo, cursor)`. La sesión, token
y contraseña se cifran con `BANCA_FERNET_KEY`. Ningún conector expone acciones de pago,
anulación o modificación.

## Programación

Cada credencial guarda activación, hora, días inicial/final, zona horaria, reintentos y
detención al completar. Celery Beat llama un evaluador horario; el servicio decide con esa
configuración si corresponde ejecutar. Un cron alternativo puede invocar el mismo comando:

```bash
venv/bin/python manage.py sync_fuente_externa todas --usuario USUARIO --automatico
```

Valores iniciales: Neat día 1 y Houm días 2–10. Una ejecución manual ignora la ventana, pero
respeta el lock, cursor e idempotencia. Houm deja de consultar cuando todas las propiedades
vinculadas tienen liquidación del período; si termina la ventana con pendientes, muestra una
alerta. Errores de autenticación, CAPTCHA/MFA o tres fallas consecutivas frenan la fuente.

## Importación y operación por consola

```bash
venv/bin/python manage.py importar_fuente_externa houm informe.xlsx --usuario USUARIO
venv/bin/python manage.py importar_fuente_externa neat historial.csv --usuario USUARIO
venv/bin/python manage.py sync_fuente_externa houm --usuario USUARIO --periodo 2026-08
venv/bin/python manage.py sync_fuente_externa neat --usuario USUARIO --probar
venv/bin/python manage.py sync_fuente_externa todas --usuario USUARIO --conciliar
```

Los originales se conservan en `contenido_privado` dentro de la base de datos y no se
publican por `MEDIA_URL`. La descarga exige sesión y propiedad del registro; además se guarda
nombre, MIME, hash y resultado de proceso. Si el volumen futuro crece, este campo puede
migrarse a un object storage privado conservando el mismo control de acceso.

## Conciliación

- Neat prioriza ID externo en la glosa, luego últimos cuatro dígitos/tarjeta, total cargado y
  una ventana de días bancarios. Principal y comisión explican un solo cargo. Varios pagos
  pueden asignarse a un cargo agregado. La conciliación se ejecuta al importar Neat y también
  al importar o reanalizar un estado de cuenta, por lo que el orden de carga no afecta el cruce.
  Cuando el destinatario identifica inequívocamente servicios básicos, telefonía, hipotecas o
  créditos, esa categoría detallada reemplaza la categoría genérica del cargo sin sobrescribir
  decisiones manuales. Banca muestra el destinatario y Gastos usa ese desglose sin volver a
  sumar el movimiento de la tarjeta.
- Houm concilia el depósito neto individual o agregado. El detalle conserva renta bruta,
  comisión, IVA, corretaje, promociones, reparaciones, servicios y ajustes.
- El reporte estándar `reporte-propiedades-M-AAAA.xlsx` puede traer el encabezado desplazado y
  usa `Plan Houm` como costo de administración y `Depósito` como ingreso neto. La asociación
  reconoce tanto `F107` como las variantes `107F` y `107 F` presentes en las direcciones.
- `Arriendo {código}` recibe el neto confirmado. Si existe un valor manual diferente, el
  evento cambia a `REQUIERE_REVISION` y no se sobrescribe.

## Analítica en Departamentos

`/dashboard/departamentos/` agrega las liquidaciones vinculadas por período y propiedad. El
último mes muestra renta informada, administración, depósito y déficit; el gráfico compara esos
valores durante los últimos 12 meses. El déficit es únicamente el faltante que permanece después
de descontar administración, corretaje, promociones, reparaciones y servicios, y de aplicar los
ajustes informados. Un depósito superior al neto esperado se conserva como excedente y no se
presenta como déficit. Todas las consultas exigen coincidencia de usuario tanto en el evento como
en el vínculo y el departamento.

Cuando el archivo contiene liquidaciones del período pero omite una unidad activa, la vista no
arrastra su promedio histórico. La unidad queda con ingreso cero y estado `SIN_INGRESO`; si conserva
una renta contractual activa, ese monto se suma al déficit del período. El crédito hipotecario se
calcula de manera independiente a la ocupación, de modo que su dividendo continúa reduciendo el
cash flow incluso durante una vacancia.

## Solicitud recomendada a los proveedores

Solicitar una API o entrega programada con alcance estrictamente read-only, identificadores
estables, paginación/cursor, estados y fechas, comprobante o informe firmado, webhooks
opcionales, rate limits documentados, revocación de tokens y sandbox. Para Houm se requieren
propiedades y liquidaciones con todas sus líneas; para Neat, pagos con principal, comisión,
total, tarjeta, destinatario y transición de estado.

## Seguridad y pruebas

La suite usa archivos sintéticos y conectores simulados; nunca accede a portales reales.
Incluye idempotencia, conflictos manuales, depósitos agregados, pagos divididos, ventanas,
detención por período completo, lock concurrente, CAPTCHA/MFA e aislamiento por usuario.
