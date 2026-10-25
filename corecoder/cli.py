"""Interactive REPL - the user-facing terminal interface."""

import argparse
import json
import os
import sys

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from . import __version__
from .agent import Agent
from .config import Config
from .hooks import load_hooks
from .llm import LLM, LiteLLM
from .mcp import load_mcp_tools
from .permissions import DEFAULT_STORE, Permission
from .session import _normalize_session_id, list_sessions, load_session, save_session
from .state import SessionState
from .tools import create_tools

console = Console()
err_console = Console(stderr=True)


def _parse_args():
    p = argparse.ArgumentParser(
        prog="corecoder",
        description="Minimal AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Model name (default: $CORECODER_MODEL or gpt-5.5)")
    p.add_argument("--base-url", help="API base URL (default: $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (default: $OPENAI_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument("--yes", action="store_true", help="Auto-approve every tool call (for scripts and CI)")
    p.add_argument("--demo", action="store_true", help="Run the offline scripted demo (no API key needed)")
    p.add_argument("--json", action="store_true", help="Emit one structured JSON result")
    p.add_argument("--max-rounds", type=int, default=50, help="Maximum model/tool rounds (>=1)")
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args()


def main():
    args = _parse_args()

    max_rounds = getattr(args, "max_rounds", 50)
    if isinstance(max_rounds, bool) or max_rounds < 1:
        raise SystemExit(2)

    if args.demo:
        from .demo import run_demo

        raise SystemExit(run_demo())

    config = Config.from_env()

    # CLI args override env vars
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key

    if not config.api_key:
        if args.prompt and getattr(args, "json", False):
            from .results import RunResult

            print(
                json.dumps(
                    RunResult(
                        "failed",
                        "",
                        0,
                        [{"call_id": None, "name": None, "status": "failed", "stage": "config", "message": "No API key found."}],
                        {},
                        "missing_api_key",
                    ).as_dict(),
                    ensure_ascii=False,
                )
            )
            raise SystemExit(1)
        console.print("[red bold]No API key found.[/]")
        console.print(
            "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or CORECODER_API_KEY\n"
            "\nExamples:\n"
            "  # OpenAI\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  # DeepSeek\n"
            "  export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com\n"
            "\n"
            "  # Ollama (local)\n"
            "  export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 CORECODER_MODEL=qwen2.5-coder\n"
        )
        sys.exit(1)

    llm_cls = LiteLLM if config.provider == "litellm" else LLM
    llm = llm_cls(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    # consent layer: ask in the REPL, refuse in one-shot mode, --yes skips it
    if args.yes:
        permission = Permission(allow_all=True)
    elif args.prompt:
        permission = Permission()
    else:
        permission = Permission(ask=_ask_permission, persist_path=DEFAULT_STORE)
    session_id = _normalize_session_id(args.resume)
    state = SessionState(session_id=session_id)
    tools = [*create_tools(state), *load_mcp_tools()]
    agent_kwargs = {
        "llm": llm,
        "tools": tools,
        "max_context_tokens": config.max_context_tokens,
        "permission": permission,
        "hooks": load_hooks(),
        "state": state,
    }
    if max_rounds != 50:
        agent_kwargs["max_rounds"] = max_rounds
    agent = Agent(**agent_kwargs)

    # resume saved session
    if args.resume:
        loaded = load_session(args.resume)
        if loaded:
            agent.messages, loaded_model = loaded
            # restore the model from the saved session unless overridden by CLI
            if not args.model:
                agent.llm.model = loaded_model
                config.model = loaded_model
            err_console.print(f"[green]Resumed session: {args.resume} (model: {agent.llm.model})[/green]")
        else:
            if args.prompt and getattr(args, "json", False):
                from .results import RunResult

                print(
                    json.dumps(
                        RunResult(
                            "failed",
                            "",
                            0,
                            [
                                {
                                    "call_id": None,
                                    "name": None,
                                    "status": "failed",
                                    "stage": "session",
                                    "message": f"Session '{args.resume}' not found.",
                                }
                            ],
                            {},
                            "session_not_found",
                        ).as_dict(),
                        ensure_ascii=False,
                    )
                )
                raise SystemExit(1)
            err_console.print(f"[red]Session '{args.resume}' not found.[/red]")
            sys.exit(1)

    # one-shot mode
    if args.prompt:
        raise SystemExit(_run_once(agent, args.prompt, json_output=args.json))

    # interactive REPL
    _repl(agent, config)


def _ask_permission(tool_name: str, arguments: dict) -> str:
    """REPL consent prompt. Anything but a clear yes counts as a no."""
    console.print(f"\n[bold yellow]permission requested:[/] [cyan]{tool_name}[/cyan]({_brief(arguments)})")
    try:
        answer = pt_prompt("  [y] allow once  [a] always allow this tool  [n] deny: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]denied[/dim]")
        return "deny"
    if answer in ("y", "yes"):
        return "once"
    if answer in ("a", "always"):
        return "always"
    return "deny"


def _run_once(agent: Agent, prompt: str, json_output: bool = False):
    """Non-interactive: run one prompt and exit."""
    perm = agent.permission
    if perm is not None and perm.ask is None and not perm.allow_all:
        err_console.print("[dim]one-shot mode: mutating tools are refused unless you pass --yes[/dim]")

    streamed = []

    def on_token(tok):
        streamed.append(tok)
        if not json_output:
            print(tok, end="", flush=True)

    def on_tool(name, kwargs):
        err_console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

    def on_reasoning(tok):
        # thinking goes to stderr so piped stdout stays the plain answer
        err_console.print(f"[dim]{tok}[/dim]", end="")

    try:
        result = agent.chat_result(prompt, on_token=on_token, on_tool=on_tool, on_reasoning=on_reasoning)
    except Exception as e:  # noqa: BLE001 - defensive wrapper for non-Agent test doubles
        from .results import RunResult

        result = RunResult(
            "failed",
            "",
            0,
            [{"call_id": None, "name": None, "status": "failed", "stage": "loop", "message": str(e)}],
            {},
            "failed",
        )
    if json_output:
        print(json.dumps(result.as_dict(), ensure_ascii=False))
    elif result.text and not streamed:
        print(result.text)
    elif streamed:
        print()
    if result.status != "completed":
        err_console.print(f"{result.finish_reason}")
    return result.exit_code


def _repl(agent: Agent, config: Config):
    """Interactive read-eval-print loop."""
    perm = agent.permission
    mode = "auto-approve every tool call (--yes)" if (perm and perm.allow_all) else "ask before mutating tools"
    mcp_count = sum(1 for t in agent.tools if t.name.startswith("mcp__"))
    console.print(
        Panel(
            f"[bold]CoreCoder[/bold] v{__version__}\n"
            f"Model: [cyan]{config.model}[/cyan]"
            + (f"  Base: [dim]{config.base_url}[/dim]" if config.base_url else "")
            + f"\nPermissions: [cyan]{mode}[/cyan]"
            + (
                f"\nHooks: [cyan]{len(agent.hooks.pre)} pre, {len(agent.hooks.post)} post[/cyan] from ~/.corecoder/hooks.json"
                if agent.hooks
                else ""
            )
            + (f"\nMCP: [cyan]{mcp_count} tools[/cyan] from ~/.corecoder/mcp.json" if mcp_count else "")
            + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit.",
            border_style="blue",
        )
    )

    hist_path = os.path.expanduser("~/.corecoder_history")
    history = FileHistory(hist_path)

    # Enter submits, Escape+Enter inserts a newline (for pasting code blocks etc.)
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    while True:
        try:
            user_input = pt_prompt(
                "You (plan) > " if agent.plan_mode else "You > ",
                history=history,
                multiline=True,
                key_bindings=kb,
                prompt_continuation="...  ",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        if not user_input:
            continue

        # built-in commands
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/reset":
            agent.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue
        if user_input == "/plan":
            agent.plan_mode = not agent.plan_mode
            if agent.plan_mode:
                console.print(
                    "[yellow]Plan mode on.[/yellow] The agent can look but not touch: it will "
                    "investigate read-only and present a plan. Type [bold]approve[/bold] to "
                    "accept the plan, or [bold]/plan[/bold] again to exit."
                )
            else:
                console.print("[yellow]Plan mode off.[/yellow]")
            continue
        if agent.plan_mode and user_input.lower() in ("approve", "/approve"):
            agent.plan_mode = False
            console.print("[yellow]Plan mode off.[/yellow]")
            user_input = "approve"  # the approval itself goes to the model, which then executes
        if user_input == "/tokens":
            p = agent.llm.total_prompt_tokens
            c = agent.llm.total_completion_tokens
            line = f"Tokens: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p + c}[/bold] total"
            cost = agent.llm.estimated_cost
            if cost is not None:
                line += f"  (~${cost:.4f})"
            summary = getattr(agent.llm, "cost_summary", None)
            if summary is not None:
                line += (
                    f"  known subtotal=${summary.known_cost:.4f};"
                    f" unknown requests={summary.unknown_requests},"
                    f" missing usage={summary.missing_usage_requests}"
                )
            console.print(line)
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            try:
                new_model = user_input[7:].strip() if user_input.startswith("/model ") else ""
                if new_model:
                    agent.llm.model = new_model
                    config.model = new_model
                    console.print(f"Switched to [cyan]{new_model}[/cyan]")
                else:
                    console.print(f"Current model: [cyan]{config.model}[/cyan]")
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Error: {e}[/red]")
            continue
        if user_input == "/compact":
            try:
                from .context import estimate_tokens, output_reserve_for

                before = estimate_tokens(agent.messages)
                full_messages = agent._full_messages() if hasattr(agent, "_full_messages") else []
                system = full_messages[0].get("content", "") if full_messages else getattr(agent, "_system", "")
                tools = agent._tool_schemas() if hasattr(agent, "_tool_schemas") else []
                state = getattr(agent, "state", None)
                result = agent.context.prepare_request(
                    agent.messages,
                    system=system,
                    tools=tools,
                    output_reserve=output_reserve_for(agent.llm, 4096),
                    artifact_dir=getattr(state, "artifact_dir", None),
                    llm=agent.llm,
                )
                if result.budget_ok and result.changed:
                    agent.messages[:] = result.messages
                compressed = result.budget_ok and result.changed
                after = estimate_tokens(agent.messages)
                if not result.budget_ok:
                    console.print(f"[red]Compression refused: {result.reason or 'context budget exceeded'}[/red]")
                elif compressed:
                    console.print(f"[green]Compressed: {before} → {after} tokens ({len(agent.messages)} messages)[/green]")
                else:
                    console.print(f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]")
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Error: {e}[/red]")
            continue
        if user_input == "/save":
            try:
                state = getattr(agent, "state", None)
                sid = save_session(agent.messages, config.model, getattr(state, "session_id", None))
                console.print(f"[green]Session saved: {sid}[/green]")
                console.print(f"Resume with: corecoder -r {sid}")
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Error: {e}[/red]")
            continue
        if user_input == "/diff":
            changed_files = set(getattr(getattr(agent, "state", None), "changed_files", set()))
            if not changed_files:
                console.print("[dim]No files modified this session.[/dim]")
            else:
                console.print(f"[bold]Files modified this session ({len(changed_files)}):[/bold]")
                for f in sorted(changed_files):
                    console.print(f"  [cyan]{f}[/cyan]")
            continue
        if user_input == "/undo" or user_input == "/undo --force":
            from .checkpoints import pending, preview, undo, use_store

            try:
                agent_store = getattr(getattr(agent, "state", None), "checkpoint_store", None)
                if agent_store is not None:
                    # Bind compatibility wrappers to this state only; the
                    # store-scoped lock prevents cross-session contamination.
                    with use_store(agent_store):
                        if user_input == "/undo --force":
                            target = preview()
                            if target is None:
                                console.print("Nothing to undo.")
                                continue
                            console.print(f"Force undo target: [cyan]{target['path']}[/cyan]")
                            console.print("This may overwrite changes made after the agent write.")
                            confirm = pt_prompt("Confirm force undo? [y/N] ").strip().lower()
                            if confirm not in {"y", "yes"}:
                                console.print("Force undo cancelled.")
                                continue
                            console.print(undo(force=True))
                        else:
                            console.print(undo())
                        left = pending()
                else:
                    with use_store(None):
                        console.print(undo(force=user_input == "/undo --force"))
                        left = pending()
                if left:
                    console.print(f"[dim]{left} more checkpoint(s) on the stack.[/dim]")
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Error: {e}[/red]")
            continue
        if user_input == "/sessions":
            try:
                sessions = list_sessions()
                if not sessions:
                    console.print("[dim]No saved sessions.[/dim]")
                else:
                    for s in sessions:
                        console.print(f"  [cyan]{s['id']}[/cyan] ({s['model']}, {s['saved_at']}) {s['preview']}")
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Error: {e}[/red]")
            continue

        # an unknown /command shouldn't be sent to the model as a prompt
        if user_input.startswith("/"):
            console.print(f"[yellow]Unknown command: {user_input.split()[0]} (try /help)[/yellow]")
            continue

        # call the agent
        streamed: list[str] = []

        def on_token(tok, streamed=streamed):
            streamed.append(tok)
            print(tok, end="", flush=True)

        def on_tool(name, kwargs):
            console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

        def on_reasoning(tok, streamed=streamed):
            # dim the thinking inline; mark the stream non-empty so the final
            # newline logic below still fires even if the reply itself is empty
            streamed.append(tok)
            console.print(f"[dim]{tok}[/dim]", end="")

        try:
            response = agent.chat(user_input, on_token=on_token, on_tool=on_tool, on_reasoning=on_reasoning)
            if getattr(getattr(agent, "last_result", None), "status", None) == "max_rounds":
                console.print("\n[yellow]max_rounds: tool loop reached its configured limit.[/yellow]")
            if streamed:
                print()  # newline after streamed tokens
            else:
                # response wasn't streamed (came after tool calls)
                console.print(Markdown(response))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:  # noqa: BLE001
            # keep the REPL alive no matter what chat() throws
            console.print(f"\n[red]Error: {e}[/red]")


def _show_help():
    console.print(
        Panel(
            "[bold]Commands:[/bold]\n"
            "  /help          Show this help\n"
            "  /reset         Clear conversation history\n"
            "  /model         Show current model\n"
            "  /model <name>  Switch model mid-conversation\n"
            "  /tokens        Show token usage\n"
            "  /compact       Compress conversation context\n"
            "  /diff          Show files modified this session\n"
            "  /undo          Revert the most recent file change\n"
            "  /undo --force  Revert after an explicit conflict override\n"
            "  /plan          Toggle plan mode: read-only, then a plan to approve\n"
            "  /save          Save session to disk\n"
            "  /sessions      List saved sessions\n"
            "  quit           Exit CoreCoder\n"
            "\n"
            "[bold]Input:[/bold]\n"
            "  Enter          Submit message\n"
            "  Esc+Enter      Insert newline (for pasting code)",
            title="CoreCoder Help",
            border_style="dim",
        )
    )


def _brief(kwargs: dict, maxlen: int = 80) -> str:
    s = ", ".join(f"{k}={repr(v)[:40]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
