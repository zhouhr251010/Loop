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

type CounterfactualSuggestion = {
  context: string;
  actual_choice: string;
  actual_result: string;
};

const EMPTY_FORM = {
  decision_context: "",
  actual_choice: "",
  actual_result: "",
  counterfactual_action: "",
  counterfactual_result: "",
};

export default function CounterfactualsPage() {
  const router = useRouter();
  const { t } = useLanguage();
  const copy = t.counterfactuals;
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [suggestions, setSuggestions] = useState<CounterfactualSuggestion[]>([]);
  const [error, setError] = useState("");
  const [suggestionError, setSuggestionError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingSuggestions, setIsLoadingSuggestions] = useState(false);
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
        void loadSuggestions();
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

  async function loadSuggestions() {
    setSuggestionError("");
    setIsLoadingSuggestions(true);
    try {
      const result = await apiRequest<CounterfactualSuggestion[]>(
        "/api/counterfactuals/suggestions",
      );
      setSuggestions(result);
    } catch (err) {
      setSuggestionError(
        err instanceof Error ? err.message : copy.suggestionsFailed,
      );
    } finally {
      setIsLoadingSuggestions(false);
    }
  }

  function updateField(
    key: keyof typeof EMPTY_FORM,
    value: string,
  ) {
    setForm((currentForm) => ({
      ...currentForm,
      [key]: value,
    }));
  }

  function applySuggestion(suggestion: CounterfactualSuggestion) {
    setForm({
      decision_context: suggestion.context,
      actual_choice: suggestion.actual_choice,
      actual_result: suggestion.actual_result,
      counterfactual_action: "",
      counterfactual_result: "",
    });
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
            actual_choice: form.actual_choice.trim() || null,
            actual_result: form.actual_result.trim() || null,
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
      <div className="mx-auto max-w-5xl">
        <header className="mb-6">
          <p className="text-sm font-medium text-slate-500">
            {copy.eyebrow}
          </p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-900">
            {copy.title}
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            {copy.description(currentUser?.username ?? copy.unknownUser)}
          </p>
        </header>

        <section className="mb-6">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">
                {copy.suggestionsTitle}
              </h2>
              <p className="mt-1 text-sm text-slate-600">
                {copy.suggestionsHelp}
              </p>
            </div>
            <button
              className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isLoadingSuggestions}
              onClick={loadSuggestions}
              type="button"
            >
              {isLoadingSuggestions ? copy.suggestionsLoading : copy.refreshSuggestions}
            </button>
          </div>

          <div className="flex gap-4 overflow-x-auto pb-2">
            {isLoadingSuggestions ? (
              Array.from({ length: 3 }).map((_, index) => (
                <div
                  className="h-44 min-w-[280px] animate-pulse rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
                  key={index}
                >
                  <div className="h-3 w-24 rounded bg-slate-200" />
                  <div className="mt-4 h-3 w-full rounded bg-slate-200" />
                  <div className="mt-2 h-3 w-5/6 rounded bg-slate-200" />
                  <div className="mt-6 h-3 w-2/3 rounded bg-slate-200" />
                </div>
              ))
            ) : suggestions.length > 0 ? (
              suggestions.map((suggestion, index) => (
                <button
                  className="min-w-[280px] max-w-xs rounded-lg border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-slate-400 hover:shadow-md"
                  key={`${suggestion.context}-${index}`}
                  onClick={() => applySuggestion(suggestion)}
                  type="button"
                >
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    {copy.suggestionCard(index + 1)}
                  </p>
                  <p className="mt-2 line-clamp-3 text-sm font-semibold leading-6 text-slate-900">
                    {suggestion.context}
                  </p>
                  <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">
                    {copy.actualChoicePrefix} {suggestion.actual_choice}
                  </p>
                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">
                    {copy.actualResultPrefix} {suggestion.actual_result}
                  </p>
                </button>
              ))
            ) : (
              <div className="w-full rounded-lg border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-500">
                {suggestionError || copy.noSuggestions}
              </div>
            )}
          </div>
        </section>

        <form
          className="space-y-5 rounded-2xl bg-white p-6 shadow-sm"
          onSubmit={submitCounterfactual}
        >
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

          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="text-sm font-semibold text-slate-800">
                {copy.actualChoiceLabel}
              </span>
              <textarea
                className="mt-2 min-h-24 w-full rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none focus:border-slate-500"
                value={form.actual_choice}
                onChange={(event) =>
                  updateField("actual_choice", event.target.value)
                }
                placeholder={copy.actualChoicePlaceholder}
              />
            </label>

            <label className="block">
              <span className="text-sm font-semibold text-slate-800">
                {copy.actualResultLabel}
              </span>
              <textarea
                className="mt-2 min-h-24 w-full rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none focus:border-slate-500"
                value={form.actual_result}
                onChange={(event) =>
                  updateField("actual_result", event.target.value)
                }
                placeholder={copy.actualResultPlaceholder}
              />
            </label>
          </div>

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
      </div>
    </main>
  );
}
