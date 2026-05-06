export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type User = {
  id: number;
  username: string;
  mbti_type?: string | null;
  big_five_scores?: Record<string, number> | null;
  schwartz_values?: Record<string, number> | null;
  autobiography?: string | null;
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

export type ChatReply = {
  reply: string;
  memory_chunks_used: number;
  chat_log: {
    id: number;
    agent_id: number;
    user_message: string;
    agent_reply: string;
    timestamp: string;
  };
};

export type MemoryUploadResponse = {
  message: string;
  chunks_added: number;
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
    const rawMessage = await response.text();
    let message = rawMessage;

    try {
      const parsed = JSON.parse(rawMessage) as { detail?: unknown };
      if (typeof parsed.detail === "string") {
        message = parsed.detail;
      } else if (parsed.detail) {
        message = JSON.stringify(parsed.detail);
      }
    } catch {
      message = rawMessage;
    }

    throw new Error(message || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}
