# Gotita, la asistente

Asistente de voz y chat con Claude (Anthropic) para la web y la app. Explica lo que hay en pantalla, responde sobre el
agua de la vivienda y lleva a otras pantallas. **Solo** responde, explica y navega: no escribe código, no da
credenciales, no inicia ni cierra sesión y no mueve la electroválvula (ninguna herramienta puede hacerlo).

| Archivo | Qué hace |
|---|---|
| `vistas.py` | Endpoints: pregunta (`/asistente/turno/`), voz, dictado, avisos e historial; límites por usuario y del sistema |
| `claude.py` | Llamada a Claude con respuesta frase por frase y las herramientas permitidas (navegar, mostrar sección, modo, idioma, velocidad) |
| `prompts.py` | Reglas e instrucciones de Gotita |
| `seguridad.py` | Revisa lo que entra (tamaño, captura de pantalla) y filtra lo que sale (código, claves, groserías) |
| `proteccion.py` | Detecta intentos de manipulación y pone en pausa a quien insiste; topes diarios |
| `destinos.py` | Pantallas a las que puede llevar a cada rol |
| `contexto.py` | Datos de la vivienda (solo lectura) y el nombre de la persona |
| `historial.py` | Conversaciones guardadas 20 días |
| `idioma.py` | Cambio automático entre español e inglés |
| `voz.py` | Voz natural y dictado con ElevenLabs |
| `avisos.py` | Redacta los avisos de los sensores con las palabras de Gotita |
| `caliche.py` | Palabras salvadoreñas que entiende |

Configuración en el `.env`: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ASISTENTE_ACTIVO`, `ELEVENLABS_*`. Las llaves solo
existen en el servidor. Pruebas: `python manage.py test App.tests_asistente`.
