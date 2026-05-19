import http from "node:http";
import next from "next";

const { hostname, port } = parseCliArgs(process.argv.slice(2));
const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8001";
const app = next({
  dev: true,
  hostname,
  port,
});
const handle = app.getRequestHandler();

await app.prepare();

const server = http.createServer((req, res) => {
  if (req.url?.startsWith("/api/social/events")) {
    proxySocialEvents(req, res);
    return;
  }

  handle(req, res).catch((err) => {
    console.error("Error handling request", err);
    if (!res.headersSent) {
      res.statusCode = 500;
      res.end("Internal Server Error");
    }
  });
});

server.listen(port, hostname, () => {
  console.log(`> Loop frontend ready on http://${hostname}:${port}`);
});

function proxySocialEvents(req, res) {
  const targetUrl = new URL(req.url ?? "/api/social/events", backendOrigin);
  const proxyReq = http.request(
    targetUrl,
    {
      method: "GET",
      headers: {
        accept: "text/event-stream",
        "cache-control": "no-cache",
        "x-forwarded-host": req.headers.host ?? "",
        "x-forwarded-proto": req.headers["x-forwarded-proto"] ?? "http",
      },
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode ?? 502, {
        "Content-Type": proxyRes.headers["content-type"] ?? "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      });
      proxyRes.pipe(res);
    },
  );

  proxyReq.on("error", (err) => {
    console.error("Error proxying social SSE", err);
    if (!res.headersSent) {
      res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    }
    res.end("Social realtime stream unavailable");
  });

  req.on("close", () => {
    proxyReq.destroy();
  });
  proxyReq.end();
}

function parseCliArgs(args) {
  let hostname = process.env.FRONTEND_HOST ?? "127.0.0.1";
  let port = Number(process.env.FRONTEND_PORT ?? 3000);

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if ((arg === "--hostname" || arg === "-H") && args[i + 1]) {
      hostname = args[i + 1];
      i += 1;
    } else if (arg.startsWith("--hostname=")) {
      hostname = arg.slice("--hostname=".length);
    } else if ((arg === "--port" || arg === "-p") && args[i + 1]) {
      port = Number(args[i + 1]);
      i += 1;
    } else if (arg.startsWith("--port=")) {
      port = Number(arg.slice("--port=".length));
    }
  }

  if (!Number.isInteger(port) || port <= 0) {
    throw new Error(`Invalid frontend port: ${port}`);
  }

  return { hostname, port };
}
