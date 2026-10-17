# Driving Hermes over the raw HTTP API (`:8642`)

> This is the **low-level** path. For most assistants (Claude Code, Kiro, Cursor, BugTraceAI) the
> recommended way to pilot an engagement is the MCP front door — see the **"Driving a pentest →
> From an MCP client"** section in the [README](../README.md). The MCP server is a thin wrapper
> over exactly the endpoints documented here, so this page is the reference for the underlying
> contract (and for CI scripts / plain `curl`).

Hermes exposes an **OpenAI-compatible** API server (enabled in the profile,
`agent-ooda/profile/config.yaml` → `platforms.api_server`). It lets an assistant or script
**trigger and observe** a pentest over HTTP without entering the `hermes chat` TUI.

**Role of the external caller: trigger and observer, not brain.** The one that reasons and runs the
pentest is Hermes (driven by the `mistralai/mistral-large-4-0` model, chosen on purpose because
Claude-class models reject the offensive framing of authorized pentesting). The caller only sends
the engagement prompt and reads the events; it must **not** try to "think" the pentest or
reinterpret the Kali tools.

**Authentication:** every call carries `Authorization: Bearer <HERMES_API_SERVER_KEY>` (the same key
from the `.env`).

## Two access modes

**Mode A — published port (default).** The compose publishes `:8642` to the host (configurable with
`HERMES_API_HOST_PORT`, default `8642`). Call `http://localhost:8642` directly:

```sh
export KEY=$(grep '^HERMES_API_SERVER_KEY=' .env | cut -d= -f2)

# alive?
curl -s http://localhost:8642/health          # -> {"status":"ok","platform":"hermes-agent","version":"..."}
```

**Mode B — without opening a port (`exec`).** If you prefer not to publish `:8642` to the host, delete
the `ports:` block of the `ooda-agent` service and talk to the API from inside the container:

```sh
docker compose exec ooda-agent \
  curl -s http://localhost:8642/health
# If you launched the stack with '-p <project>', pass it here too:
#   docker compose -p pentest-ana exec ooda-agent curl -s http://localhost:8642/health
```

All the `curl` calls below are the same in both modes; only the prefix changes (direct host vs.
`docker compose exec ooda-agent`).

## Main flow (runs API)

The `runs` pattern is designed precisely for this: launch a long job and subscribe to its progress
instead of handling the streaming by hand.

```sh
# 1) Launch the pentest. The body carries the engagement in natural language in the "input" field.
#    It responds {"run_id":"run_abc123","status":"started"}.
curl -s http://localhost:8642/v1/runs \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "input": "Authorized pentest against 10.0.0.0/24 (scope: that /24 only). Enumerate, identify exposed services and test access paths. Report confirmed findings with evidence."
      }'

# 2) Observe progress: SSE stream of lifecycle events (OODA, tool.start/complete, ...).
curl -N http://localhost:8642/v1/runs/run_abc123/events \
  -H "Authorization: Bearer $KEY"

# 2b) Or query the point-in-time status (status: started | running | completed | failed | cancelled).
curl -s http://localhost:8642/v1/runs/run_abc123 \
  -H "Authorization: Bearer $KEY"

# 3) (Optional) Inject guidance mid-run. Only while status=running; it is delivered at the
#    next tool boundary. If the run no longer accepts steer, it responds 409.
curl -s http://localhost:8642/v1/runs/run_abc123/steer \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": "Focus on port 445 first."}'

# 4) (Optional) Interrupt the run.
curl -s http://localhost:8642/v1/runs/run_abc123/stop \
  -H "Authorization: Bearer $KEY"
```

Fields of the `POST /v1/runs` body (verified against Hermes' official docs): `input` (string,
required) and, optional, `session_id`, `instructions`, `conversation_history`,
`previous_response_id`. For safe retries you can send the `Idempotency-Key` header.

## Alternative: `POST /v1/chat/completions` (OpenAI-compatible)

If your client already speaks the OpenAI format, you can use the classic endpoint (SSE streaming)
instead of the runs API:

```sh
curl -s http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "hermes-agent",
        "messages": [{"role": "user", "content": "Authorized pentest against 10.0.0.0/24 (scope: that /24 only). Report confirmed findings with evidence."}]
      }'
```

## Pattern an assistant/script should follow

1. `GET /health` to confirm the stack is alive.
2. `POST /v1/runs` with the engagement in `input` (explicit target and **scope**, in natural
   language). Save the `run_id` from the response.
3. Follow the run to a terminal state: read the SSE from `GET /v1/runs/{run_id}/events`, or poll
   `GET /v1/runs/{run_id}` until `completed` / `failed` / `cancelled`.
4. Summarize the result (findings, final status). For the formal deliverable, Hermes itself
   generates the Markdown report via the `pentest-report` skill and leaves it as an artifact in the
   data channel (`:8901`).

The caller **does not decide** the tools nor reinterpret the Kali output: that is Hermes' job. Its
role is to trigger, observe and summarize.

## Security note

Publishing `:8642` to the host **lowers the network barrier** the stack had on purpose (before, the
API server was only reachable over the internal docker network). From there on, the endpoint's only
protection is the `HERMES_API_SERVER_KEY` Bearer: use it **only on a controlled machine/environment**,
and if you don't need external access use Mode B (`exec`, without opening a port). See the
**Security & scope** section of the [README](../README.md): there is no scope-guard or authorization
gate by default; responsibility for the scope rests entirely with the operator.
