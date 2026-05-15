# Loop Project Context for Codex


## Project Summary

Loop is a computational social science research prototype for a large-model
multi-agent "parallel society" experiment.

The platform lets lab participants register or log in, submit personality/value
questionnaires and a digital autobiography, generate a virtual Agent, view Agent
posts in a branch-aware plaza feed, correct posts that do not match themselves,
chat privately with their Agent across independent sessions and experiment
modes, upload/search memories, import group-chat history, trigger sleep-style
consolidation, inspect relationship/memory state, fork timelines into
counterfactual branches, collect blind-test authenticity ratings, and export
JSONL research data.

For a more detailed Chinese handoff document, see `AGENTS.zh-CN.md`.

## Fast Orientation

If you only remember the highest-signal things before touching this codebase, remember these:

- Loop is an event-sourced, branch-aware social simulation. `EventLog` is the timeline truth for branch views, while session-level chat history is stored and paginated from `ChatLog`.
- Each `User` owns exactly one `Agent`; questionnaire submission creates or refreshes that one-to-one mapping.
- Durable identity currently lives in two places: free-form `autobiography` and structured `core_memory`.
- `core_memory` is not just three loose fields anymore. The normalized keys are `persona_traits`, `key_relationships`, `current_goals`, and `communication_style`.
- Chat is no longer "prompt + RAG only". `mode_alpha` uses the full IACL path with active memory/tools, while `mode_beta` is a static-prompt baseline for blind comparison.
- M1-M6 validation data now also includes weekly probe responses and life-decision counterfactual anchors. Keep `/api/probes/*`, `/api/counterfactuals/*`, and `ProbeResponse` aligned with the research flow.
- Chat turns are isolated by `branch_id`, `session_id`, and `topic`; do not accidentally merge independent conversations when loading history, routing context, or drift checks.
- Branch behavior depends on `TimeMachine` reconstruction plus branch-specific `EventLog` replay. Do not fake branches by simply filtering `Post` rows.
- User corrections do not rewrite `posts.content` in place. Branch feed projection overlays the newest `FEEDBACK_CREATED` correction when rendering.
- Long lists are intentionally bounded on both sides. Plaza, chat history, and event history must stay paginated.
- The frontend normally talks to Next.js on `localhost:3000`, and Next.js rewrites `/api/*` to FastAPI on `127.0.0.1:8001`. `/health` is the current exception.
- Local runtime state matters: `backend/loop_research.db` and `chroma_db/` are part of the experiment environment and must not be committed or casually deleted.

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
        probes.py
        counterfactuals.py
        chat.py
        evaluations.py
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
        drift_detector.py
        scoring_service.py
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
    src/app/probes/page.tsx
    src/app/counterfactuals/page.tsx
    src/app/evaluations/[agent_id]/page.tsx
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
    src/data/questionnaires.json
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
- `User.core_memory`: structured core memory used during chat/post generation; normalized keys are `persona_traits`, `key_relationships`, `current_goals`, and `communication_style`
- `Agent`: one virtual agent per user
- `Post`: agent-generated plaza posts
- `FeedbackLog`: user corrections, including original text, corrected text, timestamp, and future vector-store linkage
- `ChatLog`: private sync turns with user message, Agent reply, branch id, session id, topic, experiment mode, and second-precision timestamp
- `Evaluation`: public blind-test authenticity rating with evaluator relation, 1-5 score, qualitative feedback, sampled chat log ids, and timestamp
- `ProbeResponse`: authenticated M1-M6 validation probe answers, including weekly IPIP-120/PVQ-21 human baselines
- `EventLog`: append-only event store for reconstructing branch timelines
- `Relationship`: directed social-affinity scores between Agents
- `ReflectionEvent`: layered reflection nodes from sleep-style memory consolidation

## Core Mechanisms That Must Be Preserved

- **Agentic Memory / active memory addressing is now core architecture, not optional RAG.** Chat generation can let the Agent actively call tools such as `search_personal_memory`, `edit_core_memory`, `read_plaza_feed`, `get_current_time`, `check_energy_budget`, and `update_internal_state`. The Agent must use `edit_core_memory` when the user reveals durable identity facts, while `search_personal_memory` routes targeted questions into `retrieve_hybrid_memory()` instead of blindly stuffing every memory chunk into the prompt.
- `backend/app/services/llm_service.py` keeps both tool-calling chat and fallback retrieval paths. Its historical chat loader can page older branch/session-scoped chat turns on demand, so do not replace it with one unbounded "load all history" query.
- `backend/app/services/agent_graph.py` binds `AGENT_TOOLS` into the graph and preserves short-term active messages, emotion, energy, topic state, and core-memory writeback. Treat this as the Agent runtime loop.
- The chat page supports research experiment modes. `mode_alpha` is the full IACL condition; `mode_beta` calls `chat_with_agent_static_prompt()` with tools, RAG, self-updating memory, and prior history disabled.
- `/api/probes/submit` stores IPIP/PVQ validation answers, scores them through `scoring_service.py`, merges the scored profile into `User.core_memory`, and refreshes the Agent profile when needed.
- `/api/counterfactuals/suggestions` mines autobiography plus bounded recent chats/posts to suggest candidate life-decision anchors before submission.
- `/api/counterfactuals/submit` records life-decision counterfactual anchors, appends `COUNTERFACTUAL_ANCHOR_CREATED`, and writes the anchor into `persona_traits` via a `CORE_MEMORY_UPDATED` event.
- Zero-shot identity drift detection is part of the M1/M2 validation loop. `/api/chat/{agent_id}/check-drift` judges recent session replies against the identity core and appends `DRIFT_DETECTED` only when the judge flags drift.
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

Evaluation chain:

```text
Researcher shares /evaluations/{agent_id}
  -> public evaluator reads a small random sample of ChatLog turns
  -> evaluator submits relation, 1-5 authenticity score, and optional feedback
  -> Evaluation row stores M6 friend-Turing-test evidence
```

Implemented backend API:

```text
GET  /health

POST /api/users/register
POST /api/users/login
GET  /api/users/me
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
GET  /api/chat/{agent_id}/sessions
POST /api/chat/{agent_id}/check-drift

GET  /api/evaluations/blind-test/{agent_id}
POST /api/evaluations/blind-test/{agent_id}/submit

GET  /api/probes/status
POST /api/probes/submit
GET  /api/counterfactuals/suggestions
POST /api/counterfactuals/submit

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
- `Base.metadata.create_all()` creates new tables; `ensure_sqlite_schema()` performs lightweight SQLite upgrades such as `users.autobiography`, `users.core_memory`, `chat_logs.branch_id`, `chat_logs.session_id`, `chat_logs.experiment_mode`, and append-only `event_logs` triggers, so the existing `.db` does not need to be deleted.
- `EventLog` is the timeline source of truth for branch-aware plaza and time-machine reconstruction. Session-level chat history currently reads bounded `ChatLog` pages filtered by `branch_id` and `session_id`.
- Questionnaire and probe scoring live in `backend/app/services/scoring_service.py`; raw IPIP/PVQ item payloads are converted into scored Big Five/Schwartz summaries before being merged into core memory.
- `warm_up_rag_models()` runs during FastAPI lifespan unless `LOOP_RAG_PRELOAD=false`; startup can be slow when local BGE models load.
- `/api/admin/purge-branch` removes runtime records for one non-main branch, temporarily drops and restores the `event_logs_no_delete` SQLite trigger, and should be treated as a destructive research-maintenance operation.
- `chroma_db/` stores persistent local memory chunks and is ignored by Git.

## Backend Reality Checks

These are current implementation facts that are easy to miss when skimming the repo:

- `core_memory_service.py` normalizes `User.core_memory` to four fields, not three. Older data may be missing `communication_style`, so always normalize before use.
- `create_or_update_agent_for_user()` updates `Agent.system_prompt_base` in place and emits `AGENT_PROFILE_UPDATED`; agent creation is not a one-time bootstrap only.
- `post_crud.create_post()` and `feedback_crud.create_feedback_log()` always append matching immutable `EventLog` records. Branch feeds are reconstructed from those events.
- Plaza correction behavior is projection-based: the latest `FEEDBACK_CREATED` event for a post wins for display in a given branch, while the original `Post` row remains unchanged.
- `chat_crud.create_chat_log()` also appends a `MESSAGE_RECEIVED` event containing `session_id`, `topic`, and `experiment_mode`. Chat history pages read bounded `ChatLog` slices filtered by branch, session, and topic.
- `/api/chat/{agent_id}/sessions` groups `ChatLog` rows by `session_id` within a branch and returns latest-session summaries for the chat sidebar.
- `DRIFT_DETECTED` events are appended by the drift-check endpoint only after `evaluate_drift_zero_shot()` returns `is_drifting=true`; skipped or unavailable judges do not block chat storage.
- `/api/evaluations/blind-test/{agent_id}` is public by design for external raters and returns up to 5 random chat samples; the submit endpoint writes `Evaluation` rows without requiring participant auth.
- `/api/probes/status` checks whether the authenticated user needs this week's IPIP-120 baseline update, and `/api/probes/submit` bulk-stores `ProbeResponse` rows before refreshing scored personality/value summaries.
- `/api/counterfactuals/suggestions` uses autobiography plus bounded recent chat/post text to propose decision anchors; it returns `[]` cleanly when there is not enough source material yet.
- `/api/counterfactuals/submit` is an authenticated identity-memory collection path, not the same as TimeMachine branch forking. It appends a durable counterfactual anchor to `persona_traits`.
- `TimeMachine` intentionally does not replay raw chat transcripts into the prompt state. It rebuilds compact state such as normalized core memory, counterfactual overrides, intimacy, and a short `current_core_memory` string.
- `GET /api/agents/{agent_id}/events` is currently only existence-checked and paginated; the router does not enforce bearer ownership or admin auth. Treat it as an internal research endpoint until hardened.
- `POST /api/simulation/fork` now accepts `source_branch_id` plus optional `source_event_id`, validates that the chosen event belongs to the selected branch lineage, reconstructs from that source branch, and stores `from_branch_id` / `parent_event_id` in the fork payload for ancestry tracing.
- `POST /api/agents/{agent_id}/import_chat` still stores target-agent-perspective memory in Chroma with `branch_id="main"` today, but it now also accepts an optional batch-level `topic` tag for retrieval metadata.
- User-facing memory upload/search endpoints also do not expose branch parameters today; most vector-memory tooling is effectively main-world-line scoped, while branch divergence mainly comes from `EventLog` + `TimeMachine`.
- Relationship-aware feed logic already exists in two places: `post_crud.get_posts_for_viewer()` and `/api/agents/*/feed-preview`. Be careful not to regress these to pure reverse-chronological order everywhere.

## Backend Service Map

Use this section when you need to locate the "real" owner of a behavior quickly:

- `backend/app/main.py`: FastAPI app creation, middleware stack, router mounting, `.env` load, table creation, and optional RAG warmup during lifespan.
- `backend/app/security.py`: compact signed bearer tokens, admin-key dependency, request-size limit, in-memory rate limit, security headers, and trusted-host enforcement.
- `backend/app/database.py`: SQLAlchemy engine/session plus lightweight SQLite upgrade logic and append-only triggers for `event_logs`.
- `backend/app/models.py`: the research data model, all second-precision timestamps, and the append-only event entity.
- `backend/app/services/event_store.py`: one sanctioned way to append immutable timeline events with JSON-safe payloads and logging.
- `backend/app/services/branching.py`: branch id normalization, branch existence, global branch listing, parent-lineage lookup, and fork anchor reconstruction.
- `backend/app/services/time_machine.py`: branch-aware event replay into compact agent state. This is the heart of counterfactual reconstruction.
- `backend/app/services/core_memory_service.py`: normalization, prompt formatting, explicit core-memory edits, and reflection-insight mergeback.
- `backend/app/services/tools.py`: the tool layer exposed to chat agents. If the Agent should "sense" or "act", it likely belongs here.
- `backend/app/services/agent_graph.py`: LangGraph runtime loop, working-memory topics, summaries, emotion/energy state, and tool binding.
- `backend/app/services/llm_service.py`: DeepSeek request settings, post generation, chat generation, tool-calling orchestration, fallback reply path, and historical chat loader contract.
- `backend/app/services/drift_detector.py`: zero-shot identity-consistency judge for recent Agent replies, with bounded prompt context and safe skip behavior when DeepSeek is unavailable.
- `backend/app/services/rag_service.py`: Chroma persistence, BGE embedding/reranking, memory chunking, hybrid retrieval, preload, and strict/fallback behavior.
- `backend/app/services/scoring_service.py`: IPIP-NEO-120 and PVQ-21 scoring, legacy aggregate preservation, and questionnaire-score mergeback into core memory.
- `backend/app/services/consolidation_service.py`: 24-hour record collection, offline sleep-style consolidation, relationship updates, scored episodic memory creation, and working-memory clearing logic.
- `backend/app/services/feedback_service.py`: post-correction reflection merge path after user feedback.

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
LOOP_DRIFT_JUDGE_MODEL=deepseek-chat
LOOP_DRIFT_JUDGE_TIMEOUT_SECONDS=12
LOOP_DRIFT_JUDGE_MAX_TOKENS=360
LOOP_DRIFT_JUDGE_THINKING=disabled
LOOP_DRIFT_JUDGE_REASONING_EFFORT=high
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
- Chat experiment modes map to backend generation paths: `mode_alpha` uses the full active-memory/IACL path; `mode_beta` uses the static prompt baseline. Older aliases `full_iacl` and `static_prompt` normalize to those neutral labels.
- Simulated post generation treats missing/failing DeepSeek as an error (`LLMPostGenerationError` -> API 500) so failed research runs are visible. Private chat still falls back to a local memory-aware reply when remote generation fails after service fallback.
- Drift judging uses the DeepSeek-compatible client when `DEEPSEEK_API_KEY` exists. If the judge is unavailable, chat continues and the UI shows a non-blocking skip notice.
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
- `/chat`: private user-Agent chat with branch selection, independent sessions, `fast`/`deep` model choice, `mode_alpha`/`mode_beta` experiment modes, and drift calibration.
- The shared nav in `frontend/src/components/NavBar.tsx` now exposes all major app surfaces. Desktop groups daily interaction vs. experiment/admin links, while mobile collapses them into a hamburger menu.
- `/probes`: authenticated IPIP-120/PVQ-21 probe form for weekly M1-M6 baseline updates.
- `/counterfactuals`: authenticated life-decision counterfactual anchor form with AI suggestion cards and actual-choice / actual-result fields that writes durable identity memory.
- `/evaluations/[agent_id]`: public blind-test page that shows sampled chat snippets and records friend/colleague/family authenticity ratings.
- `/import`: client-side JSON / TXT / HTML group-chat import parser, sender-to-Agent mapping UI, date filters, topic tagging, and import submission.
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
- Chat page calls `GET /api/chat/{agent_id}/sessions?branch_id=...` for the sidebar, paginated `GET /api/agents/{agent_id}/chat?branch_id=...&session_id=...&skip=...&limit=...` for history, and `POST /api/agents/{agent_id}/chat` for new turns.
- Chat sends `session_id`, `topic`, and `experiment_mode`; `mode_alpha` replies trigger a drift check and may open a calibration modal when recent replies drift from core identity.
- Probes load `frontend/src/data/questionnaires.json` and submit authenticated weekly validation answers to `/api/probes/submit`.
- Counterfactuals page can prefill from `GET /api/counterfactuals/suggestions`, then submits authenticated life-decision alternatives to `/api/counterfactuals/submit`, updating durable identity memory.
- Memory and chat pages use `BranchSelector` so a user can inspect or operate on `main` and forked branches.
- `AppProviders` wraps the UI in `LanguageProvider`; page copy is loaded from `frontend/src/locales/dictionary.ts` and persisted as `loop_ui_language`.
- Site access middleware requires `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD`; successful login sets an HTTP-only `loop_site_auth` cookie.
- Site middleware intentionally leaves `/evaluations/*` and `/api/evaluations/*` public so external blind-test raters can access shared evaluation links.

Frontend UI notes:

- `/plaza` uses a centered `max-w-2xl` feed on a light gray background.
- Post cards use white backgrounds, rounded-xl corners, subtle borders/shadows, and an Agent initial avatar.
- The correction UI is a modal-style overlay with a styled textarea and primary/secondary action buttons.
- Backend post timestamps are UTC but currently arrive without a timezone suffix. Frontend code treats timezone-less timestamps as UTC by appending `Z`, then displays local time.
- Plaza time display uses relative text under 1 hour, such as `x min ago`, and `MM-DD HH:mm` local time afterward.
- The shared nav is in `frontend/src/components/NavBar.tsx`; the full app nav is hidden on `/site-login`, where only the compact Loop header and language toggle remain. Everywhere else, desktop shows grouped links and mobile uses a hamburger sheet.
- Shared timestamp helpers live in `frontend/src/lib/time.ts`.

## Frontend Page Walkthrough

### `/`

`frontend/src/app/page.tsx` is the participant entry flow.

- Supports register and login.
- Saves `loop_session` to `localStorage` after auth.
- Restores a partially completed session after refresh.
- If the user has no Agent yet, shows the questionnaire/autobiography onboarding step.
- Also exposes admin-key-based Agent session switching for research/testing.

### `/plaza`

`frontend/src/app/plaza/page.tsx` is the public square feed.

- Uses `BranchSelector` to switch the active world-line.
- Loads paginated inherited feed items from `GET /api/plaza/events`.
- Keeps `PLAZA_PAGE_SIZE` and a "load more" workflow to avoid feed blowups.
- Lets the authenticated user post manually through `POST /api/agents/me/posts`.
- Shows a correction button only on posts generated by the current user's Agent.
- Submits corrections to `POST /api/posts/{post_id}/feedback`.

### `/chat`

`frontend/src/app/chat/page.tsx` is the private user-Agent sync UI.

- Loads available branches per agent.
- Fetches bounded branch/session history from `GET /api/agents/{agent_id}/chat`.
- Loads sidebar sessions from `GET /api/chat/{agent_id}/sessions`.
- Keeps `CHAT_HISTORY_PAGE_SIZE` and incremental history loading.
- Sends new turns to `POST /api/agents/{agent_id}/chat`.
- Persists the chosen model mode: `fast` maps to `DEEPSEEK_CHAT_MODEL`, `deep` maps to `DEEPSEEK_MODEL`.
- Persists the chosen experiment mode: `mode_alpha` is full IACL, `mode_beta` is static prompt baseline.
- Persists the chosen topic bucket and lets the user switch among named topic tracks such as `general`, `daily_life`, `relationships`, `work`, and `identity`.
- Runs drift detection after `mode_alpha` replies and sends an explicit calibration instruction when the user confirms a drift correction.

### `/probes`

`frontend/src/app/probes/page.tsx` collects authenticated weekly validation probes.

- Loads IPIP-120 and PVQ-21 items from `frontend/src/data/questionnaires.json`.
- Requires a participant session and redirects unauthenticated users to `/`.
- Submits answers to `POST /api/probes/submit`.
- The backend stores `ProbeResponse` rows, scores Big Five and Schwartz values, merges the scored profile into core memory, and refreshes the Agent profile when an Agent exists.

### `/counterfactuals`

`frontend/src/app/counterfactuals/page.tsx` collects identity-relevant life-decision anchors.

- Requires an authenticated participant session.
- Loads AI anchor suggestions from `GET /api/counterfactuals/suggestions`.
- Collects decision context, optional actual choice / actual result, counterfactual action, and imagined result.
- Submits to `POST /api/counterfactuals/submit`.
- The backend appends `COUNTERFACTUAL_ANCHOR_CREATED` and `CORE_MEMORY_UPDATED`, adding the anchor to `persona_traits`.

### `/evaluations/[agent_id]`

`frontend/src/app/evaluations/[agent_id]/page.tsx` is the public blind-test authenticity-rating page.

- Loads up to 5 random private-chat samples from `GET /api/evaluations/blind-test/{agent_id}`.
- Lets external raters select relation type, score authenticity from 1 to 5, and leave optional qualitative feedback.
- Submits ratings to `POST /api/evaluations/blind-test/{agent_id}/submit`.
- Does not require the site-login cookie because evaluation links are designed to be shared outside the participant app.

### `/import`

`frontend/src/app/import/page.tsx` imports group-chat transcripts from JSON, TXT, or HTML.

- Parses the file client-side, including delimiter-based plain-text logs and HTML exports.
- Collects sender ids and lets the researcher map them to known Agent ids.
- Supports optional start/end date filters and one batch-level topic tag before upload.
- Sends target-agent-perspective import records to `POST /api/agents/me/import_chat`.
- The backend differentiates "my messages" from "others' messages" in stored memory metadata.

### `/memory`

`frontend/src/app/memory/page.tsx` is the memory lab / diagnostics page.

- Uploads long-form memory chunks.
- Runs semantic search against local vector memory.
- Triggers sleep-style consolidation.
- Inspects and clears short-term LangGraph working memory.
- Shows reconstructed core memory, topic summaries, emotion/energy, relationships, and personalized feed preview.

### `/time-machine`

`frontend/src/app/time-machine/page.tsx` and `frontend/src/components/TimeMachinePanel.tsx` power the counterfactual timeline UI.

- Loads events in pages from `GET /api/agents/{agent_id}/events`.
- Keeps `EVENT_PAGE_SIZE` and explicit "load more" behavior.
- Lets the researcher select any currently viewed branch, choose a concrete event node, and submit a counterfactual event.
- Calls `POST /api/simulation/fork` to create a new global branch.
- Fork requests now include `source_branch_id` and `source_event_id`, so nested non-main branch forking preserves lineage metadata.

### `/lab`

`frontend/src/app/lab/page.tsx` is the research/admin console.

- Checks backend health.
- Lists agents and branches.
- Triggers one-off simulated posts or global ticks.
- Exports chatlogs or feedbacks as JSONL.
- Can purge a non-main branch through the destructive admin endpoint.

### `/site-login`

`frontend/src/app/site-login/page.tsx`, `frontend/src/app/site-auth/login/route.ts`, and `frontend/src/middleware.ts` implement site-level access control separate from participant auth.

- Middleware redirects document requests to `/site-login` when the `loop_site_auth` cookie is missing or invalid.
- The login route creates an HMAC-signed session token in an HTTP-only cookie.
- `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD`, `BASIC_AUTH_COOKIE_SECRET`, and `BASIC_AUTH_SESSION_SECONDS` control this outer gate.

## Frontend Shared Runtime State

- `frontend/src/lib/session.ts` stores the participant bearer session in `localStorage` under `loop_session`.
- `frontend/src/lib/api.ts` automatically injects `Authorization: Bearer <token>` when available.
- `frontend/src/components/LanguageContext.tsx` stores UI language in `localStorage` as `loop_ui_language`.
- `frontend/src/lib/siteAuth.ts` signs and verifies the site-access cookie separately from participant auth.
- `frontend/src/lib/time.ts` treats backend timezone-less timestamps as UTC before local display.
- `frontend/src/data/questionnaires.json` contains the IPIP/PVQ probe items shown by `/probes`.
- `frontend/src/locales/dictionary.ts` is the source of truth for Chinese and English UI text; adding new UI strings usually means updating both locales.

## Event Taxonomy Cheat Sheet

These event types matter most when debugging branch behavior:

- `AGENT_CREATED`: initial Agent row was created for one user.
- `AGENT_PROFILE_UPDATED`: questionnaire/core-profile refresh updated the existing Agent prompt base.
- `POST_CREATED`: a plaza post was published and should appear in branch feed projections.
- `FEEDBACK_CREATED`: a user correction was recorded; feed rendering may now prefer corrected text.
- `MESSAGE_RECEIVED`: a private chat turn was stored.
- `DRIFT_DETECTED`: zero-shot judge detected likely identity drift in recent replies for one branch/session.
- `CORE_MEMORY_UPDATED`: durable identity memory changed, either by explicit tool use or sleep consolidation.
- `COUNTERFACTUAL_ANCHOR_CREATED`: a participant submitted a life-decision counterfactual anchor that should inform durable identity memory.
- `RELATIONSHIP_CHANGED`: directed affinity between agents changed.
- `WORKING_MEMORY_CLEARED`: short-term graph memory was manually cleared.
- `COUNTERFACTUAL_EVENT` or a custom injected event type: a branch fork or synthetic intervention modified a non-main world-line.

## Safe Change Checklist

When modifying core behavior, verify these invariants before you call the work done:

- Branch-aware reads still use `normalize_branch_id()` and respect parent lineage or fork anchors.
- New write paths append `EventLog` entries if they affect simulation state or branch reconstruction.
- Chat, plaza, and event history endpoints remain paginated with bounded `limit` values.
- Sensitive values from `.env` never enter logs, responses, screenshots, or commits.
- Dev servers still bind to `127.0.0.1`.
- Any new frontend API path either fits the existing `/api/*` rewrite or is explicitly documented like `/health`.

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
12. In `/chat`, create a new conversation, switch between `mode_alpha` and `mode_beta`, and confirm session history stays isolated.
13. Open `/probes`, submit the current IPIP/PVQ probe set, and confirm the Agent profile still loads.
14. Open `/counterfactuals`, submit one life-decision anchor, and confirm memory diagnostics show the updated core memory.
15. Open `/evaluations/{agent_id}` in a fresh browser context and submit a blind-test rating.
16. Open `/memory`, upload/search memory, trigger sleep consolidation, and refresh diagnostics.
17. Open `/time-machine`, load events, fork a branch from an event, and verify branch selectors include the new branch.
18. In `/lab`, export chatlogs or feedbacks as JSONL with the admin key.

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
