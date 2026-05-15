import { NextRequest, NextResponse } from "next/server";
import {
  getSiteAuthConfig,
  isValidSiteSessionToken,
  SITE_AUTH_COOKIE_NAME,
} from "./lib/siteAuth";

const PUBLIC_PATH_PREFIXES = [
  "/site-login",
  "/site-auth",
  "/evaluations",
  "/api/evaluations",
];

function isPublicPath(pathname: string) {
  return PUBLIC_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function isDocumentRequest(request: NextRequest) {
  if (request.method !== "GET") {
    return false;
  }

  const accept = request.headers.get("accept") ?? "";
  return accept.includes("text/html");
}

function unauthorized() {
  return NextResponse.json(
    { error: "Site authentication required" },
    {
      status: 401,
      headers: {
        "Cache-Control": "no-store",
      },
    },
  );
}

function firstForwardedValue(value: string | null) {
  return value?.split(",")[0]?.trim() ?? "";
}

function getExternalOrigin(request: NextRequest) {
  const forwardedHost = firstForwardedValue(request.headers.get("x-forwarded-host"));
  const forwardedProto = firstForwardedValue(
    request.headers.get("x-forwarded-proto"),
  );
  const host =
    forwardedHost || request.headers.get("host") || request.nextUrl.host;
  const protocol =
    forwardedProto || request.nextUrl.protocol.replace(":", "") || "http";

  return `${protocol}://${host}`;
}

export async function middleware(request: NextRequest) {
  const pathname = request.nextUrl.pathname;

  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  const siteAuth = getSiteAuthConfig();
  if (!siteAuth) {
    return new NextResponse("Basic auth is not configured", {
      status: 500,
      headers: {
        "Cache-Control": "no-store",
      },
    });
  }

  const hasValidSession = await isValidSiteSessionToken(
    request.cookies.get(SITE_AUTH_COOKIE_NAME)?.value,
    siteAuth.username,
    siteAuth.sessionSecret,
  );

  if (hasValidSession) {
    return NextResponse.next();
  }

  if (isDocumentRequest(request)) {
    const nextPath = `${request.nextUrl.pathname}${request.nextUrl.search}`;
    const loginUrl = new URL("/site-login", getExternalOrigin(request));
    loginUrl.searchParams.set("next", nextPath);

    return NextResponse.redirect(loginUrl, {
      headers: {
        "Cache-Control": "no-store",
      },
    });
  }

  return unauthorized();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
