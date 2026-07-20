# Bitacora

## 2026-07-18

- Se creo el area portable del proyecto.
- Se preparo la configuracion de ejemplo.
- Se definio el alcance preliminar y los limites de seguridad.
- No se instalaron dependencias ni se guardaron credenciales.
- Se definio **Salomé** como nombre publico del bot.
- Se registro `@Salome_G_BOT` mediante BotFather.
- Se preparo el archivo privado `.env` con el campo del token vacio.
- Se implemento la version `0.1.0` sin dependencias externas.
- Se agregaron validacion de identidad, SQLite, registros locales y polling.
- Salomé responde comandos basicos sin permisos administrativos ni IA.
- Se añadieron detectores de flood, repeticion, enlaces y menciones masivas.
- Las señales solo conservan conteos, puntuaciones e identificadores; nunca el
  texto de los mensajes.
- Se añadió `/riesgos` para consultar señales recientes en el laboratorio.
- `/riesgos` se restringio a administradores y las señales caducan tras siete
  dias.
- Se añadieron normalizacion Unicode, entidades de Telegram y deduplicacion por
  actualizacion para reducir evasiones y registros duplicados.
- Revision `0.2.1`: la purga de señales se ejecuta tambien cada 24 horas.
- La comprobacion de administradores usa una cache por chat con TTL de cinco
  minutos y `/riesgos` no responde a usuarios no autorizados.
- Se añadieron limites de respuestas por usuario y por chat.
- Revision `0.2.2`: el limite de comandos usa la fecha del mensaje de Telegram,
  por lo que una respuesta HTTP lenta ya no permite evadir el enfriamiento.
- Revision `0.2.3`: se separaron los limites de entrada y salida; las respuestas
  acumuladas no pueden salir al mismo chat con menos de un segundo de distancia.
- Revision `0.2.4`: se acotaron estrictamente memoria, filas de SQLite y logs
  para impedir agotamiento sostenido por usuarios sin privilegios.
- Revision `0.2.5`: la expiracion de memoria se ejecuta cada minuto, los eventos
  atrasados se ordenan por ventana y las fechas ausentes o futuras ya no pueden
  cruzar limites entre chats ni alterar los cooldowns. Tambien se aislaron
  fallos por update, se hicieron idempotentes los eventos y se añadieron
  presupuestos globales de salida y de consultas administrativas.
