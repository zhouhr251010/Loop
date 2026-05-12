SHELL := /bin/bash

ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
BACKEND_DIR := $(ROOT_DIR)/backend
FRONTEND_DIR := $(ROOT_DIR)/frontend
CONDA_ENV ?= Loop
BACKEND_HOST ?= 127.0.0.1
BACKEND_PORT ?= 8001
FRONTEND_HOST ?= 127.0.0.1
FRONTEND_PORT ?= 3000

.PHONY: help backend frontend backend-check frontend-check health proxy-check

help:
	@printf '%s\n' \
		'Loop development commands:' \
		'  make backend         Start FastAPI on 127.0.0.1:8001 via conda env Loop' \
		'  make frontend        Start Next.js on 127.0.0.1:3000' \
		'  make backend-check   Compile backend Python modules' \
		'  make frontend-check  Run TypeScript check' \
		'  make health          Check FastAPI health endpoint' \
		'  make proxy-check     Check Next.js /api proxy with a register probe'

backend:
	cd "$(BACKEND_DIR)" && conda run --no-capture-output -n "$(CONDA_ENV)" uvicorn app.main:app --host "$(BACKEND_HOST)" --port "$(BACKEND_PORT)" --reload

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
