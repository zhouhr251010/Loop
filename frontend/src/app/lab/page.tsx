"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  API_BASE_URL,
  Agent,
  HealthResponse,
  PostOut,
  apiRequest,
} from "@/lib/api";
import { LoopSession, getAccessToken, loadSession, saveSession } from "@/lib/session";
import { formatFeedTime, formatLocalDateTime, parseUtcTimestamp } from "@/lib/time";

type ExportKind = "chatlogs" | "feedbacks";

export default function LabPage() {
  const router = useRouter();
  const [session, setSession] = useState<LoopSession | null>(null);
  const [adminKey, setAdminKey] = useState("");
  const [targetAgentId, setTargetAgentId] = useState("");
  const [targetUserId, setTargetUserId] = useState("");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [singlePost, setSinglePost] = useState<PostOut | null>(null);
  const [tickPosts, setTickPosts] = useState<PostOut[]>([]);
  const [exportPreview, setExportPreview] = useState("");
  const [exportMeta, setExportMeta] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);
  const [isSimulatingOne, setIsSimulatingOne] = useState(false);
  const [isTicking, setIsTicking] = useState(false);
  const [isExporting, setIsExporting] = useState<ExportKind | null>(null);

  const hasAdminKey = useMemo(() => adminKey.trim().length > 0, [adminKey]);

  useEffect(() => {
    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
        return;
      }

      setSession(storedSession);
      setTargetUserId(String(storedSession.user_id));
      if (storedSession.agent_id) {
        setTargetAgentId(String(storedSession.agent_id));
        return;
      }

      try {
        const agent = await apiRequest<Agent>(
          `/api/users/${storedSession.user_id}/agent`,
        );
        const hydratedSession = {
          ...storedSession,
          agent_id: agent.id,
          agent_name: agent.agent_name,
        };
        saveSession(hydratedSession);
        setSession(hydratedSession);
        setTargetAgentId(String(agent.id));
      } catch {
        setError("No Agent found for this session yet.");
      }
    }

    bootstrap();
  }, [router]);

  function adminHeaders() {
    return {
      "X-Loop-Admin-Key": adminKey.trim(),
    };
  }

  async function checkHealth() {
    setError("");
    setMessage("");
    setIsCheckingHealth(true);
    try {
      const result = await apiRequest<HealthResponse>("/health");
      setHealth(result);
      setMessage("Backend health check succeeded.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Health check failed.");
    } finally {
      setIsCheckingHealth(false);
    }
  }

  async function simulateOne(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!hasAdminKey || !targetAgentId.trim()) {
      return;
    }

    setError("");
    setMessage("");
    setIsSimulatingOne(true);
    try {
      const post = await apiRequest<PostOut>(
        `/api/simulate/agent/${Number(targetAgentId)}/post`,
        {
          method: "POST",
          headers: adminHeaders(),
        },
      );
      setSinglePost(post);
      setMessage(`Generated one simulated post for Agent #${post.agent_id}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Single-agent simulation failed.");
    } finally {
      setIsSimulatingOne(false);
    }
  }

  async function simulateTick() {
    if (!hasAdminKey) {
      return;
    }

    setError("");
    setMessage("");
    setIsTicking(true);
    try {
      const posts = await apiRequest<PostOut[]>("/api/simulate/tick", {
        method: "POST",
        headers: adminHeaders(),
      });
      setTickPosts(posts);
      setMessage(`Simulation tick created ${posts.length} post(s).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Simulation tick failed.");
    } finally {
      setIsTicking(false);
    }
  }

  async function exportJsonl(kind: ExportKind) {
    if (!hasAdminKey || !targetUserId.trim()) {
      return;
    }

    setError("");
    setMessage("");
    setIsExporting(kind);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/export/${Number(targetUserId)}/${kind}`,
        {
          headers: {
            ...adminHeaders(),
            ...(getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {}),
          },
        },
      );

      if (!response.ok) {
        throw new Error((await response.text()) || `Export failed with ${response.status}`);
      }

      const text = await response.text();
      const filename = `loop_user_${Number(targetUserId)}_${kind}.jsonl`;
      downloadText(filename, text);
      setExportPreview(text.split("\n").slice(0, 8).join("\n"));
      setExportMeta(`${filename} · ${text.length.toLocaleString()} characters`);
      setMessage(`Exported ${kind} for user #${Number(targetUserId)}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed.");
    } finally {
      setIsExporting(null);
    }
  }

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">Loading lab console...</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
        <header className="mb-6 border-b border-gray-200 pb-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
            Lab Console
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950">
            后端功能测试台
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-500">
            集中测试健康检查、自动仿真发帖、全体 tick，以及研究数据 JSONL 导出。
          </p>
          <p className="mt-2 text-sm text-gray-400">
            User #{session.user_id} ·{" "}
            <span className="font-medium text-gray-600">{session.username}</span>
            {" · "}
            {session.agent_id ? (
              <span className="font-medium text-gray-600">
                Agent #{session.agent_id}
                {session.agent_name ? ` · ${session.agent_name}` : ""}
              </span>
            ) : (
              <span>No Agent yet</span>
            )}
          </p>
        </header>

        {message ? (
          <div className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 shadow-sm">
            {message}
          </div>
        ) : null}
        {error ? (
          <div className="mb-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 shadow-sm">
            {error}
          </div>
        ) : null}

        <section className="mb-5 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_180px_180px]">
            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                Admin API key
              </span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                onChange={(event) => setAdminKey(event.target.value)}
                placeholder="Required for simulation and export endpoints"
                type="password"
                value={adminKey}
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-gray-700">Agent ID</span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                min={1}
                onChange={(event) => setTargetAgentId(event.target.value)}
                type="number"
                value={targetAgentId}
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-gray-700">User ID</span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                min={1}
                onChange={(event) => setTargetUserId(event.target.value)}
                type="number"
                value={targetUserId}
              />
            </label>
          </div>
        </section>

        <div className="grid gap-5 lg:grid-cols-2">
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-gray-950">
                  Backend health
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  测试 `GET /health`，确认 FastAPI 服务可达。
                </p>
              </div>
              <button
                className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={isCheckingHealth}
                onClick={checkHealth}
                type="button"
              >
                {isCheckingHealth ? "Checking..." : "Check health"}
              </button>
            </div>
            {health ? (
              <dl className="mt-4 grid grid-cols-2 gap-3">
                <Metric label="status" value={health.status} />
                <Metric label="service" value={health.service} />
              </dl>
            ) : (
              <p className="mt-4 text-sm text-gray-500">
                点击后会显示后端健康检查响应。
              </p>
            )}
          </section>

          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-gray-950">
              Research exports
            </h2>
            <p className="mt-1 text-sm leading-6 text-gray-500">
              测试 JSONL 导出接口，并在页面预览前几行。
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <button
                className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={!hasAdminKey || !targetUserId || isExporting !== null}
                onClick={() => exportJsonl("chatlogs")}
                type="button"
              >
                {isExporting === "chatlogs" ? "Exporting..." : "Export chatlogs"}
              </button>
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={!hasAdminKey || !targetUserId || isExporting !== null}
                onClick={() => exportJsonl("feedbacks")}
                type="button"
              >
                {isExporting === "feedbacks" ? "Exporting..." : "Export feedbacks"}
              </button>
            </div>
            {exportMeta ? (
              <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                  Last export
                </p>
                <p className="mt-1 text-sm font-medium text-gray-700">{exportMeta}</p>
                <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-white p-3 text-xs leading-5 text-gray-700">
                  {exportPreview || "(empty file)"}
                </pre>
              </div>
            ) : null}
          </section>

          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <form onSubmit={simulateOne}>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-950">
                    Single Agent simulation
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-gray-500">
                    测试 `POST /api/simulate/agent/{targetAgentId || "agent_id"}/post`。
                  </p>
                </div>
                <button
                  className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={!hasAdminKey || !targetAgentId || isSimulatingOne}
                  type="submit"
                >
                  {isSimulatingOne ? "Generating..." : "Generate post"}
                </button>
              </div>
            </form>
            {singlePost ? <PostPreview post={singlePost} /> : null}
          </section>

          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-gray-950">
                  Global simulation tick
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  测试 `POST /api/simulate/tick`，让所有 Agent 自动发帖一轮。
                </p>
              </div>
              <button
                className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={!hasAdminKey || isTicking}
                onClick={simulateTick}
                type="button"
              >
                {isTicking ? "Running..." : "Run tick"}
              </button>
            </div>
            <div className="mt-4 space-y-3">
              {tickPosts.length > 0 ? (
                tickPosts.map((post) => <PostPreview key={post.id} post={post} />)
              ) : (
                <p className="text-sm text-gray-500">
                  运行 tick 后会显示本轮生成的帖子。
                </p>
              )}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
      <dt className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        {label}
      </dt>
      <dd className="mt-1 break-words text-sm font-semibold text-gray-950">
        {value}
      </dd>
    </div>
  );
}

function PostPreview({ post }: { post: PostOut }) {
  return (
    <article className="mt-4 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
        <span className="font-semibold text-gray-700">Post #{post.id}</span>
        <span>Agent #{post.agent_id}</span>
        <time
          dateTime={parseUtcTimestamp(post.timestamp).toISOString()}
          title={formatLocalDateTime(post.timestamp)}
        >
          {formatFeedTime(post.timestamp)}
        </time>
      </div>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-700">
        {post.content}
      </p>
    </article>
  );
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "application/x-ndjson;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
