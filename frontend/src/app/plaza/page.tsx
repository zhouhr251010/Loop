"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BranchSelector } from "@/components/BranchSelector";
import { useLanguage } from "@/components/LanguageContext";
import { Agent, Post, apiRequest } from "@/lib/api";
import { LoopSession, clearSession, loadSession, saveSession } from "@/lib/session";
import { formatFeedTime, formatLocalDateTime, parseUtcTimestamp } from "@/lib/time";

const DEFAULT_BRANCH_ID = "main";
const BRANCHES_ENDPOINT = "/api/simulation/branches";
const PLAZA_PAGE_SIZE = 20;

const PLAZA_FEED_ENDPOINT = (branchId: string, skip = 0, limit = PLAZA_PAGE_SIZE) =>
  `/api/plaza/events?branch_id=${encodeURIComponent(branchId)}&skip=${skip}&limit=${limit}`;

type ProbeStatus = {
  needs_update: boolean;
  last_submitted: string | null;
};

function avatarInitial(agentName: string) {
  return agentName.trim().charAt(0).toUpperCase() || "A";
}

export default function PlazaPage() {
  const router = useRouter();
  const { t } = useLanguage();
  const copy = t.plaza;
  const [session, setSession] = useState<LoopSession | null>(null);
  const [branches, setBranches] = useState<string[]>([DEFAULT_BRANCH_ID]);
  const [currentBranch, setCurrentBranch] = useState(DEFAULT_BRANCH_ID);
  const [posts, setPosts] = useState<Post[]>([]);
  const [activePostId, setActivePostId] = useState<number | null>(null);
  const [correctedText, setCorrectedText] = useState("");
  const [postDraft, setPostDraft] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMorePosts, setHasMorePosts] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [probeStatus, setProbeStatus] = useState<ProbeStatus | null>(null);

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
        const agent = await apiRequest<Agent>("/api/users/me/agent");
        const hydratedSession = {
          ...storedSession,
          agent_id: agent.id,
          agent_name: agent.agent_name,
        };
        saveSession(hydratedSession);
        setSession(hydratedSession);
        void loadBranches();
        void loadProbeStatus();
      } catch {
        setSession(storedSession);
        void loadBranches();
        void loadProbeStatus();
        setError(copy.noMatchingAgent);
      } finally {
        await refreshFeed(DEFAULT_BRANCH_ID, false);
      }
    }

    bootstrap();
  }, [router]);

  async function loadProbeStatus() {
    try {
      const status = await apiRequest<ProbeStatus>("/api/probes/status");
      setProbeStatus(status);
    } catch {
      setProbeStatus(null);
    }
  }

  async function loadBranches() {
    setIsLoadingBranches(true);
    try {
      const result = await apiRequest<unknown>(BRANCHES_ENDPOINT);
      const branchList = normalizeBranches(result);
      setBranches(branchList);
      if (!branchList.includes(currentBranch)) {
        setCurrentBranch(DEFAULT_BRANCH_ID);
      }
    } catch (err) {
      setBranches([DEFAULT_BRANCH_ID]);
      setCurrentBranch(DEFAULT_BRANCH_ID);
      setError(
        err instanceof Error
          ? t.common.branchUnavailable(err.message)
          : t.common.branchUnavailable(),
      );
    } finally {
      setIsLoadingBranches(false);
    }
  }

  async function refreshFeed(
    branchId = currentBranch,
    clearExistingError = true,
  ) {
    const normalizedBranchId = branchId.trim() || DEFAULT_BRANCH_ID;
    if (clearExistingError) {
      setError("");
    }
    setIsLoading(true);
    try {
      const feed = await apiRequest<Post[]>(PLAZA_FEED_ENDPOINT(normalizedBranchId));
      setPosts(feed);
      setHasMorePosts(feed.length === PLAZA_PAGE_SIZE);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.loadFeedFailed);
      setHasMorePosts(false);
    } finally {
      setIsLoading(false);
    }
  }

  async function loadMorePosts() {
    if (isLoadingMore || isLoading || !hasMorePosts) {
      return;
    }

    setError("");
    setIsLoadingMore(true);
    try {
      const nextPage = await apiRequest<Post[]>(
        PLAZA_FEED_ENDPOINT(currentBranch, posts.length),
      );
      setPosts((currentPosts) => {
        const existingIds = new Set(currentPosts.map((post) => post.id));
        const uniqueNextPosts = nextPage.filter((post) => !existingIds.has(post.id));
        return [...currentPosts, ...uniqueNextPosts];
      });
      setHasMorePosts(nextPage.length === PLAZA_PAGE_SIZE);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.loadFeedFailed);
    } finally {
      setIsLoadingMore(false);
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
        "/api/agents/me/posts",
        {
          method: "POST",
          body: JSON.stringify({
            content: publishContent,
            branch_id: currentBranch,
          }),
        },
      );
      setPosts((currentPosts) => [
        {
          ...createdPost,
          agent_name: currentAgentName,
          branch_id: currentBranch,
        },
        ...currentPosts,
      ]);
      setPostDraft("");
      setMessage(copy.published);
    } catch (err) {
      const message = err instanceof Error ? err.message : copy.publishFailed;
      if (message === "You can only create posts for your own agent.") {
        try {
          const agent = await apiRequest<Agent>("/api/users/me/agent");
          const hydratedSession = {
            ...session,
            agent_id: agent.id,
            agent_name: agent.agent_name,
          };
          const createdPost = await apiRequest<Omit<Post, "agent_name">>(
            "/api/agents/me/posts",
            {
              method: "POST",
              body: JSON.stringify({
                content: publishContent,
                branch_id: currentBranch,
              }),
            },
          );
          saveSession(hydratedSession);
          setSession(hydratedSession);
          setPosts((currentPosts) => [
            {
              ...createdPost,
              agent_name: agent.agent_name,
              branch_id: currentBranch,
            },
            ...currentPosts,
          ]);
          setPostDraft("");
          setMessage(copy.published);
          return;
        } catch (retryErr) {
          setError(
            retryErr instanceof Error ? retryErr.message : copy.publishFailed,
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
          branch_id: currentBranch,
        }),
      });
      setMessage(copy.correctionSaved);
      setPosts((currentPosts) =>
        currentPosts.map((post) =>
          post.id === activePost.id
            ? { ...post, content: correctedText.trim(), is_corrected: true }
            : post,
        ),
      );
      setCorrectedText("");
      setActivePostId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.feedbackFailed);
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

  function updateCurrentBranch(branchId: string) {
    const nextBranch = branchId.trim() || DEFAULT_BRANCH_ID;
    setCurrentBranch(nextBranch);
    setMessage("");
    setError("");
    setPosts([]);
    setHasMorePosts(false);
    void refreshFeed(nextBranch);
  }

  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">{copy.checkingSession}</p>
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
                {copy.title}
              </h1>
              <p className="mt-1 text-sm text-gray-500">
                {t.common.signedInAs}{" "}
                <span className="font-medium text-gray-700">{session.username}</span>
                <span className="text-gray-300"> · </span>
                <span className="font-medium text-gray-700">
                  {session.agent_name ?? t.common.noAgentYet}
                </span>
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100"
                onClick={() => refreshFeed(currentBranch)}
                type="button"
              >
                {copy.refresh}
              </button>
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100"
                onClick={switchUser}
                type="button"
              >
                {copy.switchUser}
              </button>
            </div>
          </div>
          <div className="mt-4 flex flex-col gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p
                className={`text-xs font-semibold uppercase tracking-wide ${
                  currentBranch === DEFAULT_BRANCH_ID
                    ? "text-gray-500"
                    : "text-purple-600"
                }`}
              >
                {copy.globalWorldLine}
              </p>
              <p className="mt-1 text-sm font-medium text-gray-700">
                {copy.currentUniverse(currentBranch)}
              </p>
            </div>
            <BranchSelector
              branches={branches}
              disabled={isLoading}
              isLoading={isLoadingBranches}
              label={t.common.branchSelector}
              loadingLabel={t.common.loading}
              onChange={updateCurrentBranch}
              onRefresh={loadBranches}
              refreshLabel={t.common.refreshBranches}
              value={currentBranch}
            />
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
        {probeStatus?.needs_update ? (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 shadow-sm">
            <span className="font-medium">{copy.probeReminderTitle}</span>{" "}
            {copy.probeReminderBody}{" "}
            <Link className="font-semibold underline" href="/probes">
              {copy.probeReminderLink}
            </Link>
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
                    {copy.postingAs(currentBranch)}
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
                placeholder={copy.placeholder}
                value={postDraft}
              />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-gray-500">
                  {session.agent_id
                    ? copy.publicHint
                    : copy.finishQuestionnaire}
                </p>
                <button
                  className="rounded-full bg-gray-950 px-5 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={!session.agent_id || isPublishing || !postDraft.trim()}
                  type="submit"
                >
                  {isPublishing ? copy.publishing : copy.post}
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
                            {copy.correctIt}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </article>
              );
            })}
            {hasMorePosts ? (
              <div className="flex justify-center pt-2">
                <button
                  className="rounded-full border border-gray-200 bg-white px-5 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isLoadingMore}
                  onClick={loadMorePosts}
                  type="button"
                >
                  {isLoadingMore ? copy.loadingMore : copy.loadMore}
                </button>
              </div>
            ) : null}
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
                  {copy.correction}
                </p>
                <h2 className="mt-1 text-lg font-semibold text-gray-950">
                  {copy.refinePost}
                </h2>
                <p className="mt-1 text-sm text-gray-500">
                  {copy.correctionHelp}
                </p>
              </div>
              <button
                aria-label={copy.closeCorrection}
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
              <span className="text-sm font-medium text-gray-700">
                {copy.correctedText}
              </span>
              <textarea
                className="mt-2 min-h-36 w-full resize-y rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm leading-6 text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                value={correctedText}
                onChange={(event) => setCorrectedText(event.target.value)}
                placeholder={copy.correctedPlaceholder}
                required
              />
            </label>

            <div className="mt-5 flex justify-end gap-3">
              <button
                className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-100"
                onClick={closeCorrection}
                type="button"
              >
                {t.common.cancel}
              </button>
              <button
                className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={isSubmitting}
                type="submit"
              >
                {isSubmitting ? t.common.submitting : copy.submitCorrection}
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
  const { t } = useLanguage();

  return (
    <div className="rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center shadow-sm">
      <p className="text-base font-semibold text-gray-900">{t.plaza.noPosts}</p>
      <p className="mt-2 text-sm leading-6 text-gray-500">
        {t.plaza.emptyHelp}
      </p>
    </div>
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
