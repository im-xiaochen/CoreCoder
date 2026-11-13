# From code change to handoff: Git review, project checks, and offline evaluation

[中文](09-review-and-verify.md) · [Contents](00-index_EN.md)

Suppose you ask CoreCoder to fix a Python function. Before handing it off, you want to know which files changed, whether the project's tests pass, and whether CoreCoder's own review workflow behaves as expected. Those questions lead to the workbench's Git panel, the project checks, and the offline evaluation in the CoreCoder source tree.

## Inspect what changed in the worktree

Start the Web workbench inside a Git repository using the [setup instructions](../frontend/README.md). Open the Git panel and inspect staged and unstaged changes. The staged diff compares HEAD with the index. The unstaged diff compares the index with the worktree. Untracked files also appear in the status list. You can see repository changes even when they were made by a terminal command.

Review the files and select the paths to stage or commit. If a file changes after you open its diff, the service compares the current state with the review token. Refresh and review again if the token is stale. Changes observed during a run can also come from your editor or another process. Check their origin before including them.

Start with `status()`, `diff()`, `stage()`, and `commit()` in the [Git workspace implementation](../corecoder/git_workspace.py). The [Web routes](../web_backend/server.py) connect those operations to the workbench.

## Verify with the project's own command

Suppose pytest is already installed for your project and you normally run tests from the repository root. Save the following as `.corecoder/checks.json`. If the project already has a configuration, add the check to its existing `checks` array and choose a unique ID.

```json
{
  "version": 1,
  "checks": [
    {"id": "test", "name": "Python tests", "command": "python -m pytest", "cwd": ".", "timeout": 120}
  ]
}
```

In an interactive CLI session, use `/checks` to list checks and `/check test` to review and confirm the command. The Web Checks panel shows commands, starts approved checks, and displays their history. When a check fails, inspect its exit status and output before deciding what to fix and rerun.

The [project checks module](../corecoder/project_checks.py) reads the configuration, runs the approved command, and records the result. The slash command entry point lives in the [CLI](../corecoder/cli.py).

## Use an offline fixture when changing CoreCoder itself

To verify a change to CoreCoder's Git integration, run this command from a **CoreCoder source checkout** with the project dependencies installed:

```bash
python -m corecoder.evaluation --task git_index --output ./evaluation-output
```

The evaluation creates fixture repositories and saves reports in the output directory. This task checks how selected changes and existing staged changes are handled. Omit `--task git_index` to run the full fixture suite, which covers areas such as run recovery, cancellation, and project checks. The evaluation uses a scripted model and synthetic usage to measure CoreCoder's behavior. Run your own project's checks to verify the application change.

The entry point and assertions are in the [offline evaluation module](../corecoder/evaluation.py). For more on how run results are saved and recovered, see [sessions and the CLI](06-session-and-cli_EN.md).
