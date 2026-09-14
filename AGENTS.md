# Guía de agentes — FinanzasPersonalesIA

## Contexto

Aplicación Django para organizar finanzas personales. El proyecto está en desarrollo y
su modelo de datos puede evolucionar; los cambios deben preservar las relaciones y los
datos existentes siempre que el dominio lo permita.

## Fuente de verdad del proyecto

Graphify reemplaza la bitácora y el mapa mental mantenidos manualmente en este archivo.
No agregar aquí historiales de cambios, listas de funcionalidades ni diagramas de la
arquitectura.

- Consultar primero `graphify-out/graph.json` cuando ya exista.
- Usar `/graphify query "pregunta"` para explorar arquitectura o flujos.
- Usar `/graphify path "origen" "destino"` para trazar relaciones.
- Usar `/graphify explain "nodo"` para entender un componente.
- Ejecutar `/graphify . --update` después de agregar o modificar archivos relevantes.
- Reservar `/graphify . --mode deep` para reconstrucciones que requieran relaciones
  inferidas con mayor profundidad.
- No editar manualmente los artefactos de `graphify-out/`.

El historial detallado de cambios corresponde al control de versiones; la arquitectura
y las relaciones corresponden a Graphify.

## Reglas de implementación

- Mantener el aislamiento por usuario en modelos, consultas, vistas, formularios,
  tareas e integraciones.
- Evitar duplicar movimientos o contabilizar dos veces una misma fuente financiera.
- Preservar compatibilidad con datos existentes mediante migraciones seguras cuando
  cambien los modelos.
- Mantener las integraciones externas herméticas en tests mediante mocks o datos
  sintéticos.
- Actualizar `README.md` cuando cambien instalación, configuración, operación o una
  funcionalidad visible para el usuario.

## Pruebas obligatorias

Toda funcionalidad nueva, modificación de lógica financiera o corrección de bug debe
incluir o actualizar tests automatizados antes de considerarse terminada.

- Cubrir como mínimo el caso nominal y un caso límite relevante.
- Cubrir aislamiento por usuario cuando se trabaje con datos de usuarios.
- Ejecutar `venv/bin/python manage.py test` y no cerrar cambios de código con tests
  fallidos.
- Para cambios exclusivamente documentales, basta revisar el diff y validar que las
  instrucciones sean coherentes; no es necesario ejecutar la suite completa.
