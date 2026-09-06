#!/usr/bin/env bash
# Prune seguro do runtime do smart_auto na VM Oracle.
#
# Remove SOMENTE artefatos efemeros, por lista fechada e padroes restritos,
# dentro de /home/ubuntu/smart_auto. Nunca toca em segredos, estado, codigo,
# units systemd, logs de runtime nem cache/ operacional.
#
# Uso:
#   bash deploy/prune_vm_runtime.sh            # dry-run (padrao): so lista
#   bash deploy/prune_vm_runtime.sh --dry-run  # idem
#   bash deploy/prune_vm_runtime.sh --apply    # remove os alvos listados
#
# Override opcional do host SSH (default: alias "smartauto"):
#   SMART_AUTO_VM_HOST=ubuntu@203.0.113.10 bash deploy/prune_vm_runtime.sh --apply
set -euo pipefail

MODE="dry-run"
case "${1:-}" in
  ""|--dry-run) MODE="dry-run" ;;
  --apply)      MODE="apply" ;;
  -h|--help)    sed -n '2,16p' "$0"; exit 0 ;;
  *) echo "Argumento invalido: ${1}. Use --dry-run ou --apply." >&2; exit 2 ;;
esac

HOST="${SMART_AUTO_VM_HOST:-smartauto}"
PROJ_DIR="/home/ubuntu/smart_auto"

# Lista fechada de padroes efemeros (alinhada a vm-runtime-pruner / limpar-sistema).
# Diretorios excluidos da varredura: .venv, .git, cache (preservados).
# NAO incluir aqui: .env, monitor_state.json, *.log, *.py, *.service, sessoes.
read -r -d '' REMOTE_SCRIPT <<'RS' || true
set -eu
PROJ_DIR="$1"
MODE="$2"
cd "$PROJ_DIR" || { echo "[erro] $PROJ_DIR inexistente"; exit 1; }

# find unico, restrito a padroes efemeros, podando .venv/.git/cache.
build_find() {
  find . \
    \( -path ./.venv -o -path ./.git -o -path ./cache \) -prune -o \
    \( -type d -name '__pycache__' \
       -o -type f -name '*.pyc' \
       -o -type f -name 'folha_ponto_*.pdf' \
       -o -type f -name 'folha_ponto_*.html' \) -print
}

echo "=== servicos (validacao pre-prune) ==="
systemctl is-active smart-auto-bot 2>/dev/null || true
echo ""

echo "=== alvos (lista fechada) ==="
TARGETS="$(build_find || true)"
if [ -z "$TARGETS" ]; then
  echo "(nenhum alvo efemero encontrado)"
else
  printf '%s\n' "$TARGETS"
fi
echo ""

if [ "$MODE" = "apply" ]; then
  echo "=== aplicando remocao ==="
  # remove arquivos efemeros e diretorios __pycache__ por nome, sem glob amplo
  find . \
    \( -path ./.venv -o -path ./.git -o -path ./cache \) -prune -o \
    \( -type f -name '*.pyc' \
       -o -type f -name 'folha_ponto_*.pdf' \
       -o -type f -name 'folha_ponto_*.html' \) -print -delete
  find . \
    \( -path ./.venv -o -path ./.git -o -path ./cache \) -prune -o \
    -type d -name '__pycache__' -print -exec rm -rf {} +
  echo ""
  echo "=== servicos (validacao pos-prune) ==="
  systemctl is-active smart-auto-bot 2>/dev/null || true
else
  echo "[dry-run] nada removido. Rode com --apply para remover os alvos acima."
fi
RS

echo "# prune_vm_runtime.sh | host=$HOST | dir=$PROJ_DIR | modo=$MODE"
ssh "$HOST" 'bash -s' -- "$PROJ_DIR" "$MODE" <<RS2
$REMOTE_SCRIPT
RS2
