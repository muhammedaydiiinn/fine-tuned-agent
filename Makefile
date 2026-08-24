# Fine-Tuned Agent platform — dev/ops shortcuts.
# LOCAL (GPU-free): GPU services (vllm, model-manager, voice/transcribe workers)
# stay behind the `gpu` profile and never start. GPU host uses the `gpu-*` targets.
#
#   make up            # start the GPU-free stack (build changed images)
#   make restart       # full refresh from scratch (down + build + up)
#   make logs s=agent-backend
#   make test          # run every service test suite
#   make help          # list everything

COMPOSE      := docker compose -f docker-compose.yml -f docker-compose.local.yml
COMPOSE_GPU  := docker compose -f docker-compose.yml
PG           := $(COMPOSE) exec -T postgres psql -U fine_tuned_agent -d fine_tuned_agent
s ?=

.DEFAULT_GOAL := help

.PHONY: help up up-build restart down stop ps health logs sh reseed psql \
        test test-agent test-panel test-voice clean prune gpu-up gpu-down gpu-logs

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

## ── Local stack (GPU-free) ──────────────────────────────────────────────────
up: ## Start/refresh the GPU-free stack (build changed images)
	$(COMPOSE) up -d --build $(s)

up-build: up ## Alias for `up`

restart: ## Full refresh from scratch (down, rebuild, up)
	$(COMPOSE) down
	$(COMPOSE) up -d --build

down: ## Stop and remove containers (keeps DB volume)
	$(COMPOSE) down

stop: ## Stop containers without removing them
	$(COMPOSE) stop $(s)

ps: ## Show container status
	$(COMPOSE) ps

health: ## Compact status table
	$(COMPOSE) ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}"

logs: ## Follow logs (all, or one: make logs s=agent-backend)
	$(COMPOSE) logs -f --tail=200 $(s)

sh: ## Shell into a service: make sh s=agent-backend
	$(COMPOSE) exec $(s) sh

## ── Tests ───────────────────────────────────────────────────────────────────
test: test-agent test-panel test-voice ## Run every service test suite

test-agent: ## agent-backend tests (in container)
	$(COMPOSE) exec -T agent-backend python -m pytest tests/ -q

test-panel: ## supervisor-panel tests (in container)
	$(COMPOSE) exec -T supervisor-panel python -m pytest tests/ -q

test-voice: ## voice-runtime tests (host; skips redis-only worker test)
	cd services/voice-runtime && python3 -m pytest tests/ -q --ignore=tests/test_transcribe_worker.py

## ── Data / DB ───────────────────────────────────────────────────────────────
reseed: ## Reseed panel prompt/settings from code defaults (after a prompt change)
	$(COMPOSE) exec -T agent-backend python -m app.reseed_policy_content $(s)

psql: ## Open a psql shell on the app database
	$(COMPOSE) exec postgres psql -U fine_tuned_agent -d fine_tuned_agent

## ── Cleanup ─────────────────────────────────────────────────────────────────
clean: ## Stop and DELETE volumes (wipes the local DB!)
	$(COMPOSE) down -v

prune: ## Remove dangling images/build cache (frees disk)
	docker image prune -f && docker builder prune -f

## ── GPU host (production) ───────────────────────────────────────────────────
gpu-up: ## Start the full stack incl. GPU services (run on the GPU host)
	$(COMPOSE_GPU) --profile gpu up -d --build

gpu-down: ## Stop the full GPU stack
	$(COMPOSE_GPU) --profile gpu down

gpu-logs: ## Follow logs on the GPU stack (make gpu-logs s=vllm-server)
	$(COMPOSE_GPU) --profile gpu logs -f --tail=200 $(s)
