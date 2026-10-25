"""Keep all acceptance tests offline and away from the user's stores."""

import socket

import pytest
from prompt_toolkit.history import InMemoryHistory

from corecoder import checkpoints, cli, mcp, session
from corecoder.tools import ALL_TOOLS, edit
from corecoder.tools.bash import get_tracked_cwd, set_tracked_cwd


@pytest.fixture(autouse=True)
def isolate_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("CORECODER_DATA_DIR", str(tmp_path / "stores"))
    monkeypatch.setattr(checkpoints, "CHECKPOINTS_FILE", tmp_path / "checkpoints.json")
    monkeypatch.setattr(checkpoints, "_stack", [])
    monkeypatch.setattr(checkpoints, "_loaded", True)
    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(mcp, "_live_clients", [])
    monkeypatch.setattr(edit, "_changed_files", set())
    # write.py imports the same set by value; patch that alias too.
    from corecoder.tools import write

    monkeypatch.setattr(write, "_changed_files", edit._changed_files)
    monkeypatch.setattr(cli, "FileHistory", lambda *a, **k: InMemoryHistory())

    def no_network(*args, **kwargs):
        raise AssertionError("Acceptance tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    previous_cwd = get_tracked_cwd()
    set_tracked_cwd(None)
    # Preserve compatibility globals without hiding sharing within a test.
    for tool in ALL_TOOLS:
        if hasattr(tool, "_tasks"):
            monkeypatch.setattr(tool, "_tasks", [])
        if hasattr(tool, "_parent_agent"):
            monkeypatch.setattr(tool, "_parent_agent", None)
    yield
    for client in mcp._live_clients:
        client.close()
    set_tracked_cwd(previous_cwd)


@pytest.fixture
def state_factory(tmp_path):
    import importlib

    from designDocument.tests.support import required_attr

    def create(cwd=None):
        try:
            module = importlib.import_module("corecoder.state")
        except ModuleNotFoundError as exc:
            if exc.name != "corecoder.state":
                raise
            pytest.fail("R01/R02 proposed contract is not implemented: corecoder.state.SessionState")
        state_type = required_attr(module, "SessionState")
        return state_type(workspace_root=tmp_path, cwd=cwd or tmp_path)

    return create
