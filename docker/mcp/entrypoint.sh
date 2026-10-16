#!/bin/sh
# Entrypoint de vulnerator-mcp: UNA imagen, DOS modos de ejecucion.
#
# Selecciona el transporte y arranca el servidor. Dos fuentes equivalentes (el arg tiene
# prioridad sobre la env var):
#   - Argumento $1:   mcp|stdio  -> stdio      ;  http|streamable-http -> streamable-http.
#   - VULNERATOR_MCP_TRANSPORT:  stdio (default) | streamable-http.
#
# stdio es el default (superficie de red cero; el cliente arranca el proceso con
# `docker run -i`). streamable-http es fail-closed: el servidor se niega a arrancar sin
# VULNERATOR_MCP_INBOUND_TOKEN (ver vulnerator_mcp_server.py).
set -eu

mode="${1:-}"

case "$mode" in
  http|streamable-http)
    VULNERATOR_MCP_TRANSPORT="streamable-http"
    ;;
  mcp|stdio)
    VULNERATOR_MCP_TRANSPORT="stdio"
    ;;
  "")
    # Sin argumento: respeta la env var; default stdio.
    VULNERATOR_MCP_TRANSPORT="${VULNERATOR_MCP_TRANSPORT:-stdio}"
    ;;
  *)
    echo "ERROR: modo desconocido '$mode' (usa: stdio|mcp | http|streamable-http)" >&2
    exit 2
    ;;
esac

export VULNERATOR_MCP_TRANSPORT
exec python3 vulnerator_mcp_server.py
