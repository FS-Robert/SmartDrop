# deploy — instalar y actualizar el servidor

Servidor: Ubuntu (Oracle Cloud Always Free) con Nginx + HTTPS (Let's Encrypt) delante de Daphne, como servicio de
systemd. La guía paso a paso y el troubleshooting están en
[`docs/MANUAL_TECNICO.md`](../docs/MANUAL_TECNICO.md#6-deployment).

| Archivo | Dónde se usa | Qué hace |
|---|---|---|
| `desplegar.ps1` | Tu PC (Windows) | Sube el código y el `.env` al servidor |
| `instalar_servidor.sh` | Servidor, la primera vez | Instala paquetes, Python, dependencias, migraciones, systemd, Nginx y el certificado HTTPS |
| `actualizar.sh` / `actualizar.ps1` | Servidor / PC | Actualiza dependencias, migra y reinicia |
| `seguridad_env.sh` | Lo usan los scripts | Genera las claves que falten en el `.env` y protege las bases |
| `aplicar_seguridad_nginx.sh` | Servidor | Aplica los límites por IP de Nginx (guarda un respaldo antes) |
| `nginx-smartdrop.conf` | Plantilla | Sitio de Nginx (HTTPS, límites, archivos estáticos, evidencias con X-Accel-Redirect) |
| `smartdrop.service` | Plantilla | Servicio de systemd que arranca Daphne (y dentro, la ingesta MQTT) |
| `smartdrop-respaldo.service` / `.timer` | Plantilla | Respaldo cifrado de la base cada noche (PB064) |
| `preparar_demo.ps1` | Tu PC | Deja datos listos para una demostración |
| `simulador_lecturas.py` | Cualquiera | Envía lecturas de prueba a `/api/lecturas/` como si fuera el ESP32 (`--clave` o `LECTURAS_API_KEY`) |

Comandos útiles en el servidor: `sudo systemctl status smartdrop`, `sudo journalctl -u smartdrop -n 100`,
`sudo systemctl restart smartdrop`, `systemctl list-timers smartdrop-respaldo`, `sudo systemctl start smartdrop-respaldo`
(respaldo ahora) y `journalctl -u smartdrop-respaldo -n 50`.
