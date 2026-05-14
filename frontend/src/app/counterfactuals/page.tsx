"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useLanguage } from "@/components/LanguageContext";
import { User, apiRequest } from "@/lib/api";
import { clearSession, loadSession } from "@/lib/session";

type CounterfactualSubmitResponse = {
  saved: boolean;
  core_memory_updated: boolean;
};

const EMPTY_FORM = {
  decision_context: "",
  counterfactual_action: "",
  counterfactual_result: "",
};

export default function CounterfactualsPage() {
  const router = useRouter();
  const { t } = useLanguage();
  const copy = t.counterfactuals;
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isComplete =
    form.decision_context.trim() &&
    form.counterfactual_action.trim() &&
    form.counterfactual_result.trim();

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

  function updateField(
    key: keyof typeof EMPTY_FORM,
    value: string,
  ) {
    setForm((currentForm) => ({
      ...currentForm,
      [key]: value,
    }));
  }

  async function submitCounterfactual(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isComplete || isSubmitting) {
      return;
    }

    setError("");
    setIsSubmitting(true);

    try {
      const result = await apiRequest<CounterfactualSubmitResponse>(
        "/api/counterfactuals/submit",
        {
          method: "POST",
          body: JSON.stringify({
            decision_context: form.decision_context.trim(),
            counterfactual_action: form.counterfactual_action.trim(),
            counterfactual_result: form.counterfactual_result.trim(),
          }),
        },
      );
      if (result.saved && result.core_memory_updated) {
        setForm(EMPTY_FORM);
        window.alert(copy.savedAlert);
      }
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
          <p className="text-sm font-medium text-slate-500">
            {copy.eyebrow}
          </p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-900">
            {copy.title}
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            {copy.description(currentUser?.username ?? copy.unknownUser)}
          </p>
        </div>

        <form className="space-y-5" onSubmit={submitCounterfactual}>
          <label className="block">
            <span className="text-sm font-semibold text-slate-800">
              {copy.decisionLabel}
            </span>
            <textarea
              className="mt-2 min-h-28 w-full rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none focus:border-slate-500"
              value={form.decision_context}
              onChange={(event) =>
                updateField("decision_context", event.target.value)
              }
              placeholder={copy.decisionPlaceholder}
            />
          </label>

          <label className="block">
            <span className="text-sm font-semibold text-slate-800">
              {copy.actionLabel}
            </span>
            <textarea
              className="mt-2 min-h-28 w-full rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none focus:border-slate-500"
              value={form.counterfactual_action}
              onChange={(event) =>
                updateField("counterfactual_action", event.target.value)
              }
              placeholder={copy.actionPlaceholder}
            />
          </label>

          <label className="block">
            <span className="text-sm font-semibold text-slate-800">
              {copy.resultLabel}
            </span>
            <textarea
              className="mt-2 min-h-32 w-full rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none focus:border-slate-500"
              value={form.counterfactual_result}
              onChange={(event) =>
                updateField("counterfactual_result", event.target.value)
              }
              placeholder={copy.resultPlaceholder}
            />
          </label>

          {error ? (
            <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
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
