"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Agent,
  AgentSessionChoice,
  AuthSession,
  User,
  apiRequest,
} from "@/lib/api";
import { useLanguage } from "@/components/LanguageContext";
import type { Dictionary } from "@/locales/dictionary";
import {
  LoopSession,
  clearImpersonationMarker,
  clearSession,
  loadSession,
  saveSession,
} from "@/lib/session";

const bigFiveFields = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "neuroticism",
] as const;

const schwartzFields = [
  "self_direction",
  "universalism",
  "security",
] as const;

type ScoreField = (typeof bigFiveFields | typeof schwartzFields)[number];

type AgentChoiceStatus =
  | { type: "instructions" }
  | { type: "loading" }
  | { type: "loaded"; count: number }
  | { type: "empty" }
  | { type: "apiUnreachable" }
  | { type: "requestFailed" };

type QuestionnaireResponse = {
  user: User;
  agent: Agent;
};

function initialScores(fields: readonly ScoreField[]) {
  return Object.fromEntries(fields.map((key) => [key, 50])) as Record<
    string,
    number
  >;
}

function formatAgentChoiceStatus(
  status: AgentChoiceStatus,
  copy: Dictionary["auth"],
) {
  if (status.type === "loaded") {
    return copy.agentChoiceStatus.loaded(status.count);
  }

  return copy.agentChoiceStatus[status.type];
}

export default function OnboardingPage() {
  const router = useRouter();
  const { t } = useLanguage();
  const copy = t.auth;
  const [authMode, setAuthMode] = useState<"register" | "login">("register");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [existingSession, setExistingSession] = useState<LoopSession | null>(null);
  const [mbtiType, setMbtiType] = useState("");
  const [bigFiveScores, setBigFiveScores] = useState(initialScores(bigFiveFields));
  const [schwartzValues, setSchwartzValues] = useState(
    initialScores(schwartzFields),
  );
  const [autobiography, setAutobiography] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [agentChoices, setAgentChoices] = useState<AgentSessionChoice[]>([]);
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);
  const [enteringAgentId, setEnteringAgentId] = useState<number | null>(null);
  const [agentChoiceStatus, setAgentChoiceStatus] = useState<AgentChoiceStatus>({
    type: "instructions",
  });

  useEffect(() => {
    const session = loadSession();
    if (!session) {
      return;
    }

    setExistingSession(session);
  }, [router]);

  function persistAuthSession(authSession: AuthSession, agent?: Agent): LoopSession {
    clearImpersonationMarker();
    const tokenExpiresAt = Date.now() + authSession.expires_in * 1000;
    const session: LoopSession = {
      user_id: authSession.user.id,
      username: authSession.user.username,
      is_admin: authSession.user.is_admin,
      access_token: authSession.access_token,
      token_expires_at: tokenExpiresAt,
      ...(agent
        ? {
            agent_id: agent.id,
            agent_name: agent.agent_name,
            agent_is_npc: agent.is_npc,
          }
        : {}),
    };
    saveSession(session);
    return session;
  }

  function resetLocalSessionForNewUser() {
    clearSession();
    setExistingSession(null);
    setUser(null);
    setAuthMode("register");
    setUsername("");
    setPassword("");
    setError("");
  }

  function continueExistingSession() {
    if (!existingSession) {
      return;
    }

    if (existingSession.agent_id) {
      if (existingSession.is_admin) {
        router.push("/lab");
        return;
      }
      router.push("/plaza");
      return;
    }

    if (existingSession.is_admin) {
      router.push("/lab");
      return;
    }

    setUser({
      id: existingSession.user_id,
      username: existingSession.username,
      is_admin: existingSession.is_admin,
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function handleAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);

    try {
      const authSession = await apiRequest<AuthSession>(
        authMode === "register" ? "/api/users/register" : "/api/users/login",
        {
          method: "POST",
          body: JSON.stringify({ username, password }),
        },
      );
      const storedSession = persistAuthSession(authSession);

      if (authSession.user.is_admin) {
        setExistingSession(storedSession);
        router.push("/lab");
        return;
      }

      try {
        const agent = await apiRequest<Agent>("/api/users/me/agent");
        persistAuthSession(authSession, agent);
        router.push("/plaza");
      } catch {
        if (authSession.user.is_admin) {
          setExistingSession(storedSession);
          setUser(null);
          setAuthMode("login");
          window.scrollTo({ top: 0, behavior: "smooth" });
          return;
        }
        setUser(authSession.user);
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.authFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleQuestionnaire(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) {
      return;
    }

    setError("");
    setIsSubmitting(true);

    try {
      const currentSession = loadSession();
      const result = await apiRequest<QuestionnaireResponse>(
        "/api/users/me/questionnaire",
        {
          method: "POST",
          body: JSON.stringify({
            mbti_type: mbtiType.toUpperCase(),
            big_five_scores: bigFiveScores,
            schwartz_values: schwartzValues,
            autobiography,
          }),
        },
      );
      saveSession({
        user_id: result.user.id,
        username: result.user.username,
        is_admin: result.user.is_admin,
        access_token: currentSession?.access_token ?? "",
        token_expires_at: currentSession?.token_expires_at,
        agent_id: result.agent.id,
        agent_name: result.agent.agent_name,
        agent_is_npc: result.agent.is_npc,
      });
      router.push("/plaza");
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.questionnaireFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function loadAgentChoices() {
    if (!existingSession?.is_admin) {
      setError(copy.adminOnly);
      return;
    }

    setError("");
    setAgentChoiceStatus({ type: "loading" });
    setIsLoadingAgents(true);
    try {
      const choices = await apiRequest<AgentSessionChoice[]>(
        "/api/users/agent-choices",
      );
      setAgentChoices(choices);
      setAgentChoiceStatus(
        choices.length > 0 ? { type: "loaded", count: choices.length } : { type: "empty" },
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : copy.failedToLoadAgents;
      setError(message);
      setAgentChoiceStatus(
        message.includes("Failed to fetch")
          ? { type: "apiUnreachable" }
          : { type: "requestFailed" },
      );
    } finally {
      setIsLoadingAgents(false);
    }
  }

  async function enterAgentChoice(choice: AgentSessionChoice) {
    if (!existingSession?.is_admin) {
      setError(copy.adminOnly);
      return;
    }

    setError("");
    setEnteringAgentId(choice.agent.id);
    try {
      const authSession = await apiRequest<AuthSession>(
        `/api/users/agent-choices/${choice.agent.id}/session`,
        {
          method: "POST",
        },
      );
      persistAuthSession(authSession, choice.agent);
      router.push("/plaza");
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.failedToEnterAgent);
    } finally {
      setEnteringAgentId(null);
    }
  }

  return (
    <main className="min-h-screen bg-gray-50 px-4 py-10 sm:px-6">
      <div className="mx-auto w-full max-w-3xl">
        <div className="mb-8">
          <p className="text-sm font-medium uppercase tracking-wide text-gray-500">
            {copy.step(user ? 2 : 1)}
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950">
            {user ? copy.profileTitle : copy.registrationTitle}
          </h1>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            {user
              ? copy.profileSubtitle(user.username)
              : authMode === "register"
                ? copy.registerSubtitle
                : copy.loginSubtitle}
          </p>
        </div>

        {error ? (
          <div className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        ) : null}

        {user ? (
          <div className="mb-6 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            {copy.sessionSecured}
          </div>
        ) : null}

        {!user && existingSession ? (
          <section className="mb-6 rounded-2xl border border-amber-200 bg-amber-50 p-5 shadow-sm">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium text-amber-900">
                  {copy.previousSessionTitle}
                </p>
                <p className="mt-1 text-sm text-amber-800">
                  {copy.signedInAs(existingSession.username)}
                  {existingSession.agent_name
                    ? ` · ${existingSession.agent_name}`
                    : ` · ${copy.profileIncomplete}`}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800"
                  onClick={continueExistingSession}
                  type="button"
                >
                  {copy.continue}
                </button>
                <button
                  className="rounded-full border border-amber-300 bg-white px-4 py-2 text-sm font-medium text-amber-900 transition hover:border-amber-500"
                  onClick={resetLocalSessionForNewUser}
                  type="button"
                >
                  {copy.registerNewUser}
                </button>
              </div>
            </div>
          </section>
        ) : null}

        {!user ? (
          <div className="space-y-6">
            <form
              onSubmit={handleAuth}
              className="space-y-4 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm"
            >
              <label className="block">
                <span className="text-sm font-medium text-gray-700">
                  {t.common.username}
                </span>
                <input
                  className="mt-2 w-full rounded-xl border border-gray-200 px-4 py-3 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                  minLength={3}
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  required
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-gray-700">
                  {t.common.password}
                </span>
                <input
                  className="mt-2 w-full rounded-xl border border-gray-200 px-4 py-3 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                  minLength={8}
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
              </label>
              <button
                className="rounded-full bg-gray-950 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:opacity-60"
                disabled={isSubmitting}
                type="submit"
              >
                {isSubmitting
                  ? t.common.submitting
                  : authMode === "register"
                    ? copy.registerAndContinue
                    : copy.signIn}
              </button>
              <button
                className="block text-sm font-medium text-gray-500 transition hover:text-gray-900"
                onClick={() => {
                  setAuthMode((current) =>
                    current === "register" ? "login" : "register",
                  );
                  setError("");
                }}
                type="button"
              >
                {authMode === "register"
                  ? copy.switchToLogin
                  : copy.switchToRegister}
              </button>
            </form>

            {existingSession?.is_admin ? (
            <section className="space-y-4 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
              <div>
                <p className="text-sm font-medium uppercase tracking-wide text-gray-500">
                  {copy.researchSwitcher}
                </p>
                <h2 className="mt-1 text-xl font-semibold text-gray-950">
                  {copy.existingAgentView}
                </h2>
              </div>
              <div className="flex justify-start">
                <button
                  className="rounded-full bg-gray-950 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:opacity-60"
                  disabled={isLoadingAgents}
                  onClick={loadAgentChoices}
                  type="button"
                >
                  {isLoadingAgents ? t.common.loading : copy.loadAgents}
                </button>
              </div>
              <p className="text-sm text-gray-500">
                {formatAgentChoiceStatus(agentChoiceStatus, copy)}
              </p>

              {agentChoices.length > 0 ? (
                <div className="divide-y divide-gray-100 overflow-hidden rounded-xl border border-gray-200">
                  {agentChoices.map((choice) => (
                    <div
                      className="flex flex-col gap-3 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                      key={choice.agent.id}
                    >
                      <div>
                        <p className="font-medium text-gray-950">
                          {choice.agent.agent_name}
                          {choice.agent.is_npc ? (
                            <span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-semibold text-gray-500">
                              NPC
                            </span>
                          ) : null}
                        </p>
                        <p className="text-sm text-gray-500">
                          @{choice.user.username} · {copy.userMeta} #{choice.user.id} ·{" "}
                          {copy.agentMeta} #
                          {choice.agent.id}
                        </p>
                      </div>
                      <button
                        className="rounded-full border border-gray-300 px-4 py-2 text-sm font-medium text-gray-800 transition hover:border-gray-900 disabled:opacity-60"
                        disabled={enteringAgentId === choice.agent.id}
                        onClick={() => enterAgentChoice(choice)}
                        type="button"
                      >
                        {enteringAgentId === choice.agent.id
                          ? copy.entering
                          : copy.enterView}
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </section>
            ) : null}
          </div>
        ) : (
          <form
            onSubmit={handleQuestionnaire}
            className="space-y-7 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm"
          >
            <label className="block">
              <span className="text-sm font-medium text-gray-700">{copy.mbti}</span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 px-4 py-3 uppercase outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                maxLength={16}
                value={mbtiType}
                onChange={(event) => setMbtiType(event.target.value)}
                placeholder="INTJ"
                required
              />
            </label>

            <ScoreGroup
              title={copy.bigFive}
              fields={bigFiveFields}
              labels={copy.scoreLabels}
              values={bigFiveScores}
              onChange={(key, value) =>
                setBigFiveScores((current) => ({ ...current, [key]: value }))
              }
            />

            <ScoreGroup
              title={copy.schwartzValues}
              fields={schwartzFields}
              labels={copy.scoreLabels}
              values={schwartzValues}
              onChange={(key, value) =>
                setSchwartzValues((current) => ({ ...current, [key]: value }))
              }
            />

            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                {copy.autobiographyLabel}
              </span>
              <textarea
                className="mt-2 min-h-40 w-full resize-y rounded-xl border border-gray-200 px-4 py-3 text-sm leading-6 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                value={autobiography}
                onChange={(event) => setAutobiography(event.target.value)}
                placeholder={copy.autobiographyPlaceholder}
              />
            </label>

            <button
              className="rounded-full bg-gray-950 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:opacity-60"
              disabled={isSubmitting}
              type="submit"
            >
              {isSubmitting ? copy.generating : copy.generateAgent}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}

function ScoreGroup({
  title,
  fields,
  labels,
  values,
  onChange,
}: {
  title: string;
  fields: readonly ScoreField[];
  labels: Record<string, string>;
  values: Record<string, number>;
  onChange: (key: string, value: number) => void;
}) {
  return (
    <section>
      <h2 className="mb-4 text-lg font-semibold text-gray-950">{title}</h2>
      <div className="space-y-4">
        {fields.map((key) => (
          <label key={key} className="block rounded-xl bg-gray-50 p-4">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="font-medium text-gray-700">{labels[key]}</span>
              <span className="font-mono text-gray-500">{values[key]}</span>
            </div>
            <input
              className="w-full accent-gray-950"
              max={100}
              min={0}
              type="range"
              value={values[key]}
              onChange={(event) => onChange(key, Number(event.target.value))}
            />
          </label>
        ))}
      </div>
    </section>
  );
}
