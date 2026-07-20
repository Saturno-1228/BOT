# Registro de decisiones

Este archivo conserva decisiones tecnicas importantes para que el proyecto siga
siendo comprensible al moverlo de equipo o retomarlo despues.

## 2026-07-18 - Estructura inicial

- El proyecto vivira en una sola carpeta portable.
- Se usaran rutas relativas.
- SQLite sera el almacenamiento inicial.
- La IA sera intercambiable y estara desactivada por defecto.
- El primer despliegue funcionara en modo observacion.
- Un bot externo de moderacion podra servir como respaldo durante las pruebas.

## 2026-07-18 - Nombre del bot

- El nombre publico y la identidad del bot seran **Salomé**.
- El paquete tecnico interno continuara llamandose `telegram_guardian`.
- El nombre de usuario registrado mediante BotFather es `@Salome_G_BOT`.
- El token se guardara exclusivamente en el archivo local `.env`, ignorado por
  Git, y nunca en documentos, mensajes o capturas.
