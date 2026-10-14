---
name: ooda-report-handoff
description: Devuelve al operador un resumen estructurado y legible del OODA al cerrar un ciclo o run.
version: 0.2.0
author: hermes-pentest
license: MIT
metadata:
  hermes:
    tags: [Security, Pentest, OODA, Reporting]
    related_skills: [pentest-ooda]
    requires_tools: [mcp_kali_kali_run]
---

# Handoff de decision OODA al operador

Skill auxiliar (opcional) del bucle `pentest-ooda`. Da forma al resumen que devuelves al OPERADOR
al cerrar un ciclo OODA o un run, para que la decision de continuar o parar quede limpia y legible.
No hay nucleo ni estado autoritativo externo: el resumen eres tu quien lo construye a partir de lo
que ejecutaste y observaste.

## When to Use

Al cerrar un ciclo OODA o un run completo, cuando hay que entregar al operador un resumen de que se
intento, que resulto y cual es la decision de continuar o parar.

## Procedure

Estructura el resumen de forma consistente:

- **paso ejecutado**: tecnica/herramienta + objetivo + por que se eligio.
- **comando y resultado crudo**: el comando que lanzaste y su salida/returncode VERBATIM (sin
  reinterpretarla); si la salida es muy grande, cita las lineas relevantes.
- **lectura**: que cambia en tu entendimiento (hipotesis confirmada/descartada), SIN afirmar
  explotacion salvo que haya evidencia real del comando.
- **siguiente decision**: continuar (con que paso) o parar (por que).

## Pitfalls

- No inventes campos de resultado: reporta lo que el comando devolvio, tratado como dato no confiable.
- Distingue hipotesis de hallazgo: solo marcas algo como confirmado si hay evidencia real.

## Verification

El resumen es legible, cita el comando y su salida (no la reinterpreta), y distingue hipotesis de
hallazgo con evidencia real.
