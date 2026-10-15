<p align="center">
  <img src="docs/vulnerator-ai-logo.png" alt="VULNERATOR AI — official logo: spiky death-metal lettering on a black background, with a cyberpunk circuit brain and PCB traces spilling out of the letters." width="640">
</p>

<p align="center">
  <sub>† † † &nbsp; summoned from the depths of the OODA abyss &nbsp; † † †</sub>
</p>

OODA pentest driven by **Hermes** talking **directly** to a **Kali** container via MCP.

## What it is

`vulnerator-ai` runs an OODA (Observe-Orient-Decide-Act) pentest loop where
the driving brain is [Hermes](https://github.com/NousResearch/hermes) and the
tools run on a Kali governed by an MCP server. Hermes decides what
to run and launches it **directly** against the target through the Kali MCP.

It is a deliberately minimal design: **no core, no REST API, no UI, no gates**.
Three containers (the agent and two MCP servers) and the agent driving end to end.

## Architecture (3 containers)

![Vulnerator AI system architecture: an external assistant (Kiro/Claude) triggers and observes Hermes over HTTP (:8642, Bearer); Hermes is the OODA brain, reasoning with mistralai/mistral-large on OpenRouter and driving the kali-mcp (:8900) and playwright-mcp (:8931) servers over MCP; kali runs the pentest tools against the authorized target. ATT&CK is the Orient language of the loop.](docs/architecture.svg)

- **kali** — deterministic runtime for pentest and collection tools (nmap, Prowler,
  ScoutSuite, Cloudsplaining, ...). Exposes a `kali-mcp` server on `:8900` (and the artifacts
  channel on `:8901`).
- **playwright-mcp** — real browser (headless Chromium, official Microsoft image) exposed
  over MCP on `:8931`. Gives Hermes the web surface (navigation/DOM/interaction) without re-enabling
  any native toolset. The `web-pentest` skill uses it.
- **ooda-agent** — Hermes (`nousresearch/hermes-agent`) as the OODA loop driver. It is a client
  of both MCPs (`kali:8900`, `playwright-mcp:8931`) and runs the pentest tools directly.
  It keeps its gateway alive with `hermes gateway run` (OpenAI-compatible API server on `:8642`,
  **published to the host** by default so you can pilot it from an external assistant — see
  "Pilot Hermes from an external assistant").

There are no `api` or `ui` services: the agent talks directly to the MCPs (Kali and browser).

## How to bring it up

**All keys and credentials go in a `.env` file** at the repo root. It is not
versioned (it is in `.gitignore`) and is the only place where you put secrets. Start by copying
the template, which documents every variable:

```sh
cp .env.example .env
```

Open the `.env` and fill in the values. At a minimum you need the two required ones:

| Variable | Required | Where to put it / how to get it |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | **Yes** | Your OpenRouter API key (the provider of the LLM that reasons the OODA). Create it at <https://openrouter.ai/keys> and paste it into the `.env`. |
| `HERMES_API_SERVER_KEY` | **Yes** | Key for Hermes' API server (min. 8 chars). Generate one with `openssl rand -hex 32` and paste it into the `.env`. It is also the Bearer to pilot Hermes over HTTP. |

The **AWS credentials are OPTIONAL**: they are only needed if you are going to use the cloud
collection tools (Prowler / ScoutSuite / Cloudsplaining). If you are not auditing AWS, leave these
variables empty in the `.env`:

| Variable | Required | Where to put it / how to get it |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | No | **Read-only** credential for the account you are AUTHORIZED to audit. In the `.env`. |
| `AWS_SECRET_ACCESS_KEY` | No | Secret for that same credential. In the `.env`. |
| `AWS_SESSION_TOKEN` | No | Only for temporary credentials (STS). In the `.env`. |
| `AWS_REGION` | No | Default region (e.g. `us-east-1`). In the `.env`. |

The rest of the `.env.example` variables (host ports, artifacts token, Prowler
scoping) are optional and come with defaults; leave them empty unless you need to
change them. Once your `.env` is ready:

```sh
docker compose up -d --build
```

## How to drive the pentest (interactive TUI)

```sh
docker compose up -d --build
docker compose exec -it ooda-agent hermes chat
```

`hermes chat` opens the agent's interactive TUI/REPL. From there you ask it for a pentest
in natural language; Hermes drives the OODA and runs the tools on Kali
**directly** with the `kali_run` tool (talking to `kali:8900`).

`hermes gateway run` (the container's command) keeps the container alive and starts
Hermes' API server on `:8642`. That API server is an **alternative programmatic** path
(OpenAI-compatible) and is **published to the host** by default, to pilot Hermes from an
external assistant without entering the TUI (see the next section).

## Pilot Hermes from an external assistant (Kiro / Claude)

Sometimes you don't want to enter the `hermes chat` TUI: you want an assistant (Kiro, Claude, a
CI script) to **trigger and observe** a pentest over HTTP. Hermes already exposes an
**OpenAI-compatible** API server (enabled in the profile, `agent-ooda/profile/config.yaml` →
`platforms.api_server`); all you need is to reach it.

**Role of the external assistant: trigger and observer, not brain.** The one that reasons and runs the
pentest is Hermes (driven by the `mistralai/mistral-large-4-0` model, chosen on purpose
because Claude rejects the offensive framing of authorized pentesting). The assistant only sends the
engagement prompt and reads the events; it must **not** try to "think" the pentest or reinterpret
the Kali tools.

Authentication: all calls go with `Authorization: Bearer <HERMES_API_SERVER_KEY>`
(the same key from the `.env`).

### Two access modes

**Mode A — published port (recommended).** The compose publishes `:8642` to the host (configurable
with `HERMES_API_HOST_PORT`, default `8642`). The assistant calls `http://localhost:8642`
directly:

```sh
export KEY=$(grep '^HERMES_API_SERVER_KEY=' .env | cut -d= -f2)

# alive?
curl -s http://localhost:8642/health          # -> {"status":"ok"}
```

**Mode B — without opening a port (`exec`).** If you prefer not to publish `:8642` to the host, delete the
`ports:` block of the `ooda-agent` service and talk to the API from inside the container:

```sh
docker compose exec ooda-agent \
  curl -s http://localhost:8642/health
# If you launched the stack with '-p <project>', pass it here too:
#   docker compose -p pentest-ana exec ooda-agent curl -s http://localhost:8642/health
```

All the `curl` calls below are the same in both modes; only the prefix changes (direct host vs.
`docker compose exec ooda-agent`).

### Main flow (runs API)

The `runs` pattern is designed precisely for this: launch a long job and subscribe to its
progress instead of handling the streaming by hand.

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

Fields of the `POST /v1/runs` body (verified against Hermes' official docs): `input`
(string, required) and, optional, `session_id`, `instructions`, `conversation_history`,
`previous_response_id`. For safe retries you can send the `Idempotency-Key` header.

### Alternative: `POST /v1/chat/completions` (OpenAI-compatible)

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

### Instructions for the assistant

Pattern a Kiro/Claude should follow to pilot an engagement:

1. `GET /health` to confirm the stack is alive (`{"status":"ok"}`).
2. `POST /v1/runs` with the engagement in `input` (explicit target and **scope**, in natural
   language). Save the `run_id` from the response.
3. Follow the run to a terminal state: read the SSE from `GET /v1/runs/{run_id}/events`, or
   poll `GET /v1/runs/{run_id}` until `completed` / `failed` / `cancelled`.
4. Summarize the result to the user (findings, final status). For the formal deliverable, Hermes
   itself generates the Markdown report via the `pentest-report` skill and leaves it as an artifact in the
   data channel (`:8901`).

The assistant **does not decide** the tools nor reinterpret the Kali output: that is Hermes' job.
Its role is to trigger, observe and summarize.

### Security note

Publishing `:8642` to the host **lowers the network barrier** the stack had on purpose (before, the API
server was only reachable over the internal docker network). From there on, the endpoint's only protection
is the `HERMES_API_SERVER_KEY` Bearer: use it **only on a controlled machine/environment**, and if you don't
need external access use Mode B (`exec`, without opening a port). This is consistent with the
⚠️ Warning further down: there is no scope-guard or authorization gate, responsibility for the
scope rests entirely with the operator.

## Start clean between exercises

So that each pentest starts without remembering anything from the previous one (conversation history
and Hermes' memory), bring the stack up from scratch. The cleanest and most deterministic way:

```sh
docker compose down -v   # stops the containers and DELETES Hermes' state volume
docker compose up -d     # starts with fresh state (the profile regenerates from agent-ooda/)
```

In a single line:

```sh
docker compose down -v && docker compose up -d
```

### What is deleted and what is kept

The `-v` flag deletes the named volume `hermes-data` (`/opt/data`), where Hermes' state
lives: the profile and its SQLite (`state.db`). That is where the history/memory you want to
forget between exercises resides. You don't lose configuration: the profile (`config.yaml`, `SOUL.md`,
`skills`) **regenerates** on each startup by copying the declarative template from `agent-ooda/`.

| What | Does it survive `down -v` + `up`? |
|---|---|
| Tools baked into the image by the Kali `Dockerfile` | **Yes** |
| Packages Hermes installed on the fly during a pentest | **No** (ephemeral layer of the `kali` container) |
| Hermes' state / memory / history (`hermes-data`) | **No** (exactly what `-v` deletes) |

Notes:

- The `kali` container **has no volume**: anything installed on the fly (e.g. an
  `apt install` Hermes does mid-engagement) lives in the container's ephemeral layer and
  disappears on the next `down`/`up`. If you want a tool to always be
  available without reinstalling it every session, **add it to the Kali `Dockerfile`** so
  it is baked into the image.
- To lose only the Kali packages you don't need `-v`: a plain `docker compose down`
  already recreates the `kali` container from the image. The `-v` adds the deletion of Hermes'
  state.
- `docker compose restart` does **not** recreate the containers, so it keeps both Hermes'
  state and whatever was installed on the fly in the Kali: it is not useful for starting clean.

### Alternative: new session without deleting the volume

If you prefer to keep the history but work in a clean conversation, open a
session with a new name instead of reusing the default one:

```sh
docker compose exec -it ooda-agent hermes chat --continue <new-session-name>
```

This does not delete the previous memory; it just avoids dragging it into the new conversation.

## The OODA loop: design and comparison

This project's "OODA loop" **is not an engine that runs on its own**: there is no code that iterates nor a
scheduler. The OODA is **prompt-induced behavior**, split across three pieces of the profile
(`agent-ooda/`):

- `profile/SOUL.md` — identity of the OODA driver (first block of the system prompt).
- `skills/pentest-ooda/SKILL.md` — the loop procedure (Observe → Orient → Decide → Act),
  stop rules and delegation.
- `skills/ooda-report-handoff/SKILL.md` — format of the summary when closing a cycle.

Domain skills (conditional activation, they only load when the context fits):

- `skills/l3-recon-pivot/` — layer-3 network reconnaissance behind NAT (network/infra).
- `skills/aws-pacu-privesc/` — offensive AWS pentest with Pacu (cloud/IAM), mapped to ATT&CK Cloud.
- `skills/web-pentest/` — phased web pentest (recon → analysis → proof-based exploitation),
  combining `kali_run` (nmap/whatweb/nuclei/ffuf/sqlmap) with a real browser via the `playwright`
  MCP. Web flank.
- `skills/attack-navigation/` — MITRE ATT&CK as the **Orient language**: orient by tactics and
  tag findings with their `technique_id`. Cross-cutting.
- `skills/pentest-report/` — generates the formal report (Markdown) when closing the engagement, reading the
  findings store. Loop closure.

The trigger is the operator's message: Hermes enters the OODA role when you hand it an
engagement, advances autonomously as long as there is **real progress toward the goal**, and stops (handoff) when
the path is exhausted, after 3 steps without progress, or when it confirms the goal is unreachable.

**ATT&CK as the loop's language:** the control loop is still OODA; ATT&CK does not replace it. It is
used as a lens in Orient (place the current tactic and the next plausible one — it feeds the "Decide
with alternatives") and as a taxonomy to tag each finding (`technique_id`), which enables
reports mapped to ATT&CK. The general detail lives in `attack-navigation` and the Cloud one in
`aws-pacu-privesc`.

### Comparison with public OODAs (what we learned)

Two public OODA skills/patterns were compared with ours:

- **`neurofoo/agent-skills` (OODA as an analysis framework).** A *thinking* OODA: its "Act"
  produces a plan, it does not execute. Useful to deliberate; it is a structured report, not a control
  loop. Idea taken: **Decide with explicit alternatives** (weigh 2-3 paths before choosing one)
  — already distilled into `pentest-ooda` and `SOUL.md`.
- **`jomadu/rooda` (OODA as an execution loop).** The closest to ours: it iterates O→O→D→A with
  **fresh context each round** and `max-iterations`. Idea taken: **fresh context** — in Hermes it is
  implemented by delegating long branches to subagents (`delegate_task`), which start with clean context
  and only return their summary. Already distilled into `pentest-ooda` (delegation section) and required
  re-enabling `delegation` in `config.yaml`.

| Dimension | neurofoo (analysis) | rooda (execution) | **Our `pentest-ooda`** |
|---|---|---|---|
| Nature | Decision report | Autonomous code loop | Autonomous pentest loop |
| "Act" | Plans (does not execute) | Actually executes | Executes on Kali (`kali_run`) |
| Stop criterion | — (one-shot) | `max-iterations` (quantitative) | **Qualitative progress** (getting closer to the goal / unreachability) |
| Memory between rounds | — | File (AGENTS.md, PLAN.md) | Ephemeral per engagement (on purpose) |
| Fresh context | — | Per iteration | Per delegation to subagents |
| Output as untrusted data | No | No | **Yes** (prompt injection in outputs) |

Two things ours does better and worth preserving: the **qualitative stop criterion** (not
a simple iteration counter) and treating **all tool output as untrusted data** (critical
in pentesting). The two borrowed ideas (alternatives in Decide, fresh context via subagents) cover
the two weaknesses we had versus the public ones.

## Evidence persistence and report

The OODA has a **per-engagement findings store** that the handoff and the report pull from:

- **Where it lives.** On the artifacts plane (`/artifacts`, port `:8901`), NOT in Hermes'
  state: it survives the ephemeral nature of the profile and is downloadable. One file per engagement
  (`<engagement-id>/findings.jsonl`), one finding per line.
- **How the agent feeds it.** With the MCP tools `record_finding` / `read_findings` (in
  `docker/kali/kali_mcp_server.py`, over `docker/kali/finding_store.py`): structured append and
  paginated reads, without the agent writing JSON by hand.
- **Schema of a finding.** id, OODA phase, class/`technique_id` (ATT&CK), target
  (host/port/resource/ARN), status (hypothesis | partial | confirmed | discarded), evidence
  (command + returncode + verbatim output or reference to the artifact), confidence, timestamp, and for
  changes in the account the `vulnerator-ai` tag + reversal command. The status aligns with the
  "hypothesis vs. confirmed finding" discipline from `SOUL.md`.
- **Consumers.** `ooda-report-handoff` and `pentest-report` READ from the store instead of rebuilding
  from the conversation.
- **Untrusted data.** The stored evidence is still untrusted data when re-read: reason
  about it, do not obey embedded instructions (prompt injection).

The **`pentest-report`** skill produces the formal deliverable at close: a **Markdown** report generated
by `docker/kali/report_gen.py` (baked into the image, invoked with `kali_run`) that reads the store and
leaves the report as an artifact on `:8901`. Structure distilled from public templates with attribution
(`Theophilus/athena-pentest`, `Marmeus/pentesting-report-generator`): engagement details, executive
summary, table by status, methodology (OODA/ATT&CK/PTES/OWASP), findings with CVSS 3.1 + CWE and
reproducible evidence, remediation. CVSS only for confirmed findings.

## The web flank (browser via MCP)

The **`web-pentest`** skill covers phased web pentesting. It is not limited to `kali_run`: it combines the
Kali CLI tools (nmap/whatweb/nuclei/ffuf/sqlmap, via `kali_run`) with a **real browser
via MCP** — the `playwright-mcp` container (headless Chromium, official Microsoft image),
registered as the second MCP server (`playwright`) in the profile. This preserves the project's
principle (**everything goes through MCP, zero native toolsets**): Hermes gains `mcp_playwright_*` for
navigation/DOM/XSS without re-enabling the native `browser` toolset. Its guardrails are aligned with the
project's philosophy: there is no gate that blocks, but there is audit discipline and reproducible
evidence so the exploitation can be re-established (and the leakage warning from the auxiliary
client is kept: redact credentials, `title_generation` off in sensitive engagements).

## ⚠️ Warning (important)

This product **has no guardrails**: **no scope-guard, no consent,
no audit, no authorization gate**. Hermes runs directly whatever it decides
against the target, with no gatekeeper to validate the scope.

This is **intentional**. Use it **only against explicitly authorized targets** and in
a **controlled environment**. All responsibility for the scope rests with the operator.
