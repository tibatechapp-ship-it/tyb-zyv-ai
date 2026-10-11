# Contributing

Thanks for wanting to help. This is a small, focused project — two
single-file assistants for [Termux](https://termux.dev) — and the rules are
simple.

## Ground rules

- **Everything is type-annotated.** Every function and method declares its
  argument and return types. CI runs `ruff` + `mypy` and will fail otherwise.
- **Standard library only** for the core. Optional voice extras (`gTTS`,
  `SpeechRecognition`) are imported lazily and must never be required to import
  the modules.
- **No secrets, ever.** Tokens live in `~/.config/zyvo/telegram.env` (never in
  the repo). CI scans for leaked secrets.
- **No external services** in the test suite — the tests are self-contained.

## Development setup

```bash
git clone https://github.com/tibatechapp-ship-it/tyb-zyv-ai.git
cd tyb-zyv-ai

# runtime extras (optional, for a full voice experience)
pip install -r requirements.txt

# test + lint tooling
pip install pytest pytest-cov ruff mypy
```

## Before you open a pull request

```bash
# 1. syntax
python -m py_compile zyvo-telegram.py zyvo-voice.py

# 2. style + types
ruff check .
mypy zyvo-telegram.py zyvo-voice.py

# 3. every function still annotated
python scripts/check_annotations.py

# 4. the test suite, with coverage
pytest --cov --cov-report=term-missing
```

Keep:
- all four checks green, and
- **coverage ≥ 80 %**.

## Adding a feature

1. Fork and branch (`git checkout -b feature/my-change`).
2. Add the code **and** its tests (see [`docs/TESTING.md`](docs/TESTING.md) for
   how the fixtures and fakes work).
3. Run the four checks above.
4. Open a pull request describing *what* changed and *why*.

Test names should read like sentences — `test_run_turn_retries_on_error`,
`test_speak_chunks_and_uses_lang` — so the output doubles as documentation.

## Reporting bugs

Open an [issue](https://github.com/tibatechapp-ship-it/tyb-zyv-ai/issues) with:
- what you ran (the exact command),
- what you expected,
- what actually happened (paste the output),
- your Termux/Python versions (`python3 --version`, `uname -a`).

## Code style

- 4-space indentation, ≤ 100 columns.
- `ruff` default rules (`E4,E7,E9,F`) — fix, don't suppress, where reasonable.
- Prefer small, pure functions and explicit names over clever one-liners.
- Comments explain *why*, not *what*.

## License

By contributing you agree your work is released under the
[MIT License](LICENSE).
