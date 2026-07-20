# Salomé

Base portable para desarrollar un bot de Telegram orientado a seguridad,
moderacion y asistencia con inteligencia artificial.

**Nombre publico:** Salomé. **Usuario de Telegram:** `@Salome_G_BOT`.

## Estado actual

La version `0.2.5` valida la identidad de `@Salome_G_BOT`, prepara SQLite,
escucha comandos y detecta flood, repeticion, rafagas de enlaces y menciones
masivas. No necesita paquetes externos y permanece en modo observacion, sin
acciones administrativas ni IA.

## Estructura

```text
Bot/
|-- config/       Configuracion publica de ejemplo
|-- data/         Base de datos y archivos locales (no se publican)
|-- docs/         Alcance, decisiones y bitacora del proyecto
|-- logs/         Registros locales (no se publican)
|-- src/          Codigo fuente
|-- .env.example  Variables necesarias, sin secretos reales
`-- README.md
```

## Portabilidad

- Todas las rutas del programa deben ser relativas a la raiz del proyecto.
- Los tokens y contrasenas se guardaran en `.env`, nunca en el codigo.
- El almacenamiento inicial sera SQLite dentro de `data/`.
- La IA podra cambiarse entre un modelo local y un proveedor externo mediante
  configuracion, sin modificar la logica de Telegram.
- La aplicacion podra ejecutarse localmente o dentro de un contenedor cuando se
  agregue la primera version funcional.

## Siguiente paso

Comprobar la conexion:

```powershell
$env:PYTHONPATH='src'
python -m telegram_guardian --check
```

Iniciar el bot despues de validar la conexion:

```powershell
$env:PYTHONPATH='src'
python -m telegram_guardian --poll
```

El proceso se detiene de forma segura con `Ctrl+C`.
