#!/usr/bin/env bash
# Build the self-hosted GramJS browser bundle -> static/gramjs.js
#
# GramJS does NOT publish a prebuilt browser bundle (the npm package is plain
# CommonJS meant to be bundled by the consumer), so we build one ourselves.
# Doing it here rather than pulling a prebuilt file off a CDN is deliberate:
# at runtime the page loads only our own origin, so a third-party CDN
# compromise cannot hand an attacker every user's account.
#
# Node built-ins get aliased away because GramJS pulls in node-only paths
# (node-localstorage -> fs/constants, client/os.js -> os) that are never
# reached in the browser flow but still have to resolve for the bundle to link.
#
# Requires: node + npm. Run from the test_webapp directory.
set -euo pipefail

GRAMJS_VERSION="2.26.22"   # pinned on purpose — never use @latest here
ESBUILD_VERSION="0.24.0"
BUILD_DIR="$(mktemp -d)"
OUT="$(cd "$(dirname "$0")" && pwd)/static/gramjs.js"

echo "==> building GramJS ${GRAMJS_VERSION} in ${BUILD_DIR}"
cd "$BUILD_DIR"
npm init -y >/dev/null 2>&1
npm i --silent \
  "telegram@${GRAMJS_VERSION}" "esbuild@${ESBUILD_VERSION}" \
  buffer process events util stream-browserify crypto-browserify path-browserify \
  >/dev/null 2>&1

# Exactly what the page needs — keeps window.telegram small and predictable.
cat > entry.js <<'EOF'
import { TelegramClient, Api } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { computeCheck } from "telegram/Password.js";
import { AuthKey } from "telegram/crypto/AuthKey.js";
export { TelegramClient, Api, StringSession, computeCheck, AuthKey };
EOF

# GramJS assumes Node globals exist.
cat > shim.js <<'EOF'
import { Buffer } from "buffer";
import process from "process";
window.Buffer = Buffer;
window.process = process;
window.global = window;
EOF

cat > empty.js <<'EOF'
module.exports = {};
EOF

cat > os-shim.js <<'EOF'
module.exports = {
  platform: () => 'browser',
  type: () => 'Browser',
  release: () => '1.0',
  arch: () => 'wasm',
  hostname: () => 'browser',
  EOL: '\n',
};
EOF

npx esbuild entry.js --bundle --format=iife --global-name=telegram \
  --outfile=bundle.js --platform=browser --target=es2020 \
  --inject:shim.js \
  --alias:fs=./empty.js --alias:os=./os-shim.js --alias:constants=./empty.js \
  --alias:path=path-browserify --alias:crypto=crypto-browserify \
  --alias:stream=stream-browserify --alias:net=./empty.js \
  --alias:tls=./empty.js --alias:zlib=./empty.js --alias:http=./empty.js \
  --alias:https=./empty.js --alias:url=./empty.js --alias:assert=./empty.js \
  --define:process.env.NODE_ENV='"production"' \
  --minify --log-limit=0

mkdir -p "$(dirname "$OUT")"
cp bundle.js "$OUT"
echo "==> wrote $OUT ($(du -h "$OUT" | cut -f1))"
echo "==> sha256: $(sha256sum "$OUT" | cut -d' ' -f1)"
rm -rf "$BUILD_DIR"
