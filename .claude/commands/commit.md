Follow these steps in order:

1. Run `git diff` and `git status` to review all changes.

2. Check for sensitive data in the staged/modified files:
   - `.env` files or any file containing tokens, passwords, or API keys
   - Hardcoded secrets (long hex strings, UUIDs used as credentials)
   - If anything sensitive is found, warn the user and do not proceed.

3. Verify that `.env` is listed in `.gitignore` and is NOT tracked by git. If it is tracked, warn the user and do not proceed.

4. Run `uv run flake8 --max-line-length=120 --exclude=.venv .` and check for errors. If there are errors, show them to the user and do not proceed with the commit.

5. Run `uv run pytest tests/ -v` and check for failures. If any tests fail, show them to the user and do not proceed with the commit.

6. If clean, generate a concise commit message based on the changes. Keep it short (one line), imperative mood.

7. Run `git add` on all modified/untracked files.

8. Run `git commit` with the generated message.

9. After the commit succeeds, display the final commit message to the user in a code block.
