# PentAgent Kali — runtime determinista de tools (Prowler) servidas por kali-mcp.
#
# Multi-arch (linux/arm64 + linux/amd64). Base: kalilinux/kali-rolling, cuyo tag
# `latest` es un manifiesto multi-arch que resuelve a la arquitectura del host
# (en Mac M2 -> arm64 nativo).
#
# Build (desde la raíz del repo):
#   docker buildx build --platform linux/arm64,linux/amd64 \
#     -f docker/kali/Dockerfile -t pentagent-kali:dev .
#
# -----------------------------------------------------------------------------
# Compatibilidad arm64 (R14.2, design.md 4.4)
# -----------------------------------------------------------------------------
# - kali-rolling publica imágenes para amd64 y arm64; en Mac M2 corre nativamente
#   en arm64.
# - Prowler es un paquete Python puro (PyPI) -> instalable en arm64 y amd64 sin
#   binarios específicos de arquitectura.
# - Si alguna tool determinista NO estuviera disponible para arm64, el servicio
#   kali-mcp debe REPORTARLO explícitamente (y el smoke test de pentagent-core lo
#   refleja en /health). No se finge disponibilidad: se reporta como no disponible
#   en esta arquitectura. Ver requirements.md R3.2/R3.3 y design.md 4.4.
#
# Nota de versión de Python: kali-rolling trae por defecto Python 3.14, pero
# Prowler aún exige `<3.14`. Por eso instalamos `python3.13` y creamos un
# virtualenv dedicado (/opt/kali-mcp/venv) donde viven Prowler + el SDK `mcp`.
# El servidor arranca con el intérprete de ese venv.

# syntax=docker/dockerfile:1

FROM kalilinux/kali-rolling:latest

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependencias de sistema mínimas para Prowler/kali-mcp. python3.13 es el
# intérprete compatible con Prowler (kali-rolling ya empaqueta 3.13).
# Prowler arrastra alguna dependencia con extensión C (p. ej. `zstd`) que compila
# en arm64; por eso se incluyen toolchain + headers + libzstd/pkg-config.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3.13 \
      python3.13-venv \
      python3.13-dev \
      build-essential \
      pkg-config \
      libzstd-dev \
      ca-certificates \
      curl \
      git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/kali-mcp

# Virtualenv dedicado con Python 3.13 (compatible con Prowler). Ponerlo primero en
# el PATH hace que `python3`/`pip`/`prowler` del venv sean los que se usan.
ENV VIRTUAL_ENV=/opt/kali-mcp/venv \
    PATH=/opt/kali-mcp/venv/bin:$PATH
RUN python3.13 -m venv "$VIRTUAL_ENV" \
 && "$VIRTUAL_ENV/bin/pip" install --no-cache-dir --upgrade pip

# Manifiesto de dependencias del VENV DEL SERVIDOR (F0.7): Prowler + el SDK `mcp` que usa
# kali-mcp. Versiones fijadas en requirements.txt.
COPY docker/kali/requirements.txt ./requirements.txt
RUN "$VIRTUAL_ENV/bin/pip" install --no-cache-dir -r requirements.txt

# Fuentes EN VIVO adicionales (N33/N32) AISLADAS con pipx: cada herramienta en su propio
# venv, con su entrypoint expuesto en /usr/local/bin (en PATH). kali-mcp las invoca como
# SUBPROCESOS (`scout`, `cloudsplaining`), no las importa, así que NO necesitan compartir el
# venv del servidor — y NO pueden: Prowler y ScoutSuite tienen deps incompatibles
# (python-dateutil), un conflicto que tumbaría el build en un venv único. Aislar es el
# patrón correcto para ejecutables independientes y evita el acoplamiento artificial.
#
# Los instala NO-FATALMENTE (`|| true`): si una tool no es instalable en esta arquitectura /
# Python, el build NO se rompe; kali-mcp la reporta ausente (server_info -> which) y su
# collection falla claro (fail-closed, R3.2/4.4). No se finge disponibilidad.
ENV PIPX_HOME=/opt/pipx \
    PIPX_BIN_DIR=/usr/local/bin
RUN "$VIRTUAL_ENV/bin/pip" install --no-cache-dir pipx \
 && ( "$VIRTUAL_ENV/bin/python" -m pipx install --python python3.13 scoutsuite==5.14.0 \
      || echo "[kali-mcp] ScoutSuite no instalable en este runtime; se reportará ausente." ) \
 && ( "$VIRTUAL_ENV/bin/python" -m pipx install --python python3.13 cloudsplaining==0.9.1 \
      || echo "[kali-mcp] Cloudsplaining no instalable en este runtime; se reportará ausente." )

# Servidor kali-mcp (entrypoint real). Expone la tool `prowler_scan` (y
# `server_info` para el smoke test) sobre streamable-HTTP en :8900.
# tool_streaming.py: helper de streaming de salida de tools en vivo (ring buffer +
# ejecucion con kill del grupo de procesos al timeout). Lo importa kali_mcp_server.
COPY docker/kali/tool_streaming.py ./tool_streaming.py
# Plano de DATOS (N23), STDLIB PURA: staging del crudo (artifact_store), servidor HTTP
# que lo entrega al core por stream (artifact_http) y finalización del escaneo
# (prowler_artifact). Los importa kali_mcp_server / collector_runner.
COPY docker/kali/artifact_store.py ./artifact_store.py
COPY docker/kali/artifact_http.py ./artifact_http.py
COPY docker/kali/prowler_artifact.py ./prowler_artifact.py
# Runner multi-herramienta genérico (N33): registro por fuente + worker/finalize únicos
# que lanzan prowler/scoutsuite/cloudsplaining bajo el mismo contrato. Lo importa
# kali_mcp_server; reutiliza tool_streaming/artifact_store/prowler_artifact.
COPY docker/kali/collector_runner.py ./collector_runner.py
# Runner AWS del foso OODA: enum IAM read-only acotado (F2, tool `iam_enum`) y simulación de
# permisos read-only (iam:SimulatePrincipalPolicy, tool `iam_simulate`). aws_exec.py/
# aws_simulate.py son STDLIB PURA (build/exec vía tool_streaming); aws_iam_enum.py/
# aws_iam_simulate.py son los scripts boto3 THIN que corren en el venv del servidor. Los
# importa/invoca kali_mcp_server.
COPY docker/kali/aws_exec.py ./aws_exec.py
COPY docker/kali/aws_iam_enum.py ./aws_iam_enum.py
COPY docker/kali/aws_simulate.py ./aws_simulate.py
COPY docker/kali/aws_iam_simulate.py ./aws_iam_simulate.py
# Runner de SHELL CRUDA (tool `kali_exec`): ejecuta un comando arbitrario de la Kali vía
# `sh -c` reutilizando tool_streaming (STDLIB PURA). Lo importa kali_mcp_server.
COPY docker/kali/shell_exec.py ./shell_exec.py
COPY docker/kali/kali_mcp_server.py ./kali_mcp_server.py

# Puerto del servidor kali-mcp. Overridable por entorno (KALI_MCP_PORT) sin
# reconstruir, pero el default del contrato es 8900 (KALI_MCP_URL=http://kali:8900).
ENV KALI_MCP_HOST=0.0.0.0 \
    KALI_MCP_PORT=8900

# Plano de DATOS (N23): el servidor HTTP de artefactos stagea el crudo en
# KALI_ARTIFACTS_DIR y lo sirve en KALI_ARTIFACTS_PORT (DEDICADO, distinto del MCP).
# KALI_ARTIFACTS_TOKEN NO se fija aquí: es obligatorio y se inyecta por el entorno
# (compose, fail-closed); sin él, el canal de datos no arranca y se usa el puente.
ENV KALI_ARTIFACTS_HOST=0.0.0.0 \
    KALI_ARTIFACTS_DIR=/artifacts \
    KALI_ARTIFACTS_PORT=8901

EXPOSE 8900
# Puerto del plano de DATOS. Se EXPONE a la red interna de docker (lo consume la API
# como KALI_ARTIFACTS_URL), pero NO se publica al host en compose: el crudo puede ser
# sensible y no debe salir del entorno docker.
EXPOSE 8901

# Healthcheck: el servidor MCP responde en la raíz (streamable-HTTP montado en "/").
# Un proceso vivo y escuchando responde al transporte MCP (4xx del handshake), no
# connection-refused; eso basta como liveness del contenedor. El smoke test
# funcional real (listar tools) lo hace pentagent-core vía /health.
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=5 \
  CMD curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${KALI_MCP_PORT}/" \
      | grep -Eq '^(200|400|405|406)$'

# kali-mcp sirve las tools deterministas (Prowler) por este puerto; es el endpoint
# que pentagent-core consume como cliente MCP (KALI_MCP_URL). Con `docker compose
# up` el pentester tiene un Kali operativo como runner por defecto, sin configurar
# nada manual. Un Kali remoto/propio sigue siendo override opcional (KALI_MCP_URL).
# El python del PATH es el del venv (Python 3.13 con Prowler + mcp).
CMD ["python3", "kali_mcp_server.py"]
