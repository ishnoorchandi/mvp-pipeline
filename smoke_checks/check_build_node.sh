#!/usr/bin/env bash
# Check: npm run build succeeds
# Usage: bash check_build_node.sh <mvp_dir>

MVP_DIR="${1:?}"

if [ -f "$MVP_DIR/frontend/package.json" ]; then
    BUILD_DIR="$MVP_DIR/frontend"
elif [ -f "$MVP_DIR/package.json" ]; then
    BUILD_DIR="$MVP_DIR"
else
    echo "[SKIP] No package.json found — skipping build check"
    exit 0
fi

# Check if a build script exists
cd "$BUILD_DIR"
if ! node -e "const p=require('./package.json'); process.exit(p.scripts && p.scripts.build ? 0 : 1)" 2>/dev/null; then
    echo "[SKIP] No build script in package.json — skipping"
    exit 0
fi

echo "Running: npm run build in $BUILD_DIR"
OUTPUT=$(npm run build 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[FAIL] npm run build failed (exit $EXIT_CODE)"
    echo "$OUTPUT" | tail -30
    exit 1
fi

echo "[PASS] npm run build succeeded"
exit 0
