# Manifest — PHP2Shell Lab

Inventario completo de ficheros y configuraciones del lab.

---

## Ficheros de documentación

| Fichero         | Propósito                                                                 |
|-----------------|---------------------------------------------------------------------------|
| `README.md`     | Descripción general, versión vulnerable, arranque/parada, endpoints       |
| `CHAINS.md`     | Detalle técnico de las 5 cadenas de ataque (A, B, C, D, E)               |
| `TESTING.md`    | Guías de verificación paso a paso con scripts de prueba                   |
| `MANIFEST.md`   | Este archivo — inventario completo del lab                                |
| `.gitignore`    | Excluir ficheros temporales, logs y sesiones de control de versiones     |

---

## Ficheros de infraestructura

| Fichero              | Propósito                                                             |
|----------------------|-----------------------------------------------------------------------|
| `docker-compose.yml` | Orquestación de 3 contenedores: nginx, php-fpm, apache mod_php       |
| `nginx.conf`         | Configuración de nginx como proxy reverso hacia php-fpm               |
| `php.ini`            | Configuración PHP-FPM vulnerable (`upload_progress=On`, `cleanup=Off`)|
| `php-apache.ini`     | Configuración Apache mod_php (`memory_limit=32M` para Chain D)        |

---

## Aplicación web vulnerable (`webroot/`)

| Fichero / Directorio    | Propósito                                                                |
|-------------------------|--------------------------------------------------------------------------|
| `index.php`             | Endpoint principal con `session_start()`, informa versión/SAPI          |
| `nosession.php`         | Variante sin `session_start()`, informa versión                          |
| `vuln.php`              | **LFI simulado** para Chain A (`include($_GET['page'])` sin sanitizar) |
| `app/.user.ini`         | Handler `php_serialize` (Chain C escritura)                              |
| `app/upload.php`        | Copia de `nosession.php` con handler `php_serialize`                     |
| `api/.user.ini`         | Handler `php` (Chain C lectura/deserialización)                          |
| `api/endpoint.php`      | Copia de `nosession.php` con handler `php`                               |

---

## Contenedores Docker

| Nombre              | Imagen                | Binding                | SAPI            | Propósito                           |
|---------------------|-----------------------|------------------------|-----------------|-------------------------------------|
| `php2shell-nginx`   | `nginx:alpine`        | `127.0.0.1:18080→80`   | (proxy)         | Front nginx hacia php-fpm           |
| `php2shell-fpm`     | `php:8.2.34-fpm`      | `127.0.0.1:9000→9000`  | `fpm-fcgi`      | PHP-FPM vulnerable (Chains A, C, E) |
| `php2shell-apache`  | `php:8.2.34-apache`   | `127.0.0.1:18081→80`   | `apache2handler`| Apache mod_php vulnerable (Chain D) |

---

## Configuración vulnerable clave

### PHP-FPM (`php.ini`)

```ini
session.upload_progress.enabled = On
session.upload_progress.cleanup = Off
session.use_strict_mode = 0
session.use_only_cookies = 0
session.save_path = /tmp/sessions
display_errors = On
error_reporting = E_ALL
```

### Apache mod_php (`php-apache.ini`)

```ini
memory_limit = 32M           # Bajo para facilitar OOM en Chain D
post_max_size = 256M
upload_max_filesize = 256M
session.upload_progress.enabled = On
session.upload_progress.cleanup = Off
display_errors = On
```

---

## Superficie de ataque por chain

| Chain | Endpoint(s)                           | Config clave                                  | Severidad |
|-------|---------------------------------------|-----------------------------------------------|-----------|
| A     | `:18080/vuln.php` + `:18080/index.php`| `upload_progress=On`, `cleanup=Off`, LFI     | Alta      |
| B     | (no implementado)                     | Requiere `unserialize()` endpoint + gadget    | Alta      |
| C     | `:18080/app/` + `:18080/api/`         | Handler mismatch vía `.user.ini`              | Alta      |
| D     | `:18081/` (Apache mod_php)            | `memory_limit=32M`, OOM en startup            | Crítica   |
| E     | `:18080/` (PHP-FPM)                   | Requiere sesión grande pre-poblada            | Alta      |

---

## Verificación rápida

```bash
# 1. Arrancar el lab
docker compose up -d

# 2. Verificar versión vulnerable
curl -s http://127.0.0.1:18080/index.php
# Salida esperada: PHP 8.2.34, SAPI fpm-fcgi, upload_progress 1

# 3. Confirmar contenedores arriba
docker ps --filter "name=php2shell"

# 4. Ejecutar tests (ver TESTING.md)
python3 test_chain_a.py
./test_chain_c.sh
python3 test_chain_d.py

# 5. Parar el lab
docker compose down
```

---

## Tamaños aproximados

- **Imágenes Docker:** ~150MB (`nginx:alpine`) + ~500MB (`php:8.2.34-fpm`) + ~500MB (`php:8.2.34-apache`) = ~1.15GB total
- **Webroot:** <10KB (ficheros PHP mínimos)
- **Configuración:** <5KB (`.ini` y `nginx.conf`)

---

## Referencias técnicas

- **CVE-2026-109618:** RCE via OOM corruption — `longjmp()` leaves request state invalid
- **CVE-2026-109619:** RCE via stale global state — worker retains freed request memory
- **Patch oficial:** [Commit 3584a98](https://github.com/php/php-src/commit/3584a98bda) — 300 files changed, replaces `longjmp()` with delayed soft OOM + hard `_exit()`
- **Investigación original:** [ikow/php2shell-poc](https://github.com/ikow/php2shell-poc)
- **Templates de detección:** [Ademking/php2shell-nuclei-templates](https://github.com/Ademking/php2shell-nuclei-templates)

---

**Lab creado:** 21 octubre 2026  
**Versión vulnerable fijada:** PHP 8.2.34  
**Uso autorizado:** CTF local, pentesting autorizado, desarrollo de herramientas de seguridad  
**Prohibido:** uso contra sistemas externos sin autorización explícita
