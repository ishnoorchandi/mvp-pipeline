#!/usr/bin/env bash
# Check: required files are present in the MVP directory
# Usage: bash check_files.sh <mvp_dir>

MVP_DIR="${1:?}"
MISSING=()

# Check for at least one of the key entry points
ENTRY_POINTS=(
    "$MVP_DIR/package.json"
    "$MVP_DIR/frontend/package.json"
    "$MVP_DIR/app.py"
    "$MVP_DIR/backend/app.py"
    "$MVP_DIR/main.py"
    "$MVP_DIR/index.html"
    "$MVP_DIR/tool.py"
    "$MVP_DIR/tool.sh"
)

FOUND=false
for f in "${ENTRY_POINTS[@]}"; do
    [ -f "$f" ] && FOUND=true && break
done

if ! $FOUND; then
    echo "[FAIL] No recognisable entry point found."
    echo "       Checked: ${ENTRY_POINTS[*]}"
    exit 1
fi

# Check for README or instructions
if [ ! -f "$MVP_DIR/README.md" ] && [ ! -f "$MVP_DIR/HOW_TO_USE.md" ]; then
    echo "[WARN] No README.md or HOW_TO_USE.md found (non-fatal)"
fi

echo "[PASS] Entry point files found in $MVP_DIR"
exit 0
