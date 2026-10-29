"""Core agent loop with validated, exactly-once tool outcomes."""

import concurrent.futures
import inspect
from contextlib import nullcontext

from .context import ContextBudgetExceeded, ContextManager, outcome_status, output_reserve_for
from .permissions import Permission
from .prompt import PLAN_MODE_PROMPT, system_prompt
from .results import RunResult
from .state import SessionState
from .tools import create_tools
from .tools.agent import AgentTool
from .tools.base import Tool, ToolOutcome
from .tools.todo import TodoWriteTool


class Agent:
    def __init__(self, llm, tools=None, max_context_tokens=128_000, max_rounds=50, permission=None, hooks=None, state=None):
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
            raise ValueError("max_rounds must be an integer >= 1")
        self.llm, self.state = llm, state or SessionState()
        # The tool retains this token, so a freed Agent's address cannot be reused as its owner.
        self._tool_owner_token = object()
        if tools is None:
            self.tools = create_tools(self.state)
        else:
            prepared = []
            for tool in tools:
                owner = getattr(tool, "_corecoder_owner", None)
                if owner is None and getattr(tool, "state", None) is None and not getattr(tool, "stateless", False):
                    tool._corecoder_owner = self._tool_owner_token
                elif owner is not None and owner is not self._tool_owner_token:
                    raise ValueError(f"custom tool {tool.name!r} has an existing owner and cannot be reused")
                tool_state = getattr(tool, "state", None)
                if tool_state is not None and tool_state is not self.state:
                    clone = getattr(tool, "clone_for_state", None)
                    if clone is None:
                        raise ValueError(f"stateful tool {tool.name!r} cannot be reused; implement clone_for_state(state)")
                    tool = clone(self.state)
                prepared.append(tool)
            self.tools = prepared
        for tool in self.tools:
            if not isinstance(tool, Tool):
                raise TypeError(f"invalid tool {tool!r}")
            tool.validate_schema()
        self.permission, self.hooks = permission, hooks
        self._tool_by_name = {t.name: t for t in self.tools}
        self.messages, self.context = [], ContextManager(max_tokens=max_context_tokens)
        self.max_rounds, self._system = max_rounds, system_prompt(self.tools, self.state)
        self.plan_mode = False
        self._tool_execution_cwds, self._tool_result_cache = {}, {}
        self.last_result = None
        self._interrupt_seen = False
        for t in self.tools:
            if isinstance(t, AgentTool):
                t.bind_parent(self)
        self._todo = next((t for t in self.tools if isinstance(t, TodoWriteTool)), None)

    def _full_messages(self):
        system = system_prompt(self.tools, self.state)
        if self.plan_mode:
            system += "\n\n" + PLAN_MODE_PROMPT
        if self._todo is not None:
            rendered = self._todo.render()
            if rendered:
                system += "\n\n# Current task list\n" + rendered
        return [{"role": "system", "content": system}] + self.messages

    def _tool_schemas(self):
        return [t.schema() for t in self.tools]

    @staticmethod
    def _invoke(fn, *args, cwd=None):
        try:
            sig = inspect.signature(fn)
            accepts = "cwd" in sig.parameters or any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
        except (TypeError, ValueError):
            accepts = False
        return fn(*args, **({"cwd": str(cwd)} if accepts and cwd is not None else {}))

    def _validate_call_structure(self, calls):
        ids = [getattr(c, "id", None) for c in calls]
        if any(not isinstance(i, str) or not i.strip() for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("invalid tool response: tool call IDs must be non-empty and unique")
        if any(not isinstance(getattr(c, "name", None), str) or not c.name.strip() for c in calls):
            raise ValueError("invalid tool response: tool names must be non-empty")

    def _run_tool_call(self, tc, on_tool=None):
        name, args = getattr(tc, "name", ""), getattr(tc, "arguments", None)
        self._tool_execution_cwds[tc.id] = self.state.cwd
        tool = self._tool_by_name.get(name)
        if tool is None:
            outcome = ToolOutcome(tc.id, name, "failed", f"Error: unknown_tool: unknown tool '{name}'", "validate")
            self._tool_result_cache[tc.id] = outcome
            return outcome
        raw_error = getattr(tc, "arguments_error", None)
        try:
            if raw_error:
                outcome = ToolOutcome(tc.id, name, "failed", f"Error: invalid_arguments {raw_error}", "validate")
                self._tool_result_cache[tc.id] = outcome
                return outcome
            error = tool.validate_arguments(args)
            if error:
                outcome = ToolOutcome(tc.id, name, "failed", f"Error: invalid_arguments {error}", "validate")
                self._tool_result_cache[tc.id] = outcome
                return outcome
            # JSON Schema may intentionally omit Python-callable requirements
            # (for example a custom tool with an underspecified schema).  Check
            # the concrete execute signature before display, hooks, or
            # authorization so these are reported as parameter errors without
            # triggering side effects.  Some extension callables do not expose
            # a signature; the execution-side guard remains the fallback.
            try:
                signature = inspect.signature(tool.execute)
            except (TypeError, ValueError):
                signature = None
            if signature is not None:
                try:
                    signature.bind(**args)
                except TypeError as exc:
                    outcome = ToolOutcome(tc.id, name, "failed", f"Error: invalid_arguments {exc}", "validate")
                    self._tool_result_cache[tc.id] = outcome
                    return outcome
        except Exception as exc:  # noqa: BLE001
            outcome = ToolOutcome(tc.id, name, "failed", f"Error: invalid_arguments {exc}", "validate")
            self._tool_result_cache[tc.id] = outcome
            return outcome
        try:
            if on_tool:
                on_tool(name, args)
        except KeyboardInterrupt:
            self._interrupt_seen = True
            return ToolOutcome(tc.id, name, "cancelled", "Error: tool execution cancelled before start", "display")
        except Exception as exc:  # noqa: BLE001
            return ToolOutcome(tc.id, name, "failed", f"Error: display failed: {exc}", "display")
        try:
            try:
                blocked = self._pre_hooks(tc)
            except KeyboardInterrupt:
                self._interrupt_seen = True
                return ToolOutcome(tc.id, name, "cancelled", "Error: tool execution cancelled before start", "pre")
            except Exception as exc:  # noqa: BLE001
                return ToolOutcome(tc.id, name, "failed", f"Error: pre hook failed: {exc}", "pre")
            if blocked is not None:
                return ToolOutcome(tc.id, name, "refused", blocked, "pre")
            try:
                blocked = self._permit(tc)
            except KeyboardInterrupt:
                self._interrupt_seen = True
                return ToolOutcome(tc.id, name, "cancelled", "Error: tool execution cancelled before start", "permission")
            except Exception as exc:  # noqa: BLE001
                return ToolOutcome(tc.id, name, "failed", f"Error: permission failed: {exc}", "permission")
            if blocked is not None:
                return ToolOutcome(tc.id, name, "refused", blocked, "permission")
            result = self._exec_tool(tc)
            status = outcome_status(result)
            outcome = ToolOutcome(
                tc.id,
                name,
                "failed" if status == "failed" else ("refused" if status == "refused" else "succeeded"),
                result,
                "execute",
            )
            self._tool_result_cache[tc.id] = outcome
            try:
                self._post_hooks(tc, outcome.content)
            except KeyboardInterrupt as exc:
                self._interrupt_seen = True
                outcome.warning = f"post hook interrupted: {exc}"
                outcome.content += "\nWarning: post hook interrupted"
            except Exception as exc:  # noqa: BLE001
                outcome.warning = f"post hook failed: {exc}"
                outcome.content += f"\nWarning: post hook failed: {exc}"
            return outcome
        except KeyboardInterrupt:
            self._interrupt_seen = True
            outcome = ToolOutcome(tc.id, name, "unknown", "Error: tool execution interrupted; result is unknown", "execute")
            self._tool_result_cache[tc.id] = outcome
            return outcome
        except Exception as exc:  # noqa: BLE001
            return ToolOutcome(tc.id, name, "failed", f"Error: {exc}", "execute")

    def _run_turn(self, on_token=None, on_tool=None, on_reasoning=None):
        prepared = self.context.prepare_request(
            self.messages,
            system=self._full_messages()[0]["content"],
            tools=self._tool_schemas(),
            output_reserve=output_reserve_for(self.llm, 4096),
            artifact_dir=getattr(self.state, "artifact_dir", None),
            llm=self.llm,
        )
        if not prepared.budget_ok:
            raise ContextBudgetExceeded(prepared.reason or "context_budget_exceeded")
        self.messages[:] = prepared.messages
        self._turn_started = True
        return self.llm.chat(
            messages=self._full_messages(), tools=self._tool_schemas(), on_token=on_token, on_reasoning=on_reasoning
        )

    def chat_result(self, user_input, on_token=None, on_tool=None, on_reasoning=None):
        cm = self.llm.run_scope() if hasattr(self.llm, "run_scope") else nullcontext(None)
        with cm as scope:
            result = self._chat_result_unscoped(user_input, on_token, on_tool, on_reasoning)
            if scope is not None:
                result.usage = scope.as_dict()
            return result

    def _chat_result_unscoped(self, user_input, on_token=None, on_tool=None, on_reasoning=None):
        self.messages.append({"role": "user", "content": user_input})
        start_p, start_c = getattr(self.llm, "total_prompt_tokens", 0) or 0, getattr(self.llm, "total_completion_tokens", 0) or 0
        errors, text_parts, rounds = [], [], 0
        self._interrupt_seen = False
        self._turn_started = False
        try:
            for _ in range(self.max_rounds):
                self._turn_started = False
                calls = None
                resp = self._run_turn(on_token, on_tool, on_reasoning)
                rounds += 1
                self._turn_started = False
                calls = list(getattr(resp, "tool_calls", []) or [])
                if getattr(resp, "content", None):
                    text_parts.append(str(resp.content))
                if not calls:
                    text = "".join(text_parts)
                    self.messages.append(resp.message)
                    result = RunResult("completed", text, rounds, errors, self._usage(start_p, start_c), "completed")
                    self.last_result = result
                    return result
                envelope_invalid = False
                try:
                    self._validate_call_structure(calls)
                except ValueError:
                    envelope_invalid = True
                    raise
                self.messages.append(resp.message)
                self._tool_result_cache.clear()
                self._tool_execution_cwds.clear()
                answered_ids = set()
                record_failures = []
                outcomes = self._exec_tools_parallel(calls, on_tool, return_outcomes=True)
                for tc, outcome in zip(calls, outcomes):
                    execution_cwd = self._tool_execution_cwds.get(tc.id, self.state.cwd)
                    raw_args = getattr(tc, "arguments", {})
                    safe_args = raw_args if isinstance(raw_args, dict) else {}
                    record_error = self._record_outcome_safe(tc, outcome, safe_args, execution_cwd)
                    if record_error:
                        record_failures.append(record_error)
                        errors.append(
                            {
                                "call_id": tc.id,
                                "name": tc.name,
                                "status": "failed",
                                "stage": "record",
                                "message": str(record_error),
                            }
                        )
                    if outcome.as_error():
                        errors.append(
                            {
                                "call_id": tc.id,
                                "name": tc.name,
                                "status": outcome.status,
                                "stage": outcome.stage,
                                "message": outcome.message,
                            }
                        )
                    self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": outcome.content})
                    self._tool_result_cache[tc.id] = outcome.content
                    answered_ids.add(tc.id)
                if self._interrupt_seen:
                    raise KeyboardInterrupt
                if record_failures:
                    raise RuntimeError(f"outcome record failed: {record_failures[0]}")
            result = RunResult("max_rounds", "".join(text_parts), rounds, errors, self._usage(start_p, start_c), "max_rounds")
        except KeyboardInterrupt:
            if calls is not None and not locals().get("envelope_invalid", False):
                self._answer_pending_tool_calls(calls, errors, answered=answered_ids if "answered_ids" in locals() else set())
            if self._turn_started:
                rounds += 1
            result = RunResult("cancelled", "".join(text_parts), rounds, errors, self._usage(start_p, start_c), "cancelled")
        except Exception as exc:  # noqa: BLE001
            if calls is not None and not locals().get("envelope_invalid", False):
                self._answer_pending_tool_calls(calls, errors, answered=answered_ids if "answered_ids" in locals() else set())
            if self._turn_started:
                rounds += 1
            result = RunResult(
                "failed",
                "".join(text_parts),
                rounds,
                errors + [{"call_id": None, "name": None, "status": "failed", "stage": "loop", "message": str(exc)}],
                self._usage(start_p, start_c),
                "failed",
                exc,
            )
        self.last_result = result
        return result

    def _usage(self, start_p, start_c):
        return {
            "prompt_tokens": max(0, (getattr(self.llm, "total_prompt_tokens", 0) or 0) - start_p),
            "completion_tokens": max(0, (getattr(self.llm, "total_completion_tokens", 0) or 0) - start_c),
        }

    def _record_outcome_safe(self, tc, outcome, arguments, execution_cwd):
        try:
            self.state.record_outcome(tc.id, tc.name, outcome.status, arguments, outcome.content, execution_cwd=execution_cwd)
        except Exception as exc:  # noqa: BLE001
            return exc
        return None

    def chat(self, user_input, on_token=None, on_tool=None, on_reasoning=None):
        result = self.chat_result(user_input, on_token, on_tool, on_reasoning)
        if result.status == "cancelled":
            raise KeyboardInterrupt
        if result.status == "failed":
            raise result.error or RuntimeError(result.tool_errors[-1]["message"] if result.tool_errors else "agent run failed")
        return result.text

    def _pre_hooks(self, tc):
        if self.hooks is None:
            return None
        return self._invoke(self.hooks.run_pre, tc.name, tc.arguments, cwd=self.state.cwd)

    def _post_hooks(self, tc, result):
        if self.hooks is not None:
            self._invoke(self.hooks.run_post, tc.name, tc.arguments, result, cwd=self.state.cwd)

    def _permit(self, tc):
        if self.plan_mode and tc.name not in Permission.READ_ONLY:
            return (
                "Plan mode is on, so this call was refused: plan mode is read-only. "
                "Do not retry it. Type approve or /plan to resume execution."
            )
        if self.permission is None:
            return None
        return self.permission.check(tc.name, tc.arguments)

    def _exec_tool(self, tc):
        tool = self._tool_by_name.get(tc.name)
        if tool is None:
            return f"Error: unknown_tool: unknown tool '{tc.name}'"
        try:
            inspect.signature(tool.execute).bind(**tc.arguments)
        except TypeError as exc:
            return f"Error: bad arguments for {tc.name}: {exc}"
        try:
            result = tool.execute(**tc.arguments)
            return result if isinstance(result, str) else str(result)
        except Exception as exc:  # noqa: BLE001
            return f"Error executing {tc.name}: {exc}"

    def _exec_tools_parallel(self, tool_calls, on_tool=None, return_outcomes=False):
        readonly = all(getattr(tc, "name", "") in {"read_file", "glob", "grep"} for tc in tool_calls)
        if readonly and len(tool_calls) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(tool_calls))) as pool:
                outcomes = list(pool.map(lambda tc: self._run_tool_call(tc, on_tool), tool_calls))
            return outcomes if return_outcomes else [o.content for o in outcomes]
        outcomes = []
        for i, tc in enumerate(tool_calls):
            outcomes.append(self._run_tool_call(tc, on_tool))
            if self._interrupt_seen:
                for pending in tool_calls[i + 1 :]:
                    outcomes.append(
                        ToolOutcome(
                            pending.id, pending.name, "cancelled", "Error: tool execution cancelled before start", "execute"
                        )
                    )
                break
        return outcomes if return_outcomes else [o.content for o in outcomes]

    def _answer_pending_tool_calls(self, tool_calls, errors=None, answered=None):
        if answered is None:
            last_assistant = max((i for i, m in enumerate(self.messages) if m.get("role") == "assistant"), default=-1)
            answered = {m.get("tool_call_id") for m in self.messages[last_assistant + 1 :] if m.get("role") == "tool"}
        for tc in tool_calls:
            if tc.id in answered:
                continue
            cached = self._tool_result_cache.get(tc.id)
            if isinstance(cached, ToolOutcome):
                content, status = cached.content, cached.status
            else:
                content = cached or "Error: tool execution cancelled before start"
                status = (
                    "cancelled"
                    if content.startswith("Error: tool execution cancelled")
                    else ("unknown" if content.startswith("Error: tool execution interrupted") else outcome_status(content))
                )
            self._record_outcome_safe(
                tc,
                ToolOutcome(tc.id, tc.name, status, content, "execute"),
                getattr(tc, "arguments", {}) if isinstance(getattr(tc, "arguments", {}), dict) else {},
                self.state.cwd,
            )
            self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
            if errors is not None:
                errors.append({"call_id": tc.id, "name": tc.name, "status": status, "stage": "execute", "message": content})

    def reset(self):
        self.messages.clear()
        if isinstance(self._todo, TodoWriteTool):
            self.state.todos.clear()
        self.state.execution_records.clear()
        self.state.task_memory = None
        self._tool_result_cache.clear()
        self._tool_execution_cwds.clear()
        self.last_result = None
