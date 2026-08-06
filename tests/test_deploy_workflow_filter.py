"""The deploy trigger's path filter, and the thing that makes it sound (PUL-122).

`deploy.yml` skips the whole pipeline for commits confined to `paths-ignore`. That is only
safe while those paths cannot reach the image — and the file that decides *that* is
`.dockerignore`, in a different directory, with a different glob dialect, edited for
entirely different reasons.

Nothing links them. Add `docs/` to the image tomorrow and the workflow keeps skipping
deploys for changes that now matter; the failure is a deploy that silently never happened,
which surfaces as "the fix didn't work in production" rather than as a red build.

So the assertion here is not "the filter is configured" but **every path the workflow
ignores is a path `.dockerignore` keeps out of the build context**, checked against the
repository's real tracked files rather than against the patterns as strings.
"""
import re
import subprocess
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DEPLOY = _ROOT / ".github" / "workflows" / "deploy.yml"
_TESTS_WORKFLOW = _ROOT / ".github" / "workflows" / "tests.yml"
_DOCKERIGNORE = _ROOT / ".dockerignore"

# Trees whose contents must always reach production. Listed from the ticket's acceptance
# criteria, not derived from the config being tested — a check that reads its expectation
# out of the file under test proves nothing.
_MUST_DEPLOY = ("src/", "db/", "static/", "tests/", ".github/workflows/")


def _load_workflow(path: Path) -> dict:
    # `on` is the YAML 1.1 boolean `true`, so PyYAML gives back the key True, not "on".
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _push_trigger(workflow: dict) -> dict:
    triggers = workflow[True] if True in workflow else workflow["on"]
    return triggers["push"]


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a GitHub filter pattern to a regex.

    `**` crosses directory separators, `*` and `?` do not — the same distinction Docker
    makes, which is why one translator serves both dialects here.

    `**/` matches *zero* or more directories, so `**/*.md` covers `README.md` at the root.
    Both dialects agree on that (Docker compiles it to `(.*/)?`), and getting it wrong is
    what a first draft of this file did — reporting a sound `.dockerignore` as broken.
    """
    out = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _dockerignore_rules() -> list[tuple[str, bool]]:
    """(pattern, is_negation) pairs, comments and blanks dropped, trailing `/` stripped."""
    rules = []
    for raw in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        rules.append((line.lstrip("!").rstrip("/"), negated))
    return rules


def _excluded_from_build_context(path: str, rules: list[tuple[str, bool]]) -> bool:
    """Whether Docker would keep `path` out of the build context.

    A directory rule excludes everything beneath it, so each ancestor is tested too. Later
    rules win over earlier ones, and a `!` rule re-includes — Docker's own precedence.
    """
    candidates = [path]
    parts = path.split("/")
    candidates += ["/".join(parts[:i]) for i in range(1, len(parts))]

    excluded = False
    for pattern, negated in rules:
        matcher = _glob_to_regex(pattern)
        if any(matcher.match(candidate) for candidate in candidates):
            excluded = not negated
    return excluded


@pytest.fixture(scope="module")
def ignore_patterns() -> list[str]:
    patterns = _push_trigger(_load_workflow(_DEPLOY)).get("paths-ignore")
    assert patterns, (
        "deploy.yml's push trigger has no paths-ignore, so every documentation-only "
        "commit rebuilds the image and cuts a new Cloud Run revision (PUL-122)"
    )
    return patterns


def test_context_changes_do_not_trigger_a_deploy(ignore_patterns):
    # The 10x workflow emits at least two context-only commits per change (the plan
    # epilogue and the archive move), which is what makes this structural rather than
    # incidental.
    assert "context/**" in ignore_patterns


def test_every_ignored_path_is_one_docker_never_sees(ignore_patterns):
    """The invariant this file exists for.

    Checked against real tracked files: comparing the two pattern dialects as strings
    would be comparing spellings, not behaviour.
    """
    rules = _dockerignore_rules()
    tracked = _tracked_files()

    for pattern in ignore_patterns:
        matcher = _glob_to_regex(pattern)
        matched = [path for path in tracked if matcher.match(path)]

        # A pattern matching nothing is a typo that would otherwise pass silently.
        assert matched, f"paths-ignore pattern {pattern!r} matches no tracked file"

        reachable = [
            path for path in matched if not _excluded_from_build_context(path, rules)
        ]
        assert not reachable, (
            f"deploy.yml skips commits to {pattern!r}, but .dockerignore lets "
            f"{reachable[:5]} into the build context — those commits change the image "
            "and would be deployed silently late, or never"
        )


def test_the_deployable_trees_still_trigger_a_deploy(ignore_patterns):
    matchers = [_glob_to_regex(pattern) for pattern in ignore_patterns]
    tracked = _tracked_files()

    for tree in _MUST_DEPLOY:
        in_tree = [path for path in tracked if path.startswith(tree)]
        assert in_tree, f"no tracked files under {tree} — has the layout moved?"

        swallowed = [
            path for path in in_tree if any(m.match(path) for m in matchers)
        ]
        assert not swallowed, (
            f"{swallowed[:5]} live under {tree} but match a paths-ignore pattern, so "
            "changing them would no longer reach production"
        )


def test_pull_request_checks_are_left_alone():
    # paths-ignore belongs on the push trigger only. A docs PR still deserves a green
    # suite — PR #243's run is what confirmed an archive move had not broken collection.
    pull_request = _load_workflow(_TESTS_WORKFLOW)[True]["pull_request"]
    assert "paths" not in pull_request
    assert "paths-ignore" not in pull_request
