"""Smoke test the installed console command and bundled workbench assets."""

import json
import os
import re
import subprocess
import sysconfig
import tempfile
from pathlib import Path
from queue import Queue
from threading import Thread
from urllib.request import urlopen


def main():
    executable = Path(sysconfig.get_path("scripts")) / ("corecoder.exe" if os.name == "nt" else "corecoder")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        home = root / "home"
        home.mkdir()
        env = {name: value for name, value in os.environ.items() if not name.startswith("CORECODER_")}
        key = "workbench-smoke-test-key"
        env.update(HOME=str(home), USERPROFILE=str(home), PATH=str(executable.parent),
                   PYTHONIOENCODING="utf-8", CORECODER_API_KEY=key)
        with subprocess.Popen(
            [str(executable), "web", "--port", "0"],
            cwd=workspace, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        ) as process:
            lines = Queue()

            def read_output():
                for line in process.stdout:
                    lines.put(line)
                lines.put(None)

            Thread(target=read_output, daemon=True).start()
            try:
                startup = lines.get(timeout=15)
                url = re.search(r"http://127\.0\.0\.1:\d+", startup or "")
                if url is None:
                    raise RuntimeError(f"Workbench did not report its startup URL: {startup!r}")
                base = url.group()
                with urlopen(base, timeout=5) as response:
                    html = response.read().decode("utf-8")
                script = re.search(r'<script[^>]+src="(/assets/[^"]+\.js)"', html)
                assert script, "Bundled index does not refer to a Vite script"
                with urlopen(base + script[1], timeout=5) as response:
                    assert "javascript" in response.headers["Content-Type"]
                    assert response.read()
                with urlopen(base + "/api/bootstrap", timeout=5) as response:
                    body = response.read().decode("utf-8")
                bootstrap = json.loads(body)
                assert Path(bootstrap["workspace"]) == workspace
                assert bootstrap["token"]
                assert bootstrap["key_configured"] is True
                assert key not in body
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    main()
