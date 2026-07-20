# Revisiones de seguridad

## 2026-07-18 - Retencion y consultas administrativas

### Hallazgos

1. La purga de señales se ejecutaba al iniciar, pero no durante una ejecucion
   continua. Por ello, el limite anunciado de siete dias no era estricto hasta
   el siguiente reinicio.
2. Cada uso de `/riesgos` consultaba la lista de administradores mediante una
   llamada nueva a Telegram. Un usuario podia provocar llamadas repetidas y
   contribuir a alcanzar limites HTTP 429.

### Correcciones

- Se conserva la purga al iniciar y se añade mantenimiento cada 24 horas con
  reloj monotono.
- La lista completa de administradores se guarda por chat durante cinco minutos.
  Una consulta nueva dentro de ese periodo no genera trafico HTTP adicional,
  incluso si la realizan usuarios diferentes.
- Las consultas `/riesgos` no autorizadas se ignoran sin enviar respuesta.
- Las respuestas a comandos tienen limites por usuario y por chat para reducir
  abuso y proteger los limites de Telegram.
- La cache se limita a 1024 chats y expulsa la entrada con vencimiento mas
  antiguo para mantener acotado el uso de memoria.

## 2026-07-18 - Limite de comandos y latencia de red

### Hallazgo

El enfriamiento de comandos utilizaba el reloj monotono del proceso. Una llamada
`sendMessage` lenta podia mantener el bucle ocupado y hacer que dos comandos
enviados de inmediato se procesaran con mas de tres segundos de separacion. El
segundo comando evitaba asi el limite aunque Telegram lo hubiera recibido casi
al mismo tiempo que el primero.

### Correccion

- El limite por usuario y la ventana por chat ahora usan `message.date`, una
  marca temporal asignada por Telegram que no depende de nuestra latencia.
- El reloj monotono se conserva para tareas internas como mantenimiento y TTL
  de caches, donde sigue siendo la fuente correcta.
- Se agrego una prueba que simula 25 segundos de bloqueo de red entre dos
  comandos enviados con un segundo de diferencia.

## 2026-07-18 - Respuestas acumuladas

### Hallazgo

El limite de entrada funcionaba, pero comandos legitimos separados por mas de
tres segundos podian acumularse durante una respuesta HTTP lenta y ser enviados
casi juntos al vaciarse la cola.

### Correccion

- La hora de Telegram sigue gobernando el limite de entrada por usuario.
- Un segundo limite usa el reloj monotono de procesamiento para impedir dos
  respuestas al mismo chat con menos de un segundo de separacion.
- La ventana de quince respuestas por minuto tambien usa ahora la hora real de
  procesamiento, que es la adecuada para proteger el limite de salida de la API.

## 2026-07-18 - Agotamiento de memoria y disco

### Hallazgos

- Las claves de ventanas por usuario, huellas de mensajes y enfriamientos no se
  eliminaban al vaciarse sus colas. Texto aleatorio sostenido podia aumentar la
  memoria sin limite.
- Los mapas del limitador de comandos tampoco tenian techo global.
- La retencion solo limpiaba `moderation_signals`; `events` y `salome.log`
  podian crecer indefinidamente.

### Correcciones

- Todas las estructuras en memoria tienen expiracion cada minuto, ventanas con
  longitud maxima y limites LRU estrictos para usuarios, huellas y chats.
- SQLite purga tanto eventos como señales y aplica un objetivo de 50 000 filas
  por tabla. Entre compactaciones cada 256 escrituras puede alcanzar como
  maximo operativo 50 255; al abrir y durante mantenimiento vuelve a 50 000.
- Se añadieron indices de fecha y truncado del WAL durante mantenimiento.
- `salome.log` rota a 5 MiB y conserva tres respaldos: aproximadamente 20 MiB
  como maximo total.
- Los comandos limitados o no autorizados se registran en nivel DEBUG, que no
  genera escritura con la configuracion normal `INFO`.
- `/riesgos` pasa primero por el limite de entrada; los intentos bloqueados no
  consultan la API de administradores.
- Los limites LRU priorizan disponibilidad. Bajo churn extremo un atacante puede
  expulsar estado antiguo y reducir temporalmente la precision, pero no puede
  provocar crecimiento ilimitado de RAM.
- Las marcas temporales atrasadas se insertan ordenadas dentro de ventanas
  pequeñas y acotadas. Las fechas ausentes o no finitas usan el reloj de pared;
  las fechas futuras se recortan al momento actual.
- El gestor de SQLite cierra explicitamente cada conexion. El contexto nativo
  solo confirma o revierte transacciones y no garantiza el cierre del descriptor
  en Windows.

## 2026-07-18 - Aislamiento temporal entre chats

### Hallazgos

- Un maximo temporal global podia permitir que una fecha futura de un chat
  limpiara el cooldown o las ventanas activas de otro.
- Una marca atrasada podia dejar desordenadas las ventanas de repeticion y
  enlaces, cuya duracion es mayor que la ventana de flood.
- La limpieza provocada solo por volumen no garantizaba expiracion durante
  periodos sin mensajes analizables.

### Correcciones

- El cooldown de entrada exige separacion suficiente tanto en la fecha de
  Telegram como en el reloj monotono de procesamiento.
- La recoleccion de estado usa exclusivamente tiempo monotono para comandos y
  tiempo de pared validado para ventanas de moderacion; no comparte un maximo
  entre chats.
- Las ventanas se mantienen ordenadas y cuentan solo eventos dentro del rango
  temporal del mensaje analizado, aunque los updates lleguen fuera de orden.
- El bucle de polling ejecuta limpieza de memoria cada minuto aun cuando no
  llegan mensajes nuevos; los techos LRU siguen siendo la defensa inmediata.

## 2026-07-18 - Updates venenosos y presupuesto global

### Hallazgos

- El offset se guardaba despues de responder. Si `sendMessage` fallaba, el
  mismo update se procesaba indefinidamente y bloqueaba todos los posteriores.
- Los limites de salida eran solo por chat; repartir comandos entre muchos
  grupos podia sumar trafico sin un techo total.
- `/riesgos` podia intentar consultar administradores desde un chat privado o
  renovar la cache en demasiados grupos distintos.

### Correcciones

- Cada update queda aislado. Un fallo al responder se registra, el offset
  avanza y el siguiente update se procesa; no hay reintentos infinitos.
- Los eventos son idempotentes por `(update_id, event_type)` para que un replay
  legitimo no duplique filas.
- El bot admite como maximo 10 respuestas por segundo y 120 por minuto, ademas
  de los limites por chat, y aplica el cooldown del usuario entre chats
  distintos.
- Las renovaciones reales de administradores se limitan a seis por minuto para
  todo el proceso; los resultados se conservan cinco minutos y los fallos,
  treinta segundos.
- `/riesgos` se ignora fuera de grupos y supergrupos.
