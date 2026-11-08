import { type AnyHandler, getResponse, HttpResponse, http, RequestHandler } from "msw";
import { API_BASE_URL } from "@/api/base";
import { isPartPath } from "@/api/client";

type Part =
  | { kind: "answered"; status: number; headers: Record<string, string>; body: string }
  | { kind: "left"; reason: "not a part" | "timed out" };

async function partOf(path: string, reads: Request, handlers: RequestHandler[]): Promise<Part> {
  if (!isPartPath(path)) {
    return { kind: "left", reason: "not a part" };
  }
  const url = new URL(`${API_BASE_URL}${path}`, reads.url);
  const response = await getResponse(handlers, new Request(url, { headers: reads.headers }));
  if (response === undefined) {
    throw new Error(`no handler in src/mocks answers the part GET ${path}`);
  }
  if (response.type === "error") {
    return { kind: "left", reason: "timed out" };
  }
  const headers = Object.fromEntries(
    [...response.headers].filter(([name]) => name !== "content-length"),
  );
  return { kind: "answered", status: response.status, headers, body: await response.text() };
}

export function answersReads(handlersNow: () => readonly AnyHandler[]) {
  return http.get(`${API_BASE_URL}/reads`, async ({ request }) => {
    const paths = new URL(request.url).searchParams.getAll("part");
    if (paths.length === 0) {
      return HttpResponse.json(
        { detail: [{ loc: ["query", "part"], msg: "Field required", type: "missing" }] },
        { status: 422 },
      );
    }
    const handlers = handlersNow().filter(
      (handler): handler is RequestHandler => handler instanceof RequestHandler,
    );
    const parts: Part[] = [];
    for (const path of paths) {
      parts.push(await partOf(path, request, handlers));
    }
    return HttpResponse.json({ parts });
  });
}
