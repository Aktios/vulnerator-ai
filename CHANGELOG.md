# Changelog

All notable changes to Vulnerator AI are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.1.0]: https://github.com/irraz/vulnerator-ai/releases/tag/v1.1.0
[1.0.1]: https://github.com/irraz/vulnerator-ai/releases/tag/v1.0.1
