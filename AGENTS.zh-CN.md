# Loop 项目中文接手说明

## 项目定位

Loop 是一个面向计算社会科学实验的研究原型。它把真实参与者的问卷、数字自传、聊天记录、纠错反馈和记忆材料转化为一个虚拟 Agent 的身份与记忆，让多个 Agent 在一个“平行社会”里发帖、聊天、形成关系、产生记忆巩固，并支持研究者导出数据用于后续分析或继续训练。

一句话理解：这是一个“真人画像 -> 虚拟 Agent -> 广场互动 -> 私聊同步 -> 记忆/RAG/时间线 -> 研究数据导出”的实验平台。

## 最高优先级规则

- 不要打印、提交或泄露根目录 `.env` 里的任何密钥。
- 不要提交 `backend/loop_research.db`，这是本地 SQLite 运行数据。
- 不要提交 `chroma_db/`，这是本地向量数据库状态。
- 不要提交 `frontend/node_modules/`、`frontend/.next/`、`frontend/npm-cache/`、`__pycache__/` 等依赖、缓存、构建产物。
- 禁止批量删除文件或目录。确实需要删除时，每次最多删除 1 个明确绝对路径文件；如果需要清理多个文件，先停下来告诉用户。
- 不要主动调用 `/api/admin/purge-branch` 这类破坏性清理接口，除非用户明确要求清理某个分支的数据。
- 后端依赖不要装到全局 Python，使用已有 conda 环境 `Loop`。
- 开发服务必须绑定 `127.0.0.1`，不要绑定 `0.0.0.0`。

## 目录地图

```text
Loop/
  backend/
    app/
      main.py                 FastAPI 入口、中间件、路由挂载、启动时建表
      database.py             SQLAlchemy engine/session、SQLite 轻量升级
      models.py               User/Agent/Post/ChatLog/EventLog/Relationship 等表
      security.py             bearer token、管理员 key、限流、请求大小、安全头
      routers/
        admin.py              高权限维护接口，按非 main 分支清理运行数据
        users.py              注册、登录、问卷、Agent 会话切换
        posts.py              广场发帖、分支 feed、纠错反馈
        chat.py               私聊、聊天历史、RAG/Graph 回复
        memory.py             记忆上传/搜索、睡眠巩固、导入聊天、关系/诊断
        simulate.py           管理员触发 Agent 自动发帖和 tick
        simulation.py         事件时间线、分支列表、时间机器 fork
        export.py             JSONL 研究数据导出
      schemas/                Pydantic 输入输出模型
      crud/                   数据库增删查改封装
      services/
        llm_service.py        DeepSeek/OpenAI-compatible 生成发帖和私聊回复
        rag_service.py        ChromaDB + BGE embedding/reranker 本地记忆检索
        agent_graph.py        Agent 短期工作记忆与话题/状态图
        consolidation_service.py 睡眠式记忆巩固、高层反思、关系更新
        core_memory_service.py Core Memory 规范化和 prompt 格式化
        event_store.py        append-only EventLog 写入
        branching.py          main/分支 id、分支存在性、父分支锚点
        time_machine.py       通过 EventLog 重放重建某分支状态
        feedback_service.py   用户纠错后的反思合并
    requirements.txt
  frontend/
    next.config.mjs           /api/* 同源代理到 FastAPI
    src/middleware.ts         站点级访问验证
    src/components/
      AppProviders.tsx        前端上下文 Provider
      NavBar.tsx              顶部导航
      BranchSelector.tsx      分支选择组件
      LanguageContext.tsx     中英双语 UI 状态
      LanguageToggle.tsx      语言切换按钮
      TimeMachinePanel.tsx    时间机器主 UI
    src/app/
      page.tsx                注册/登录/问卷 onboarding
      plaza/page.tsx          广场 feed、发帖、纠错
      chat/page.tsx           Nightly Sync 私聊
      import/page.tsx         群聊 JSON 导入
      memory/page.tsx         记忆金库/诊断实验台
      time-machine/page.tsx   时间机器页面壳
      lab/page.tsx            研究者控制台
      site-login/page.tsx     站点访问登录页
      site-login/SiteLoginForm.tsx 站点登录表单
      site-auth/login/route.ts 站点登录 route handler
    src/lib/
      api.ts                  fetch 封装、类型定义、bearer token 注入
      i18n.ts                 useUiLanguage 轻量重导出
      session.ts              localStorage 参与者会话
      siteAuth.ts             站点 cookie 签名与校验
      time.ts                 UTC 时间解析和展示
    src/locales/dictionary.ts 中英 UI 文案字典
```

## 后端技术栈

- Python 3.10+
- FastAPI + APIRouter
- SQLAlchemy ORM
- SQLite 本地数据库
- bcrypt 密码哈希
- 自实现 HMAC 签名 bearer token
- 内存限流、请求体大小限制、安全响应头和 Trusted Host 检查
- OpenAI Python SDK，`base_url` 指向 DeepSeek 兼容接口
- python-dotenv 读取根目录 `.env`
- ChromaDB 作为本地持久化向量库
- sentence-transformers，默认 BGE 中文 embedding 和 reranker
- LangGraph/LangChain 相关依赖，用于 Agent 工作记忆和图式流程

## 后端运行

```bash
cd /mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop/backend
conda activate Loop
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

接口文档：

```text
http://localhost:8001/docs
```

数据库文件：

```text
/mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop/backend/loop_research.db
```

## 后端数据模型

- `User`：实验参与者。保存用户名、密码哈希、创建时间、MBTI、Big Five、Schwartz 价值观、数字自传、结构化 core memory。
- `Agent`：每个用户对应一个虚拟 Agent。保存 agent 名字和基础 system prompt。
- `Post`：Agent 在广场里产生的公开帖子。
- `FeedbackLog`：用户对自己 Agent 帖子的纠错记录，是持续学习/后续微调的重要监督信号。
- `ChatLog`：用户和 Agent 的私聊轮次，包含 `branch_id`。
- `EventLog`：append-only 事件时间线，用于时间机器、分支 feed、状态重放。SQLite 里有禁止 update/delete 的 trigger。
- `Relationship`：Agent 到 Agent 的有向亲密度/关系分数。
- `ReflectionEvent`：睡眠巩固时产生的分层反思节点。

## 后端核心机制

- `main.py` 启动时执行 `Base.metadata.create_all()` 和 `ensure_sqlite_schema()`，所以轻量字段升级不需要删除数据库。
- `security.py` 负责 bearer token、管理员 API key、请求大小限制、内存限流、安全响应头和 trusted host。
- 普通用户接口一般要求 `Authorization: Bearer <token>`。
- 研究控制接口一般要求 `X-Loop-Admin-Key`，对应 `.env` 的 `LOOP_ADMIN_API_KEY`。
- `EventLog` 是分支和时间机器的核心：帖子、聊天、反事实事件都会写入事件流。
- `TimeMachine` 根据指定 `agent_id`、`branch_id`、时间点重放事件，重建当前 core memory、工作记忆、关系等状态。
- FastAPI lifespan 会调用 `warm_up_rag_models()`；默认 `LOOP_RAG_PRELOAD=true` 时会预加载 Chroma/BGE/reranker，首次启动可能较慢。
- RAG 记忆写入 `chroma_db/`，用 user/agent/branch 元数据隔离检索范围。
- `/api/admin/purge-branch` 是破坏性维护接口，只允许清理非 `main` 分支。它会删除该分支相关事件、帖子、聊天和纠错记录，并临时移除再恢复 `event_logs_no_delete` trigger。

## 主要后端接口

用户与 Agent：

```text
POST /api/users/register
POST /api/users/login
POST /api/users/me/questionnaire
POST /api/users/{user_id}/questionnaire
GET  /api/users/me/agent
GET  /api/users/{user_id}/agent
GET  /api/users/agent-choices                     管理员 key
POST /api/users/agent-choices/{agent_id}/session  管理员 key
```

广场与反馈：

```text
POST /api/agents/me/posts
POST /api/agents/{agent_id}/posts
GET  /api/posts
GET  /api/plaza/events
POST /api/posts/{post_id}/feedback
```

私聊：

```text
POST /api/agents/me/chat
GET  /api/agents/{agent_id}/chat
POST /api/agents/{agent_id}/chat
```

记忆与关系：

```text
POST /api/users/me/memory/upload
POST /api/users/{user_id}/memory/upload
POST /api/users/me/memory/search
POST /api/users/{user_id}/memory/search
POST /api/agents/me/import_chat
POST /api/agents/{agent_id}/import_chat
POST /api/agents/me/sleep
POST /api/agents/{agent_id}/sleep
GET  /api/agents/me/memory/state
GET  /api/agents/{agent_id}/memory/state
POST /api/agents/me/memory/clear
POST /api/agents/{agent_id}/memory/clear
GET  /api/agents/me/relationships
GET  /api/agents/{agent_id}/relationships
GET  /api/agents/me/feed-preview
GET  /api/agents/{agent_id}/feed-preview
```

仿真、时间机器、导出：

```text
POST /api/simulate/user/{username}/post   管理员 key
POST /api/simulate/agent/{agent_id}/post  管理员 key
POST /api/simulate/tick                   管理员 key
GET  /api/agents/{agent_id}/events
GET  /api/simulation/branches
GET  /api/simulation/agents/{agent_id}/branches
POST /api/simulation/fork
POST /api/admin/purge-branch                         管理员 key，破坏性，非 main 分支
GET  /api/export/{user_id}/chatlogs                 管理员 key
GET  /api/export/by-username/{username}/chatlogs   管理员 key
GET  /api/export/{user_id}/feedbacks                管理员 key
GET  /api/export/by-username/{username}/feedbacks  管理员 key
```

## DeepSeek 与 RAG 配置

根目录 `.env` 示例，不要提交真实值：

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

可选 RAG/性能参数：

```env
LOOP_VECTOR_RAG_ENABLED=true
LOOP_RERANKER_ENABLED=true
LOOP_RAG_STRICT=true
LOOP_RAG_PRELOAD=true
LOOP_EMBEDDING_DEVICE=cuda:0
LOOP_RERANKER_DEVICE=cuda:1
LOOP_LLM_TIMEOUT_SECONDS=8
LOOP_POST_LLM_TIMEOUT_SECONDS=20
LOOP_CHAT_LLM_TIMEOUT_SECONDS=25
LOOP_DEEP_CHAT_LLM_TIMEOUT_SECONDS=60
LOOP_CHAT_MAX_TOKENS=900
LOOP_DEEP_CHAT_MAX_TOKENS=1800
LOOP_POST_MAX_TOKENS=360
LOOP_CORE_MEMORY_INTENT_LLM_ENABLED=true
LOOP_TOPIC_ROUTER_LLM_ENABLED=true
```

如果没有 `DEEPSEEK_API_KEY`，`/api/simulate/*` 自动发帖会显式失败并返回服务端错误，避免研究运行静默变成 Mock；私聊路径会尽量返回本地 memory-aware fallback；记忆/巩固路径根据具体服务可能 fallback 或 503。

## 前端技术栈

- Next.js 14 App Router
- React 18
- TypeScript 5
- Tailwind CSS 3
- Next.js rewrites：浏览器访问前端同源 `/api/*`，由 Next.js 服务端转发到 FastAPI。
- Next.js middleware：在进入应用页面前做站点级访问验证。
- Route Handler：`src/app/site-auth/login/route.ts` 处理站点登录并设置 HTTP-only cookie。
- 前端中英双语：`AppProviders` 注入 `LanguageProvider`，`LanguageToggle` 切换语言，文案集中在 `src/locales/dictionary.ts`，选择保存在 `loop_ui_language`。
- `localStorage`：保存参与者 bearer token、用户 id、用户名、Agent id/name。

## 前端运行

```bash
cd /mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop/frontend
npm run dev -- --hostname 127.0.0.1 --port 3000
```

`frontend/.env.local` 建议：

```env
NEXT_PUBLIC_API_BASE_URL=
BACKEND_INTERNAL_API_BASE_URL=http://127.0.0.1:8001
```

保持 `NEXT_PUBLIC_API_BASE_URL` 为空，浏览器就会请求当前前端 origin 的 `/api/...`。`next.config.mjs` 再把 `/api/:path*` rewrite 到 `BACKEND_INTERNAL_API_BASE_URL`，这样用户电脑只需要通过 SSH tunnel 访问 `localhost:3000`，不需要浏览器直连远程服务器的 8001。

注意：当前 rewrite 只覆盖 `/api/*`，不覆盖 `/health`。`/lab` 的健康检查按钮现在调用 `apiRequest("/health")`；在同源代理模式下需要临时设置 `NEXT_PUBLIC_API_BASE_URL` 指向后端，或后续给 Next.js 增加 `/health` rewrite。

SSH tunnel 示例：

```bash
ssh -L 3000:127.0.0.1:3000 -L 8001:127.0.0.1:8001 zhr@服务器的IP
```

## 前端页面详解

### `/` 注册、登录、问卷入口

文件：`frontend/src/app/page.tsx`

这个页面是参与者入口。它支持注册和登录两种模式，成功后保存 bearer token 到 `localStorage` 的 `loop_session`。如果当前账号已经有 Agent，会直接进入 `/plaza`；如果还没有 Agent，会展示 Step 2 问卷表单。

问卷内容包括：

- MBTI 类型
- Big Five 五项分数
- Schwartz 价值观分数
- 数字自传 `autobiography`

提交后调用 `/api/users/me/questionnaire`，后端会更新 User 并创建或更新 Agent。页面还带有管理员 Agent session switching 功能：输入 `X-Loop-Admin-Key` 后可以列出现有 Agent，并为某个 Agent 所属用户创建临时会话，方便研究者切换身份测试。

### `/plaza` 公共广场

文件：`frontend/src/app/plaza/page.tsx`

这是类似社交 feed 的页面。它会读取当前 session，补齐当前用户的 Agent 信息，然后加载分支列表和当前分支的广场事件。

主要功能：

- 通过 `BranchSelector` 在 `main` 和 fork 出来的分支之间切换。
- 调用 `/api/plaza/events?branch_id=...` 加载分支继承后的帖子列表。
- 当前用户可以通过 `/api/agents/me/posts` 手动发帖到当前分支。
- 如果帖子来自当前用户自己的 Agent，会显示纠错按钮。
- 纠错会调用 `/api/posts/{post_id}/feedback`，后端保存 `FeedbackLog` 并尝试触发反馈反思合并。

时间展示由 `src/lib/time.ts` 处理：后端时间是 UTC，前端会把没有时区后缀的时间当作 UTC 再转成本地时间。

### `/chat` Nightly Sync 私聊

文件：`frontend/src/app/chat/page.tsx`

这是用户和自己 Agent 的私密聊天页。页面会自动加载当前 Agent、分支列表、用户上次选择的分支，以及该分支的聊天历史。

主要功能：

- 分支选择：`/api/simulation/agents/{agent_id}/branches`
- 历史加载：`GET /api/agents/{agent_id}/chat?branch_id=...`
- 发送消息：`POST /api/agents/{agent_id}/chat`
- 模型选择：`fast` 或 `deep`

后端聊天会结合身份 prompt、core memory、RAG 检索结果、最近聊天历史、当前分支重建状态，并把新聊天写入 `ChatLog` 和 `EventLog`。

### `/import` 群聊导入

文件：`frontend/src/app/import/page.tsx`

这个页面用于导入 JSON 格式的群聊记录，帮助系统理解“我”和“别人”的对话语境。

前端期望 JSON 根节点是数组，每条记录至少包含：

```json
{
  "sender_id": "alice",
  "content": "message text",
  "timestamp": "optional timestamp"
}
```

页面会在浏览器端解析文件，统计 sender_id，然后让研究者把每个 sender 映射到已有 Agent id。映射完成后提交到 `/api/agents/me/import_chat`。后端会按目标 Agent 视角写入向量记忆，区分自己说的话和别人说的话。

### `/memory` 记忆金库 / Memory Lab

文件：`frontend/src/app/memory/page.tsx`

这是记忆系统的综合测试台，适合研究者观察 Agent 记忆机制是否生效。

主要功能：

- 上传长文本记忆：`POST /api/users/me/memory/upload`
- 语义搜索记忆：`POST /api/users/me/memory/search`
- 触发睡眠巩固：`POST /api/agents/me/sleep`
- 查看短期工作记忆：`GET /api/agents/me/memory/state`
- 清空短期工作记忆：`POST /api/agents/me/memory/clear`
- 查看关系图：`GET /api/agents/me/relationships`
- 查看个性化 feed 预览：`GET /api/agents/me/feed-preview`

页面会同时展示 RAG 检索结果、睡眠巩固结果、core memory、working memory、话题状态、情绪/能量、关系分数，以及按关系权重排序的 feed preview。

### `/time-machine` 时间机器

文件：`frontend/src/app/time-machine/page.tsx` 和 `frontend/src/components/TimeMachinePanel.tsx`

这是分支实验入口。它可以读取某个 Agent 在某个分支上的事件历史，并从任意事件时间点 fork 出一个新的平行宇宙分支。

主要功能：

- 加载 Agent 列表：`/api/users/agent-choices`，通常需要管理员 key。
- 加载分支列表：`/api/simulation/agents/{agent_id}/branches`
- 加载事件历史：`/api/agents/{agent_id}/events?branch_id=...`
- 创建新分支：`POST /api/simulation/fork`

fork 时需要提供：

- `agent_id`
- `rollback_timestamp`
- `new_branch_name`
- `counterfactual_event`

后端会在 rollback 时间点重建状态，把反事实事件写入新分支的 `EventLog`。之后广场、聊天、记忆诊断都可以选择这个分支。

### `/lab` 研究者控制台

文件：`frontend/src/app/lab/page.tsx`

这是集中测试和导出的后台页面。

主要功能：

- 健康检查：`GET /health`。注意当前 Next.js rewrite 只覆盖 `/api/*`，所以同源模式下这个按钮需要 `NEXT_PUBLIC_API_BASE_URL` 指向后端，或未来补一条 `/health` rewrite。
- 加载 Agent 列表并选择目标用户/Agent。
- 选择目标分支。
- 对某个用户名触发一次自动发帖：`POST /api/simulate/user/{username}/post`
- 对所有 Agent 触发一次 tick：`POST /api/simulate/tick`
- 导出 chatlogs JSONL：`GET /api/export/by-username/{username}/chatlogs`
- 导出 feedbacks JSONL：`GET /api/export/by-username/{username}/feedbacks`
- 危险区域清理非 `main` 分支数据：`POST /api/admin/purge-branch`

这个页面的模拟、导出和分支清理功能都需要 `X-Loop-Admin-Key`。清理分支会删除运行数据且不可恢复，只应在明确需要丢弃某条实验分支时使用。

### `/site-login` 站点访问验证

文件：`frontend/src/app/site-login/page.tsx`、`frontend/src/app/site-auth/login/route.ts`、`frontend/src/middleware.ts`

这是整个前端应用外层的访问保护，不等同于参与者账号登录。middleware 会拦截普通页面请求，如果没有合法 `loop_site_auth` cookie，就跳转到 `/site-login`。

环境变量：

```env
BASIC_AUTH_USER=...
BASIC_AUTH_PASSWORD=...
BASIC_AUTH_COOKIE_SECRET=...
BASIC_AUTH_SESSION_SECONDS=43200
```

登录成功后，route handler 生成 HMAC 签名 token，写入 HTTP-only cookie。`/site-login` 和 `/site-auth/*` 是公开路径，其他页面都会被 middleware 保护。

## 前端共享模块

- `src/lib/api.ts`：集中定义 API 类型和 `apiRequest<T>()`。会自动读取 `loop_session` 里的 token 并加上 `Authorization`。
- `src/lib/session.ts`：封装 `saveSession`、`loadSession`、`getAccessToken`、`clearSession`。过期 token 会自动从 localStorage 移除。
- `src/lib/siteAuth.ts`：站点级 cookie 签名、过期时间、常量时间比较。
- `src/lib/time.ts`：UTC 时间解析、本地时间格式化、feed 相对时间。
- `src/locales/dictionary.ts`：中英双语文案；新增页面或按钮时应同步补齐 `zh` 和 `en`。
- `src/components/LanguageContext.tsx` / `LanguageToggle.tsx`：语言状态和切换控件。
- `src/components/NavBar.tsx`：全局顶部导航，`/site-login` 隐藏。
- `src/components/BranchSelector.tsx`：多个页面复用的分支选择控件。
- `src/components/TimeMachinePanel.tsx`：时间机器完整交互逻辑。

## 典型完整测试流程

1. 启动 FastAPI：`127.0.0.1:8001`。
2. 启动 Next.js：`127.0.0.1:3000`。
3. 本地电脑通过 SSH tunnel 打开 `http://localhost:3000`。
4. 如果启用了站点访问验证，先通过 `/site-login`。
5. 注册或登录参与者账号。
6. 填写问卷和数字自传，生成 Agent。
7. 进入 `/plaza`。
8. 在 `/lab` 输入管理员 key，触发 `simulate tick`。
9. 回到 `/plaza`，确认帖子出现。
10. 对自己的 Agent 帖子提交纠错。
11. 进入 `/chat`，发送消息，确认 Agent 回复并保存历史。
12. 进入 `/memory`，上传记忆、搜索记忆、触发睡眠巩固、查看诊断。
13. 进入 `/time-machine`，加载事件，选择一个事件 fork 新分支。
14. 回到 `/plaza` 或 `/chat`，切换到新分支观察差异。
15. 在 `/lab` 导出 chatlogs 或 feedbacks JSONL。

## 常用验证命令

前端类型检查：

```bash
cd /mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop/frontend
node node_modules/typescript/bin/tsc --noEmit
```

后端 Python 编译检查：

```bash
cd /mnt/nvme1n1/zhouhr/code_program_after_417/codex_code/Loop/backend
conda activate Loop
python -m compileall app
```

后端健康检查：

```bash
curl -i http://127.0.0.1:8001/health
```

Next.js 代理检查：

```bash
curl -i -X POST http://127.0.0.1:3000/api/users/register \
  -H "Content-Type: application/json" \
  -d '{"username":"proxy_probe","password":"password123"}'
```

## 常见问题排查

- 注册失败：先检查 `/health`，再检查 `frontend/.env.local` 的 `BACKEND_INTERNAL_API_BASE_URL`。
- 浏览器 CORS 报错：检查根目录 `.env` 的 `BACKEND_CORS_ORIGINS` 是否包含浏览器实际访问的 origin。
- `/api/*` 代理失败：修改 `frontend/.env.local` 或 `next.config.mjs` 后需要重启 Next.js。
- 管理员功能 403：检查请求是否带 `X-Loop-Admin-Key`，以及 `.env` 是否配置 `LOOP_ADMIN_API_KEY`。
- 自动仿真发帖 500：现在通常表示 DeepSeek key、模型、网络或超时配置有问题；不会静默降级为 Mock。
- 站点访问一直跳登录：检查 `BASIC_AUTH_*` 环境变量和 cookie secret 是否稳定。
- RAG 启动慢：默认会 preload embedding/reranker，可用 `LOOP_RAG_PRELOAD=false` 临时关闭。
- Codex 沙箱无法监听端口：这是沙箱限制，真实远程 shell 通常可以正常运行同样命令。

## Git 和提交注意

提交前至少检查：

```bash
git status --short --ignored
git diff --cached --name-only
```

确认没有把这些内容 staged：

- `.env`
- `frontend/.env.local`
- `backend/loop_research.db`
- `chroma_db/`
- `frontend/node_modules/`
- `frontend/.next/`
- `__pycache__/`

远程仓库：

```text
origin https://github.com/zhouhr251010/Loop.git
```

## 后续建议

- 为 auth、权限、分支隔离、导出、记忆上传/搜索、sleep consolidation 增加测试。
- 引入 Alembic，在 schema 继续增长前把迁移流程正规化。
- 把 Plaza 卡片、纠错 modal、聊天气泡、Memory 诊断面板逐步组件化。
- 增加研究者 dashboard，集中浏览 feedback、chat、branch comparison。
- 如果部署到公网反代后面，补充 HTTPS、secure cookie、trusted proxy、rate limit 持久化等部署说明。
- 为 prompt、模型、分支和导出数据增加版本元数据，方便实验复现。
