# Alcance del proyecto

## Objetivo

Crear un bot portable de Telegram que ayude a proteger y administrar un grupo,
incorpore funciones de IA y mantenga a una persona a cargo de las decisiones
de mayor riesgo.

## Primera version propuesta

1. Conectarse a un grupo de prueba de Telegram.
2. Registrar entradas, salidas, mensajes y acciones administrativas.
3. Detectar flood, enlaces repetidos y patrones basicos de spam.
4. Enviar alertas a un chat privado de administradores.
5. Trabajar primero en modo observacion, sin expulsiones automaticas.
6. Permitir consultas de IA mediante un comando explicito.
7. Guardar configuracion y reputacion localmente con SQLite.

## Funciones posteriores

- CAPTCHA o validacion de solicitudes de ingreso.
- Puntuacion de riesgo y reputacion por usuario.
- Analisis seguro de enlaces.
- Deteccion de suplantacion de administradores.
- Resumen diario del grupo.
- Base de conocimiento para preguntas frecuentes.
- Panel privado para revisar y revertir acciones.
- Modo antiraid.

## Limites de seguridad

- La IA no aplicara expulsiones permanentes sin aprobacion humana.
- El bot recibira solamente los permisos administrativos indispensables.
- No se almacenara el texto completo de los mensajes por defecto.
- Ningun token se escribira en el codigo o en archivos versionados.
- Las acciones del bot quedaran registradas y deberan poder revertirse.
- El contenido de la comunidad queda fuera del alcance de este modulo; el bot
  se limita a seguridad, moderacion tecnica y asistencia de IA.

