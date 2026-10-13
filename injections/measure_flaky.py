"""Measure how often a flaky injection spec actually fails, without touching the repo.

Usage (from the repository root, with the project's virtualenv)::

    python injections/measure_flaky.py injections/specs/inj-049.json
    python injections/measure_flaky.py injections/specs/inj-049.json 30

The repository is copied to a temporary folder (skipping ``.venv``, ``.git``,
``data`` and ``models`` at the top level, and bytecode caches anywhere), the
spec's find/replace is applied to the copy only, and
``python -m pytest tests/test_behaviour.py -q -p no:cacheprovider`` is run N
times there. Each run is a fresh process, so string hash randomization differs
between runs; ``PYTHONHASHSEED`` is removed from the child environment so an
inherited value cannot pin it. The working tree is never written to.

Prints the failure rate and every distinct set of failing tests seen, with
how many runs produced it.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True  # importing run_injection must not write into the working tree

from run_injection import Abort, Spec, load_spec  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_TOP_LEVEL = {".venv", ".git", "data", "models"}
SKIP_ANYWHERE = {"__pycache__", ".pytest_cache"}
PYTEST_ARGS = ["-m", "pytest", "tests/test_behaviour.py", "-q", "-p", "no:cacheprovider"]
FAILING_LINE = re.compile(r"^(?:FAILED|ERROR) (tests/\S+)", flags=re.MULTILINE)


def ignore(directory: str, names: list[str]) -> set[str]:
    """``copytree`` filter: top-level skips only at the root (``api/models`` must be kept)."""
    skipped = {name for name in names if name in SKIP_ANYWHERE}
    if Path(directory).resolve() == REPO_ROOT:
        skipped |= {name for name in names if name in SKIP_TOP_LEVEL}
    return skipped


def apply_to_copy(spec: Spec, copy_root: Path) -> None:
    """Apply the spec's find/replace to the copy, mirroring ``run_injection.apply_mutation``."""
    path = (copy_root / spec.target_file).resolve()
    if not path.is_relative_to(copy_root.resolve()):
        raise Abort(f"refusing to write outside the temporary copy: {path}")
    raw = path.read_bytes()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    original = raw.decode(encoding)

    find, replace = spec.find, spec.replace
    if "\r\n" in original and "\n" in find and "\r\n" not in find:
        find = find.replace("\n", "\r\n")
        replace = replace.replace("\n", "\r\n")
    count = original.count(find)
    if count != 1:
        raise Abort(f"'find' occurs {count} times in {spec.target_file}; expected exactly 1")
    path.write_bytes(original.replace(find, replace, 1).encode(encoding))


def run_once(copy_root: Path, env: dict[str, str]) -> tuple[int, frozenset[str], str]:
    """Run the behaviour tests once. Returns (exit code, failing test ids, output tail)."""
    proc = subprocess.run(
        [sys.executable, *PYTEST_ARGS],
        cwd=copy_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = proc.stdout + proc.stderr
    failing = frozenset(FAILING_LINE.findall(output))
    if proc.returncode != 0 and not failing:
        failing = frozenset({f"<exit {proc.returncode}, no FAILED/ERROR line>"})
    return proc.returncode, failing, output.strip().splitlines()[-1] if output.strip() else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", help="path to a spec file, e.g. injections/specs/inj-049.json")
    parser.add_argument("n", nargs="?", type=int, default=30, help="number of pytest runs (default: 30)")
    args = parser.parse_args()
    if args.n < 1:
        parser.error("n must be at least 1")

    try:
        spec = load_spec(Path(args.spec))
    except Abort as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return 2

    env = {key: value for key, value in os.environ.items() if key != "PYTHONHASHSEED"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    with tempfile.TemporaryDirectory(prefix=f"measure-{spec.id}-") as tmp:
        copy_root = Path(tmp) / "repo"
        shutil.copytree(REPO_ROOT, copy_root, ignore=ignore)
        try:
            apply_to_copy(spec, copy_root)
        except Abort as exc:
            print(f"ABORT: {exc}", file=sys.stderr)
            return 2

        expected = frozenset(spec.expected_failing_tests)
        outcomes: Counter[frozenset[str]] = Counter()
        failures = 0
        started = time.monotonic()
        for index in range(1, args.n + 1):
            code, failing, tail = run_once(copy_root, env)
            failures += code != 0
            outcomes[failing] += 1
            mark = "P" if code == 0 else ("F" if failing == expected else "X")
            print(f"[{spec.id}] run {index:>2}/{args.n} {mark}  {tail}", flush=True)
        elapsed = time.monotonic() - started

    unexpected = [failing for failing in outcomes if failing and failing != expected]
    print()
    print(f"{spec.id} ({spec.category}) target={spec.target_file}")
    print(f"expected failing: {sorted(expected)}")
    print(f"failure rate: {failures}/{args.n} = {failures / args.n:.0%}  ({elapsed:.0f} s)")
    print("distinct failing-test sets:")
    for failing, count in sorted(outcomes.items(), key=lambda item: -item[1]):
        label = "(none: green)" if not failing else ", ".join(sorted(failing))
        print(f"  {count:>3} x {label}")
    print(f"other tests ever failed: {'YES ' + str(unexpected) if unexpected else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
