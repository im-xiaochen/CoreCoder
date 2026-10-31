export type ToolStatus =
  "succeeded" | "failed" | "refused" | "cancelled" | "unknown";
export type RunStatus = "completed" | "failed" | "cancelled" | "max_rounds";
export interface RunResult {
  status: RunStatus;
  finishReason?: string;
  rounds?: number;
  toolErrors: {
    call_id: string | null;
    name: string | null;
    status: string;
    stage: string;
    message: string;
  }[];
  text?: string;
  at?: number;
}
export interface ToolEvent {
  type: string;
  id?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  result?: string;
  text?: string;
  message?: string;
  refused?: boolean;
  toolStatus?: ToolStatus;
  runStatus?: RunStatus;
  stage?: string;
  warning?: string | null;
  finish_reason?: string;
  rounds?: number;
  tool_errors?: {
    call_id: string | null;
    name: string | null;
    status: string;
    stage: string;
    message: string;
  }[];
  at?: number;
  session?: Session;
}
export interface Message {
  role: "user" | "assistant" | "tool" | "system";
  content: string | null;
  tool_call_id?: string;
  tool_calls?: { id: string; function: { name: string; arguments: string } }[];
}
export interface SessionSummary {
  id: string;
  title: string;
  updated_at: number;
  model: string;
  busy: boolean;
  message_count: number;
}
export interface Change {
  path: string;
  status: string;
  additions: number;
  deletions: number;
  diff?: string;
}
export interface Permission {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}
export type SessionMode = "chat" | "plan" | "code";
export interface Session extends SessionSummary {
  messages: Message[];
  timeline: ToolEvent[];
  tool_outcomes: { call_id: string | null; status: ToolStatus }[];
  cwd: string;
  plan_mode: boolean;
  /** Older server snapshots only include plan_mode. */
  mode?: SessionMode;
  tasks: { content: string; status: "pending" | "in_progress" | "done" }[];
  pending_permission: Permission | null;
  allowed_tools: string[];
  changes: Change[];
  undo_count: number;
  usage: {
    prompt: number;
    completion: number;
    cost: number | null;
    context: number;
    max_context: number;
    cost_summary: {
      known_cost: string | null;
      unknown_prompt_tokens: number;
      unknown_completion_tokens: number;
      unknown_requests: number;
      missing_usage_requests: number;
    };
  };
}
export interface Bootstrap {
  token: string;
  version: string;
  workspace: string;
  project: string;
  model: string;
  provider: string;
  base_url: string | null;
  key_configured: boolean;
  tools: string[];
  mcp_tools: string[];
  hooks: { pre: number; post: number };
  sessions: SessionSummary[];
}
