#!/usr/bin/env bash
# Check: Flask/backend API starts and responds
# Usage: bash check_api.sh <mvp_dir>
#
# Starts the backend on a test port, hits the health endpoint, then kills it.

MVP_DIR="${1:?}"

# Find app.py
if [ -f "$MVP_DIR/backend/app.py" ]; then
    APP_FILE="$MVP_DIR/backend/app.py"
    APP_DIR="$MVP_DIR/backend"
elif [ -f "$MVP_DIR/app.py" ]; then
    APP_FILE="$MVP_DIR/app.py"
    APP_DIR="$MVP_DIR"
else
    echo "[SKIP] No app.py found — skipping API check"
    exit 0
fi

TEST_PORT=15001
TIMEOUT=15
PID=""

cleanup() {
    if [ -n "$PID" ]; then
        kill "$PID" 2>/dev/null || true
        wait "$PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

cd "$APP_DIR"

# Activate venv if present
if [ -d "./venv" ]; then
    source ./venv/bin/activate
fi

echo "Starting Flask on test port $TEST_PORT..."
FLASK_ENV=development PORT=$TEST_PORT python "$APP_FILE" --port "$TEST_PORT" &>/tmp/mvp_api_smoke.log &
PID=$!

# Wait for it to come up
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if curl -sf "http://127.0.0.1:$TEST_PORT/" >/dev/null 2>&1 || \
       curl -sf "http://127.0.0.1:$TEST_PORT/health" >/dev/null 2>&1 || \
       curl -sf "http://127.0.0.1:$TEST_PORT/api" >/dev/null 2>&1; then
        echo "[PASS] Flask API started and responded on port $TEST_PORT"
        exit 0
    fi
    sleep 1
    ((ELAPSED++)) || true
done

echo "[FAIL] Flask API did not respond within ${TIMEOUT}s"
echo "--- Last log lines ---"
tail -20 /tmp/mvp_api_smoke.log 2>/dev/null || echo "(no log)"
exit 1
