# php2shell-lab — Lab Docker local y vulnerable (PHP2Shell)

Entorno de laboratorio **local**, **vulnerable** y **autorizado** para investigar la
vulnerabilidad PHP2Shell (**CVE-2026-109618 / CVE-2026-109619**) en un contexto de CTF
controlado. Replica (adaptado) el lab oficial del investigador `ikow/php2shell-poc/lab`.

> **Uso exclusivo en CTF local autorizado.** Todos los servicios escuchan únicamente en
> `127.0.0.1`, nunca se exponen a la red. No ejecutar escaneos ni exploits contra sistemas
> externos. Este lab sólo sirve para probar la explotación localmente de forma controlada.

## ¿Qué es el lab?

La vulnerabilidad vive en el handler `session.upload_progress` de PHP (habilitado por
defecto) combinado con el bailout OOM del allocator de memoria. Afecta a PHP desde la
versión 5.4.0 hasta la 8.5.11, y está **corregida** en 8.2.35 / 8.3.36 / 8.4.27 / 8.5.12.

Para que el lab sea **realmente vulnerable** se fija una versión afectada anterior al
parche. No se usan las etiquetas flotantes `php:8.2-fpm` / `php:8.2-apache` porque hoy ya
resuelven a la versión parcheada (8.2.35+).

## Versión de PHP corriendo (verificada)

Etiqueta fijada: **`php:8.2.34-fpm`** y **`php:8.2.34-apache`**.

Versión exacta confirmada con `docker exec php2shell-fpm php -v`:

```
PHP 8.2.34 (cli) (built: Oct 21 2026 00:37:31) (NTS)
```

`8.2.34 < 8.2.35` → **vulnerable**.

## Endpoints y puertos (todos en 127.0.0.1)

| Servicio     | Contenedor         | Binding                   | SAPI            | Descripción                          |
|--------------|--------------------|---------------------------|-----------------|--------------------------------------|
| nginx → fpm  | `php2shell-nginx`  | `127.0.0.1:18080` → 80    | `fpm-fcgi`      | nginx como front hacia php-fpm       |
| apache       | `php2shell-apache` | `127.0.0.1:18081` → 80    | `apache2handler`| Apache con mod_php                   |
| php-fpm      | `php2shell-fpm`    | `127.0.0.1:9000` → 9000   | `fpm-fcgi`      | FastCGI php-fpm directo              |

Comprobación rápida:

```bash
curl -s http://127.0.0.1:18080/index.php   # nginx -> php-fpm
curl -s http://127.0.0.1:18081/index.php   # apache mod_php
```

Salida esperada (versión < 8.2.35 y `upload_progress 1`):

```
PHP 8.2.34
SAPI fpm-fcgi
upload_progress 1
OK
```

## Configuración vulnerable

`session.upload_progress` está **activado** y la limpieza (`cleanup`) **desactivada**, lo
que permite que los datos de progreso de subida persistan en los ficheros de sesión:

- `session.upload_progress.enabled = On`
- `session.upload_progress.cleanup = Off`

Ver `php.ini` (FPM) y `php-apache.ini` (Apache mod_php). El servicio Apache usa además
`memory_limit = 32M` para exponer la superficie OOM necesaria para Chain D.

## Mapeo de cadenas de ataque a endpoints

| Cadena   | Endpoint / fichero                              | Qué habilita                                                        |
|----------|-------------------------------------------------|---------------------------------------------------------------------|
| Chain A  | `vuln.php?page=...` (vía :18080 / :18081)       | LFI simulado (`include($_GET['page'])`) hacia el fichero de sesión  |
| Chain C  | `app/` (`.user.ini` → `php_serialize`) y `api/` (`.user.ini` → `php`) | Distintos `session.serialize_handler` por directorio |
| Chain D  | Apache `:18081` (`memory_limit = 32M`)          | Superficie OOM del allocator (bailout) con memoria reducida         |

Ficheros de apoyo:

- `webroot/index.php` — informa versión, SAPI y estado de `upload_progress`.
- `webroot/nosession.php` — variante sin `session_start`.
- `webroot/vuln.php` — LFI simulado para Chain A.
- `webroot/app/.user.ini` → `session.serialize_handler = php_serialize` (Chain C).
- `webroot/app/upload.php` — copia de `nosession.php`.
- `webroot/api/.user.ini` → `session.serialize_handler = php` (Chain C).
- `webroot/api/endpoint.php` — copia de `nosession.php`.

## Arrancar y parar

```bash
# Arrancar (desde php2shell-lab/)
docker compose up -d

# Parar y eliminar contenedores + red
docker compose down
```

El `pull` inicial de las imágenes puede tardar; usa un timeout amplio la primera vez.

## Documentación adicional

- **[CHAINS.md](CHAINS.md)** — Detalle técnico de las 5 cadenas de ataque (A, B, C, D, E), mecánica completa y ejemplos de peticiones.
- **[TESTING.md](TESTING.md)** — Guías de verificación paso a paso con scripts de prueba listos para ejecutar.
- **[MANIFEST.md](MANIFEST.md)** — Inventario completo de ficheros, configuraciones y superficie de ataque.
- **[examples/](examples/)** — Informes reales de explotaciones exitosas ejecutadas por Vulnerator contra el lab (ej. Chain A → RCE confirmado).

## Referencias externas

- Repo del PoC del investigador: [ikow/php2shell-poc](https://github.com/ikow/php2shell-poc)
- Plantillas Nuclei: [Ademking/php2shell-nuclei-templates](https://github.com/Ademking/php2shell-nuclei-templates)
- CVE-2026-109618 (OOM corruption)
- CVE-2026-109619 (stale global state)
