"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import PROBE_QUESTIONS from "@/data/questionnaires.json";
import { useLanguage } from "@/components/LanguageContext";
import { User, apiRequest } from "@/lib/api";
import { clearSession, loadSession } from "@/lib/session";

type ProbeQuestion = {
  set: "IPIP120" | "PVQ21";
  id: string;
  text: string;
  text_en?: string;
  text_zh?: string;
  type: "likert5" | "likert6";
};

type ProbeSubmitResponse = {
  submitted: number;
};

const questionnaireItems = PROBE_QUESTIONS as ProbeQuestion[];

function questionKey(question: ProbeQuestion) {
  return `${question.set}:${question.id}`;
}

function scaleFor(type: ProbeQuestion["type"]) {
  const max = type === "likert5" ? 5 : 6;
  return Array.from({ length: max }, (_, index) => index + 1);
}

export default function ProbesPage() {
  const router = useRouter();
  const { language, t } = useLanguage();
  const copy = t.probes;
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isComplete = useMemo(
    () =>
      questionnaireItems.every(
        (question) => answers[questionKey(question)] !== undefined,
      ),
    [answers],
  );

  useEffect(() => {
    async function bootstrap() {
      const session = loadSession();
      if (!session) {
        router.replace("/");
        return;
      }

      try {
        const user = await apiRequest<User>("/api/users/me");
        setCurrentUser(user);
      } catch (err) {
        clearSession();
        setError(err instanceof Error ? err.message : copy.sessionExpired);
        router.replace("/");
      } finally {
        setIsLoading(false);
      }
    }

    bootstrap();
  }, [router]);

  function updateAnswer(question: ProbeQuestion, value: number) {
    setAnswers((currentAnswers) => ({
      ...currentAnswers,
      [questionKey(question)]: value,
    }));
  }

  function questionText(question: ProbeQuestion) {
    return language === "zh"
      ? question.text_zh ?? question.text
      : question.text_en ?? question.text;
  }

  async function submitProbes(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isComplete || isSubmitting) {
      return;
    }

    setError("");
    setMessage("");
    setIsSubmitting(true);

    try {
      const payload = questionnaireItems.map((question) => ({
        probe_set: question.set,
        probe_id: question.id,
        answer: {
          value: answers[questionKey(question)],
          scale: question.type,
        },
      }));
      const result = await apiRequest<ProbeSubmitResponse>("/api/probes/submit", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setMessage(copy.submitted(result.submitted));
      window.setTimeout(() => router.push("/plaza"), 800);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.submitFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

  if (isLoading) {
    return (
      <main className="min-h-screen bg-slate-100 px-4 py-10">
        <section className="mx-auto max-w-2xl rounded-2xl bg-white p-6 shadow-sm">
          <p className="text-sm text-slate-600">{copy.loading}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-100 px-4 py-10">
      <section className="mx-auto max-w-2xl rounded-2xl bg-white p-6 shadow-sm">
        <div className="mb-6">
          <p className="text-sm font-medium text-slate-500">{copy.eyebrow}</p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-900">
            {copy.title}
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            {copy.description(currentUser?.username ?? copy.unknownUser)}
          </p>
        </div>

        <form className="space-y-6" onSubmit={submitProbes}>
          {questionnaireItems.map((question) => (
            <fieldset
              key={questionKey(question)}
              className="rounded-xl border border-slate-200 p-4"
            >
              <legend className="px-1 text-sm font-semibold text-slate-800">
                {question.set} {question.id}
              </legend>
              <p className="mt-2 text-base text-slate-900">
                {questionText(question)}
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                {scaleFor(question.type).map((value) => (
                  <label
                    key={value}
                    className="flex cursor-pointer items-center gap-2 rounded-full border border-slate-200 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
                  >
                    <input
                      className="h-4 w-4"
                      type="radio"
                      name={questionKey(question)}
                      value={value}
                      checked={answers[questionKey(question)] === value}
                      onChange={() => updateAnswer(question, value)}
                    />
                    {value}
                  </label>
                ))}
              </div>
            </fieldset>
          ))}

          {error ? (
            <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </p>
          ) : null}
          {message ? (
            <p className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
              {message}
            </p>
          ) : null}

          <button
            className="w-full rounded-xl bg-slate-900 px-4 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-400"
            type="submit"
            disabled={!isComplete || isSubmitting}
          >
            {isSubmitting ? copy.submitting : copy.submit}
          </button>
        </form>
      </section>
    </main>
  );
}
