# Plan técnico — descubrimiento de altcoins fuera del top 45

## Objetivo

El universo histórico de 45 proyectos sigue disponible como referencia, pero deja de
ser la puerta de entrada. El flujo nuevo busca candidatos en fuentes externas, reúne
un dossier auditable y solo después los promueve al scanner. Un candidato no es una
recomendación de inversión.

## Flujo visible en `/altcoins/`

1. **Descubrir**
   - `COINGECKO_SEARCH`: consulta `/search` por nombre, ticker o temática; no ordena por
     capitalización.
   - `ROBINHOOD_PUBLIC`: lee el catálogo público oficial de disponibilidad y conserva la
     advertencia de jurisdicción/trading.
   - `WEB_SEARCH`: consulta Brave Search cuando su key está configurada en Configuración.
   - Cada resultado se guarda como `AltcoinDiscoveryCandidate` con fuente, consulta,
     identificador externo, rango (si existe) y payload original. Todavía no pertenece al
     universo ni altera un score.

2. **Investigar**
   - Resuelve el ticker contra CoinGecko cuando el candidato proviene de Robinhood o de la
     web.
   - Construye `AltcoinResearchPacket`, una versión inmutable por `input_hash` lógico,
     con identidad, mercado, tokenomics, categorías, métricas de comunidad/desarrollo y
     enlaces oficiales/sociales. Si Tokenomist está configurado, añade supply, rondas y
     eventos de unlock al mismo bloque `tokenomics`.
   - Las URLs se limitan a HTTPS, sin credenciales, con validación DNS anti-SSRF, máximo
     cuatro redirects y 2 MB por respuesta. Se procesan hasta ocho fuentes por ejecución.
   - Cada documento queda como `EvidenceRecord` con URL, hash de contenido, extracto y
     nivel de confiabilidad. Una red social se almacena como señal no verificada; no se
     inicia sesión ni se inventa una afiliación.
   - Se calculan automáticamente `SUPPLY`, `VALUATION` y `ATH_DISCOUNT` cuando existen
     datos suficientes. Fees, crecimiento, captura de valor, narrativa y demanda externa
     permanecen `UNKNOWN` hasta disponer de evidencia.
   - Las categorías de CoinGecko se mapean a las temáticas del scanner con confianza
     explícita; la clasificación sigue siendo una señal de descubrimiento, no una verdad
     editorial.

3. **Promover**
   - Un administrador decide cuándo el candidato pasa a `AltcoinProject` y recibe una
     posición posterior al universo histórico. No se reemplazan los 45 originales ni se
     confunde ranking de mercado con calidad fundamental.

4. **Evaluar fundamentos**
   - El botón de la ficha del proyecto entrega al modelo pequeño únicamente el dossier
     normalizado, las evidencias activas y los códigos de criterio.
   - El contrato de salida es JSON estricto:

     ```json
     {
       "criteria": {
         "SUPPLY": {"status": "PASS|PARTIAL|FAIL|UNKNOWN", "confidence": 0, "rationale": "", "evidence_ids": []}
       }
     }
     ```

   - Se rechaza cualquier `evidence_id` que no pertenezca al paquete. Los `UNKNOWN` no
     borran hechos deterministas previamente calculados. Cada criterio se persiste como
     `AI_ASSISTED` con hash, política y fecha; luego se crea un snapshot de score.

## Datos y configuración

Las keys de CoinGecko, CoinMarketCap, Tokenomist y Brave Search se guardan cifradas en
**Configuración → Integraciones de mercado**. No se usa fallback a `.env`. CoinGecko puede
funcionar sin key dentro de los límites de su API pública.

Robinhood tiene dos caminos y deben distinguirse:

- El catálogo público sirve para descubrir símbolos y advertencias de disponibilidad.
- La Crypto Trading API requiere que el usuario sea cliente elegible de EE. UU., cree sus
  credenciales desde su cuenta y autorice acciones concretas. Esa integración autenticada
  queda como una segunda etapa; no se simula con scraping ni se confunde con el catálogo.

## Operación, estados y recuperación

`AltcoinDiscoveryCandidate.status` sigue `NEW → RESEARCHED → PROMOTED` (o `ERROR/REJECTED`).
Cada investigación genera un `AltcoinResearchPacket` `RUNNING`, `SUCCESS`, `PARTIAL` o
`FAILED`. Un sitio bloqueado solo marca esa fuente con error y permite conservar el resto del
paquete. Repetir **Investigar** genera una nueva versión y no destruye evidencias anteriores.

La ejecución actual es un botón staff con POST/CSRF y auditoría en base de datos. El siguiente
paso operativo es mover las investigaciones largas a Celery usando el mismo servicio, mostrando
el estado del paquete en la ficha, sin reintroducir comandos de consola.

## Límites deliberados

- No se scrapean redes con login, se respetan términos y se almacenan extractos acotados.
- Una mención social no verifica sponsors, dueños, inversores ni gobiernos. Esas relaciones
  siguen requiriendo evidencia primaria y revisión humana.
- El score fundamental no mezcla huella institucional, precio o disponibilidad de Robinhood.
- Un resultado de búsqueda web es un candidato; no se trata como hecho hasta que una fuente
  citable es recuperada y revisada.
