#!/usr/bin/env bash
# Create a worktree for one arm of the harness comparison, and seed the
# gitignored runtime files it needs.
#
# Without the seeding step both arms fail closed at once — no OpenRouter key,
# no OIDC config, no ledger — for a reason that has nothing to do with the
# harness being measured.
set -euo pipefail

ARM="${1:?usage: setup-worktree.sh <arm-name>   (e.g. arm-b-workflow)}"
ROOT="$(git rev-parse --show-toplevel)"
DEST="${ROOT}/../interv_preper-${ARM}"

git -C "$ROOT" worktree add -b "wp4/${ARM}" "$DEST" dev

mkdir -p "${DEST}/.streamlit"
cp "${ROOT}/.env" "${DEST}/.env"
cp "${ROOT}/.streamlit/secrets.toml" "${DEST}/.streamlit/secrets.toml"

# Both arms share one Supabase table, so they would otherwise contaminate each
# other's ledger. Nothing enforces separation server-side; the arms must not be
# run concurrently, or the totals interleave.
echo
echo "Worktree ready:  ${DEST}"
echo "Branch:          wp4/${ARM}  (off dev)"
echo "Seeded:          .env, .streamlit/secrets.toml"
echo
echo "NOTE: both arms write to the same 'spend' table. Do not run them"
echo "      concurrently, and truncate between arms:"
echo "        delete from spend;"
