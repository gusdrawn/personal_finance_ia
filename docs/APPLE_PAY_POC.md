# POC Apple Pay → FinanzasPersonalesIA

Esta prueba de concepto permite que un Atajo de Apple Shortcuts envíe un pago a Django
apenas ocurre una transacción Apple Pay. El servidor crea un `MovimientoBancario`, aplica
las reglas aprendidas y, si hay una configuración de IA, clasifica la glosa con el mismo
servicio que usan las cargas bancarias. También intenta enviar el aviso Telegram personal
configurado por el usuario.

## 1. Crear una API key

La llave se genera desde el servidor y se guarda en la base de datos únicamente como hash:

Para la prueba de concepto también puedes administrarla desde **Configuración → Apple Pay y
Atajos** (`/dashboard/configuracion/`). Desde ahí puedes crear una key con un nombre sencillo,
ver el prefijo y último uso de tus keys, y revocarlas sin entrar al admin. El secreto completo
se muestra una sola vez después de crearla; cópialo inmediatamente al Atajo.

```bash
venv/bin/python manage.py migrate
venv/bin/python manage.py generate_apple_pay_key demo_user --name "iPhone personal"
```

El comando muestra el secreto una sola vez. Guárdalo en el iPhone como secreto del Atajo y
no lo pongas en la URL, en el nombre del Atajo ni en un repositorio. También existe el
endpoint autenticado `POST /banca/api/apple-pay/keys/` para generar y `POST
/banca/api/apple-pay/keys/<id>/revoke/` para revocar una llave desde una sesión Django.

## 2. Endpoint de ingesta

```text
POST /banca/api/apple-pay/ingest/
Authorization: Bearer fpa_apple_...
Content-Type: application/json
```

Payload mínimo:

```json
{
  "event_id": "uuid-generado-por-shortcuts",
  "occurred_at": "2026-08-24T14:32:00-04:00",
  "merchant": "Supermercado Central",
  "amount": "12990",
  "currency": "CLP",
  "notify": true
}
```

Campos opcionales:

- `card_last4`: últimos cuatro dígitos; intenta encontrar un producto existente y, si no,
  crea un producto técnico `Apple Pay · *1234`.
- `product_id`: producto financiero existente; tiene prioridad sobre `card_last4`.
- `amount_clp` o `exchange_rate`: necesarios para un pago en USD si se desea guardar su
  equivalente CLP.
- `notify`: `true` por defecto; en `false` no se intenta enviar Telegram.

El movimiento nace como `PROVISIONAL` y sigue contabilizando. Al importar un estado de cuenta
con período explícito, un calce seguro por tarjeta, monto/moneda, fecha y comercio actualiza
esa misma fila a `FACTURADO`; no vuelve a enviar el aviso. Lo que no aparece al cierre deja de
contabilizarse con auditoría, y un PDF sin período no cierra provisionales.

El `event_id` es obligatorio. Apple Shortcuts debe generar uno nuevo por transacción. Si el
Atajo reintenta el mismo evento, la API devuelve `200` con `duplicated: true` y no vuelve a
crear ni notificar el movimiento. Si reutiliza el ID con datos distintos, devuelve `409`.

Respuesta resumida:

```json
{
  "ok": true,
  "created": true,
  "duplicated": false,
  "movement": {
    "id": 42,
    "merchant": "Supermercado Central",
    "amount_clp": "12990.00",
    "category": "Supermercado",
    "classification_source": "REGLA",
    "status": "PROVISIONAL"
  },
  "classification": {
    "source": "REGLA",
    "category": "Supermercado",
    "counts": {"regla": 1, "ia": 0, "sin_resolver": 0}
  },
  "notification": {"telegram_sent": false}
}
```

## 3. Configurar el Atajo

Para evitar crear cada acción manualmente, el repositorio incluye la plantilla firmada
[FinanzasPersonalesIA - Registrar Apple Pay v2 - Importable.shortcut](shortcuts/FinanzasPersonalesIA%20-%20Registrar%20Apple%20Pay%20v2%20-%20Importable.shortcut).
Impórtala en **Shortcuts** en el Mac y, cuando se solicite, introduce la key creada en
Configuración. Si macOS no muestra la pregunta de importación, abre el atajo y reemplaza
`PASTE_API_KEY_HERE` en la acción `Text`.

La plantilla actual usa el formato de diccionarios HTTP de Shortcuts 7.0. La primera
versión de la POC serializaba `merchant` y `amount` con un tipo interno incompatible y
podía cerrar Shortcuts durante la importación; vuelve a descargar el archivo enlazado si
conservas una copia anterior.

La versión v2 extrae el valor numérico mediante `Amount → Currency Amount`. Wallet entrega
`Amount` como un objeto de moneda, no como un número directo. La notificación final muestra
la respuesta de Django, por lo que `ok: false` ya no se presenta como una transacción
registrada. Revoca la key anterior antes de reemplazar un atajo que haya quedado expuesto.

No compartas el archivo después de introducir la key. Con **Shortcuts en iCloud** activado,
el atajo normal debería aparecer también en el iPhone; la automatización personal que elige
la tarjeta debe crearse en el iPhone porque Apple la mantiene ligada al dispositivo.

1. Crea una automatización personal cuyo disparador sea una transacción de Apple Pay y
   ejecútala inmediatamente, según las opciones que muestre tu versión de iOS.
2. Genera un UUID nuevo para `event_id`.
3. Usa **Obtener contenido de URL** con método `POST`, la URL pública HTTPS de la API y los
   encabezados `Authorization: Bearer <API_KEY>` y `Content-Type: application/json`.
4. Construye el JSON con las variables que exponga el disparador: comercio, monto, moneda,
   fecha/hora y, si están disponibles, últimos cuatro dígitos o producto.
5. Lee `movement.category` y `notification.telegram_sent` para mostrar una confirmación
   breve en el iPhone. La clasificación puede ser `PENDIENTE` si aún no hay IA configurada.

Para una prueba local, el servidor debe estar publicado mediante HTTPS o una red privada
accesible desde el iPhone. No se debe exponer el servidor de desarrollo directamente a
Internet.

## Alertas

Configura token, chat ID, monto mínimo y demás preferencias en **Configuración → Telegram y
alertas**. Las credenciales se cifran por usuario. El sistema puede avisar una sola vez si el
movimiento sigue provisional después de 45 días (plazo configurable).

## Alcance y siguientes pasos

La POC procesa la petición inline para facilitar la prueba. Antes de usarla como integración
permanente conviene añadir rate limiting por llave, rotación con expiración, una cola para
clasificación/notificaciones, registro del timestamp completo, una bandeja de confirmación
en la aplicación y notificaciones push nativas. El contrato JSON queda separado de la
ingesta bancaria para poder cambiar Apple Pay por otra fuente de eventos después.
