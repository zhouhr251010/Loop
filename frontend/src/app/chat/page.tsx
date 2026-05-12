"use client";

import { FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLanguage } from "@/components/LanguageContext";
import { Agent, ChatReply, apiRequest } from "@/lib/api";
import { LoopSession, loadSession, saveSession } from "@/lib/session";
import { formatFeedTime } from "@/lib/time";

const DEFAULT_BRANCH_ID = "main";
const CHAT_HISTORY_PAGE_SIZE = 15;
const CHAT_MODEL_STORAGE_KEY = "loop_chat_model";
const CHAT_BRANCH_STORAGE_PREFIX = "loop_chat_branch";

const BRANCHES_ENDPOINT = (agentId: number) =>
  `/api/simulation/agents/${agentId}/branches`;

const CHAT_HISTORY_ENDPOINT = (
  agentId: number,
  branchId: string,
  skip = 0,
  limit = CHAT_HISTORY_PAGE_SIZE,
) =>
  `/api/agents/${agentId}/chat?branch_id=${encodeURIComponent(branchId)}&skip=${skip}&limit=${limit}`;

type ChatMessage = {
  id: string;
  role: "user" | "agent";
  content: string;
  timestamp: string;
  memoryChunksUsed?: number;
  modelUsed?: ChatModelChoice;
  warning?: string | null;
};

type ChatModelChoice = "fast" | "deep";

type ChatLog = {
  id: number;
  agent_id: number;
  user_message: string;
  agent_reply: string;
  timestamp: string;
  branch_id?: string;
  memory_chunks_used?: number;
  model_used?: ChatModelChoice;
  warning?: string | null;
};

function nowIso() {
  return new Date().toISOString();
}

function loadChatModel(): ChatModelChoice {
  if (typeof window === "undefined") {
    return "fast";
  }
  return localStorage.getItem(CHAT_MODEL_STORAGE_KEY) === "deep" ? "deep" : "fast";
}

function branchStorageKey(agentId: number) {
  return `${CHAT_BRANCH_STORAGE_PREFIX}_${agentId}`;
}

function loadBranchPreference(agentId: number) {
  if (typeof window === "undefined") {
    return DEFAULT_BRANCH_ID;
  }
  return localStorage.getItem(branchStorageKey(agentId)) || DEFAULT_BRANCH_ID;
}

function saveBranchPreference(agentId: number, branchId: string) {
  localStorage.setItem(branchStorageKey(agentId), branchId);
}

export default function ChatPage() {
  const router = useRouter();
  const { t } = useLanguage();
  const copy = t.chat;
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const scrollContainerRef = useRef<HTMLElement | null>(null);
  const historyRequestIdRef = useRef(0);
  const shouldScrollToBottomRef = useRef(false);
  const pendingScrollAnchorRef = useRef<{
    scrollHeight: number;
    scrollTop: number;
  } | null>(null);
  const [session, setSession] = useState<LoopSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [branches, setBranches] = useState<string[]>([DEFAULT_BRANCH_ID]);
  const [currentBranch, setCurrentBranch] = useState(DEFAULT_BRANCH_ID);
  const [historySkip, setHistorySkip] = useState(CHAT_HISTORY_PAGE_SIZE);
  const [hasMoreHistory, setHasMoreHistory] = useState(true);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [isLoadingOlderHistory, setIsLoadingOlderHistory] = useState(false);
  const [chatModel, setChatModel] = useState<ChatModelChoice>("fast");

  useEffect(() => {
    setChatModel(loadChatModel());

    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
        return;
      }

      if (storedSession.agent_id) {
        const preferredBranch = loadBranchPreference(storedSession.agent_id);
        setCurrentBranch(preferredBranch);
        setSession(storedSession);
        loadChatHistory(storedSession.agent_id, preferredBranch);
        return;
      }

      try {
        const agent = await apiRequest<Agent>("/api/users/me/agent");
        const hydratedSession = {
          ...storedSession,
          agent_id: agent.id,
          agent_name: agent.agent_name,
        };
        const preferredBranch = loadBranchPreference(agent.id);
        saveSession(hydratedSession);
        setCurrentBranch(preferredBranch);
        setSession(hydratedSession);
        loadChatHistory(agent.id, preferredBranch);
      } catch {
        setSession(storedSession);
        setError(copy.noAgent);
      }
    }

    bootstrap();
  }, [router]);

  useEffect(() => {
    if (!session?.agent_id) {
      return;
    }

    loadBranches(session.agent_id);
  }, [session?.agent_id]);

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

  async function loadBranches(agentId: number) {
    setIsLoadingBranches(true);
    try {
      const result = await apiRequest<unknown>(BRANCHES_ENDPOINT(agentId));
      const branchList = normalizeBranches(result);
      setBranches(branchList);

      const preferredBranch = loadBranchPreference(agentId);
      if (branchList.includes(preferredBranch)) {
        setCurrentBranch(preferredBranch);
      } else if (!branchList.includes(currentBranch)) {
        setCurrentBranch(DEFAULT_BRANCH_ID);
        saveBranchPreference(agentId, DEFAULT_BRANCH_ID);
        loadChatHistory(agentId, DEFAULT_BRANCH_ID);
      }
    } catch (err) {
      setBranches([DEFAULT_BRANCH_ID]);
      setCurrentBranch(DEFAULT_BRANCH_ID);
      setNotice(
        err instanceof Error
          ? t.common.branchUnavailable(err.message)
          : t.common.branchUnavailable(),
      );
    } finally {
      setIsLoadingBranches(false);
    }
  }

  async function loadChatHistory(agentId: number, branchId: string) {
    const requestId = historyRequestIdRef.current + 1;
    historyRequestIdRef.current = requestId;
    pendingScrollAnchorRef.current = null;
    setMessages([]);
    setNotice("");
    setHistorySkip(CHAT_HISTORY_PAGE_SIZE);
    setHasMoreHistory(true);
    setIsLoadingHistory(true);

    try {
      const result = await apiRequest<unknown>(
        CHAT_HISTORY_ENDPOINT(agentId, branchId, 0, CHAT_HISTORY_PAGE_SIZE),
      );
      if (historyRequestIdRef.current !== requestId) {
        return;
      }
      const loadedTurns = countChatHistoryTurns(result);
      setHistorySkip(loadedTurns);
      setHasMoreHistory(loadedTurns === CHAT_HISTORY_PAGE_SIZE);
      shouldScrollToBottomRef.current = true;
      setMessages(normalizeChatHistory(result, branchId));
    } catch (err) {
      if (historyRequestIdRef.current !== requestId) {
        return;
      }
      setMessages([]);
      setNotice(
        err instanceof Error
          ? copy.branchNotice(branchId, err.message)
          : copy.branchNotice(branchId),
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
          historySkip,
          CHAT_HISTORY_PAGE_SIZE,
        ),
      );
      if (historyRequestIdRef.current !== requestId) {
        return;
      }

      const olderMessages = normalizeChatHistory(result, currentBranch);
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
          ? copy.branchNotice(currentBranch, err.message)
          : copy.branchNotice(currentBranch),
      );
    } finally {
      setIsLoadingOlderHistory(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session?.agent_id || !currentBranch || !input.trim()) {
      return;
    }

    const content = input.trim();
    const localTimestamp = nowIso();
    setInput("");
    setError("");
    setIsSending(true);
    shouldScrollToBottomRef.current = true;
    setMessages((current) => [
      ...current,
      {
        id: `user-${Date.now()}`,
        role: "user",
        content,
        timestamp: localTimestamp,
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
          warning: result.warning,
        },
      ]);
      if (result.chat_log) {
        setHistorySkip((current) => current + 1);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.sendFailed);
    } finally {
      setIsSending(false);
    }
  }

  function updateChatModel(model: ChatModelChoice) {
    setChatModel(model);
    localStorage.setItem(CHAT_MODEL_STORAGE_KEY, model);
  }

  function updateCurrentBranch(branchId: string) {
    if (!session?.agent_id) {
      return;
    }

    const nextBranch = branchId.trim() || DEFAULT_BRANCH_ID;
    historyRequestIdRef.current += 1;
    pendingScrollAnchorRef.current = null;
    setCurrentBranch(nextBranch);
    setMessages([]);
    setError("");
    setNotice("");
    setHistorySkip(CHAT_HISTORY_PAGE_SIZE);
    setHasMoreHistory(true);
    saveBranchPreference(session.agent_id, nextBranch);
    loadChatHistory(session.agent_id, nextBranch);
  }

  const isMainBranch = currentBranch === DEFAULT_BRANCH_ID;

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">{copy.loading}</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto flex min-h-[calc(100vh-57px)] w-full max-w-3xl flex-col px-4 py-6 sm:px-6">
        <header className="mb-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
            Nightly Sync
          </p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-gray-950">
            {copy.title}
          </h1>
          <p className="mt-2 text-sm leading-6 text-gray-500">
            {copy.subtitle(session.agent_name ?? t.common.currentAgent)}
          </p>
          <div
            className={`mt-4 rounded-2xl border px-4 py-4 ${
              isMainBranch
                ? "border-gray-200 bg-gray-50"
                : "border-purple-200 bg-purple-50"
            }`}
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p
                  className={`text-xs font-semibold uppercase tracking-wide ${
                    isMainBranch ? "text-gray-500" : "text-purple-600"
                  }`}
                >
                  {copy.currentTimeline}
                </p>
                <p
                  className={`mt-1 text-base font-semibold ${
                    isMainBranch ? "text-gray-950" : "text-purple-950"
                  }`}
                >
                  {copy.currentTimelineValue(currentBranch)}
                </p>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <select
                  className={`rounded-full border px-4 py-2 text-sm font-medium outline-none transition ${
                    isMainBranch
                      ? "border-gray-200 bg-white text-gray-900 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                      : "border-purple-200 bg-white text-purple-950 focus:border-purple-400 focus:ring-4 focus:ring-purple-100"
                  }`}
                  disabled={isLoadingBranches || isSending}
                  onChange={(event) => updateCurrentBranch(event.target.value)}
                  value={currentBranch}
                >
                  {branches.map((branchId) => (
                    <option key={branchId} value={branchId}>
                      {branchId}
                    </option>
                  ))}
                </select>
                <button
                  className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={!session.agent_id || isLoadingBranches}
                  onClick={() => session.agent_id && loadBranches(session.agent_id)}
                  type="button"
                >
                  {isLoadingBranches ? t.common.loading : t.common.refreshBranches}
                </button>
              </div>
            </div>
          </div>
        </header>

        {error ? (
          <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        ) : null}
        {notice ? (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
            {notice}
          </div>
        ) : null}

        <section
          className="flex-1 space-y-4 overflow-y-auto rounded-2xl border border-gray-200 bg-white p-4 shadow-sm"
          ref={scrollContainerRef}
        >
          {isLoadingHistory ? (
            <div className="flex min-h-64 items-center justify-center text-center">
              <div>
                <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-gray-200 border-t-purple-500" />
                <p className="mt-3 text-sm font-medium text-gray-500">
                  {copy.loadHistory(currentBranch)}
                </p>
              </div>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex min-h-64 items-center justify-center text-center">
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
        </section>

        <form className="mt-4 space-y-3" onSubmit={sendMessage}>
          <div className="flex items-center justify-between gap-3 rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
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

          <div className="flex gap-3">
            <input
              className="min-w-0 flex-1 rounded-full border border-gray-200 bg-white px-5 py-3 text-sm outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
              disabled={!session.agent_id || !currentBranch || isSending}
              onChange={(event) => setInput(event.target.value)}
              placeholder={copy.placeholder(currentBranch)}
              value={input}
            />
            <button
              className="rounded-full bg-gray-950 px-5 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!session.agent_id || !currentBranch || isSending || !input.trim()}
              type="submit"
            >
              {isSending ? t.common.sending : copy.send}
            </button>
          </div>
        </form>
      </div>
    </main>
  );
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

function normalizeChatHistory(result: unknown, branchId: string): ChatMessage[] {
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
          id: message.id ?? `${branchId}-message-${index}`,
          role: message.role,
          content: String(message.content ?? ""),
          timestamp: message.timestamp ?? nowIso(),
          memoryChunksUsed: message.memoryChunksUsed,
          modelUsed: message.modelUsed,
          warning: message.warning,
        },
      ];
    }

    if ("user_message" in item && "agent_reply" in item) {
      const chatLog = item as ChatLog;
      const timestamp = chatLog.timestamp ?? nowIso();
      return [
        {
          id: `${branchId}-user-${chatLog.id ?? index}`,
          role: "user" as const,
          content: String(chatLog.user_message ?? ""),
          timestamp,
        },
        {
          id: `${branchId}-agent-${chatLog.id ?? index}`,
          role: "agent" as const,
          content: String(chatLog.agent_reply ?? ""),
          timestamp,
          memoryChunksUsed: chatLog.memory_chunks_used,
          modelUsed: chatLog.model_used,
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

function uniqueBranches(branches: string[]) {
  return Array.from(new Set(branches)).sort((left, right) => {
    if (left === DEFAULT_BRANCH_ID) {
      return -1;
    }
    if (right === DEFAULT_BRANCH_ID) {
      return 1;
    }
    return left.localeCompare(right);
  });
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const { t } = useLanguage();
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
