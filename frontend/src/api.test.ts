import { afterEach, describe, expect, it, vi } from "vitest";
import { api, chat } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("chat acceptance boundary", () => {
  it("does not mark a rejected request as accepted", async () => {
    const accepted = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: "会话正在运行" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(chat("s", "reject", vi.fn(), accepted)).rejects.toThrow(
      "会话正在运行",
    );
    expect(accepted).not.toHaveBeenCalled();
  });

  it("marks a successful HTTP response accepted before consuming its SSE stream", async () => {
    const accepted = vi.fn();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode('data: {"type":"done"}\n\n'),
        );
        controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(body, { status: 200 })),
    );
    await chat("s", "ok", vi.fn(), accepted);
    expect(accepted).toHaveBeenCalledOnce();
  });
});

describe("JSON API errors", () => {
  it("rejects an HTTP 200 response that is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html>proxy error</html>", { status: 200 }),
        ),
    );
    await expect(api("bootstrap")).rejects.toThrow("无法解析");
  });
});
