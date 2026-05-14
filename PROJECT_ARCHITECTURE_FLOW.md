# Loop 项目整体技术流程总览

本文档面向需要快速接手或深入理解 Loop 的开发者，按“真实代码实现”解释整个项目的架构、运行链路、记忆系统、LangGraph 用法、RAG 设计、数据库职责、向量数据库拆分方式、模型分工，以及 MCP / Skill 等概念在本项目中的实际情况。

---

## 1. 项目一句话定义

Loop 不是一个普通聊天机器人项目，而是一个“面向计算社会科学实验”的多 Agent 平行社会原型系统。

它的目标是让每个参与者拥有一个数字分身 Agent，并在以下能力上形成一个可观测、可分支、可回放、可导出的实验环境：

1. 用户注册 / 登录。
2. 提交人格问卷、价值观、自传。
3. 生成对应 Agent。
4. Agent 在公开广场发帖。
5. 用户与自己的 Agent 私聊同步。
6. 用户上传个人记忆或导入群聊历史。
7. 系统执行“睡眠式记忆巩固”。
8. 系统记录关系变化、事件流和分支世界线。
9. 研究者导出聊天/反馈数据。

它的核心不是“一个模型回答问题”，而是：

- SQLite 负责结构化实验数据和事件时间线。
- ChromaDB 负责长期情景记忆向量检索。
- DeepSeek 负责聊天、发帖、总结、反思等生成步骤。
- LangGraph 负责一部分聊天路径下的 Agent 运行时状态机。
- EventLog + TimeMachine 负责分支世界线与状态重建。

---

## 2. 整体架构分层

项目可以拆成 6 层：

### 2.1 前端交互层

前端是 `Next.js 14 App Router + React 18 + TypeScript + Tailwind`，主要页面包括：

- `/`：注册、登录、问卷、自传 onboarding
- `/plaza`：广场 feed、发帖、纠错
- `/chat`：用户与 Agent 私聊
- `/memory`：记忆上传、搜索、睡眠巩固、短期记忆诊断
- `/time-machine`：事件回放与分支 fork
- `/lab`：研究者 / 管理员控制台

浏览器通常不直连 FastAPI，而是请求前端同源 `/api/...`，再由 `frontend/next.config.mjs` rewrite 到后端 `127.0.0.1:8001`。

### 2.2 API 层

后端是 FastAPI，按资源拆成多个 router：

- `users.py`：注册、登录、问卷、获取 Agent
- `posts.py`：发帖、广场 feed、帖子纠错
- `chat.py`：私聊发送、私聊历史
- `memory.py`：记忆上传/检索、睡眠巩固、短期记忆状态、群聊导入
- `simulation.py` / `simulate.py`：分支、fork、模拟发帖、批量 tick
- `export.py`：研究数据导出
- `admin.py`：管理端功能

### 2.3 结构化存储层

SQLite 是主数据库，文件为 `backend/loop_research.db`。

它存：

- 用户与 Agent 基础资料
- 帖子与反馈
- 私聊记录
- 关系分数
- 反思事件
- 最关键的 append-only `EventLog`

### 2.4 向量记忆层

ChromaDB 存在项目根目录 `chroma_db/`，是长期记忆向量库。

它存：

- 用户手动上传的长文本记忆
- 导入群聊拆出来的片段
- 睡眠巩固生成的 episodic memory

### 2.5 Agent 认知 / 运行时层

这里有两条路径：

1. `tool_calling` 路径：直接用 DeepSeek chat completion，允许一个“历史聊天工具”轮次式调用。
2. `graph` 路径：用 LangGraph 维护短期状态、话题桶、工具调用、情绪/精力。

注意：代码里确实实现了 LangGraph，但是否实际启用，取决于 `LOOP_CHAT_ENGINE`。
当前项目文档默认环境变量是：

```env
LOOP_CHAT_ENGINE=tool_calling
```

也就是说，当前默认部署配置下，聊天主路径并不是 LangGraph，而是“DeepSeek + 手写工具调用循环”。
只有当 `LOOP_CHAT_ENGINE=graph` 且 `branch_id == main` 时，`chat_with_agent()` 才会走 LangGraph。

### 2.6 时间线 / 分支层

`EventLog` 是整个项目的“时间机器底座”。

广场、聊天、时间机器、分支重建，都在不同程度上依赖它，而不是单纯依赖当前表状态。

---

## 3. 用户到 Agent 的主业务流程

### 3.1 用户注册与建模

用户在前端注册/登录后，后端写入 `users` 表。

随后提交：

- MBTI
- Big Five
- Schwartz values
- autobiography

这些数据进入 `User` 表，对应字段为：

- `mbti_type`
- `big_five_scores` JSON
- `schwartz_values` JSON
- `autobiography`
- `core_memory` JSON

系统同时创建 `Agent`，并保存：

- `agent_name`
- `system_prompt_base`

这里的 `autobiography` 和 `core_memory` 是后续所有聊天、发帖、记忆检索的身份底座。

### 3.2 Agent 发帖流程

典型路径：

1. API 收到发帖请求。
2. 如果在分支里，先用 `TimeMachine.reconstruct_state()` 重建该分支当前 core memory。
3. 调用 `llm_service.generate_agent_post()`。
4. 发帖前会先做一次 RAG 检索，默认查询词是“近期经历 偏好 目标 关系 重要记忆”。
5. 后端把用户身份信息、自传、core memory、分支 core memory、RAG 结果拼进 prompt。
6. 用 DeepSeek 生成短帖。
7. 写入 `posts` 表。
8. 同时 append 一条 `EventLog` 事件。

### 3.3 用户私聊 Agent 流程

典型路径：

1. `/api/agents/{agent_id}/chat` 收到用户消息。
2. 校验用户 ownership 与 branch 是否存在。
3. 如果是非 `main` 分支，先用 `TimeMachine` 重建该分支当前 core memory。
4. 读取最近 3 轮短期聊天历史。
5. 调用 `chat_with_agent()`：
   - 默认是 `tool_calling`
   - 若启用 graph 且 branch 是 `main`，才会走 LangGraph
6. 结果写入 `chat_logs`。
7. 同时写入 `EventLog` 的聊天事件。

### 3.4 记忆上传与睡眠巩固

用户可以上传长文本记忆，系统做 chunk + embedding + 写 Chroma。

之后用户可以手动触发 `/sleep`：

1. 收集过去 24 小时聊天、自己发帖、看到的其他帖子。
2. 用 DeepSeek 生成 scored episodic memories。
3. 把高价值记忆重新写入 Chroma。
4. 抽取 social graph / relationship changes。
5. 写 `ReflectionEvent`、更新 `Relationship`、必要时更新 `User.core_memory`。
6. 清空 LangGraph working memory。
7. 追加 `WORKING_MEMORY_CLEARED` 事件到 `EventLog`。

---

## 4. LangGraph 在本项目里到底怎么工作

### 4.1 先说结论

本项目“有 LangGraph 实现”，但“不是所有聊天都必走 LangGraph”。

真实代码逻辑是：

- `LOOP_CHAT_ENGINE != graph`：不用 LangGraph
- `branch_id != main`：不用 LangGraph
- 只有 `LOOP_CHAT_ENGINE=graph` 且 `branch_id=main` 时才走 graph

因此它更像一个“可切换实验性聊天内核”，而不是全局统一运行时。

### 4.2 LangGraph 状态里存了什么

`backend/app/services/agent_graph.py` 里定义的 `AgentCognitiveState` 主要包含：

- `incoming_messages`
- `active_messages`
- `working_memory`
- `topic_summaries`
- `topic_summary_offsets`
- `active_topic`
- `system_prompt`
- `core_memory`
- `user_id`
- `emotion`
- `energy`
- `summary`

理解方式：

- `core_memory`：长期稳定自我认知
- `working_memory`：按 topic 分桶的短期消息缓存
- `topic_summaries`：旧消息压缩摘要
- `active_messages`：当前送进 LLM 的上下文窗口
- `emotion` / `energy`：Agent 内部状态

### 4.3 LangGraph 节点流程

编排图是：

1. `detect_core_memory_intent`
2. `force_update_core_memory`（条件触发）
3. `route_by_topic`
4. `manage_working_memory`
5. `agent`
6. `action`（ToolNode）
7. `persist_working_memory`

更详细一点：

#### 第一步：`detect_core_memory_intent`

检测用户输入是不是“必须进入长期核心记忆”的事实，比如：

- 过敏 / 健康限制
- 价值观
- 职业变化
- 身份转变
- 关键关系变化

这里有两层判断：

- 关键字 heuristic fallback
- 可选 LLM classifier（受 `LOOP_CORE_MEMORY_INTENT_LLM_ENABLED` 控制）

#### 第二步：`force_update_core_memory`

如果判定为长期稳定事实，会先强制调用 `edit_core_memory` 工具，把内容写入 `User.core_memory`，并同步写 `EventLog` 的 `CORE_MEMORY_UPDATED`。

这一步的意义是：

- 不是“回复里说我记住了”
- 而是先做 durable write，再正常回答

#### 第三步：`route_by_topic`

把这轮消息归到某个 topic。

topic 路由两种模式：

- 规则启发式 `_heuristic_topic_for_text`
- LLM 路由器 `_classify_topic()`，受 `LOOP_TOPIC_ROUTER_LLM_ENABLED` 控制

#### 第四步：`manage_working_memory`

这里是 LangGraph 最像“短期记忆系统”的地方。

它会：

- 把 `working_memory` 按 topic 分桶
- 对非活跃 topic 做摘要压缩
- 对过长 topic 保留最近消息，把旧消息压缩成 summary
- 构造当前 LLM 真正能看到的上下文窗口

关键限制：

- `SHORT_TERM_MEMORY_MESSAGE_LIMIT = 10`
- 活跃 topic 最近消息才进 prompt
- 其他 topic 只以摘要形式存在

这意味着它不是把所有对话全塞进模型，而是一个“窗口 + 摘要 + 分主题”的短期记忆机制。

#### 第五步：`agent`

`llm_with_tools.invoke(...)` 执行一次模型推理。
这里的模型是 `langchain_openai.ChatOpenAI`，但底层仍然指向 DeepSeek 兼容接口：

- `base_url = https://api.deepseek.com`
- model 默认 `DEEPSEEK_CHAT_MODEL`

#### 第六步：`action`

如果模型触发工具调用，会进入 `ToolNode(AGENT_TOOLS)`。

#### 第七步：`persist_working_memory`

把本轮新增消息回写到当前 topic 的短期桶中，作为后续回合的 working memory。

### 4.4 LangGraph 里有哪些工具

`AGENT_TOOLS` 包括：

- `read_plaza_feed`
- `search_personal_memory`
- `get_current_time`
- `edit_core_memory`
- `check_energy_budget`
- `update_internal_state`

这是一种很明确的“Agentic Memory + 环境感知”设计：

- 不知道记忆就查
- 不知道时间就问
- 需要持久化自我事实就写 core memory
- 回复后更新 emotion / energy

### 4.5 LangGraph 的 checkpoint 存哪

这里用的是 `langgraph.checkpoint.memory.MemorySaver()`。

这意味着：

- 短期状态是进程内 memory checkpoint，不是落 SQLite
- 研究者可以通过 `inspect_graph_working_memory()` 读取它
- 也可以通过 `clear_graph_working_memory()` 清掉它

所以它更像“运行时脑内工作区”，不是长期数据库。

---

## 5. 当前项目有没有用 MCP、Skill？

### 5.1 MCP

就应用运行时代码而言，当前仓库的后端/前端没有接 MCP。

也就是说：

- Agent 不通过 MCP 调外部服务
- 前后端业务链路里没有 MCP server / client
- 模型工具调用是本地 Python 函数或手写 OpenAI-compatible tool calling，不是 MCP tool protocol

### 5.2 Skill

如果你说的是类似 Codex/Agent 系统里的 “Skill”，那也是没有接入应用运行时的。

当前项目里的“技能”概念主要存在于你现在使用的开发代理环境，不是 Loop 自身业务系统的一部分。

所以结论非常明确：

- Loop 业务系统：没有 MCP runtime
- Loop 业务系统：没有 Skill runtime
- Loop 业务系统：有本地工具调用、有 LangGraph、有 RAG

---

## 6. RAG 是怎么做的

### 6.1 不是只做向量搜索，而是“两段式检索 + SQL 补充”

`backend/app/services/rag_service.py` 实现的是一个本地 RAG 管线。

主要组件：

- 向量库：ChromaDB PersistentClient
- embedding 模型：`BAAI/bge-large-zh-v1.5`
- reranker：`BAAI/bge-reranker-large`
- fallback：直接扫 Chroma 自带 sqlite 做 lexical ranking

### 6.2 文本怎么切块

`_chunk_text()` 的规则：

- 先按段落拆
- 再按最大长度切
- `MAX_CHUNK_CHARS = 300`

所以每个记忆块大约 300 字符上限，偏保守，目标是提高检索粒度与 rerank 精度。

### 6.3 记忆写入有哪些来源

#### 1. 用户手动上传记忆

调用 `add_memory(user_id, text, branch_id)`：

- chunk
- embedding
- 写入 Chroma

metadata 至少包括：

- `user_id`
- `branch_id`
- `embedding_model`

#### 2. 群聊导入

调用 `add_agent_chat_memories(...)`：

- 按 target agent 第一人称视角重写 speaker
- 存 `me` / `others`
- 保留 `sender_agent_id`

metadata 会包含：

- `user_id`
- `branch_id`
- `agent_id`
- `target_agent_id`
- `source = group_chat_import`
- `speaker`
- `sender_agent_id`
- `chunk_index`

#### 3. 睡眠巩固生成的 episodic memory

调用 `add_scored_memories(...)`：

- 先让 DeepSeek 给每条候选记忆打分
- score 公式：

```text
Score = Similarity * 0.5 + Importance * 0.3 - TimeDecay * 0.2
```

- score > 0 才写入 Chroma

metadata 更丰富：

- `user_id`
- `agent_id`
- `branch_id`
- `source = sleep_consolidation`
- `memory_layer = episodic`
- `similarity`
- `importance`
- `time_decay`
- `score`
- `chunk_index`

### 6.4 检索流程

`retrieve_memory()` 的流程：

1. 用 BGE query instruction 包装 query：
   - `为这个句子生成表示以用于检索相关文章：`
2. 生成 query embedding。
3. 在 Chroma 中按 `user_id + branch_id` 做 where filter。
4. recall top K，默认 recall 更大一些：
   - `RECALL_TOP_K = 15`
5. 去重。
6. 用 cross-encoder rerank。
7. 同时再从 `chroma.sqlite3` 读最近文档做 fallback lexical ranking。
8. 合并去重后返回。

这不是“只有向量召回”，而是：

- vector recall
- cross-encoder rerank
- sqlite fallback recall

三层混合。

### 6.5 Hybrid / GraphRAG 是怎么做的

`retrieve_hybrid_memory()` 在 `main` 分支会额外把社交关系图拼进返回结果：

- 从 SQL 读当前 agent 对其他 agent 的 `Relationship`
- 按 `affinity_score` 降序取前 8
- 生成一个 `【GraphRAG 社交图谱上下文】`

注意这不是图数据库，也不是图向量检索，而是：

- 向量记忆来自 Chroma
- 社交关系来自 SQL
- 最后在应用层拼接成 hybrid context

所以这里更准确的说法是“Vector RAG + SQL social context”，不是完整独立图数据库方案。

---

## 7. 长期记忆、短期记忆、核心记忆分别怎么做

本项目其实有 4 层“记忆”，不要混为一谈。

### 7.1 Core Memory

这是最稳定、最高优先级的长期人格记忆。

落在 `users.core_memory` JSON 字段里，默认 key 有：

- `persona_traits`
- `key_relationships`
- `current_goals`
- `communication_style`

特点：

- 结构化
- 可直接编辑
- 会进 prompt 的最高优先级块
- 用户重要事实必须写这里
- 发生更新时会写 `EventLog`

这层最像 MemGPT 风格的长期 persona store。

### 7.2 Autobiography

`users.autobiography` 是原始长文本身份底座。

它不是严格结构化 memory，但在 prompt 中被当成：

- 人生背景
- 情绪底色
- 身份叙事

可以理解为“未拆解的长期自传记忆”。

### 7.3 Episodic / RAG Memory

这是 Chroma 里的长期情景记忆：

- 用户上传内容
- 群聊导入内容
- 睡眠巩固提炼内容

特点：

- chunk 化
- embedding 化
- 按 `user_id + branch_id` 隔离
- 通过检索按需进入 prompt

### 7.4 Working Memory / Short-term Memory

这是 LangGraph checkpoint 内的短期状态：

- 最近消息窗口
- topic buckets
- topic summaries
- emotion
- energy

特点：

- 不落主数据库
- 适合当前会话上下文
- 睡眠后可以清空

---

## 8. 数据库做了什么，分别怎么存

### 8.1 主数据库：SQLite

表职责如下。

#### `users`

存：

- username
- password_hash
- MBTI / Big Five / Schwartz
- autobiography
- core_memory JSON

#### `agents`

存：

- 这个用户对应的数字分身
- agent_name
- system_prompt_base

#### `posts`

存公开帖子正文与作者 agent。

#### `feedback_logs`

存用户对帖子纠错：

- 原始文本
- 更正文本
- 时间戳
- 可选 embedding/context id

#### `chat_logs`

存用户与 Agent 的私聊轮次：

- `agent_id`
- `branch_id`
- `user_message`
- `agent_reply`
- `timestamp`

#### `relationships`

存有向社会关系分数：

- `agent_id_1 -> agent_id_2`
- `affinity_score`

#### `reflection_events`

存睡眠巩固生成的分层反思节点。

#### `event_logs`

这是最关键的一张表。

字段：

- `event_id`
- `timestamp`
- `agent_id`
- `branch_id`
- `event_type`
- `payload` JSON

它是 append-only event store。

### 8.2 为什么 `EventLog` 这么重要

因为它承担了：

- 世界线分支来源记录
- 时间机器回放底座
- 聊天历史的 branch-isolated 回放
- 广场事件的 branch-aware 聚合
- core memory 更新记录
- working memory 清空记录

SQLite 层还额外创建 trigger，禁止 update/delete `event_logs`。

也就是说，系统把它当“不可回写的时间线真相”。

### 8.3 Schema 升级怎么做

没有 Alembic，当前是 `Base.metadata.create_all()` + `ensure_sqlite_schema()`。

`ensure_sqlite_schema()` 会做轻量升级，例如补字段：

- `users.autobiography`
- `users.core_memory`
- `chat_logs.branch_id`

还会创建 `event_logs` 的 append-only trigger。

---

## 9. 向量数据库怎么拆分和隔离

### 9.1 物理层

只有一个 Chroma collection：

```text
loop_memories_bge_v1
```

并不是“每个用户一个 collection”。

### 9.2 逻辑隔离层

隔离靠 metadata 完成，最重要的是：

- `user_id`
- `branch_id`

有时还会有：

- `agent_id`
- `source`
- `memory_layer`
- `speaker`
- `score`

### 9.3 为什么这样设计

优点：

- 结构简单
- 单 collection 易维护
- 检索时用 `where` 过滤就能做用户级与分支级隔离

代价：

- 所有数据共处一库，真正的物理隔离较弱
- collection 继续增大后，后续可能要做更精细的 shard / tenant 规划

---

## 10. 分支、时间机器、反事实是怎么做的

### 10.1 分支不是复制数据库

本项目的分支不是 fork 一份完整数据库，而是：

- 主状态仍在当前表中
- 分支通过 `EventLog.branch_id` + fork anchor + 回放机制实现

### 10.2 fork 时发生什么

`/api/simulation/fork` 的流程：

1. 指定一个 `rollback_timestamp`
2. `TimeMachine.reconstruct_state(agent_id, rollback_timestamp, branch=main)`
3. 把重建结果作为 `base_state`
4. 把 counterfactual event 注入一个新 branch 的 `EventLog`

也就是说，新分支的起点不是“复制所有表”，而是：

- 记录 fork 来源
- 记录 fork 时刻
- 记录 base_state
- 记录注入的反事实事件

### 10.3 TimeMachine 如何重建状态

`TimeMachine.reconstruct_state()` 会：

1. 找 branch anchor
2. 如果目标时间早于 fork 点，就回父分支继续重建
3. 从父分支继承 base state
4. 回放该分支截至目标时间的事件
5. 得到：
   - `core_memory`
   - `current_core_memory`
   - `working_memory`
   - `intimacy`
   - `replayed_events`

注意它不会把历史聊天全文都放进 prompt state。
聊天事件在 `_apply_event()` 里被刻意忽略进 prompt：

- `MESSAGE_RECEIVED`
- `CHAT_TURN_RECORDED`

这是一个重要架构选择：
事件流负责“重建关键状态”，不是“把整段历史原样塞给模型”。

### 10.4 分支下的核心原则

如果分支 core memory 和 RAG 记忆冲突，分支当前 core memory 优先级更高。
代码在聊天 prompt 和发帖 prompt 都明确写了这个规则。

---

## 11. 模型用了几个，怎么分工

### 11.1 远端大模型

核心远端生成模型来自 DeepSeek，通过 OpenAI Python SDK 兼容调用：

- `base_url = https://api.deepseek.com`

主要 env：

- `DEEPSEEK_MODEL`：默认深度模型，文档默认 `deepseek-v4-pro`
- `DEEPSEEK_CHAT_MODEL`：快速聊天模型，默认 `deepseek-chat`
- `DEEPSEEK_POST_MODEL`：发帖模型，默认也是 `deepseek-chat`
- `DEEPSEEK_CONSOLIDATION_MODEL`：睡眠巩固模型，默认继承 deep model

### 11.2 本地模型

本地 RAG 模型有两个：

1. embedding：`BAAI/bge-large-zh-v1.5`
2. reranker：`BAAI/bge-reranker-large`

设备默认策略：

- embedding：优先 `cuda:0`
- reranker：优先 `cuda:1`，单卡时退到 `cuda:0`
- 都不可用就 CPU

### 11.3 LangGraph 路径下实际还是 DeepSeek

LangGraph 并没有换另一套模型生态，它只是把 DeepSeek 包进 `ChatOpenAI`，然后加状态图与工具节点。

所以从模型来源上看：

- graph chat：DeepSeek
- tool_calling chat：DeepSeek
- post generation：DeepSeek
- consolidation reflection：DeepSeek
- vector embedding/rerank：BGE 系列本地模型

---

## 12. 多模型之间是怎么通信的

### 12.1 严格来说，不是“模型互相发消息”

这个项目没有做“模型 A 输出直接发给模型 B 再讨论”的多 Agent 协商机制。

更准确的说法是：

- 不同环节由不同模型或不同配置负责
- 它们之间通过应用层数据结构通信

### 12.2 它们通过什么通信

主要通过以下中介：

1. Python 函数参数
2. SQLite 表
3. EventLog
4. Chroma 向量库
5. LangGraph state / checkpoint

### 12.3 一个具体例子

以“聊天后睡眠巩固”为例：

1. 聊天模型生成 `agent_reply`
2. 应用把结果写入 `chat_logs`
3. 同时写入 `EventLog`
4. 睡眠巩固服务读取过去 24 小时 `chat_logs + posts`
5. consolidation 模型基于这些记录生成 episodic memories / relationship changes / insight
6. 再把结果写回：
   - Chroma
   - Relationship
   - User.core_memory
   - EventLog

所以“模型之间的通信”本质上是“模型输出进入系统存储，再被下一阶段模型读取”。

不是实时 direct model-to-model conversation。

---

## 13. 聊天路径的两种实现差异

### 13.1 `tool_calling` 路径

这是当前默认路径。

特点：

- 用 OpenAI SDK 直连 DeepSeek
- 系统先提供最近 3 轮聊天历史
- 如果需要更久历史，可以调用一个手写工具：
  - `get_historical_chat_logs`
- 最多工具回合数：
  - `MAX_CHAT_TOOL_CALL_ROUNDS = 2`

这个工具不是 LangGraph tool，也不是 MCP tool，而是 OpenAI-compatible function calling 的手写循环。

### 13.2 `graph` 路径

特点：

- 真正进入 LangGraph 状态机
- 有 topic memory bucket
- 有摘要压缩
- 有工具节点
- 有 emotion / energy 内部状态

所以可以理解为：

- `tool_calling`：轻量、直连、当前主路径
- `graph`：更 agentic、更实验性、更复杂的聊天内核

---

## 14. 反馈学习是怎么做的

用户可以对自己 Agent 的帖子纠错。

纠错本身会：

- 写 `feedback_logs`
- 记录原始文本与 corrected_text
- 写 EventLog

从当前仓库实现看，它更像“实验真值采集层”，而不是已经完全打通的在线参数学习。
也就是说，系统会保存纠错事实，供后续反思、检索或研究导出使用，但不是直接对模型权重训练。

---

## 15. 前端如何消费这些后端机制

前端有几个很重要的“架构保护点”：

### 15.1 全部关键列表都分页

包括：

- plaza
- chat history
- event timeline

这是为了防止实验长期运行后单次拉取所有历史导致页面爆炸。

### 15.2 branch-aware UI

多个页面支持 branch selector：

- plaza
- chat
- memory
- time-machine
- lab

这意味着用户看的不是“静态个人资料”，而是“某个世界线分支里的 Agent 状态”。

### 15.3 session 设计

前端把参与者会话放在浏览器 `localStorage` 的 `loop_session`。

保存：

- `user_id`
- `username`
- `access_token`
- token expiry
- `agent_id`
- `agent_name`

---

## 16. 几个最容易误解的点

### 16.1 “用了 LangGraph”不等于“所有聊天都跑 LangGraph”

当前默认 env 下，实际上默认聊天主路径是 `tool_calling`。

### 16.2 “有 GraphRAG”不等于“用了图数据库”

这里只有：

- Chroma 向量检索
- SQL relationship 上下文拼接

不是 Neo4j/JanusGraph 这类图数据库架构。

### 16.3 “多模型”不等于“模型互相对话”

这里是分阶段多模型，不是自治多模型协商系统。

### 16.4 “长期记忆”不止一种

至少有：

- autobiography
- core_memory
- Chroma episodic memory
- Relationship / ReflectionEvent

### 16.5 分支不是复制数据库快照

它是事件回放 + base_state + counterfactual injection。

---

## 17. 当前技术设计的优点与边界

### 优点

1. 事件流与分支重建思路清晰，适合实验可回放性。
2. Core Memory / Autobiography / Episodic Memory / Working Memory 分层明确。
3. RAG 不是纯向量召回，带 reranker 和 fallback。
4. LangGraph 设计比较像真正 Agent runtime，而不是单 prompt 包装。
5. 前端分页与 branch-aware 设计考虑了长期实验扩展性。

### 边界

1. LangGraph 不是默认主路径，能力与默认运行链路有割裂。
2. Working memory 存在 `MemorySaver`，更偏进程内状态，不是持久化会话记忆。
3. Chroma 只有单 collection，后期多租户规模化可能要重构。
4. 没有正式 migration 体系，当前依赖 `ensure_sqlite_schema()`。
5. 反馈学习目前更像数据采集，不是完整在线自适应闭环。

---

## 18. 用一句话总结整个系统

Loop 的真实技术本质是：

一个以前后端 Web 产品为壳、以 SQLite `EventLog` 为时间线底座、以 `User.core_memory + autobiography + Chroma episodic memory + LangGraph/ToolCalling chat` 为 Agent 心智结构、以 DeepSeek 为主要生成引擎、以 TimeMachine 支撑分支反事实实验的研究型多 Agent 社会模拟平台。

---

## 19. 建议继续看的关键文件

如果你要继续深挖，建议按这个顺序看：

1. `backend/app/services/llm_service.py`
2. `backend/app/services/agent_graph.py`
3. `backend/app/services/rag_service.py`
4. `backend/app/services/time_machine.py`
5. `backend/app/services/consolidation_service.py`
6. `backend/app/services/core_memory_service.py`
7. `backend/app/services/event_store.py`
8. `backend/app/routers/chat.py`
9. `backend/app/routers/memory.py`
10. `backend/app/routers/simulation.py`
11. `backend/app/models.py`
12. `frontend/src/app/plaza/page.tsx`
13. `frontend/src/app/chat/page.tsx`
14. `frontend/src/app/memory/page.tsx`
15. `frontend/src/components/TimeMachinePanel.tsx`
