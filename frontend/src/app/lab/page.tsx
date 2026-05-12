"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { BranchSelector } from "@/components/BranchSelector";
import { useLanguage } from "@/components/LanguageContext";
import {
  API_BASE_URL,
  Agent,
  AgentSessionChoice,
  HealthResponse,
  PostOut,
  apiRequest,
} from "@/lib/api";
import { LoopSession, getAccessToken, loadSession, saveSession } from "@/lib/session";
import { formatFeedTime, formatLocalDateTime, parseUtcTimestamp } from "@/lib/time";

type ExportKind = "chatlogs" | "feedbacks";
type BranchPurgeResult = {
  branch_id: string;
  events_deleted: number;
  posts_deleted: number;
  chat_logs_deleted: number;
  feedback_logs_deleted: number;
  post_ids: number[];
  feedback_log_ids: number[];
  verification: Record<string, number>;
  is_clean: boolean;
  deletion_log: string[];
  message: string;
};

const DEFAULT_BRANCH_ID = "main";
const BRANCHES_ENDPOINT = "/api/simulation/branches";
const PURGE_BRANCH_ENDPOINT = "/api/admin/purge-branch";

const SIMULATE_USER_POST_ENDPOINT = (username: string, branchId: string) =>
  `/api/simulate/user/${encodeURIComponent(username)}/post?branch_id=${encodeURIComponent(
    branchId,
  )}`;

const SIMULATE_TICK_ENDPOINT = (branchId: string) =>
  `/api/simulate/tick?branch_id=${encodeURIComponent(branchId)}`;

export default function LabPage() {
  const router = useRouter();
  const { t } = useLanguage();
  const copy = t.lab;
  const [session, setSession] = useState<LoopSession | null>(null);
  const [adminKey, setAdminKey] = useState("");
  const [targetAgentId, setTargetAgentId] = useState("");
  const [targetUsername, setTargetUsername] = useState("");
  const [targetBranch, setTargetBranch] = useState(DEFAULT_BRANCH_ID);
  const [purgeBranchId, setPurgeBranchId] = useState(DEFAULT_BRANCH_ID);
  const [branches, setBranches] = useState<string[]>([DEFAULT_BRANCH_ID]);
  const [agentChoices, setAgentChoices] = useState<AgentSessionChoice[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [singlePost, setSinglePost] = useState<PostOut | null>(null);
  const [tickPosts, setTickPosts] = useState<PostOut[]>([]);
  const [exportPreview, setExportPreview] = useState("");
  const [exportMeta, setExportMeta] = useState("");
  const [purgeResult, setPurgeResult] = useState<BranchPurgeResult | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);
  const [isSimulatingOne, setIsSimulatingOne] = useState(false);
  const [isTicking, setIsTicking] = useState(false);
  const [isExporting, setIsExporting] = useState<ExportKind | null>(null);
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [isPurgingBranch, setIsPurgingBranch] = useState(false);

  const hasAdminKey = useMemo(() => adminKey.trim().length > 0, [adminKey]);

  useEffect(() => {
    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
        return;
      }

      setSession(storedSession);
      setTargetUsername(storedSession.username);
      void loadBranches();
      if (storedSession.agent_id) {
        setTargetAgentId(String(storedSession.agent_id));
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
        setTargetAgentId(String(agent.id));
      } catch {
        setError(copy.noAgentForSession);
      }
    }

    bootstrap();
  }, [router]);

  function adminHeaders() {
    return {
      "X-Loop-Admin-Key": adminKey.trim(),
    };
  }

  async function loadBranches() {
    setIsLoadingBranches(true);
    try {
      const result = await apiRequest<unknown>(BRANCHES_ENDPOINT);
      const branchList = normalizeBranches(result);
      setBranches(branchList);
      if (!branchList.includes(targetBranch)) {
        setTargetBranch(DEFAULT_BRANCH_ID);
      }
      if (!branchList.includes(purgeBranchId)) {
        setPurgeBranchId(DEFAULT_BRANCH_ID);
      }
    } catch (err) {
      setBranches([DEFAULT_BRANCH_ID]);
      setTargetBranch(DEFAULT_BRANCH_ID);
      setPurgeBranchId(DEFAULT_BRANCH_ID);
      setError(
        err instanceof Error
          ? t.common.branchUnavailable(err.message)
          : t.common.branchUnavailable(),
      );
    } finally {
      setIsLoadingBranches(false);
    }
  }

  async function loadAgentChoices() {
    if (!hasAdminKey) {
      return;
    }

    setError("");
    setMessage("");
    setIsLoadingAgents(true);
    try {
      const choices = await apiRequest<AgentSessionChoice[]>(
        "/api/users/agent-choices",
        {
          headers: adminHeaders(),
        },
      );
      setAgentChoices(choices);
      setMessage(copy.loadedChoices(choices.length));
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.loadAgentsFailed);
    } finally {
      setIsLoadingAgents(false);
    }
  }

  function chooseAgent(agentId: string) {
    const choice = agentChoices.find(
      (item) => String(item.agent.id) === agentId,
    );
    if (!choice) {
      return;
    }

    setTargetAgentId(String(choice.agent.id));
    setTargetUsername(choice.user.username);
  }

  function updateTargetBranch(branchId: string) {
    setTargetBranch(branchId.trim() || DEFAULT_BRANCH_ID);
    setSinglePost(null);
    setTickPosts([]);
    setExportPreview("");
    setExportMeta("");
    setPurgeResult(null);
    setMessage("");
    setError("");
  }

  async function checkHealth() {
    setError("");
    setMessage("");
    setIsCheckingHealth(true);
    try {
      const result = await apiRequest<HealthResponse>("/health");
      setHealth(result);
      setMessage(copy.healthSucceeded);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.healthFailed);
    } finally {
      setIsCheckingHealth(false);
    }
  }

  async function simulateOne(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!hasAdminKey || !targetUsername.trim()) {
      return;
    }

    setError("");
    setMessage("");
    setIsSimulatingOne(true);
    try {
      const post = await apiRequest<PostOut>(
        SIMULATE_USER_POST_ENDPOINT(targetUsername.trim(), targetBranch),
        {
          method: "POST",
          headers: adminHeaders(),
        },
      );
      setSinglePost(post);
      setMessage(copy.generatedOne(targetUsername.trim(), targetBranch));
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.singleSimulationFailed);
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
      const posts = await apiRequest<PostOut[]>(
        SIMULATE_TICK_ENDPOINT(targetBranch),
        {
          method: "POST",
          headers: adminHeaders(),
        },
      );
      setTickPosts(posts);
      setMessage(copy.tickCreated(posts.length, targetBranch));
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.tickFailed);
    } finally {
      setIsTicking(false);
    }
  }

  async function exportJsonl(kind: ExportKind) {
    if (!hasAdminKey || !targetUsername.trim()) {
      return;
    }

    setError("");
    setMessage("");
    setIsExporting(kind);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/export/by-username/${encodeURIComponent(
          targetUsername.trim(),
        )}/${kind}?branch_id=${encodeURIComponent(targetBranch)}`,
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
      const filename = `loop_user_${targetUsername.trim()}_${targetBranch}_${kind}.jsonl`;
      downloadText(filename, text);
      setExportPreview(text.split("\n").slice(0, 8).join("\n"));
      setExportMeta(
        `${filename} · ${text.length.toLocaleString()} ${t.common.characters}`,
      );
      setMessage(copy.exported(kind, targetUsername.trim(), targetBranch));
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.exportFailed);
    } finally {
      setIsExporting(null);
    }
  }

  async function purgeBranch() {
    if (!hasAdminKey || !purgeBranchId || purgeBranchId === DEFAULT_BRANCH_ID) {
      return;
    }
    if (!window.confirm(copy.purgeConfirm)) {
      return;
    }

    setError("");
    setMessage("");
    setIsPurgingBranch(true);
    try {
      const result = await apiRequest<BranchPurgeResult>(PURGE_BRANCH_ENDPOINT, {
        method: "POST",
        headers: adminHeaders(),
        body: JSON.stringify({ branch_id: purgeBranchId }),
      });

      setSinglePost(null);
      setTickPosts([]);
      setExportPreview("");
      setExportMeta("");
      setTargetBranch(DEFAULT_BRANCH_ID);
      setPurgeBranchId(DEFAULT_BRANCH_ID);
      setPurgeResult(result);
      setMessage(result.message);
      await loadBranches();
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.purgeFailed);
    } finally {
      setIsPurgingBranch(false);
    }
  }

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">{copy.loading}</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
        <header className="mb-6 border-b border-gray-200 pb-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
            {copy.eyebrow}
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950">
            {copy.title}
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-500">
            {copy.subtitle}
          </p>
          <p className="mt-2 text-sm text-gray-400">
            {t.common.signedInAs}{" "}
            <span className="font-medium text-gray-600">@{session.username}</span>
            {" · "}
            <span className="font-medium text-gray-600">
              {session.agent_name ?? t.common.noAgentYet}
            </span>
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
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_190px_220px_220px_auto]">
            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                {t.common.adminApiKey}
              </span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                onChange={(event) => setAdminKey(event.target.value)}
                placeholder={copy.adminPlaceholder}
                type="password"
                value={adminKey}
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                {t.common.username}
              </span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                onChange={(event) => setTargetUsername(event.target.value)}
                placeholder={copy.participantPlaceholder}
                value={targetUsername}
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                {copy.agentPicker}
              </span>
              <select
                className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                onChange={(event) => chooseAgent(event.target.value)}
                value={targetAgentId}
              >
                <option value="">{t.common.chooseLoadedAgent}</option>
                {agentChoices.map((choice) => (
                  <option key={choice.agent.id} value={choice.agent.id}>
                    @{choice.user.username} · {choice.agent.agent_name}
                  </option>
                ))}
              </select>
            </label>
            <BranchSelector
              branches={branches}
              label={copy.targetBranch}
              loadingLabel={t.common.loading}
              refreshLabel={t.common.refreshBranches}
              isLoading={isLoadingBranches}
              onChange={updateTargetBranch}
              onRefresh={loadBranches}
              value={targetBranch}
            />
            <div className="flex items-end">
              <button
                className="w-full rounded-full bg-gray-950 px-4 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60 md:w-auto"
                disabled={!hasAdminKey || isLoadingAgents}
                onClick={loadAgentChoices}
                type="button"
              >
                {isLoadingAgents ? t.common.loading : copy.loadAgents}
              </button>
            </div>
          </div>
        </section>

        <div className="grid gap-5 lg:grid-cols-2">
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-gray-950">
                  {copy.backendHealth}
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  {copy.backendHealthDescription}
                </p>
              </div>
              <button
                className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={isCheckingHealth}
                onClick={checkHealth}
                type="button"
              >
                {isCheckingHealth ? copy.checking : copy.checkHealth}
              </button>
            </div>
            {health ? (
              <dl className="mt-4 grid grid-cols-2 gap-3">
                <Metric label={copy.metricStatus} value={health.status} />
                <Metric label={copy.metricService} value={health.service} />
              </dl>
            ) : (
              <p className="mt-4 text-sm text-gray-500">
                {copy.backendHealthEmpty}
              </p>
            )}
          </section>

          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-gray-950">
              {copy.exports}
            </h2>
            <p className="mt-1 text-sm leading-6 text-gray-500">
              {copy.exportsDescription}
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <button
                className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={!hasAdminKey || !targetUsername || isExporting !== null}
                onClick={() => exportJsonl("chatlogs")}
                type="button"
              >
                {isExporting === "chatlogs" ? copy.exporting : copy.exportChatlogs}
              </button>
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={!hasAdminKey || !targetUsername || isExporting !== null}
                onClick={() => exportJsonl("feedbacks")}
                type="button"
              >
                {isExporting === "feedbacks" ? copy.exporting : copy.exportFeedbacks}
              </button>
            </div>
            {exportMeta ? (
              <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                  {copy.lastExport}
                </p>
                <p className="mt-1 text-sm font-medium text-gray-700">{exportMeta}</p>
                <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-white p-3 text-xs leading-5 text-gray-700">
                  {exportPreview || `(${t.common.emptyFile})`}
                </pre>
              </div>
            ) : null}
          </section>

          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <form onSubmit={simulateOne}>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-950">
                    {copy.singleSimulation}
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-gray-500">
                    {copy.singleSimulationDescription}
                  </p>
                </div>
                <button
                  className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={!hasAdminKey || !targetUsername || isSimulatingOne}
                  type="submit"
                >
                  {isSimulatingOne ? copy.generating : copy.generatePost}
                </button>
              </div>
            </form>
            {singlePost ? <PostPreview post={singlePost} /> : null}
          </section>

          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-gray-950">
                  {copy.globalTick}
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  {copy.globalTickDescription}
                </p>
              </div>
              <button
                className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={!hasAdminKey || isTicking}
                onClick={simulateTick}
                type="button"
              >
                {isTicking ? copy.running : copy.runTick}
              </button>
            </div>
            <div className="mt-4 space-y-3">
              {tickPosts.length > 0 ? (
                tickPosts.map((post) => <PostPreview key={post.id} post={post} />)
              ) : (
                <p className="text-sm text-gray-500">
                  {copy.tickEmpty}
                </p>
              )}
            </div>
          </section>
        </div>

        <section className="mt-6 rounded-xl border border-rose-300 bg-rose-50 p-5 shadow-sm">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-2xl">
              <p className="text-xs font-semibold uppercase tracking-wide text-rose-600">
                {copy.danger}
              </p>
              <h2 className="mt-2 text-lg font-semibold text-rose-950">
                {copy.purgeTitle}
              </h2>
              <p className="mt-2 text-sm leading-6 text-rose-700">
                {copy.purgeDescription}
              </p>
            </div>
            <div className="grid w-full gap-3 sm:grid-cols-[minmax(0,1fr)_auto] lg:max-w-xl">
              <label className="block">
                <span className="text-sm font-medium text-rose-900">
                  {copy.branchToPurge}
                </span>
                <select
                  className="mt-2 w-full rounded-xl border border-rose-200 bg-white px-4 py-3 text-sm text-rose-950 outline-none transition focus:border-rose-400 focus:ring-4 focus:ring-rose-100"
                  disabled={isLoadingBranches || isPurgingBranch}
                  onChange={(event) => setPurgeBranchId(event.target.value)}
                  value={purgeBranchId}
                >
                  {branches.map((branchId) => (
                    <option key={branchId} value={branchId}>
                      {branchId === DEFAULT_BRANCH_ID
                        ? `${branchId} (${t.common.protected})`
                        : branchId}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex items-end">
                <button
                  className="w-full rounded-full bg-rose-600 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
                  disabled={
                    !hasAdminKey ||
                    purgeBranchId === DEFAULT_BRANCH_ID ||
                    isPurgingBranch
                  }
                  onClick={purgeBranch}
                  type="button"
                >
                  {isPurgingBranch
                    ? copy.purging
                    : copy.purgeData}
                </button>
              </div>
            </div>
          </div>
          {purgeResult ? (
            <div className="mt-5 rounded-lg border border-rose-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-rose-500">
                    {copy.lastPurgeLog}
                  </p>
                  <p className="mt-1 text-sm font-semibold text-rose-950">
                    {purgeResult.branch_id} ·{" "}
                    {purgeResult.is_clean ? copy.clean : copy.needsReview}
                  </p>
                </div>
                <span
                  className={`rounded-full px-3 py-1 text-xs font-semibold ${
                    purgeResult.is_clean
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-amber-50 text-amber-700"
                  }`}
                >
                  {purgeResult.is_clean ? copy.verifiedClean : copy.residualRecords}
                </span>
              </div>
              <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Metric label={copy.metricEventsDeleted} value={purgeResult.events_deleted} />
                <Metric label={copy.metricPostsDeleted} value={purgeResult.posts_deleted} />
                <Metric
                  label={copy.metricChatLogsDeleted}
                  value={purgeResult.chat_logs_deleted}
                />
                <Metric
                  label={copy.metricFeedbacksDeleted}
                  value={purgeResult.feedback_logs_deleted}
                />
              </dl>
              <pre className="mt-4 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-rose-950 p-3 text-xs leading-5 text-rose-50">
                {purgeResult.deletion_log.join("\n")}
              </pre>
            </div>
          ) : null}
        </section>
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
        {post.branch_id ? <span>{post.branch_id}</span> : null}
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

function normalizeBranches(result: unknown) {
  const rawBranches =
    result && typeof result === "object"
      ? "branch_ids" in result
        ? (result as { branch_ids?: unknown }).branch_ids
        : "branches" in result
          ? (result as { branches?: unknown }).branches
          : result
      : result;

  const branches = Array.isArray(rawBranches)
    ? rawBranches
        .map((item) => {
          if (typeof item === "string") {
            return item;
          }
          if (item && typeof item === "object" && "branch_id" in item) {
            return String((item as { branch_id: unknown }).branch_id);
          }
          return "";
        })
        .map((branchId) => branchId.trim())
        .filter(Boolean)
    : [];

  return Array.from(new Set([DEFAULT_BRANCH_ID, ...branches])).sort(
    (left, right) => {
      if (left === DEFAULT_BRANCH_ID) {
        return -1;
      }
      if (right === DEFAULT_BRANCH_ID) {
        return 1;
      }
      return left.localeCompare(right);
    },
  );
}
