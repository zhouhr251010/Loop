"use client";

export type LoopSession = {
  user_id: number;
  username: string;
  is_admin: boolean;
  access_token: string;
  token_expires_at?: number;
  agent_id?: number;
  agent_name?: string;
  agent_is_npc?: boolean;
};

export type ImpersonationMarker = {
  admin_user_id: number;
  admin_username: string;
  started_at: number;
};

const SESSION_KEY = "loop_session";
const IMPERSONATION_KEY = "loop_impersonation";
const ADMIN_BACKUP_KEY = "loop_admin_backup";

export function saveSession(session: LoopSession) {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function loadSession(): LoopSession | null {
  const raw = localStorage.getItem(SESSION_KEY);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<LoopSession>;
    const session = {
      ...parsed,
      is_admin: parsed.is_admin === true,
    } as LoopSession;
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

export function saveImpersonationMarker(adminSession: LoopSession) {
  const marker: ImpersonationMarker = {
    admin_user_id: adminSession.user_id,
    admin_username: adminSession.username,
    started_at: Date.now(),
  };
  localStorage.setItem(IMPERSONATION_KEY, JSON.stringify(marker));
}

export function loadImpersonationMarker(): ImpersonationMarker | null {
  const raw = localStorage.getItem(IMPERSONATION_KEY);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<ImpersonationMarker>;
    if (
      typeof parsed.admin_user_id !== "number" ||
      typeof parsed.admin_username !== "string" ||
      typeof parsed.started_at !== "number"
    ) {
      localStorage.removeItem(IMPERSONATION_KEY);
      return null;
    }
    return parsed as ImpersonationMarker;
  } catch {
    localStorage.removeItem(IMPERSONATION_KEY);
    return null;
  }
}

export function clearImpersonationMarker() {
  localStorage.removeItem(IMPERSONATION_KEY);
  localStorage.removeItem(ADMIN_BACKUP_KEY);
}

export function saveAdminBackupSession(adminSession: LoopSession) {
  localStorage.setItem(ADMIN_BACKUP_KEY, JSON.stringify(adminSession));
}

export function loadAdminBackupSession(): LoopSession | null {
  const raw = localStorage.getItem(ADMIN_BACKUP_KEY);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<LoopSession>;
    const session = {
      ...parsed,
      is_admin: parsed.is_admin === true,
    } as LoopSession;
    if (!session.access_token || session.is_admin !== true) {
      localStorage.removeItem(ADMIN_BACKUP_KEY);
      return null;
    }
    if (session.token_expires_at && Date.now() >= session.token_expires_at) {
      localStorage.removeItem(ADMIN_BACKUP_KEY);
      return null;
    }
    return session;
  } catch {
    localStorage.removeItem(ADMIN_BACKUP_KEY);
    return null;
  }
}

export function clearAdminBackupSession() {
  localStorage.removeItem(ADMIN_BACKUP_KEY);
}

export function clearSession() {
  localStorage.removeItem(SESSION_KEY);
  clearImpersonationMarker();
}
