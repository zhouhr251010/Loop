import { NextRequest, NextResponse } from "next/server";
import {
  createSiteSessionToken,
  getSiteAuthConfig,
  getSiteAuthSessionSeconds,
  safeEqual,
  SITE_AUTH_COOKIE_NAME,
} from "@/lib/siteAuth";

function isSecureRequest(request: NextRequest) {
  const forwardedProtocol = request.headers.get("x-forwarded-proto");
  return request.nextUrl.protocol === "https:" || forwardedProtocol === "https";
}

export async function POST(request: NextRequest) {
  const siteAuth = getSiteAuthConfig();
  if (!siteAuth) {
    return NextResponse.json(
      { error: "Site authentication is not configured" },
      { status: 500 },
    );
  }

  let body: { username?: unknown; password?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: "Invalid login request" },
      { status: 400 },
    );
  }

  const username = typeof body.username === "string" ? body.username : "";
  const password = typeof body.password === "string" ? body.password : "";
  const credentialsAreValid =
    safeEqual(username, siteAuth.username) && safeEqual(password, siteAuth.password);

  if (!credentialsAreValid) {
    return NextResponse.json(
      { error: "Incorrect username or password" },
      { status: 401 },
    );
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set({
    name: SITE_AUTH_COOKIE_NAME,
    value: await createSiteSessionToken(siteAuth.username, siteAuth.sessionSecret),
    httpOnly: true,
    sameSite: "lax",
    secure: isSecureRequest(request),
    maxAge: getSiteAuthSessionSeconds(),
    path: "/",
  });

  return response;
}
