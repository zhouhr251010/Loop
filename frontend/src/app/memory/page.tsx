"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { BranchSelector } from "@/components/BranchSelector";
import { useLanguage } from "@/components/LanguageContext";
import {
  Agent,
  AgentWorkingMemoryState,
  GlobalSystemSettings,
  MemoryConsolidationAcceptedResponse,
  MemoryConsolidationResponse,
  MemorySearchResponse,
  MemoryUploadResponse,
  PersonalizedPostPreview,
  Relationship,
  apiRequest,
} from "@/lib/api";
import { LoopSession, loadSession, saveSession } from "@/lib/session";
import { formatFeedTime, formatLocalDateTime, parseUtcTimestamp } from "@/lib/time";

const DEFAULT_BRANCH_ID = "main";
const RELATIONSHIP_PREVIEW_LIMIT = 10;
const FEED_PREVIEW_LIMIT = 5;

const BRANCHES_ENDPOINT = (agentId: number) =>
  `/api/simulation/agents/${agentId}/branches`;

const MEMORY_STATE_ENDPOINT = (branchId: string) =>
  `/api/agents/me/memory/state?branch_id=${encodeURIComponent(branchId)}`;

const CLEAR_MEMORY_ENDPOINT = (branchId: string) =>
  `/api/agents/me/memory/clear?branch_id=${encodeURIComponent(branchId)}`;

const RELATIONSHIPS_PREVIEW_ENDPOINT =
  `/api/agents/me/relationships?limit=${RELATIONSHIP_PREVIEW_LIMIT}`;

const FEED_PREVIEW_ENDPOINT =
  `/api/agents/me/feed-preview?limit=${FEED_PREVIEW_LIMIT}`;

export default function MemoryPage() {
  const router = useRouter();
  const { t } = useLanguage();
  const copy = t.memory;
  const diagnosticsRequestIdRef = useRef(0);
  const [session, setSession] = useState<LoopSession | null>(null);
  const [branches, setBranches] = useState<string[]>([DEFAULT_BRANCH_ID]);
  const [currentBranch, setCurrentBranch] = useState(DEFAULT_BRANCH_ID);
  const [systemSettings, setSystemSettings] =
    useState<GlobalSystemSettings | null>(null);
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
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const canSwitchBranches =
    session?.is_admin === true || systemSettings?.allow_user_branch_switch === true;

  useEffect(() => {
    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
        return;
      }

      let initialBranch = DEFAULT_BRANCH_ID;
      let allowInitialBranchSwitch = storedSession.is_admin;
      if (!storedSession.is_admin) {
        try {
          const settings = await apiRequest<GlobalSystemSettings>(
            "/api/simulation/settings",
          );
          initialBranch = settings.global_active_branch?.trim() || DEFAULT_BRANCH_ID;
          allowInitialBranchSwitch = settings.allow_user_branch_switch;
          setSystemSettings(settings);
          setBranches((currentBranches) =>
            Array.from(new Set([initialBranch, ...currentBranches])),
          );
        } catch {
          setSystemSettings({
            allow_user_branch_switch: false,
            global_active_branch: DEFAULT_BRANCH_ID,
          });
        }
      }
      setCurrentBranch(initialBranch);

      try {
        const agent = await apiRequest<Agent>("/api/users/me/agent");
        const hydratedSession = {
          ...storedSession,
          agent_id: agent.id,
          agent_name: agent.agent_name,
          agent_is_npc: agent.is_npc,
        };
        saveSession(hydratedSession);
        setSession(hydratedSession);
        if (allowInitialBranchSwitch) {
          void loadBranches(hydratedSession.agent_id);
        }
        await refreshDiagnostics(hydratedSession, initialBranch);
      } catch {
        setSession(storedSession);
        setError(copy.noAgent);
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

  async function loadBranches(agentId: number) {
    setIsLoadingBranches(true);
    try {
      const result = await apiRequest<unknown>(BRANCHES_ENDPOINT(agentId));
      const branchList = normalizeBranches(result);
      setBranches(branchList);
      if (!branchList.includes(currentBranch)) {
        setCurrentBranch(DEFAULT_BRANCH_ID);
      }
    } catch (err) {
      setBranches([DEFAULT_BRANCH_ID]);
      setCurrentBranch(DEFAULT_BRANCH_ID);
      setToast(
        err instanceof Error
          ? t.common.branchUnavailable(err.message)
          : t.common.branchUnavailable(),
      );
    } finally {
      setIsLoadingBranches(false);
    }
  }

  async function refreshDiagnostics(
    targetSession = session,
    branchId = currentBranch,
  ) {
    if (!targetSession?.agent_id) {
      return;
    }

    const normalizedBranchId = branchId.trim() || DEFAULT_BRANCH_ID;
    const requestId = diagnosticsRequestIdRef.current + 1;
    diagnosticsRequestIdRef.current = requestId;
    setIsRefreshing(true);
    setError("");
    try {
      const [state, graph, preview] = await Promise.all([
        apiRequest<AgentWorkingMemoryState>(
          MEMORY_STATE_ENDPOINT(normalizedBranchId),
        ),
        apiRequest<Relationship[]>(
          RELATIONSHIPS_PREVIEW_ENDPOINT,
        ),
        apiRequest<PersonalizedPostPreview[]>(
          FEED_PREVIEW_ENDPOINT,
        ),
      ]);
      if (diagnosticsRequestIdRef.current !== requestId) {
        return;
      }
      setWorkingState(state);
      setRelationships(graph);
      setFeedPreview(preview);
    } catch (err) {
      if (diagnosticsRequestIdRef.current !== requestId) {
        return;
      }
      setError(err instanceof Error ? err.message : copy.refreshFailed);
    } finally {
      if (diagnosticsRequestIdRef.current === requestId) {
        setIsRefreshing(false);
      }
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
      setToast(copy.uploadSuccess(result.chunks_added));
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.uploadFailed);
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
      setError(err instanceof Error ? err.message : copy.searchFailed);
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
      const result = await apiRequest<MemoryConsolidationAcceptedResponse>(
        "/api/agents/me/sleep",
        { method: "POST" },
      );
      setSleepResult(null);
      setToast(result.message);
      await refreshDiagnostics(session, currentBranch);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.sleepFailed);
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
        CLEAR_MEMORY_ENDPOINT(currentBranch),
        { method: "POST" },
      );
      setWorkingState(state);
      setToast(copy.stmCleared);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.clearFailed);
    } finally {
      setIsClearing(false);
    }
  }

  function updateCurrentBranch(branchId: string) {
    const nextBranch = branchId.trim() || DEFAULT_BRANCH_ID;
    setCurrentBranch(nextBranch);
    setWorkingState(null);
    setSearchResult(null);
    setError("");
    setToast("");
    void refreshDiagnostics(session, nextBranch);
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
            Memory Lab
          </p>
          <div className="mt-2 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-3xl font-bold tracking-tight text-gray-950">
                {copy.title}
              </h1>
              <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-500">
                {copy.subtitle}
              </p>
              <p className="mt-2 text-sm text-gray-400">
                {t.common.testingAs}{" "}
                <span className="font-medium text-gray-600">@{session.username}</span>
                {" · "}
                <span className="font-medium text-gray-600">
                  {session.agent_name ?? t.common.noAgentYet}
                </span>
              </p>
            </div>
            <button
              className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isRefreshing || isLoadingBranches || !session.agent_id}
              onClick={() => refreshDiagnostics(session, currentBranch)}
              type="button"
            >
              {isRefreshing ? t.common.refreshing : copy.refreshDiagnostics}
            </button>
          </div>
          {canSwitchBranches ? (
            <div className="mt-5 flex flex-col gap-3 rounded-xl border border-gray-200 bg-white px-4 py-4 shadow-sm sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p
                  className={`text-xs font-semibold uppercase tracking-wide ${
                    currentBranch === DEFAULT_BRANCH_ID
                      ? "text-gray-500"
                      : "text-purple-600"
                  }`}
                >
                  {copy.currentTimeline}
                </p>
                <p className="mt-1 text-sm font-medium text-gray-700">
                  {copy.currentView(currentBranch)}
                </p>
              </div>
              <BranchSelector
                branches={branches}
                disabled={!session.agent_id || isRefreshing}
                isLoading={isLoadingBranches}
                label={t.common.branchSelector}
                loadingLabel={t.common.loading}
                onChange={updateCurrentBranch}
                onRefresh={() => session.agent_id && loadBranches(session.agent_id)}
                refreshLabel={t.common.refreshBranches}
                value={currentBranch}
              />
            </div>
          ) : null}
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
                  {copy.uploadTitle}
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  {copy.uploadHelp}
                </p>
              </div>
              <label className="block">
                <span className="text-sm font-medium text-gray-700">
                  {copy.memoryContent}
                </span>
                <textarea
                  className="mt-3 min-h-64 w-full resize-y rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                  disabled={isUploading}
                  onChange={(event) => setContent(event.target.value)}
                  placeholder={copy.uploadPlaceholder}
                  required
                  value={content}
                />
              </label>

              <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-gray-400">
                  {copy.readyChars(content.trim().length.toLocaleString())}
                </p>
                <button
                  className="rounded-full bg-gray-950 px-5 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isUploading || !content.trim()}
                  type="submit"
                >
                  {isUploading ? copy.uploading : copy.uploadMemory}
                </button>
              </div>
            </form>

            <form
              className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
              onSubmit={searchMemory}
            >
              <div className="mb-4">
                <h2 className="text-lg font-semibold text-gray-950">
                  {copy.searchTitle}
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  {copy.searchHelp}
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
                <label className="block">
                  <span className="text-sm font-medium text-gray-700">
                    {copy.query}
                  </span>
                  <input
                    className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder={copy.queryPlaceholder}
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
                  {isSearching ? copy.searching : copy.searchMemory}
                </button>
              </div>
            </form>

            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h2 className="text-lg font-semibold text-gray-950">
                {copy.resultsTitle}
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
                      {copy.noChunks}
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-3 text-sm text-gray-500">
                  {copy.resultsEmpty}
                </p>
              )}
            </section>
          </section>

          <section className="space-y-5">
            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-950">
                    {copy.sleepTitle}
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-gray-500">
                    {copy.sleepHelp}
                  </p>
                </div>
                <button
                  className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isSleeping || !session.agent_id}
                  onClick={sleepAgent}
                  type="button"
                >
                  {isSleeping ? copy.sleeping : copy.triggerSleep}
                </button>
              </div>
              {sleepResult ? (
                <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                  <Metric label={copy.metricRecords} value={sleepResult.records_consolidated} />
                  <Metric label={copy.metricChunks} value={sleepResult.chunks_added} />
                  <Metric
                    label={copy.metricRelations}
                    value={sleepResult.relationship_updates.length}
                  />
                  <Metric
                    label={copy.metricStmCleared}
                    value={sleepResult.graph_memory_cleared ? copy.yes : copy.no}
                  />
                </dl>
              ) : (
                <p className="mt-4 text-sm text-gray-500">
                  {copy.sleepEmpty}
                </p>
              )}
            </section>

            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-950">
                    {copy.stmTitle}
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-gray-500">
                    {copy.stmHelp}
                  </p>
                </div>
                <button
                  className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isClearing || !session.agent_id}
                  onClick={clearWorkingMemory}
                  type="button"
                >
                  {isClearing ? copy.clearing : copy.clearStm}
                </button>
              </div>
              {workingState ? (
                <div className="mt-4 space-y-3">
                  <dl className="grid grid-cols-2 gap-3 text-sm">
                    <Metric
                      label={copy.metricAvailable}
                      value={workingState.graph_available ? copy.yes : copy.no}
                    />
                    <Metric label={copy.metricMessages} value={workingState.message_count} />
                    <Metric
                      label={copy.metricWorking}
                      value={workingState.working_message_count}
                    />
                    <Metric label={copy.metricEnergy} value={workingState.energy} />
                  </dl>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                      {copy.coreMemory} · {workingState.branch_id}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-gray-700">
                      {workingState.current_core_memory || copy.noCoreMemory}
                    </p>
                  </div>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                      {copy.emotion}
                    </p>
                    <p className="mt-1 text-sm text-gray-700">
                      {workingState.emotion}
                    </p>
                  </div>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                      {copy.summary}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-gray-700">
                      {workingState.summary || copy.noSummary}
                    </p>
                  </div>
                  {workingState.error ? (
                    <p className="text-xs leading-5 text-amber-600">
                      {copy.graphUnavailable}: {workingState.error}
                    </p>
                  ) : null}
                </div>
              ) : (
                <p className="mt-4 text-sm text-gray-500">
                  {copy.stmEmpty}
                </p>
              )}
            </section>

            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-gray-950">
                    {copy.socialGraph}
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-gray-500">
                    {copy.socialGraphHelp(RELATIONSHIP_PREVIEW_LIMIT)}
                  </p>
                </div>
                <button
                  className="shrink-0 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:border-gray-300 hover:bg-gray-100"
                  onClick={() => setToast(copy.viewAllComingSoon)}
                  type="button"
                >
                  {copy.viewAll}
                </button>
              </div>
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
                    {copy.noRelationships}
                  </p>
                )}
              </div>
            </section>

            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-gray-950">
                    {copy.feedPreview}
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-gray-500">
                    {copy.feedPreviewHelp(FEED_PREVIEW_LIMIT)}
                  </p>
                </div>
                <button
                  className="shrink-0 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:border-gray-300 hover:bg-gray-100"
                  onClick={() => setToast(copy.viewAllComingSoon)}
                  type="button"
                >
                  {copy.viewAll}
                </button>
              </div>
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
                    {copy.noPreview}
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
