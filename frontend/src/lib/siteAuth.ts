export const SITE_AUTH_COOKIE_NAME = "loop_site_auth";
export const DEFAULT_SITE_AUTH_SESSION_SECONDS = 12 * 60 * 60;

export type SiteAuthConfig = {
  username: string;
  password: string;
  sessionSecret: string;
};

export function getSiteAuthConfig(): SiteAuthConfig | null {
  const username = process.env.BASIC_AUTH_USER;
  const password = process.env.BASIC_AUTH_PASSWORD;

  if (!username || !password) {
    return null;
  }

  return {
    username,
    password,
    sessionSecret: process.env.BASIC_AUTH_COOKIE_SECRET ?? password,
  };
}

export function getSiteAuthSessionSeconds() {
  const configuredSeconds = Number(process.env.BASIC_AUTH_SESSION_SECONDS);
  return Number.isFinite(configuredSeconds) && configuredSeconds > 0
    ? configuredSeconds
    : DEFAULT_SITE_AUTH_SESSION_SECONDS;
}

function bytesToHex(bytes: ArrayBuffer) {
  return Array.from(new Uint8Array(bytes))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function safeEqual(left: string, right: string) {
  if (left.length !== right.length) {
    return false;
  }

  let result = 0;
  for (let index = 0; index < left.length; index += 1) {
    result |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }

  return result === 0;
}

async function signSession(username: string, expiresAt: number, secret: string) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`${username}:${expiresAt}`),
  );

  return bytesToHex(signature);
}

export async function createSiteSessionToken(username: string, secret: string) {
  const expiresAt = Math.floor(Date.now() / 1000) + getSiteAuthSessionSeconds();
  const signature = await signSession(username, expiresAt, secret);

  return `${expiresAt}.${signature}`;
}

export async function isValidSiteSessionToken(
  token: string | undefined,
  username: string,
  secret: string,
) {
  if (!token) {
    return false;
  }

  const [expiresAtText, signature] = token.split(".");
  const expiresAt = Number(expiresAtText);
  if (!signature || !Number.isFinite(expiresAt)) {
    return false;
  }

  if (expiresAt <= Math.floor(Date.now() / 1000)) {
    return false;
  }

  const expectedSignature = await signSession(username, expiresAt, secret);
  return safeEqual(signature, expectedSignature);
}
