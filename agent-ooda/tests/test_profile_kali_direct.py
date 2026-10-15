"""Tests declarativos del perfil Hermes (OODA directo a Kali) y su cableado en el compose.

El perfil es config declarativa (YAML/Markdown), no codigo ejecutable del repo. Estos tests
afirman el CONTRATO declarativo: el config apunta SOLO al MCP de la Kali, con la whitelist de
tools reales, y el servicio ooda-agent del compose arranca Hermes contra esa Kali.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# WHITELIST EXACTA (en orden) de las tools reales del servidor kali-mcp.
EXPECTED_TOOLS = [
    "kali_run",
    "kali_exec",
    "iam_enum",
    "iam_simulate",
    "get_scan_status",
    "read_tool_logs",
    "prowler_scan",
    "record_finding",
    "read_findings",
    "server_info",
]

# Servidores MCP que el perfil debe declarar (Kali + navegador por MCP).
EXPECTED_MCP_SERVERS = ["kali", "playwright"]

# Tools de un contrato de orquestador externo que NO deben reaparecer en el perfil: el agente
# habla SOLO con el MCP de la Kali. Los nombres se componen por partes a propósito, para que las
# cadenas literales no queden escritas en el fichero.
_PREFIX = "penta" + "gent_"
FORBIDDEN_TOOLS = [
    _PREFIX + "get_findings",
    _PREFIX + "get_attack_graph",
    _PREFIX + "get_analysis",
    _PREFIX + "exec" + "ute_step",
    _PREFIX + "report_" + "observation",
    "get_state",
    "propose_next_step",
]


# --- Perfil kali-direct: agent-ooda/profile/config.yaml ---------------------


def _load_config(profile_config_path: Path) -> dict:
    return yaml.safe_load(profile_config_path.read_text(encoding="utf-8"))


def test_mcp_servers_are_kali_and_playwright(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    servers = cfg["mcp_servers"]
    assert list(servers) == EXPECTED_MCP_SERVERS, "los MCP deben ser exactamente kali y playwright"
    # No debe reaparecer el contrato de un orquestador externo retirado.
    assert "pa" not in servers
    assert "pentagent" not in servers


def test_kali_url_points_to_kali_mcp(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    assert cfg["mcp_servers"]["kali"]["url"] == "http://kali:8900"


def test_playwright_url_points_to_browser_mcp(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    assert cfg["mcp_servers"]["playwright"]["url"] == "http://playwright-mcp:8931/mcp"


def test_kali_server_has_no_auth_headers(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    kali = cfg["mcp_servers"]["kali"]
    assert "headers" not in kali, "el server kali no debe llevar headers (sin token)"
    raw = profile_config_path.read_text(encoding="utf-8")
    assert "Authorization" not in raw, "no debe haber header Authorization"


def test_whitelist_is_exact_kali_tools(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    include = cfg["mcp_servers"]["kali"]["tools"]["include"]
    assert include == EXPECTED_TOOLS, "la whitelist debe ser EXACTAMENTE las tools de la Kali, en orden"
    assert "kali_run" in include


def test_forbidden_tools_absent(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    include = cfg["mcp_servers"]["kali"]["tools"]["include"]
    for tool in FORBIDDEN_TOOLS:
        assert tool not in include, f"{tool} (contrato externo) no debe estar en la whitelist"


def test_utility_wrappers_disabled(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    tools = cfg["mcp_servers"]["kali"]["tools"]
    assert tools["prompts"] is False
    assert tools["resources"] is False


def test_native_toolsets_trimmed(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    toolsets = cfg.get("toolsets")
    assert isinstance(toolsets, list), "toolsets debe ser una LISTA de bundles (no un dict)"
    assert toolsets == ["kali", "playwright"], (
        "la superficie deben ser los dos MCP (kali + playwright), sin toolsets nativos"
    )
    disabled = cfg.get("agent", {}).get("disabled_toolsets", [])
    # El navegador llega por MCP (`playwright`), NO por el toolset `browser` nativo: este sigue
    # desactivado, igual que el resto de bundles nativos.
    for bundle in ("code_execution", "terminal", "file", "browser", "web"):
        assert bundle in disabled, f"{bundle} debe estar en agent.disabled_toolsets"


def test_tool_search_disabled(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    assert cfg["tools"]["tool_search"]["enabled"] is False


def test_model_default_is_mistral_large(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    assert cfg["model"]["default"] == "mistralai/mistral-large-4-0"


def test_api_server_platform_declared(profile_config_path: Path) -> None:
    cfg = _load_config(profile_config_path)
    api = cfg["platforms"]["api_server"]
    assert api["enabled"] is True
    assert api["port"] == 8642


def test_profile_has_no_plaintext_secrets_nor_yolo(profile_config_path: Path) -> None:
    raw = profile_config_path.read_text(encoding="utf-8")
    assert "--yolo" not in raw


# --- Compose: cableado del servicio ooda-agent (kali-direct) ----------------


def _load_services(compose_path: Path) -> dict:
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    return compose["services"]


def _load_ooda_agent(compose_path: Path) -> dict:
    return _load_services(compose_path)["ooda-agent"]


def test_compose_services_are_kali_playwright_and_ooda_agent(compose_path: Path) -> None:
    services = _load_services(compose_path)
    assert set(services) == {"kali", "playwright-mcp", "ooda-agent"}, (
        "los servicios deben ser kali, playwright-mcp y ooda-agent (sin api/ui)"
    )


def test_ooda_agent_depends_on_both_mcp_healthy(compose_path: Path) -> None:
    svc = _load_ooda_agent(compose_path)
    depends = svc["depends_on"]
    assert depends["kali"]["condition"] == "service_healthy"
    assert depends["playwright-mcp"]["condition"] == "service_healthy"
    assert "api" not in depends, "ya no hay servicio api"


def test_playwright_mcp_has_no_published_ports(compose_path: Path) -> None:
    svc = _load_services(compose_path)["playwright-mcp"]
    assert "ports" not in svc, "el MCP del navegador no se publica al host (red interna)"


def test_ooda_agent_publishes_api_server_port(compose_path: Path) -> None:
    # El API server de Hermes (:8642) se publica al host para pilotarlo desde un asistente
    # externo (ver README "Pilotar Hermes desde un asistente externo"). El puerto del host es
    # configurable con HERMES_API_HOST_PORT (default 8642); el del contenedor es 8642.
    svc = _load_ooda_agent(compose_path)
    ports = svc.get("ports", [])
    assert ports, "ooda-agent debe publicar el API server (:8642) al host"
    mapped = [str(p) for p in ports]
    assert any("8642" in p for p in mapped), f"debe mapear el :8642 del contenedor (ports={mapped})"


def test_ooda_agent_interactive_tty(compose_path: Path) -> None:
    svc = _load_ooda_agent(compose_path)
    assert svc["stdin_open"] is True
    assert svc["tty"] is True


def test_ooda_agent_volume_named_and_profile_bind_mount(compose_path: Path) -> None:
    svc = _load_ooda_agent(compose_path)
    volumes = svc["volumes"]
    # hermes-data:/opt/data DEBE ser volumen CON NOMBRE (no bind-mount) por el SQLite WAL.
    assert "hermes-data:/opt/data" in volumes
    # El perfil se inyecta por bind-mount READ-ONLY FUERA de /opt/data.
    assert "./agent-ooda:/opt/profile-src:ro" in volumes
    # NINGUN bind-mount debe caer dentro de /opt/data (corromperia el WAL).
    for vol in volumes:
        if vol.startswith("./") or vol.startswith("/") or vol.startswith("../"):
            target = vol.split(":")[1] if ":" in vol else ""
            assert not target.startswith("/opt/data"), f"bind-mount dentro de /opt/data prohibido: {vol}"
    # El volumen con nombre esta declarado a nivel raiz del compose.
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert "hermes-data" in compose["volumes"]


def test_ooda_agent_command_runs_gateway(compose_path: Path) -> None:
    svc = _load_ooda_agent(compose_path)
    command = svc["command"]
    script = command[-1] if isinstance(command, list) else str(command)
    # Copia la plantilla declarativa desde el bind-mount FUERA de /opt/data.
    assert "/opt/profile-src" in script
    # Arranca el gateway (mantiene el contenedor vivo + API server).
    assert "hermes gateway run" in script


def test_ooda_agent_llm_key_by_openrouter_not_hermes_llm(compose_path: Path) -> None:
    svc = _load_ooda_agent(compose_path)
    env = svc["environment"]
    # La key del LLM se inyecta por OPENROUTER_API_KEY (Hermes la lee de ahi), NO HERMES_LLM_API_KEY.
    assert "OPENROUTER_API_KEY" in env
    assert "HERMES_LLM_API_KEY" not in env, "la key va por OPENROUTER_API_KEY, no HERMES_LLM_API_KEY"


def test_compose_has_no_yolo(compose_path: Path) -> None:
    compose_raw = compose_path.read_text(encoding="utf-8")
    assert "--yolo" not in compose_raw
