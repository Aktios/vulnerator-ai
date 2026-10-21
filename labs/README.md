# Labs de seguridad — Vulnerator AI

Colección de laboratorios **locales**, **vulnerables** y **autorizados** para entrenamiento, testing y desarrollo de capacidades de explotación con Vulnerator.

> **⚠️ Uso exclusivo en entornos controlados.** Todos los labs de este directorio están diseñados para correr **únicamente en localhost** (bindings a `127.0.0.1`), nunca expuestos a la red. No ejecutar escaneos ni exploits contra sistemas externos. Solo para uso en CTF local, pentesting autorizado y desarrollo de herramientas de seguridad.

---

## Labs disponibles

### 1. `php2shell-lab/` — PHP2Shell (CVE-2026-109618 / CVE-2026-109619)

**Vulnerabilidad:** Session file injection via `session.upload_progress` + LFI/handler-mismatch/OOM → RCE.

**Versión vulnerable:** PHP 8.2.34 (< 8.2.35, parcheado el 21 oct 2026).

**Servicios:**
- nginx → PHP-FPM (`127.0.0.1:18080`)
- Apache mod_php (`127.0.0.1:18081`)
- PHP-FPM directo (`127.0.0.1:9000`)

**Chains reproducibles:**
- **Chain A:** Session injection + LFI → RCE (100% fiable)
- **Chain C:** Handler mismatch (`php` vs `php_serialize`) → `__destruct`
- **Chain D:** Apache mod_php OOM → stale `EG()` → RCE (0-day sin dependencias)

**Documentación:**
- [`php2shell-lab/README.md`](php2shell-lab/README.md) — Descripción general, arranque y parada
- [`php2shell-lab/CHAINS.md`](php2shell-lab/CHAINS.md) — Detalle técnico de las 5 chains (A, B, C, D, E)
- [`php2shell-lab/TESTING.md`](php2shell-lab/TESTING.md) — Guías de verificación paso a paso

**Referencias:**
- [ikow/php2shell-poc](https://github.com/ikow/php2shell-poc) — Investigación original
- [Ademking/php2shell-nuclei-templates](https://github.com/Ademking/php2shell-nuclei-templates) — Templates de detección Nuclei

**Arranque rápido:**
```bash
cd labs/php2shell-lab
docker compose up -d
curl -s http://127.0.0.1:18080/index.php  # Verificar que responde
```

---

## Estructura común de labs

Cada lab sigue esta estructura:

```
labs/<lab-name>/
├── README.md              # Descripción general, arranque, configuración
├── CHAINS.md (opcional)   # Detalle técnico de vectores de ataque
├── TESTING.md (opcional)  # Guías de verificación y ejemplos prácticos
├── docker-compose.yml     # Orquestación de contenedores
├── webroot/               # Aplicación web vulnerable
├── *.ini                  # Configuraciones de PHP/servicios
└── fixtures/ (opcional)   # Datos de prueba, payloads, etc.
```

---

## Añadir un nuevo lab

1. Crear directorio bajo `labs/<nombre-lab>/`
2. Incluir `README.md` con:
   - Descripción de la vulnerabilidad
   - Versión vulnerable exacta
   - Puertos y servicios (todos en `127.0.0.1`)
   - Comandos de arranque/parada
3. `docker-compose.yml` con bindings **exclusivamente a `127.0.0.1`**
4. (Opcional) `CHAINS.md` / `TESTING.md` para detalle técnico y verificación
5. Actualizar este `labs/README.md` con entrada en la tabla de labs

---

## Uso con Vulnerator

Los labs están optimizados para testing con Vulnerator/Hermes. Cuando Vulnerator corra dentro de un contenedor Docker (ej. `vulnerator-ai-kali-1`), usar `host.docker.internal` en lugar de `127.0.0.1` para alcanzar los labs del host:

```
Target: http://host.docker.internal:18080
```

---

## Seguridad y responsabilidad

- ✅ **Permitido:** testing local en tu máquina, entornos de lab controlados, CTFs locales autorizados.
- ❌ **Prohibido:** ejecutar contra hosts externos, infraestructura de producción, sistemas sin autorización explícita.

**Los labs son código educativo y de investigación.** El autor no se responsabiliza del uso indebido de estos materiales. Úsalos de forma ética y legal.

---

## Referencias generales

- [OWASP WebGoat](https://owasp.org/www-project-webgoat/) — Labs web genéricos
- [VulnHub](https://www.vulnhub.com/) — Máquinas virtuales vulnerables
- [HackTheBox](https://www.hackthebox.com/) — Plataforma de CTF online
- [Damn Vulnerable Web Application (DVWA)](https://github.com/digininja/DVWA)

---

**Última actualización:** 21 octubre 2026
