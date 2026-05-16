"use client";

import { FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { BranchSelector } from "@/components/BranchSelector";
import { useLanguage } from "@/components/LanguageContext";
import {
  Agent,
  ChatReply,
  DriftCheckResponse,
  GlobalSystemSettings,
  apiRequest,
} from "@/lib/api";
import { LoopSession, loadSession, saveSession } from "@/lib/session";
import { formatFeedTime } from "@/lib/time";

const DEFAULT_BRANCH_ID = "main";
const CHAT_HISTORY_PAGE_SIZE = 15;
const CHAT_MODEL_STORAGE_KEY = "loop_chat_model";
const EXPERIMENT_MODE_STORAGE_KEY = "loop_experiment_mode";
const CHAT_TOPIC_STORAGE_KEY = "loop_chat_topic";
const DEFAULT_CHAT_TOPIC = "general";
const CHAT_TOPICS = ["general", "daily_life", "relationships", "work", "identity"];

const BRANCHES_ENDPOINT = (agentId: number) =>
  `/api/simulation/agents/${agentId}/branches`;

const CHAT_HISTORY_ENDPOINT = (
  agentId: number,
  branchId: string,
  sessionId: string,
  skip = 0,
  limit = CHAT_HISTORY_PAGE_SIZE,
) =>
  `/api/agents/${agentId}/chat?branch_id=${encodeURIComponent(branchId)}&session_id=${encodeURIComponent(sessionId)}&skip=${skip}&limit=${limit}`;

const DRIFT_CHECK_ENDPOINT = (agentId: number) =>
  `/api/chat/${agentId}/check-drift`;

const CHAT_SESSIONS_ENDPOINT = (agentId: number, branchId: string) =>
  `/api/chat/${agentId}/sessions?branch_id=${encodeURIComponent(branchId)}`;

type ChatMessage = {
  id: string;
  role: "user" | "agent";
  content: string;
  timestamp: string;
  memoryChunksUsed?: number;
  modelUsed?: ChatModelChoice;
  experimentMode?: ExperimentModeChoice;
  topic?: string;
  warning?: string | null;
};

type ChatModelChoice = "fast" | "deep";
type ExperimentModeChoice = "mode_alpha" | "mode_beta";
type MobilePanel = "sessions" | "settings" | "diagnostics" | null;

type ChatSessionSummary = {
  branch_id: string;
  session_id: string;
  first_message: string;
  latest_message: string;
  latest_timestamp: string;
  turn_count: number;
};

type ChatLog = {
  id: number;
  agent_id: number;
  user_message: string;
  agent_reply: string;
  timestamp: string;
  branch_id?: string;
  session_id?: string;
  topic?: string;
  experiment_mode?: ExperimentModeChoice;
  memory_chunks_used?: number;
  model_used?: ChatModelChoice;
  warning?: string | null;
};

type MemoryDiagnostic = {
  kind: "identity" | "semantic" | "episodic";
  summary: string;
};

type DeveloperSnapshot = {
  queryRoute: string;
  topic: string;
  lastQuery: string;
  memoryDiagnostics: MemoryDiagnostic[];
  driftProbability: number | null;
  driftReason: string;
  updatedAt: string;
};

function nowIso() {
  return new Date().toISOString();
}

function createSessionId() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function loadChatModel(): ChatModelChoice {
  if (typeof window === "undefined") {
    return "fast";
  }
  return localStorage.getItem(CHAT_MODEL_STORAGE_KEY) === "deep" ? "deep" : "fast";
}

function loadExperimentMode(): ExperimentModeChoice {
  if (typeof window === "undefined") {
    return "mode_alpha";
  }
  const storedMode = localStorage.getItem(EXPERIMENT_MODE_STORAGE_KEY);
  if (storedMode === "mode_beta" || storedMode === "static_prompt") {
    return "mode_beta";
  }
  return "mode_alpha";
}

function loadChatTopic() {
  if (typeof window === "undefined") {
    return DEFAULT_CHAT_TOPIC;
  }
  return localStorage.getItem(CHAT_TOPIC_STORAGE_KEY) || DEFAULT_CHAT_TOPIC;
}

function truncateSummary(value: string, fallback: string, maxLength = 42) {
  const trimmed = value.replace(/\s+/g, " ").trim();
  if (!trimmed) {
    return fallback;
  }
  return trimmed.length > maxLength
    ? `${trimmed.slice(0, maxLength - 1)}...`
    : trimmed;
}

export default function ChatPage() {
  const router = useRouter();
  const { language, t } = useLanguage();
  const copy = t.chat;
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const historyRequestIdRef = useRef(0);
  const shouldScrollToBottomRef = useRef(false);
  const pendingScrollAnchorRef = useRef<{
    scrollHeight: number;
    scrollTop: number;
  } | null>(null);
  const [session, setSession] = useState<LoopSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatSessions, setChatSessions] = useState<ChatSessionSummary[]>([]);
  const [branches, setBranches] = useState<string[]>([DEFAULT_BRANCH_ID]);
  const [currentBranch, setCurrentBranch] = useState(DEFAULT_BRANCH_ID);
  const [systemSettings, setSystemSettings] =
    useState<GlobalSystemSettings | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState("");
  const [historySkip, setHistorySkip] = useState(0);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isLoadingSessions, setIsLoadingSessions] = useState(false);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [isLoadingOlderHistory, setIsLoadingOlderHistory] = useState(false);
  const [chatModel, setChatModel] = useState<ChatModelChoice>("fast");
  const [experimentMode, setExperimentMode] =
    useState<ExperimentModeChoice>("mode_alpha");
  const [currentTopic, setCurrentTopic] = useState(DEFAULT_CHAT_TOPIC);
  const [isDeveloperPanelOpen, setIsDeveloperPanelOpen] = useState(true);
  const [mobilePanel, setMobilePanel] = useState<MobilePanel>(null);
  const [developerSnapshot, setDeveloperSnapshot] =
    useState<DeveloperSnapshot | null>(null);
  const [latestDriftResult, setLatestDriftResult] =
    useState<DriftCheckResponse | null>(null);
  const [driftResult, setDriftResult] = useState<DriftCheckResponse | null>(null);
  const [calibrationWrong, setCalibrationWrong] = useState("");
  const [calibrationIdeal, setCalibrationIdeal] = useState("");
  const [isCalibrating, setIsCalibrating] = useState(false);
  const currentSession = chatSessions.find(
    (chatSession) => chatSession.session_id === currentSessionId,
  );
  const currentSessionLabel = getSessionDisplayLabel(currentSessionId);
  const visibleTimelineBranches = uniqueBranches([currentBranch, ...branches]);
  const canSwitchBranches =
    session?.is_admin === true || systemSettings?.allow_user_branch_switch === true;
  const mobilePanelCopy =
    language === "zh"
      ? {
          sessions: "会话",
          sessionsTitle: "会话记录",
          settings: "设置",
          settingsTitle: "聊天设置",
          diagnostics: "诊断",
          diagnosticsTitle: "心智诊断",
          close: "关闭",
          closePanel: "关闭面板",
        }
      : {
          sessions: "Chats",
          sessionsTitle: "Chat history",
          settings: "Setup",
          settingsTitle: "Chat setup",
          diagnostics: "Insight",
          diagnosticsTitle: "Mind insight",
          close: "Close",
          closePanel: "Close panel",
        };

  useEffect(() => {
    setChatModel(loadChatModel());
    setExperimentMode(loadExperimentMode());
    setCurrentTopic(loadChatTopic());

    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
        return;
      }
      if (storedSession.is_admin) {
        router.replace("/lab");
        return;
      }

      let initialBranch = DEFAULT_BRANCH_ID;
      let allowInitialBranchSwitch = false;
      try {
        const settings = await apiRequest<GlobalSystemSettings>(
          "/api/simulation/settings",
        );
        initialBranch = settings.global_active_branch?.trim() || DEFAULT_BRANCH_ID;
        allowInitialBranchSwitch = settings.allow_user_branch_switch;
        setSystemSettings(settings);
      } catch {
        setSystemSettings({
          allow_user_branch_switch: false,
          global_active_branch: DEFAULT_BRANCH_ID,
        });
      }
      setCurrentBranch(initialBranch);
      setBranches((currentBranches) =>
        uniqueBranches([initialBranch, ...currentBranches]),
      );

      if (storedSession.agent_id) {
        startNewConversation();
        setSession(storedSession);
        loadChatSessions(storedSession.agent_id, initialBranch);
        if (allowInitialBranchSwitch) {
          loadBranches(storedSession.agent_id);
        }
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
        startNewConversation();
        setSession(hydratedSession);
        loadChatSessions(agent.id, initialBranch);
        if (allowInitialBranchSwitch) {
          loadBranches(agent.id);
        }
      } catch {
        setSession(storedSession);
        setError(copy.noAgent);
      }
    }

    bootstrap();
  }, [router]);

  useLayoutEffect(() => {
    const anchor = pendingScrollAnchorRef.current;
    const container = scrollContainerRef.current;
    if (anchor && container) {
      container.scrollTop =
        container.scrollHeight - anchor.scrollHeight + anchor.scrollTop;
      pendingScrollAnchorRef.current = null;
      return;
    }

    if (shouldScrollToBottomRef.current) {
      scrollToBottom("auto");
      shouldScrollToBottomRef.current = false;
    }
  }, [messages]);

  function scrollToBottom(behavior: ScrollBehavior = "smooth") {
    const container = scrollContainerRef.current;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior });
      return;
    }
    bottomRef.current?.scrollIntoView({ behavior });
  }

  function getSessionDisplayLabel(sessionId: string) {
    const normalizedSessionId = sessionId.trim();
    if (!normalizedSessionId) {
      return copy.currentConversation;
    }

    const matchingSession = chatSessions.find(
      (chatSession) => chatSession.session_id === normalizedSessionId,
    );
    return truncateSummary(
      matchingSession?.first_message ?? currentSession?.first_message ?? "",
      copy.currentConversation,
    );
  }

  async function loadChatSessions(agentId: number, branchId = currentBranch) {
    setIsLoadingSessions(true);
    try {
      const result = await apiRequest<unknown>(
        CHAT_SESSIONS_ENDPOINT(agentId, branchId),
      );
      setChatSessions(normalizeChatSessions(result));
    } catch (err) {
      setNotice(
        err instanceof Error
          ? `${copy.loadSessionsFailed} ${err.message}`
          : copy.loadSessionsFailed,
      );
    } finally {
      setIsLoadingSessions(false);
    }
  }

  async function loadBranches(agentId: number) {
    setIsLoadingBranches(true);
    try {
      const result = await apiRequest<unknown>(BRANCHES_ENDPOINT(agentId));
      setBranches(normalizeBranches(result));
    } catch (err) {
      setBranches([DEFAULT_BRANCH_ID]);
      setNotice(
        err instanceof Error
          ? t.common.branchUnavailable(err.message)
          : t.common.branchUnavailable(),
      );
    } finally {
      setIsLoadingBranches(false);
    }
  }

  function startNewConversation() {
    const nextSessionId = createSessionId();
    historyRequestIdRef.current += 1;
    pendingScrollAnchorRef.current = null;
    setCurrentSessionId(nextSessionId);
    setMessages([]);
    setError("");
    setNotice("");
    setInput("");
    setHistorySkip(0);
    setHasMoreHistory(false);
    setIsLoadingHistory(false);
    setDriftResult(null);
    setLatestDriftResult(null);
    setDeveloperSnapshot(null);
    setMobilePanel(null);
  }

  async function loadChatHistory(
    agentId: number,
    branchId: string,
    sessionId: string,
  ) {
    const requestId = historyRequestIdRef.current + 1;
    historyRequestIdRef.current = requestId;
    pendingScrollAnchorRef.current = null;
    setMessages([]);
    setNotice("");
    setHistorySkip(0);
    setHasMoreHistory(true);
    setIsLoadingHistory(true);

    try {
      const result = await apiRequest<unknown>(
        CHAT_HISTORY_ENDPOINT(
          agentId,
          branchId,
          sessionId,
          0,
          CHAT_HISTORY_PAGE_SIZE,
        ),
      );
      if (historyRequestIdRef.current !== requestId) {
        return;
      }
      const loadedTurns = countChatHistoryTurns(result);
      setHistorySkip(loadedTurns);
      setHasMoreHistory(loadedTurns === CHAT_HISTORY_PAGE_SIZE);
      shouldScrollToBottomRef.current = true;
      setMessages(normalizeChatHistory(result, branchId, sessionId));
    } catch (err) {
      if (historyRequestIdRef.current !== requestId) {
        return;
      }
      setMessages([]);
      setNotice(
        err instanceof Error
          ? copy.branchNotice(getSessionDisplayLabel(sessionId), err.message)
          : copy.branchNotice(getSessionDisplayLabel(sessionId)),
      );
    } finally {
      if (historyRequestIdRef.current === requestId) {
        setIsLoadingHistory(false);
      }
    }
  }

  async function loadOlderChatHistory() {
    if (
      !session?.agent_id ||
      !currentBranch ||
      !currentSessionId ||
      !hasMoreHistory ||
      isLoadingHistory ||
      isLoadingOlderHistory
    ) {
      return;
    }

    const requestId = historyRequestIdRef.current;
    setIsLoadingOlderHistory(true);
    setNotice("");

    try {
      const result = await apiRequest<unknown>(
        CHAT_HISTORY_ENDPOINT(
          session.agent_id,
          currentBranch,
          currentSessionId,
          historySkip,
          CHAT_HISTORY_PAGE_SIZE,
        ),
      );
      if (historyRequestIdRef.current !== requestId) {
        return;
      }

      const olderMessages = normalizeChatHistory(
        result,
        currentBranch,
        currentSessionId,
      );
      const loadedTurns = countChatHistoryTurns(result);
      if (loadedTurns === 0 || olderMessages.length === 0) {
        setHasMoreHistory(false);
        return;
      }

      const container = scrollContainerRef.current;
      pendingScrollAnchorRef.current = container
        ? {
            scrollHeight: container.scrollHeight,
            scrollTop: container.scrollTop,
          }
        : null;
      setMessages((current) => [...olderMessages, ...current]);
      setHistorySkip((current) => current + loadedTurns);
      setHasMoreHistory(loadedTurns === CHAT_HISTORY_PAGE_SIZE);
    } catch (err) {
      setNotice(
        err instanceof Error
          ? copy.branchNotice(currentSessionLabel, err.message)
          : copy.branchNotice(currentSessionLabel),
      );
    } finally {
      setIsLoadingOlderHistory(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session?.agent_id || !currentBranch || !currentSessionId || !input.trim()) {
      return;
    }

    const content = input.trim();
    const localTimestamp = nowIso();
    const topic = currentTopic.trim() || DEFAULT_CHAT_TOPIC;
    setInput("");
    setError("");
    setIsSending(true);
    setDeveloperSnapshot({
      queryRoute:
        experimentMode === "mode_beta" ? "Static Baseline" : "Full IACL",
      topic,
      lastQuery: content,
      memoryDiagnostics: [],
      driftProbability: latestDriftResult?.drift_probability ?? null,
      driftReason: latestDriftResult?.reason ?? "",
      updatedAt: localTimestamp,
    });
    shouldScrollToBottomRef.current = true;
    setMessages((current) => [
      ...current,
      {
        id: `user-${Date.now()}`,
        role: "user",
        content,
        timestamp: localTimestamp,
        topic,
      },
    ]);

    try {
      const result = await apiRequest<ChatReply>(
        `/api/agents/${session.agent_id}/chat`,
        {
          method: "POST",
          body: JSON.stringify({
            message: content,
            model: chatModel,
            branch_id: currentBranch,
            session_id: currentSessionId,
            topic,
            experiment_mode: experimentMode,
          }),
        },
      );

      shouldScrollToBottomRef.current = true;
      setMessages((current) => [
        ...current,
        {
          id: `agent-${result.chat_log?.id ?? Date.now()}`,
          role: "agent",
          content: result.reply,
          timestamp: result.chat_log?.timestamp ?? nowIso(),
          memoryChunksUsed: result.memory_chunks_used,
          modelUsed: result.model_used,
          experimentMode: result.chat_log?.experiment_mode ?? experimentMode,
          topic: result.chat_log?.topic ?? topic,
          warning: result.warning,
        },
      ]);
      setDeveloperSnapshot({
        queryRoute:
          result.query_route ??
          (experimentMode === "mode_beta" ? "Static Baseline" : "Full IACL"),
        topic,
        lastQuery: content,
        memoryDiagnostics: result.memory_diagnostics ?? [],
        driftProbability: latestDriftResult?.drift_probability ?? null,
        driftReason: latestDriftResult?.reason ?? "",
        updatedAt: nowIso(),
      });
      if (result.chat_log) {
        setHistorySkip((current) => current + 1);
        void loadChatSessions(session.agent_id, currentBranch);
        if (experimentMode === "mode_alpha") {
          void checkDriftAfterReply(
            session.agent_id,
            currentBranch,
            currentSessionId,
            topic,
          );
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.sendFailed);
    } finally {
      setIsSending(false);
    }
  }

  async function checkDriftAfterReply(
    agentId: number,
    branchId: string,
    sessionId: string,
    topic: string,
  ) {
    try {
      const result = await apiRequest<DriftCheckResponse>(
        DRIFT_CHECK_ENDPOINT(agentId),
        {
          method: "POST",
          body: JSON.stringify({ branch_id: branchId, session_id: sessionId, topic }),
        },
      );
      setLatestDriftResult(result);
      setDeveloperSnapshot((current) =>
        current
          ? {
              ...current,
              driftProbability: result.drift_probability,
              driftReason: result.reason,
              updatedAt: nowIso(),
            }
          : {
              queryRoute: "Full IACL",
              topic,
              lastQuery: "",
              memoryDiagnostics: [],
              driftProbability: result.drift_probability,
              driftReason: result.reason,
              updatedAt: nowIso(),
            },
      );
      if (result.is_drifting) {
        setDriftResult(result);
        setCalibrationWrong("");
        setCalibrationIdeal("");
      }
    } catch (err) {
      setNotice(
        err instanceof Error
          ? copy.driftDetectorSkipped(err.message)
          : copy.driftDetectorSkipped(),
      );
    }
  }

  async function submitCalibration() {
    if (!session?.agent_id || !currentBranch || !currentSessionId) {
      return;
    }

    const wrong = calibrationWrong.trim();
    const ideal = calibrationIdeal.trim();
    if (!wrong || !ideal) {
      setError(copy.calibrationRequired);
      return;
    }

    const calibrationInstruction = copy.calibrationInstruction(wrong, ideal);

    setError("");
    setIsCalibrating(true);
    try {
      const result = await apiRequest<ChatReply>(
        `/api/agents/${session.agent_id}/chat`,
        {
          method: "POST",
          body: JSON.stringify({
            message: calibrationInstruction,
            model: "deep",
            branch_id: currentBranch,
            session_id: currentSessionId,
            topic: currentTopic,
            experiment_mode: "mode_alpha",
          }),
        },
      );

      const timestamp = result.chat_log?.timestamp ?? nowIso();
      shouldScrollToBottomRef.current = true;
      setMessages((current) => [
        ...current,
        {
          id: `calibration-user-${Date.now()}`,
          role: "user",
          content: copy.calibrationUserMarker,
          timestamp,
        },
        {
          id: `calibration-agent-${result.chat_log?.id ?? Date.now()}`,
          role: "agent",
          content: result.reply,
          timestamp,
          memoryChunksUsed: result.memory_chunks_used,
          modelUsed: result.model_used,
          experimentMode: "mode_alpha",
          topic: currentTopic,
          warning: result.warning,
        },
      ]);
      setDeveloperSnapshot({
        queryRoute: result.query_route ?? "Full IACL",
        topic: currentTopic,
        lastQuery: calibrationInstruction,
        memoryDiagnostics: result.memory_diagnostics ?? [],
        driftProbability: null,
        driftReason: "",
        updatedAt: nowIso(),
      });
      if (result.chat_log) {
        setHistorySkip((current) => current + 1);
        void loadChatSessions(session.agent_id, currentBranch);
      }
      setDriftResult(null);
      setCalibrationWrong("");
      setCalibrationIdeal("");
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.sendFailed);
    } finally {
      setIsCalibrating(false);
    }
  }

  function updateChatModel(model: ChatModelChoice) {
    setChatModel(model);
    localStorage.setItem(CHAT_MODEL_STORAGE_KEY, model);
  }

  function updateExperimentMode(mode: ExperimentModeChoice) {
    setExperimentMode(mode);
    localStorage.setItem(EXPERIMENT_MODE_STORAGE_KEY, mode);
  }

  function updateCurrentTopic(topic: string) {
    const nextTopic = topic.trim() || DEFAULT_CHAT_TOPIC;
    setCurrentTopic(nextTopic);
    localStorage.setItem(CHAT_TOPIC_STORAGE_KEY, nextTopic);
    setDeveloperSnapshot((current) =>
      current
        ? {
            ...current,
            topic: nextTopic,
            updatedAt: nowIso(),
          }
        : current,
    );
  }

  function openChatSession(sessionId: string) {
    if (!session?.agent_id) {
      return;
    }

    const nextSessionId = sessionId.trim();
    if (!nextSessionId || nextSessionId === currentSessionId) {
      return;
    }
    historyRequestIdRef.current += 1;
    pendingScrollAnchorRef.current = null;
    setCurrentSessionId(nextSessionId);
    setMessages([]);
    setError("");
    setNotice("");
    setHistorySkip(0);
    setHasMoreHistory(true);
    setDriftResult(null);
    setLatestDriftResult(null);
    setDeveloperSnapshot(null);
    setMobilePanel(null);
    loadChatHistory(session.agent_id, currentBranch, nextSessionId);
  }

  function updateCurrentBranch(branchId: string) {
    if (!session?.agent_id) {
      return;
    }

    const nextBranch = branchId.trim() || DEFAULT_BRANCH_ID;
    historyRequestIdRef.current += 1;
    pendingScrollAnchorRef.current = null;
    setCurrentBranch(nextBranch);
    const nextSessionId = createSessionId();
    setCurrentSessionId(nextSessionId);
    setChatSessions([]);
    setMessages([]);
    setError("");
    setNotice("");
    setInput("");
    setHistorySkip(0);
    setHasMoreHistory(false);
    setDriftResult(null);
    setLatestDriftResult(null);
    setDeveloperSnapshot(null);
    setMobilePanel(null);
    loadChatSessions(session.agent_id, nextBranch);
  }

  if (!session) {
    return (
      <main className="fixed inset-0 flex h-screen w-full items-center justify-center overflow-hidden bg-gray-50 px-6 pt-[65px]">
        <p className="text-sm text-gray-500">{copy.loading}</p>
      </main>
    );
  }

  return (
    <main className="flex h-[calc(100dvh-65px)] min-h-0 w-full overflow-hidden bg-gray-50">
      <aside className="hidden h-full w-64 flex-shrink-0 flex-col overflow-y-auto border-r border-gray-800 bg-gray-950 text-gray-100 lg:flex">
            <div className="border-b border-white/10 p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                {copy.sidebarTitle}
              </p>
              <button
                className="mt-3 w-full rounded-lg bg-white px-4 py-3 text-left text-sm font-semibold text-gray-950 shadow-sm transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={!session.agent_id || isSending}
                onClick={() => startNewConversation()}
                type="button"
              >
                {copy.newConversation}
              </button>
            </div>

            <div className="p-3">
              <div className="mb-2 flex items-center justify-between px-1">
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                  {copy.history}
                </p>
                <button
                  className="text-xs font-medium text-gray-400 transition hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={!session.agent_id || isLoadingSessions}
                  onClick={() =>
                    session.agent_id && loadChatSessions(session.agent_id)
                  }
                  type="button"
                >
                  {isLoadingSessions ? t.common.loading : copy.refreshHistory}
                </button>
              </div>

              {chatSessions.length === 0 ? (
                <p className="rounded-lg px-3 py-3 text-sm text-gray-500">
                  {copy.noSessions}
                </p>
              ) : (
                <div className="space-y-1">
                  {chatSessions.map((chatSession) => {
                    const isActive = chatSession.session_id === currentSessionId;
                    return (
                      <button
                        className={`w-full rounded-lg px-3 py-3 text-left transition ${
                          isActive
                            ? "bg-white/15 text-white"
                            : "text-gray-300 hover:bg-white/10 hover:text-white"
                        }`}
                        key={chatSession.session_id}
                        onClick={() => openChatSession(chatSession.session_id)}
                        type="button"
                      >
                        <span className="block truncate text-sm font-medium">
                          {chatSession.latest_message || copy.sessionUntitled}
                        </span>
                        <span className="mt-1 block text-xs text-gray-500">
                          {formatFeedTime(chatSession.latest_timestamp)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
      </aside>

      <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col bg-gray-50">
            <header className="hidden flex-shrink-0 border-b border-gray-200 bg-white px-5 py-4 lg:block">
              <div className="mx-auto w-full max-w-5xl space-y-3 lg:space-y-4">
                <div className="min-w-0">
                  <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
                    {copy.currentSession}
                  </p>
                  <h1 className="mt-1 max-w-xl text-xl font-bold tracking-tight text-gray-950 lg:text-2xl">
                    {copy.title}
                  </h1>
                  <p className="mt-2 hidden max-w-2xl text-sm leading-6 text-gray-500 lg:block">
                    {copy.subtitle(session.agent_name ?? t.common.currentAgent)}
                  </p>
                  <p className="mt-1 truncate text-xs leading-5 text-gray-400 lg:mt-2">
                    {canSwitchBranches ? (
                      <>
                        {copy.branchLabel}:{" "}
                        <span
                          className={`font-semibold ${
                            currentBranch === DEFAULT_BRANCH_ID
                              ? "text-indigo-700"
                              : "text-fuchsia-700"
                          }`}
                        >
                          {currentBranch}
                        </span>
                        <span className="mx-2 text-gray-300">/</span>
                      </>
                    ) : null}
                    {copy.currentConversation}: {currentSessionLabel}
                  </p>
                </div>

                <div className="hidden grid-cols-1 gap-3 sm:grid-cols-2 lg:grid xl:grid-cols-[minmax(18rem,1.3fr)_minmax(12rem,1fr)_minmax(12rem,1fr)_auto] xl:items-end">
                  {canSwitchBranches ? (
                    <BranchSelector
                      branches={visibleTimelineBranches}
                      className="sm:col-span-2 xl:col-span-1"
                      disabled={isSending || !session.agent_id}
                      isLoading={isLoadingBranches}
                      label={copy.currentTimeline}
                      loadingLabel={t.common.loading}
                      onChange={updateCurrentBranch}
                      onRefresh={() =>
                        session.agent_id && loadBranches(session.agent_id)
                      }
                      refreshLabel={t.common.refreshBranches}
                      value={currentBranch}
                    />
                  ) : null}
                  <label className="block min-w-0 sm:col-span-2 xl:col-span-1">
                    <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                      {copy.experimentMode}
                    </span>
                    <select
                      className="mt-2 w-full rounded-full border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                      disabled={isSending || Boolean(driftResult)}
                      id="experiment-mode"
                      onChange={(event) =>
                        updateExperimentMode(
                          event.target.value as ExperimentModeChoice,
                        )
                      }
                      value={experimentMode}
                    >
                      <option value="mode_alpha">
                        {copy.experimentModes.mode_alpha}
                      </option>
                      <option value="mode_beta">
                        {copy.experimentModes.mode_beta}
                      </option>
                    </select>
                  </label>
                  <label className="block min-w-0">
                    <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                      {copy.currentTopic}
                    </span>
                    <select
                      className="mt-2 w-full rounded-full border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                      disabled={isSending || Boolean(driftResult)}
                      onChange={(event) => updateCurrentTopic(event.target.value)}
                      value={currentTopic}
                    >
                      {CHAT_TOPICS.map((topic) => (
                        <option key={topic} value={topic}>
                          {(copy.topics as Record<string, string>)[topic] ?? topic}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="self-end rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 shadow-sm transition hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700 xl:w-auto"
                    onClick={() =>
                      setIsDeveloperPanelOpen((isOpen) => !isOpen)
                    }
                    type="button"
                  >
                    {isDeveloperPanelOpen
                      ? copy.hideDeveloperTools
                      : copy.showDeveloperTools}
                  </button>
                </div>
              </div>
            </header>

            <div
              className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3 sm:p-4"
              ref={scrollContainerRef}
            >
              <div className="mx-auto flex w-full max-w-5xl flex-grow flex-col space-y-4">
                {error ? (
                  <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                    {error}
                  </div>
                ) : null}
                {notice ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                    {notice}
                  </div>
                ) : null}

                {isLoadingHistory ? (
                  <div className="flex min-h-0 flex-1 items-center justify-center text-center">
                    <div>
                      <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-gray-200 border-t-purple-500" />
                      <p className="mt-3 text-sm font-medium text-gray-500">
                        {copy.loadHistory(currentSessionLabel)}
                      </p>
                    </div>
                  </div>
                ) : messages.length === 0 ? (
                  <div className="flex min-h-0 flex-1 items-center justify-center text-center">
                    <div>
                      <p className="text-base font-semibold text-gray-900">
                        {copy.startTitle}
                      </p>
                      <p className="mt-2 max-w-sm text-sm leading-6 text-gray-500">
                        {copy.startHelp}
                      </p>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="sticky top-0 z-10 -mx-4 flex justify-center bg-white/90 px-4 pb-2 pt-1 backdrop-blur">
                      <button
                        className="rounded-full border border-gray-200 bg-white px-4 py-2 text-xs font-medium text-gray-500 shadow-sm transition hover:border-gray-300 hover:bg-gray-50 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-60"
                        disabled={!hasMoreHistory || isLoadingOlderHistory}
                        onClick={loadOlderChatHistory}
                        type="button"
                      >
                        {!hasMoreHistory
                          ? copy.noPreviousMessages
                          : isLoadingOlderHistory
                            ? copy.loadingPreviousMessages
                            : copy.loadPreviousMessages}
                      </button>
                    </div>
                    {messages.map((message) => (
                      <ChatBubble key={message.id} message={message} />
                    ))}
                  </>
                )}
                <div ref={bottomRef} />
              </div>
            </div>

            <form
              className="flex-shrink-0 border-t border-gray-200 bg-white/95 p-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] shadow-[0_-10px_30px_rgba(15,23,42,0.06)] lg:p-4"
              onSubmit={sendMessage}
            >
                <div className="mx-auto w-full max-w-5xl space-y-2 lg:space-y-3">
                  <div className="hidden items-center justify-between gap-3 px-2 lg:flex">
                    <label
                      className="text-xs font-semibold uppercase tracking-wide text-gray-500"
                      htmlFor="chat-model"
                    >
                      {copy.model}
                    </label>
                    <select
                      className="rounded-full border border-gray-200 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-900 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                      disabled={isSending}
                      id="chat-model"
                      onChange={(event) =>
                        updateChatModel(event.target.value as ChatModelChoice)
                      }
                      value={chatModel}
                    >
                      <option value="fast">DeepSeek Chat</option>
                      <option value="deep">DeepSeek V4 Pro</option>
                    </select>
                  </div>

                  <div className="flex gap-2 lg:gap-3">
                    <input
                      className="min-w-0 flex-1 rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100 lg:px-5"
                      disabled={
                        !session.agent_id ||
                        !currentBranch ||
                        !currentSessionId ||
                        isSending ||
                        Boolean(driftResult)
                      }
                      onChange={(event) => setInput(event.target.value)}
                      placeholder={copy.placeholder}
                      value={input}
                    />
                    <button
                      className="shrink-0 rounded-full bg-gray-950 px-4 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60 lg:px-5"
                      disabled={
                        !session.agent_id ||
                        !currentBranch ||
                        !currentSessionId ||
                        isSending ||
                        Boolean(driftResult) ||
                        !input.trim()
                      }
                      type="submit"
                    >
                      {isSending ? t.common.sending : copy.send}
                    </button>
                  </div>
                </div>
            </form>
            <div className="grid flex-shrink-0 grid-cols-3 border-t border-gray-200 bg-white text-xs font-semibold text-gray-600 lg:hidden">
              <button
                className="border-r border-gray-200 px-3 py-2.5 transition hover:bg-gray-50"
                onClick={() => setMobilePanel("sessions")}
                type="button"
              >
                {mobilePanelCopy.sessions}
              </button>
              <button
                className="border-r border-gray-200 px-3 py-2.5 transition hover:bg-gray-50"
                onClick={() => setMobilePanel("settings")}
                type="button"
              >
                {mobilePanelCopy.settings}
              </button>
              <button
                className="px-3 py-2.5 transition hover:bg-gray-50"
                onClick={() => setMobilePanel("diagnostics")}
                type="button"
              >
                {mobilePanelCopy.diagnostics}
              </button>
            </div>
      </div>
      {isDeveloperPanelOpen ? (
        <DeveloperPanel
          currentBranch={currentBranch}
          currentSessionLabel={currentSessionLabel}
          currentTopic={currentTopic}
          driftResult={latestDriftResult}
          experimentMode={experimentMode}
          isSending={isSending}
          showBranchDetails={session.is_admin}
          snapshot={developerSnapshot}
        />
      ) : null}
      {mobilePanel ? (
        <div className="fixed inset-0 z-50 flex items-end bg-gray-950/40 lg:hidden">
          <button
            aria-label={mobilePanelCopy.closePanel}
            className="absolute inset-0 cursor-default"
            onClick={() => setMobilePanel(null)}
            type="button"
          />
          <div className="relative max-h-[78dvh] w-full overflow-hidden rounded-t-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
              <h2 className="text-sm font-bold text-gray-950">
                {mobilePanel === "sessions"
                  ? mobilePanelCopy.sessionsTitle
                  : mobilePanel === "settings"
                    ? mobilePanelCopy.settingsTitle
                    : mobilePanelCopy.diagnosticsTitle}
              </h2>
              <button
                className="rounded-full border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 shadow-sm"
                onClick={() => setMobilePanel(null)}
                type="button"
              >
                {mobilePanelCopy.close}
              </button>
            </div>

            {mobilePanel === "sessions" ? (
              <div className="max-h-[calc(78dvh-3.5rem)] overflow-y-auto p-4">
                <button
                  className="mb-4 w-full rounded-xl bg-gray-950 px-4 py-3 text-left text-sm font-semibold text-white shadow-sm disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={!session.agent_id || isSending}
                  onClick={() => startNewConversation()}
                  type="button"
                >
                  {copy.newConversation}
                </button>
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                    {copy.history}
                  </p>
                  <button
                    className="text-xs font-semibold text-indigo-600 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={!session.agent_id || isLoadingSessions}
                    onClick={() =>
                      session.agent_id && loadChatSessions(session.agent_id)
                    }
                    type="button"
                  >
                    {isLoadingSessions ? t.common.loading : copy.refreshHistory}
                  </button>
                </div>
                {chatSessions.length === 0 ? (
                  <p className="rounded-xl bg-gray-50 px-3 py-4 text-sm text-gray-500">
                    {copy.noSessions}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {chatSessions.map((chatSession) => {
                      const isActive =
                        chatSession.session_id === currentSessionId;
                      return (
                        <button
                          className={`w-full rounded-xl border px-3 py-3 text-left transition ${
                            isActive
                              ? "border-indigo-200 bg-indigo-50 text-indigo-950"
                              : "border-gray-200 bg-white text-gray-700"
                          }`}
                          key={chatSession.session_id}
                          onClick={() => openChatSession(chatSession.session_id)}
                          type="button"
                        >
                          <span className="block truncate text-sm font-semibold">
                            {chatSession.latest_message || copy.sessionUntitled}
                          </span>
                          <span className="mt-1 block text-xs text-gray-500">
                            {formatFeedTime(chatSession.latest_timestamp)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            ) : null}

            {mobilePanel === "settings" ? (
              <div className="max-h-[calc(78dvh-3.5rem)] space-y-4 overflow-y-auto p-4">
                {canSwitchBranches ? (
                  <BranchSelector
                    branches={visibleTimelineBranches}
                    disabled={isSending || !session.agent_id}
                    isLoading={isLoadingBranches}
                    label={copy.currentTimeline}
                    loadingLabel={t.common.loading}
                    onChange={updateCurrentBranch}
                    onRefresh={() => session.agent_id && loadBranches(session.agent_id)}
                    refreshLabel={t.common.refreshBranches}
                    value={currentBranch}
                  />
                ) : null}
                <label className="block">
                  <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    {copy.experimentMode}
                  </span>
                  <select
                    className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-3 text-sm font-medium text-gray-900 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                    disabled={isSending || Boolean(driftResult)}
                    onChange={(event) =>
                      updateExperimentMode(
                        event.target.value as ExperimentModeChoice,
                      )
                    }
                    value={experimentMode}
                  >
                    <option value="mode_alpha">
                      {copy.experimentModes.mode_alpha}
                    </option>
                    <option value="mode_beta">
                      {copy.experimentModes.mode_beta}
                    </option>
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    {copy.currentTopic}
                  </span>
                  <select
                    className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-3 text-sm font-medium text-gray-900 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                    disabled={isSending || Boolean(driftResult)}
                    onChange={(event) => updateCurrentTopic(event.target.value)}
                    value={currentTopic}
                  >
                    {CHAT_TOPICS.map((topic) => (
                      <option key={topic} value={topic}>
                        {(copy.topics as Record<string, string>)[topic] ?? topic}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    {copy.model}
                  </span>
                  <select
                    className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-3 text-sm font-medium text-gray-900 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                    disabled={isSending}
                    onChange={(event) =>
                      updateChatModel(event.target.value as ChatModelChoice)
                    }
                    value={chatModel}
                  >
                    <option value="fast">DeepSeek Chat</option>
                    <option value="deep">DeepSeek V4 Pro</option>
                  </select>
                </label>
              </div>
            ) : null}

            {mobilePanel === "diagnostics" ? (
              <div className="max-h-[calc(78dvh-3.5rem)] overflow-y-auto">
                <DeveloperPanelContent
                  currentBranch={currentBranch}
                  currentSessionLabel={currentSessionLabel}
                  currentTopic={currentTopic}
                  driftResult={latestDriftResult}
                  experimentMode={experimentMode}
                  isSending={isSending}
                  showBranchDetails={session.is_admin}
                  snapshot={developerSnapshot}
                />
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
      {driftResult ? (
        <CalibrationModal
          calibrationIdeal={calibrationIdeal}
          calibrationWrong={calibrationWrong}
          driftResult={driftResult}
          isCalibrating={isCalibrating}
          onCalibrationIdealChange={setCalibrationIdeal}
          onCalibrationWrongChange={setCalibrationWrong}
          onSubmit={submitCalibration}
        />
      ) : null}
    </main>
  );
}

function driftRiskTone(probability: number | null | undefined) {
  if (probability === null || probability === undefined) {
    return {
      bar: "bg-gray-200",
      text: "text-gray-500",
      label: "N/A",
      percent: 0,
    };
  }
  const percent = Math.round(Math.max(0, Math.min(1, probability)) * 100);
  if (probability >= 0.7) {
    return { bar: "bg-rose-500", text: "text-rose-700", label: "High", percent };
  }
  if (probability >= 0.35) {
    return {
      bar: "bg-amber-400",
      text: "text-amber-700",
      label: "Medium",
      percent,
    };
  }
  return { bar: "bg-emerald-500", text: "text-emerald-700", label: "Low", percent };
}

function DeveloperPanel({
  currentBranch,
  currentSessionLabel,
  currentTopic,
  driftResult,
  experimentMode,
  isSending,
  showBranchDetails,
  snapshot,
}: {
  currentBranch: string;
  currentSessionLabel: string;
  currentTopic: string;
  driftResult: DriftCheckResponse | null;
  experimentMode: ExperimentModeChoice;
  isSending: boolean;
  showBranchDetails: boolean;
  snapshot: DeveloperSnapshot | null;
}) {
  const { t } = useLanguage();
  const copy = t.chat.developerTools;
  const route =
    snapshot?.queryRoute ??
    (experimentMode === "mode_beta" ? "Static Baseline" : "Full IACL");
  const probability =
    driftResult?.drift_probability ?? snapshot?.driftProbability ?? null;
  const risk = driftRiskTone(probability);
  const memories = snapshot?.memoryDiagnostics ?? [];

  return (
    <aside className="hidden h-full w-80 flex-shrink-0 flex-col overflow-y-auto border-l border-gray-200 bg-gray-50 xl:flex">
      <div className="border-b border-gray-200 bg-white px-4 py-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
          {copy.eyebrow}
        </p>
        <h2 className="mt-1 text-lg font-bold text-gray-950">{copy.title}</h2>
        <p className="mt-2 text-xs leading-5 text-gray-500">
          {showBranchDetails ? (
            <>
              <span
                className={`font-semibold ${
                  currentBranch === DEFAULT_BRANCH_ID
                    ? "text-indigo-700"
                    : "text-fuchsia-700"
                }`}
              >
                {currentBranch}
              </span>{" "}
              /{" "}
            </>
          ) : null}
          {currentSessionLabel} /{" "}
          {(t.chat.topics as Record<string, string>)[currentTopic] ?? currentTopic}
        </p>
      </div>

      <div className="space-y-4 p-4">
        <section className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              {copy.route}
            </span>
            {isSending ? (
              <span className="rounded-full bg-indigo-50 px-2 py-1 text-[11px] font-semibold text-indigo-600">
                {copy.live}
              </span>
            ) : null}
          </div>
          <p className="mt-2 text-sm font-semibold text-gray-950">{route}</p>
          {snapshot?.lastQuery ? (
            <p className="mt-2 line-clamp-3 text-xs leading-5 text-gray-500">
              {snapshot.lastQuery}
            </p>
          ) : null}
        </section>

        <section className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              {copy.driftProbability}
            </span>
            <span className={`text-xs font-bold ${risk.text}`}>{risk.label}</span>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-gray-100">
            <div
              className={`h-full rounded-full transition-all ${risk.bar}`}
              style={{ width: `${risk.percent}%` }}
            />
          </div>
          <p className={`mt-2 text-2xl font-bold ${risk.text}`}>
            {probability === null || probability === undefined
              ? "N/A"
              : `${risk.percent}%`}
          </p>
          {driftResult?.reason || snapshot?.driftReason ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">
              {driftResult?.reason ?? snapshot?.driftReason}
            </p>
          ) : null}
        </section>

        <section className="rounded-lg border border-gray-200 bg-white p-3">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            {copy.memories}
          </span>
          <div className="mt-3 space-y-2">
            {memories.length > 0 ? (
              memories.map((memory, index) => (
                <article
                  className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2"
                  key={`${memory.kind}-${index}`}
                >
                  <p className="text-[11px] font-bold uppercase tracking-wide text-gray-500">
                    {copy.memoryKinds[memory.kind]}
                  </p>
                  <p className="mt-1 line-clamp-4 text-xs leading-5 text-gray-700">
                    {memory.summary}
                  </p>
                </article>
              ))
            ) : (
              <p className="rounded-lg border border-dashed border-gray-200 px-3 py-5 text-center text-xs text-gray-400">
                {copy.noMemories}
              </p>
            )}
          </div>
        </section>
      </div>
    </aside>
  );
}

function DeveloperPanelContent({
  currentBranch,
  currentSessionLabel,
  currentTopic,
  driftResult,
  experimentMode,
  isSending,
  showBranchDetails,
  snapshot,
}: {
  currentBranch: string;
  currentSessionLabel: string;
  currentTopic: string;
  driftResult: DriftCheckResponse | null;
  experimentMode: ExperimentModeChoice;
  isSending: boolean;
  showBranchDetails: boolean;
  snapshot: DeveloperSnapshot | null;
}) {
  const { t } = useLanguage();
  const copy = t.chat.developerTools;
  const route =
    snapshot?.queryRoute ??
    (experimentMode === "mode_beta" ? "Static Baseline" : "Full IACL");
  const probability =
    driftResult?.drift_probability ?? snapshot?.driftProbability ?? null;
  const risk = driftRiskTone(probability);
  const memories = snapshot?.memoryDiagnostics ?? [];

  return (
    <>
      <div className="border-b border-gray-200 bg-white px-4 py-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
          {copy.eyebrow}
        </p>
        <h2 className="mt-1 text-lg font-bold text-gray-950">{copy.title}</h2>
        <p className="mt-2 text-xs leading-5 text-gray-500">
          {showBranchDetails ? (
            <>
              <span
                className={`font-semibold ${
                  currentBranch === DEFAULT_BRANCH_ID
                    ? "text-indigo-700"
                    : "text-fuchsia-700"
                }`}
              >
                {currentBranch}
              </span>{" "}
              /{" "}
            </>
          ) : null}
          {currentSessionLabel} /{" "}
          {(t.chat.topics as Record<string, string>)[currentTopic] ?? currentTopic}
        </p>
      </div>

      <div className="space-y-4 p-4">
        <section className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              {copy.route}
            </span>
            {isSending ? (
              <span className="rounded-full bg-indigo-50 px-2 py-1 text-[11px] font-semibold text-indigo-600">
                {copy.live}
              </span>
            ) : null}
          </div>
          <p className="mt-2 text-sm font-semibold text-gray-950">{route}</p>
          {snapshot?.lastQuery ? (
            <p className="mt-2 line-clamp-3 text-xs leading-5 text-gray-500">
              {snapshot.lastQuery}
            </p>
          ) : null}
        </section>

        <section className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              {copy.driftProbability}
            </span>
            <span className={`text-xs font-bold ${risk.text}`}>{risk.label}</span>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-gray-100">
            <div
              className={`h-full rounded-full transition-all ${risk.bar}`}
              style={{ width: `${risk.percent}%` }}
            />
          </div>
          <p className={`mt-2 text-2xl font-bold ${risk.text}`}>
            {probability === null || probability === undefined
              ? "N/A"
              : `${risk.percent}%`}
          </p>
          {driftResult?.reason || snapshot?.driftReason ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">
              {driftResult?.reason ?? snapshot?.driftReason}
            </p>
          ) : null}
        </section>

        <section className="rounded-lg border border-gray-200 bg-white p-3">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            {copy.memories}
          </span>
          <div className="mt-3 space-y-2">
            {memories.length > 0 ? (
              memories.map((memory, index) => (
                <article
                  className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2"
                  key={`${memory.kind}-${index}`}
                >
                  <p className="text-[11px] font-bold uppercase tracking-wide text-gray-500">
                    {copy.memoryKinds[memory.kind]}
                  </p>
                  <p className="mt-1 line-clamp-4 text-xs leading-5 text-gray-700">
                    {memory.summary}
                  </p>
                </article>
              ))
            ) : (
              <p className="rounded-lg border border-dashed border-gray-200 px-3 py-5 text-center text-xs text-gray-400">
                {copy.noMemories}
              </p>
            )}
          </div>
        </section>
      </div>
    </>
  );
}

function normalizeChatSessions(result: unknown): ChatSessionSummary[] {
  const rawSessions = Array.isArray(result)
    ? result
    : result && typeof result === "object"
      ? "sessions" in result
        ? (result as { sessions?: unknown }).sessions
        : undefined
      : undefined;

  if (!Array.isArray(rawSessions)) {
    return [];
  }

  return rawSessions.flatMap((item): ChatSessionSummary[] => {
    if (!item || typeof item !== "object" || !("session_id" in item)) {
      return [];
    }
    const summary = item as Partial<ChatSessionSummary>;
    const sessionId = String(summary.session_id ?? "").trim();
    if (!sessionId) {
      return [];
    }
    return [
      {
        branch_id: String(summary.branch_id ?? DEFAULT_BRANCH_ID).trim(),
        session_id: sessionId,
        first_message: String(summary.first_message ?? ""),
        latest_message: String(summary.latest_message ?? ""),
        latest_timestamp: String(summary.latest_timestamp ?? nowIso()),
        turn_count: Number(summary.turn_count ?? 0),
      },
    ];
  });
}

function normalizeBranches(result: unknown) {
  const rawBranches = Array.isArray(result)
    ? result
    : result && typeof result === "object"
      ? "branch_ids" in result
        ? (result as { branch_ids?: unknown }).branch_ids
        : "branches" in result
          ? (result as { branches?: unknown }).branches
          : undefined
      : undefined;

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

  return uniqueBranches([DEFAULT_BRANCH_ID, ...branches]);
}

function uniqueBranches(branches: string[]) {
  return Array.from(new Set(branches.filter(Boolean))).sort((left, right) => {
    if (left === DEFAULT_BRANCH_ID) {
      return -1;
    }
    if (right === DEFAULT_BRANCH_ID) {
      return 1;
    }
    return left.localeCompare(right);
  });
}

function normalizeChatHistory(
  result: unknown,
  branchId: string,
  sessionId: string,
): ChatMessage[] {
  const rawLogs = Array.isArray(result)
    ? result
    : result && typeof result === "object"
      ? "chat_logs" in result
        ? (result as { chat_logs?: unknown }).chat_logs
        : "messages" in result
          ? (result as { messages?: unknown }).messages
          : "history" in result
            ? (result as { history?: unknown }).history
            : undefined
      : undefined;

  if (!Array.isArray(rawLogs)) {
    return [];
  }

  return rawLogs.flatMap((item, index): ChatMessage[] => {
    if (!item || typeof item !== "object") {
      return [];
    }

    if ("role" in item && "content" in item) {
      const message = item as Partial<ChatMessage>;
      if (message.role !== "user" && message.role !== "agent") {
        return [];
      }
      return [
        {
          id: message.id ?? `${branchId}-${sessionId}-message-${index}`,
          role: message.role,
          content: String(message.content ?? ""),
          timestamp: message.timestamp ?? nowIso(),
          memoryChunksUsed: message.memoryChunksUsed,
          modelUsed: message.modelUsed,
          experimentMode: message.experimentMode,
          topic: message.topic,
          warning: message.warning,
        },
      ];
    }

    if ("user_message" in item && "agent_reply" in item) {
      const chatLog = item as ChatLog;
      const timestamp = chatLog.timestamp ?? nowIso();
      return [
        {
          id: `${branchId}-${sessionId}-user-${chatLog.id ?? index}`,
          role: "user" as const,
          content: String(chatLog.user_message ?? ""),
          timestamp,
          topic: chatLog.topic ?? DEFAULT_CHAT_TOPIC,
        },
        {
          id: `${branchId}-${sessionId}-agent-${chatLog.id ?? index}`,
          role: "agent" as const,
          content: String(chatLog.agent_reply ?? ""),
          timestamp,
          memoryChunksUsed: chatLog.memory_chunks_used,
          modelUsed: chatLog.model_used,
          experimentMode: chatLog.experiment_mode,
          topic: chatLog.topic ?? DEFAULT_CHAT_TOPIC,
          warning: chatLog.warning,
        },
      ];
    }

    return [];
  });
}

function countChatHistoryTurns(result: unknown) {
  const rawLogs = Array.isArray(result)
    ? result
    : result && typeof result === "object"
      ? "chat_logs" in result
        ? (result as { chat_logs?: unknown }).chat_logs
        : "messages" in result
          ? (result as { messages?: unknown }).messages
          : "history" in result
            ? (result as { history?: unknown }).history
            : undefined
      : undefined;

  return Array.isArray(rawLogs) ? rawLogs.length : 0;
}

function CalibrationModal({
  calibrationIdeal,
  calibrationWrong,
  driftResult,
  isCalibrating,
  onCalibrationIdealChange,
  onCalibrationWrongChange,
  onSubmit,
}: {
  calibrationIdeal: string;
  calibrationWrong: string;
  driftResult: DriftCheckResponse;
  isCalibrating: boolean;
  onCalibrationIdealChange: (value: string) => void;
  onCalibrationWrongChange: (value: string) => void;
  onSubmit: () => void;
}) {
  const { t } = useLanguage();
  const copy = t.chat.calibration;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/50 px-4 backdrop-blur-sm">
      <div
        aria-modal="true"
        className="w-full max-w-xl rounded-2xl border border-amber-200 bg-white p-6 shadow-2xl"
        role="dialog"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-amber-600">
          {copy.eyebrow}
        </p>
        <h2 className="mt-2 text-xl font-bold text-gray-950">
          {copy.title}
        </h2>
        <p className="mt-3 text-sm leading-6 text-gray-600">
          {copy.description}
        </p>
        <p className="mt-2 text-xs leading-5 text-amber-700">
          {copy.consistency(driftResult.consistency_score.toFixed(2))} ·{" "}
          {driftResult.reason}
        </p>

        <div className="mt-5 space-y-4">
          <label className="block">
            <span className="text-sm font-semibold text-gray-800">
              {copy.wrongQuestion}
            </span>
            <textarea
              className="mt-2 min-h-28 w-full resize-none rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 text-gray-900 outline-none transition focus:border-amber-400 focus:ring-4 focus:ring-amber-100"
              disabled={isCalibrating}
              onChange={(event) => onCalibrationWrongChange(event.target.value)}
              value={calibrationWrong}
            />
          </label>

          <label className="block">
            <span className="text-sm font-semibold text-gray-800">
              {copy.idealQuestion}
            </span>
            <textarea
              className="mt-2 min-h-28 w-full resize-none rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 text-gray-900 outline-none transition focus:border-amber-400 focus:ring-4 focus:ring-amber-100"
              disabled={isCalibrating}
              onChange={(event) => onCalibrationIdealChange(event.target.value)}
              value={calibrationIdeal}
            />
          </label>
        </div>

        <div className="mt-5 flex justify-end">
          <button
            className="rounded-full bg-gray-950 px-5 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={
              isCalibrating ||
              !calibrationWrong.trim() ||
              !calibrationIdeal.trim()
            }
            onClick={onSubmit}
            type="button"
          >
            {isCalibrating ? copy.submitting : copy.submit}
          </button>
        </div>
      </div>
    </div>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const { t } = useLanguage();
  const copy = t.chat;
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[82%] rounded-2xl px-4 py-3 shadow-sm ${
          isUser
            ? "rounded-br-md bg-gray-950 text-white"
            : "rounded-bl-md border border-gray-200 bg-gray-50 text-gray-900"
        }`}
      >
        <p className="whitespace-pre-wrap text-sm leading-6">{message.content}</p>
        {!isUser && message.memoryChunksUsed ? (
          <p className="mt-2 text-[11px] font-medium text-indigo-500">
            {t.chat.memoryActive(message.memoryChunksUsed)}
          </p>
        ) : null}
        {!isUser && message.modelUsed ? (
          <p className="mt-1 text-[11px] font-medium text-gray-400">
            {message.modelUsed === "deep" ? "DeepSeek V4 Pro" : "DeepSeek Chat"}
          </p>
        ) : null}
        {!isUser && message.experimentMode ? (
          <p className="mt-1 text-[11px] font-medium text-gray-400">
            {message.experimentMode === "mode_beta"
              ? copy.experimentModes.mode_beta
              : copy.experimentModes.mode_alpha}
          </p>
        ) : null}
        {message.topic ? (
          <p
            className={`mt-1 text-[11px] font-medium ${
              isUser ? "text-gray-300" : "text-gray-400"
            }`}
          >
            {copy.currentTopic}:{" "}
            {(copy.topics as Record<string, string>)[message.topic] ??
              message.topic}
          </p>
        ) : null}
        {!isUser && message.warning ? (
          <p className="mt-2 text-[11px] font-medium text-amber-600">
            {message.warning}
          </p>
        ) : null}
        <p
          className={`mt-2 text-right text-[11px] ${
            isUser ? "text-gray-300" : "text-gray-400"
          }`}
        >
          {formatFeedTime(message.timestamp)}
        </p>
      </div>
    </div>
  );
}
