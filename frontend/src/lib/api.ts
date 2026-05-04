export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type User = {
  id: number;
  username: string;
  mbti_type?: string | null;
  big_five_scores?: Record<string, number> | null;
  schwartz_values?: Record<string, number> | null;
};

export type Agent = {
  id: number;
  user_id: number;
  agent_name: string;
  system_prompt_base: string;
};

export type Post = {
  id: number;
  agent_id: number;
  agent_name: string;
  content: string;
  timestamp: string;
};

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}
