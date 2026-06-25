#!/usr/bin/env bash
# Check: Node dependency install succeeds, without mutating tracked lockfiles.
# Usage: bash check_install_node.sh <mvp_dir>
#
# Existing App Upgrade runs this against a REAL repository. A plain `npm install`
# rewrites package-lock.json even when nothing actually changed, which then shows
# up as an out-of-boundary file change and blocks Local Delivery. Prefer a
# non-mutating / frozen-lockfile install whenever a lockfile is present, and only
# fall back to a mutating install when there is no lockfile at all.

MVP_DIR="${1:?}"

# Find the package.json location (root or frontend/)
if [ -f "$MVP_DIR/frontend/package.json" ]; then
    INSTALL_DIR="$MVP_DIR/frontend"
elif [ -f "$MVP_DIR/package.json" ]; then
    INSTALL_DIR="$MVP_DIR"
else
    echo "[SKIP] No package.json found — skipping npm install check"
    exit 0
fi

cd "$INSTALL_DIR"

if [ -f "package-lock.json" ]; then
    echo "Running: npm ci (package-lock.json present — non-mutating install)"
    OUTPUT=$(npm ci 2>&1)
    EXIT_CODE=$?
elif [ -f "yarn.lock" ] && command -v yarn >/dev/null 2>&1; then
    YARN_MAJOR=$(yarn --version 2>/dev/null | cut -d. -f1)
    if [ "${YARN_MAJOR:-1}" -ge 2 ] 2>/dev/null; then
        echo "Running: yarn install --immutable (yarn.lock present — non-mutating install)"
        OUTPUT=$(yarn install --immutable 2>&1)
    else
        echo "Running: yarn install --frozen-lockfile (yarn.lock present — non-mutating install)"
        OUTPUT=$(yarn install --frozen-lockfile 2>&1)
    fi
    EXIT_CODE=$?
elif [ -f "pnpm-lock.yaml" ] && command -v pnpm >/dev/null 2>&1; then
    echo "Running: pnpm install --frozen-lockfile (pnpm-lock.yaml present — non-mutating install)"
    OUTPUT=$(pnpm install --frozen-lockfile 2>&1)
    EXIT_CODE=$?
else
    echo "Running: npm install --prefer-offline (no lockfile found — no non-mutating option available)"
    OUTPUT=$(npm install --prefer-offline 2>&1)
    EXIT_CODE=$?
fi

if [ $EXIT_CODE -ne 0 ]; then
    echo "[FAIL] install failed (exit $EXIT_CODE)"
    echo "$OUTPUT" | tail -20
    exit 1
fi

echo "[PASS] install succeeded"
exit 0
