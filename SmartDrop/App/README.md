# App — sitio web y API de la app móvil

La aplicación principal de Django: las pantallas de la web, la API que usa la app Android, la seguridad y la lógica
de negocio (válvula, consumo, reportes, alertas). La documentación completa está en
[`docs/MANUAL_TECNICO.md`](../../docs/MANUAL_TECNICO.md).

## Archivos importantes

| Archivo | Qué hace |
|---|---|
| `urls.py` | Rutas de la web (pantallas, reportes, Gotita, consumo del admin, manual de usuario) |
| `views.py` | Pantallas de la web: inicio, tanque, presión, calidad, consumo, válvulas, panel del admin, login y lecturas del ESP32 (`/api/lecturas/`) |
| `views_reportes.py` | Reportes de problemas, chat, evidencias, muro de la comunidad y alertas |
| `views_consumo_admin.py` + `consumo_admin.py` | Consumo por usuario y por zona para el administrador (PB032): pantalla, detalle por usuario y Excel |
| `views_manual.py` | Manual de usuario (web y app) |
| `queries.py` | Consultas a Supabase con caché (viviendas, sensores, lecturas, consumo estimado desde el flujo) |
| `supabase_client.py` | Cliente REST de Supabase; valida los filtros para evitar inyección |
| `valve_service.py` + `mqtt_service.py` | Comandos de la electroválvula por MQTT, temporizador y registro en `log_valvula` |
| `seguridad.py` | Límites de uso (rate limiting), bloqueo por intentos fallidos, revalidación de la sesión con Supabase |
| `seguridad_web.py` | Cabeceras de seguridad (CSP, Permissions-Policy) |
| `ratelimit.py` | Límites de Gotita y de la válvula |
| `backends.py` | Inicio de sesión contra la tabla `usuario` de Supabase (bcrypt) |
| `preferencias.py` | Preferencias del usuario (tema, idioma, avisos) compartidas por web y app |
| `models.py` | Tablas locales: usuario de la sesión, preferencias, historial de Gotita, ajustes de la comunidad |
| `consumers.py`, `routing.py` | WebSocket `/ws/sensors/` (lecturas en tiempo real) |
| `asistente/` | Gotita, la asistente con Claude ([README](asistente/README.md)) |
| `api/` | API REST de la app (DRF + JWT): cuenta, agua, consumo, válvula, reportes, búsqueda |
| `Templates/App/` | Plantillas HTML · `Static/App/` CSS, JS e imágenes |
| `tests*.py` | Pruebas: `python manage.py test App` |

## Reglas

* La web y la app nunca leen Supabase directamente: todo pasa por estas vistas.
* Cada vista revisa el rol: un usuario solo ve sus viviendas; las pantallas del admin redirigen a quien no lo es.
* Los textos para el usuario van en español; los comentarios del código, sencillos.
