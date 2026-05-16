"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BranchSelector } from "@/components/BranchSelector";
import { useLanguage } from "@/components/LanguageContext";
import {
  API_BASE_URL,
  Agent,
  AgentDeletionResponse,
  AgentSessionChoice,
  AuthSession,
  GlobalSystemSettings,
  HealthResponse,
  PostOut,
  apiRequest,
  formatAgentChoiceLabel,
} from "@/lib/api";
import {
  LoopSession,
  getAccessToken,
  loadSession,
  saveAdminBackupSession,
  saveSession,
} from "@/lib/session";
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
  const [targetAgentId, setTargetAgentId] = useState("");
  const [targetUsername, setTargetUsername] = useState("");
  const [targetBranch, setTargetBranch] = useState(DEFAULT_BRANCH_ID);
  const [purgeBranchId, setPurgeBranchId] = useState(DEFAULT_BRANCH_ID);
  const [branches, setBranches] = useState<string[]>([DEFAULT_BRANCH_ID]);
  const [allowUserBranchSwitch, setAllowUserBranchSwitch] = useState(false);
  const [globalActiveBranch, setGlobalActiveBranch] = useState(DEFAULT_BRANCH_ID);
  const [agentChoices, setAgentChoices] = useState<AgentSessionChoice[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [singlePost, setSinglePost] = useState<PostOut | null>(null);
  const [tickPosts, setTickPosts] = useState<PostOut[]>([]);
  const [exportPreview, setExportPreview] = useState("");
  const [exportMeta, setExportMeta] = useState("");
  const [purgeResult, setPurgeResult] = useState<BranchPurgeResult | null>(null);
  const [agentDeleteResult, setAgentDeleteResult] =
    useState<AgentDeletionResponse | null>(null);
  const [agentPendingDelete, setAgentPendingDelete] =
    useState<AgentSessionChoice | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);
  const [isSimulatingOne, setIsSimulatingOne] = useState(false);
  const [isTicking, setIsTicking] = useState(false);
  const [isExporting, setIsExporting] = useState<ExportKind | null>(null);
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [isLoadingSettings, setIsLoadingSettings] = useState(false);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [isPurgingBranch, setIsPurgingBranch] = useState(false);
  const [isDeletingAgent, setIsDeletingAgent] = useState(false);
  const [impersonatingAgentId, setImpersonatingAgentId] = useState<number | null>(null);
  const settingBranches = normalizeBranches([globalActiveBranch, ...branches]);

  useEffect(() => {
    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
        return;
      }
      if (!storedSession.is_admin) {
        router.replace("/plaza");
        return;
      }

      setSession(storedSession);
      setTargetUsername(storedSession.username);
      void loadBranches();
      void loadSettings();
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
          agent_is_npc: agent.is_npc,
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

  async function loadSettings() {
    setIsLoadingSettings(true);
    try {
      const settings = await apiRequest<GlobalSystemSettings>(
        "/api/simulation/settings",
      );
      const branchId = settings.global_active_branch?.trim() || DEFAULT_BRANCH_ID;
      setAllowUserBranchSwitch(settings.allow_user_branch_switch);
      setGlobalActiveBranch(branchId);
      setBranches((currentBranches) =>
        normalizeBranches([branchId, ...currentBranches]),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.settingsLoadFailed);
    } finally {
      setIsLoadingSettings(false);
    }
  }

  async function saveSettings() {
    setError("");
    setMessage("");
    setIsSavingSettings(true);
    try {
      const settings = await apiRequest<GlobalSystemSettings>(
        "/api/simulation/settings",
        {
          method: "PATCH",
          body: JSON.stringify({
            allow_user_branch_switch: allowUserBranchSwitch,
            global_active_branch: globalActiveBranch,
          }),
        },
      );
      const branchId = settings.global_active_branch?.trim() || DEFAULT_BRANCH_ID;
      setAllowUserBranchSwitch(settings.allow_user_branch_switch);
      setGlobalActiveBranch(branchId);
      setBranches((currentBranches) =>
        normalizeBranches([branchId, ...currentBranches]),
      );
      setMessage(copy.settingsSaved(branchId));
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.settingsSaveFailed);
    } finally {
      setIsSavingSettings(false);
    }
  }

  async function loadAgentChoices() {
    setError("");
    setMessage("");
    setIsLoadingAgents(true);
    try {
      const choices = await apiRequest<AgentSessionChoice[]>(
        "/api/users/agent-choices",
      );
      setAgentChoices(choices);
      setAgentDeleteResult(null);
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

  async function impersonateAgent(choice: AgentSessionChoice) {
    if (!session?.is_admin) {
      setError(copy.noAgentForSession);
      return;
    }

    setError("");
    setMessage("");
    setImpersonatingAgentId(choice.agent.id);
    try {
      const authSession = await apiRequest<AuthSession>(
        `/api/users/agent-choices/${choice.agent.id}/session`,
        { method: "POST" },
      );
      saveAdminBackupSession(session);
      saveSession({
        user_id: authSession.user.id,
        username: authSession.user.username,
        is_admin: authSession.user.is_admin,
        access_token: authSession.access_token,
        token_expires_at: Date.now() + authSession.expires_in * 1000,
        agent_id: choice.agent.id,
        agent_name: choice.agent.agent_name,
        agent_is_npc: choice.agent.is_npc,
      });
      setMessage(copy.impersonated(choice.user.username));
      window.location.href = "/chat";
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.impersonateFailed);
    } finally {
      setImpersonatingAgentId(null);
    }
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
    if (!targetUsername.trim()) {
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
    setError("");
    setMessage("");
    setIsTicking(true);
    try {
      const posts = await apiRequest<PostOut[]>(
        SIMULATE_TICK_ENDPOINT(targetBranch),
        {
          method: "POST",
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
    if (!targetUsername.trim()) {
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
    if (!purgeBranchId || purgeBranchId === DEFAULT_BRANCH_ID) {
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

  function requestDeleteAgent(choice: AgentSessionChoice) {
    setAgentPendingDelete(choice);
    setAgentDeleteResult(null);
    setError("");
    setMessage("");
  }

  async function confirmDeleteAgent() {
    if (!agentPendingDelete) {
      return;
    }

    const deletingAgentId = agentPendingDelete.agent.id;
    setError("");
    setMessage("");
    setIsDeletingAgent(true);
    try {
      const result = await apiRequest<AgentDeletionResponse>(
        `/api/agents/${deletingAgentId}`,
        {
          method: "DELETE",
        },
      );
      setAgentChoices((choices) =>
        choices.filter((choice) => choice.agent.id !== deletingAgentId),
      );
      if (targetAgentId === String(deletingAgentId)) {
        setTargetAgentId("");
        setTargetUsername(session?.username ?? "");
      }
      if (session?.agent_id === deletingAgentId) {
        const updatedSession = {
          ...session,
          agent_id: undefined,
          agent_name: undefined,
          agent_is_npc: undefined,
        };
        saveSession(updatedSession);
        setSession(updatedSession);
      }
      setAgentPendingDelete(null);
      setAgentDeleteResult(result);
      setMessage(copy.agentDeleted(result.agent_name, result.agent_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.deleteAgentFailed);
    } finally {
      setIsDeletingAgent(false);
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

        <section className="mb-5 rounded-xl border border-indigo-200 bg-white p-5 shadow-sm">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
                {copy.globalStageEyebrow}
              </p>
              <h2 className="mt-1 text-lg font-semibold text-gray-950">
                {copy.globalStageTitle}
              </h2>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-gray-500">
                {copy.globalStageDescription}
              </p>
            </div>
            <button
              className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isSavingSettings || isLoadingSettings}
              onClick={saveSettings}
              type="button"
            >
              {isSavingSettings ? t.common.submitting : copy.saveSettings}
            </button>
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.8fr)] lg:items-end">
            <label className="flex min-h-[4.5rem] items-center justify-between gap-4 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
              <span>
                <span className="block text-sm font-semibold text-gray-900">
                  {copy.allowUserBranchSwitch}
                </span>
                <span className="mt-1 block text-xs leading-5 text-gray-500">
                  {copy.allowUserBranchSwitchHelp}
                </span>
              </span>
              <input
                checked={allowUserBranchSwitch}
                className="h-5 w-5 accent-gray-950"
                disabled={isSavingSettings || isLoadingSettings}
                onChange={(event) => setAllowUserBranchSwitch(event.target.checked)}
                type="checkbox"
              />
            </label>
            <BranchSelector
              branches={settingBranches}
              disabled={isSavingSettings || isLoadingSettings}
              isLoading={isLoadingBranches}
              label={copy.globalActiveBranch}
              loadingLabel={t.common.loading}
              onChange={setGlobalActiveBranch}
              onRefresh={loadBranches}
              refreshLabel={t.common.refreshBranches}
              value={globalActiveBranch}
            />
          </div>
        </section>

        <section className="mb-5 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px_220px_auto]">
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
                    {formatAgentChoiceLabel(choice)}
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
                disabled={isLoadingAgents}
                onClick={loadAgentChoices}
                type="button"
              >
                {isLoadingAgents ? t.common.loading : copy.loadAgents}
              </button>
            </div>
          </div>
        </section>

        <section className="mb-5 rounded-xl border border-indigo-200 bg-indigo-50/50 p-5 shadow-sm">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="text-lg font-semibold text-gray-950">
                {copy.impersonationTitle}
              </h2>
              <p className="mt-1 text-sm leading-6 text-gray-600">
                {copy.impersonationDescription}
              </p>
            </div>
            <button
              className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isLoadingAgents}
              onClick={loadAgentChoices}
              type="button"
            >
              {isLoadingAgents ? t.common.loading : copy.loadAgents}
            </button>
          </div>
          {agentChoices.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-lg border border-indigo-100 bg-white">
              <div className="divide-y divide-gray-100">
                {agentChoices.map((choice) => (
                  <div
                    className="grid gap-3 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                    key={choice.agent.id}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-gray-950">
                        {formatAgentChoiceLabel(choice)}
                      </p>
                      <p className="mt-1 text-xs text-gray-500">
                        Agent #{choice.agent.id} · User #{choice.user.id}
                      </p>
                    </div>
                    <button
                      className="rounded-full border border-indigo-200 bg-indigo-50 px-4 py-2 text-sm font-semibold text-indigo-700 transition hover:border-indigo-300 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={impersonatingAgentId === choice.agent.id}
                      onClick={() => impersonateAgent(choice)}
                      type="button"
                    >
                      {impersonatingAgentId === choice.agent.id
                        ? copy.impersonating
                        : copy.impersonate}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </section>

        {agentChoices.length > 0 || agentDeleteResult ? (
          <section className="mb-5 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-gray-950">
                  {copy.agentManagement}
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  {copy.agentManagementDescription}
                </p>
              </div>
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={isLoadingAgents}
                onClick={loadAgentChoices}
                type="button"
              >
                {isLoadingAgents ? t.common.loading : t.common.refreshing}
              </button>
            </div>
            {agentChoices.length > 0 ? (
              <div className="mt-4 overflow-hidden rounded-lg border border-gray-200">
                <div className="divide-y divide-gray-100">
                  {agentChoices.map((choice) => (
                    <div
                      className="grid gap-3 bg-white px-4 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                      key={choice.agent.id}
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="truncate text-sm font-semibold text-gray-950">
                            {formatAgentChoiceLabel(choice)}
                          </p>
                          {choice.agent.is_npc ? (
                            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700">
                              NPC
                            </span>
                          ) : null}
                        </div>
                        <p className="mt-1 text-xs text-gray-500">
                          Agent #{choice.agent.id} · User #{choice.user.id}
                        </p>
                      </div>
                      <button
                        className="rounded-full border border-rose-200 bg-white px-4 py-2 text-sm font-semibold text-rose-700 transition hover:border-rose-300 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-60"
                        disabled={isDeletingAgent}
                        onClick={() => requestDeleteAgent(choice)}
                        type="button"
                      >
                        {copy.deleteAgent}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {agentDeleteResult ? (
              <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Metric
                  label={copy.metricEventsDeleted}
                  value={agentDeleteResult.event_logs_deleted}
                />
                <Metric
                  label={copy.metricChatLogsDeleted}
                  value={agentDeleteResult.chat_logs_deleted}
                />
                <Metric
                  label={copy.metricVectorMemoriesDeleted}
                  value={agentDeleteResult.vector_memories_deleted}
                />
                <Metric
                  label={copy.metricRelationshipsDeleted}
                  value={agentDeleteResult.relationships_deleted}
                />
              </dl>
            ) : null}
          </section>
        ) : null}

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
                disabled={!targetUsername || isExporting !== null}
                onClick={() => exportJsonl("chatlogs")}
                type="button"
              >
                {isExporting === "chatlogs" ? copy.exporting : copy.exportChatlogs}
              </button>
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={!targetUsername || isExporting !== null}
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
                  disabled={!targetUsername || isSimulatingOne}
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
                disabled={isTicking}
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
              <BranchSelector
                branches={branches}
                disabled={isPurgingBranch}
                isLoading={isLoadingBranches}
                label={copy.branchToPurge}
                loadingLabel={t.common.loading}
                onChange={setPurgeBranchId}
                onRefresh={loadBranches}
                optionLabel={(branchId) =>
                  branchId === DEFAULT_BRANCH_ID
                    ? `${branchId} (${t.common.protected})`
                    : branchId
                }
                refreshLabel={t.common.refreshBranches}
                value={purgeBranchId}
              />
              <div className="flex items-end">
                <button
                  className="w-full rounded-full bg-rose-600 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
                  disabled={
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
      {agentPendingDelete ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/50 px-4 py-6">
          <div
            aria-modal="true"
            className="w-full max-w-lg rounded-xl bg-white p-5 shadow-xl"
            role="dialog"
          >
            <p className="text-xs font-semibold uppercase tracking-wide text-rose-600">
              {copy.deleteAgentDanger}
            </p>
            <h2 className="mt-2 text-lg font-semibold text-gray-950">
              {copy.deleteAgentTitle}
            </h2>
            <p className="mt-2 text-sm leading-6 text-gray-600">
              {copy.deleteAgentConfirm}
            </p>
            <p className="mt-4 rounded-lg bg-gray-50 px-4 py-3 text-sm font-medium text-gray-800">
              {formatAgentChoiceLabel(agentPendingDelete)}
            </p>
            <div className="mt-5 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={isDeletingAgent}
                onClick={() => setAgentPendingDelete(null)}
                type="button"
              >
                {t.common.cancel}
              </button>
              <button
                className="rounded-full bg-rose-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={isDeletingAgent}
                onClick={confirmDeleteAgent}
                type="button"
              >
                {isDeletingAgent ? copy.deletingAgent : copy.confirmDeleteAgent}
              </button>
            </div>
          </div>
        </div>
      ) : null}
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
