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
import { LoopSession, clearSession, loadSession, saveSession } from "@/lib/session";

const bigFiveFields = [
  ["openness", "Openness"],
  ["conscientiousness", "Conscientiousness"],
  ["extraversion", "Extraversion"],
  ["agreeableness", "Agreeableness"],
  ["neuroticism", "Neuroticism"],
] as const;

const schwartzFields = [
  ["self_direction", "Self Direction"],
  ["universalism", "Universalism"],
  ["security", "Security"],
] as const;

type ScoreField = readonly [key: string, label: string];

type QuestionnaireResponse = {
  user: User;
  agent: Agent;
};

function initialScores(fields: readonly ScoreField[]) {
  return Object.fromEntries(fields.map(([key]) => [key, 50])) as Record<
    string,
    number
  >;
}

export default function OnboardingPage() {
  const router = useRouter();
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
  const [adminKey, setAdminKey] = useState("");
  const [agentChoices, setAgentChoices] = useState<AgentSessionChoice[]>([]);
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);
  const [enteringAgentId, setEnteringAgentId] = useState<number | null>(null);
  const [agentChoiceStatus, setAgentChoiceStatus] = useState(
    "Enter the admin key, then load the existing Agent list.",
  );

  useEffect(() => {
    const session = loadSession();
    if (!session) {
      return;
    }

    setExistingSession(session);
  }, [router]);

  function persistAuthSession(authSession: AuthSession, agent?: Agent) {
    const tokenExpiresAt = Date.now() + authSession.expires_in * 1000;
    saveSession({
      user_id: authSession.user.id,
      username: authSession.user.username,
      access_token: authSession.access_token,
      token_expires_at: tokenExpiresAt,
      ...(agent
        ? {
            agent_id: agent.id,
            agent_name: agent.agent_name,
          }
        : {}),
    });
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
      router.push("/plaza");
      return;
    }

    setUser({
      id: existingSession.user_id,
      username: existingSession.username,
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
      persistAuthSession(authSession);

      try {
        const agent = await apiRequest<Agent>("/api/users/me/agent");
        persistAuthSession(authSession, agent);
        router.push("/plaza");
      } catch {
        setUser(authSession.user);
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed.");
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
        access_token: currentSession?.access_token ?? "",
        token_expires_at: currentSession?.token_expires_at,
        agent_id: result.agent.id,
        agent_name: result.agent.agent_name,
      });
      router.push("/plaza");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Questionnaire submission failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function loadAgentChoices() {
    const trimmedAdminKey = adminKey.trim();
    if (!trimmedAdminKey) {
      setError("Admin key is required.");
      return;
    }

    setError("");
    setAgentChoiceStatus("Loading existing Agents...");
    setIsLoadingAgents(true);
    try {
      const choices = await apiRequest<AgentSessionChoice[]>(
        "/api/users/agent-choices",
        {
          headers: {
            "X-Loop-Admin-Key": trimmedAdminKey,
          },
        },
      );
      setAgentChoices(choices);
      setAgentChoiceStatus(
        choices.length > 0
          ? `Loaded ${choices.length} Agent${choices.length === 1 ? "" : "s"}.`
          : "No existing Agents were found in the database.",
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load agents.";
      setError(message);
      setAgentChoiceStatus(
        message.includes("Failed to fetch")
          ? "Could not reach the API. Restart FastAPI and Next.js, then try again."
          : "Agent list request failed. Check the admin key and backend logs.",
      );
    } finally {
      setIsLoadingAgents(false);
    }
  }

  async function enterAgentChoice(choice: AgentSessionChoice) {
    const trimmedAdminKey = adminKey.trim();
    if (!trimmedAdminKey) {
      setError("Admin key is required.");
      return;
    }

    setError("");
    setEnteringAgentId(choice.agent.id);
    try {
      const authSession = await apiRequest<AuthSession>(
        `/api/users/agent-choices/${choice.agent.id}/session`,
        {
          method: "POST",
          headers: {
            "X-Loop-Admin-Key": trimmedAdminKey,
          },
        },
      );
      persistAuthSession(authSession, choice.agent);
      router.push("/plaza");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to enter agent.");
    } finally {
      setEnteringAgentId(null);
    }
  }

  return (
    <main className="min-h-screen bg-gray-50 px-4 py-10 sm:px-6">
      <div className="mx-auto w-full max-w-3xl">
        <div className="mb-8">
          <p className="text-sm font-medium uppercase tracking-wide text-gray-500">
            {user ? "Step 2 of 2" : "Step 1 of 2"} · Loop Research Platform
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950">
            {user ? "Personality profile" : "Participant registration"}
          </h1>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            {user
              ? `Registered as ${user.username}. Complete the personality questionnaire to generate your digital Agent.`
              : authMode === "register"
                ? "Register first, then describe your identity core and generate your digital Agent."
                : "Sign in to continue your Loop research session."}
          </p>
        </div>

        {error ? (
          <div className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        ) : null}

        {user ? (
          <div className="mb-6 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            Session secured. Continue with the personality profile below.
          </div>
        ) : null}

        {!user && existingSession ? (
          <section className="mb-6 rounded-2xl border border-amber-200 bg-amber-50 p-5 shadow-sm">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium text-amber-900">
                  Previous local session found
                </p>
                <p className="mt-1 text-sm text-amber-800">
                  Signed in as {existingSession.username}
                  {existingSession.agent_name
                    ? ` · ${existingSession.agent_name}`
                    : " · profile not completed"}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  className="rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800"
                  onClick={continueExistingSession}
                  type="button"
                >
                  Continue
                </button>
                <button
                  className="rounded-full border border-amber-300 bg-white px-4 py-2 text-sm font-medium text-amber-900 transition hover:border-amber-500"
                  onClick={resetLocalSessionForNewUser}
                  type="button"
                >
                  Register new user
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
                <span className="text-sm font-medium text-gray-700">Username</span>
                <input
                  className="mt-2 w-full rounded-xl border border-gray-200 px-4 py-3 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                  minLength={3}
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  required
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-gray-700">Password</span>
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
                  ? "Submitting..."
                  : authMode === "register"
                    ? "Register and continue"
                    : "Sign in"}
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
                  ? "Already registered? Sign in"
                  : "Need a new account? Register"}
              </button>
            </form>

            <section className="space-y-4 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
              <div>
                <p className="text-sm font-medium uppercase tracking-wide text-gray-500">
                  Research switcher
                </p>
                <h2 className="mt-1 text-xl font-semibold text-gray-950">
                  Existing Agent view
                </h2>
              </div>
              <div className="flex flex-col gap-3 sm:flex-row">
                <label className="block flex-1">
                  <span className="text-sm font-medium text-gray-700">
                    Admin key
                  </span>
                  <input
                    className="mt-2 w-full rounded-xl border border-gray-200 px-4 py-3 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                    type="password"
                    value={adminKey}
                    onChange={(event) => {
                      setAdminKey(event.target.value);
                      setAgentChoices([]);
                      setAgentChoiceStatus(
                        "Enter the admin key, then load the existing Agent list.",
                      );
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void loadAgentChoices();
                      }
                    }}
                  />
                </label>
                <div className="flex items-end">
                  <button
                    className="rounded-full bg-gray-950 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:opacity-60"
                    disabled={isLoadingAgents}
                    onClick={loadAgentChoices}
                    type="button"
                  >
                    {isLoadingAgents ? "Loading..." : "Load agents"}
                  </button>
                </div>
              </div>
              <p className="text-sm text-gray-500">{agentChoiceStatus}</p>

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
                        </p>
                        <p className="text-sm text-gray-500">
                          @{choice.user.username} · user #{choice.user.id} · agent #
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
                          ? "Entering..."
                          : "Enter view"}
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </section>
          </div>
        ) : (
          <form
            onSubmit={handleQuestionnaire}
            className="space-y-7 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm"
          >
            <label className="block">
              <span className="text-sm font-medium text-gray-700">MBTI</span>
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
              title="Big Five"
              fields={bigFiveFields}
              values={bigFiveScores}
              onChange={(key, value) =>
                setBigFiveScores((current) => ({ ...current, [key]: value }))
              }
            />

            <ScoreGroup
              title="Schwartz Values"
              fields={schwartzFields}
              values={schwartzValues}
              onChange={(key, value) =>
                setSchwartzValues((current) => ({ ...current, [key]: value }))
              }
            />

            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                Digital autobiography / core values
              </span>
              <textarea
                className="mt-2 min-h-40 w-full resize-y rounded-xl border border-gray-200 px-4 py-3 text-sm leading-6 outline-none transition placeholder:text-gray-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
                value={autobiography}
                onChange={(event) => setAutobiography(event.target.value)}
                placeholder="Write your digital autobiography / core values. This will become the soul tone of your Agent."
              />
            </label>

            <button
              className="rounded-full bg-gray-950 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:opacity-60"
              disabled={isSubmitting}
              type="submit"
            >
              {isSubmitting ? "Generating..." : "Generate my Agent"}
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
  values,
  onChange,
}: {
  title: string;
  fields: readonly ScoreField[];
  values: Record<string, number>;
  onChange: (key: string, value: number) => void;
}) {
  return (
    <section>
      <h2 className="mb-4 text-lg font-semibold text-gray-950">{title}</h2>
      <div className="space-y-4">
        {fields.map(([key, label]) => (
          <label key={key} className="block rounded-xl bg-gray-50 p-4">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="font-medium text-gray-700">{label}</span>
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
