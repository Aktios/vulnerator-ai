# Security & scope

> ⚠️ **By default this product has no guardrails: no scope-guard, no consent, no audit, no
> authorization gate.** Hermes runs directly whatever it decides against the target. This is
> **intentional.** Use it **only against explicitly authorized targets** and in a **controlled
> environment**. All responsibility for the scope and for lawful use rests with the operator.

There are two things you *can* turn on, and it helps to know what each does and does **not** cover.

## The opt-in scope-guard (`kali_run` / `kali_exec`)

A deterministic (code, not prompt) gate that validates the destination of every raw-shell action
**before** executing it. It lives inside the Kali MCP, in front of the two raw-shell runners. It
does **not** touch the cloud tools (Prowler/Pacu/IAM) or the browser MCP.

It activates **only** if a scope file exists and contains `enabled: true`; with no file, behavior is
identical to the default (no gate). When active it is **fail-closed**: a destination that is not
allowed — or a call where Hermes did not declare its `targets` — fails with a structured
`SCOPE_VIOLATION` and does not execute. Every violation is logged into the findings store as a
`discarded` record, so an attempt to leave scope is auditable in the report.

Turn it on:

1. Copy the template and edit it with your lab's allowed destinations (`scope.json` is git-ignored):

   ```sh
   cp scope.example.json scope.json
   ```

   ```json
   {
     "enabled": true,
     "allow": [{ "cidr": "10.0.0.0/24" }, { "host": "juice-shop" }],
     "deny": [{ "cidr": "10.0.0.1/32" }],
     "ports": [80, 443, 3000]
   }
   ```

   - `allow`: `{cidr}` matches an IP against a CIDR; `{host}` is a **literal** hostname match (no
     DNS, a suffix does not match). `deny` takes **priority** over `allow`. `ports` is optional:
     when set, only those ports are allowed when the target carries one.

2. Uncomment the bind-mount in `docker-compose.yml` (kali service) so the file lands at
   `/scope/scope.json`, then recreate the service. Create `scope.json` **before** `docker compose
   up` so Docker mounts a file, not a directory.

   ```yaml
   # volumes:
   #   - ./scope.json:/scope/scope.json:ro
   ```

   **Strict `targets`:** with the scope active, Hermes **must** declare the `targets` argument (the
   IP/host/URL the command touches) on `kali_run`/`kali_exec`, or the call is blocked. A best-effort
   parse of the command is an extra belt that can only **block**, never approve — the declared
   `targets` are the source of truth.

   **Kill-switch:** `SCOPE_ENFORCE=0` force-disables the guard even if the file says `enabled: true`.

## The MCP inbound token (`vulnerator-mcp` over HTTP)

When the `vulnerator-mcp` streamable-HTTP service is reachable, it is **fail-closed** on
`VULNERATOR_MCP_INBOUND_TOKEN` and every request must carry it as a Bearer. This gates **only** the
*client → vulnerator-mcp* edge. It does **not** re-protect the pre-existing `:8642` exposure (that
stays under its own `HERMES_API_SERVER_KEY` Bearer), and it is **not** a scope-guard — it controls
*who can reach the trigger*, not *what the engine is allowed to touch*. The local **stdio** mode has
no listener and needs no token.

## What none of this is

The scope-guard gates `kali_run`/`kali_exec` by destination; the inbound token gates reachability.
Neither is consent, a human-approval workflow, or a network firewall. The `scope` argument on the
MCP `start_pentest` tool is **advisory** (folded into the prompt for operator clarity), not an
enforced constraint. The hard brake is the opt-in scope-guard above.
