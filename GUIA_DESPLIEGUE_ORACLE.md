# Publicar SmartDrop en Internet (Oracle Cloud Always Free + dominio gratis)

Resultado: `https://<tu-nombre>.duckdns.org` con HTTPS, siempre encendido, sin arranques en frío,
con los mismos datos que tienes en localhost (usuarios, lecturas, modelos ML) y la app Android conectada.

## Por qué esta opción y no Render Free

| | Render Free | Oracle Always Free (esta guía) |
|---|---|---|
| Se duerme | Sí, a los 15 min; despertar tarda ~1 min | No, siempre encendido |
| RAM | 512 MB (el motor ML no cabe) | Hasta 24 GB (ARM Ampere) |
| Disco | Se borra en cada reinicio | Persistente: SQLite, modelos y fotos se quedan |
| Monitor de fugas | Se detiene al dormir | Corre cada 10 min |
| Cambios de código | Pasar a Postgres + almacenamiento externo | Casi ninguno |
| Costo | $0 | $0 (piden tarjeta solo para verificar identidad) |

**Velocidad:** tu Supabase está en **us-west-2 (Oregón)**. Desde tu PC en El Salvador cada consulta tarda
~0,7 s; desde un servidor en **US West (San Jose)** tarda ~30-50 ms. El sitio público debería ir **más rápido
que tu localhost**.

## Arquitectura

```text
Navegador / App Android
        │  HTTPS / WSS (Let's Encrypt)
        ▼
   Nginx :443  ── /static/ (caché 1 año) y /media/ directo del disco
        │
        ▼
   Daphne 127.0.0.1:8000  (Django + Channels, servicio systemd)
        ├── db.sqlite3 / ml_timeseries.sqlite3 / ml_models/ / media/  (disco del servidor)
        ├── Supabase REST (us-west-2)
        └── Broker MQTT
```

---

## Paso 1: crea la cuenta de Oracle Cloud (región = San Jose)

1. Entra a <https://www.oracle.com/cloud/free/> → **Start for free**.
2. **Home Region: `US West (San Jose)`.** Esta elección **no se puede cambiar después** y los recursos
   gratis solo existen en tu región de inicio. Si San Jose no aparece, usa `US West (Phoenix)`.
3. Completa la verificación con tarjeta (es un cargo de prueba que se revierte; la cuenta Free no cobra).

> Si rechazan la tarjeta o el registro, suele ser por usar VPN o datos que no coinciden con la tarjeta.
> Prueba sin VPN, con tu dirección real, o con otra tarjeta.

## Paso 2: crea el servidor (instancia)

En la consola: **☰ → Compute → Instances → Create instance**.

| Campo | Valor |
|---|---|
| Name | `smartdrop` |
| Image | **Canonical Ubuntu 24.04** (no la "Minimal") |
| Shape | **Ampere → VM.Standard.A1.Flex**, **2 OCPU y 12 GB** (de sobra; el máximo gratis es 4 y 24) |
| Networking | Create new virtual cloud network + **public subnet**, ✔ *Assign a public IPv4 address* |
| SSH keys | **Generate a key pair for me → Save private key** (guárdala, p. ej. `C:\Users\rober\.ssh\oracle-smartdrop.key`) |
| Boot volume | Por defecto (≈47 GB, gratis hasta 200 GB) |

Pulsa **Create**. Cuando diga *Running*, copia la **Public IP address**.

> **"Out of capacity for shape VM.Standard.A1.Flex"**: es común. Prueba otro *Availability domain*,
> baja a 1 OCPU / 6 GB o reintenta en otro horario. Mientras tanto puedes seguir con los pasos 3 y 4.

## Paso 3: abre los puertos 80 y 443 en Oracle

**☰ → Networking → Virtual cloud networks →** tu VCN **→ Subnets →** la subnet pública **→ Security →**
la *Default Security List* **→ Add Ingress Rules**:

| Source CIDR | IP Protocol | Destination Port Range |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

(El firewall interno de Ubuntu lo abre el script automáticamente.)

## Paso 4: dominio gratis con DuckDNS

1. Entra a <https://www.duckdns.org> e inicia sesión (GitHub o Google).
2. Escribe el subdominio, por ejemplo `smartdrop-ugb`, y pulsa **add domain**.
3. En **current ip** pega la **IP pública de Oracle** y pulsa **update ip**.

Tu dominio será `smartdrop-ugb.duckdns.org`. La IP de Oracle no cambia mientras no borres la instancia,
así que no hace falta nada más.

> ¿Prefieres un dominio "de verdad" (`.me`, `.tech`) gratis por un año? Con el
> [GitHub Student Developer Pack](https://education.github.com/pack) (correo de la UGB) puedes sacarlo en
> Namecheap o get.tech. Solo crea un registro **A** apuntando a la IP de Oracle y usa ese dominio en el paso 6.

## Paso 5: sube los cambios a GitHub

El servidor descarga el código de GitHub, así que primero sube estos cambios (desde la carpeta del backend):

```powershell
git add .gitattributes deploy GUIA_DESPLIEGUE_ORACLE.md SmartDrop
git commit -m "Despliegue en Oracle Cloud"
git push
```

`.env`, las bases SQLite, `ml_models/` y `media/` **no** van a GitHub (están en `.gitignore`); el script
del paso 6 los copia directamente al servidor por SSH.

## Paso 6: despliega con un solo comando

Desde la carpeta del backend (`Backend\SmartDrop`), en PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\desplegar.ps1 -Ip <IP_DE_ORACLE> -Llave C:\Users\rober\.ssh\oracle-smartdrop.key -Dominio smartdrop-ugb.duckdns.org -Correo <tu-correo>
```

Tarda unos 5 minutos la primera vez. Qué hace:

1. Clona el repo en el servidor (`~/SmartDrop`).
2. Sube `.env`, `db.sqlite3`, `ml_timeseries.sqlite3`, `ml_models/` y `media/` (copia consistente
   aunque tengas el servidor local abierto). Si el servidor ya tiene datos, **no los pisa**.
3. En el servidor (`deploy/instalar_servidor.sh`):
   - instala Nginx, Certbot y **Python 3.13** (la misma versión que usas en tu PC);
   - abre los puertos 80/443 en el firewall de Ubuntu;
   - ajusta el `.env` del servidor: `DEBUG=False`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` y
     `REDIS_URL` vacío (un solo proceso usa el canal en memoria);
   - ejecuta las migraciones de ambas bases y `collectstatic`;
   - crea el servicio `smartdrop` (Daphne), que arranca solo y se reinicia si falla;
   - configura Nginx (gzip, caché de estáticos, WebSockets, subidas de hasta 60 MB);
   - obtiene el certificado HTTPS de Let's Encrypt (acepta sus términos de servicio con tu correo),
     redirige HTTP→HTTPS y activa HTTP/2.

Al terminar verás `Listo: https://smartdrop-ugb.duckdns.org`.

## Paso 7: comprueba

1. Abre `https://smartdrop-ugb.duckdns.org` en una ventana privada: login con estilos (CSS e iconos).
2. Inicia sesión con un usuario de siempre; revisa dashboard, consumo, tanque, válvulas y reportes.
3. Abre la página de un sensor: los datos en tiempo real llegan por WebSocket (`wss://`).
4. Entra al panel ML como admin y lanza una predicción.

Si algo falla, mira los errores del servidor:

```powershell
ssh -i C:\Users\rober\.ssh\oracle-smartdrop.key ubuntu@<IP_DE_ORACLE> "sudo journalctl -u smartdrop -n 100 --no-pager"
```

## Paso 8: app Android

Ya está preparada: el APK **release** usa `https://` y `wss://` con el host de `gradle.properties`:

```properties
smartdropReleaseApiHost=smartdrop-ugb.duckdns.org
```

1. Si tu dominio es otro, cambia esa línea (solo el nombre, sin `https://` ni `/`).
2. Android Studio → **Build → Generate Signed App Bundle / APK → APK → release**. Crea un keystore y
   **guárdalo con su contraseña**: lo necesitarás para cualquier actualización futura del APK.
3. Instala el APK en un teléfono con datos o Wi-Fi y prueba login, dashboard, sensores y reportes.

La variante **debug** sigue usando tu PC (`smartdropApiHost`), así que puedes seguir desarrollando igual.

## Cómo publicar cambios después

1. Programa y prueba en localhost como siempre.
2. `git commit` + `git push`.
3. Publica:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1 -Ip <IP_DE_ORACLE> -Llave C:\Users\rober\.ssh\oracle-smartdrop.key
```

Eso hace `git pull`, instala dependencias nuevas, migra, ejecuta `collectstatic` y reinicia. **No toca**
los datos del servidor: los usuarios y reportes creados en línea se conservan. (Ojo: desde ese momento la
base "real" es la del servidor; no vuelvas a ejecutar `desplegar.ps1 -ReemplazarDatos` o la pisarías con
la de tu PC.)

## Comandos útiles en el servidor

```bash
ssh -i C:\Users\rober\.ssh\oracle-smartdrop.key ubuntu@<IP_DE_ORACLE>
sudo systemctl status smartdrop          # ¿está corriendo?
sudo journalctl -u smartdrop -f          # logs en vivo (Ctrl+C para salir)
sudo systemctl restart smartdrop         # reiniciar
nano ~/SmartDrop/.env                    # cambiar variables (luego reinicia)
cp ~/SmartDrop/SmartDrop/db.sqlite3 ~/respaldo-$(date +%F).sqlite3   # respaldo rápido
```

## Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| El navegador no carga nada (timeout) | Faltan las reglas del paso 3, o DuckDNS tiene otra IP |
| Certbot: "Timeout during connect" | Igual que arriba: el puerto 80 debe estar abierto y el dominio apuntar a la IP |
| "Bad Request (400)" | El dominio no está en `ALLOWED_HOSTS` del `.env` del servidor |
| "CSRF verification failed" al iniciar sesión | `CSRF_TRUSTED_ORIGINS` debe ser `https://<tu dominio>` |
| 502 Bad Gateway | Daphne no arrancó: `sudo journalctl -u smartdrop -n 100` |
| Página sin estilos | Repite `bash ~/SmartDrop/deploy/actualizar.sh` (vuelve a ejecutar `collectstatic`) |
| La app Android no conecta | Revisa `smartdropReleaseApiHost` y que instalaste el APK *release*, no el *debug* |

## Límites a tener en cuenta

- **Inactividad en Oracle:** Oracle puede recuperar instancias Always Free *muy* ociosas (CPU, red y
  memoria por debajo del 20 % durante 7 días). Si te llega un correo de aviso, entra a la consola; si
  necesitas garantía total, convierte la cuenta a *Pay As You Go* (sigue sin cobrar mientras uses solo
  recursos Always Free).
- **Supabase Free** pausa proyectos sin actividad en 7 días; el servidor consulta Supabase cada 5 minutos,
  así que se mantiene activo.
- **Fotos de reportes:** se guardan en el disco del servidor y cualquiera con el enlace exacto puede verlas.
  Para la demo está bien; para usuarios reales conviene protegerlas.
- **Respaldos:** son SQLite en un solo servidor. Antes de la presentación haz una copia (comando de arriba)
  o descárgala con `scp`.
