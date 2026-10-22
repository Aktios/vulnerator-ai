#!/bin/bash
# rebuild-containers.sh — Reconstruir contenedores de Vulnerator-AI desde cero
# Uso: ./rebuild-containers.sh

set -e  # Exit on error

echo "🔥 [1/5] Deteniendo y eliminando contenedores existentes..."
docker-compose down -v || true  # -v elimina volúmenes también

echo ""
echo "🧹 [2/5] Limpiando imágenes de Vulnerator-AI..."
docker images | grep vulnerator | awk '{print $3}' | xargs -r docker rmi -f || true

echo ""
echo "🏗️  [3/5] Reconstruyendo imágenes desde cero (sin cache)..."
docker-compose build --no-cache

echo ""
echo "🚀 [4/5] Levantando contenedores..."
docker-compose up -d

echo ""
echo "⏳ [5/5] Esperando a que los servicios estén listos..."
sleep 5

echo ""
echo "✅ Contenedores reconstruidos y arrancados."
echo ""
echo "📋 Estado actual:"
docker-compose ps

echo ""
echo "🔍 Verificaciones:"
echo ""
echo "1. Hermes Dashboard: http://localhost:9119"
echo "   Credenciales: admin / vulnerator"
echo ""
echo "2. Entrar en TUI:"
echo "   docker exec -it ooda-agent hermes tui"
echo ""
echo "3. Ver logs del contenedor OODA:"
echo "   docker logs -f ooda-agent"
echo ""
echo "4. Verificar config Kanban:"
echo "   docker exec ooda-agent hermes config show delegation"
echo ""
echo "5. Test rápido Kanban desde TUI:"
echo "   > Crea un board de prueba llamado 'test-engagement' con 3 tareas: OBSERVE, ORIENT, DECIDE"
echo ""
echo "🎯 Rama actual: $(git branch --show-current)"
echo "📝 Último commit: $(git log -1 --oneline)"
