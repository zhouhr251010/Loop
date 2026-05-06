"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Agent, ChatReply, apiRequest } from "@/lib/api";
import { LoopSession, loadSession, saveSession } from "@/lib/session";
import { formatFeedTime } from "@/lib/time";

type ChatMessage = {
  id: string;
  role: "user" | "agent";
  content: string;
  timestamp: string;
  memoryChunksUsed?: number;
};

function nowIso() {
  return new Date().toISOString();
}

export default function ChatPage() {
  const router = useRouter();
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const [session, setSession] = useState<LoopSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [isSending, setIsSending] = useState(false);

  useEffect(() => {
    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
        return;
      }

      if (storedSession.agent_id) {
        setSession(storedSession);
        return;
      }

      try {
        const agent = await apiRequest<Agent>(
          `/api/users/${storedSession.user_id}/agent`,
        );
        const hydratedSession = {
          ...storedSession,
          agent_id: agent.id,
          agent_name: agent.agent_name,
        };
        saveSession(hydratedSession);
        setSession(hydratedSession);
      } catch {
        setSession(storedSession);
        setError("No Agent found. Please complete onboarding first.");
      }
    }

    bootstrap();
  }, [router]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session?.agent_id || !input.trim()) {
      return;
    }

    const content = input.trim();
    const localTimestamp = nowIso();
    setInput("");
    setError("");
    setIsSending(true);
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
          body: JSON.stringify({ message: content }),
        },
      );

      setMessages((current) => [
        ...current,
        {
          id: `agent-${result.chat_log.id}`,
          role: "agent",
          content: result.reply,
          timestamp: result.chat_log.timestamp,
          memoryChunksUsed: result.memory_chunks_used,
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message.");
    } finally {
      setIsSending(false);
    }
  }

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">Loading nightly sync...</p>
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
            Daily private chat
          </h1>
          <p className="mt-2 text-sm leading-6 text-gray-500">
            Chat with {session.agent_name ?? "your Agent"} and store each turn as
            continual-learning memory.
          </p>
        </header>

        {error ? (
          <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        ) : null}

        <section className="flex-1 space-y-4 overflow-y-auto rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
          {messages.length === 0 ? (
            <div className="flex min-h-64 items-center justify-center text-center">
              <div>
                <p className="text-base font-semibold text-gray-900">
                  Start tonight's sync
                </p>
                <p className="mt-2 max-w-sm text-sm leading-6 text-gray-500">
                  Share what happened today, what felt unlike you, or what your
                  Agent should remember tomorrow.
                </p>
              </div>
            </div>
          ) : (
            messages.map((message) => (
              <ChatBubble key={message.id} message={message} />
            ))
          )}
          <div ref={bottomRef} />
        </section>

        <form className="mt-4 flex gap-3" onSubmit={sendMessage}>
          <input
            className="min-w-0 flex-1 rounded-full border border-gray-200 bg-white px-5 py-3 text-sm outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
            disabled={!session.agent_id || isSending}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Send a private sync message..."
            value={input}
          />
          <button
            className="rounded-full bg-gray-950 px-5 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!session.agent_id || isSending || !input.trim()}
            type="submit"
          >
            {isSending ? "Sending..." : "Send"}
          </button>
        </form>
      </div>
    </main>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
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
            Memory Vault active · {message.memoryChunksUsed} fragments
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
