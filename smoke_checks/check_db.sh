#!/usr/bin/env bash
# Check: database connection works
# Usage: bash check_db.sh <mvp_dir>
#
# Reads .env or .env.example to find DB_NAME, then pings it via psql.

MVP_DIR="${1:?}"

# Look for .env or .env.example
ENV_FILE=""
for f in "$MVP_DIR/.env" "$MVP_DIR/backend/.env" "$MVP_DIR/.env.example" "$MVP_DIR/backend/.env.example"; do
    [ -f "$f" ] && ENV_FILE="$f" && break
done

# Try to extract DB name
DB_NAME=""
if [ -n "$ENV_FILE" ]; then
    DB_NAME=$(grep -E "^DB_NAME|^DATABASE_NAME|^POSTGRES_DB" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '"' | tr -d "'" | tr -d ' ')
fi

# Fallback: check if psql is available at all
if ! command -v psql &>/dev/null; then
    echo "[SKIP] psql not found — skipping DB check"
    exit 0
fi

if [ -z "$DB_NAME" ]; then
    echo "[SKIP] No DB_NAME found in .env — skipping DB check"
    exit 0
fi

echo "Pinging database: $DB_NAME"
OUTPUT=$(psql -d "$DB_NAME" -c "SELECT 1;" 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[FAIL] Could not connect to database '$DB_NAME'"
    echo "$OUTPUT"
    exit 1
fi

echo "[PASS] Database '$DB_NAME' is reachable"
exit 0
