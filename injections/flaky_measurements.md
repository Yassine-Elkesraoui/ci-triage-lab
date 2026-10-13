# Flaky injection measurements (inj-049 to inj-058)

Measured locally on 2026-10-11 with `python injections/measure_flaky.py injections/specs/inj-0NN.json 30`
(30 runs of `python -m pytest tests/test_behaviour.py -q -p no:cacheprovider` on a temporary copy, PYTHONHASHSEED unset).
Failing test ids are relative to `tests/test_behaviour.py::`.

| id | root cause | target file | failing test | failure rate (N=30) | other tests ever failed? |
|---|---|---|---|---|---|
| inj-049 | randomness: random test data sometimes invalid | tests/test_behaviour.py | `test_chat_request_accepts_valid_question` | 15/30 = 50% | no |
| inj-050 | randomness: random input size vs fixed threshold | tests/test_behaviour.py | `test_chat_request_rejects_overlong_question` | 16/30 = 53% | no |
| inj-051 | unordered collection: set of strings | tests/test_behaviour.py | `test_chat_request_rejects_short_question` | 17/30 = 57% | no |
| inj-052 | time: one-second resolution, ~0.5 s apart | tests/test_behaviour.py | `test_chat_response_requires_all_fields` | 12/30 = 40% | no |
| inj-053 | async wait | tests/test_behaviour.py | `test_chunking_returns_empty_list_for_blank_text` | 15/30 = 50% | no |
| inj-054 | concurrency: unsynchronised shared state | tests/test_behaviour.py | `test_chunking_char_count_matches_content_length` | 18/30 = 60% | no |
| inj-055 | performance threshold | tests/test_behaviour.py | `test_settings_defaults_are_sane` | 14/30 = 47% | no |
| inj-056 | test order dependency: parametrize over a set | tests/test_behaviour.py | `test_chunking_splits_long_text_into_multiple_chunks[count]` | 15/30 = 50% | no |
| inj-057 | product-code flake: randomness (uuid4 top bit) | api/services/chunking_service.py | `test_chunking_attaches_sequential_indexes_and_document_id` | 11/30 = 37% | no |
| inj-058 | product-code flake: unordered collection | api/models/schemas.py | `test_chat_request_rejects_overlong_question` | 18/30 = 60% | no |
