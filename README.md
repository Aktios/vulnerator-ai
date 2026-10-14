# hermes-pentest

OODA pentest conducido por **Hermes** hablando **directo** a un contenedor **Kali** vía MCP.

## Qué es

`hermes-pentest` ejecuta un bucle OODA (Observe-Orient-Decide-Act) de pentest donde
el cerebro conductor es [Hermes](https://github.com/NousResearch/hermes) y las
herramientas corren en un Kali gobernado por un servidor MCP. Hermes decide qué
ejecutar y lo lanza **directamente** contra el objetivo a través del MCP de Kali.

A diferencia de PentAgent, aquí **no hay core, ni API REST, ni UI, ni gates**. Es un
diseño deliberadamente mínimo: solo dos contenedores y el agente conduciendo de punta
a punta.

## Arquitectura (2 contenedores)

- **kali** — runtime determinista de tools de pentest y recolección (nmap, Prowler,
  ScoutSuite, Cloudsplaining, ...). Expone un servidor `kali-mcp` en `:8900`.
- **ooda-agent** — Hermes (`nousresearch/hermes-agent`) como conductor del bucle OODA.
  Es cliente directo del MCP de Kali (`kali:8900`) y ejecuta las tools de pentest con
  `kali_run`. Mantiene vivo su gateway con `hermes gateway run` (API server interno en
  `:8642`, **no publicado al host**).

No hay servicios `api` ni `ui`: el agente habla directo con Kali.

## Cómo levantarlo

```sh
cp .env.example .env
# Rellena como mínimo:
#   OPENROUTER_API_KEY   (tu key de OpenRouter)
#   HERMES_API_SERVER_KEY  (genera una: openssl rand -hex 32)
docker compose up -d --build
```

## Cómo conducir el pentest (TUI interactivo)

```sh
docker compose up -d --build
docker compose exec -it ooda-agent hermes chat
```

`hermes chat` abre el TUI/REPL interactivo del agente. Desde ahí le pides un pentest
en lenguaje natural; Hermes conduce el OODA y ejecuta las herramientas en Kali
**directamente** con la tool `kali_run` (hablando con `kali:8900`).

`hermes gateway run` (el comando del contenedor) mantiene el contenedor vivo y arranca
el API server interno de Hermes en `:8642`. Ese API server es una vía **programática
alternativa** (OpenAI-compatible), accesible solo por la red interna de docker.

## ⚠️ Advertencia (importante)

Este producto **NO tiene los guardarraíles de PentAgent**: **no hay scope-guard, no hay
consentimiento, no hay audit, no hay gate E4**. Hermes ejecuta directamente lo que
decida contra el objetivo, sin portero que valide el alcance.

Esto es **intencional**. Úsalo **solo contra targets explícitamente autorizados** y en
un **entorno controlado**. Toda la responsabilidad del alcance recae en el operador.
