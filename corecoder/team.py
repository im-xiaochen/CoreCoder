"""Validated, bounded execution primitives for read-only agent teams."""

import signal
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import ContextVar, copy_context
from threading import Event, Lock, current_thread, main_thread
from uuid import uuid4

from .budget import BudgetExceeded

_TEAM_STOP = ContextVar("corecoder_team_stop", default=None)

class TeamStop:
    def __init__(self, check_cancel):
        self.event = Event()
        self.lock = Lock()
        self.reason = None
        self.check_cancel = check_cancel
        self.signal_cancelled = False

    def set(self, reason):
        with self.lock:
            if self.reason is None:
                self.reason = reason
                self.event.set()


def check_team_stop():
    stop = _TEAM_STOP.get()
    if stop is not None:
        if stop.signal_cancelled:
            stop.set("cancelled")
        if stop.check_cancel is not None:
            try:
                stop.check_cancel()
            except KeyboardInterrupt:
                stop.set("cancelled")
        if stop.event.is_set():
            if stop.reason == "cancelled":
                raise KeyboardInterrupt
            raise BudgetExceeded("team budget exhausted")


def in_team():
    return _TEAM_STOP.get() is not None


def validate_tasks(tasks):
    """Validate the complete request before any member is launched."""
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 8:
        raise ValueError("tasks must contain between 1 and 8 items")
    seen = set()
    for item in tasks:
        if not isinstance(item, dict) or not {"name", "task"} <= set(item) or set(item) - {"name", "task", "depends_on"}:
            raise ValueError("each task must contain name, task and optional depends_on")
        name, task = item["name"], item["task"]
        if not isinstance(name, str) or not name.strip() or name in seen:
            raise ValueError("task names must be nonempty and unique")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be nonempty")
        seen.add(name)
    graph = {}
    for item in tasks:
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            raise ValueError("depends_on must be an array of names")
        if len(set(deps)) != len(deps) or item["name"] in deps or any(dep not in seen for dep in deps):
            raise ValueError("invalid dependencies")
        graph[item["name"]] = deps
    visiting, visited = set(), set()
    def visit(name):
        if name in visiting:
            raise ValueError("cyclic dependencies")
        if name not in visited:
            visiting.add(name)
            for dep in graph[name]:
                visit(dep)
            visiting.remove(name)
            visited.add(name)
    for name in graph:
        visit(name)
    return tasks


def run_team(tasks, run_member, on_status=None, check_cancel=None):
    """Run validated tasks in declaration order with independent contexts.

    The callback receives a task item; its return value is collected unchanged.
    Exceptions propagate to the caller after executor cleanup.
    """
    validate_tasks(tasks)
    results = [None] * len(tasks)
    ids = [uuid4().hex for _ in tasks]
    team_id = uuid4().hex
    def publish(index, status, result=None):
        if on_status is not None:
            on_status({"team_id": team_id, "task_id": ids[index], "name": tasks[index]["name"],
                       "depends_on": tasks[index].get("depends_on", []), "status": status,
                       "reason": (result or {}).get("reason"), "summary": (result or {}).get("summary", "")[:2000],
                       "evidence": (result or {}).get("evidence", []), "usage": (result or {}).get("usage", {}),
                       "artifact_error": (result or {}).get("artifact_error")})
    for index in range(len(tasks)):
        publish(index, "pending")
    stop = TeamStop(check_cancel)
    token = _TEAM_STOP.set(stop)
    previous_handler = None
    if current_thread() is main_thread() and signal.getsignal(signal.SIGINT) is signal.default_int_handler:
        previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda signum, frame: setattr(stop, "signal_cancelled", True))

    def invoke(item):
        try:
            check_team_stop()
            result = run_member(item)
            if result.get("reason") == "budget_exceeded":
                stop.set("budget_exceeded")
            return result
        except BudgetExceeded:
            stop.set("budget_exceeded")
            return {"name": item["name"], "status": "failed", "reason": "budget_exceeded",
                    "summary": "", "evidence": [], "usage": {}}
        except KeyboardInterrupt:
            return {"name": item["name"], "status": "cancelled", "reason": "cancelled",
                    "summary": "", "evidence": [], "usage": {}}
        except Exception as exc:  # noqa: BLE001 - isolate member failures
            return {"name": item["name"], "status": "failed", "reason": str(exc),
                    "summary": "", "evidence": [], "usage": {}}

    try:
        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as pool:
            pending = {}
            remaining = set(range(len(tasks)))
            indices = {item["name"]: i for i, item in enumerate(tasks)}
            while pending or remaining:
                try:
                    try:
                        check_team_stop()
                    except (BudgetExceeded, KeyboardInterrupt) as exc:
                        if isinstance(exc, KeyboardInterrupt):
                            stop.set("cancelled")
                        for future in pending:
                            future.cancel()
                        for index in sorted(remaining):
                            results[index] = {"name": tasks[index]["name"], "id": ids[index], "status": "cancelled",
                                              "reason": stop.reason, "summary": "", "evidence": [], "usage": {}}
                            publish(index, "cancelled", results[index])
                        remaining.clear()
                    for index in sorted(remaining):
                        if len(pending) >= 4:
                            break
                        deps = tasks[index].get("depends_on", [])
                        upstream = [results[indices[name]] for name in deps]
                        if any(result is None for result in upstream):
                            continue
                        failed = [name for name, result in zip(deps, upstream) if result["status"] != "completed"]
                        remaining.remove(index)
                        if failed:
                            results[index] = {"name": tasks[index]["name"], "id": ids[index], "status": "skipped",
                                              "reason": "blocked by " + ", ".join(failed),
                                              "summary": "", "evidence": [], "usage": {}}
                            publish(index, "skipped", results[index])
                            continue
                        if stop.event.is_set() or stop.signal_cancelled:
                            remaining.add(index)
                            break
                        item = dict(tasks[index])
                        item["_id"] = ids[index]
                        if upstream:
                            import json
                            evidence = [{"name": result["name"], "summary": result.get("summary", "")[:2000],
                                         "evidence": result.get("evidence", [])} for result in upstream]
                            item["task"] += "\n\nDirect dependency reports:\n" + json.dumps(evidence, ensure_ascii=False)
                        try:
                            future = pool.submit(copy_context().run, invoke, item)
                        except KeyboardInterrupt:
                            stop.set("cancelled")
                            remaining.add(index)
                            break
                        pending[future] = index
                        publish(index, "running")
                    if not pending:
                        continue
                    try:
                        finished, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                    except KeyboardInterrupt:
                        stop.set("cancelled")
                        continue
                    for future in finished:
                        index = pending.pop(future)
                        if future.cancelled():
                            results[index] = {"name": tasks[index]["name"], "id": ids[index], "status": "cancelled",
                                              "reason": stop.reason, "summary": "", "evidence": [], "usage": {}}
                        else:
                            results[index] = future.result()
                            results[index].setdefault("id", ids[index])
                            if results[index].get("reason") == "budget_exceeded":
                                stop.set("budget_exceeded")
                        publish(index, results[index]["status"], results[index])
                except KeyboardInterrupt:
                    stop.set("cancelled")
                    remaining.update(i for i, result in enumerate(results)
                                     if result is None and i not in pending.values())
                    continue
        if stop.reason == "budget_exceeded":
            error = BudgetExceeded("team budget exhausted")
            error.team_results = results
            raise error
        if stop.reason == "cancelled":
            raise KeyboardInterrupt
        return results
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
        _TEAM_STOP.reset(token)
