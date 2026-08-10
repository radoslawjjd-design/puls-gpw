"""Anti-drift guards for the round-trip scripts and the lessons that cite them.

The round-trip scripts are the only thing in this repo that runs SQL against a
real BigQuery dataset. They cannot run in CI — they need credentials and they
create and drop tables — so nothing automated proves they still say what the
docs claim. That is exactly how `test-plan.md` drifted: it named tests that had
been deleted, and every check passed because nobody was comparing the two.

These tests do not run the scripts. They check the two couplings that break
silently:

1. a `_check_*` function written but never wired into `main()` — it never runs,
   and the script still prints "All checks passed";
2. a `scripts/*.py` path cited in `lessons.md` that no longer exists — the
   lesson survives, the mechanism it points at does not.

Same idea as `tests/test_deploy_workflow_filter.py`, which couples `deploy.yml`
to `.dockerignore`: when two artifacts must agree, a comment is not enough.
"""

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ROUNDTRIP_SCRIPTS = sorted(REPO.glob("scripts/test_bq_*.py"))


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_round_trip_scripts_are_discovered():
    """A guard on the guard: if the glob stops matching, the tests below vacuously pass."""
    assert ROUNDTRIP_SCRIPTS, "no scripts/test_bq_*.py found — did they move?"


@pytest.mark.parametrize("script", ROUNDTRIP_SCRIPTS, ids=lambda p: p.name)
def test_every_check_function_is_called_from_main(script: Path):
    """Every `_check_*` defined in a round-trip script must be called from `main()`.

    A check that is written but not wired is worse than a missing one: the script
    reports success and the reader assumes the property is verified. This is the
    automatic half of phase 3 in
    `context/changes/refactor-opportunities/plan.md` — the part that can run
    without GCP credentials, since it reads the source rather than executing it.
    """
    tree = _module(script)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_check_")
    }
    if not defined:
        pytest.skip(
            f"{script.name} asserts inline in main() rather than in _check_* helpers, "
            "so it has no unwired-check failure mode"
        )

    main = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"),
        None,
    )
    assert main is not None, f"{script.name} defines _check_* but has no main()"

    called = {
        node.func.id
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    orphans = sorted(defined - called)
    assert not orphans, (
        f"{script.name}: defined but never called from main(): {orphans}. "
        "An unwired check still lets the script print that everything passed."
    )


def test_scripts_cited_in_lessons_still_exist():
    """Every `scripts/*.py` path named in `lessons.md` must resolve.

    `lessons.md` is where this project records mechanisms that are mandatory but
    unautomated — including "round-trip is obligatory after the PUL-29 incident".
    A lesson pointing at a deleted script reads as a live safety net that is not
    there. This is the enforcement step of phase 4: the rule stops being a
    convention the moment a test fails when the mechanism disappears.
    """
    lessons = REPO / "context" / "foundation" / "lessons.md"
    text = lessons.read_text(encoding="utf-8")

    cited = sorted({
        token.strip("`.,;:)")
        for token in text.replace("`", " ").split()
        if token.strip("`.,;:)").startswith("scripts/")
        and token.strip("`.,;:)").endswith(".py")
    })
    missing = [path for path in cited if not (REPO / path).is_file()]
    assert not missing, (
        f"lessons.md cites scripts that no longer exist: {missing}. "
        "Either restore the script or update the lesson — a lesson pointing at "
        "nothing is worse than no lesson."
    )
