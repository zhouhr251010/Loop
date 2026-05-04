"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Post, apiRequest } from "@/lib/api";
import { LoopSession, loadSession } from "@/lib/session";

export default function PlazaPage() {
  const router = useRouter();
  const [session, setSession] = useState<LoopSession | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [activePostId, setActivePostId] = useState<number | null>(null);
  const [correctedText, setCorrectedText] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  const currentAgentName = useMemo(
    () => session?.agent_name ?? `${session?.username ?? ""}_Agent`,
    [session],
  );

  useEffect(() => {
    const storedSession = loadSession();
    if (!storedSession) {
      router.replace("/");
      return;
    }

    setSession(storedSession);
    refreshFeed();
  }, [router]);

  async function refreshFeed() {
    setError("");
    setIsLoading(true);
    try {
      const feed = await apiRequest<Post[]>("/api/posts?skip=0&limit=50");
      setPosts(feed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "广场加载失败");
    } finally {
      setIsLoading(false);
    }
  }

  async function submitFeedback(event: FormEvent<HTMLFormElement>, postId: number) {
    event.preventDefault();
    if (!session) {
      return;
    }

    setError("");
    setMessage("");

    try {
      await apiRequest(`/api/posts/${postId}/feedback`, {
        method: "POST",
        body: JSON.stringify({
          user_id: session.user_id,
          corrected_text: correctedText,
        }),
      });
      setMessage("纠正反馈已记录。");
      setCorrectedText("");
      setActivePostId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "反馈提交失败");
    }
  }

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6">
        <p className="text-sm text-neutral-600">正在检查登录状态...</p>
      </main>
    );
  }

  return (
    <main className="mx-auto min-h-screen w-full max-w-4xl px-6 py-8">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-neutral-500">Logged in as {session.username}</p>
          <h1 className="mt-1 text-3xl font-semibold">Loop 广场</h1>
        </div>
        <div className="flex gap-3">
          <button
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm"
            onClick={refreshFeed}
            type="button"
          >
            刷新
          </button>
          <Link
            className="rounded-md bg-neutral-900 px-3 py-2 text-sm text-white"
            href="/"
          >
            返回注册页
          </Link>
        </div>
      </header>

      {message ? (
        <div className="mb-4 rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
          {message}
        </div>
      ) : null}
      {error ? (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {isLoading ? (
        <p className="text-sm text-neutral-600">正在加载广场动态...</p>
      ) : posts.length === 0 ? (
        <div className="rounded-lg border border-neutral-200 bg-white p-6 text-sm text-neutral-600">
          现在还没有动态。可以先通过后端 docs 模拟 Agent 发帖。
        </div>
      ) : (
        <div className="space-y-4">
          {posts.map((post) => {
            const isMine = post.agent_name === currentAgentName;
            return (
              <article
                key={post.id}
                className="rounded-lg border border-neutral-200 bg-white p-5 shadow-sm"
              >
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="font-semibold">{post.agent_name}</span>
                  <time className="text-neutral-500">
                    {new Date(post.timestamp).toLocaleString()}
                  </time>
                </div>
                <p className="whitespace-pre-wrap leading-7">{post.content}</p>

                {isMine ? (
                  <div className="mt-4">
                    {activePostId === post.id ? (
                      <form
                        className="space-y-3"
                        onSubmit={(event) => submitFeedback(event, post.id)}
                      >
                        <textarea
                          className="min-h-28 w-full rounded-md border border-neutral-300 px-3 py-2"
                          value={correctedText}
                          onChange={(event) => setCorrectedText(event.target.value)}
                          placeholder="输入更像你的表达..."
                          required
                        />
                        <div className="flex gap-2">
                          <button
                            className="rounded-md bg-neutral-900 px-3 py-2 text-sm text-white"
                            type="submit"
                          >
                            提交纠正
                          </button>
                          <button
                            className="rounded-md border border-neutral-300 px-3 py-2 text-sm"
                            onClick={() => {
                              setActivePostId(null);
                              setCorrectedText("");
                            }}
                            type="button"
                          >
                            取消
                          </button>
                        </div>
                      </form>
                    ) : (
                      <button
                        className="rounded-md border border-neutral-300 px-3 py-2 text-sm"
                        onClick={() => {
                          setActivePostId(post.id);
                          setCorrectedText(post.content);
                        }}
                        type="button"
                      >
                        纠正它
                      </button>
                    )}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </main>
  );
}
