#!/usr/bin/env bash
# Écrit le jeton OIDC de GitHub (audience Anthropic) dans $ANTHROPIC_IDENTITY_TOKEN_FILE.
# Le jeton GitHub expire après 5 min : avec --boucle, il est renouvelé toutes les 4 min (le SDK relit le fichier à chaque échange).
set -euo pipefail

renouveler() {
  local jeton
  jeton=$(curl -sSf -H "Authorization: Bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
    "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=https://api.anthropic.com" | jq -r .value)
  echo "::add-mask::$jeton"
  (umask 077; printf '%s' "$jeton" > "$ANTHROPIC_IDENTITY_TOKEN_FILE.tmp")
  mv "$ANTHROPIC_IDENTITY_TOKEN_FILE.tmp" "$ANTHROPIC_IDENTITY_TOKEN_FILE"
}

if [ "${1:-}" = "--boucle" ]; then
  while sleep 240; do renouveler || true; done
else
  renouveler
fi
