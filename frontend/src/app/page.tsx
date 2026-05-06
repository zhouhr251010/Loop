"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { Agent, User, apiRequest } from "@/lib/api";
import { saveSession } from "@/lib/session";

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
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [mbtiType, setMbtiType] = useState("");
  const [bigFiveScores, setBigFiveScores] = useState(initialScores(bigFiveFields));
  const [schwartzValues, setSchwartzValues] = useState(
    initialScores(schwartzFields),
  );
  const [autobiography, setAutobiography] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleRegister(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);

    try {
      const createdUser = await apiRequest<User>("/api/users/register", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setUser(createdUser);
      saveSession({ user_id: createdUser.id, username: createdUser.username });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed.");
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
      const result = await apiRequest<QuestionnaireResponse>(
        `/api/users/${user.id}/questionnaire`,
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

  return (
    <main className="min-h-screen bg-gray-50 px-4 py-10 sm:px-6">
      <div className="mx-auto w-full max-w-3xl">
        <div className="mb-8">
          <p className="text-sm font-medium uppercase tracking-wide text-gray-500">
            Loop Research Platform
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950">
            Participant onboarding
          </h1>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            Register, describe your identity core, and generate your digital Agent.
          </p>
        </div>

        {error ? (
          <div className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        ) : null}

        {!user ? (
          <form
            onSubmit={handleRegister}
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
              {isSubmitting ? "Submitting..." : "Register and continue"}
            </button>
          </form>
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
