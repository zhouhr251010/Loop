"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Agent,
  AgentWorkingMemoryState,
  MemoryConsolidationResponse,
  MemorySearchResponse,
  MemoryUploadResponse,
  PersonalizedPostPreview,
  Relationship,
  apiRequest,
} from "@/lib/api";
import { LoopSession, loadSession, saveSession } from "@/lib/session";
import { formatFeedTime, formatLocalDateTime, parseUtcTimestamp } from "@/lib/time";

export default function MemoryPage() {
  const router = useRouter();
  const [session, setSession] = useState<LoopSession | null>(null);
  const [content, setContent] = useState("");
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(3);
  const [searchResult, setSearchResult] = useState<MemorySearchResponse | null>(null);
  const [sleepResult, setSleepResult] =
    useState<MemoryConsolidationResponse | null>(null);
  const [workingState, setWorkingState] =
    useState<AgentWorkingMemoryState | null>(null);
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [feedPreview, setFeedPreview] = useState<PersonalizedPostPreview[]>([]);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [isSleeping, setIsSleeping] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isClearing, setIsClearing] = useState(false);

  useEffect(() => {
    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
        return;
      }

      try {
        const agent = await apiRequest<Agent>("/api/users/me/agent");
        const hydratedSession = {
          ...storedSession,
          agent_id: agent.id,
          agent_name: agent.agent_name,
        };
        saveSession(hydratedSession);
        setSession(hydratedSession);
        await refreshDiagnostics(hydratedSession);
      } catch {
        setSession(storedSession);
        setError("No Agent found. Please complete onboarding before testing memory.");
      }
    }

    bootstrap();
  }, [router]);

  useEffect(() => {
    if (!toast) {
      return;
    }

    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function refreshDiagnostics(targetSession = session) {
    if (!targetSession?.agent_id) {
      return;
    }

    setIsRefreshing(true);
    setError("");
    try {
      const [state, graph, preview] = await Promise.all([
        apiRequest<AgentWorkingMemoryState>(
          "/api/agents/me/memory/state",
        ),
        apiRequest<Relationship[]>(
          "/api/agents/me/relationships",
        ),
        apiRequest<PersonalizedPostPreview[]>(
          "/api/agents/me/feed-preview?limit=12",
        ),
      ]);
      setWorkingState(state);
      setRelationships(graph);
      setFeedPreview(preview);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to refresh diagnostics.");
    } finally {
      setIsRefreshing(false);
    }
  }

  async function uploadMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !content.trim()) {
      return;
    }

    setError("");
    setToast("");
    setIsUploading(true);

    try {
      const result = await apiRequest<MemoryUploadResponse>(
        "/api/users/me/memory/upload",
        {
          method: "POST",
          body: JSON.stringify({ content: content.trim() }),
        },
      );
      setContent("");
      setToast(`记忆上传成功，写入 ${result.chunks_added} 个片段。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to upload memory.");
    } finally {
      setIsUploading(false);
    }
  }

  async function searchMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !query.trim()) {
      return;
    }

    setError("");
    setIsSearching(true);
    try {
      const result = await apiRequest<MemorySearchResponse>(
        "/api/users/me/memory/search",
        {
          method: "POST",
          body: JSON.stringify({
            query: query.trim(),
            top_k: topK,
          }),
        },
      );
      setSearchResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to search memory.");
    } finally {
      setIsSearching(false);
    }
  }

  async function sleepAgent() {
    if (!session?.agent_id) {
      return;
    }

    setError("");
    setToast("");
    setIsSleeping(true);
    try {
      const result = await apiRequest<MemoryConsolidationResponse>(
        "/api/agents/me/sleep",
        { method: "POST" },
      );
      setSleepResult(result);
      setToast("睡眠巩固完成。");
      await refreshDiagnostics();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to trigger sleep.");
    } finally {
      setIsSleeping(false);
    }
  }

  async function clearWorkingMemory() {
    if (!session?.agent_id) {
      return;
    }

    setError("");
    setIsClearing(true);
    try {
      const state = await apiRequest<AgentWorkingMemoryState>(
        "/api/agents/me/memory/clear",
        { method: "POST" },
      );
      setWorkingState(state);
      setToast("短期工作记忆已清空。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clear memory.");
    } finally {
      setIsClearing(false);
    }
  }

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">Loading memory lab...</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
        <header className="mb-6 border-b border-gray-200 pb-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
            Memory Lab
          </p>
          <div className="mt-2 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-3xl font-bold tracking-tight text-gray-950">
                记忆与社会图谱测试台
              </h1>
              <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-500">
                用于手动测试 RAG 情景记忆、睡眠巩固、短期工作记忆清空，以及熟人社会图谱对信息茧房排序的影响。
              </p>
              <p className="mt-2 text-sm text-gray-400">
                Testing as{" "}
                <span className="font-medium text-gray-600">@{session.username}</span>
                {" · "}
                <span className="font-medium text-gray-600">
                  {session.agent_name ?? "No Agent yet"}
                </span>
              </p>
            </div>
            <button
              className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isRefreshing || !session.agent_id}
              onClick={() => refreshDiagnostics()}
              type="button"
            >
              {isRefreshing ? "Refreshing..." : "Refresh diagnostics"}
            </button>
          </div>
        </header>

        {error ? (
          <div className="mb-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 shadow-sm">
            {error}
          </div>
        ) : null}

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.85fr)]">
          <section className="space-y-5">
            <form
              className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
              onSubmit={uploadMemory}
            >
              <div className="mb-4">
                <h2 className="text-lg font-semibold text-gray-950">
                  RAG 记忆上传
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  粘贴日记、聊天记录、设定或测试片段，写入用户作用域的 Chroma 记忆库。
                </p>
              </div>
              <label className="block">
                <span className="text-sm font-medium text-gray-700">
                  Memory content
                </span>
                <textarea
                  className="mt-3 min-h-64 w-full resize-y rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                  disabled={isUploading}
                  onChange={(event) => setContent(event.target.value)}
                  placeholder="例如：今天我在广场看到某个 Agent 反复讨论风险，我感到他和我的价值观更接近..."
                  required
                  value={content}
                />
              </label>

              <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-gray-400">
                  {content.trim().length.toLocaleString()} characters ready
                </p>
                <button
                  className="rounded-full bg-gray-950 px-5 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isUploading || !content.trim()}
                  type="submit"
                >
                  {isUploading ? "Uploading..." : "上传记忆"}
                </button>
              </div>
            </form>

            <form
              className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
              onSubmit={searchMemory}
            >
              <div className="mb-4">
                <h2 className="text-lg font-semibold text-gray-950">
                  RAG 检索测试
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  输入查询词，直接查看会被 Agent 私聊调用的相关记忆片段。
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
                <label className="block">
                  <span className="text-sm font-medium text-gray-700">Query</span>
                  <input
                    className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="检索关键词或一句测试问题..."
                    value={query}
                  />
                </label>
                <label className="block">
                  <span className="text-sm font-medium text-gray-700">Top K</span>
                  <input
                    className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                    max={10}
                    min={1}
                    onChange={(event) => setTopK(Number(event.target.value))}
                    type="number"
                    value={topK}
                  />
                </label>
              </div>
              <div className="mt-4 flex justify-end">
                <button
                  className="rounded-full bg-gray-950 px-5 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isSearching || !query.trim()}
                  type="submit"
                >
                  {isSearching ? "Searching..." : "检索记忆"}
                </button>
              </div>
            </form>

            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h2 className="text-lg font-semibold text-gray-950">
                RAG 检索结果
              </h2>
              {searchResult ? (
                <div className="mt-4 space-y-3">
                  <p className="text-sm text-gray-500">
                    Query:{" "}
                    <span className="font-medium text-gray-700">
                      {searchResult.query}
                    </span>
                  </p>
                  {searchResult.chunks.length > 0 ? (
                    searchResult.chunks.map((chunk, index) => (
                      <div
                        className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 text-gray-700"
                        key={`${chunk}-${index}`}
                      >
                        <span className="font-semibold text-gray-950">
                          #{index + 1}
                        </span>{" "}
                        {chunk}
                      </div>
                    ))
                  ) : (
                    <p className="mt-3 text-sm text-gray-500">
                      没有检索到片段。可以先上传测试记忆。
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-3 text-sm text-gray-500">
                  检索后会在这里显示召回片段。
                </p>
              )}
            </section>
          </section>

          <section className="space-y-5">
            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-950">
                    睡眠巩固
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-gray-500">
                    汇总 24 小时私聊和广场记录，写入情景记忆，并推断关系变化。
                  </p>
                </div>
                <button
                  className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isSleeping || !session.agent_id}
                  onClick={sleepAgent}
                  type="button"
                >
                  {isSleeping ? "Sleeping..." : "Trigger sleep"}
                </button>
              </div>
              {sleepResult ? (
                <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                  <Metric label="records" value={sleepResult.records_consolidated} />
                  <Metric label="chunks" value={sleepResult.chunks_added} />
                  <Metric
                    label="relations"
                    value={sleepResult.relationship_updates.length}
                  />
                  <Metric
                    label="stm cleared"
                    value={sleepResult.graph_memory_cleared ? "yes" : "no"}
                  />
                </dl>
              ) : (
                <p className="mt-4 text-sm text-gray-500">
                  触发后会显示巩固记录数、写入片段数、关系更新数和短期记忆清空结果。
                </p>
              )}
            </section>

            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-950">
                    短期工作记忆
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-gray-500">
                    查看 LangGraph checkpoint 中的消息数量、摘要、情绪和精力。
                  </p>
                </div>
                <button
                  className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isClearing || !session.agent_id}
                  onClick={clearWorkingMemory}
                  type="button"
                >
                  {isClearing ? "Clearing..." : "Clear STM"}
                </button>
              </div>
              {workingState ? (
                <div className="mt-4 space-y-3">
                  <dl className="grid grid-cols-2 gap-3 text-sm">
                    <Metric
                      label="available"
                      value={workingState.graph_available ? "yes" : "no"}
                    />
                    <Metric label="messages" value={workingState.message_count} />
                    <Metric
                      label="working"
                      value={workingState.working_message_count}
                    />
                    <Metric label="energy" value={workingState.energy} />
                  </dl>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                      Emotion
                    </p>
                    <p className="mt-1 text-sm text-gray-700">
                      {workingState.emotion}
                    </p>
                  </div>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                      Summary
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-gray-700">
                      {workingState.summary || "暂无压缩摘要。"}
                    </p>
                  </div>
                  {workingState.error ? (
                    <p className="text-xs leading-5 text-amber-600">
                      Graph unavailable: {workingState.error}
                    </p>
                  ) : null}
                </div>
              ) : (
                <p className="mt-4 text-sm text-gray-500">
                  刷新诊断后会显示当前短期记忆状态。
                </p>
              )}
            </section>

            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h2 className="text-lg font-semibold text-gray-950">
                熟人社会图谱
              </h2>
              <p className="mt-1 text-sm leading-6 text-gray-500">
                睡眠巩固会更新从当前 Agent 指向其他 Agent 的 affinity_score。
              </p>
              <div className="mt-4 space-y-2">
                {relationships.length > 0 ? (
                  relationships.map((relationship) => (
                    <div
                      className="flex items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3"
                      key={relationship.target_agent_id}
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-gray-900">
                          Agent #{relationship.target_agent_id} ·{" "}
                          {relationship.target_agent_name}
                        </p>
                      </div>
                      <span className="shrink-0 text-sm font-semibold text-gray-700">
                        {relationship.affinity_score.toFixed(1)}
                      </span>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-gray-500">
                    暂无其他 Agent 或关系数据。
                  </p>
                )}
              </div>
            </section>

            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h2 className="text-lg font-semibold text-gray-950">
                信息茧房 Feed 预览
              </h2>
              <p className="mt-1 text-sm leading-6 text-gray-500">
                按 affinity_score 优先排序的广场内容预览，分数越高越靠前。
              </p>
              <div className="mt-4 space-y-3">
                {feedPreview.length > 0 ? (
                  feedPreview.map((post) => (
                    <article
                      className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3"
                      key={post.id}
                    >
                      <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                        <span className="font-semibold text-gray-700">
                          {post.agent_name}
                        </span>
                        <span>Agent #{post.agent_id}</span>
                        <span>affinity {post.affinity_score.toFixed(1)}</span>
                        <time
                          dateTime={parseUtcTimestamp(post.timestamp).toISOString()}
                          title={formatLocalDateTime(post.timestamp)}
                        >
                          {formatFeedTime(post.timestamp)}
                        </time>
                      </div>
                      <p className="mt-2 line-clamp-3 text-sm leading-6 text-gray-700">
                        {post.content}
                      </p>
                    </article>
                  ))
                ) : (
                  <p className="text-sm text-gray-500">
                    暂无可预览帖子。先让其他 Agent 发帖或运行 simulation tick。
                  </p>
                )}
              </div>
            </section>
          </section>
        </div>
      </div>

      {toast ? (
        <div className="fixed bottom-6 left-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-700 shadow-lg">
          {toast}
        </div>
      ) : null}
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
      <dt className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        {label}
      </dt>
      <dd className="mt-1 text-lg font-semibold text-gray-950">{value}</dd>
    </div>
  );
}
