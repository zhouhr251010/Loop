SHELL := /bin/bash

# Avoid inheriting an unrelated conda environment's native libraries into
# recipe shells; stale LD_LIBRARY_PATH values can make bash print libtinfo
# warnings before Loop even starts.
unexport LD_LIBRARY_PATH

ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
BACKEND_DIR := $(ROOT_DIR)/backend
FRONTEND_DIR := $(ROOT_DIR)/frontend
ENV_FILE := $(ROOT_DIR)/.env
INFRA_COMPOSE_FILE := $(ROOT_DIR)/docker-compose.infra.yml
CONDA_ENV ?= Loop
BACKEND_HOST ?= 127.0.0.1
BACKEND_PORT ?= 8001
BACKEND_WORKERS ?= 16
FRONTEND_HOST ?= 127.0.0.1
FRONTEND_PORT ?= 3000
DOCKER_COMPOSE ?= docker-compose

.PHONY: help infra infra-down infra-logs backend frontend backend-check frontend-check health proxy-check

help:
	@printf '%s\n' \
		'Loop development commands:' \
		'  make infra           Start Postgres and Redis from docker-compose.infra.yml' \
		'  make infra-down      Stop Postgres and Redis containers' \
		'  make infra-logs      Tail Postgres and Redis logs' \
		'  make backend         Start FastAPI Gunicorn workers on 127.0.0.1:8001 via conda env Loop' \
		'                       Override worker count with BACKEND_WORKERS=4' \
		'  make frontend        Start Next.js on 127.0.0.1:3000' \
		'  make backend-check   Compile backend Python modules' \
		'  make frontend-check  Run TypeScript check' \
		'  make health          Check FastAPI health endpoint' \
		'  make proxy-check     Check Next.js /api proxy with a register probe'

infra:
	@test -f "$(ENV_FILE)" || (printf '%s\n' '.env is missing. Create it from .env.example first.' >&2; exit 1)
	@grep -Eq '^POSTGRES_USER=.+$$' "$(ENV_FILE)" || (printf '%s\n' 'POSTGRES_USER is missing or empty in .env.' >&2; exit 1)
	@grep -Eq '^POSTGRES_PASSWORD=.+$$' "$(ENV_FILE)" || (printf '%s\n' 'POSTGRES_PASSWORD is missing or empty in .env.' >&2; exit 1)
	@grep -Eq '^POSTGRES_DB=.+$$' "$(ENV_FILE)" || (printf '%s\n' 'POSTGRES_DB is missing or empty in .env.' >&2; exit 1)
	@grep -Eq '^LOOP_REDIS_PASSWORD=.+$$' "$(ENV_FILE)" || (printf '%s\n' 'LOOP_REDIS_PASSWORD is missing or empty in .env.' >&2; exit 1)
	cd "$(ROOT_DIR)" && "$(DOCKER_COMPOSE)" --env-file "$(ENV_FILE)" -f "$(INFRA_COMPOSE_FILE)" up -d

infra-down:
	@test -f "$(ENV_FILE)" || (printf '%s\n' '.env is missing. Create it from .env.example first.' >&2; exit 1)
	cd "$(ROOT_DIR)" && "$(DOCKER_COMPOSE)" --env-file "$(ENV_FILE)" -f "$(INFRA_COMPOSE_FILE)" down

infra-logs:
	@test -f "$(ENV_FILE)" || (printf '%s\n' '.env is missing. Create it from .env.example first.' >&2; exit 1)
	cd "$(ROOT_DIR)" && "$(DOCKER_COMPOSE)" --env-file "$(ENV_FILE)" -f "$(INFRA_COMPOSE_FILE)" logs -f

backend:
	@test -f "$(ENV_FILE)" || (printf '%s\n' '.env is missing. Create it from .env.example first.' >&2; exit 1)
	@if ! grep -Eq '^POSTGRES_URL=.+$$' "$(ENV_FILE)" && \
		! (grep -Eq '^POSTGRES_USER=.+$$' "$(ENV_FILE)" && \
		   grep -Eq '^POSTGRES_PASSWORD=.+$$' "$(ENV_FILE)" && \
		   grep -Eq '^POSTGRES_DB=.+$$' "$(ENV_FILE)"); then \
		printf '%s\n' 'PostgreSQL config is incomplete in .env. Set POSTGRES_URL or POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB.' >&2; \
		exit 1; \
	fi
	cd "$(ROOT_DIR)" && PYTHONPATH="$(BACKEND_DIR)" conda run --no-capture-output -n "$(CONDA_ENV)" gunicorn backend.app.main:app -w "$(BACKEND_WORKERS)" -k uvicorn.workers.UvicornWorker --bind "$(BACKEND_HOST):$(BACKEND_PORT)" --timeout 120

frontend:
	cd "$(FRONTEND_DIR)" && npm run dev -- --hostname "$(FRONTEND_HOST)" --port "$(FRONTEND_PORT)"

backend-check:
	cd "$(BACKEND_DIR)" && conda run --no-capture-output -n "$(CONDA_ENV)" python -m compileall app

frontend-check:
	cd "$(FRONTEND_DIR)" && node node_modules/typescript/bin/tsc --noEmit

health:
	curl -i "http://$(BACKEND_HOST):$(BACKEND_PORT)/health"

proxy-check:
	curl -i -X POST "http://$(FRONTEND_HOST):$(FRONTEND_PORT)/api/users/register" \
		-H "Content-Type: application/json" \
		-d '{"username":"proxy_probe_'"$$(date +%s)"'","password":"password123"}'
