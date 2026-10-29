import type { RunResult, ToolEvent, ToolStatus } from "./types";

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const DRAFT_PREFIX = "corecoder-draft:v1:";

export function draftKey(workspace: string, sessionId: string) {
  return `${DRAFT_PREFIX}${encodeURIComponent(workspace)}:${encodeURIComponent(sessionId)}`;
}

export function readDraft(
  storage: KeyValueStorage,
  workspace: string,
  sessionId: string,
) {
  try {
    return storage.getItem(draftKey(workspace, sessionId)) || "";
  } catch {
    return "";
  }
}

export function writeDraft(
  storage: KeyValueStorage,
  workspace: string,
  sessionId: string,
  value: string,
) {
  try {
    const key = draftKey(workspace, sessionId);
    if (value) storage.setItem(key, value);
    else storage.removeItem(key);
  } catch {
    /* Private browsing and storage quotas must not break the composer. */
  }
}

export function restoreRejectedPrompt(
  currentDraft: string,
  sentPrompt: string,
) {
  return currentDraft.trim() ? currentDraft : sentPrompt;
}

export function latestRunResult(timeline: ToolEvent[]): RunResult | null {
  const event = [...timeline].reverse().find((item) => {
    if (item.type === "run_result") return true;
    const rawStatus = (item as ToolEvent & { status?: RunResult["status"] })
      .status;
    if (item.type === "error") return true;
    return (
      item.type === "notice" &&
      (rawStatus === "cancelled" || /已停止|取消/.test(item.message || ""))
    );
  });
  if (!event) return null;
  const rawStatus =
    event.runStatus ||
    (event as ToolEvent & { status?: RunResult["status"] }).status;
  const status =
    rawStatus ||
    (event.type === "error"
      ? "failed"
      : event.type === "notice"
        ? "cancelled"
        : undefined);
  if (!status) return null;
  return {
    status,
    finishReason: event.finish_reason,
    rounds: event.rounds,
    toolErrors:
      event.tool_errors ||
      (event.type === "error" && event.message
        ? [
            {
              call_id: null,
              name: null,
              status: "failed",
              stage: "run",
              message: event.message,
            },
          ]
        : []),
    text: event.text,
    at: event.at,
  };
}

export interface ToolView {
  key: string;
  id?: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: string;
  status?: ToolStatus;
  stage?: string;
  warning?: string | null;
  running: boolean;
}

export function mergeToolEvents(events: ToolEvent[]): ToolView[] {
  const views: ToolView[] = [];
  const pending = new Map<string, number[]>();
  events.forEach((event, eventIndex) => {
    if (event.type === "tool_start") {
      const viewIndex = views.length;
      views.push({
        key: `start-${eventIndex}`,
        id: event.id,
        name: event.name || "unknown",
        arguments: event.arguments || {},
        running: true,
      });
      if (event.id)
        pending.set(event.id, [...(pending.get(event.id) || []), viewIndex]);
      return;
    }
    if (event.type !== "tool_result") return;
    const status =
      event.toolStatus || (event as ToolEvent & { status?: ToolStatus }).status;
    const queue = event.id ? pending.get(event.id) : undefined;
    const viewIndex = queue?.shift();
    if (viewIndex !== undefined) {
      const prior = views[viewIndex];
      views[viewIndex] = {
        ...prior,
        name: event.name || prior.name,
        arguments: Object.keys(prior.arguments).length
          ? prior.arguments
          : event.arguments || {},
        result: event.result,
        status,
        stage: event.stage,
        warning: event.warning,
        running: false,
      };
    } else {
      views.push({
        key: `result-${eventIndex}`,
        id: event.id,
        name: event.name || "unknown",
        arguments: event.arguments || {},
        result: event.result,
        status: status || "unknown",
        stage: event.stage,
        warning: event.warning,
        running: false,
      });
    }
  });
  return views;
}
