# Configuration (`.env`)

All keys and credentials live in a `.env` file at the repo root. It is **not** versioned (it is in
`.gitignore`) and is the only place where you put secrets. The template `.env.example` documents
every variable.

The easiest way to produce a working `.env` is the installer — it only asks for your OpenRouter key
and auto-generates the two secret tokens:

```sh
./install.sh
```

To do it by hand, copy the template and fill in the values:

```sh
cp .env.example .env
```

## Required variables

| Variable | Required | Where to put it / how to get it |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | **Yes** | Your OpenRouter API key (the provider of the LLM that reasons the OODA). Create it at <https://openrouter.ai/keys> and paste it into the `.env`. Not required in Ollama mode (see below). |
| `HERMES_API_SERVER_KEY` | **Yes** | Key for Hermes' API server (min. 8 chars). Generate one with `openssl rand -hex 32` and paste it into the `.env`. It is also the Bearer to pilot Hermes over HTTP. |
| `VULNERATOR_MCP_INBOUND_TOKEN` | **Yes** | Inbound Bearer for the `vulnerator-mcp` front door, which is **up by default** in streamable-HTTP mode. Generate one with `openssl rand -hex 32` and paste it into the `.env`. The default stack is **fail-closed** on this: `docker compose up` aborts without it. It is a **separate** secret from `HERMES_API_SERVER_KEY` and gates a different edge. (The local **stdio** mode for Claude Code / Kiro does not need it.) |

> The installer (`./install.sh`) fills `OPENROUTER_API_KEY` from your input and auto-generates both
> `HERMES_API_SERVER_KEY` and `VULNERATOR_MCP_INBOUND_TOKEN` as separate 32-byte hex secrets.

## Optional — AWS (cloud collection)

Only needed if you are going to use the cloud collection tools (Prowler / ScoutSuite /
Cloudsplaining). If you are not auditing AWS, leave these empty. Use **read-only** credentials for
the account you are AUTHORIZED to audit.

| Variable | Required | Where to put it / how to get it |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | No | **Read-only** credential for the account you are AUTHORIZED to audit. |
| `AWS_SECRET_ACCESS_KEY` | No | Secret for that same credential. |
| `AWS_SESSION_TOKEN` | No | Only for temporary credentials (STS). |
| `AWS_REGION` | No | Default region (e.g. `us-east-1`). |

## Optional — model, ports, scoping

| Variable | Required | Where to put it / how to get it |
| --- | --- | --- |
| `OPENROUTER_MODEL` | No | Overrides the OpenRouter model Hermes reasons with. Empty = the profile default (`mistralai/mistral-large-4-0`). **Caveat:** the project uses Mistral Large on purpose because models like Claude (`anthropic/*`) refuse the authorized-offensive pentest framing (OpenRouter/Anthropic returns "violative cyber content"); overriding with such a model may make the agent refuse to work. |
| `VULNERATOR_MCP_HOST_PORT` | No | HOST port to publish the `vulnerator-mcp` streamable-HTTP server. By default it publishes **no** host port (internal docker network only). Set this **and** uncomment the service's `ports:` block in `docker-compose.yml` to publish on a controlled machine. |
| `HERMES_API_HOST_PORT` | No | HOST port where Hermes' `:8642` API server is published. Empty = `8642`. |
| `KALI_MCP_HOST_PORT` | No | HOST port where `kali-mcp` is published for host-side inspection. Empty = `8900`. |
| `KALI_ARTIFACTS_TOKEN` | No | Shared token for the Kali artifacts data channel. |
| `PROWLER_STATUS` / `PROWLER_REGIONS` / `PROWLER_SERVICES` / `PROWLER_TIMEOUT` | No | Scope the Prowler cloud scan. Empty = compose defaults. |

The rest of the `.env.example` variables come with sane defaults; leave them empty unless you need
to change them.

## Local inference with Ollama (optional)

By default Hermes reasons with Mistral Large on OpenRouter. If you prefer to run the model yourself,
point Hermes at an [Ollama](https://ollama.com) endpoint (OpenAI-compatible) instead. This is
**opt-in** and fully controlled from the `.env`:

| Variable | Required | Where to put it / how to get it |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | No | Empty = use OpenRouter (default). Set it to an Ollama OpenAI-compatible endpoint to use Ollama instead: `http://host.docker.internal:11434/v1` for a local host Ollama, or `http://<host>:11434/v1` for a remote one. When set, `OPENROUTER_API_KEY` is **not** required. |
| `OLLAMA_MODEL` | No | The Ollama model name used in Ollama mode, e.g. `qwen2.5-coder:32b`. |

At startup the stack branches on `OLLAMA_BASE_URL`: when it is empty the OpenRouter path is used
exactly as before (and a missing `OPENROUTER_API_KEY` still aborts startup); when it is set, the
startup script runs `hermes config set model.provider custom`, `model.base_url <OLLAMA_BASE_URL>`
and `model.default <OLLAMA_MODEL>`, so Hermes talks to Ollama.

Caveats before you rely on it:

- **Context window (hard blocker):** Hermes refuses to start against an endpoint serving less than
  ~64K tokens of context, and Ollama defaults to as low as 4K. Raise it **server-side** where Ollama
  runs with `OLLAMA_CONTEXT_LENGTH=64000` (it cannot be raised through the API).
- **Networking:** make Ollama listen on all interfaces (`OLLAMA_HOST=0.0.0.0`) so the container can
  reach it. On Linux, reaching a host-run Ollama relies on the
  `extra_hosts: ["host.docker.internal:host-gateway"]` mapping already set on `ooda-agent` (Docker
  Desktop on macOS/Windows resolves `host.docker.internal` automatically).
- **Model suitability (experimental):** every pentest action routes through MCP, so the model must do
  reliable tool/function-calling and must not refuse the authorized-offensive pentest framing. Parity
  with Mistral-Large-on-OpenRouter is **not guaranteed** — treat local inference as experimental and
  validate your chosen model before relying on it.
