#!/usr/bin/env bash
# Assemble a clean folder for the public GitHub repository of the desktop app.
# It contains only the app, the TermGround pipeline code and the build workflow:
# no paper, no benchmark clauses, no gold labels, no model or llama.cpp binaries (CI downloads them).
# Usage: bash app/packaging/export_repo.sh /path/to/contract2bpmn-repo
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:?give the output folder}"
mkdir -p "$OUT"
rsync -a --delete \
  --exclude '.buildenv' --exclude 'build' --exclude 'dist' --exclude 'models/*.gguf' --exclude 'llama' \
  --exclude 'vendor/llama-*' --exclude '__pycache__' --exclude '*.png' \
  "$ROOT/app/" "$OUT/app/"
mkdir -p "$OUT/src/termground" "$OUT/.github/workflows"
rsync -a --delete --exclude '__pycache__' \
  --include '*.py' --include 'schema/' --include 'schema/*.xsd' --exclude '*' \
  "$ROOT/src/termground/" "$OUT/src/termground/"
cp "$ROOT/.github/workflows/desktop-app.yml" "$OUT/.github/workflows/"
cp "$ROOT/app/packaging/README_repo.md" "$OUT/README.md"
cat > "$OUT/.gitignore" <<'GI'
__pycache__/
app/.buildenv/
app/build/
app/dist/
app/llama/
app/llama_x/
app/llama_pkg
app/models/*.gguf
app/vendor/llama-*/
GI
mkdir -p "$OUT/app/models" && touch "$OUT/app/models/.gitkeep"
echo "exported to $OUT"
find "$OUT" -type f | grep -v '/\.git/' | sort | head -60
