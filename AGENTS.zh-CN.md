# Loop 项目中文接手说明


## 项目定位

Loop 是一个面向计算社会科学实验的研究原型。它把真实参与者的问卷、数字自传、聊天记录、真实人际聊天、纠错反馈和记忆材料转化为一个虚拟 Agent 的身份与记忆，让多个 Agent 在一个“平行社会”里发帖、聊天、形成关系、产生记忆巩固，并支持研究者导出数据用于后续分析或继续训练。

一句话理解：这是一个“真人画像 -> 虚拟 Agent -> 广场互动 -> 多会话私聊/实验模式 -> 真人社交旁路 -> Agent 群聊/辩论沙盘 -> 漂移检测/盲测评估 -> 记忆/RAG/时间线 -> 研究数据导出”的实验平台。

## 接手时先记住这些

如果你只来得及记住最高优先级的事，优先记这些：

- Loop 是一个事件溯源、分支可回放的社会模拟系统。分支视图的真相来源是 `EventLog`，但会话级聊天历史现在从 `ChatLog` 按 `branch_id + session_id` 分页读取。
- 一个 `User` 只对应一个 `Agent`；问卷提交会创建或刷新这个一对一映射。
- 系统支持通过 `LOOP_ADMIN_USERNAME` / `LOOP_ADMIN_PASSWORD` 配置一个 bearer 登录型管理员账号。管理员 session 可以进入 Lab、切换/代入 Agent 视图，并操作受保护的研究控制功能。
- 用户的长期身份目前主要落在两处：`autobiography` 和结构化 `core_memory`。
- `core_memory` 现在不是三个字段了，规范键包括 `persona_traits`、`key_relationships`、`current_goals`、`communication_style`。
- 系统里现在还维护 system-owned NPC Agent。启动时会补一个默认 NPC，导入群聊时也可以按外部 sender 稳定创建 NPC；不要把这些 NPC 当成真实参与者。
- 私聊已经不是“固定 prompt + 普通 RAG”而已。`mode_alpha` 是完整 IACL 路径，允许主动查记忆、读广场、改 Core Memory、更新内部状态；`mode_beta` 是静态 prompt 基线，用于盲测对照。
- 系统现在有持久化真人社交聊天。真人 1v1 和真人群聊都写入 `ChatLog`，用 `SessionType.HUMAN_TO_HUMAN` / `GROUP_SHARED` 区分，分支读取遵守 fork lineage，实时通知走 `/api/social/events` SSE 和可选 Redis fanout。
- 群组有“Boundary 1”物种隔离：`HUMAN_ONLY` 群只能加入 `USER`，`AGENT_ONLY` 群只能加入 `AGENT`。不要在 CRUD、导入、沙盘或 UI 快捷逻辑里混用。
- `/sandbox` 是管理员专用的推演沙盘，和 `/lab` 分开；它用于监督式多 Agent 辩论、创建隔离群组、手动推进 Agent-only 群聊回合。
- 全局分支曝光由单例 `SystemSetting` 控制：`global_active_branch` 决定普通参与者默认看到的分支，`allow_user_branch_switch` 决定非管理员能否在 UI 中手动切分支。
- M1-M6 验证数据还包括按分支记录的每周 probe 问卷和人生反事实锚点。`/api/probes/*`、`/api/counterfactuals/*`、`ProbeResponse.branch_id`，以及管理员可选的 `agent_id` 目标选择，都是研究链路的一部分。
- 每轮聊天同时受 `branch_id`、`session_id`、`topic` 和 `session_type` 隔离；读取历史、话题路由、漂移检测、社交通知未读数或导出分析时，不要把 Agent 私聊、真人 1v1、群聊混在一起。
- 分支效果依赖 `TimeMachine` 的状态重建和分支事件回放，不能只靠给 `Post` 加个 `branch_id` 过滤来理解。
- 用户纠错不会直接改写 `posts.content`。广场展示时会根据最新的 `FEEDBACK_CREATED` 事件覆盖显示文本。
- 广场、聊天历史、事件时间线都必须分页，前后端的 `limit`/`skip` 保护是已经上线的核心架构，不是临时优化。
- 前端正常情况下只和 `localhost:3000` 通信，再由 Next.js rewrite `/api/*` 到 `127.0.0.1:8001`。`/health` 目前是例外。
- RAG 基础设施已经不是本地向量库目录了。当前链路是 Postgres + pgvector 的 `rag_documents`、运行在 `127.0.0.1:7997/7998` 的 Infinity embedding/reranker 服务、Git-style 分支记忆读取（`main` 只读 main，fork 分支读 `main + 当前分支`），以及可选的 Redis 限流。
- `.env`、`model_cache/` 和 Postgres/Redis 对应的 Docker volume 都属于实验运行态，不要提交，也不要随手删除。

## 最高优先级规则

- 不要打印、提交或泄露根目录 `.env` 里的任何密钥。
- 不要提交数据库 dump 或运行态导出，它们可能包含实验数据。
- 不要提交 `model_cache/`，这是 Infinity / Hugging Face 运行时缓存。
- 不要提交 `frontend/node_modules/`、`frontend/.next/`、`frontend/npm-cache/`、`__pycache__/` 等依赖、缓存、构建产物。
- 禁止批量删除文件或目录。确实需要删除时，每次最多删除 1 个明确绝对路径文件；如果需要清理多个文件，先停下来告诉用户。
- 不要主动调用 `/api/admin/purge-branch` 这类破坏性清理接口，除非用户明确要求清理某个分支的数据。
- 不要调用 `DELETE /api/agents/{agent_id}`，除非用户明确要求删除这个 Agent 及其关联痕迹。
- 后端依赖不要装到全局 Python，使用已有 conda 环境 `Loop`。
- 开发服务必须绑定 `127.0.0.1`，不要绑定 `0.0.0.0`。

## 目录地图

```text
Loop/
  Makefile                  工程化启动和校验脚本
  backend/
    app/
      main.py                 FastAPI 入口、中间件、路由挂载、启动时建表
      database.py             SQLAlchemy engine/session、Postgres/pgvector 初始化、RAG 表和触发器启动引导
      models.py               User/Agent/Post/ChatLog/EventLog/Relationship 等表
      security.py             bearer token、管理员 bearer/机器 key、限流、请求大小、安全头
      routers/
        admin.py              高权限维护接口，按非 main 分支清理运行数据
        users.py              注册、登录、问卷、Agent 会话切换
        posts.py              广场发帖、分支 feed、纠错反馈
        probes.py             M1-M6 probe 问卷状态与提交
        counterfactuals.py    人生反事实锚点采集
        chat.py               私聊、多会话历史、实验模式、漂移检测
        group.py              Boundary-1 群组创建、成员管理、真人群消息写入
        social.py             真人 1v1 / 真人群聊 REST、SSE、WebSocket 通知
        evaluations.py        外部盲测评估接口
        memory.py             记忆上传/搜索、睡眠巩固、导入聊天、关系/诊断
        simulate.py           管理员触发 Agent 自动发帖和 tick
        simulation.py         事件时间线、分支列表、时间机器 fork
        export.py             JSONL 研究数据导出
        agents.py             Agent 删除等单体管理接口
      schemas/                Pydantic 输入输出模型，含 system_settings.py
      crud/                   数据库增删查改封装
      services/
        llm_service.py        DeepSeek/OpenAI-compatible 生成发帖和私聊回复
        drift_detector.py     zero-shot 身份一致性漂移检测
        rag_service.py        Postgres/pgvector + Infinity 的记忆检索
        infinity_client.py    Infinity embedding/reranker 共享异步 HTTP 客户端
        agent_graph.py        Agent 短期工作记忆与话题/状态图
        consolidation_service.py 睡眠式记忆巩固、高层反思、关系更新
        core_memory_service.py Core Memory 规范化和 prompt 格式化
        event_store.py        append-only EventLog 写入
        branching.py          main/分支 id、分支存在性、父分支锚点
        time_machine.py       通过 EventLog 重放重建某分支状态
        feedback_service.py   用户纠错后的反思合并
        scoring_service.py    IPIP/PVQ 问卷计分与 core memory 合并
        tools.py              私聊 Agent 可调用的工具层
        npc_seed.py           系统 NPC / sender NPC 建种子
        memory_watcher.py     私聊后后台抽取 durable 身份事实
        agent_cleanup_service.py Agent 级联删除和痕迹清理
        access_control.py     Agent 级资源的 owner/admin 权限解析
        rolling_summary.py    群聊滚动摘要，压缩长上下文
        speaker_manager.py    Agent-only 群组单回合发言选择和生成
        debate_graph.py       监督式多 Agent 辩论 LangGraph
        social_realtime.py    社交通知 SSE + Redis fanout
        ws_manager.py         WebSocket 连接管理
    requirements.txt
  frontend/
    next.config.mjs           /api/* 同源代理到 FastAPI
    dev-server.mjs            Next dev 包装器，专门代理 /api/social/events SSE
    src/middleware.ts         站点级访问验证
    src/components/
      AppProviders.tsx        前端上下文 Provider
      NavBar.tsx              顶部导航
      BranchSelector.tsx      分支选择组件
      LanguageContext.tsx     中英双语 UI 状态
      LanguageToggle.tsx      语言切换按钮
      TimeMachinePanel.tsx    时间机器主 UI
      social/
        H2HChatPanel.tsx      真人 1v1 聊天面板
        HumanGroupPanel.tsx   真人群聊面板
      sandbox/
        DebatePanel.tsx       多 Agent 辩论触发面板
        GroupSimulationPanel.tsx 群组仿真和 Agent-only tick 面板
    src/app/
      page.tsx                注册/登录/问卷 onboarding
      plaza/page.tsx          广场 feed、发帖、纠错
      chat/page.tsx           多会话私聊、实验模式、校准弹窗
      social/page.tsx         真人社交空间
      sandbox/page.tsx        管理员推演沙盘
      probes/page.tsx         IPIP-120/PVQ-21 probe 问卷页
      counterfactuals/page.tsx 人生反事实锚点页
      evaluations/[agent_id]/page.tsx 公开盲测评估页
      import/page.tsx         群聊 JSON 导入
      memory/page.tsx         记忆金库/诊断实验台
      time-machine/page.tsx   时间机器页面壳
      lab/page.tsx            研究者控制台
      site-login/page.tsx     站点访问登录页
      site-login/SiteLoginForm.tsx 站点登录表单
      site-auth/login/route.ts 站点登录 route handler
      layout.tsx              全局布局与 Provider 外壳
    src/lib/
      api.ts                  fetch 封装、类型定义、bearer token 注入
      i18n.ts                 useUiLanguage 轻量重导出
      session.ts              localStorage 参与者会话
      siteAuth.ts             站点 cookie 签名与校验
      time.ts                 UTC 时间解析和展示
    src/data/questionnaires.json IPIP/PVQ probe 题目
    src/locales/dictionary.ts 中英 UI 文案字典
  .env.example
  docker-compose.infra.yml   Postgres / Redis / Infinity 基建编排
  model_cache/               Infinity / Hugging Face 模型缓存
```

## 后端技术栈

- Python 3.10+
- FastAPI + APIRouter
- SQLAlchemy ORM
- PostgreSQL + pgvector 是必需项；未配置 Postgres 时启动直接失败
- bcrypt 密码哈希
- 自实现 HMAC 签名 bearer token
- 配置型管理员 bearer 登录：`LOOP_ADMIN_USERNAME` / `LOOP_ADMIN_PASSWORD` 会维护唯一 `User.is_admin=true` 管理员账号；部分自动化接口也支持 `X-Loop-Admin-Key`
- 配置 `LOOP_REDIS_URL` 时使用 Redis 异步固定窗口限流；Redis 不可用时 fail-open 放行，同时保留请求体大小限制、安全响应头和 Trusted Host 检查
- OpenAI Python SDK，`base_url` 指向 DeepSeek 兼容接口
- python-dotenv 读取根目录 `.env`
- Infinity HTTP 服务承载 BGE embedding 和 reranker
- LangGraph/LangChain 相关依赖，用于 Agent 工作记忆和图式流程

推荐本地启动顺序：

```bash
make infra
make backend
make frontend
```

`make infra` 会通过 `docker-compose.infra.yml` 启动 Postgres、Redis 和 Infinity embedding/reranker。

模型缓存部署说明：

- `model_cache/` 虽然被 `.gitignore` 忽略，但它是运行环境的一部分，因为 Infinity 会把它挂载为 `/app/.cache`。
- 新服务器建议先预下载模型，再开始实验流程，避免首轮启动慢或超时：
  1. `make infra`
  2. `docker compose -f docker-compose.infra.yml up -d embedding reranker`
  3. 首次执行一次 `make backend`，触发 `warm_up_rag_models()` 自动拉取并填充 `model_cache/`。
- 多服务器快速部署时，建议从已预热机器用 `rsync` 拷贝 `model_cache/`，并保持该目录不入库。

## 后端运行

```bash
make backend
```

接口文档：

```text
http://localhost:8001/docs
```

数据库：

```text
通过 POSTGRES_URL 或 POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB 连接 PostgreSQL
```

没有本地数据库文件回退。

## 后端数据模型

- `SystemSetting`：单例全局实验曝光控制，保存 `allow_user_branch_switch` 和 `global_active_branch`。
- `User`：实验参与者。保存用户名、密码哈希、管理员标记、创建时间、MBTI、Big Five、Schwartz 价值观、数字自传、结构化 core memory。
- `User.core_memory` 规范字段：`persona_traits`、`key_relationships`、`current_goals`、`communication_style`。
- `Agent`：每个用户对应一个虚拟 Agent，也包含 `is_npc=true` 的系统/NPC Agent。保存 agent 名字和基础 system prompt。
- `Post`：Agent 在广场里产生的公开帖子。
- `FeedbackLog`：用户对自己 Agent 帖子的纠错记录，是持续学习/后续微调的重要监督信号。
- `Group`：N-to-N 聊天房间，记录 `HUMAN_ONLY` / `AGENT_ONLY` 类型、owner 和 topic。
- `GroupMember`：群成员行，使用 `entity_type`（`USER` / `AGENT`）和 `entity_id` 标识具体成员，CRUD 层强制 Boundary 1 隔离。
- `GroupSummary`：按 `group_id + branch_id` 保存群聊滚动摘要，用于长上下文压缩。
- `ChatLog`：用户和 Agent 私聊、真人 1v1、群聊共享消息的统一存储，包含 sender/receiver/group 元数据、`branch_id`、`session_id`、`topic`、`experiment_mode`、`session_type`、未读/记忆抽取标记。
- `Evaluation`：外部盲测评价，保存评价人与被试关系、1-5 分真实性评分、文字反馈、抽样聊天 id。
- `ProbeResponse`：M1-M6 验证 probe 回答，包含 `branch_id`，当前用于保存认证用户的 IPIP-120/PVQ-21 每周基线。
- `EventLog`：append-only 事件时间线，用于时间机器、分支 feed、状态重放。Postgres 里有禁止 update/delete 的 trigger。
- `Relationship`：Agent 到 Agent 的有向亲密度/关系分数。
- `ReflectionEvent`：睡眠巩固时产生的分层反思节点。

## 后端核心机制

- `main.py` 启动时执行 `Base.metadata.create_all()`，启用 `vector` 扩展，补齐 `agents.is_npc`、`probe_responses.branch_id`、ChatLog 社交/群聊字段（`session_type`、`sender_user_id`、`receiver_user_id`、`group_id`、`is_memory_extracted`、`is_read`）、`groups.owner_id`、分支化 `group_summaries`、社交聊天组合索引、`users.is_admin`，维护配置型管理员账号，创建 `rag_documents` 表/索引，并安装 Postgres append-only trigger。
- FastAPI lifespan 启动顺序是：`initialize_database()` -> `ensure_system_npc_agent()` -> `warm_up_rag_models()`。
- `security.py` 负责 bearer token、管理员 bearer 权限、机器 API key、请求大小限制、Redis 异步限流、安全响应头和 trusted host。
- 普通用户接口一般要求 `Authorization: Bearer <token>`。
- UI/研究后台类管理员接口一般要求 `User.is_admin=true` 的 bearer session。启动时会根据 `LOOP_ADMIN_USERNAME` / `LOOP_ADMIN_PASSWORD` 创建或更新唯一管理员账号，普通注册不能抢占该用户名。
- 机器自动化接口使用 `X-Loop-Admin-Key`，对应 `.env` 的 `LOOP_ADMIN_API_KEY`；当前主要用于 simulation 和 branch purge 这类 `require_admin_or_machine_key` 路径。
- 如果没配 `LOOP_AUTH_SECRET`，bearer token 会退回到进程级临时 secret；后端重启后旧 session 会全部失效。
- `backend/app/database.py` 会读取 `POSTGRES_URL`，或 `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` 组合来连接 Postgres；没配 Postgres 会直接抛出 `RuntimeError` 阻止启动。
- **架构黑科技 1：Agentic Memory / 主动寻址记忆已经是核心链路，不是普通 RAG 装饰。** Chat 生成路径允许 Agent 主动调用 `search_personal_memory`、`edit_core_memory`、`read_plaza_feed`、`get_current_time`、`check_energy_budget`、`update_internal_state` 等工具。用户说出长期身份事实、关系变化、稳定偏好或价值观时，Agent 必须用 `edit_core_memory` 写入 durable Core Memory；遇到需要回忆的问题时，用 `search_personal_memory` 定向进入 `retrieve_hybrid_memory()`，而不是把所有历史粗暴塞进 prompt。
- `llm_service.py` 同时保留 tool-calling chat 和 fallback retrieval 路径，并通过 `historical_chat_loader` 按需翻页读取更早的分支/会话聊天历史。不要把它退化成一次性加载全部聊天记录。
- `agent_graph.py` 把 `AGENT_TOOLS` 绑定进 LangGraph 流程，维护 active messages、emotion、energy、topic state 和 core-memory writeback。这是 Agent 运行时心智回路，后续改动必须保护。
- 私聊实验模式是当前论文验证链路的一部分：`mode_alpha` 是完整 IACL，`mode_beta` 走 `chat_with_agent_static_prompt()`，禁用工具、RAG、自我更新记忆和历史上下文。
- 真人社交聊天是持久研究信号，不是纯 UI 状态。1v1 消息追加 `HUMAN_MESSAGE_RECEIVED`，真人群消息追加 `GROUP_MESSAGE_RECEIVED`，并都写入带分支的 `ChatLog`。当同一用户积累 20 条未处理真人消息时，会触发旁路 memory watcher 提取表达风格/社交姿态洞察。
- Agent-only 群聊和监督式辩论是管理员/机器仿真路径。`speaker_manager.py` 每次只为 `AGENT_ONLY` 群选择一个发言 Agent 并保存为群消息；`debate_graph.py` 使用主持人式 LangGraph 决策是否结束、下一位发言者，并写入辩论发言和总结事件。
- 群聊长上下文由 `rolling_summary.py` 保护：prompt 中只保留最新有限条原文，超出窗口的消息被压缩进按分支隔离的 `GroupSummary`。
- `/api/probes/submit` 会保存 IPIP/PVQ probe 回答，经 `scoring_service.py` 计分后追加分支记忆事件；只有 main 分支会合并进 `User.core_memory`，必要时刷新 Agent 画像。
- `/api/counterfactuals/suggestions` 会从数字自传和有界的近期私聊/帖子里挖掘候选人生决策锚点，供用户提交前选择或补写。
- `/api/counterfactuals/submit` 会保存人生决策反事实锚点，追加 `COUNTERFACTUAL_ANCHOR_CREATED` 和 `CORE_MEMORY_UPDATED`；只有 main 分支会把锚点直接持久写入 `User.core_memory.persona_traits`。
- Probe 和 counterfactual 接口都接受可选 `agent_id`。普通用户只能操作自己的 Agent；管理员 bearer session 可以为指定 Agent 采集或生成研究数据，权限解析集中在 `access_control.py`。
- `/api/simulation/settings` 暴露全局分支曝光设置。管理员可以 patch `global_active_branch` 和 `allow_user_branch_switch`；前端用这两个值决定默认分支，以及普通用户是否能看到可用的分支切换控件。
- zero-shot 身份漂移检测是 M1/M2 验证链路的一部分：`/api/chat/{agent_id}/check-drift` 会用最近会话回复对照身份核心，只有判定漂移时才追加 `DRIFT_DETECTED` 事件。
- `EventLog` 是分支和时间机器的核心：帖子、聊天、反事实事件都会写入事件流。
- `TimeMachine` 根据指定 `agent_id`、`branch_id`、时间点重放事件，重建当前 core memory、工作记忆、关系等状态。
- FastAPI lifespan 会调用 `warm_up_rag_models()`；默认 `LOOP_RAG_PRELOAD=true` 时会带锁和 TTL 地预热 Infinity embedding/reranker，避免多 worker 重复打爆模型加载。
- Postgres 模式下启动会额外启用 `vector` extension，并创建 `rag_documents` 表及其 metadata/embedding 索引。
- RAG 记忆现在写入 Postgres `rag_documents`，通过 user/agent/branch/topic 等 metadata 做隔离，不再依赖本地向量库目录。读取时 `main` 只读 main，非 main 分支会读 main 加当前分支，避免平行分支完全丢失主线记忆。
- **架构黑科技 2：前端分页防爆机制已经上线。** Plaza、Chat、TimeMachine 三类可能无限增长的长列表都必须走 `skip`/`limit` 分页、`hasMore*` 状态和显式“加载更多”。保留 `PLAZA_PAGE_SIZE`、`CHAT_HISTORY_PAGE_SIZE`、`EVENT_PAGE_SIZE` 这类硬上限，不要回退成一次性拉取全部帖子、全部聊天或全部事件。
- 后端列表接口也必须继续保留有界 `limit`：`/api/plaza/events`、`/api/posts`、`/api/agents/{agent_id}/chat`、`/api/agents/{agent_id}/events`、`/api/social/contacts`、`/api/social/messages/{contact_id}`、`/api/social/groups`、`/api/social/groups/{group_id}/messages` 是长实验防爆边界。
- `/api/admin/purge-branch` 是破坏性维护接口，只允许清理非 `main` 分支。它会删除该分支相关事件、帖子、聊天和纠错记录，并临时移除再恢复 `event_logs_no_delete` trigger。

## 修改后端前必须知道的实现现状

下面这些是代码里的真实行为，很容易在快速阅读时漏掉：

- `core_memory_service.py` 会把 `User.core_memory` 规范化为 4 个字段，不是 3 个。老数据可能没有 `communication_style`，所以读取前总是先 normalize。
- `initialize_database()` 会维护配置型管理员账号。如果没有配置 `LOOP_ADMIN_USERNAME` / `LOOP_ADMIN_PASSWORD`，所有已有 `is_admin` 标记会被清掉，也就没有 bearer 登录型管理员。
- `create_or_update_agent_for_user()` 不只是首次创建时才重要。用户重新提交问卷后，它会更新现有 `Agent.system_prompt_base`，并追加 `AGENT_PROFILE_UPDATED` 事件。
- `ensure_system_npc_agent()` 会在启动时补一个默认系统 NPC；`/api/users/npc-agents/from-senders` 会按外部 sender_id 创建或复用稳定的 NPC Agent，供群聊导入映射。
- `SystemSetting` 由 `/api/simulation/settings` 懒创建。普通前端页面默认使用 `global_active_branch`，只有管理员或 `allow_user_branch_switch=true` 时才开放手动切分支。
- `post_crud.create_post()` 和 `feedback_crud.create_feedback_log()` 都会同步追加 `EventLog`，分支广场显示是靠这些事件重建出来的。
- 广场纠错是“投影覆盖”而不是“原地修改”：`FeedbackLog`/`FEEDBACK_CREATED` 记录保存改写结果，渲染 feed 时按分支找到最新纠错文本覆盖展示，`posts` 表原始内容仍保留。
- `chat_crud.create_chat_log()` 保存聊天后还会追加带 `session_id`、`topic` 和 `experiment_mode` 的 `MESSAGE_RECEIVED` 事件；聊天历史页面读的是按分支/会话/话题过滤的有界 `ChatLog` 切片。
- `create_human_to_human_chat_log()` 会追加 `HUMAN_MESSAGE_RECEIVED`；`create_group_message_log()` 会追加 `GROUP_MESSAGE_RECEIVED`。这些记录都复用 `ChatLog`，所以只想读 Agent 私聊时必须过滤 `session_type`。
- 真人社交列表读取使用 `get_branch_read_windows()` 和 `branch_window_filter()`，fork 分支可以看到合适的主线历史，但不会把所有分支消息粗暴混在一起。
- `/api/social/events` 是前端实时通知主路径。`frontend/dev-server.mjs` 专门代理这个 SSE 长连接到 `BACKEND_ORIGIN`（默认 `http://127.0.0.1:8001`），因为普通 Next dev rewrite 对长连接不够可靠。
- `social_realtime.py` 会把通知推到本进程 SSE 队列；如果配置了 `LOOP_REDIS_URL`，还会用 Redis pub/sub 跨 worker fanout 并做近期事件去重。没有 Redis 时，多 worker 间实时同步会退化成本 worker 可见。
- 真人 1v1 和真人群聊每累计 20 条未处理的用户本人消息，会触发旁路 memory watcher；main 分支洞察直接合并 `User.core_memory`，非 main 分支写成分支 `CORE_MEMORY_UPDATED` 事件。
- `Group` CRUD 强制 Boundary 1：`HUMAN_ONLY` 拒绝 Agent 成员，`AGENT_ONLY` 拒绝 User 成员。管理员工具也不能绕开这个不变量。
- `POST /api/simulate/groups/{group_id}/tick` 每次只推进一个 `AGENT_ONLY` 群的一个 Agent 发言回合，并使用进程锁 + Postgres advisory lock 防重复。
- `POST /api/simulate/debate` 运行监督式 LangGraph 辩论；发言写成 `GROUP_MESSAGE_RECEIVED` 事件，最终主持人报告写成 `DEBATE_CONCLUDED`。这条路径目前不会为每条辩论发言创建 `ChatLog` 行。
- 私聊存储成功后，`extract_and_update_memory_background()` 还会在后台尝试从最新一轮对话里抽 durable 身份事实，并通过 `merge_core_memory_insight()` 写回 core memory。
- `/api/chat/{agent_id}/sessions` 会按某个分支下的 `session_id` 汇总聊天会话，供聊天页侧边栏显示。
- `DRIFT_DETECTED` 只在 `evaluate_drift_zero_shot()` 返回 `is_drifting=true` 后写入；模型不可用或跳过时不阻塞聊天保存。
- `/api/evaluations/blind-test/{agent_id}` 是给外部评价者使用的公开接口，随机返回最多 5 条聊天样本；提交接口写入 `Evaluation`，不要求参与者 bearer token。
- `/api/probes/status` 判断当前认证用户，或管理员选择的目标 Agent，是否需要本周 main 分支 IPIP-120 基线更新；`/api/probes/submit` 会保存 `ProbeResponse.branch_id`、计分、追加分支 `CORE_MEMORY_UPDATED`，并且只有 `main` 分支会真正写回 `User.big_five_scores` / `User.schwartz_values` / `User.core_memory` 和刷新 Agent 画像。
- `/api/counterfactuals/suggestions` 会基于数字自传和所选分支作用域（main 或 main + 当前分支）里的有界近期私聊/帖子文本生成候选锚点；如果素材还不够，会稳定返回空数组 `[]`。
- `/api/counterfactuals/submit` 是认证用户的身份记忆采集路径，不等同于 TimeMachine 的分支 fork。它会追加 `COUNTERFACTUAL_ANCHOR_CREATED` 和 `CORE_MEMORY_UPDATED`；只有 main 分支提交会直接持久写入 `User.core_memory.persona_traits`。
- `TimeMachine` 故意不把回放出来的完整聊天文本塞进 prompt 状态，它主要重建的是 compact state：规范化 core memory、反事实覆盖、关系分数，以及一段短 `current_core_memory`。
- `GET /api/agents/{agent_id}/events` 现在需要 bearer auth，并强制 owner 或 admin 才能读取某分支下的有界事件页。
- `POST /api/simulation/fork` 现在支持传入 `source_branch_id` 和可选的 `source_event_id`，会校验事件是否属于该分支血统链，从源分支重建状态，并把 `from_branch_id` / `parent_event_id` 写进 fork payload 方便追溯。
- `POST /api/agents/{agent_id}/import_chat` 现在是管理员接口，会校验每个 sender Agent id，按请求里的 `branch_id` 和可选批次级 `topic` 写入 target-agent-perspective pgvector 记忆，并调用 `sync_group_chat_memory_access()` 让参与群聊的 Agent 都能检索共享上下文。
- `DELETE /api/agents/{agent_id}` 会硬删除该 Agent 的事件、聊天、帖子、纠错、关系、反思、评估和 pgvector 记忆；如果删的是 NPC Agent，还会把背后的系统 user 一并删掉。
- 用户侧的记忆上传/搜索接口现在暴露 `branch_id`，仍然通过 pgvector metadata 和 `rag_service` 的 Git-style 分支过滤来读写，不依赖本地向量库目录。
- `extract_and_update_memory_background()` 可以把私聊抽取成带 branch metadata 的 RAG chunk，但只有 main 分支会把 durable 身份事实真正写回 `User.core_memory`；非 main 身份差异主要靠分支事件和 TimeMachine 重建表达。
- 关系加权的“个性化广场”逻辑已经落在 `post_crud.get_posts_for_viewer()` 和 `/api/agents/*/feed-preview` 两处，不要无意中把它们全部回退成纯时间倒序。

## 后端服务地图

这部分适合在你不知道“真实责任应该去哪找”时快速定位：

- `backend/app/main.py`：FastAPI 组装、middleware、router 挂载、`.env` 读取、建表和可选 RAG 预热。
- `backend/app/security.py`：bearer token、管理员 bearer / 机器 key、请求体大小限制、Redis 异步限流、安全响应头、trusted host。
- `backend/app/database.py`：SQLAlchemy engine/session、Postgres/pgvector 初始化、兼容性 schema 补齐、配置型管理员账号维护、`rag_documents` 表/索引创建，以及 `event_logs` append-only trigger。
- `backend/app/models.py`：核心研究数据模型，统一 second precision 时间戳，包含单例系统设置。
- `backend/app/services/event_store.py`：追加不可变 `EventLog` 的标准入口，负责 JSON-safe payload 和日志。
- `backend/app/services/branching.py`：分支 id 规范化、分支存在性、全局分支列表、父分支 lineage 查询、fork 锚点推导。
- `backend/app/services/time_machine.py`：按分支回放事件并重建 Agent 紧凑状态，是反事实实验的核心。
- `backend/app/services/core_memory_service.py`：Core Memory 规范化、prompt 格式化、显式修改和反思合并写回。
- `backend/app/services/tools.py`：Agent 在私聊中可调用的工具层。如果一个能力属于“Agent 主动感知/行动”，通常应放在这里。
- `backend/app/services/agent_graph.py`：LangGraph 运行时心智回路，管理 working memory、topic summaries、emotion/energy 和工具绑定。
- `backend/app/services/llm_service.py`：DeepSeek 请求参数、发帖生成、私聊生成、tool-calling 编排、fallback 路径、历史聊天按需加载。
- `backend/app/services/npc_seed.py`：系统默认 NPC 建种子，以及按外部 sender 生成稳定 NPC Agent。
- `backend/app/services/memory_watcher.py`：私聊结束后后台抽取 durable 身份事实并尝试并入 core memory。
- `backend/app/services/agent_cleanup_service.py`：Agent 级联删除，包括 SQL 记录和 pgvector 记忆清理。
- `backend/app/services/drift_detector.py`：zero-shot 身份一致性评估器，限制 prompt 上下文长度，并在 DeepSeek 不可用时安全跳过。
- `backend/app/services/infinity_client.py`：Infinity embedding/reranker 的共享异步 HTTP 客户端，负责重试和退避。
- `backend/app/services/rag_service.py`：Postgres/pgvector 持久化、Infinity embedding/reranker、chunking、hybrid retrieval、预热与严格模式。
- `backend/app/services/scoring_service.py`：IPIP-NEO-120 和 PVQ-21 计分、兼容旧版聚合分数输入，并把问卷画像合并回 core memory。
- `backend/app/services/access_control.py`：Agent-scoped probe/counterfactual 工作流的 owner/admin 权限解析。
- `backend/app/services/consolidation_service.py`：过去 24 小时记录收集、睡眠式巩固、关系更新、episodic memory 写入、working memory 清空。
- `backend/app/services/feedback_service.py`：帖子纠错后的反思与合并路径。
- `backend/app/services/rolling_summary.py`：群聊滚动摘要，按分支保存压缩上下文，避免长群聊 prompt 爆炸。
- `backend/app/services/speaker_manager.py`：`AGENT_ONLY` 群组的单回合发言者选择、发言生成和锁保护。
- `backend/app/services/debate_graph.py`：监督式多 Agent 辩论图、主持人路由、记忆检索和总结事件写入。
- `backend/app/services/social_realtime.py`：社交消息 SSE 通知 hub，可选 Redis pub/sub 跨 worker。
- `backend/app/services/ws_manager.py`：`/api/ws/social` 保留使用的轻量 WebSocket 连接管理。

## 事件类型速记

排查分支、时间机器或数据导出时，最常见的是这些事件类型：

- `SystemSetting` 变更不是时间线事件；它控制实验曝光，不代表模拟世界状态变化。
- `AGENT_CREATED`：用户首次生成 Agent。
- `AGENT_PROFILE_UPDATED`：用户重新提交问卷/资料后，Agent 基础 prompt 和画像更新。
- `POST_CREATED`：广场新帖。
- `FEEDBACK_CREATED`：用户对帖子提交纠错，feed 投影可能因此显示改写文本。
- `MESSAGE_RECEIVED`：一次私聊 turn 已保存。
- `HUMAN_MESSAGE_RECEIVED`：一次真人 1v1 社交消息已保存。
- `GROUP_MESSAGE_RECEIVED`：真人群聊、Agent-only 群聊回合或辩论式群消息已记录。
- `DEBATE_CONCLUDED`：监督式辩论图结束并写入主持人总结。
- `DRIFT_DETECTED`：zero-shot 评估器认为某个分支/会话的近期回复出现身份漂移。
- `CORE_MEMORY_UPDATED`：长期身份记忆发生变化，来源可能是工具调用或睡眠巩固。
- `COUNTERFACTUAL_ANCHOR_CREATED`：用户提交了人生决策反事实锚点，后续会进入 durable identity memory。
- `RELATIONSHIP_CHANGED`：Agent 间有向关系分数更新。
- `WORKING_MEMORY_CLEARED`：短期工作记忆被手动清空。
- `COUNTERFACTUAL_EVENT` 或自定义注入事件：某条非主分支时间线被注入了反事实干预。

## 核心数据流转 (Data Flow)

参与者建模链路：

```text
参与者注册/登录
  -> 前端 localStorage 保存 bearer session
  -> 提交问卷 + 数字自传
  -> 写入 User.core_memory 并创建/更新 Agent
  -> 后续所有广场、私聊、记忆、时间机器操作都围绕 agent_id + branch_id 展开
```

运行时事件链路：

```text
用户或 Agent 发起动作
  -> FastAPI router 校验 bearer/admin bearer/机器 key、权限和 branch_id
  -> SQLAlchemy 写入 Post/ChatLog/FeedbackLog/Relationship 等业务表
  -> event_store 追加 EventLog，不 update/delete 时间线
  -> 分支读模型按 branch lineage 过滤或由 TimeMachine 重放
  -> 前端分页视图渲染当前 branch 的广场、聊天、记忆诊断或事件历史
```

记忆学习链路：

```text
数字自传 / 记忆上传 / 群聊导入 / 私聊 / 帖子纠错
  -> Postgres `rag_documents` + User.core_memory + ChatLog/FeedbackLog
  -> Agentic Memory 工具主动检索或更新目标记忆
  -> DeepSeek/tool-calling chat 或自动发帖使用分支状态 + 主动检索记忆
  -> sleep consolidation 和 feedback reflection 继续沉淀高层反思与关系分数
```

真人社交 / 群聊链路：

```text
真人 1v1 / 真人群聊 / Agent-only 群组仿真
  -> ChatLog 写入 session_type + branch_id + sender/receiver/group metadata
  -> EventLog 追加 HUMAN_MESSAGE_RECEIVED 或 GROUP_MESSAGE_RECEIVED
  -> SSE/Redis fanout 在可用时推送实时通知
  -> 旁路 memory watcher 或 rolling summary 压缩高频社交上下文
```

Probe 与反事实身份锚点链路：

```text
IPIP/PVQ probe 或人生反事实锚点提交
  -> ProbeResponse / EventLog + User.core_memory
  -> scoring_service 计分或 counterfactual anchor 合并
  -> Agent.system_prompt_base 刷新，后续 chat/post 使用更新后的身份画像
```

分支实验与导出链路：

```text
TimeMachine 在某个 EventLog 时间点重建 Agent 状态
  -> fork 写入 counterfactual event 到新 branch
  -> Plaza/Chat/Memory Lab/TimeMachine 通过 BranchSelector 切换分支
  -> Lab 导出 ChatLog/FeedbackLog JSONL 进入研究分析
```

盲测评估链路：

```text
研究者分享 /evaluations/{agent_id}
  -> 外部评价者阅读随机抽样的 ChatLog 对话片段
  -> 提交与被试关系、1-5 分真实性评分、可选文字反馈
  -> Evaluation 表沉淀 M6 Friend Turing Test 证据
```

## 主要后端接口

用户与 Agent：

```text
POST /api/users/register
POST /api/users/login
GET  /api/users/me
GET  /api/users/directory
POST /api/users/npc-agents/from-senders            管理员 bearer
POST /api/users/me/questionnaire
POST /api/users/{user_id}/questionnaire
GET  /api/users/me/agent
GET  /api/users/{user_id}/agent
GET  /api/users/agent-choices                     管理员 bearer
POST /api/users/agent-choices/{agent_id}/session  管理员 bearer
GET  /api/agents/directory                        管理员 bearer
DELETE /api/agents/{agent_id}                     owner/admin bearer，破坏性
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
GET  /api/chat/{agent_id}/sessions
POST /api/chat/{agent_id}/check-drift
```

真人社交与群组：

```text
POST /api/groups
POST /api/groups/{group_id}/members
POST /api/groups/{group_id}/messages
GET  /api/social/events                           bearer token query，SSE
GET  /api/social/contacts
GET  /api/social/messages/{contact_id}
POST /api/social/messages/{contact_id}/read
POST /api/social/messages
POST /api/social/groups
GET  /api/social/groups
GET  /api/social/groups/{group_id}/messages
POST /api/social/groups/{group_id}/messages
WS   /api/ws/social                               bearer token query
```

盲测评估：

```text
GET  /api/evaluations/blind-test/{agent_id}
POST /api/evaluations/blind-test/{agent_id}/submit
```

Probe 与反事实锚点：

```text
GET  /api/probes/status
POST /api/probes/submit
GET  /api/counterfactuals/suggestions
POST /api/counterfactuals/submit
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
POST /api/simulate/user/{username}/post   管理员 bearer 或机器 key
POST /api/simulate/agent/{agent_id}/post  管理员 bearer 或机器 key
POST /api/simulate/debate                 管理员 bearer 或机器 key
POST /api/simulate/groups/{group_id}/tick 管理员 bearer 或机器 key
POST /api/simulate/tick                   管理员 bearer 或机器 key
GET  /api/agents/{agent_id}/events
GET  /api/simulation/settings
PATCH /api/simulation/settings            管理员 bearer
GET  /api/simulation/branches
GET  /api/simulation/agents/{agent_id}/branches
POST /api/simulation/fork
POST /api/admin/purge-branch                         管理员 bearer 或机器 key，破坏性，非 main 分支
GET  /api/export/{user_id}/chatlogs                 管理员 bearer
GET  /api/export/by-username/{username}/chatlogs   管理员 bearer
GET  /api/export/{user_id}/feedbacks                管理员 bearer
GET  /api/export/by-username/{username}/feedbacks  管理员 bearer
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
LOOP_CHAT_ENGINE=tool_calling
LOOP_LLM_TIMEOUT_SECONDS=8
LOOP_POST_LLM_TIMEOUT_SECONDS=20
LOOP_CHAT_LLM_TIMEOUT_SECONDS=25
LOOP_DEEP_CHAT_LLM_TIMEOUT_SECONDS=60
LOOP_CHAT_MAX_TOKENS=600
LOOP_DEEP_CHAT_MAX_TOKENS=1200
LOOP_POST_MAX_TOKENS=360
LOOP_VECTOR_RAG_ENABLED=true
LOOP_RERANKER_ENABLED=true
LOOP_RAG_STRICT=true
LOOP_RAG_PRELOAD=true
LOOP_EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
LOOP_RERANKER_MODEL=BAAI/bge-reranker-large
LOOP_EMBEDDING_BASE_URL=http://127.0.0.1:7997
LOOP_RERANKER_BASE_URL=http://127.0.0.1:7998
LOOP_INFINITY_TIMEOUT_SECONDS=30
LOOP_INFINITY_RETRIES=3
LOOP_INFINITY_RETRY_BACKOFF_SECONDS=0.5
LOOP_EMBEDDING_DIM=1024
LOOP_CORE_MEMORY_INTENT_LLM_ENABLED=true
LOOP_TOPIC_ROUTER_LLM_ENABLED=true
LOOP_DRIFT_JUDGE_MODEL=deepseek-chat
LOOP_DRIFT_JUDGE_TIMEOUT_SECONDS=12
LOOP_DRIFT_JUDGE_MAX_TOKENS=360
LOOP_DRIFT_JUDGE_THINKING=disabled
LOOP_DRIFT_JUDGE_REASONING_EFFORT=high
LOOP_CONSOLIDATION_LLM_TIMEOUT_SECONDS=60
LOOP_CONSOLIDATION_LLM_MAX_RETRIES=0
LOOP_DEBATE_MODEL=deepseek-chat
LOOP_DEBATE_LLM_TIMEOUT_SECONDS=25
LOOP_DEBATE_MAX_TOKENS=900
LOOP_ADMIN_API_KEY=choose_a_private_admin_key
LOOP_ADMIN_USERNAME=loop_research_admin
LOOP_ADMIN_PASSWORD=choose_a_private_admin_password
LOOP_AUTH_SECRET=choose_a_stable_token_signing_secret
POSTGRES_USER=loop_admin
POSTGRES_PASSWORD=choose_a_private_postgres_password
POSTGRES_DB=loop_research
LOOP_REDIS_PASSWORD=choose_a_private_redis_password
LOOP_REDIS_URL=redis://:password@127.0.0.1:6379/0
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

如果没有 `DEEPSEEK_API_KEY`，`/api/simulate/*` 自动发帖会显式失败并返回服务端错误，避免研究运行静默变成 Mock；私聊路径会尽量返回本地 memory-aware fallback；漂移检测会安全跳过并返回非阻塞提示；记忆/巩固路径根据具体服务可能 fallback 或 503。

仓库里的 `.env.example` 现在更像一个“最小可启动基线”：它包含 `make infra` 需要的核心 Postgres / Redis 变量和 bearer 管理员账号变量，但 Infinity 仍使用旧的 `INFINITY_EMBEDDING_URL` / `INFINITY_RERANKER_URL` 命名，也没有把上面那批可选调优参数全部展开。`rag_service.py` 同时兼容旧名和推荐的 `LOOP_EMBEDDING_BASE_URL` / `LOOP_RERANKER_BASE_URL`。

私聊实验模式使用中性标签：`mode_alpha` 表示完整 active-memory/IACL 路径，`mode_beta` 表示静态 prompt 基线。旧别名 `full_iacl` 和 `static_prompt` 会在后端归一化到这两个标签。

Agent-only 群组单回合发言使用 `DEEPSEEK_CHAT_MODEL` 和普通聊天 thinking 设置；监督式辩论优先使用 `LOOP_DEBATE_MODEL`、`LOOP_DEBATE_LLM_TIMEOUT_SECONDS`、`LOOP_DEBATE_MAX_TOKENS`，未配置时回退到 `DEEPSEEK_CHAT_MODEL`。

## 前端技术栈

- Next.js 14 App Router
- React 18
- TypeScript 5
- Tailwind CSS 3
- Next.js rewrites：浏览器访问前端同源 `/api/*`，由 Next.js 服务端转发到 FastAPI。当前 `next.config.mjs` 的 REST rewrite 目标硬编码为 `http://127.0.0.1:8001/api/:path*`，后端端口变更时要同步改。
- 自定义 `frontend/dev-server.mjs`：包装 Next dev，并单独代理 `/api/social/events` 到 `BACKEND_ORIGIN`（默认 `http://127.0.0.1:8001`），用于保持 SSE 长连接稳定。
- Next.js middleware：在进入应用页面前做站点级访问验证。
- Route Handler：`src/app/site-auth/login/route.ts` 处理站点登录并设置 HTTP-only cookie。
- 前端中英双语：`AppProviders` 注入 `LanguageProvider`，`LanguageToggle` 切换语言，文案集中在 `src/locales/dictionary.ts`，选择保存在 `loop_ui_language`。
- `localStorage`：保存参与者/管理员 bearer token、用户 id、用户名、Agent id/name、`agent_is_npc`，以及管理员代入用户时的备份 session。

## 前端运行

```bash
make frontend
```

`frontend/.env.local` 建议：

```env
NEXT_PUBLIC_API_BASE_URL=
BACKEND_INTERNAL_API_BASE_URL=http://127.0.0.1:8001
```

保持 `NEXT_PUBLIC_API_BASE_URL` 为空，浏览器就会请求当前前端 origin 的 `/api/...`。当前 `next.config.mjs` 会把 REST `/api/:path*` rewrite 到 `http://127.0.0.1:8001/api/:path*`；`dev-server.mjs` 另用 `BACKEND_ORIGIN` 代理 `/api/social/events` SSE。这样用户电脑只需要通过 SSH tunnel 访问 `localhost:3000`，不需要浏览器直连远程服务器的 8001。

注意：当前 rewrite 只覆盖 `/api/*`，不覆盖 `/health`。`/lab` 的健康检查按钮现在调用 `apiRequest("/health")`；在同源代理模式下需要临时设置 `NEXT_PUBLIC_API_BASE_URL` 指向后端，或后续给 Next.js 增加 `/health` rewrite。

修改 `frontend/.env.local`、`frontend/dev-server.mjs` 或 `frontend/next.config.mjs` 后都要重启 Next.js dev server。

另一个容易漏掉的点：后端 CORS 中间件当前允许 `GET`、`POST`、`DELETE`、`OPTIONS`。`PATCH /api/simulation/settings` 在 Next.js 同源代理下可用；如果让浏览器直接跨源访问 FastAPI，需要同步补上 `PATCH` CORS method。

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

提交后调用 `/api/users/me/questionnaire`，后端会更新 User 并创建或更新 Agent。配置型管理员登录后会直接进入 `/lab`；如果当前保存的是管理员 bearer session，首页也会显示 Agent session switching 功能，可以列出现有 Agent，并为某个 Agent 所属用户创建临时会话，方便研究者切换身份测试。

### `/plaza` 公共广场

文件：`frontend/src/app/plaza/page.tsx`

这是类似社交 feed 的页面。它会读取当前 session，补齐当前用户的 Agent 信息，然后加载分支列表和当前分支的广场事件。

主要功能：

- 通过 `BranchSelector` 在 `main` 和 fork 出来的分支之间切换。
- 读取 `/api/simulation/settings`；普通用户默认固定在 `global_active_branch`，只有管理员或 `allow_user_branch_switch=true` 时才开放分支切换。
- 调用 `/api/plaza/events?branch_id=...&skip=...&limit=...` 分页加载分支继承后的帖子列表。
- 首屏只加载 `PLAZA_PAGE_SIZE` 条，点击“加载更多”后按 `posts.length` 作为 skip 继续取下一页，并对已有 post id 去重，避免长实验 feed 一次性爆内存。
- 当前用户可以通过 `/api/agents/me/posts` 手动发帖到当前分支。
- 如果帖子来自当前用户自己的 Agent，会显示纠错按钮。
- 纠错会调用 `/api/posts/{post_id}/feedback`，后端保存 `FeedbackLog` 并尝试触发反馈反思合并。

时间展示由 `src/lib/time.ts` 处理：后端时间是 UTC，前端会把没有时区后缀的时间当作 UTC 再转成本地时间。

### `/chat` 多会话私聊

文件：`frontend/src/app/chat/page.tsx`

这是用户和自己 Agent 的私密聊天页。页面会自动加载当前 Agent、分支列表、当前分支下的会话侧边栏，以及当前会话的聊天历史。

主要功能：

- 读取 `/api/simulation/settings`；普通用户默认固定在 `global_active_branch`，只有管理员或 `allow_user_branch_switch=true` 时才开放分支切换。
- 分支选择：`/api/simulation/agents/{agent_id}/branches`
- 会话列表：`GET /api/chat/{agent_id}/sessions?branch_id=...`
- 历史加载：`GET /api/agents/{agent_id}/chat?branch_id=...&session_id=...&skip=...&limit=...`
- 历史分页：首屏加载 `CHAT_HISTORY_PAGE_SIZE` 个 turn，更早消息点击按钮再取；插入旧消息时保留滚动锚点，避免聊天窗口跳动。
- 发送消息：`POST /api/agents/{agent_id}/chat`
- 模型选择：`fast` 或 `deep`
- 实验模式选择：`mode_alpha` 完整 IACL，`mode_beta` 静态 prompt 基线
- 话题选择：支持 `general`、`daily_life`、`relationships`、`work`、`identity` 等 topic 桶，并会持久化当前话题。
- `mode_alpha` 回复后会触发漂移检测；如果发现核心人格漂移，页面会要求用户填写“不像我在哪里”和“真实我会怎么说”，再发起强制校准。

后端 `mode_alpha` 聊天会结合身份 prompt、core memory、RAG 检索结果、最近会话历史、当前分支重建状态，并把新聊天写入 `ChatLog` 和 `EventLog`。`mode_beta` 只使用初始问卷人格摘要，不使用 RAG、工具、历史或自我更新记忆。

### `/social` 真人社交空间

文件：`frontend/src/app/social/page.tsx`、`frontend/src/components/social/H2HChatPanel.tsx`、`frontend/src/components/social/HumanGroupPanel.tsx`

这是分支感知的真人参与者社交页面。

主要功能：

- 读取 `/api/simulation/settings`；管理员可切换分支，普通用户遵守 `global_active_branch` / `allow_user_branch_switch`。
- 真人 1v1：`GET /api/social/contacts` 加载通讯录和未读数，`GET /api/social/messages/{contact_id}` 分页加载历史，`POST /api/social/messages` 发送，`POST /api/social/messages/{contact_id}/read` 标记已读。
- 真人群聊：`POST /api/social/groups` 创建 `HUMAN_ONLY` 群，`GET /api/social/groups` 分页列出群，`GET/POST /api/social/groups/{group_id}/messages` 读写群消息。
- 实时通知：前端用 `EventSource` 连接 `/api/social/events?token=...`；开发服务器会专门代理该 SSE 长连接。
- UI 会做 optimistic pending message 合并，但最终仍以 REST 持久化后的 `ChatLog` 为准。
- 这些真人消息会进入 `ChatLog` 和 `EventLog`，并可能触发旁路 memory watcher，把真实社交中的稳定表达风格沉淀成身份记忆。

### `/sandbox` 推演沙盘

文件：`frontend/src/app/sandbox/page.tsx`、`frontend/src/components/sandbox/DebatePanel.tsx`、`frontend/src/components/sandbox/GroupSimulationPanel.tsx`

这是管理员专用的仿真工作台，非管理员会被重定向。

主要功能：

- 加载分支列表并选择目标分支。
- 通过 `/api/agents/directory` 选择 Agent。
- 触发监督式多 Agent 辩论：`POST /api/simulate/debate`，返回已执行回合数、是否达成共识和 final report。
- 创建 Boundary-1 群组：`POST /api/groups`，可选 `HUMAN_ONLY` 或 `AGENT_ONLY`。
- 添加群成员：`POST /api/groups/{group_id}/members`，由后端校验 `USER` / `AGENT` 是否符合群类型。
- 手动推进 Agent-only 群组一个发言回合：`POST /api/simulate/groups/{group_id}/tick`。

### `/probes` 每周验证问卷

文件：`frontend/src/app/probes/page.tsx`

这是认证参与者的 IPIP-120/PVQ-21 probe 采集页，用于 M1-M6 验证基线更新。

主要功能：

- 从 `frontend/src/data/questionnaires.json` 加载 IPIP-120 和 PVQ-21 题目。
- 未登录用户会被重定向到 `/`。
- 读取全局分支设置；管理员可以选择目标 Agent，并通过 `agent_id` 为目标 Agent 提交验证数据。
- 提交到 `POST /api/probes/submit`。
- 后端保存带 `branch_id` 的 `ProbeResponse`，计算 Big Five 和 Schwartz 维度，追加分支 `CORE_MEMORY_UPDATED`；只有 main 分支会把计分画像持久合并进 `User.core_memory` 并刷新 `Agent.system_prompt_base`。

### `/counterfactuals` 人生反事实锚点

文件：`frontend/src/app/counterfactuals/page.tsx`

这是认证参与者提交人生决策反事实锚点的页面，用于补强 durable identity memory。

主要功能：

- 读取全局分支设置；管理员可以选择目标 Agent，并通过 `agent_id` 为目标 Agent 提交反事实锚点。
- 先从 `GET /api/counterfactuals/suggestions` 加载 AI 推荐的人生决策锚点卡片。
- 收集真实决策背景、可选的现实选择/现实结果、反事实选择、假设结果。
- 提交到 `POST /api/counterfactuals/submit`。
- 后端追加 `COUNTERFACTUAL_ANCHOR_CREATED` 和 `CORE_MEMORY_UPDATED`，并把锚点写入 `persona_traits`；只有 main 分支提交会直接写入 `User.core_memory`。
- 该页面已在 `NavBar` 中作为“人生如果 / Counterfactuals”入口。

### `/evaluations/[agent_id]` 公开盲测评估

文件：`frontend/src/app/evaluations/[agent_id]/page.tsx`

这是给外部评价者访问的 M6 盲测页面，不要求站点登录 cookie。

主要功能：

- 加载随机抽样对话片段：`GET /api/evaluations/blind-test/{agent_id}`
- 评价者选择与被试关系：朋友、同事、伴侣、亲属、其他
- 评价 1-5 分“像本人程度”，并可填写定性反馈
- 提交到 `POST /api/evaluations/blind-test/{agent_id}/submit`

### `/import` 群聊导入

文件：`frontend/src/app/import/page.tsx`

这个页面用于导入 JSON、TXT 或 HTML 格式的群聊记录，帮助系统理解“我”和“别人”的对话语境。当前 UI 和后端都要求管理员 bearer session。

如果是 JSON，前端期望根节点是数组，每条记录至少包含：

```json
{
  "sender_id": "alice",
  "content": "message text",
  "timestamp": "optional timestamp"
}
```

页面会在浏览器端解析文件，支持按日期范围筛选，并为整批导入附加一个可选 branch 和 topic 标签。随后统计 sender_id，让研究者把每个 sender 映射到已有 Agent id；如果遇到还没有对应 Agent 的外部 sender，也可以用管理员 bearer 调 `/api/users/npc-agents/from-senders` 批量创建稳定 NPC 再继续映射。映射完成后提交到 `/api/agents/me/import_chat`。后端会按目标 Agent 视角写入向量记忆，区分自己说的话和别人说的话，并同步共享群聊检索权限。

### `/memory` 记忆金库 / Memory Lab

文件：`frontend/src/app/memory/page.tsx`

这是记忆系统的综合测试台，适合研究者观察 Agent 记忆机制是否生效。

主要功能：

- 上传长文本记忆：`POST /api/users/me/memory/upload`
- 语义搜索记忆：`POST /api/users/me/memory/search`
- 读取全局分支设置；分支选择会进入上传/搜索 payload，也影响 working memory/core memory 重建。
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

- 加载 Agent 列表：`/api/users/agent-choices`，需要管理员 bearer session。
- 加载分支列表：`/api/simulation/agents/{agent_id}/branches`
- 加载事件历史：`/api/agents/{agent_id}/events?branch_id=...&skip=...&limit=...`
- 事件分页：每页 `EVENT_PAGE_SIZE` 条，使用“加载更早事件”继续取历史，避免时间线过长时卡死浏览器。
- 创建新分支：`POST /api/simulation/fork`

fork 时需要提供：

- `agent_id`
- `source_branch_id`
- `source_event_id`
- `rollback_timestamp`
- `new_branch_name`
- `counterfactual_event`

后端会从当前选中的源分支重建 rollback 时间点状态，校验事件节点是否属于该分支的 lineage，并把反事实事件写入新分支的 `EventLog`。之后广场、聊天、记忆诊断都可以选择这个分支。

### `/lab` 研究者控制台

文件：`frontend/src/app/lab/page.tsx`

这是集中测试和导出的后台页面。

主要功能：

- 健康检查：`GET /health`。注意当前 Next.js rewrite 只覆盖 `/api/*`，所以同源模式下这个按钮需要 `NEXT_PUBLIC_API_BASE_URL` 指向后端，或未来补一条 `/health` rewrite。
- 加载 Agent 列表并选择目标用户/Agent。
- 选择目标分支。
- 更新普通参与者可见的全局分支设置：`PATCH /api/simulation/settings`。
- 对某个用户名触发一次自动发帖：`POST /api/simulate/user/{username}/post`
- 对所有 Agent 触发一次 tick：`POST /api/simulate/tick`
- 导出 chatlogs JSONL：`GET /api/export/by-username/{username}/chatlogs`
- 导出 feedbacks JSONL：`GET /api/export/by-username/{username}/feedbacks`
- 删除单个 Agent 及其痕迹：`DELETE /api/agents/{agent_id}`
- 危险区域清理非 `main` 分支数据：`POST /api/admin/purge-branch`

这个页面的导出、Agent 切换、Agent 删除、全局分支设置等 UI 功能需要管理员 bearer session；模拟和分支清理后端也支持机器 `X-Loop-Admin-Key`。删除 Agent 或清理分支都会移除运行数据且不可恢复，只应在明确知道后果时使用。

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

登录成功后，route handler 生成 HMAC 签名 token，写入 HTTP-only cookie。`/site-login` 和 `/site-auth/*` 是公开路径，其他页面都会被 middleware 保护。`/evaluations/*` 和 `/api/evaluations/*` 也故意保持公开，用于向外部评价者分享盲测链接。

## 前端共享模块

- `src/lib/api.ts`：集中定义 API 类型和 `apiRequest<T>()`。会自动读取 `loop_session` 里的 token 并加上 `Authorization`。
- `src/lib/session.ts`：封装 `saveSession`、`loadSession`、`getAccessToken`、`clearSession`。过期 token 会自动从 localStorage 移除。
- `src/lib/siteAuth.ts`：站点级 cookie 签名、过期时间、常量时间比较。
- `src/lib/time.ts`：UTC 时间解析、本地时间格式化、feed 相对时间。
- `src/data/questionnaires.json`：`/probes` 页面使用的 IPIP/PVQ probe 题目。
- `src/locales/dictionary.ts`：中英双语文案；新增页面或按钮时应同步补齐 `zh` 和 `en`。
- `src/components/LanguageContext.tsx` / `LanguageToggle.tsx`：语言状态和切换控件。
- `src/components/NavBar.tsx`：全局顶部导航；`/site-login` 只保留紧凑 Loop 标题和语言切换，不显示完整应用导航。其余页面桌面端按“日常交互 / 实验与管理”分组，移动端收进汉堡菜单。
- `src/components/BranchSelector.tsx`：多个页面复用的分支选择控件。
- `src/components/TimeMachinePanel.tsx`：时间机器完整交互逻辑。

## 改动前自检清单

修改核心逻辑前后，至少确认这些不变量仍然成立：

- 所有分支相关读路径都继续使用 `normalize_branch_id()`，并正确尊重父分支 lineage 或 fork anchor。
- 任何会影响模拟状态或分支重建的新写路径，都有对应 `EventLog` 追加。
- 新增管理员工作流时，UI/bearer 管理员操作优先用 `require_admin`；只有真正需要机器自动化调用时才用 `require_admin_or_machine_key`。
- 广场、聊天、事件历史接口仍然维持分页和有界 `limit`。
- `.env` 里的敏感值不会进入日志、响应、截图或 Git 提交。
- 本地开发服务仍然绑定 `127.0.0.1`。
- 前端新增 API 路径要么走现有 `/api/*` rewrite，要么像 `/health` 一样在文档里明确写清楚特殊性。

## 典型完整测试流程

1. 启动基建：`make infra`。
2. 启动 FastAPI：`make backend`，监听 `127.0.0.1:8001`。
3. 启动 Next.js：`make frontend`，监听 `127.0.0.1:3000`。
4. 本地电脑通过 SSH tunnel 打开 `http://localhost:3000`。
5. 如果启用了站点访问验证，先通过 `/site-login`。
6. 注册或登录参与者账号。
7. 填写问卷和数字自传，生成 Agent。
8. 进入 `/plaza`。
9. 使用配置型管理员账号登录，进入 `/lab`，在目标分支触发一次 `simulate tick`。
10. 回到 `/plaza`，确认帖子出现。
11. 对自己的 Agent 帖子提交纠错。
12. 进入 `/chat`，发送消息，确认 Agent 回复并保存历史。
13. 在 `/chat` 新建一个会话，切换 `mode_alpha` / `mode_beta`，确认历史按会话隔离。
14. 进入 `/social`，发送一条真人 1v1 消息，再创建一个真人群聊并发送群消息，刷新后确认消息仍在。
15. 使用管理员进入 `/sandbox`，运行一次小规模多 Agent 辩论，或创建 `AGENT_ONLY` 群并手动 tick 一个发言回合。
16. 进入 `/probes`，提交当前 IPIP/PVQ probe，确认 Agent 画像仍可加载。
17. 进入 `/counterfactuals`，提交一条人生反事实锚点，确认 memory 诊断中 core memory 有更新。
18. 用新浏览器上下文打开 `/evaluations/{agent_id}`，提交一次盲测评分。
19. 进入 `/memory`，上传记忆、搜索记忆、触发睡眠巩固、查看诊断。
20. 进入 `/time-machine`，加载事件，选择一个事件 fork 新分支。
21. 回到 `/plaza` 或 `/chat`，切换到新分支观察差异。
22. 在 `/lab` 导出 chatlogs 或 feedbacks JSONL。

## 常用验证命令

前端类型检查：

```bash
make frontend-check
```

后端 Python 编译检查：

```bash
make backend-check
```

后端健康检查：

```bash
make health
```

Next.js 代理检查：

```bash
make proxy-check
```

## 常见问题排查

- 注册失败：先检查 `/health`，再检查 `frontend/next.config.mjs` 的 REST rewrite 目标、`frontend/dev-server.mjs` 的 `BACKEND_ORIGIN` 默认值，以及 `frontend/.env.local` 的 `NEXT_PUBLIC_API_BASE_URL` 是否为空。
- 浏览器 CORS 报错：检查根目录 `.env` 的 `BACKEND_CORS_ORIGINS` 是否包含浏览器实际访问的 origin。
- `/api/*` 代理失败：修改 `frontend/.env.local`、`frontend/next.config.mjs` 或 `frontend/dev-server.mjs` 后需要重启 Next.js。
- 管理员功能 403：先确认当前 `loop_session` 是否来自 `LOOP_ADMIN_USERNAME` / `LOOP_ADMIN_PASSWORD` 配置的管理员账号；simulation/purge 这类机器接口再检查 `X-Loop-Admin-Key` 和 `LOOP_ADMIN_API_KEY`。
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
- `model_cache/`
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
