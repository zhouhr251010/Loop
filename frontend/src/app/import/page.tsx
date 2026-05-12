"use client";

import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useLanguage } from "@/components/LanguageContext";
import {
  Agent,
  AgentSessionChoice,
  ChatImportResponse,
  ImportedChatMessage,
  apiRequest,
} from "@/lib/api";
import { LoopSession, loadSession, saveSession } from "@/lib/session";

type RawChatMessage = {
  sender_id: string;
  content: string;
  timestamp?: string | null;
};

type ParseResult = {
  messages: RawChatMessage[];
  skipped: number;
};

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
    const senderId = String(record.sender_id ?? "").trim();
    const content = String(record.content ?? "").trim();
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
    });
  }

  return { messages, skipped };
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
  const [adminKey, setAdminKey] = useState("");
  const [agentChoices, setAgentChoices] = useState<AgentSessionChoice[]>([]);
  const [result, setResult] = useState<ChatImportResponse | null>(null);
  const [error, setError] = useState("");
  const [isParsing, setIsParsing] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);

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
      } catch {
        setSession(storedSession);
        setError(copy.noAgent);
      }
    }

    bootstrap();
  }, [router]);

  const senderIds = useMemo(
    () => Array.from(new Set(rawMessages.map((message) => message.sender_id))).sort(),
    [rawMessages],
  );

  const mappedMessages = useMemo<ImportedChatMessage[]>(() => {
    const messages: ImportedChatMessage[] = [];
    for (const message of rawMessages) {
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
  }, [rawMessages, senderMap]);

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
        `@${session.username} · ${session.agent_name ?? copy.currentAgent}`,
      );
    }
    for (const choice of agentChoices) {
      labels.set(choice.agent.id, `@${choice.user.username} · ${choice.agent.agent_name}`);
    }
    return labels;
  }, [agentChoices, session]);

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setError("");
    setResult(null);
    setRawMessages([]);
    setSkippedRows(0);
    setSenderMap({});

    if (!file) {
      setFileName("");
      return;
    }

    setFileName(file.name);
    setIsParsing(true);
    try {
      const text = await file.text();
      const parsed = parseChatFilePayload(JSON.parse(text), copy.parseRootError);
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
      setError(err instanceof Error ? err.message : copy.parseFailed);
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
          body: JSON.stringify({ messages: mappedMessages }),
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
    const trimmedAdminKey = adminKey.trim();
    if (!trimmedAdminKey) {
      setError(copy.adminRequired);
      return;
    }

    setError("");
    setIsLoadingAgents(true);
    try {
      const choices = await apiRequest<AgentSessionChoice[]>(
        "/api/users/agent-choices",
        {
          headers: {
            "X-Loop-Admin-Key": trimmedAdminKey,
          },
        },
      );
      setAgentChoices(choices);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.loadAgentsFailed);
    } finally {
      setIsLoadingAgents(false);
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
                  {copy.jsonFile}
                </h2>
                <p className="mt-1 text-sm text-gray-500">
                  {fileName || copy.noFile}
                </p>
              </div>
              <label className="inline-flex cursor-pointer items-center justify-center rounded-full bg-gray-950 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800">
                <input
                  accept="application/json,.json"
                  className="sr-only"
                  disabled={isParsing || isImporting}
                  onChange={handleFileChange}
                  type="file"
                />
                {isParsing ? copy.parsing : copy.chooseJson}
              </label>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-3">
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

            <div className="mt-6">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <h2 className="text-base font-semibold text-gray-950">
                    {copy.senderMapping}
                  </h2>
                  <p className="mt-1 text-sm text-gray-500">
                    {copy.senderMappingHelp}
                  </p>
                </div>
                <div className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-80 sm:flex-row sm:items-end">
                  <label className="block flex-1">
                    <span className="text-xs font-medium text-gray-500">
                      {t.common.adminKey}
                    </span>
                    <input
                      className="mt-1 w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                      onChange={(event) => setAdminKey(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          void loadAgentChoices();
                        }
                      }}
                      type="password"
                      value={adminKey}
                    />
                  </label>
                  <button
                    className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={isLoadingAgents}
                    onClick={loadAgentChoices}
                    type="button"
                  >
                    {isLoadingAgents ? t.common.loading : copy.loadAgents}
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
                                  {session.agent_name ?? copy.currentAgent}
                                </option>
                              ) : null}
                              {agentChoices.map((choice) => (
                                <option
                                  key={choice.agent.id}
                                  value={choice.agent.id}
                                >
                                  @{choice.user.username} · {choice.agent.agent_name}
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
                {copy.mapped(mappedMessages.length, rawMessages.length)}
              </span>
            </div>

            <div className="mt-4 max-h-[620px] space-y-3 overflow-y-auto pr-1">
              {rawMessages.length > 0 ? (
                rawMessages.slice(0, 80).map((message, index) => {
                  const mappedAgentId = senderMap[message.sender_id];
                  const isMe =
                    session.agent_id !== undefined &&
                    Number(mappedAgentId) === session.agent_id;
                  return (
                    <article
                      className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3"
                      key={`${message.sender_id}-${index}`}
                    >
                      <div className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
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
                        <span
                          className={`rounded-full px-2 py-0.5 font-medium ${
                            isMe
                              ? "bg-indigo-100 text-indigo-700"
                              : "bg-gray-200 text-gray-600"
                          }`}
                        >
                          {isMe ? copy.speakerMe : copy.speakerOthers}
                        </span>
                        {message.timestamp ? <span>{message.timestamp}</span> : null}
                      </div>
                      <p className="mt-2 text-sm leading-6 text-gray-800">
                        {message.content}
                      </p>
                    </article>
                  );
                })
              ) : (
                <div className="rounded-xl border border-dashed border-gray-200 px-4 py-12 text-center text-sm text-gray-400">
                  {copy.noParsed}
                </div>
              )}
            </div>

            {rawMessages.length > 80 ? (
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

export default ChatImportView;
