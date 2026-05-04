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
      setError(err instanceof Error ? err.message : "注册失败");
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
          }),
        },
      );
      saveSession({
        user_id: result.user.id,
        username: result.user.username,
        agent_name: result.agent.agent_name,
      });
      router.push("/plaza");
    } catch (err) {
      setError(err instanceof Error ? err.message : "问卷提交失败");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col justify-center px-6 py-10">
      <div className="mb-8">
        <p className="text-sm font-medium uppercase tracking-wide text-neutral-500">
          Loop Research Platform
        </p>
        <h1 className="mt-2 text-3xl font-semibold">志愿者注册与问卷</h1>
      </div>

      {error ? (
        <div className="mb-6 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {!user ? (
        <form
          onSubmit={handleRegister}
          className="space-y-4 rounded-lg border border-neutral-200 bg-white p-6 shadow-sm"
        >
          <label className="block">
            <span className="text-sm font-medium">Username</span>
            <input
              className="mt-2 w-full rounded-md border border-neutral-300 px-3 py-2"
              minLength={3}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium">Password</span>
            <input
              className="mt-2 w-full rounded-md border border-neutral-300 px-3 py-2"
              minLength={8}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          <button
            className="rounded-md bg-neutral-900 px-4 py-2 text-white disabled:opacity-60"
            disabled={isSubmitting}
            type="submit"
          >
            {isSubmitting ? "提交中..." : "注册并继续"}
          </button>
        </form>
      ) : (
        <form
          onSubmit={handleQuestionnaire}
          className="space-y-6 rounded-lg border border-neutral-200 bg-white p-6 shadow-sm"
        >
          <label className="block">
            <span className="text-sm font-medium">MBTI</span>
            <input
              className="mt-2 w-full rounded-md border border-neutral-300 px-3 py-2 uppercase"
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

          <button
            className="rounded-md bg-neutral-900 px-4 py-2 text-white disabled:opacity-60"
            disabled={isSubmitting}
            type="submit"
          >
            {isSubmitting ? "生成中..." : "生成我的 Agent"}
          </button>
        </form>
      )}
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
      <h2 className="mb-3 text-lg font-semibold">{title}</h2>
      <div className="space-y-4">
        {fields.map(([key, label]) => (
          <label key={key} className="block">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span>{label}</span>
              <span className="font-mono">{values[key]}</span>
            </div>
            <input
              className="w-full accent-neutral-900"
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
