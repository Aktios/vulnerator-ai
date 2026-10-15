---
name: attack-navigation
description: Usa MITRE ATT&CK como IDIOMA del bucle OODA — orienta por tacticas (que viene despues) y etiqueta cada hallazgo con su technique_id.
version: 0.1.0
author: vulnerator-ai
license: MIT
metadata:
  hermes:
    tags: [Security, Pentest, MITRE, ATTACK, OODA, Orient]
    related_skills: [pentest-ooda, aws-pacu-privesc, l3-recon-pivot, ooda-report-handoff]
    # Transversal: aplica a cualquier engagement con ejecucion en la Kali. Es una LENTE de orientacion
    # y una taxonomia de etiquetado, no un procedimiento de ataque.
    requires_tools: [mcp_kali_kali_run]
---

# ATT&CK como idioma del bucle OODA

[MITRE ATT&CK](https://attack.mitre.org/) es el vocabulario estandar de tacticas y tecnicas
adversarias. NO es tu bucle de control (el bucle sigue siendo OODA) ni un procedimiento de ataque:
es el **idioma con el que ORIENTAS** (nombrar lo que ves y ver que tactica toca despues) y la
**taxonomia con la que ETIQUETAS** cada hallazgo. Ligero a proposito: esto es el esqueleto de
tacticas y las tecnicas mas relevantes a lo que tu Kali puede ejecutar, NO la matriz completa.

Metodologia y IDs tomados de la fuente oficial (https://attack.mitre.org/, dominio Enterprise).

## When to Use

En la fase **Orient** de cualquier engagement, para situar donde estas en el kill-chain y decidir
que tactica es la siguiente logica. Y al registrar un hallazgo, para asignarle su `technique_id`. Si
el engagement es cloud/AWS, combina con `aws-pacu-privesc` (que trae el detalle de ATT&CK Cloud).

## Las 14 tacticas Enterprise (el eje del kill-chain)

Orden tipico de progresion de un engagement. En Orient, pregunta: "¿en que tactica estoy y cual es
la siguiente plausible?".

| ID | Tactica | El "por que" del adversario |
|---|---|---|
| TA0043 | Reconnaissance | Recopilar informacion del objetivo (previa al acceso). |
| TA0042 | Resource Development | Preparar infraestructura/credenciales para el ataque. |
| TA0001 | Initial Access | Entrar en el entorno. |
| TA0002 | Execution | Ejecutar codigo en el objetivo. |
| TA0003 | Persistence | Mantener el acceso. |
| TA0004 | Privilege Escalation | Obtener permisos mas altos. |
| TA0005 | Defense Evasion | Evitar deteccion. |
| TA0006 | Credential Access | Robar credenciales. |
| TA0007 | Discovery | Entender el entorno interno. |
| TA0008 | Lateral Movement | Moverse a otros sistemas. |
| TA0009 | Collection | Reunir datos de interes. |
| TA0011 | Command and Control | Comunicarse con sistemas comprometidos. |
| TA0010 | Exfiltration | Sacar los datos. |
| TA0040 | Impact | Manipular/interrumpir/destruir. |

El orden NO es rigido: un engagement real salta y repite tacticas. El eje sirve para orientar, no
para forzar una secuencia.

## Como integrarlo en OODA (sin cambiar el bucle)

- **Observe.** Lo que observas suele ser Reconnaissance/Discovery. Registra que descubriste.
- **Orient.** Situa lo observado en una tactica y pregunta cual es la siguiente plausible dado tu
  acceso actual. Ejemplo: tienes credenciales validas (T1078) -> la siguiente logica puede ser
  Discovery (TA0007) para mapear permisos, o Privilege Escalation (TA0004) si ya ves una ruta. ATT&CK
  te da el abanico de "que viene despues", que alimenta el **Decide con alternativas** de
  `pentest-ooda`.
- **Decide.** Al elegir UNA accion, nombra la tactica/tecnica que persigue. Eso hace la decision
  trazable y comparable.
- **Act + registro.** Al ejecutar y registrar el resultado, **etiqueta el hallazgo con su
  `technique_id`** (p. ej. `T1552` para credenciales expuestas que encontraste). Esto es lo que, mas
  adelante, permite informes mapeados a ATT&CK y un Navigator layer casi gratis.

## Tecnicas frecuentes por tactica (subconjunto util en esta Kali)

No exhaustivo. Son las que mas probablemente etiquetaras con las tools de pentest/recoleccion de esta
Kali:

- **Reconnaissance (TA0043)**: T1595 Active Scanning (nmap), T1590 Gather Victim Network Info.
- **Initial Access (TA0001)**: T1078 Valid Accounts, T1190 Exploit Public-Facing Application.
- **Execution (TA0002)**: T1059 Command and Scripting Interpreter.
- **Persistence (TA0003)**: T1098 Account Manipulation, T1136 Create Account.
- **Privilege Escalation (TA0004)**: T1548 Abuse Elevation Control Mechanism, T1078 Valid Accounts.
- **Credential Access (TA0006)**: T1552 Unsecured Credentials, T1110 Brute Force.
- **Discovery (TA0007)**: T1046 Network Service Discovery, T1526 Cloud Service Discovery, T1087
  Account Discovery.
- **Lateral Movement (TA0008)**: T1021 Remote Services.
- **Collection/Exfiltration (TA0009/TA0010)**: T1530 Data from Cloud Storage, T1537 Transfer to Cloud
  Account.

Cuando necesites una tecnica que no esta aqui, usa el ID real de attack.mitre.org; no inventes IDs.

## Pitfalls

- **ATT&CK clasifica, no procede.** No lo trates como una checklist a completar de arriba abajo; es
  una lente para orientar y etiquetar. El bucle lo conduces con OODA.
- **No inventes technique_ids.** Si no estas seguro del ID, dilo o busca el real; un ID inventado
  contamina el informe.
- **Etiqueta solo lo que la evidencia respalda.** Marcar un hallazgo con una tecnica es afirmar que
  ocurrio: aplica la misma disciplina hipotesis-vs-confirmado del resto del OODA.

## Verification

Antes de cerrar un ciclo con ATT&CK: (a) orientaste nombrando la tactica actual y la siguiente
plausible; (b) cada hallazgo registrado lleva un `technique_id` REAL y respaldado por evidencia; (c)
no forzaste una secuencia rigida de tacticas ni inventaste IDs.
