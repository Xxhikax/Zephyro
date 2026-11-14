#!/usr/bin/env bash
# Inicia el backend Zephyro y luego Tauri en secuencia.
# Uso: npm start   o   bash start.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
BACKEND_PORT="${HTTP_PORT:-8000}"

echo "[Zephyro] Iniciando backend en :$BACKEND_PORT..."
cd "$BACKEND_DIR"
python web_app.py &
BACKEND_PID=$!

echo "[Zephyro] Esperando que el backend esté listo..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$BACKEND_PORT/" > /dev/null 2>&1 || \
       curl -sf "http://localhost:$BACKEND_PORT/api/health" > /dev/null 2>&1; then
        echo "[Zephyro] Backend listo."
        break
    fi
    if [ $i -eq 30 ]; then
        echo "[Zephyro] Backend no respondió en 15s. Continuando de todas formas..."
    fi
    sleep 0.5
done

cd "$SCRIPT_DIR"
echo "[Zephyro] Iniciando Tauri..."
npm run dev

# Cleanup: matar backend al cerrar Tauri
echo "[Zephyro] Cerrando backend (PID $BACKEND_PID)..."
kill "$BACKEND_PID" 2>/dev/null || true
