"""Managed shell processes with bounded, incremental output."""
import codecs
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from .shell import _git_bash, _posixify_drive_paths

MAX_OUTPUT = 2 * 1024 * 1024


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    status: str
    exit_code: int | None
    truncated: bool


def run_command(command, cwd, timeout=120, cancel=None, on_output=None):
    start = time.monotonic()
    bash = _git_bash()
    args = [bash, '-c', _posixify_drive_paths(command)] if bash else command
    kwargs = {'cwd': cwd, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
              'stdin': subprocess.DEVNULL, 'shell': bash is None}
    if os.name == 'nt':
        # The helper blocks before creating the shell. The command is released
        # only after the helper has joined the Job Object, so all descendants
        # inherit membership before any user command executes.
        helper = ("import json,subprocess,sys; "
                  "v=json.loads(sys.stdin.readline()); "
                  "sys.exit(subprocess.call(v['args'],shell=v['shell']))")
        # A Windows venv python.exe is a redirector that starts the real
        # interpreter before Popen returns. Assign the real interpreter itself.
        base_executable = getattr(sys, '_base_executable', None)
        if not base_executable:
            return CommandResult('', 'Unable to establish a reliable helper executable.', 'unknown', None, False)
        launch_args = [base_executable, '-c', helper]
        kwargs['shell'] = False
        kwargs['stdin'] = subprocess.PIPE
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        launch_args = args
    if os.name != 'nt':
        kwargs['start_new_session'] = True
    proc = subprocess.Popen(launch_args, **kwargs)
    job = None
    if os.name == 'nt':
        from .windows_job import attach_process
        job = attach_process(proc)
        if not job:
            proc.terminate()
            proc.wait(timeout=3)
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
            return CommandResult('', 'Unable to establish process ownership; command was not started.', 'unknown', None, False)
        early_status = 'cancelled' if cancel is not None and cancel.is_set() else (
            'timeout' if time.monotonic() - start >= timeout else None)
        if early_status:
            from .windows_job import close_job, terminate_job
            if not terminate_job(job):
                early_status = 'unknown'
            proc.wait(timeout=3)
            close_job(job)
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
            return CommandResult('', '', early_status, proc.returncode, False)
        try:
            payload = json.dumps({'args': args, 'shell': bash is None}) + '\n'
            proc.stdin.write(payload.encode('utf-8'))
            proc.stdin.close()
        except (OSError, ValueError):
            from .windows_job import close_job, terminate_job
            terminate_job(job)
            proc.wait(timeout=3)
            close_job(job)
            return CommandResult('', 'Unable to start managed command.', 'unknown', None, False)
    lock = threading.Lock()
    chunks = {'stdout': [], 'stderr': []}
    used = 0
    truncated = False
    sequence = 0
    closed = False
    output_queue = queue.Queue(maxsize=256)
    output_finished = threading.Event()

    def dispatch_output():
        while not output_finished.is_set():
            try:
                event = output_queue.get(timeout=.05)
            except queue.Empty:
                continue
            try:
                on_output(*event)
            except Exception:  # noqa: BLE001, S110 - display failures cannot affect execution
                pass

    output_thread = None
    if on_output:
        output_thread = threading.Thread(target=dispatch_output, daemon=True)
        output_thread.start()

    def read_pipe(name, pipe):
        nonlocal used, truncated, sequence
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        try:
            while True:
                data = os.read(pipe.fileno(), 4096)
                if not data:
                    text = decoder.decode(b'', final=True)
                else:
                    text = decoder.decode(data)
                if text:
                    with lock:
                        if closed:
                            continue
                        available = max(0, MAX_OUTPUT - used)
                        encoded = text.encode('utf-8')
                        kept = encoded[:available].decode('utf-8', 'ignore')
                        used += len(kept.encode('utf-8'))
                        if len(encoded) > len(kept.encode('utf-8')):
                            truncated = True
                        if kept:
                            chunks[name].append(kept)
                            sequence += 1
                            if on_output:
                                try:
                                    output_queue.put_nowait((name, kept, sequence))
                                except queue.Full:
                                    truncated = True
                if not data:
                    break
        except (OSError, ValueError):
            pass
        finally:
            pipe.close()

    threads = [threading.Thread(target=read_pipe, args=(name, pipe), daemon=True)
               for name, pipe in (('stdout', proc.stdout), ('stderr', proc.stderr))]
    for thread in threads:
        thread.start()
    status = 'exited'
    interrupted = False
    try:
        while True:
            active = proc.poll() is None or any(thread.is_alive() for thread in threads)
            if job:
                from .windows_job import active_processes
                try:
                    active = active or active_processes(job) > 0
                except OSError:
                    status = 'unknown'
                    break
            if not active:
                break
            if cancel is not None and cancel.is_set():
                status = 'cancelled'
                break
            if time.monotonic() - start >= timeout:
                status = 'timeout'
                break
            time.sleep(.05)
    except KeyboardInterrupt:
        status = 'cancelled'
        interrupted = True
    finally:
        if status != 'exited':
            try:
                if os.name == 'nt':
                    if job:
                        from .windows_job import terminate_job
                        if not terminate_job(job):
                            status = 'unknown'
                        else:
                            from .windows_job import active_processes
                            deadline = time.monotonic() + 3
                            while active_processes(job) and time.monotonic() < deadline:
                                time.sleep(.02)
                            if active_processes(job):
                                status = 'unknown' 
                    else:
                        killed = subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], check=False,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
                        if killed.returncode or proc.poll() is not None:
                            status = 'unknown'
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                status = 'unknown'
        if job:
            from .windows_job import close_job
            if not close_job(job):
                status = 'unknown'
        for thread in threads:
            thread.join(timeout=.5)
        if any(thread.is_alive() for thread in threads):
            status = 'unknown'
        with lock:
            closed = True
        if output_thread and status == 'exited':
            deadline = time.monotonic() + .2
            while not output_queue.empty() and time.monotonic() < deadline:
                time.sleep(.005)
            output_finished.set()
            output_thread.join(timeout=max(0, deadline - time.monotonic()))
            if output_thread.is_alive() or not output_queue.empty():
                truncated = True
        else:
            output_finished.set()
            if output_thread and (output_thread.is_alive() or not output_queue.empty()):
                truncated = True
    if interrupted:
        # Preserve cancellation even if process cleanup could not be verified.
        error = KeyboardInterrupt()
        error.command_status = status
        raise error
    return CommandResult(''.join(chunks['stdout']), ''.join(chunks['stderr']), status, proc.poll(), truncated)
