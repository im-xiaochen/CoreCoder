import { describe, expect, it } from "vitest";
import type { ToolEvent } from "./types";
import {
  draftKey,
  latestRunResult,
  mergeToolEvents,
  readDraft,
  restoreRejectedPrompt,
  writeDraft,
} from "./workbench-state";

class MemoryStorage {
  values = new Map<string, string>();
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
}

describe("workspace/session drafts", () => {
  it("isolates drafts and restores them after switching sessions", () => {
    const storage = new MemoryStorage();
    writeDraft(storage, "D:/one", "a", "第一条草稿");
    writeDraft(storage, "D:/one", "b", "第二条草稿");
    writeDraft(storage, "D:/two", "a", "另一工作区");
    expect(readDraft(storage, "D:/one", "a")).toBe("第一条草稿");
    expect(readDraft(storage, "D:/one", "b")).toBe("第二条草稿");
    expect(readDraft(storage, "D:/two", "a")).toBe("另一工作区");
    expect(draftKey("D:/one", "a")).not.toBe(draftKey("D:/two", "a"));
  });

  it("restores an unaccepted prompt without overwriting the next draft", () => {
    expect(restoreRejectedPrompt("", "被拒绝的任务")).toBe("被拒绝的任务");
    expect(restoreRejectedPrompt("发送期间写的新草稿", "被拒绝的任务")).toBe(
      "发送期间写的新草稿",
    );
  });
});

describe("durable run results", () => {
  it("restores the last terminal result from persisted timeline", () => {
    const timeline = [
      {
        type: "run_result",
        status: "failed",
        tool_errors: [
          {
            call_id: null,
            name: null,
            status: "failed",
            stage: "loop",
            message: "provider error",
          },
        ],
      },
      { type: "run_result", status: "cancelled", rounds: 2 },
    ] as unknown as ToolEvent[];
    expect(latestRunResult(timeline)).toMatchObject({
      status: "cancelled",
      rounds: 2,
    });
  });

  it("returns no invented result for legacy sessions", () => {
    expect(
      latestRunResult([{ type: "notice", message: "old session" }]),
    ).toBeNull();
  });
});

describe("tool event merge", () => {
  it("does not infer success from a legacy result without a status", () => {
    expect(mergeToolEvents([
      { type: "tool_start", id: "legacy", name: "read_file" },
      { type: "tool_result", id: "legacy", result: "legacy output" },
    ])).toMatchObject([{ status: "unknown", running: false }]);
  });

  it("pairs repeated ids in occurrence order and keeps result-only calls terminal", () => {
    const events = [
      {
        type: "tool_start",
        id: "same",
        name: "read_file",
        arguments: { file_path: "a" },
      },
      {
        type: "tool_start",
        id: "same",
        name: "read_file",
        arguments: { file_path: "b" },
      },
      {
        type: "tool_result",
        id: "same",
        name: "read_file",
        result: "A",
        toolStatus: "succeeded",
      },
      {
        type: "tool_result",
        id: "same",
        name: "read_file",
        result: "B",
        toolStatus: "failed",
        warning: "截断",
      },
      { type: "tool_result", id: "orphan", name: "grep", result: "C" },
    ] satisfies ToolEvent[];
    expect(mergeToolEvents(events)).toMatchObject([
      {
        result: "A",
        status: "succeeded",
        running: false,
        arguments: { file_path: "a" },
      },
      {
        result: "B",
        status: "failed",
        warning: "截断",
        running: false,
        arguments: { file_path: "b" },
      },
      { result: "C", status: "unknown", running: false },
    ]);
  });
});
