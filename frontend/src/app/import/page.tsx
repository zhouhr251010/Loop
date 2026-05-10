"use client";

import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
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

function parseChatFilePayload(payload: unknown): ParseResult {
  if (!Array.isArray(payload)) {
    throw new Error("JSON root must be an array of chat messages.");
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
        setError("No Agent found. Please complete onboarding before importing chat.");
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
        `@${session.username} · ${session.agent_name ?? "current Agent"}`,
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
      const parsed = parseChatFilePayload(JSON.parse(text));
      if (parsed.messages.length === 0) {
        throw new Error("No valid chat messages were found in this file.");
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
      setError(err instanceof Error ? err.message : "Failed to parse JSON file.");
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
      setError(err instanceof Error ? err.message : "Failed to import group chat.");
    } finally {
      setIsImporting(false);
    }
  }

  async function loadAgentChoices() {
    const trimmedAdminKey = adminKey.trim();
    if (!trimmedAdminKey) {
      setError("Admin key is required to load the Agent dropdown.");
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
      setError(err instanceof Error ? err.message : "Failed to load agents.");
    } finally {
      setIsLoadingAgents(false);
    }
  }

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">Loading chat import...</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
        <header className="mb-6 border-b border-gray-200 pb-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
            Chat Import
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950">
            群聊历史导入
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-500">
            将外部群聊 sender_id 对齐到 Loop Agent，并从当前 Agent 的第一人称视角写入初始化记忆。
          </p>
          <p className="mt-2 text-sm text-gray-400">
            Importing as{" "}
            <span className="font-medium text-gray-600">@{session.username}</span>
            {" · "}
            <span className="font-medium text-gray-600">
              {session.agent_name ?? "No Agent yet"}
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
            Imported {result.records_received} messages into{" "}
            {session.agent_name ?? "current Agent"}: {result.me_messages} me,{" "}
            {result.others_messages} others, {result.chunks_added} memory chunk(s).
          </div>
        ) : null}

        <div className="grid gap-5 lg:grid-cols-[minmax(0,0.82fr)_minmax(380px,1fr)]">
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold text-gray-950">
                  JSON 文件
                </h2>
                <p className="mt-1 text-sm text-gray-500">
                  {fileName || "No file selected"}
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
                {isParsing ? "Parsing..." : "Choose JSON"}
              </label>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  Messages
                </p>
                <p className="mt-1 text-2xl font-bold text-gray-950">
                  {rawMessages.length}
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  Senders
                </p>
                <p className="mt-1 text-2xl font-bold text-gray-950">
                  {senderIds.length}
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  Skipped
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
                    Sender 映射
                  </h2>
                  <p className="mt-1 text-sm text-gray-500">
                    用用户名/Agent 名称选择，不需要手填 Agent ID。
                  </p>
                </div>
                <div className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-80 sm:flex-row sm:items-end">
                  <label className="block flex-1">
                    <span className="text-xs font-medium text-gray-500">
                      Admin key
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
                    {isLoadingAgents ? "Loading..." : "Load agents"}
                  </button>
                </div>
              </div>
              <div className="mt-3 overflow-hidden rounded-xl border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200 text-sm">
                  <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-4 py-3 font-semibold">sender_id</th>
                      <th className="px-4 py-3 font-semibold">Loop Agent</th>
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
                              <option value="">Choose Agent</option>
                              {session.agent_id ? (
                                <option value={session.agent_id}>
                                  @{session.username} ·{" "}
                                  {session.agent_name ?? "current Agent"}
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
                          Upload a JSON file to populate sender mappings.
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
              {isImporting ? "Importing..." : "Import into current Agent"}
            </button>
          </section>

          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-gray-950">
                清洗后预览
              </h2>
              <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-500">
                {mappedMessages.length}/{rawMessages.length} mapped
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
                            : "unmapped"}
                        </span>
                        <span
                          className={`rounded-full px-2 py-0.5 font-medium ${
                            isMe
                              ? "bg-indigo-100 text-indigo-700"
                              : "bg-gray-200 text-gray-600"
                          }`}
                        >
                          {isMe ? "speaker: me" : "speaker: others"}
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
                  No parsed messages yet.
                </div>
              )}
            </div>

            {rawMessages.length > 80 ? (
              <p className="mt-3 text-xs text-gray-400">
                Preview limited to first 80 messages.
              </p>
            ) : null}
          </section>
        </div>
      </div>
    </main>
  );
}

export default ChatImportView;
