# Flaky injection measurements, v1.1 holdout (inj-066 to inj-069)

v1.0 measurements (inj-049 to inj-058) are in `flaky_measurements.md`, which is a v1.0 file and is not changed.

Measured locally on 2026-11-20, 19:19 to 19:41 UTC, with
`python injections/measure_flaky.py injections/specs/inj-0NN.json 30`: 30 runs of
`python -m pytest tests/test_behaviour.py -q -p no:cacheprovider` on a temporary copy of the repository with the
spec's find/replace applied, each run a fresh process, PYTHONHASHSEED unset. The working tree is not written to.
The specs measured are the ones committed in 168ed54; inj-066..069 have not changed since.

Environment: Windows 11 (10.0.26200), 12 logical CPUs, Python 3.11.9, pytest 9.0.3, the project's `.venv`. The
four measurements ran **at the same time** (each took 1322 s), so they competed for CPU. That matters most for
inj-066 and inj-067, whose outcome depends on which thread finishes first. CI runs one job alone on a GitHub-hosted
Linux runner with fewer cores, so the CI failure rate may differ from these numbers. The first run of each measurement took
about 92 s (cold imports) and the others 9.5 to 21 s; nothing timed out.

inj-070 (wall-clock date) is not here: `measure_flaky.py` runs `tests/test_behaviour.py` only, and its outcome
depends on the UTC hour, not on chance (see the inj-070 spec).

Failing test ids are relative to `tests/test_behaviour.py::`.

| id | root cause | target file | failing test | failure rate (N=30) | other tests ever failed? |
|---|---|---|---|---|---|
| inj-066 | completion order of concurrent tasks: `as_completed` results paired with submission order | tests/test_behaviour.py | `test_chunking_attaches_sequential_indexes_and_document_id` | 11/30 = 37% | no |
| inj-067 | completion order of concurrent tasks: first finished taken as first submitted | tests/test_behaviour.py | `test_chunking_char_count_matches_content_length` | 20/30 = 67% | no |
| inj-068 | ordering by generated ids: oldest upload picked by smallest uuid4 | tests/test_behaviour.py | `test_chunking_splits_long_text_into_multiple_chunks` | 15/30 = 50% | no |
| inj-069 | ordering by generated ids: newest draft picked by largest uuid4 | tests/test_behaviour.py | `test_chat_request_accepts_valid_question` | 18/30 = 60% | no |

Every failing run failed the expected test and only that test (0 runs marked X by the tool). The rates fall
in the same range as v1.0 (37% to 60%). inj-067, at 67%, is the furthest from the target of about 50%. The
plan has a planned passing same-commit run for inj-068 only, not for inj-067, so the higher rate does not
affect a planned rerun. Raw tool output was not saved in the repository.
