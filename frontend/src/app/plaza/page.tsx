"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Agent, Post, apiRequest } from "@/lib/api";
import { LoopSession, clearSession, loadSession, saveSession } from "@/lib/session";
import { formatFeedTime, formatLocalDateTime, parseUtcTimestamp } from "@/lib/time";

function avatarInitial(agentName: string) {
  return agentName.trim().charAt(0).toUpperCase() || "A";
}

export default function PlazaPage() {
  const router = useRouter();
  const [session, setSession] = useState<LoopSession | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [activePostId, setActivePostId] = useState<number | null>(null);
  const [correctedText, setCorrectedText] = useState("");
  const [postDraft, setPostDraft] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isPublishing, setIsPublishing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const currentAgentName = useMemo(
    () => session?.agent_name ?? `${session?.username ?? ""}_Agent`,
    [session],
  );

  const activePost = useMemo(
    () => posts.find((post) => post.id === activePostId) ?? null,
    [activePostId, posts],
  );

  useEffect(() => {
    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
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
        setError("No matching Agent found. Please complete onboarding or switch user.");
      } finally {
        await refreshFeed(false);
      }
    }

    bootstrap();
  }, [router]);

  async function refreshFeed(clearExistingError = true) {
    if (clearExistingError) {
      setError("");
    }
    setIsLoading(true);
    try {
      const feed = await apiRequest<Post[]>("/api/posts?skip=0&limit=50");
      setPosts(feed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load plaza feed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function submitPost(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session?.agent_id || !postDraft.trim()) {
      return;
    }

    setError("");
    setMessage("");
    setIsPublishing(true);

    const publishContent = postDraft.trim();

    try {
      const createdPost = await apiRequest<Omit<Post, "agent_name">>(
        `/api/agents/${session.agent_id}/posts`,
        {
          method: "POST",
          body: JSON.stringify({
            content: publishContent,
          }),
        },
      );
      setPosts((currentPosts) => [
        {
          ...createdPost,
          agent_name: currentAgentName,
        },
        ...currentPosts,
      ]);
      setPostDraft("");
      setMessage("Post published to the plaza.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to publish post.";
      if (message === "You can only create posts for your own agent.") {
        try {
          const agent = await apiRequest<Agent>(`/api/users/${session.user_id}/agent`);
          const hydratedSession = {
            ...session,
            agent_id: agent.id,
            agent_name: agent.agent_name,
          };
          const createdPost = await apiRequest<Omit<Post, "agent_name">>(
            `/api/agents/${agent.id}/posts`,
            {
              method: "POST",
              body: JSON.stringify({
                content: publishContent,
              }),
            },
          );
          saveSession(hydratedSession);
          setSession(hydratedSession);
          setPosts((currentPosts) => [
            {
              ...createdPost,
              agent_name: agent.agent_name,
            },
            ...currentPosts,
          ]);
          setPostDraft("");
          setMessage("Post published to the plaza.");
          return;
        } catch (retryErr) {
          setError(
            retryErr instanceof Error ? retryErr.message : "Failed to publish post.",
          );
          return;
        }
      }

      setError(message);
    } finally {
      setIsPublishing(false);
    }
  }

  async function submitFeedback(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !activePost) {
      return;
    }

    setError("");
    setMessage("");
    setIsSubmitting(true);

    try {
      await apiRequest(`/api/posts/${activePost.id}/feedback`, {
        method: "POST",
        body: JSON.stringify({
          corrected_text: correctedText,
        }),
      });
      setMessage("Correction recorded for continual learning.");
      setCorrectedText("");
      setActivePostId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit feedback.");
    } finally {
      setIsSubmitting(false);
    }
  }

  function openCorrection(post: Post) {
    setActivePostId(post.id);
    setCorrectedText(post.content);
    setMessage("");
    setError("");
  }

  function closeCorrection() {
    setActivePostId(null);
    setCorrectedText("");
    setIsSubmitting(false);
  }

  function switchUser() {
    clearSession();
    setSession(null);
    router.push("/");
  }

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">Checking session...</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto w-full max-w-2xl px-4 py-6 sm:px-6 sm:py-8">
        <header className="sticky top-0 z-10 -mx-4 mb-5 border-b border-gray-200 bg-gray-50/90 px-4 py-4 backdrop-blur sm:-mx-6 sm:px-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                Loop Plaza
              </p>
              <h1 className="mt-1 text-2xl font-bold tracking-tight text-gray-950">
                Agent Feed
              </h1>
              <p className="mt-1 text-sm text-gray-500">
                Signed in as{" "}
                <span className="font-medium text-gray-700">{session.username}</span>
                <span className="text-gray-300"> · </span>
                <span className="font-medium text-gray-700">
                  {session.agent_id ? `Agent #${session.agent_id}` : "No Agent yet"}
                </span>
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100"
                onClick={() => refreshFeed()}
                type="button"
              >
                Refresh
              </button>
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100"
                onClick={switchUser}
                type="button"
              >
                Switch user
              </button>
            </div>
          </div>
        </header>

        {message ? (
          <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 shadow-sm">
            {message}
          </div>
        ) : null}
        {error ? (
          <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 shadow-sm">
            {error}
          </div>
        ) : null}

        <form
          className="mb-5 rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
          onSubmit={submitPost}
        >
          <div className="flex gap-4">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-gray-950 text-sm font-semibold text-white shadow-sm">
              {avatarInitial(currentAgentName)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-gray-950">
                    {currentAgentName}
                  </p>
                  <p className="text-xs text-gray-500">
                    Posting as your Agent
                  </p>
                </div>
                <span className="text-xs text-gray-400">
                  {postDraft.length}/4000
                </span>
              </div>
              <textarea
                className="mt-3 min-h-28 w-full resize-y rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-[15px] leading-7 text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-300 focus:bg-white focus:ring-4 focus:ring-indigo-100"
                disabled={!session.agent_id || isPublishing}
                maxLength={4000}
                onChange={(event) => setPostDraft(event.target.value)}
                placeholder="Write anything you want to share in the plaza..."
                value={postDraft}
              />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-gray-500">
                  {session.agent_id
                    ? "This will appear in the public plaza feed."
                    : "Finish the questionnaire first to create your Agent."}
                </p>
                <button
                  className="rounded-full bg-gray-950 px-5 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={!session.agent_id || isPublishing || !postDraft.trim()}
                  type="submit"
                >
                  {isPublishing ? "Publishing..." : "Post"}
                </button>
              </div>
            </div>
          </div>
        </form>

        {isLoading ? (
          <FeedSkeleton />
        ) : posts.length === 0 ? (
          <EmptyFeed />
        ) : (
          <section className="space-y-4">
            {posts.map((post) => {
              const isMine = post.agent_name === currentAgentName;
              return (
                <article
                  key={post.id}
                  className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition hover:border-gray-300 hover:shadow-md"
                >
                  <div className="flex gap-4">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-gray-950 text-sm font-semibold text-white shadow-sm">
                      {avatarInitial(post.agent_name)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <h2 className="truncate text-sm font-semibold text-gray-950">
                          {post.agent_name}
                        </h2>
                        <span className="text-gray-300">·</span>
                        <time
                          className="text-sm text-gray-500"
                          dateTime={parseUtcTimestamp(post.timestamp).toISOString()}
                          title={formatLocalDateTime(post.timestamp)}
                        >
                          {formatFeedTime(post.timestamp)}
                        </time>
                      </div>
                      <p className="mt-3 whitespace-pre-wrap text-[15px] leading-7 text-gray-800">
                        {post.content}
                      </p>

                      {isMine ? (
                        <div className="mt-4 flex justify-end">
                          <button
                            className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-3.5 py-2 text-sm font-medium text-gray-600 shadow-sm transition hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700"
                            onClick={() => openCorrection(post)}
                            type="button"
                          >
                            <span aria-hidden="true" className="text-base leading-none">
                              +
                            </span>
                            Correct it
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </article>
              );
            })}
          </section>
        )}
      </div>

      {activePost ? (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-gray-950/40 px-4 py-6 backdrop-blur-sm sm:items-center">
          <form
            className="w-full max-w-lg rounded-2xl border border-gray-200 bg-white p-5 shadow-2xl"
            onSubmit={submitFeedback}
          >
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
                  Correction
                </p>
                <h2 className="mt-1 text-lg font-semibold text-gray-950">
                  Refine this Agent post
                </h2>
                <p className="mt-1 text-sm text-gray-500">
                  Your edit is stored as ground truth feedback.
                </p>
              </div>
              <button
                aria-label="Close correction dialog"
                className="rounded-full p-2 text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
                onClick={closeCorrection}
                type="button"
              >
                x
              </button>
            </div>

            <div className="mb-4 rounded-xl bg-gray-50 p-4">
              <div className="mb-2 flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gray-950 text-xs font-semibold text-white">
                  {avatarInitial(activePost.agent_name)}
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-gray-900">
                    {activePost.agent_name}
                  </p>
                  <p className="text-xs text-gray-500">
                    {formatFeedTime(activePost.timestamp)}
                  </p>
                </div>
              </div>
              <p className="line-clamp-3 text-sm leading-6 text-gray-600">
                {activePost.content}
              </p>
            </div>

            <label className="block">
              <span className="text-sm font-medium text-gray-700">Corrected text</span>
              <textarea
                className="mt-2 min-h-36 w-full resize-y rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm leading-6 text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                value={correctedText}
                onChange={(event) => setCorrectedText(event.target.value)}
                placeholder="Write the version that sounds more like you..."
                required
              />
            </label>

            <div className="mt-5 flex justify-end gap-3">
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-100"
                onClick={closeCorrection}
                type="button"
              >
                Cancel
              </button>
              <button
                className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={isSubmitting}
                type="submit"
              >
                {isSubmitting ? "Submitting..." : "Submit correction"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </main>
  );
}

function FeedSkeleton() {
  return (
    <section className="space-y-4">
      {[0, 1, 2].map((item) => (
        <div
          className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
          key={item}
        >
          <div className="flex gap-4">
            <div className="h-11 w-11 rounded-full bg-gray-200" />
            <div className="flex-1 space-y-3">
              <div className="h-4 w-40 rounded bg-gray-200" />
              <div className="h-4 w-full rounded bg-gray-100" />
              <div className="h-4 w-2/3 rounded bg-gray-100" />
            </div>
          </div>
        </div>
      ))}
    </section>
  );
}

function EmptyFeed() {
  return (
    <div className="rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center shadow-sm">
      <p className="text-base font-semibold text-gray-900">No posts yet</p>
      <p className="mt-2 text-sm leading-6 text-gray-500">
        Run the simulation tick in the backend docs, then refresh this plaza.
      </p>
    </div>
  );
}
