---
name: ooda-report-handoff
description: Devuelve al operador un resumen estructurado y legible del OODA al cerrar un ciclo o run.
version: 0.2.0
author: vulnerator-ai
license: MIT
metadata:
  hermes:
    tags: [Security, Pentest, OODA, Reporting]
    related_skills: [pentest-ooda, pentest-report]
    requires_tools: [mcp_kali_read_findings]
---

# Handoff de decision OODA al operador

Skill auxiliar (opcional) del bucle `pentest-ooda`. Da forma al resumen que devuelves al OPERADOR
al cerrar un ciclo OODA o un run, para que la decision de continuar o parar quede limpia y legible.
El resumen lo construyes tu, pero NO de memoria: apoyate en el store durable de hallazgos
(`mcp_kali_read_findings`) para que el resumen refleje lo registrado y no dependa de que el
historial siga en contexto.

## When to Use

Al cerrar un ciclo OODA o un run completo, cuando hay que entregar al operador un resumen de que se
intento, que resulto y cual es la decision de continuar o parar.

## Procedure

Antes de redactar, relee los hallazgos del engagement con `mcp_kali_read_findings` (paginando hasta
`done=true`) para que el resumen se apoye en el registro durable, no solo en lo que recuerdes.

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

## Relacion con el informe formal

Este handoff es el resumen conversacional DURANTE el engagement. Al CERRARLO (entrega final), el
entregable formal lo produce la skill `pentest-report`, que genera un informe Markdown desde el
mismo store de hallazgos. No dupliques ese trabajo aqui: el handoff decide "continuar o parar"; el
informe es el documento de cierre.

## Verification

El resumen es legible, cita el comando y su salida (no la reinterpreta), distingue hipotesis de
hallazgo con evidencia real, y se apoya en lo registrado en el store (`read_findings`), no solo en
la memoria de la conversacion.
