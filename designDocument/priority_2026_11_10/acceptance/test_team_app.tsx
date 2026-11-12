import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../../../frontend/src/App";

function makeSession(id: string, title: string, busy: boolean) {
  return {
    id, title, busy, run_id: `run-${id}`, updated_at: 1, model: "offline",
    message_count: 1, messages: [{ role: "user", content: "Run a read-only team" }],
    timeline: [], tool_outcomes: [], cwd: "E:/independent",
    plan_mode: false, tasks: [], team_tasks: {}, pending_permission: null, allowed_tools: [],
    changes: [], undo_count: 0,
    usage: {
      prompt: 0, completion: 0, cost: null, context: 0, max_context: 128000,
      cost_summary: { known_cost: null, unknown_prompt_tokens: 0, unknown_completion_tokens: 0,
        unknown_requests: 0, missing_usage_requests: 0 },
    },
  };
}

function task(runId: string, id: string, name: string, status: string, extra = {}) {
  return {
    type: "team_task", run_id: runId, team_id: "same-team-id", task_id: id,
    name, status, depends_on: [], reason: "", summary: "", ...extra,
  };
}

function openStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
  return {
    response: new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
    send(event: Record<string, unknown> & { seq: number }) {
      controller.enqueue(new TextEncoder().encode(`id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`));
    },
    end() { controller.close(); },
  };
}

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  });
  Element.prototype.scrollIntoView = vi.fn();
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("renders live team transitions, resumes from the last cursor, and ignores the old run after switching", async () => {
  const a = makeSession("a", "Independent Alpha", true);
  const b = makeSession("b", "Independent Bravo", false);
  const original = openStream();
  const resumed = openStream();
  const other = openStream();
  const subscribed: string[] = [];
  const requestedWrites: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    const url = new URL(path, "http://local.invalid");
    if (init?.method === "POST") requestedWrites.push(path);
    if (url.pathname === "/api/run/events") {
      const key = `${url.searchParams.get("id")}:${url.searchParams.get("after")}`;
      subscribed.push(key);
      if (key === "run-a:0") return original.response;
      if (key === "run-a:1") return resumed.response;
      if (key === "run-b:0") return other.response;
      throw new Error("Unexpected subscription " + key);
    }
    if (url.pathname === "/api/bootstrap") return Response.json({
      token: "offline-only", version: "test", workspace: "E:/independent", project: "Independent",
      model: "offline", provider: "openai", base_url: null, key_configured: true, tools: [],
      mcp_tools: [], hooks: { pre: 0, post: 0 }, sessions: [a, b],
    });
    if (url.pathname === "/api/sessions") return Response.json([a, b]);
    if (url.pathname === "/api/session") return Response.json(url.searchParams.get("id") === "a" ? a : b);
    if (url.pathname === "/api/run") return Response.json({ team_tasks: {} });
    throw new Error("Unexpected HTTP request " + path);
  }));
  const user = userEvent.setup();
  render(<App />);
  await waitFor(() => expect(subscribed).toContain("run-a:0"));
  await act(async () => {
    original.send({ ...task("run-a", "member", "Alpha member", "running"), seq: 1 });
    original.end();
  });
  expect(await screen.findByText("Alpha member")).toBeTruthy();
  await waitFor(() => expect(subscribed).toContain("run-a:1"));

  await act(async () => {
    resumed.send({ ...task("run-a", "member", "duplicate must be ignored", "running"), seq: 1 });
    resumed.send({
      ...task("run-a", "member", "Alpha member", "skipped", {
        depends_on: ["dependency-proof"], reason: "blocked by dependency-proof",
      }),
      seq: 2,
    });
  });
  expect(await screen.findByText(/blocked by dependency-proof/)).toBeTruthy();
  expect(screen.getAllByText("Alpha member")).toHaveLength(1);
  expect(screen.getByText("Alpha member").parentElement?.textContent).toContain("skipped");
  expect(screen.getByText("Alpha member").parentElement?.textContent).toContain("依赖：dependency-proof");
  expect(screen.queryByText("duplicate must be ignored")).toBeNull();

  // The production workbench disables navigation while its current run is
  // active. Let Alpha's terminal snapshot arrive while its stream is still
  // open, then simulate another viewer starting Bravo before navigation.
  await act(async () => {
    a.busy = false;
    resumed.send({ type: "snapshot", run_id: "run-a", session: { ...a }, seq: 3 });
  });
  const bravo = screen.getByRole<HTMLButtonElement>("button", { name: /Independent Bravo/ });
  await waitFor(() => expect(bravo.disabled).toBe(false));
  b.busy = true;
  await user.click(bravo);
  await waitFor(() => expect(subscribed).toContain("run-b:0"));
  await act(async () => {
    resumed.send({ ...task("run-a", "late", "stale Alpha member", "completed"), seq: 4 });
    resumed.end();
    other.send({ ...task("run-b", "member", "Bravo member", "running"), seq: 1 });
  });
  expect(await screen.findByText("Bravo member")).toBeTruthy();
  expect(screen.queryByText("Alpha member")).toBeNull();
  expect(screen.queryByText("stale Alpha member")).toBeNull();

  const recovered = task("run-b", "member", "Recovered Bravo member", "running", {
    summary: "authoritative reset",
  });
  const teamSnapshot = { member: recovered };
  await act(async () => {
    other.send({
      type: "reset", run_id: "run-b", status: "running", seq: 10,
      session: { ...b, team_tasks: teamSnapshot }, team_tasks: teamSnapshot,
    });
  });
  expect(await screen.findByText("Recovered Bravo member")).toBeTruthy();
  expect(screen.queryByText("Bravo member")).toBeNull();
  expect(requestedWrites).toEqual([]);
});

it("keeps an authoritative terminal row across older events and reselecting the idle session", async () => {
  const session = makeSession("a", "Independent Authority", true);
  const initial = openStream();
  const completed = { ...task("run-a", "member", "Authoritative member", "completed"), seq: 9 };
  let sessionReads = 0;
  let runReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    const url = new URL(path, "http://local.invalid");
    if (url.pathname === "/api/run/events") return initial.response;
    if (url.pathname === "/api/run") {
      runReads++;
      return Response.json({ status: "completed", team_tasks: { member: completed } });
    }
    if (url.pathname === "/api/bootstrap") return Response.json({
      token: "offline-only", version: "test", workspace: "E:/independent", project: "Independent",
      model: "offline", provider: "openai", base_url: null, key_configured: true, tools: [],
      mcp_tools: [], hooks: { pre: 0, post: 0 }, sessions: [session],
    });
    if (url.pathname === "/api/sessions") return Response.json([session]);
    if (url.pathname === "/api/session") {
      sessionReads++;
      return Response.json(session);
    }
    throw new Error("Unexpected HTTP request " + path);
  }));
  const user = userEvent.setup();
  render(<App />);
  expect(await screen.findByText("Authoritative member")).toBeTruthy();
  await act(async () => {
    initial.send({ ...task("run-a", "member", "Old replay must not replace", "running"), seq: 1 });
  });
  expect(screen.getByText("Authoritative member")).toBeTruthy();
  expect(screen.getByText("Authoritative member").parentElement?.textContent).toContain("completed");
  expect(screen.queryByText("Old replay must not replace")).toBeNull();

  await act(async () => {
    session.busy = false;
    initial.send({ type: "snapshot", run_id: "run-a", session: { ...session }, seq: 10 });
    initial.end();
  });
  const selected = screen.getByRole<HTMLButtonElement>("button", { name: /Independent Authority/ });
  await waitFor(() => expect(selected.disabled).toBe(false));
  const beforeReselect = sessionReads;
  await user.click(selected);
  await waitFor(() => expect(sessionReads).toBe(beforeReselect + 1));
  await waitFor(() => expect(selected.disabled).toBe(false));
  expect(screen.getByText("Authoritative member").parentElement?.textContent).toContain("completed");
  expect(runReads).toBe(1);
});
