"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import PROBE_QUESTIONS from "@/data/questionnaires.json";
import { BranchSelector } from "@/components/BranchSelector";
import { useLanguage } from "@/components/LanguageContext";
import {
  AgentSessionChoice,
  GlobalSystemSettings,
  User,
  apiRequest,
  formatAgentChoiceLabel,
} from "@/lib/api";
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
const DEFAULT_BRANCH_ID = "main";

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
  const [agentChoices, setAgentChoices] = useState<AgentSessionChoice[]>([]);
  const [targetAgentId, setTargetAgentId] = useState<number | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [branches, setBranches] = useState<string[]>([DEFAULT_BRANCH_ID]);
  const [currentBranch, setCurrentBranch] = useState(DEFAULT_BRANCH_ID);
  const [systemSettings, setSystemSettings] =
    useState<GlobalSystemSettings | null>(null);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isComplete = useMemo(
    () =>
      questionnaireItems.every(
        (question) => answers[questionKey(question)] !== undefined,
      ),
    [answers],
  );
  const canSwitchBranches =
    currentUser?.is_admin === true ||
    systemSettings?.allow_user_branch_switch === true;

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

        let initialBranch = DEFAULT_BRANCH_ID;
        let allowInitialBranchSwitch = false;
        try {
          const settings = await apiRequest<GlobalSystemSettings>(
            "/api/simulation/settings",
          );
          initialBranch = settings.global_active_branch?.trim() || DEFAULT_BRANCH_ID;
          allowInitialBranchSwitch = settings.allow_user_branch_switch;
          setSystemSettings(settings);
        } catch {
          setSystemSettings({
            allow_user_branch_switch: false,
            global_active_branch: DEFAULT_BRANCH_ID,
          });
        }

        setCurrentBranch(initialBranch);
        setBranches((currentBranches) =>
          normalizeBranches([initialBranch, ...currentBranches]),
        );
        if (user.is_admin || allowInitialBranchSwitch) {
          void loadBranches(initialBranch);
        }
        if (user.is_admin) {
          void loadAgentChoices();
        }
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

  async function loadBranches(preferredBranch = currentBranch) {
    setIsLoadingBranches(true);
    try {
      const result = await apiRequest<unknown>("/api/simulation/branches");
      const nextBranches = normalizeBranches([
        preferredBranch,
        ...normalizeBranches(result),
      ]);
      setBranches(nextBranches);
      setCurrentBranch((branchId) => {
        if (preferredBranch && nextBranches.includes(preferredBranch)) {
          return preferredBranch;
        }
        return nextBranches.includes(branchId) ? branchId : DEFAULT_BRANCH_ID;
      });
    } catch {
      const fallbackBranch = preferredBranch || DEFAULT_BRANCH_ID;
      setBranches(normalizeBranches([fallbackBranch]));
      setCurrentBranch(fallbackBranch);
    } finally {
      setIsLoadingBranches(false);
    }
  }

  async function loadAgentChoices() {
    setIsLoadingAgents(true);
    try {
      const choices = await apiRequest<AgentSessionChoice[]>("/api/users/agent-choices");
      setAgentChoices(choices);
      setTargetAgentId((currentTargetAgentId) => {
        if (
          currentTargetAgentId &&
          choices.some((choice) => choice.agent.id === currentTargetAgentId)
        ) {
          return currentTargetAgentId;
        }
        return choices[0]?.agent.id ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.loadAgentsFailed);
    } finally {
      setIsLoadingAgents(false);
    }
  }

  async function submitProbes(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !isComplete ||
      isSubmitting ||
      (currentUser?.is_admin && !targetAgentId)
    ) {
      return;
    }

    setError("");
    setMessage("");
    setIsSubmitting(true);

    try {
      const payload = {
        agent_id: currentUser?.is_admin ? targetAgentId : undefined,
        branch_id: currentBranch,
        responses: questionnaireItems.map((question) => ({
          probe_set: question.set,
          probe_id: question.id,
          answer: {
            value: answers[questionKey(question)],
            scale: question.type,
          },
        })),
      };
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
          {canSwitchBranches ? (
            <BranchSelector
              branches={branches}
              disabled={isSubmitting}
              isLoading={isLoadingBranches}
              label={t.common.branchSelector}
              loadingLabel={t.common.refreshing}
              onChange={setCurrentBranch}
              onRefresh={() => loadBranches(currentBranch)}
              refreshLabel={t.common.refreshBranches}
              value={currentBranch}
            />
          ) : null}

          {currentUser?.is_admin ? (
            <label className="block">
              <span className="text-sm font-semibold text-slate-800">
                {copy.agentPicker}
              </span>
              <select
                className="mt-2 w-full rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none focus:border-slate-500"
                disabled={isSubmitting || isLoadingAgents}
                onChange={(event) => setTargetAgentId(Number(event.target.value) || null)}
                value={targetAgentId ?? ""}
              >
                {agentChoices.length === 0 ? (
                  <option value="">{t.common.chooseAgent}</option>
                ) : null}
                {agentChoices.map((choice) => (
                  <option key={choice.agent.id} value={choice.agent.id}>
                    {formatAgentChoiceLabel(choice)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

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
            disabled={
              !isComplete ||
              isSubmitting ||
              (currentUser?.is_admin && !targetAgentId)
            }
          >
            {isSubmitting ? copy.submitting : copy.submit}
          </button>
        </form>
      </section>
    </main>
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
