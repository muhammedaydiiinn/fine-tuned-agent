#!/usr/bin/env bash
# GPU-free local dev helper. GPU services (vllm, model-manager, voice-runtime,
# transcribe, training-worker-gpu) stay behind the `gpu` profile and never start.
#
#   bash infra/scripts/dev.sh up           # build changed images + start (full refresh)
#   bash infra/scripts/dev.sh up <svc>     # same, one service (e.g. supervisor-panel)
#   bash infra/scripts/dev.sh down         # stop everything
#   bash infra/scripts/dev.sh ps           # status
#   bash infra/scripts/dev.sh logs <svc>   # follow logs
#   bash infra/scripts/dev.sh reseed       # reseed policy_content (new prompt/facts)
set -euo pipefail
cd "$(dirname "$0")/../.."

DC="docker compose -f docker-compose.yml -f docker-compose.local.yml"
cmd="${1:-up}"; shift || true

case "$cmd" in
  up)      $DC up -d --build "$@" ;;   # --build => templates/JS/code changes take effect
  down)    $DC down "$@" ;;
  ps)      $DC ps ;;
  logs)    $DC logs -f "$@" ;;
  reseed)  $DC exec -T agent-backend python -m app.reseed_policy_content "$@" ;;
  *)       echo "usage: dev.sh {up|down|ps|logs|reseed} [service]" >&2; exit 2 ;;
esac
