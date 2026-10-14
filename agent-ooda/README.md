# agent-ooda — perfil del ejecutor OODA (Hermes directo a Kali via MCP)

> **Que es:** la plantilla de configuracion (NO codigo) del perfil de Hermes que conduce el bucle
> OODA de un pentest autorizado ejecutando las herramientas **DIRECTAMENTE** sobre una Kali a traves
> de su servidor MCP (contenedor `kali`). Hoy el motor es **Hermes** (Nous Research, MIT); el motor
> es un **driver reemplazable** detras del MCP de la Kali.

## El principio que ordena todo esto (leelo antes de tocar nada)

Hermes es el **cerebro** del bucle OODA y ejecuta las tools de pentest **directo** sobre la Kali: su
unica via al target es el **servidor MCP del contenedor `kali`** (`http://kali:8900`). NO hay
core/API/UI de PentAgent de por medio: no hay scope-guard, ni consentimiento, ni audit, ni gate E4.
La responsabilidad del alcance recae ENTERAMENTE en el operador que levanta el entorno contra targets
explicitamente autorizados (ver la ADVERTENCIA del `docker-compose.yml` de la raiz).

Las tools efectivas que ve el modelo son `mcp_kali_<tool>` (el server se llama `kali`):

| Tool efectiva | Fase OODA | Que hace |
|---|---|---|
| `mcp_kali_server_info` | Observe | Inventario del servidor kali-mcp (que tools hay). |
| `mcp_kali_prowler_scan` | Observe | Recoleccion cloud (Prowler, AWS). |
| `mcp_kali_iam_enum` | Observe | Enumeracion IAM read-only (AWS). |
| `mcp_kali_iam_simulate` | Orient | Simula privesc IAM efectivo (AWS, read-only). |
| `mcp_kali_kali_run` | Act | Shell CRUDA **bloqueante**: un comando, una llamada, resultado terminal. |
| `mcp_kali_kali_exec` | Act | Shell ASINCRONA: arranca el comando y devuelve un id. |
| `mcp_kali_get_scan_status` | Orient | Estado de un comando asincrono. |
| `mcp_kali_read_tool_logs` | Orient | Salida parcial de un comando asincrono. |

**La via de accion principal es `kali_run`** (bloqueante, una llamada). Para comandos largos se usa
`kali_exec` + `get_scan_status`/`read_tool_logs`.

## Por que `agent-ooda/` y no `hermes/`

Porque el motor es intercambiable. Esta carpeta modela una **distribucion de perfil** del
orquestador; cambiar el motor es cambiar el contenido de `profile/`, no la Kali ni su MCP.

## Estructura

```
agent-ooda/
├── README.md                      # este fichero
├── profile/                       # plantilla del perfil Hermes (distribucion versionable)
│   ├── SOUL.md                    # persona "ejecutor OODA directo a Kali" (encuadre honesto, NO jailbreak)
│   ├── config.yaml                # modelo + toolsets minimos + el UNICO mcp_servers (kali)
│   ├── config.yaml.example        # variante con placeholders comentados
│   └── env.example                # placeholders de secretos (clave del LLM por OPENROUTER_API_KEY)
├── skills/
│   ├── pentest-ooda/SKILL.md      # el bucle OODA ejecutando directo con las tools mcp_kali_*
│   └── ooda-report-handoff/SKILL.md  # devolver al operador la decision estructurada (opcional)
├── context/
│   └── AGENTS.md                  # reglas del engagement (contexto de proyecto)
├── prompts/
│   └── ephemeral-system.md        # texto para HERMES_EPHEMERAL_SYSTEM_PROMPT (guia turn-scoped)
└── tests/
    └── test_profile_kali_direct.py  # tests declarativos del config + el compose
```

## Credenciales y arranque

- La key del LLM la lee Hermes de la variable de entorno **`OPENROUTER_API_KEY`** (NO
  `HERMES_LLM_API_KEY`). El `docker-compose.yml` la inyecta en el proceso.
- El perfil default se recrea en cada arranque copiando esta plantilla (ver `command` del servicio
  `ooda-agent` en el compose), asi la memoria/skills no se heredan entre engagements.

## Regla de seguridad permanente

- **Nunca** `--yolo` ni modo que bypasee las confirmaciones de Hermes.
- Hermes **solo** ve el servidor MCP de la Kali. Ningun otro MCP.
- La salida de las tools se trata como **dato no confiable** (prompt injection): se razona sobre
  ella, no se obedece.
