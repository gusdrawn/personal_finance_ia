# Puesta en marcha — Integración bancaria (`banca` + `bank_sync`)

Guía corta para dejar funcionando la sección **Banca**: dependencias, variables de entorno,
migraciones, semilla de categorías y comandos de sincronización. Para el diseño completo, ver
[ARQUITECTURA_INTEGRACION_BANCARIA.md](ARQUITECTURA_INTEGRACION_BANCARIA.md).

---

## 1. Dependencias

Con el entorno virtual activo:

```bash
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium     # navegador para el scraping de Santander
```

> El connector usa **Chromium** (`bank_sync/connectors/santander.py`). `cryptography` es
> obligatoria para guardar credenciales; `playwright` solo se necesita para la sync en vivo.
> Si falta alguna, el código degrada con un mensaje claro en vez de un 500.

---

## 2. Variables de entorno (`.env`)

Copia `.env.example` a `.env` y complétalo. Mínimo indispensable para credenciales:

```bash
# Llave Fernet para cifrar las credenciales bancarias (OBLIGATORIA para guardar credenciales)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

| Variable | Para qué | ¿Obligatoria? |
|----------|----------|---------------|
| `BANCA_FERNET_KEY` | Cifra RUT/clave del banco, API key de IA y credenciales personales de Telegram (Fernet) | Sí, para credenciales |
| `IA_API_KEY` / `OPENROUTER_API_KEY` | API key de IA (solo fallback; lo normal es guardarla cifrada en la UI) | Opcional |
| `CELERY_BROKER_URL` | Sync en background/programada | Opcional (sin broker, la sync corre inline) |
| `BANK_SYNC_SESSION_DIR` | Dónde se guarda la sesión del navegador | Opcional (`~/.bank_sync/sessions`) |
| `BANK_SYNC_DEBUG_DIR` | Dónde se vuelcan artefactos al fallar el login | Opcional (`~/.bank_sync/debug`) |

> El proyecto **carga el `.env` automáticamente** (puente en `settings.py`). Tras editar el
> `.env`, **reinicia el server** para que tome los cambios.
> Sin `BANCA_FERNET_KEY`, la pantalla de credenciales muestra un error explicativo (no se cae).

### Proveedor de IA (OpenRouter, OpenCode Zen, etc.)

La clasificación usa cualquier gateway **compatible con OpenAI**. Se configura por usuario en
**Banca → Configuración → Presupuesto e IA** (no en el `.env`):

- **Base URL**: la raíz del API del proveedor, ej. `https://openrouter.ai/api/v1` o la de
  OpenCode Zen. Sin `/chat/completions`.
- **API key**: se ingresa una vez y se guarda **cifrada** (mismo Fernet que las credenciales).
- **Modelo**: botón **«Refrescar modelos desde el proveedor»** consulta el endpoint `/models`
  y llena el desplegable; eliges el modelo a usar.

---

## 3. Base de datos y datos iniciales

```bash
python manage.py migrate                          # aplica banca.0001_initial entre otras
python manage.py seed_categorias_banca            # categorías por defecto (todos los usuarios)
# o para un usuario puntual:
python manage.py seed_categorias_banca --user <username>
```

El seed es **idempotente** (no duplica). Crea 17 categorías temáticas con su esencialidad
`FIJO`/`OCIO`, base del presupuesto y de la clasificación IA.

---

## 4. Configurar desde la app

1. Levanta el server: `python manage.py runserver`.
2. Menú lateral → **Banca** (o **Configuración → Credenciales y Banca**).
3. Pestaña **Configuración** → tarjeta **Fuentes de datos**. El selector *Fuente* agrupa
   bancos y exchanges/wallets; los campos se adaptan al tipo elegido:
   - **Bancos** (`santander`, …): RUT/usuario + clave. La institución se elige o se deja en
     blanco (se auto-crea con el nombre del banco). Todo se cifra (Fernet).
   - **Exchanges cripto** (`binance`, `buda`, `cryptomkt`): API key + secret de **solo
     lectura** (sin permisos de trading/retiro). Se cifran; la institución se auto-crea.
   - **Wallet Bitcoin** (`bitcoin_wallet`): solo una **dirección pública o xpub** (sin
     secreto); se consulta el saldo en un explorador público.
   - **Fintual** (`fintual`, inversión): API oficial con **email + contraseña** (los campos
     "API key"/"API secret" se muestran como Email/Contraseña). Cada meta se valoriza en CLP
     como `FONDO_MUTUO`.
   - **Parámetros**: ventana de ingresos, umbral de alerta Ocio, tolerancia de bicicleta, modelo IA.
   - **Contrapartes**: personas de bicicleta financiera (sus transferencias no cuentan como ingreso).
4. Pestaña **Categorías**: revisa/edita el set sembrado.

> Las fuentes de **activos** (cripto/inversión/AFP) no generan movimientos de gasto: sus
> posiciones alimentan **Patrimonio → Activos** (`patrimonio.Activo` + `HistorialActivo`),
> valorizadas en CLP/USD. Los exchanges chilenos (Buda, CryptoMKT) cotizan en CLP; Binance y
> la wallet BTC se valorizan en USD con el precio spot de Binance y se convierten con la UF/USD.

---

## 5. Sincronización

### Manual (CLI)

```bash
# Modos: no_facturado | estado_cuenta | deudas | full
python manage.py sync_banco --modo full
python manage.py sync_banco --banco santander --modo no_facturado
python manage.py sync_banco --modo estado_cuenta --no-headless   # ver el navegador (debug)
python manage.py sync_banco --modo deudas --no-notify            # sin Telegram
```

### Desde la app

Botón **Sincronizar ahora** en el Dashboard / Historial. Usa Celery si hay broker; si no,
corre inline. El resultado de cada corrida queda en **Banca → Sync** (`SyncLog`).

### Programada (Celery Beat)

El botón **Sincronizar ahora** corre inline (no necesita worker). Celery es para correr en
**background / programado**. Ya está cableado (`finanzas_project/celery.py`).

```bash
# 1) Redis debe estar corriendo (broker):
redis-cli ping            # -> PONG   (si no: brew services start redis)

# 2) Worker + Beat (programador) con un solo comando:
./scripts/celery.sh
# equivale a: celery -A finanzas_project worker -B --pool=solo -l info
```

Tareas ya programadas (en el `beat_schedule` de `finanzas_project/celery.py`):

- `configuracion.tasks.actualizar_tasas_diario` — **7:00 AM**: UF/USD del día (mindicador.cl).
- `banca.tasks.sync_activos_diario` — **8:00 AM**: refresca cripto/inversiones vía API (sin navegador).
- `core.tasks.recordatorios_pagos_diario` — **8:30 AM**: avisa por Telegram los pagos próximos
  usando los días configurados por cada usuario.
- `core.tasks.alertas_financieras_periodicas` — cada 15 minutos: envía a la hora local elegida
  el resumen diario y detecta provisionales Apple Pay atrasados.

Otras tareas disponibles (no programadas por defecto):

- `banca.tasks.sync_no_facturados_diario` / `sync_estado_cuenta_mensual` — sync de bancos
  (Playwright). No se programan solas porque pueden gatillar validación de dispositivo;
  agrégalas al `beat_schedule` a conciencia si quieres.

> **Para recibir recordatorios/alertas por Telegram**, crea un bot con @BotFather y guarda
> su token y tu chat ID en **Configuración → Telegram y alertas**. Las credenciales se cifran
> por usuario; ya no existe un chat global en `.env`.

> macOS: usamos `--pool=solo` para evitar los crashes de fork de Objective-C (y no
> necesitamos concurrencia para un uso personal).

---

## 6. Verificación rápida

```bash
python manage.py check                      # 0 issues
python manage.py makemigrations --check      # sin cambios de esquema
python manage.py test                        # suite de regresión (herméticos: sin red ni navegador)
```

La suite de tests (`banca/`, `gastos/`, `configuracion/`) blinda lo que ya funciona:
huella estable anti-duplicación, registro de connectors (banco vs activo), cifrado de
credenciales, ingesta idempotente de movimientos/saldos, la señal que crea pasivos, la
ingesta de posiciones (cripto/inversiones/AFP) y el ruteo de `run_sync`. No requiere
`BANCA_FERNET_KEY` ni red: los tests fijan una llave efímera y mockean las descargas.

- La sección Banca tiene 6 páginas: Dashboard, Movimientos, Deudas, Categorías, Sync,
  Configuración (todas requieren login).
- Los `SaldoSnapshot` de la sync alimentan automáticamente los pasivos del patrimonio
  (señal `banca/signals.py`), y la reconciliación vincula la bicicleta a `prestamos.Prestamo`.

---

## 7. Depurar el login del banco

El sitio del banco solo se puede inspeccionar **desde tu máquina** (bloquea automatización/red
de entornos sandbox). Si la sync falla en el login con un timeout buscando el campo RUT:

```bash
# Abre el login (sin ingresar credenciales) y vuelca inputs/screenshot/HTML
python manage.py inspeccionar_login --no-headless
```

Esto genera en `~/.bank_sync/debug/`:
- `inspeccion_*.png` — captura de la página de login.
- `inspeccion_*.html` — HTML completo.
- `inspeccion_*.inputs.json` — lista de `<input>` (name/id/type/placeholder), **incluyendo iframes**.

Con el `name`/`id` real del campo RUT y clave se ajustan los selectores en
`bank_sync/connectors/santander.py` (método `login` / `_find_input`). El connector ya busca en
el documento y en cualquier iframe, y **vuelca estos mismos artefactos automáticamente cuando
el login falla** (carpeta `BANK_SYNC_DEBUG_DIR`).

---

## 8. Notas

- **Primer login**: corre `sync_banco --no-headless` una vez por si Santander pide validar
  el dispositivo; la sesión se persiste (`storage_state`) para las siguientes corridas.
- **Seguridad**: nunca commitear `.env` ni la `BANCA_FERNET_KEY`. Las credenciales y la API key
  de IA se guardan solo cifradas; el admin y la UI nunca muestran el blob.
