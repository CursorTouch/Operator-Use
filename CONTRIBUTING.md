# Contributing to Operator-Use

Thanks for your interest in contributing! Here's everything you need to get started.

---

## Maintainer

- **@CursorTouch** — project lead

---

## Branching Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Stable, production-ready releases |
| `dev`  | Active development, new features, refactoring |

**Target `dev` for:**
- New features (new channels, providers, tools)
- Refactoring or API changes
- Experimental work

**Target `main` for:**
- Bug fixes
- Documentation updates
- Minor tweaks with no breaking changes

When in doubt, open against `dev`.

---

## Development Setup

```bash
# Clone the repo
git clone https://github.com/CursorTouch/Operator-Use.git
cd Operator-Use

# Install with dev dependencies
pip install -e ".[dev]"

# Copy and fill in your environment variables
cp .config.example.json ~/.operator/config.json
```

---

## Code Style

- Python 3.12+
- Follow existing patterns — consistency over cleverness
- Keep things simple and decoupled
- Max line length: 100 characters
- Use `ruff` for linting and formatting:
  ```bash
  ruff check .
  ruff format .
  ```

---

## Adding a New Channel

1. Create `operator_use/gateway/channels/{channel_name}.py` inheriting from `BaseChannel`
2. Implement `name`, `start()`, `stop()`, `_listen()`, and `send()`
3. Add a config class in `operator_use/gateway/channels/config.py`
4. Register it in `operator_use/cli/start.py`
5. Document it in the README channels table

## Adding a New LLM Provider

1. Create `operator_use/providers/{provider_name}/llm.py` following `BaseChatLLM`
2. Export from `operator_use/providers/{provider_name}/__init__.py`
3. Add the provider name to the onboard wizard choices

---

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Write a clear description of what changed and why
- Reference any related issues
- Ensure your branch is up to date with the target branch before opening a PR

---

## Reporting Bugs

Open an issue with:
- A clear description of the problem
- Steps to reproduce
- Expected vs actual behavior
- Your OS, Python version, and Operator-Use version

---

## Questions

Open a discussion or issue on GitHub.
