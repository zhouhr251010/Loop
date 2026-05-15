"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useLanguage } from "@/components/LanguageContext";
import { apiRequest } from "@/lib/api";
import { formatFeedTime } from "@/lib/time";

type BlindTestChatLog = {
  id: number;
  user_message: string;
  agent_reply: string;
  timestamp: string;
};

type BlindTestPayload = {
  agent_id: number;
  agent_name: string;
  samples: BlindTestChatLog[];
};

type EvaluatorRelation = "朋友" | "同事" | "伴侣" | "亲属" | "其他";

const RELATION_OPTIONS: EvaluatorRelation[] = [
  "朋友",
  "同事",
  "伴侣",
  "亲属",
  "其他",
];

export default function BlindTestPage() {
  const params = useParams<{ agent_id: string }>();
  const { t } = useLanguage();
  const copy = t.evaluations;
  const agentId = Number(params.agent_id);
  const [payload, setPayload] = useState<BlindTestPayload | null>(null);
  const [relation, setRelation] = useState<EvaluatorRelation>("朋友");
  const [score, setScore] = useState(3);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSubmitted, setIsSubmitted] = useState(false);

  const sampledChatLogIds = useMemo(
    () => payload?.samples.map((sample) => sample.id) ?? [],
    [payload],
  );

  useEffect(() => {
    async function loadBlindTest() {
      if (!Number.isFinite(agentId)) {
        setError(copy.invalidLink);
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setError("");
      try {
        const result = await apiRequest<BlindTestPayload>(
          `/api/evaluations/blind-test/${agentId}`,
        );
        setPayload(result);
      } catch (err) {
        setError(err instanceof Error ? err.message : copy.loadFailed);
      } finally {
        setIsLoading(false);
      }
    }

    loadBlindTest();
  }, [agentId]);

  async function submitEvaluation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!payload || isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    setError("");
    try {
      await apiRequest(`/api/evaluations/blind-test/${payload.agent_id}/submit`, {
        method: "POST",
        body: JSON.stringify({
          evaluator_relation: relation,
          authenticity_score: score,
          qualitative_feedback: feedback,
          sampled_chat_log_ids: sampledChatLogIds,
        }),
      });
      setIsSubmitted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.submitFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-gray-50 px-4 py-8 sm:px-6">
      <div className="mx-auto w-full max-w-3xl">
        <header className="border-b border-gray-200 pb-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
            {copy.eyebrow}
          </p>
          <h1 className="mt-2 text-2xl font-bold text-gray-950">
            {copy.title}
          </h1>
          <p className="mt-3 text-sm leading-6 text-gray-600">
            {copy.intro}
          </p>
        </header>

        {isLoading ? (
          <div className="mt-10 rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500 shadow-sm">
            {copy.loading}
          </div>
        ) : null}

        {error ? (
          <div className="mt-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        ) : null}

        {!isLoading && payload ? (
          <>
            <section className="mt-6 space-y-5">
              {payload.samples.length === 0 ? (
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                  {copy.empty}
                </div>
              ) : (
                payload.samples.map((sample, index) => (
                  <article
                    className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
                    key={sample.id}
                  >
                    <div className="mb-3 flex items-center justify-between gap-3 text-xs text-gray-400">
                      <span className="font-semibold text-gray-500">
                        {copy.sample(index + 1)}
                      </span>
                      <span>{formatFeedTime(sample.timestamp)}</span>
                    </div>
                    <div className="space-y-3">
                      <ChatBubble content={sample.user_message} role="user" />
                      <ChatBubble content={sample.agent_reply} role="agent" />
                    </div>
                  </article>
                ))
              )}
            </section>

            {isSubmitted ? (
              <div className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm font-medium text-emerald-800">
                {copy.thanks}
              </div>
            ) : (
              <form
                className="mt-6 rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
                onSubmit={submitEvaluation}
              >
                <div className="grid gap-4 sm:grid-cols-[1fr_1.2fr]">
                  <label className="block">
                    <span className="text-sm font-semibold text-gray-800">
                      {copy.relationLabel}
                    </span>
                    <select
                      className="mt-2 w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                      onChange={(event) =>
                        setRelation(event.target.value as EvaluatorRelation)
                      }
                      value={relation}
                    >
                      {RELATION_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {copy.relations[option]}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="block">
                    <span className="text-sm font-semibold text-gray-800">
                      {copy.scoreLabel(score)}
                    </span>
                    <input
                      className="mt-4 w-full accent-gray-950"
                      max={5}
                      min={1}
                      onChange={(event) => setScore(Number(event.target.value))}
                      step={1}
                      type="range"
                      value={score}
                    />
                    <div className="mt-2 flex justify-between text-xs text-gray-500">
                      <span>{copy.scoreLow}</span>
                      <span>{copy.scoreHigh}</span>
                    </div>
                  </label>
                </div>

                <label className="mt-5 block">
                  <span className="text-sm font-semibold text-gray-800">
                    {copy.feedbackLabel}
                  </span>
                  <textarea
                    className="mt-2 min-h-28 w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 text-gray-900 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                    maxLength={4000}
                    onChange={(event) => setFeedback(event.target.value)}
                    value={feedback}
                  />
                </label>

                <div className="mt-5 flex justify-end">
                  <button
                    className="rounded-full bg-gray-950 px-5 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={
                      isSubmitting ||
                      payload.samples.length === 0 ||
                      sampledChatLogIds.length === 0
                    }
                    type="submit"
                  >
                    {isSubmitting ? copy.submitting : copy.submit}
                  </button>
                </div>
              </form>
            )}
          </>
        ) : null}
      </div>
    </main>
  );
}

function ChatBubble({
  content,
  role,
}: {
  content: string;
  role: "user" | "agent";
}) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[86%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm ${
          isUser
            ? "rounded-br-md bg-gray-950 text-white"
            : "rounded-bl-md border border-gray-200 bg-gray-50 text-gray-900"
        }`}
      >
        <p className="whitespace-pre-wrap">{content}</p>
      </div>
    </div>
  );
}
