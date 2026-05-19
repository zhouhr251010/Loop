"use client";

import {
  FormEvent,
  KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  API_BASE_URL,
  SocialMessage,
  UserDirectoryEntry,
  getSocialMessages,
  getUsersDirectory,
  markSocialMessagesRead,
  sendSocialMessage,
} from "@/lib/api";
import { useUiLanguage } from "@/lib/i18n";
import { getAccessToken, loadSession } from "@/lib/session";
import { dictionary } from "@/locales/dictionary";

type H2HChatPanelProps = {
  branchId: string;
};

const ACTIVE_CONVERSATION_SYNC_MS = 1000;
const RATE_LIMIT_RETRY_MS = 5000;
const MAX_SEND_QUEUE_SIZE = 3;
const DISCONNECTED_GRACE_MS = 4000;
const H2H_TOPIC = "general";
const SOCIAL_SIDEBAR_LIMIT = 100;

type ChatBubble = {
  id: string;
  direction: "me" | "peer" | "system";
  content: string;
  timestamp: string;
  senderUserId?: number;
  receiverUserId?: number;
  delivered?: boolean;
};

type IncomingRealtimePayload = {
  type?: string;
  message?: string;
  id?: number;
  chat_log_id?: number;
  content?: string;
  sender_id?: number;
  sender_user_id?: number;
  sender_username?: string;
  receiver_id?: number;
  receiver_user_id?: number;
  timestamp?: string;
  is_read?: boolean;
  delivered?: boolean;
  branch_id?: string;
  session_id?: string;
  topic?: string;
};

export function H2HChatPanel({ branchId }: H2HChatPanelProps) {
  const { language } = useUiLanguage();
  const t = dictionary[language].social;
  const eventSourceRef = useRef<EventSource | null>(null);
  const disconnectTimerRef = useRef<number | null>(null);
  const receiverUserIdRef = useRef("");
  const currentUserIdRef = useRef<number | null>(null);
  const isSendingRef = useRef(false);
  const sendQueueRef = useRef<
    Array<{
      branchId: string;
      content: string;
      pendingId: string;
      receiverUserId: string;
    }>
  >([]);
  const sendQueueTimerRef = useRef<number | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const [receiverUserId, setReceiverUserId] = useState("");
  const [messages, setMessages] = useState<ChatBubble[]>([]);
  const [inputMessage, setInputMessage] = useState("");
  const [userDirectory, setUserDirectory] = useState<UserDirectoryEntry[]>([]);
  const [directorySearch, setDirectorySearch] = useState("");
  const [isConnected, setIsConnected] = useState(false);
  const [isLoadingDirectory, setIsLoadingDirectory] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [sendQueueDepth, setSendQueueDepth] = useState(0);
  const [connectionError, setConnectionError] = useState("");
  const [directoryError, setDirectoryError] = useState("");
  const isSendQueueFull = sendQueueDepth >= MAX_SEND_QUEUE_SIZE;

  const activeContact = useMemo(
    () => userDirectory.find((user) => user.user_id === receiverUserId) ?? null,
    [receiverUserId, userDirectory],
  );

  useEffect(() => {
    receiverUserIdRef.current = receiverUserId;
  }, [receiverUserId]);

  useEffect(() => {
    currentUserIdRef.current = loadSession()?.user_id ?? null;
  }, []);

  useEffect(() => {
    return () => {
      if (sendQueueTimerRef.current !== null) {
        window.clearTimeout(sendQueueTimerRef.current);
      }
      if (disconnectTimerRef.current !== null) {
        window.clearTimeout(disconnectTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }
    container.scrollTop = container.scrollHeight;
  }, [messages]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      async function loadDirectory() {
        setIsLoadingDirectory(true);
        setDirectoryError("");
        try {
          const directory = await getUsersDirectory({
            q: directorySearch,
            limit: SOCIAL_SIDEBAR_LIMIT,
            branchId,
          });
          setUserDirectory(directory);
        } catch (err) {
          setDirectoryError(
            t.directoryFailed(err instanceof Error ? err.message : undefined),
          );
        } finally {
          setIsLoadingDirectory(false);
        }
      }

      void loadDirectory();
    }, 250);

    return () => window.clearTimeout(timeoutId);
  }, [branchId, directorySearch, t]);

  useEffect(() => {
    async function loadMessages() {
      const normalizedReceiverUserId = receiverUserId.trim();
      if (!normalizedReceiverUserId) {
        setMessages([]);
        return;
      }

      setConnectionError("");
      try {
        await refreshMessagesFromServer(normalizedReceiverUserId, branchId, {
          markRead: true,
        });
      } catch (err) {
        setConnectionError(
          err instanceof Error ? err.message : t.realtimeMessageError,
        );
      }
    }

    void loadMessages();
  }, [branchId, receiverUserId, t]);

  useEffect(() => {
    const normalizedReceiverUserId = receiverUserId.trim();
    if (!normalizedReceiverUserId) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void refreshMessagesFromServer(normalizedReceiverUserId, branchId);
    }, ACTIVE_CONVERSATION_SYNC_MS);

    return () => window.clearInterval(intervalId);
  }, [branchId, receiverUserId]);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) {
      setConnectionError(t.tokenMissing);
      return;
    }
    const accessToken = token;

    const eventSource = new EventSource(buildSocialEventsUrl(accessToken));
    eventSourceRef.current = eventSource;

    eventSource.onopen = () => {
      clearDisconnectTimer();
      setIsConnected(true);
      setConnectionError("");
    };

    eventSource.onmessage = (event) => {
      clearDisconnectTimer();
      setIsConnected(true);
      handleRealtimePayload(event.data);
    };

    eventSource.onerror = () => {
      if (eventSource.readyState === EventSource.OPEN) {
        clearDisconnectTimer();
        setIsConnected(true);
      } else {
        scheduleDisconnectedState();
      }
      setConnectionError("");
    };

    return () => {
      eventSource.onopen = null;
      eventSource.onmessage = null;
      eventSource.onerror = null;
      eventSource.close();
      if (eventSourceRef.current === eventSource) {
        eventSourceRef.current = null;
      }
      clearDisconnectTimer();
    };
  }, [t]);

  function handleRealtimePayload(data: unknown) {
    let normalizedMsg: IncomingRealtimePayload | null = null;
    try {
      const rawMsg = parseRealtimePayload(String(data).trim());
      normalizedMsg = normalizeIncomingPayload(rawMsg);
    } catch (err) {
      console.error("Realtime message parse error:", data, err);
      appendSystemMessage(t.realtimeParseError);
      return;
    }
    if (!normalizedMsg) {
      appendSystemMessage(t.realtimeParseError);
      return;
    }
    setConnectionError("");

    if (normalizedMsg.type === "error") {
      setConnectionError(normalizedMsg.message || t.realtimeMessageError);
      return;
    }

    if (
      normalizedMsg.type !== "social_message"
      && normalizedMsg.type !== "human_message"
    ) {
      return;
    }

    const content = String(
      normalizedMsg.content ?? normalizedMsg.message ?? "",
    ).trim();
    if (!content) {
      return;
    }

    const senderUserId = Number(normalizedMsg.sender_id);
    const receiverUserIdFromPayload = Number(normalizedMsg.receiver_id);
    const messageId = normalizedMsg.id ?? Date.now();
    const timestamp = normalizedMsg.timestamp || new Date().toISOString();

    if (String(normalizedMsg.sender_id) === String(receiverUserIdRef.current)) {
      const nextBubble: ChatBubble = {
        id: `peer-${messageId}`,
        direction: "peer",
        content,
        timestamp,
        senderUserId,
        receiverUserId: receiverUserIdFromPayload,
        delivered: true,
      };
      setMessages((currentMessages) => appendUniqueBubble(currentMessages, nextBubble));
      if (Number.isFinite(senderUserId)) {
        void markContactRead(String(senderUserId));
      }
      return;
    }

    if (Number.isFinite(senderUserId)) {
      incrementUnreadCount(String(senderUserId));
    }
  }

  function appendSystemMessage(content: string) {
    setMessages((currentMessages) => [
      ...currentMessages,
      {
        id: `system-${Date.now()}-${currentMessages.length}`,
        direction: "system",
        content,
        timestamp: new Date().toISOString(),
        delivered: true,
      },
    ]);
  }

  function clearDisconnectTimer() {
    if (disconnectTimerRef.current === null) {
      return;
    }
    window.clearTimeout(disconnectTimerRef.current);
    disconnectTimerRef.current = null;
  }

  function scheduleDisconnectedState() {
    if (disconnectTimerRef.current !== null) {
      return;
    }
    disconnectTimerRef.current = window.setTimeout(() => {
      disconnectTimerRef.current = null;
      const eventSource = eventSourceRef.current;
      if (!eventSource || eventSource.readyState !== EventSource.OPEN) {
        setIsConnected(false);
      }
    }, DISCONNECTED_GRACE_MS);
  }

  async function markContactRead(contactId: string) {
    if (!contactId) {
      return;
    }
    setUserDirectory((currentDirectory) => {
      let changed = false;
      const nextDirectory = currentDirectory.map((user) => {
        if (user.user_id !== contactId || (user.unread_count ?? 0) === 0) {
          return user;
        }
        changed = true;
        return { ...user, unread_count: 0 };
      });
      return changed ? nextDirectory : currentDirectory;
    });
    try {
      await markSocialMessagesRead(contactId, branchId);
    } catch (err) {
      console.debug("[Loop Social] failed to mark contact read", err);
    }
  }

  async function refreshMessagesFromServer(
    contactId: string,
    activeBranchId: string,
    options: { markRead?: boolean } = {},
  ) {
    const normalizedContactId = contactId.trim();
    if (!normalizedContactId) {
      return;
    }
    try {
      const history = await getSocialMessages(normalizedContactId, activeBranchId);
      const nextMessages = history.map((message) =>
        toChatBubble(message, currentUserIdRef.current),
      );
      setMessages((currentMessages) => {
        const mergedMessages = mergePendingChatBubbles(
          nextMessages,
          currentMessages,
        );
        return areChatBubblesEqual(currentMessages, mergedMessages)
          ? currentMessages
          : mergedMessages;
      });
      if (options.markRead) {
        await markContactRead(normalizedContactId);
      }
    } catch (err) {
      console.debug("[Loop Social] failed to refresh active conversation", err);
    }
  }

  function incrementUnreadCount(contactId: string) {
    if (!contactId) {
      return;
    }
    setUserDirectory((currentDirectory) => {
      let changed = false;
      const nextDirectory = currentDirectory.map((user) => {
        if (user.user_id !== contactId) {
          return user;
        }
        changed = true;
        return { ...user, unread_count: (user.unread_count ?? 0) + 1 };
      });
      return changed ? nextDirectory : currentDirectory;
    });
  }

  function scheduleSendQueue(delayMs = RATE_LIMIT_RETRY_MS) {
    if (sendQueueTimerRef.current !== null) {
      return;
    }
    sendQueueTimerRef.current = window.setTimeout(() => {
      sendQueueTimerRef.current = null;
      void processSendQueue();
    }, delayMs);
  }

  function getSendQueueDepth() {
    return sendQueueRef.current.length + (isSendingRef.current ? 1 : 0);
  }

  function syncSendQueueState() {
    const queueDepth = getSendQueueDepth();
    setSendQueueDepth(queueDepth);
    setIsSending(queueDepth > 0);
    if (queueDepth < MAX_SEND_QUEUE_SIZE) {
      setConnectionError((currentError) =>
        currentError === "发送队列已满，请稍等上一条消息发出后再继续。"
          ? ""
          : currentError,
      );
    }
  }

  async function processSendQueue() {
    if (isSendingRef.current) {
      return;
    }
    const nextMessage = sendQueueRef.current.shift();
    if (!nextMessage) {
      syncSendQueueState();
      return;
    }

    isSendingRef.current = true;
    syncSendQueueState();
    setConnectionError("");
    try {
      const savedMessage = await sendSocialMessage({
        receiver_user_id: Number(nextMessage.receiverUserId),
        content: nextMessage.content,
        branch_id: nextMessage.branchId,
        topic: H2H_TOPIC,
      });
      if (
        String(nextMessage.receiverUserId) === String(receiverUserIdRef.current)
        && nextMessage.branchId === branchId
      ) {
        const nextBubble = toChatBubble(savedMessage, currentUserIdRef.current);
        setMessages((currentMessages) => {
          if (currentMessages.some((message) => message.id === nextBubble.id)) {
            return currentMessages.filter(
              (message) => message.id !== nextMessage.pendingId,
            );
          }
          const replacedMessages = currentMessages.map((message) =>
            message.id === nextMessage.pendingId ? nextBubble : message,
          );
          return replacedMessages.some((message) => message.id === nextBubble.id)
            ? replacedMessages
            : [...currentMessages, nextBubble];
        });
      }
    } catch (err) {
      sendQueueRef.current.unshift(nextMessage);
      if (isRateLimitError(err)) {
        console.debug("[Loop Social] send queue paused by rate limit", err);
      } else {
        setConnectionError(err instanceof Error ? err.message : t.realtimeMessageError);
      }
      scheduleSendQueue(RATE_LIMIT_RETRY_MS);
      return;
    } finally {
      isSendingRef.current = false;
      syncSendQueueState();
    }

    if (sendQueueRef.current.length > 0) {
      void processSendQueue();
    }
  }

  async function handleSendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setConnectionError("");

    const normalizedReceiverUserId = receiverUserId.trim();
    const normalizedContent = inputMessage.trim();
    if (!normalizedReceiverUserId || !normalizedContent) {
      setConnectionError(t.h2hRequired);
      return;
    }
    if (getSendQueueDepth() >= MAX_SEND_QUEUE_SIZE) {
      setConnectionError("发送队列已满，请稍等上一条消息发出后再继续。");
      return;
    }
    const pendingId = `pending-${Date.now()}-${Math.random()
      .toString(36)
      .slice(2)}`;

    sendQueueRef.current.push({
      branchId,
      content: normalizedContent,
      pendingId,
      receiverUserId: normalizedReceiverUserId,
    });
    setMessages((currentMessages) => [
      ...currentMessages,
      {
        id: pendingId,
        direction: "me",
        content: normalizedContent,
        timestamp: new Date().toISOString(),
        senderUserId: currentUserIdRef.current ?? undefined,
        receiverUserId: Number(normalizedReceiverUserId),
        delivered: false,
      },
    ]);
    setInputMessage("");
    syncSendQueueState();
    if (!isSendingRef.current && sendQueueTimerRef.current === null) {
      void processSendQueue();
    }
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  return (
    <section className="flex h-[44rem] flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm md:h-[32rem] md:flex-row">
      <aside className="flex h-52 w-full shrink-0 flex-col border-b border-gray-200 bg-gray-50 md:h-auto md:w-72 md:border-b-0 md:border-r">
        <div className="border-b border-gray-200 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-emerald-600">
                Symbiotic 1v1
              </p>
              <h2 className="mt-1 text-base font-semibold text-gray-950">
                {t.symbiotic1v1}
              </h2>
            </div>
            <span
              className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                isConnected
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-amber-100 text-amber-700"
              }`}
            >
              {isConnected ? t.connected : t.disconnected}
            </span>
          </div>
        </div>

        <div className="border-b border-gray-200 p-3">
          <input
            className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-950 outline-none transition focus:border-emerald-400 focus:ring-4 focus:ring-emerald-100"
            onChange={(event) => setDirectorySearch(event.target.value)}
            placeholder={t.contactSearchPlaceholder}
            value={directorySearch}
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {isLoadingDirectory ? (
            <div className="px-4 py-5 text-sm text-gray-500">
              {t.loadingDirectory}
            </div>
          ) : userDirectory.length === 0 ? (
            <div className="px-4 py-5 text-sm leading-6 text-gray-500">
              {t.noContacts}
            </div>
          ) : (
            userDirectory.map((user) => (
              <button
                className={`w-full border-b border-gray-200 px-4 py-3 text-left transition ${
                  user.user_id === receiverUserId ? "bg-white" : "hover:bg-white/80"
                }`}
                key={user.user_id}
                onClick={() => {
                  setReceiverUserId(user.user_id);
                  setConnectionError("");
                }}
                type="button"
              >
                <div className="flex items-center gap-3">
                  <ContactAvatar name={user.username} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-sm font-semibold text-gray-950">
                        {user.username}
                      </p>
                      {(user.unread_count ?? 0) > 0 ? (
                        <span className="min-w-5 rounded-full bg-rose-600 px-1.5 py-0.5 text-center text-[11px] font-bold text-white">
                          {user.unread_count}
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-1 truncate text-xs text-gray-500">
                      {t.privateChat}
                    </p>
                  </div>
                </div>
              </button>
            ))
          )}
          {userDirectory.length >= SOCIAL_SIDEBAR_LIMIT ? (
            <div className="px-4 py-3 text-xs leading-5 text-gray-500">
              {t.directoryLimitedHint(SOCIAL_SIDEBAR_LIMIT)}
            </div>
          ) : null}
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="shrink-0 border-b border-gray-200 px-5 py-4">
          <h3 className="text-base font-semibold text-gray-950">
            {activeContact?.username || t.choosePeer}
          </h3>
          <p className="mt-1 text-xs text-gray-500">
            {activeContact ? t.symbioticSubtitle : t.choosePeerHint}
          </p>
        </header>

        <div className="min-h-0 flex-1 bg-white">
          <div
            className="flex h-full flex-col gap-3 overflow-y-auto overscroll-contain p-4"
            ref={scrollContainerRef}
          >
            {messages.length === 0 ? (
              <div className="flex h-full items-center justify-center text-center text-sm text-gray-500">
                {receiverUserId ? t.h2hEmpty : t.choosePeerHint}
              </div>
            ) : (
              messages.map((message) => (
                <MessageBubble
                  deliveredLabel={t.delivered}
                  key={message.id}
                  message={message}
                  sendingLabel={t.sending}
                />
              ))
            )}
          </div>
        </div>

        {directoryError || connectionError ? (
          <div className="shrink-0 border-t border-rose-100 bg-rose-50 px-5 py-3 text-sm font-medium text-rose-700">
            {directoryError || connectionError}
          </div>
        ) : null}

        <form
          className="flex shrink-0 items-stretch gap-3 border-t border-gray-200 bg-gray-50 p-4"
          onSubmit={handleSendMessage}
        >
          <input
            className="min-w-0 flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-emerald-400 focus:ring-4 focus:ring-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!receiverUserId}
            onChange={(event) => setInputMessage(event.target.value)}
            onKeyDown={handleInputKeyDown}
            placeholder={receiverUserId ? t.inputPlaceholder : t.choosePeer}
            value={inputMessage}
          />
          <button
            className="inline-flex h-11 w-20 shrink-0 items-center justify-center rounded-xl bg-gray-950 px-3 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!receiverUserId || !inputMessage.trim() || isSendQueueFull}
            type="submit"
          >
            {t.send}
          </button>
        </form>
      </div>
    </section>
  );
}

function MessageBubble({
  deliveredLabel,
  message,
  sendingLabel,
}: {
  deliveredLabel: string;
  message: ChatBubble;
  sendingLabel: string;
}) {
  if (message.direction === "system") {
    return (
      <div className="self-center rounded-full bg-white px-3 py-1 text-xs font-medium text-gray-500">
        {message.content}
      </div>
    );
  }

  const isMine = message.direction === "me";
  return (
    <div className={`flex ${isMine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[82%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm ${
          isMine
            ? "bg-gray-950 text-white"
            : "border border-gray-200 bg-white text-gray-800"
        }`}
      >
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
        <p
          className={`mt-2 text-[11px] font-medium ${
            isMine ? "text-gray-300" : "text-gray-500"
          }`}
        >
          {formatMessageTime(message.timestamp)}
          {isMine
            ? ` · ${message.delivered ? deliveredLabel : sendingLabel}`
            : ""}
        </p>
      </div>
    </div>
  );
}

function buildSocialEventsUrl(token: string) {
  const apiBaseUrl = API_BASE_URL.trim();
  const baseUrl = apiBaseUrl ? new URL(apiBaseUrl, window.location.origin) : window.location.origin;
  const url = new URL("/api/social/events", baseUrl);
  url.searchParams.set("token", token);
  return url.toString();
}

function ContactAvatar({ name }: { name: string }) {
  return (
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-sm font-bold text-emerald-700">
      {initials(name)}
    </div>
  );
}

function toChatBubble(
  message: SocialMessage,
  currentUserId: number | null,
): ChatBubble {
  const isMine = message.sender_id === currentUserId;
  return {
    id: `${isMine ? "me" : "peer"}-${message.id}`,
    direction: isMine ? "me" : "peer",
    content: message.content,
    timestamp: message.timestamp,
    senderUserId: message.sender_id,
    receiverUserId: message.receiver_id ?? undefined,
    delivered: true,
  };
}

function parseRealtimePayload(data: unknown): IncomingRealtimePayload | null {
  const parsed = JSON.parse(String(data)) as unknown;
  return parsed && typeof parsed === "object"
    ? (parsed as IncomingRealtimePayload)
    : null;
}

function normalizeIncomingPayload(
  rawMsg: IncomingRealtimePayload | null,
): IncomingRealtimePayload | null {
  if (!rawMsg) {
    return null;
  }
  return {
    ...rawMsg,
    id: rawMsg.id ?? rawMsg.chat_log_id,
    sender_id: rawMsg.sender_id ?? rawMsg.sender_user_id,
    receiver_id: rawMsg.receiver_id ?? rawMsg.receiver_user_id,
  };
}

function areChatBubblesEqual(
  currentMessages: ChatBubble[],
  nextMessages: ChatBubble[],
) {
  if (currentMessages.length !== nextMessages.length) {
    return false;
  }
  return currentMessages.every((message, index) => {
    const nextMessage = nextMessages[index];
    return (
      message.id === nextMessage.id
      && message.direction === nextMessage.direction
      && message.content === nextMessage.content
      && message.timestamp === nextMessage.timestamp
      && message.senderUserId === nextMessage.senderUserId
      && message.receiverUserId === nextMessage.receiverUserId
    );
  });
}

function mergePendingChatBubbles(
  savedMessages: ChatBubble[],
  currentMessages: ChatBubble[],
) {
  const pendingMessages = currentMessages.filter((message) =>
    message.id.startsWith("pending-"),
  );
  if (pendingMessages.length === 0) {
    return savedMessages;
  }
  return [...savedMessages, ...pendingMessages];
}

function appendUniqueBubble(
  currentMessages: ChatBubble[],
  nextBubble: ChatBubble,
) {
  if (currentMessages.some((message) => message.id === nextBubble.id)) {
    return currentMessages;
  }
  return [...currentMessages, nextBubble];
}

function isRateLimitError(err: unknown) {
  return err instanceof Error && err.message.includes("Too many requests");
}

function initials(name: string) {
  const cleanName = String(name || "?").trim();
  return cleanName.slice(0, 2).toUpperCase();
}

function formatMessageTime(rawTimestamp: string) {
  const timestamp = Date.parse(rawTimestamp);
  if (Number.isNaN(timestamp)) {
    return rawTimestamp;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}
