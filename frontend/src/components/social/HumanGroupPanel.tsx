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
  SocialGroup,
  SocialMessage,
  UserDirectoryEntry,
  createSocialGroup,
  getSocialGroupMessages,
  getSocialGroups,
  getUsersDirectory,
  sendSocialGroupMessage,
} from "@/lib/api";
import { useUiLanguage } from "@/lib/i18n";
import { getAccessToken, loadSession } from "@/lib/session";
import { dictionary } from "@/locales/dictionary";

type HumanGroupPanelProps = {
  branchId: string;
};

const ACTIVE_GROUP_SYNC_MS = 1000;
const RATE_LIMIT_RETRY_MS = 5000;
const MAX_SEND_QUEUE_SIZE = 3;
const SCROLL_BOTTOM_THRESHOLD_PX = 96;
const SOCIAL_SIDEBAR_LIMIT = 100;

type GroupBubble = {
  id: string;
  direction: "me" | "peer";
  senderId: number;
  senderName: string;
  content: string;
  timestamp: string;
};

type IncomingGroupPayload = {
  type?: string;
  id?: number;
  chat_log_id?: number;
  group_id?: string | null;
  sender_id?: number;
  sender_user_id?: number;
  sender_username?: string;
  content?: string;
  timestamp?: string;
};

export function HumanGroupPanel({ branchId }: HumanGroupPanelProps) {
  const { language } = useUiLanguage();
  const t = dictionary[language].social;
  const common = dictionary[language].common;
  const eventSourceRef = useRef<EventSource | null>(null);
  const activeGroupIdRef = useRef("");
  const currentUserIdRef = useRef<number | null>(null);
  const isSendingRef = useRef(false);
  const shouldScrollToBottomRef = useRef(false);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const sendQueueRef = useRef<
    Array<{ branchId: string; content: string; groupId: string; pendingId: string }>
  >([]);
  const sendQueueTimerRef = useRef<number | null>(null);
  const [groups, setGroups] = useState<SocialGroup[]>([]);
  const [activeGroupId, setActiveGroupId] = useState("");
  const [messages, setMessages] = useState<GroupBubble[]>([]);
  const [contacts, setContacts] = useState<UserDirectoryEntry[]>([]);
  const [selectedContactIds, setSelectedContactIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [draft, setDraft] = useState("");
  const [groupName, setGroupName] = useState("");
  const [groupSearch, setGroupSearch] = useState("");
  const [contactSearch, setContactSearch] = useState("");
  const [isComposerOpen, setIsComposerOpen] = useState(false);
  const [isLoadingGroups, setIsLoadingGroups] = useState(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [sendQueueDepth, setSendQueueDepth] = useState(0);
  const [isCreatingGroup, setIsCreatingGroup] = useState(false);
  const [error, setError] = useState("");
  const isSendQueueFull = sendQueueDepth >= MAX_SEND_QUEUE_SIZE;

  const activeGroup = useMemo(
    () => groups.find((group) => group.id === activeGroupId) ?? null,
    [activeGroupId, groups],
  );

  useEffect(() => {
    currentUserIdRef.current = loadSession()?.user_id ?? null;
  }, []);

  useEffect(() => {
    return () => {
      if (sendQueueTimerRef.current !== null) {
        window.clearTimeout(sendQueueTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    activeGroupIdRef.current = activeGroupId;
  }, [activeGroupId]);

  useEffect(() => {
    if (!shouldScrollToBottomRef.current) {
      return;
    }
    shouldScrollToBottomRef.current = false;
    scrollMessagesToBottom();
  }, [messages]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      async function loadGroups() {
        setIsLoadingGroups(true);
        setError("");
        try {
          const groupList = await getSocialGroups({
            q: groupSearch,
            limit: SOCIAL_SIDEBAR_LIMIT,
            branchId,
          });
          setGroups(groupList);
          setActiveGroupId((currentGroupId) =>
            currentGroupId && groupList.some((group) => group.id === currentGroupId)
              ? currentGroupId
              : groupList[0]?.id || "",
          );
        } catch (err) {
          setError(err instanceof Error ? err.message : t.groupListFailed);
        } finally {
          setIsLoadingGroups(false);
        }
      }

      void loadGroups();
    }, 250);

    return () => window.clearTimeout(timeoutId);
  }, [branchId, groupSearch, t]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      async function loadContacts() {
        try {
          const contactList = await getUsersDirectory({
            q: contactSearch,
            limit: SOCIAL_SIDEBAR_LIMIT,
            branchId,
          });
          setContacts(contactList);
        } catch (err) {
          console.debug("[Loop Social] failed to load group contacts", err);
        }
      }

      void loadContacts();
    }, 250);

    return () => window.clearTimeout(timeoutId);
  }, [branchId, contactSearch]);

  useEffect(() => {
    async function loadMessages() {
      if (!activeGroupId) {
        setMessages([]);
        return;
      }

      setIsLoadingMessages(true);
      setError("");
      try {
        await refreshGroupMessagesFromServer(activeGroupId, branchId);
      } catch (err) {
        setError(err instanceof Error ? err.message : t.groupMessagesFailed);
      } finally {
        setIsLoadingMessages(false);
      }
    }

    void loadMessages();
  }, [activeGroupId, branchId, t]);

  useEffect(() => {
    if (!activeGroupId) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void refreshGroupMessagesFromServer(activeGroupId, branchId);
    }, ACTIVE_GROUP_SYNC_MS);

    return () => window.clearInterval(intervalId);
  }, [activeGroupId, branchId]);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) {
      return;
    }

    const eventSource = new EventSource(buildSocialEventsUrl(token));
    eventSourceRef.current = eventSource;
    eventSource.onmessage = (event) => {
      let normalizedMsg: IncomingGroupPayload | null = null;
      try {
        const rawMsg = parseGroupPayload(event.data.trim());
        normalizedMsg = normalizeIncomingGroupPayload(rawMsg);
      } catch (err) {
        console.error("SSE Parse Error:", event.data, err);
        return;
      }
      if (!normalizedMsg || normalizedMsg.type !== "social_group_message") {
        return;
      }

      const groupId = String(normalizedMsg.group_id || "");
      const content = String(normalizedMsg.content || "").trim();
      if (!groupId || !content) {
        return;
      }

      setGroups((currentGroups) =>
        currentGroups.map((group) =>
          group.id === groupId
            ? {
                ...group,
                latest_message: content,
                latest_timestamp: normalizedMsg.timestamp || new Date().toISOString(),
              }
            : group,
        ),
      );

      if (String(normalizedMsg.group_id) === String(activeGroupIdRef.current)) {
        const senderId = Number(normalizedMsg.sender_id);
        const nextBubble: GroupBubble = {
          id: `group-${normalizedMsg.id ?? Date.now()}`,
          direction: senderId === currentUserIdRef.current ? "me" : "peer",
          senderId,
          senderName: normalizedMsg.sender_username || t.unknownSender,
          content,
          timestamp: normalizedMsg.timestamp || new Date().toISOString(),
        };
        const shouldScroll = isScrolledNearBottom();
        setMessages((currentMessages) => {
          if (currentMessages.some((message) => message.id === nextBubble.id)) {
            return currentMessages;
          }
          if (shouldScroll) {
            shouldScrollToBottomRef.current = true;
          }
          return [...currentMessages, nextBubble];
        });
      } else {
        console.debug("[Loop Social] group message for inactive room", normalizedMsg);
      }
    };

    return () => {
      eventSource.onmessage = null;
      eventSource.onerror = null;
      eventSource.onopen = null;
      eventSource.close();
      if (eventSourceRef.current === eventSource) {
        eventSourceRef.current = null;
      }
    };
  }, [t]);

  async function handleCreateGroup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (selectedContactIds.size === 0) {
      setError(t.groupCreateRequiresContact);
      return;
    }

    setIsCreatingGroup(true);
    try {
      const group = await createSocialGroup({
        contact_ids: Array.from(selectedContactIds).map(Number),
        name: groupName.trim() || undefined,
      });
      setGroups((currentGroups) => [group, ...currentGroups]);
      setActiveGroupId(group.id);
      setMessages([]);
      setSelectedContactIds(new Set());
      setGroupName("");
      setIsComposerOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.groupCreateFailed);
    } finally {
      setIsCreatingGroup(false);
    }
  }

  async function refreshGroupMessagesFromServer(groupId: string, activeBranchId: string) {
    if (!groupId) {
      return;
    }
    try {
      const history = await getSocialGroupMessages(groupId, activeBranchId);
      const nextMessages = history.map((message) =>
        toGroupBubble(message, currentUserIdRef.current),
      );
      const shouldScroll = isScrolledNearBottom();
      setMessages((currentMessages) => {
        const mergedMessages = mergePendingGroupBubbles(
          nextMessages,
          currentMessages,
        );
        if (
          shouldScroll
          && mergedMessages.length > currentMessages.length
        ) {
          shouldScrollToBottomRef.current = true;
        }
        return areGroupBubblesEqual(currentMessages, mergedMessages)
          ? currentMessages
          : mergedMessages;
      });
    } catch (err) {
      console.debug("[Loop Social] failed to refresh active group", err);
    }
  }

  function isScrolledNearBottom() {
    const container = scrollContainerRef.current;
    if (!container) {
      return true;
    }
    return (
      container.scrollHeight - container.scrollTop - container.clientHeight
      <= SCROLL_BOTTOM_THRESHOLD_PX
    );
  }

  function scrollMessagesToBottom() {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }
    container.scrollTop = container.scrollHeight;
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
      setError((currentError) =>
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
    setError("");
    try {
      const savedMessage = await sendSocialGroupMessage(nextMessage.groupId, {
        content: nextMessage.content,
        branch_id: nextMessage.branchId,
        topic: "group_chat",
      });
      if (
        String(nextMessage.groupId) === String(activeGroupIdRef.current)
        && nextMessage.branchId === branchId
      ) {
        const nextBubble = toGroupBubble(savedMessage, currentUserIdRef.current);
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
      setGroups((currentGroups) =>
        currentGroups.map((group) =>
          group.id === nextMessage.groupId
            ? {
                ...group,
                latest_message: savedMessage.content,
                latest_timestamp: savedMessage.timestamp,
              }
            : group,
        ),
      );
    } catch (err) {
      sendQueueRef.current.unshift(nextMessage);
      if (isRateLimitError(err)) {
        console.debug("[Loop Social] group send queue paused by rate limit", err);
      } else {
        setError(err instanceof Error ? err.message : t.groupSendFailed());
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

  async function handleSendMessage() {
    const content = draft.trim();
    if (!activeGroupId || !content) {
      return;
    }
    if (getSendQueueDepth() >= MAX_SEND_QUEUE_SIZE) {
      setError("发送队列已满，请稍等上一条消息发出后再继续。");
      return;
    }
    const pendingId = `pending-group-${Date.now()}-${Math.random()
      .toString(36)
      .slice(2)}`;

    sendQueueRef.current.push({
      branchId,
      content,
      groupId: activeGroupId,
      pendingId,
    });
    shouldScrollToBottomRef.current = true;
    setMessages((currentMessages) => [
      ...currentMessages,
      {
        id: pendingId,
        direction: "me",
        senderId: currentUserIdRef.current ?? 0,
        senderName: t.unknownSender,
        content,
        timestamp: new Date().toISOString(),
      },
    ]);
    syncSendQueueState();
    setError("");
    setDraft("");
    if (!isSendingRef.current && sendQueueTimerRef.current === null) {
      void processSendQueue();
    }
  }

  function handleDraftKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    void handleSendMessage();
  }

  function toggleContact(contactId: string) {
    setSelectedContactIds((currentIds) => {
      const nextIds = new Set(currentIds);
      if (nextIds.has(contactId)) {
        nextIds.delete(contactId);
      } else {
        nextIds.add(contactId);
      }
      return nextIds;
    });
  }

  return (
    <section className="flex h-[44rem] flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm md:h-[32rem] md:flex-row">
      <aside className="flex h-52 w-full shrink-0 flex-col border-b border-gray-200 bg-gray-50 md:h-auto md:w-72 md:border-b-0 md:border-r">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-indigo-600">
              Human Room
            </p>
            <h2 className="mt-1 text-base font-semibold text-gray-950">
              {t.humanGroup}
            </h2>
          </div>
          <button
            className="inline-flex h-9 items-center rounded-lg bg-gray-950 px-3 text-sm font-semibold text-white transition hover:bg-gray-800"
            onClick={() => setIsComposerOpen(true)}
            type="button"
          >
            {t.createGroup}
          </button>
        </div>

        <div className="border-b border-gray-200 p-3">
          <input
            className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-950 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
            onChange={(event) => setGroupSearch(event.target.value)}
            placeholder={t.groupSearchPlaceholder}
            value={groupSearch}
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {isLoadingGroups ? (
            <div className="px-4 py-5 text-sm text-gray-500">
              {t.loadingGroups}
            </div>
          ) : groups.length === 0 ? (
            <div className="px-4 py-5 text-sm leading-6 text-gray-500">
              {t.groupEmpty}
            </div>
          ) : (
            groups.map((group) => (
              <button
                className={`w-full border-b border-gray-200 px-4 py-3 text-left transition ${
                  group.id === activeGroupId
                    ? "bg-white"
                    : "hover:bg-white/80"
                }`}
                key={group.id}
                onClick={() => setActiveGroupId(group.id)}
                type="button"
              >
                <div className="flex items-center gap-3">
                  <GroupAvatar name={group.name} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-sm font-semibold text-gray-950">
                        {group.name}
                      </p>
                      <span className="text-[11px] font-medium text-gray-400">
                        {group.member_count}
                      </span>
                    </div>
                    <p className="mt-1 truncate text-xs text-gray-500">
                      {group.latest_message || t.noGroupMessages}
                    </p>
                  </div>
                </div>
              </button>
            ))
          )}
          {groups.length >= SOCIAL_SIDEBAR_LIMIT ? (
            <div className="px-4 py-3 text-xs leading-5 text-gray-500">
              {t.groupLimitedHint(SOCIAL_SIDEBAR_LIMIT)}
            </div>
          ) : null}
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="shrink-0 border-b border-gray-200 px-5 py-4">
          <h3 className="text-base font-semibold text-gray-950">
            {activeGroup?.name || t.chooseGroup}
          </h3>
          <p className="mt-1 text-xs text-gray-500">
            {activeGroup
              ? t.groupMemberCount(activeGroup.member_count)
              : t.chooseGroupHint}
          </p>
        </header>

        <div className="min-h-0 flex-1 bg-white">
          <div
            className="flex h-full flex-col gap-4 overflow-y-auto overscroll-contain px-5 py-4"
            ref={scrollContainerRef}
          >
            {isLoadingMessages ? (
              <div className="flex h-full items-center justify-center text-sm text-gray-500">
                {t.loadingMessages}
              </div>
            ) : messages.length === 0 ? (
              <div className="flex h-full items-center justify-center text-center text-sm text-gray-500">
                {activeGroup ? t.groupMessageEmpty : t.chooseGroupHint}
              </div>
            ) : (
              messages.map((message) => (
                <GroupMessageBubble key={message.id} message={message} />
              ))
            )}
          </div>
        </div>

        {error ? (
          <div className="shrink-0 border-t border-rose-100 bg-rose-50 px-5 py-3 text-sm font-medium text-rose-700">
            {error}
          </div>
        ) : null}

        <div className="shrink-0 border-t border-gray-200 bg-gray-50 p-4">
          <div className="flex items-stretch gap-3">
            <textarea
              className="h-12 min-w-0 flex-1 resize-none rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm leading-5 text-gray-950 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!activeGroup}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleDraftKeyDown}
              placeholder={activeGroup ? t.groupInputPlaceholder : t.chooseGroup}
              value={draft}
            />
            <button
              className="inline-flex h-12 w-20 shrink-0 items-center justify-center rounded-xl bg-gray-950 px-3 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!activeGroup || !draft.trim() || isSendQueueFull}
              onClick={() => void handleSendMessage()}
              type="button"
            >
              {isSending ? common.sending : t.send}
            </button>
          </div>
        </div>
      </div>

      {isComposerOpen ? (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-gray-950/35 px-4">
          <form
            className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-xl"
            onSubmit={handleCreateGroup}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-950">
                  {t.createGroup}
                </h3>
                <p className="mt-1 text-sm text-gray-500">
                  {t.createGroupHint}
                </p>
              </div>
              <button
                className="rounded-lg px-2 py-1 text-sm font-semibold text-gray-500 hover:bg-gray-100"
                onClick={() => setIsComposerOpen(false)}
                type="button"
              >
                {common.cancel}
              </button>
            </div>

            <input
              className="mt-5 w-full rounded-xl border border-gray-200 px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
              onChange={(event) => setGroupName(event.target.value)}
              placeholder={t.groupNamePlaceholder}
              value={groupName}
            />

            <input
              className="mt-3 w-full rounded-xl border border-gray-200 px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
              onChange={(event) => setContactSearch(event.target.value)}
              placeholder={t.contactSearchPlaceholder}
              value={contactSearch}
            />

            <div className="mt-4 max-h-72 overflow-y-auto rounded-xl border border-gray-200">
              {contacts.length === 0 ? (
                <div className="px-4 py-5 text-sm text-gray-500">
                  {t.noContacts}
                </div>
              ) : (
                contacts.map((contact) => (
                  <label
                    className="flex cursor-pointer items-center gap-3 border-b border-gray-100 px-4 py-3 last:border-b-0 hover:bg-gray-50"
                    key={contact.user_id}
                  >
                    <input
                      checked={selectedContactIds.has(contact.user_id)}
                      className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                      onChange={() => toggleContact(contact.user_id)}
                      type="checkbox"
                    />
                    <UserAvatar name={contact.username} />
                    <span className="text-sm font-medium text-gray-800">
                      {contact.username}
                    </span>
                  </label>
                ))
              )}
              {contacts.length >= SOCIAL_SIDEBAR_LIMIT ? (
                <div className="px-4 py-3 text-xs leading-5 text-gray-500">
                  {t.directoryLimitedHint(SOCIAL_SIDEBAR_LIMIT)}
                </div>
              ) : null}
            </div>

            <div className="mt-5 flex justify-end gap-3">
              <button
                className="inline-flex h-10 items-center rounded-xl border border-gray-200 px-4 text-sm font-semibold text-gray-700 hover:bg-gray-50"
                onClick={() => setIsComposerOpen(false)}
                type="button"
              >
                {common.cancel}
              </button>
              <button
                className="inline-flex h-10 items-center rounded-xl bg-gray-950 px-4 text-sm font-semibold text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={isCreatingGroup || selectedContactIds.size === 0}
                type="submit"
              >
                {isCreatingGroup ? common.sending : t.createGroup}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}

function GroupMessageBubble({ message }: { message: GroupBubble }) {
  const isMine = message.direction === "me";
  return (
    <div className={`flex gap-3 ${isMine ? "justify-end" : "justify-start"}`}>
      {!isMine ? <UserAvatar name={message.senderName} /> : null}
      <div className={`max-w-[72%] ${isMine ? "items-end" : "items-start"}`}>
        <p
          className={`mb-1 text-xs font-medium text-gray-500 ${
            isMine ? "text-right" : "text-left"
          }`}
        >
          {message.senderName}
        </p>
        <div
          className={`rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm ${
            isMine
              ? "bg-gray-950 text-white"
              : "border border-gray-200 bg-gray-50 text-gray-800"
          }`}
        >
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
          <p
            className={`mt-2 text-[11px] ${
              isMine ? "text-gray-300" : "text-gray-500"
            }`}
          >
            {formatMessageTime(message.timestamp)}
          </p>
        </div>
      </div>
      {isMine ? <UserAvatar name={message.senderName} /> : null}
    </div>
  );
}

function GroupAvatar({ name }: { name: string }) {
  return (
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-100 text-sm font-bold text-indigo-700">
      {initials(name)}
    </div>
  );
}

function UserAvatar({ name }: { name: string }) {
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gray-200 text-xs font-bold text-gray-700">
      {initials(name)}
    </div>
  );
}

function toGroupBubble(
  message: SocialMessage,
  currentUserId: number | null,
): GroupBubble {
  const isMine = message.sender_id === currentUserId;
  return {
    id: `group-${message.id}`,
    direction: isMine ? "me" : "peer",
    senderId: message.sender_id,
    senderName: message.sender_username,
    content: message.content,
    timestamp: message.timestamp,
  };
}

function buildSocialEventsUrl(token: string) {
  const apiBaseUrl = API_BASE_URL.trim();
  const baseUrl = apiBaseUrl ? new URL(apiBaseUrl, window.location.origin) : window.location.origin;
  const url = new URL("/api/social/events", baseUrl);
  url.searchParams.set("token", token);
  return url.toString();
}

function parseGroupPayload(data: unknown): IncomingGroupPayload | null {
  const parsed = JSON.parse(String(data)) as unknown;
  return parsed && typeof parsed === "object"
    ? (parsed as IncomingGroupPayload)
    : null;
}

function normalizeIncomingGroupPayload(
  rawMsg: IncomingGroupPayload | null,
): IncomingGroupPayload | null {
  if (!rawMsg) {
    return null;
  }
  return {
    ...rawMsg,
    id: rawMsg.id ?? rawMsg.chat_log_id,
    sender_id: rawMsg.sender_id ?? rawMsg.sender_user_id,
  };
}

function areGroupBubblesEqual(
  currentMessages: GroupBubble[],
  nextMessages: GroupBubble[],
) {
  if (currentMessages.length !== nextMessages.length) {
    return false;
  }
  return currentMessages.every((message, index) => {
    const nextMessage = nextMessages[index];
    return (
      message.id === nextMessage.id
      && message.direction === nextMessage.direction
      && message.senderId === nextMessage.senderId
      && message.senderName === nextMessage.senderName
      && message.content === nextMessage.content
      && message.timestamp === nextMessage.timestamp
    );
  });
}

function mergePendingGroupBubbles(
  savedMessages: GroupBubble[],
  currentMessages: GroupBubble[],
) {
  const pendingMessages = currentMessages.filter((message) =>
    message.id.startsWith("pending-group-"),
  );
  if (pendingMessages.length === 0) {
    return savedMessages;
  }
  return [...savedMessages, ...pendingMessages];
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
