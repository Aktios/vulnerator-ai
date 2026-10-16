# Changelog

All notable changes to Vulnerator AI are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-10-16

### Added
- **Curated pentest CLIs baked into the Kali image.** The Kali `Dockerfile` now pre-installs a
  small, deliberate set the skills assume exist and invoke via `kali_run`: `nmap`, `whatweb`,
  `nuclei`, `ffuf`, `sqlmap`, `gobuster`, plus `impacket-scripts` and `bloodhound.py`. The base
  `kalilinux/kali-rolling` image ships none of these, so this fixes a latent runtime gap while
  keeping the image reasonably sized (curated, not the full `kali-linux-headless` metapackage).
- **Documented Juice Shop lab quickstart.** New README subsection documenting how to bring the
  stack up together with `docker-compose.target.yml` (OWASP Juice Shop) and point an engagement
  at `http://juice-shop:3000` — a deliberately-vulnerable web target on the internal network (not
  exposed to the host) for practicing the `web-pentest` skill.

### Changed
- The Ollama startup branch now **warns** (non-fatal, to stderr) when `OLLAMA_BASE_URL` is set but
  `OLLAMA_MODEL` is empty, since the profile default model name would otherwise be used against the
  Ollama endpoint (which almost certainly does not serve it). Behavior is otherwise unchanged.
- Added a regression test for the OpenRouter-vs-Ollama provider-selection startup logic in
  `docker-compose.yml`, protecting the optional `${...:-}` env form, the fail-closed OpenRouter
  guard and the new empty-`OLLAMA_MODEL` warning against silent regressions.

## [1.1.0] - 2026-10-16

### Added
- **Opt-in local / remote Ollama inference.** Set `OLLAMA_BASE_URL` in `.env` to point
  Hermes at an Ollama OpenAI-compatible endpoint (local `http://host.docker.internal:11434/v1`
  or a remote `http://<host>:11434/v1`) instead of OpenRouter. Leave it empty to keep the
  OpenRouter default behavior.
- `OLLAMA_MODEL` to set the Ollama model name used in Ollama mode (e.g. `qwen2.5-coder:32b`).
- `extra_hosts: host.docker.internal:host-gateway` on `ooda-agent` so a host-run Ollama is
  reachable on Linux as well.

### Changed
- `OPENROUTER_API_KEY` is no longer required when running in Ollama mode. The fail-closed
  guard (abort when the key is missing) is re-created in the startup script and now applies
  only to OpenRouter mode, so default behavior is unchanged.
- Documented the Ollama caveats: the endpoint must serve a context window of at least ~64K
  tokens (`OLLAMA_CONTEXT_LENGTH=64000`), and the model must support reliable tool/function
  calling and not refuse the authorized-offensive framing. Local parity with Mistral Large on
  OpenRouter is not guaranteed (experimental).

## [1.0.1] - 2026-10-16

### Added
- **Configurable OpenRouter model via `OPENROUTER_MODEL`.** Override the model Hermes reasons
  with from `.env`; empty falls back to the profile default (`mistralai/mistral-large-4-0`),
  so existing setups are unaffected.
- `VERSION` file.

### Notes
- The project deliberately uses Mistral Large because models like Claude (`anthropic/*`) refuse
  the authorized-offensive pentest framing; overriding with such a model may make the agent
  refuse to work.

[1.2.0]: https://github.com/irraz/vulnerator-ai/releases/tag/v1.2.0
[1.1.0]: https://github.com/irraz/vulnerator-ai/releases/tag/v1.1.0
[1.0.1]: https://github.com/irraz/vulnerator-ai/releases/tag/v1.0.1
