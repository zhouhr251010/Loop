"use client";

import { FormEvent, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useLanguage } from "@/components/LanguageContext";

function getSafeNextPath(nextPath: string | null) {
  if (!nextPath || !nextPath.startsWith("/") || nextPath.startsWith("//")) {
    return "/plaza";
  }

  if (nextPath.startsWith("/site-login") || nextPath.startsWith("/site-auth")) {
    return "/plaza";
  }

  return nextPath;
}

export function SiteLoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { t } = useLanguage();
  const copy = t.siteLogin;
  const nextPath = useMemo(
    () => getSafeNextPath(searchParams.get("next")),
    [searchParams],
  );
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);

    try {
      const response = await fetch("/site-auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ username, password }),
      });

      if (!response.ok) {
        setError(copy.invalidCredentials);
        return;
      }

      router.replace(nextPath);
      router.refresh();
    } catch {
      setError(copy.requestFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="min-h-[calc(100vh-65px)] bg-[#f7f7f4] px-4 py-12 text-gray-950">
      <section className="mx-auto flex w-full max-w-md flex-col gap-6">
        <div>
          <p className="text-sm font-semibold text-gray-500">{copy.product}</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">{copy.title}</h1>
          <p className="mt-3 text-sm leading-6 text-gray-600">{copy.subtitle}</p>
        </div>

        <form
          className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
          onSubmit={handleSubmit}
        >
          <label className="block text-sm font-semibold text-gray-700">
            {copy.username}
            <input
              autoComplete="username"
              autoFocus
              className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-base outline-none transition focus:border-gray-950 focus:ring-2 focus:ring-gray-950/10"
              disabled={isSubmitting}
              onChange={(event) => setUsername(event.target.value)}
              value={username}
            />
          </label>

          <label className="mt-4 block text-sm font-semibold text-gray-700">
            {copy.password}
            <input
              autoComplete="current-password"
              className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-base outline-none transition focus:border-gray-950 focus:ring-2 focus:ring-gray-950/10"
              disabled={isSubmitting}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              value={password}
            />
          </label>

          {error ? (
            <p className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-medium text-red-700">
              {error}
            </p>
          ) : null}

          <button
            className="mt-5 w-full rounded-md bg-gray-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-400"
            disabled={isSubmitting}
            type="submit"
          >
            {isSubmitting ? copy.submitting : copy.submit}
          </button>
        </form>
      </section>
    </main>
  );
}
