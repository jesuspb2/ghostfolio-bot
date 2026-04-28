Follow these steps in order:

1. Run `git diff` and `git status` to review all changes.

2. Check for sensitive data in the staged/modified files:
   - `.env` files or any file containing tokens, passwords, or API keys
   - Hardcoded secrets (long hex strings, UUIDs used as credentials)
   - If anything sensitive is found, warn the user and do not proceed.

3. Verify that `.env` is listed in `.gitignore` and is NOT tracked by git. If it is tracked, warn the user and do not proceed.

4. Run `uv run ruff check .` and check for errors. If there are errors, show them to the user and do not proceed with the commit.

5. Run `uv run mypy bot` and check for type errors. If there are errors, show them to the user and do not proceed with the commit.

6. Run `uv run pytest tests/ -v` and check for failures. If any tests fail, show them to the user and do not proceed with the commit.

7. If clean, generate a concise commit message based on the changes. Keep it short (one line), imperative mood.

8. Run `git add` on all modified/untracked files.

9. Run `git commit` with the generated message.

10. After the commit succeeds, display the final commit message to the user in a code block.
