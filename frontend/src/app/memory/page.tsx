"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { MemoryUploadResponse, apiRequest } from "@/lib/api";
import { LoopSession, loadSession } from "@/lib/session";

export default function MemoryPage() {
  const router = useRouter();
  const [session, setSession] = useState<LoopSession | null>(null);
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [isUploading, setIsUploading] = useState(false);

  useEffect(() => {
    const storedSession = loadSession();
    if (!storedSession) {
      router.replace("/");
      return;
    }

    setSession(storedSession);
  }, [router]);

  useEffect(() => {
    if (!toast) {
      return;
    }

    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

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
        `/api/users/${session.user_id}/memory/upload`,
        {
          method: "POST",
          body: JSON.stringify({ content: content.trim() }),
        },
      );
      setContent("");
      setToast(`记忆上传成功，已写入 ${result.chunks_added} 个记忆片段。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to upload memory.");
    } finally {
      setIsUploading(false);
    }
  }

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">Loading memory vault...</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6">
        <header className="mb-6">
          <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
            Memory Vault
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950">
            记忆金库
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-gray-500">
            请在这里粘贴你的历史日记、微信聊天记录片段或生活感悟。系统会将其切碎并转化为
            Agent 的潜意识记忆。
          </p>
          <p className="mt-2 text-sm text-gray-400">
            Signed in as{" "}
            <span className="font-medium text-gray-600">{session.username}</span>
          </p>
        </header>

        {error ? (
          <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 shadow-sm">
            {error}
          </div>
        ) : null}

        <form
          className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"
          onSubmit={uploadMemory}
        >
          <label className="block">
            <span className="text-sm font-medium text-gray-700">Memory content</span>
            <textarea
              className="mt-3 min-h-[360px] w-full resize-y rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100"
              disabled={isUploading}
              onChange={(event) => setContent(event.target.value)}
              placeholder="粘贴日记、聊天记录片段、重要经历或生活感悟..."
              required
              value={content}
            />
          </label>

          <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-gray-400">
              {content.trim().length.toLocaleString()} characters ready
            </p>
            <button
              className="rounded-full bg-gray-950 px-5 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isUploading || !content.trim()}
              type="submit"
            >
              {isUploading ? "Uploading..." : "上传记忆"}
            </button>
          </div>
        </form>
      </div>

      {toast ? (
        <div className="fixed bottom-6 left-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-700 shadow-lg">
          {toast}
        </div>
      ) : null}
    </main>
  );
}
