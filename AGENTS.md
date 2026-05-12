# Loop Project Context for Codex

## Project Summary

Loop is a computational social science research prototype for a large-model
multi-agent "parallel society" experiment.

The platform lets lab participants register or log in, submit personality/value
questionnaires and a digital autobiography, generate a virtual Agent, view Agent
posts in a branch-aware plaza feed, correct posts that do not match themselves,
chat privately with their Agent, upload/search memories, import group-chat
history, trigger sleep-style consolidation, inspect relationship/memory state,
fork timelines into counterfactual branches, and export JSONL research data.

For a more detailed Chinese handoff document, see `AGENTS.zh-CN.md`.

## Repository Layout

```text
/mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop
  backend/
    app/
      main.py
      database.py
      models.py
      schemas/
      crud/
      routers/
        admin.py
        users.py
        posts.py
        chat.py
        memory.py
        simulate.py
        simulation.py
        export.py
      services/
        llm_service.py
        rag_service.py
        agent_graph.py
        consolidation_service.py
        core_memory_service.py
        event_store.py
        branching.py
        time_machine.py
        feedback_service.py
    requirements.txt
  frontend/
    next.config.mjs
    src/middleware.ts
    src/components/
      AppProviders.tsx
      NavBar.tsx
      BranchSelector.tsx
      LanguageContext.tsx
      LanguageToggle.tsx
      TimeMachinePanel.tsx
    src/app/page.tsx
    src/app/plaza/page.tsx
    src/app/chat/page.tsx
    src/app/import/page.tsx
    src/app/memory/page.tsx
    src/app/time-machine/page.tsx
    src/app/lab/page.tsx
    src/app/site-login/page.tsx
    src/app/site-login/SiteLoginForm.tsx
    src/app/site-auth/login/route.ts
    src/lib/api.ts
    src/lib/i18n.ts
    src/lib/session.ts
    src/lib/siteAuth.ts
    src/lib/time.ts
    src/locales/dictionary.ts
    package.json
    .env.local.example
  .env
  .gitignore
  Makefile
  AGENTS.md
  AGENTS.zh-CN.md
```

## Hard Safety Rules

- Never commit or print secrets from `.env`.
- Never commit `loop_research.db`; it contains changing research/runtime data.
- Never commit dependency/cache/build folders such as `frontend/node_modules/`, `frontend/.next/`, `frontend/npm-cache/`, or Python `__pycache__/`.
- Never commit `chroma_db/`; it is generated local vector-store state.
- Never batch-delete files or folders. If deletion is needed, delete at most one explicitly named file at a time, and avoid deleting generated data unless the user explicitly asks.
- Do not invoke destructive cleanup flows such as `/api/admin/purge-branch` unless the user explicitly asks for that specific data removal.
- Do not run dependency installs in a global Python environment. Use the existing conda environment named `Loop`.
- Do not bind development servers to `0.0.0.0`; use loopback plus SSH port forwarding.

## Backend

Tech stack:

- Python 3.10+
- FastAPI
- SQLAlchemy ORM
- SQLite
- bcrypt password hashing
- compact signed bearer tokens implemented in `backend/app/security.py`
- in-memory rate limiting, request-size limits, security headers, and trusted-host checks
- OpenAI Python SDK pointed at DeepSeek-compatible API
- python-dotenv
- ChromaDB persistent local vector store
- sentence-transformers BGE embedding/reranker models
- LangGraph/LangChain-inspired Agent memory graph helpers

Run backend:

```bash
make backend
```

Backend docs:

```text
http://localhost:8001/docs
```

Database file:

```text
/mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop/backend/loop_research.db
```

This file is intentionally ignored by Git.

Core SQLAlchemy tables:

- `User`: username, password hash, MBTI, Big Five, Schwartz values
- `User.autobiography`: optional digital autobiography / core life memory used as Agent identity memory
- `User.core_memory`: structured core memory fields used during chat/post generation
- `Agent`: one virtual agent per user
- `Post`: agent-generated plaza posts
- `FeedbackLog`: user corrections, including original text, corrected text, timestamp, and future vector-store linkage
- `ChatLog`: private sync turns with user message, Agent reply, branch id, and second-precision timestamp
- `EventLog`: append-only event store for reconstructing branch timelines
- `Relationship`: directed social-affinity scores between Agents
- `ReflectionEvent`: layered reflection nodes from sleep-style memory consolidation

## Core Mechanisms That Must Be Preserved

- **Agentic Memory / active memory addressing is now core architecture, not optional RAG.** Chat generation can let the Agent actively call tools such as `search_personal_memory`, `edit_core_memory`, `read_plaza_feed`, `get_current_time`, `check_energy_budget`, and `update_internal_state`. The Agent must use `edit_core_memory` when the user reveals durable identity facts, while `search_personal_memory` routes targeted questions into `retrieve_hybrid_memory()` instead of blindly stuffing every memory chunk into the prompt.
- `backend/app/services/llm_service.py` keeps both tool-calling chat and fallback retrieval paths. Its historical chat loader can page older branch-scoped chat turns on demand, so do not replace it with one unbounded "load all history" query.
- `backend/app/services/agent_graph.py` binds `AGENT_TOOLS` into the graph and preserves short-term active messages, emotion, energy, topic state, and core-memory writeback. Treat this as the Agent runtime loop.
- **Frontend pagination anti-blowup is also core architecture.** Plaza, Chat, and TimeMachine load bounded pages with `skip`/`limit`, `hasMore*` flags, and explicit "load more" flows. Keep `PLAZA_PAGE_SIZE`, `CHAT_HISTORY_PAGE_SIZE`, and `EVENT_PAGE_SIZE` style guards; do not regress these pages to fetching all posts, all chat logs, or all events at once.
- Backend list endpoints must continue to enforce bounded `limit` values: `/api/plaza/events`, `/api/posts`, `/api/agents/{agent_id}/chat`, and `/api/agents/{agent_id}/events` are intentionally paginated to protect long-running experiments.

## Core Data Flow

```text
Participant register/login
  -> bearer session in frontend localStorage
  -> questionnaire + autobiography
  -> User.core_memory + Agent creation
  -> branch-aware activity
```

Primary runtime chain:

```text
User/Agent action
  -> FastAPI router validates bearer/admin key and branch_id
  -> SQLAlchemy writes domain row when needed
  -> EventLog append-only event is recorded
  -> branch-aware readers replay or filter EventLog
  -> frontend paginated views render the selected branch
```

Memory and learning chain:

```text
Autobiography / uploads / imported chat / private chat / feedback
  -> ChromaDB episodic chunks + User.core_memory + ChatLog/FeedbackLog
  -> Agentic Memory tools actively retrieve or update the right memory
  -> DeepSeek/tool-calling chat or post generation uses branch state + retrieved memory
  -> sleep consolidation and feedback reflection update higher-level memory/relationships
```

Branching and export chain:

```text
TimeMachine reconstructs state at an EventLog timestamp
  -> fork writes a counterfactual event into a new branch
  -> Plaza, Chat, Memory Lab, and TimeMachine select that branch
  -> Lab exports ChatLog/FeedbackLog JSONL for research
```

Implemented backend API:

```text
GET  /health

POST /api/users/register
POST /api/users/login
GET  /api/users/agent-choices                       [admin key]
POST /api/users/agent-choices/{agent_id}/session    [admin key]
POST /api/users/me/questionnaire
POST /api/users/{user_id}/questionnaire
GET  /api/users/me/agent
GET  /api/users/{user_id}/agent

POST /api/agents/me/posts
POST /api/agents/{agent_id}/posts
GET  /api/posts
GET  /api/plaza/events
POST /api/posts/{post_id}/feedback

POST /api/simulate/user/{username}/post             [admin key]
POST /api/simulate/agent/{agent_id}/post            [admin key]
POST /api/simulate/tick                             [admin key]

POST /api/agents/me/chat
GET  /api/agents/{agent_id}/chat
POST /api/agents/{agent_id}/chat

POST /api/users/me/memory/upload
POST /api/users/{user_id}/memory/upload
POST /api/users/me/memory/search
POST /api/users/{user_id}/memory/search
POST /api/agents/me/sleep
POST /api/agents/{agent_id}/sleep
POST /api/agents/me/import_chat
POST /api/agents/{agent_id}/import_chat
GET  /api/agents/me/memory/state
GET  /api/agents/{agent_id}/memory/state
POST /api/agents/me/memory/clear
POST /api/agents/{agent_id}/memory/clear
GET  /api/agents/me/relationships
GET  /api/agents/{agent_id}/relationships
GET  /api/agents/me/feed-preview
GET  /api/agents/{agent_id}/feed-preview

GET  /api/agents/{agent_id}/events
GET  /api/simulation/agents/{agent_id}/branches
GET  /api/simulation/branches
POST /api/simulation/fork

POST /api/admin/purge-branch                         [admin key, destructive, non-main only]

GET  /api/export/{user_id}/chatlogs                 [admin key]
GET  /api/export/by-username/{username}/chatlogs    [admin key]
GET  /api/export/{user_id}/feedbacks                [admin key]
GET  /api/export/by-username/{username}/feedbacks   [admin key]
```

Important backend conventions:

- All DB routes use `Depends(get_db)`.
- Timestamps use second precision through `utc_now_seconds()`.
- Password hashes are never returned by response schemas.
- Most user/Agent data endpoints require a bearer token from register/login.
- Research-control endpoints use `X-Loop-Admin-Key`, checked against `LOOP_ADMIN_API_KEY`.
- Feedback creation validates that users can only correct posts generated by their own Agent.
- CORS defaults to `http://localhost:3000` and `http://127.0.0.1:3000`, and can be configured with comma-separated `BACKEND_CORS_ORIGINS` in the root `.env`.
- `Base.metadata.create_all()` creates new tables; `ensure_sqlite_schema()` performs lightweight SQLite upgrades such as `users.autobiography`, `users.core_memory`, `chat_logs.branch_id`, and append-only `event_logs` triggers, so the existing `.db` does not need to be deleted.
- `EventLog` is the timeline source of truth for branch-aware plaza, chat history, and time-machine reconstruction.
- `warm_up_rag_models()` runs during FastAPI lifespan unless `LOOP_RAG_PRELOAD=false`; startup can be slow when local BGE models load.
- `/api/admin/purge-branch` removes runtime records for one non-main branch, temporarily drops and restores the `event_logs_no_delete` SQLite trigger, and should be treated as a destructive research-maintenance operation.
- `chroma_db/` stores persistent local memory chunks and is ignored by Git.

## DeepSeek Configuration

Create or update this file:

```text
/mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop/.env
```

Expected format for the current remote server:

```env
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_CHAT_MODEL=deepseek-chat
DEEPSEEK_POST_MODEL=deepseek-chat
DEEPSEEK_THINKING=enabled
DEEPSEEK_CHAT_THINKING=disabled
DEEPSEEK_POST_THINKING=disabled
DEEPSEEK_REASONING_EFFORT=high
DEEPSEEK_CHAT_REASONING_EFFORT=high
DEEPSEEK_POST_REASONING_EFFORT=high
LOOP_CHAT_ENGINE=tool_calling
LOOP_LLM_TIMEOUT_SECONDS=8
LOOP_POST_LLM_TIMEOUT_SECONDS=20
LOOP_CHAT_LLM_TIMEOUT_SECONDS=25
LOOP_DEEP_CHAT_LLM_TIMEOUT_SECONDS=60
LOOP_CHAT_MAX_TOKENS=900
LOOP_DEEP_CHAT_MAX_TOKENS=1800
LOOP_POST_MAX_TOKENS=360
LOOP_VECTOR_RAG_ENABLED=true
LOOP_RERANKER_ENABLED=true
LOOP_RAG_PRELOAD=true
LOOP_RAG_STRICT=true
LOOP_EMBEDDING_DEVICE=cuda:0
LOOP_RERANKER_DEVICE=cuda:1
LOOP_CORE_MEMORY_INTENT_LLM_ENABLED=true
LOOP_TOPIC_ROUTER_LLM_ENABLED=true
LOOP_ADMIN_API_KEY=choose_a_private_admin_key
LOOP_AUTH_SECRET=choose_a_stable_token_signing_secret
LOOP_ACCESS_TOKEN_TTL_SECONDS=86400
LOOP_MAX_REQUEST_BYTES=524288
LOOP_RATE_LIMIT_REQUESTS=120
LOOP_RATE_LIMIT_WINDOW_SECONDS=60
LOOP_TRUST_X_FORWARDED_FOR=false
BACKEND_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
LOOP_ALLOWED_HOSTS=localhost,127.0.0.1
BASIC_AUTH_USER=site_username
BASIC_AUTH_PASSWORD=site_password
BASIC_AUTH_COOKIE_SECRET=choose_a_site_cookie_secret
BASIC_AUTH_SESSION_SECONDS=43200
```

`backend/app/services/llm_service.py` and `backend/app/main.py` load the root `.env` with `python-dotenv`.

DeepSeek integration details:

- Uses `OpenAI` client from the OpenAI Python SDK.
- Uses `base_url="https://api.deepseek.com"`.
- Uses standard `client.chat.completions.create(...)`.
- Extracts generated text from `response.choices[0].message.content`.
- Defaults to `deepseek-v4-pro` for deep chat / thinking paths, `deepseek-chat` for fast chat, and `DEEPSEEK_POST_MODEL` for simulated posts unless overridden by env vars.
- Chat UI model choices map to backend modes: `fast` uses `DEEPSEEK_CHAT_MODEL`; `deep` uses `DEEPSEEK_MODEL`.
- Simulated post generation treats missing/failing DeepSeek as an error (`LLMPostGenerationError` -> API 500) so failed research runs are visible. Private chat still falls back to a local memory-aware reply when remote generation fails after service fallback.
- Agent post generation and private chat replies both inject the user's identity data. If `autobiography` exists, it is treated as core memory / life background.
- Memory consolidation and feedback reflection also use DeepSeek where configured, with safe fallbacks or errors depending on path.

## Frontend

Tech stack:

- Next.js 14 App Router
- React 18 client components
- TypeScript 5
- Tailwind CSS 3
- Next.js middleware and route handlers for site-level access control
- Client-side Chinese/English UI dictionary with `LanguageProvider`, `LanguageToggle`, and `localStorage` key `loop_ui_language`
- Browser `localStorage` for the research participant session token and active Agent metadata

Run frontend:

```bash
make frontend
```

Frontend URL through SSH tunnel:

```text
http://localhost:3000
```

Never bind Loop development services to `0.0.0.0`. From your personal computer,
create an SSH tunnel to the remote server instead:

```bash
ssh -L 3000:127.0.0.1:3000 -L 8001:127.0.0.1:8001 zhr@服务器的IP
```

This project currently runs FastAPI on `8001`. If you intentionally move the
backend to `8000`, update both the backend command and the second tunnel mapping
to use `8000`.

If Docker is introduced later, publish ports to loopback only, for example
`127.0.0.1:3000:3000` and `127.0.0.1:8001:8001`. Do not use Docker's
host-all-interfaces shorthand.

Frontend API configuration:

```bash
cd /mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop/frontend
cp .env.local.example .env.local
```

Expected `frontend/.env.local` for the current remote server:

```env
NEXT_PUBLIC_API_BASE_URL=
BACKEND_INTERNAL_API_BASE_URL=http://127.0.0.1:8001
```

Frontend API requests should normally use the same-origin Next.js proxy:

- Browser calls `/api/...` on the frontend origin.
- `frontend/next.config.mjs` rewrites `/api/:path*` to `BACKEND_INTERNAL_API_BASE_URL`.
- FastAPI stays on `127.0.0.1:8001` from Next.js's point of view, so the browser does not need direct access to port `8001`.
- The current rewrite does not cover `/health`; Lab's health button uses `apiRequest("/health")`, so in same-origin proxy mode it needs either `NEXT_PUBLIC_API_BASE_URL` pointed at the backend or a future `/health` rewrite.

Restart the Next.js dev server after changing `frontend/.env.local` or `frontend/next.config.mjs`; `NEXT_PUBLIC_*` variables and rewrites are bundled at dev-server startup/build time.

Implemented pages:

- `/`: participant registration/login, session continuation, questionnaire/autobiography onboarding, and admin-key Agent session switching.
- `/plaza`: branch-aware public plaza feed, manual Agent post composer, correction modal, and current-user Agent ownership checks.
- `/chat`: private user-Agent chat with branch selection, chat history reload, and `fast`/`deep` model choice.
- `/import`: client-side JSON group-chat import parser, sender-to-Agent mapping UI, and import submission.
- `/memory`: memory vault/lab for long-form memory upload, semantic search, sleep consolidation, working-memory diagnostics, relationships, and personalized feed preview.
- `/time-machine`: event timeline viewer and counterfactual branch/fork console.
- `/lab`: research/admin console for health checks, simulation posts/ticks, Agent switching, branch selection, JSONL exports, and destructive non-main branch purge.
- `/site-login`: site-level access gate shown by Next.js middleware before the app is usable.

Frontend MVP behavior:

- Registration/login stores `user_id`, `username`, bearer `access_token`, token expiry, and later `agent_id`/`agent_name` in `localStorage` under `loop_session`.
- If a user refreshes after authentication but before questionnaire submission, `/` restores the saved session and lets the user continue.
- Questionnaire submission sends MBTI, Big Five, Schwartz values, and `autobiography`, then stores `agent_id` and `agent_name`.
- `frontend/src/lib/api.ts` attaches `Authorization: Bearer <token>` to API calls.
- Plaza loads branch-aware posts from `GET /api/plaza/events?branch_id=...`.
- Plaza fetches posts one page at a time from `GET /api/plaza/events?branch_id=...&skip=...&limit=...`, appends unique posts, and only shows "Load more" while the last page is full.
- Plaza can publish authenticated Agent posts through `POST /api/agents/me/posts`.
- Posts from the current user's Agent show a correction button.
- Corrections are sent to `POST /api/posts/{post_id}/feedback`.
- Chat page calls paginated `GET /api/agents/{agent_id}/chat?branch_id=...&skip=...&limit=...` for history and `POST /api/agents/{agent_id}/chat` for new turns.
- Memory and chat pages use `BranchSelector` so a user can inspect or operate on `main` and forked branches.
- `AppProviders` wraps the UI in `LanguageProvider`; page copy is loaded from `frontend/src/locales/dictionary.ts` and persisted as `loop_ui_language`.
- Site access middleware requires `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD`; successful login sets an HTTP-only `loop_site_auth` cookie.

Frontend UI notes:

- `/plaza` uses a centered `max-w-2xl` feed on a light gray background.
- Post cards use white backgrounds, rounded-xl corners, subtle borders/shadows, and an Agent initial avatar.
- The correction UI is a modal-style overlay with a styled textarea and primary/secondary action buttons.
- Backend post timestamps are UTC but currently arrive without a timezone suffix. Frontend code treats timezone-less timestamps as UTC by appending `Z`, then displays local time.
- Plaza time display uses relative text under 1 hour, such as `x min ago`, and `MM-DD HH:mm` local time afterward.
- The shared nav is in `frontend/src/components/NavBar.tsx`; it is hidden on `/site-login`.
- Shared timestamp helpers live in `frontend/src/lib/time.ts`.

## End-to-End Test Flow

1. Start backend with `make backend`.
2. Start frontend with `make frontend`.
3. From your personal computer, open `http://localhost:3000` through the SSH tunnel.
4. Pass `/site-login` if site-level auth env vars are configured.
5. Register or log in as a participant.
6. Submit MBTI, Big Five, Schwartz values, and a digital autobiography / identity-core memory.
7. Confirm redirect to `/plaza`.
8. In `/lab` or backend docs, run `POST /api/simulate/tick` with `X-Loop-Admin-Key`.
9. Refresh `/plaza` and confirm generated posts appear.
10. Correct a post from the current user's Agent.
11. Open `/chat`, send a nightly sync message, and confirm the Agent replies.
12. Open `/memory`, upload/search memory, trigger sleep consolidation, and refresh diagnostics.
13. Open `/time-machine`, load events, fork a branch from an event, and verify branch selectors include the new branch.
14. In `/lab`, export chatlogs or feedbacks as JSONL with the admin key.

If `DEEPSEEK_API_KEY` is missing, `/api/simulate/*` post generation should fail visibly with a server error; chat paths should still return a local fallback reply.

## Remote Registration Troubleshooting

If frontend registration fails on the remote server, check these in order:

1. Backend health:

```bash
make health
```

Expected response: `200 OK` with `{"status":"ok","service":"loop-research-api"}`.

2. Frontend API target:

```bash
cat frontend/.env.local
```

Expected value for the current server:

```env
NEXT_PUBLIC_API_BASE_URL=
BACKEND_INTERNAL_API_BASE_URL=http://127.0.0.1:8001
```

Restart `npm run dev` after changing this file. Keep `NEXT_PUBLIC_API_BASE_URL` empty so the browser calls the tunneled frontend origin, not FastAPI directly.

3. Backend CORS origin:

```bash
grep '^BACKEND_CORS_ORIGINS=' .env
```

The value must match the browser address origin exactly. For SSH tunnel access,
use `http://localhost:3000,http://127.0.0.1:3000` and restart FastAPI.

4. CORS preflight:

```bash
curl -i -X OPTIONS http://127.0.0.1:8001/api/users/register \
  -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type"
```

Expected response: `200 OK` with `access-control-allow-origin` matching the frontend origin.

5. Next.js same-origin proxy:

```bash
make proxy-check
```

Expected response: `201 Created`. This verifies the frontend server can proxy `/api` requests to FastAPI.

6. Port exposure:

Do not expose `3000` or `8001` publicly. Keep both services bound to
`127.0.0.1` on the server and access them only with SSH local forwarding.

Frontend validation command:

```bash
make frontend-check
```

Backend import check:

```bash
make backend-check
```

## Git Notes

Remote:

```text
origin https://github.com/zhouhr251010/Loop.git
```

The initial MVP snapshot commit was:

```text
e409bdc feat: MVP Step 5 backend/frontend architecture with DeepSeek integration
```

Before committing, always check:

```bash
git status --short --ignored
git diff --cached --name-only
```

Ensure ignored/sensitive files are not staged.

## Known Environment Notes

- The current working path is Linux remote server path `/mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop`.
- Port `8000` is occupied on the server; run FastAPI on `8001`.
- Remote dev servers must bind to `127.0.0.1`, never `0.0.0.0`.
- Browser `127.0.0.1` means the user's local computer. Use SSH local forwarding from the personal computer, keep `NEXT_PUBLIC_API_BASE_URL` empty for same-origin frontend calls, and let Next.js proxy `/api/*` to `BACKEND_INTERNAL_API_BASE_URL`.
- `frontend/.env.local` is intentionally ignored by Git; commit only `frontend/.env.local.example`.
- The root `.env` is intentionally ignored by Git and may contain DeepSeek, admin, token, CORS, host, site-auth, and RAG settings.
- `backend/loop_research.db` and `chroma_db/` are local research/runtime state and must stay untracked.
- In Codex's sandbox, listening sockets may fail with `PermissionError: [Errno 1] Operation not permitted`; running the same server command normally on the remote shell works.
- In Codex's sandbox, GitHub network access may fail. The local repo already has `origin` configured; pushing from a normal shell should work.

## Preferred Next Steps

Likely future work:

- Add tests for auth, route permissions, branch isolation, exports, and memory flows.
- Add Alembic migrations before schema changes become frequent.
- Extract plaza feed cards, correction modal, chat bubbles, and diagnostics panels if UI grows.
- Add researcher dashboard views for feedback logs, chat logs, and branch comparison.
- Add stronger site deployment docs for reverse proxy / HTTPS / cookie security.
- Consider moving rate limits and sessions to persistent storage if the service becomes multi-process.
- Add prompt/version metadata for reproducible research experiments.
