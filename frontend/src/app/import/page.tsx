"use client";

import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { BranchSelector } from "@/components/BranchSelector";
import { useLanguage } from "@/components/LanguageContext";
import {
  Agent,
  AgentSessionChoice,
  ChatImportResponse,
  ImportedChatMessage,
  NpcAgentSenderSeedResult,
  apiRequest,
  formatAgentChoiceLabel,
} from "@/lib/api";
import { LoopSession, loadSession, saveSession } from "@/lib/session";

type RawChatMessage = {
  sender_id: string;
  content: string;
  timestamp?: string | null;
  parsed_at_ms?: number | null;
};

type ParseResult = {
  messages: RawChatMessage[];
  skipped: number;
};

const DEFAULT_BRANCH_ID = "main";

const TEXT_MESSAGE_PATTERNS: RegExp[] = [
  /^\[?((?:\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)\]?\s*(?:-|–|—)?\s*([^:：\n]+?)[:：]\s*(.*)$/,
  /^((?:\d{1,4}年\d{1,2}月\d{1,2}日)\s+\d{1,2}:\d{2}(?::\d{2})?)\s*(?:-|–|—)?\s*([^:：\n]+?)[:：]\s*(.*)$/,
  /^((?:\d{1,4}[./-]\d{1,2}[./-]\d{1,4})\s+\d{1,2}:\d{2}(?::\d{2})?)\s*(?:-|–|—)?\s*([^:：\n]+?)[:：]\s*(.*)$/,
];

const TEXT_HEADER_PATTERNS: RegExp[] = [
  /^((?:\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)\s+([^:：\n]{1,80})$/,
  /^((?:\d{1,4}年\d{1,2}月\d{1,2}日)\s+\d{1,2}:\d{2}(?::\d{2})?)\s+([^:：\n]{1,80})$/,
];

function parseTimestampToMs(rawTimestamp: string | null | undefined) {
  const value = String(rawTimestamp ?? "").trim();
  if (!value) {
    return null;
  }

  const normalizedChinese = value
    .replace(/年/g, "-")
    .replace(/月/g, "-")
    .replace(/日/g, "");
  const direct = Date.parse(normalizedChinese);
  if (!Number.isNaN(direct)) {
    return direct;
  }

  const match = normalizedChinese.match(
    /^(\d{1,4})[./-](\d{1,2})[./-](\d{1,4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM|am|pm)?$/,
  );
  if (!match) {
    return null;
  }

  const first = Number(match[1]);
  const second = Number(match[2]);
  const third = Number(match[3]);
  let year: number;
  let month: number;
  let day: number;

  if (match[1].length === 4) {
    year = first;
    month = second;
    day = third;
  } else {
    year = third < 100 ? 2000 + third : third;
    if (first > 12) {
      day = first;
      month = second;
    } else {
      month = first;
      day = second;
    }
  }

  let hour = Number(match[4]);
  const minute = Number(match[5]);
  const secondValue = Number(match[6] ?? 0);
  const amPm = match[7]?.toLowerCase();
  if (amPm === "pm" && hour < 12) {
    hour += 12;
  }
  if (amPm === "am" && hour === 12) {
    hour = 0;
  }

  const parsed = new Date(year, month - 1, day, hour, minute, secondValue).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

function parseChatFilePayload(payload: unknown, rootError: string): ParseResult {
  if (!Array.isArray(payload)) {
    throw new Error(rootError);
  }

  const messages: RawChatMessage[] = [];
  let skipped = 0;

  for (const item of payload) {
    if (!item || typeof item !== "object") {
      skipped += 1;
      continue;
    }

    const record = item as Record<string, unknown>;
    const senderId = String(
      record.sender_id ?? record.sender ?? record.sender_name ?? "",
    ).trim();
    const content = String(record.content ?? record.text ?? record.message ?? "").trim();
    if (!senderId || !content) {
      skipped += 1;
      continue;
    }

    const rawTimestamp = record.timestamp;
    const timestamp =
      rawTimestamp === undefined || rawTimestamp === null
        ? null
        : String(rawTimestamp).trim() || null;

    messages.push({
      sender_id: senderId,
      content,
      timestamp,
      parsed_at_ms: parseTimestampToMs(timestamp),
    });
  }

  return { messages, skipped };
}

function pushParsedTextMessage(
  messages: RawChatMessage[],
  timestamp: string,
  senderId: string,
  content: string,
) {
  const trimmedContent = content.trim();
  const trimmedSenderId = senderId.trim();
  if (!trimmedSenderId || !trimmedContent) {
    return false;
  }

  messages.push({
    sender_id: trimmedSenderId,
    content: trimmedContent,
    timestamp: timestamp.trim() || null,
    parsed_at_ms: parseTimestampToMs(timestamp),
  });
  return true;
}

function parseDelimitedChatText(text: string): ParseResult {
  const messages: RawChatMessage[] = [];
  const lines = text
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n");
  let skipped = 0;
  let pendingHeader: { timestamp: string; senderId: string } | null = null;

  for (const line of lines) {
    const trimmedLine = line.trim();
    if (!trimmedLine) {
      continue;
    }

    const match = TEXT_MESSAGE_PATTERNS.map((pattern) => trimmedLine.match(pattern)).find(
      Boolean,
    );
    if (match) {
      const [, timestamp, senderId, content] = match;
      if (!pushParsedTextMessage(messages, timestamp, senderId, content)) {
        skipped += 1;
      }
      continue;
    }

    const headerMatch = TEXT_HEADER_PATTERNS.map((pattern) =>
      trimmedLine.match(pattern),
    ).find(Boolean);
    if (headerMatch) {
      pendingHeader = {
        timestamp: headerMatch[1],
        senderId: headerMatch[2],
      };
      continue;
    }

    if (pendingHeader) {
      if (
        !pushParsedTextMessage(
          messages,
          pendingHeader.timestamp,
          pendingHeader.senderId,
          trimmedLine,
        )
      ) {
        skipped += 1;
      }
      pendingHeader = null;
      continue;
    }

    const previousMessage = messages[messages.length - 1];
    if (previousMessage) {
      previousMessage.content = `${previousMessage.content}\n${trimmedLine}`.trim();
    } else {
      skipped += 1;
    }
  }

  if (pendingHeader) {
    skipped += 1;
  }

  return { messages, skipped };
}

function htmlToText(html: string) {
  if (typeof DOMParser !== "undefined") {
    const document = new DOMParser().parseFromString(html, "text/html");
    return document.body.innerText;
  }
  return html
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(?:p|div|li|tr|section|article)>/gi, "\n")
    .replace(/<[^>]*>/g, " ");
}

function parseChatFile(file: File, text: string, rootError: string): ParseResult {
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (extension === "json" || file.type === "application/json") {
    return parseChatFilePayload(JSON.parse(text), rootError);
  }
  if (extension === "html" || extension === "htm" || file.type === "text/html") {
    return parseDelimitedChatText(htmlToText(text));
  }
  return parseDelimitedChatText(text);
}

function dateInputToMs(value: string, endOfDay = false) {
  if (!value) {
    return null;
  }
  const parsed = new Date(`${value}T${endOfDay ? "23:59:59.999" : "00:00:00.000"}`);
  const ms = parsed.getTime();
  return Number.isNaN(ms) ? null : ms;
}

function ChatImportView() {
  const router = useRouter();
  const { t } = useLanguage();
  const copy = t.import;
  const [session, setSession] = useState<LoopSession | null>(null);
  const [fileName, setFileName] = useState("");
  const [rawMessages, setRawMessages] = useState<RawChatMessage[]>([]);
  const [skippedRows, setSkippedRows] = useState(0);
  const [senderMap, setSenderMap] = useState<Record<string, string>>({});
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [topicTag, setTopicTag] = useState("");
  const [branches, setBranches] = useState<string[]>([DEFAULT_BRANCH_ID]);
  const [currentBranch, setCurrentBranch] = useState(DEFAULT_BRANCH_ID);
  const [agentChoices, setAgentChoices] = useState<AgentSessionChoice[]>([]);
  const [result, setResult] = useState<ChatImportResponse | null>(null);
  const [error, setError] = useState("");
  const [isParsing, setIsParsing] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);
  const [isCreatingNpcs, setIsCreatingNpcs] = useState(false);

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
        void loadBranches();
      } catch {
        setSession(storedSession);
        setError(copy.noAgent);
        void loadBranches();
      }
    }

    bootstrap();
  }, [router]);

  const filteredMessages = useMemo(() => {
    const startMs = dateInputToMs(startDate);
    const endMs = dateInputToMs(endDate, true);
    if (startMs === null && endMs === null) {
      return rawMessages;
    }
    return rawMessages.filter((message) => {
      if (message.parsed_at_ms === null || message.parsed_at_ms === undefined) {
        return false;
      }
      if (startMs !== null && message.parsed_at_ms < startMs) {
        return false;
      }
      if (endMs !== null && message.parsed_at_ms > endMs) {
        return false;
      }
      return true;
    });
  }, [endDate, rawMessages, startDate]);

  const senderIds = useMemo(
    () =>
      Array.from(new Set(filteredMessages.map((message) => message.sender_id))).sort(),
    [filteredMessages],
  );

  const mappedMessages = useMemo<ImportedChatMessage[]>(() => {
    const messages: ImportedChatMessage[] = [];
    for (const message of filteredMessages) {
      const mappedAgentId = Number(senderMap[message.sender_id]);
      if (!Number.isInteger(mappedAgentId) || mappedAgentId <= 0) {
        continue;
      }
      messages.push({
        sender_agent_id: mappedAgentId,
        content: message.content,
        timestamp: message.timestamp ?? null,
      });
    }
    return messages;
  }, [filteredMessages, senderMap]);

  const mappingComplete =
    senderIds.length > 0 &&
    senderIds.every((senderId) => {
      const mappedAgentId = Number(senderMap[senderId]);
      return Number.isInteger(mappedAgentId) && mappedAgentId > 0;
    });

  const agentLabelById = useMemo(() => {
    const labels = new Map<number, string>();
    if (session?.agent_id) {
      labels.set(
        session.agent_id,
        `@${session.username} · ${
          session.agent_is_npc
            ? `${session.agent_name ?? copy.currentAgent} [NPC]`
            : session.agent_name ?? copy.currentAgent
        }`,
      );
    }
    for (const choice of agentChoices) {
      labels.set(choice.agent.id, formatAgentChoiceLabel(choice));
    }
    return labels;
  }, [agentChoices, session]);
  const selectableAgentChoices = useMemo(
    () =>
      agentChoices.filter((choice) => choice.agent.id !== session?.agent_id),
    [agentChoices, session?.agent_id],
  );
  const unmappedSenderIds = useMemo(
    () =>
      senderIds.filter((senderId) => {
        const mappedAgentId = Number(senderMap[senderId]);
        return !Number.isInteger(mappedAgentId) || mappedAgentId <= 0;
      }),
    [senderIds, senderMap],
  );

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setError("");
    setResult(null);
    setRawMessages([]);
    setSkippedRows(0);
    setSenderMap({});
    setStartDate("");
    setEndDate("");

    if (!file) {
      setFileName("");
      return;
    }

    setFileName(file.name);
    setIsParsing(true);
    try {
      const text = await file.text();
      const parsed = parseChatFile(file, text, copy.parseRootError);
      if (parsed.messages.length === 0) {
        throw new Error(copy.noValidMessages);
      }

      setRawMessages(parsed.messages);
      setSkippedRows(parsed.skipped);
      setSenderMap(
        Object.fromEntries(
          Array.from(new Set(parsed.messages.map((message) => message.sender_id)))
            .sort()
            .map((senderId) => [
              senderId,
              session?.agent_id && senderId === session.username
                ? String(session.agent_id)
                : "",
            ]),
        ),
      );
    } catch (err) {
      setFileName("");
      setError(err instanceof Error ? err.message : copy.parseFailed(file.name));
    } finally {
      setIsParsing(false);
      event.target.value = "";
    }
  }

  async function importChat() {
    if (!session?.agent_id || !mappingComplete || mappedMessages.length === 0) {
      return;
    }

    setError("");
    setResult(null);
    setIsImporting(true);
    try {
      const response = await apiRequest<ChatImportResponse>(
        "/api/agents/me/import_chat",
        {
          method: "POST",
          body: JSON.stringify({
            branch_id: currentBranch,
            messages: mappedMessages,
            topic: topicTag.trim() || null,
          }),
        },
      );
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.importFailed);
    } finally {
      setIsImporting(false);
    }
  }

  async function loadAgentChoices() {
    setError("");
    setIsLoadingAgents(true);
    try {
      const choices = await apiRequest<AgentSessionChoice[]>(
        "/api/users/agent-choices",
      );
      setAgentChoices(choices);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.loadAgentsFailed);
    } finally {
      setIsLoadingAgents(false);
    }
  }

  async function loadBranches() {
    setIsLoadingBranches(true);
    try {
      const result = await apiRequest<unknown>("/api/simulation/branches");
      const nextBranches = normalizeBranches(result);
      setBranches(nextBranches);
      setCurrentBranch((branchId) =>
        nextBranches.includes(branchId) ? branchId : DEFAULT_BRANCH_ID,
      );
    } catch {
      setBranches([DEFAULT_BRANCH_ID]);
      setCurrentBranch(DEFAULT_BRANCH_ID);
    } finally {
      setIsLoadingBranches(false);
    }
  }

  async function createNpcAgentsForUnmappedSenders() {
    if (unmappedSenderIds.length === 0) {
      return;
    }

    setError("");
    setIsCreatingNpcs(true);
    try {
      const createdChoices = await apiRequest<NpcAgentSenderSeedResult[]>(
        "/api/users/npc-agents/from-senders",
        {
          method: "POST",
          body: JSON.stringify({
            sender_ids: unmappedSenderIds,
          }),
        },
      );
      setAgentChoices((currentChoices) => {
        const choicesByAgentId = new Map<number, AgentSessionChoice>();
        for (const choice of [...createdChoices, ...currentChoices]) {
          choicesByAgentId.set(choice.agent.id, {
            user: choice.user,
            agent: choice.agent,
          });
        }
        return Array.from(choicesByAgentId.values()).sort((left, right) => {
          if (left.agent.is_npc !== right.agent.is_npc) {
            return left.agent.is_npc ? -1 : 1;
          }
          return left.agent.id - right.agent.id;
        });
      });
      setSenderMap((currentMap) => {
        const nextMap = { ...currentMap };
        for (const choice of createdChoices) {
          nextMap[choice.sender_id] = String(choice.agent.id);
        }
        return nextMap;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.createNpcsFailed);
    } finally {
      setIsCreatingNpcs(false);
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
            {t.nav.import}
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950">
            {copy.title}
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-500">
            {copy.subtitle}
          </p>
          <p className="mt-2 text-sm text-gray-400">
            {t.common.importingAs}{" "}
            <span className="font-medium text-gray-600">@{session.username}</span>
            {" · "}
            <span className="font-medium text-gray-600">
              {session.agent_name ?? t.common.noAgentYet}
            </span>
          </p>
        </header>

        {error ? (
          <div className="mb-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 shadow-sm">
            {error}
          </div>
        ) : null}

        {result ? (
          <div className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 shadow-sm">
            {copy.importedSummary(
              result.records_received,
              session.agent_name ?? copy.currentAgent,
              result.me_messages,
              result.others_messages,
              result.chunks_added,
            )}
          </div>
        ) : null}

        <div className="grid gap-5 lg:grid-cols-[minmax(0,0.82fr)_minmax(380px,1fr)]">
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold text-gray-950">
                  {copy.sourceFile}
                </h2>
                <p className="mt-1 text-sm text-gray-500">
                  {fileName || copy.noFile}
                </p>
              </div>
              <label className="inline-flex cursor-pointer items-center justify-center rounded-full bg-gray-950 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800">
                <input
                  accept="application/json,text/plain,text/html,.json,.txt,.html,.htm"
                  className="sr-only"
                  disabled={isParsing || isImporting}
                  onChange={handleFileChange}
                  type="file"
                />
                {isParsing ? copy.parsing : copy.chooseFile}
              </label>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  {copy.messages}
                </p>
                <p className="mt-1 text-2xl font-bold text-gray-950">
                  {rawMessages.length}
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  {copy.selected}
                </p>
                <p className="mt-1 text-2xl font-bold text-gray-950">
                  {filteredMessages.length}
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  {copy.senders}
                </p>
                <p className="mt-1 text-2xl font-bold text-gray-950">
                  {senderIds.length}
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  {copy.skipped}
                </p>
                <p className="mt-1 text-2xl font-bold text-gray-950">
                  {skippedRows}
                </p>
              </div>
            </div>

            <div className="mt-5 rounded-xl border border-gray-200 bg-gray-50 p-4">
              <div>
                <h2 className="text-base font-semibold text-gray-950">
                  {copy.advancedFilters}
                </h2>
                <p className="mt-1 text-sm text-gray-500">
                  {copy.filterHelp}
                </p>
              </div>
              <div className="mt-4 space-y-4">
                <BranchSelector
                  branches={branches}
                  className="max-w-2xl"
                  disabled={isImporting}
                  isLoading={isLoadingBranches}
                  label={t.common.branchSelector}
                  loadingLabel={t.common.refreshing}
                  onChange={setCurrentBranch}
                  onRefresh={loadBranches}
                  refreshLabel={t.common.refreshBranches}
                  value={currentBranch}
                />
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  <label className="block">
                    <span className="text-xs font-medium text-gray-500">
                      {copy.startDate}
                    </span>
                    <input
                      className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                      max={endDate || undefined}
                      onChange={(event) => setStartDate(event.target.value)}
                      type="date"
                      value={startDate}
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-gray-500">
                      {copy.endDate}
                    </span>
                    <input
                      className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                      min={startDate || undefined}
                      onChange={(event) => setEndDate(event.target.value)}
                      type="date"
                      value={endDate}
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-gray-500">
                      {copy.topicTag}
                    </span>
                    <input
                      className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                      maxLength={80}
                      onChange={(event) => setTopicTag(event.target.value)}
                      placeholder={copy.topicPlaceholder}
                      type="text"
                      value={topicTag}
                    />
                  </label>
                </div>
              </div>
            </div>

            <div className="mt-6">
              <div className="flex flex-col gap-3">
                <div>
                  <h2 className="text-base font-semibold text-gray-950">
                    {copy.senderMapping}
                  </h2>
                  <p className="mt-1 text-sm text-gray-500">
                    {copy.senderMappingHelp}
                  </p>
                </div>
                <div className="flex w-full flex-col gap-2 md:flex-row md:flex-wrap md:items-end lg:w-auto">
                  <button
                    className="shrink-0 rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={isLoadingAgents}
                    onClick={loadAgentChoices}
                    type="button"
                  >
                    {isLoadingAgents ? t.common.loading : copy.loadAgents}
                  </button>
                  <button
                    className="shrink-0 rounded-full border border-gray-300 px-4 py-2 text-sm font-medium text-gray-800 transition hover:border-gray-900 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={
                      isCreatingNpcs ||
                      isParsing ||
                      unmappedSenderIds.length === 0
                    }
                    onClick={createNpcAgentsForUnmappedSenders}
                    type="button"
                  >
                    {isCreatingNpcs
                      ? copy.creatingNpcs
                      : copy.createNpcs(unmappedSenderIds.length)}
                  </button>
                </div>
              </div>
              <div className="mt-3 overflow-hidden rounded-xl border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200 text-sm">
                  <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-4 py-3 font-semibold">sender_id</th>
                      <th className="px-4 py-3 font-semibold">{copy.loopAgent}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {senderIds.length > 0 ? (
                      senderIds.map((senderId) => (
                        <tr key={senderId}>
                          <td className="px-4 py-3 font-mono text-xs text-gray-700">
                            {senderId}
                          </td>
                          <td className="px-4 py-3">
                            <select
                              className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                              onChange={(event) =>
                                setSenderMap((currentMap) => ({
                                  ...currentMap,
                                  [senderId]: event.target.value,
                                }))
                              }
                              value={senderMap[senderId] ?? ""}
                            >
                              <option value="">{copy.chooseAgent}</option>
                              {session.agent_id ? (
                                <option value={session.agent_id}>
                                  @{session.username} ·{" "}
                                  {session.agent_is_npc
                                    ? `${session.agent_name ?? copy.currentAgent} [NPC]`
                                    : session.agent_name ?? copy.currentAgent}
                                </option>
                              ) : null}
                              {selectableAgentChoices.map((choice) => (
                                <option
                                  key={choice.agent.id}
                                  value={choice.agent.id}
                                >
                                  {formatAgentChoiceLabel(choice)}
                                </option>
                              ))}
                            </select>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td
                          className="px-4 py-8 text-center text-sm text-gray-400"
                          colSpan={2}
                        >
                          {copy.uploadToPopulate}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <button
              className="mt-5 w-full rounded-full bg-indigo-600 px-5 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-gray-300"
              disabled={
                !session.agent_id ||
                !mappingComplete ||
                mappedMessages.length === 0 ||
                isImporting
              }
              onClick={importChat}
              type="button"
            >
              {isImporting ? copy.importing : copy.importButton}
            </button>
          </section>

          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-gray-950">
                {copy.previewTitle}
              </h2>
              <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-500">
                {copy.mapped(mappedMessages.length, filteredMessages.length)}
              </span>
            </div>

            <div className="mt-4 max-h-[620px] space-y-4 overflow-y-auto pr-1">
              {filteredMessages.length > 0 ? (
                filteredMessages.slice(0, 50).map((message, index) => {
                  const mappedAgentId = senderMap[message.sender_id];
                  const isMe =
                    session.agent_id !== undefined &&
                    Number(mappedAgentId) === session.agent_id;
                  return (
                    <article
                      className={`flex ${isMe ? "justify-end" : "justify-start"}`}
                      key={`${message.sender_id}-${index}`}
                    >
                      <div className="max-w-[88%]">
                        <div
                          className={`mb-1 flex flex-wrap items-center gap-2 text-xs ${
                            isMe ? "justify-end text-indigo-400" : "text-gray-400"
                          }`}
                        >
                          <span className="font-mono text-gray-600">
                            {message.sender_id}
                          </span>
                          <span>→</span>
                          <span className="font-semibold text-gray-700">
                            {mappedAgentId
                              ? agentLabelById.get(Number(mappedAgentId)) ??
                                `Agent #${mappedAgentId}`
                              : copy.unmapped}
                          </span>
                          {message.timestamp ? <span>{message.timestamp}</span> : null}
                        </div>
                        <div
                          className={`rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm ${
                            isMe
                              ? "rounded-tr-sm bg-indigo-600 text-white"
                              : "rounded-tl-sm border border-gray-200 bg-gray-50 text-gray-800"
                          }`}
                        >
                          <p className="whitespace-pre-wrap break-words">
                            {message.content}
                          </p>
                        </div>
                      </div>
                    </article>
                  );
                })
              ) : (
                <div className="rounded-xl border border-dashed border-gray-200 px-4 py-12 text-center text-sm text-gray-400">
                  {copy.noParsed}
                </div>
              )}
            </div>

            {filteredMessages.length > 50 ? (
              <p className="mt-3 text-xs text-gray-400">
                {copy.previewLimited}
              </p>
            ) : null}
          </section>
        </div>
      </div>
    </main>
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

export default ChatImportView;
