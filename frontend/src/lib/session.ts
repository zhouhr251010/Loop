"use client";

export type LoopSession = {
  user_id: number;
  username: string;
  access_token: string;
  token_expires_at?: number;
  agent_id?: number;
  agent_name?: string;
  agent_is_npc?: boolean;
};

const SESSION_KEY = "loop_session";

export function saveSession(session: LoopSession) {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function loadSession(): LoopSession | null {
  const raw = localStorage.getItem(SESSION_KEY);
  if (!raw) {
    return null;
  }

  try {
    const session = JSON.parse(raw) as LoopSession;
    if (!session.access_token) {
      localStorage.removeItem(SESSION_KEY);
      return null;
    }
    if (session.token_expires_at && Date.now() >= session.token_expires_at) {
      localStorage.removeItem(SESSION_KEY);
      return null;
    }
    return session;
  } catch {
    localStorage.removeItem(SESSION_KEY);
    return null;
  }
}

export function getAccessToken(): string | null {
  return loadSession()?.access_token ?? null;
}

export function clearSession() {
  localStorage.removeItem(SESSION_KEY);
}
