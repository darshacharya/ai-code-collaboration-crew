import streamlit as st
import threading
import subprocess
import tempfile
import shutil
import requests
import ast
import sys
import io
import os
import re
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.crew import build_crew

st.set_page_config(
    page_title="AI Code Collaboration Crew",
    page_icon="🤖",
    layout="wide",
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)

SAFE_FROM_PREFIXES = (
    "from pytest", "from unittest", "from typing",
    "from collections", "from datetime", "from io",
    "from os", "from sys", "from re", "from math",
    "from contextlib", "from functools", "from pathlib",
    "from src.main_module",
)


def _get_top_level_names(source_code: str) -> list[str]:
    """Use AST to get real top-level function/class names (ignores strings)."""
    try:
        tree = ast.parse(source_code)
        return [
            node.name
            for node in ast.iter_child_nodes(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
    except SyntaxError:
        return []


# ── GitHub API ──────────────────────────────────────────────────────────

def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_github_username(token: str) -> str | None:
    r = requests.get("https://api.github.com/user", headers=_gh_headers(token))
    if r.status_code == 200:
        return r.json().get("login")
    return None


def _validate_github_token(token: str) -> tuple[bool, str]:
    if not token:
        return False, "No token provided."
    user = _get_github_username(token)
    if user:
        return True, user
    return False, "Invalid token — could not authenticate."


# ── Helpers ─────────────────────────────────────────────────────────────

def _extract_best_block(text: str) -> str:
    blocks = [b.strip() for b in CODE_BLOCK_RE.findall(text) if b.strip()]
    return blocks[-1] if blocks else text.strip()


def _detect_imports(source_code: str) -> list[str]:
    stdlib = {
        "abc", "argparse", "ast", "asyncio", "base64", "collections",
        "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal",
        "enum", "functools", "glob", "hashlib", "html", "http", "inspect",
        "io", "itertools", "json", "logging", "math", "operator", "os",
        "pathlib", "pickle", "platform", "pprint", "queue", "random", "re",
        "secrets", "shutil", "signal", "socket", "sqlite3", "string",
        "struct", "subprocess", "sys", "tempfile", "textwrap", "threading",
        "time", "traceback", "typing", "unittest", "urllib", "uuid",
        "warnings", "xml", "zipfile",
    }
    deps = set()
    for line in source_code.split("\n"):
        line = line.strip()
        if line.startswith("import "):
            mod = line.split()[1].split(".")[0]
            if mod not in stdlib:
                deps.add(mod)
        elif line.startswith("from ") and "import" in line:
            mod = line.split()[1].split(".")[0]
            if mod not in stdlib and mod != "src":
                deps.add(mod)
    return sorted(deps)


def extract_files_from_tasks(tasks_output, feature: str) -> dict[str, str]:
    files: dict[str, str] = {}

    source_raw = ""
    if len(tasks_output) >= 2 and tasks_output[1].raw:
        source_raw = tasks_output[1].raw
    elif len(tasks_output) >= 1 and tasks_output[0].raw:
        source_raw = tasks_output[0].raw

    if source_raw:
        files["src/main_module.py"] = _extract_best_block(source_raw) + "\n"

    test_raw = ""
    if len(tasks_output) >= 3 and tasks_output[2].raw:
        test_raw = tasks_output[2].raw
    elif len(tasks_output) >= 1 and tasks_output[-1].raw:
        test_raw = tasks_output[-1].raw

    if test_raw:
        test_code = _extract_best_block(test_raw)
        test_code = re.sub(
            r"#\s*-+\s*Start of the code under test.*?#\s*-+\s*End of the code under test[^\n]*\n?",
            "", test_code, flags=re.DOTALL | re.IGNORECASE,
        )

        # Remove placeholder "from X import ..." lines (single and multi-line)
        # while keeping safe imports (stdlib, pytest, src.main_module).
        lines = test_code.split("\n")
        cleaned = []
        skip_until_close = False
        for line in lines:
            s = line.strip()

            if skip_until_close:
                if ")" in s:
                    skip_until_close = False
                continue

            if re.match(r"^from\s+\S+\s+import\s+", s):
                is_safe = any(s.startswith(p) for p in SAFE_FROM_PREFIXES)
                if is_safe:
                    cleaned.append(line)
                    if "(" in s and ")" not in s:
                        skip_until_close = True
                else:
                    if "(" in s and ")" not in s:
                        skip_until_close = True
                    continue
            else:
                cleaned.append(line)
        test_code = "\n".join(cleaned)

        func_names = _get_top_level_names(files.get("src/main_module.py", ""))
        if func_names:
            correct = f"from src.main_module import {', '.join(func_names)}"
            if correct not in test_code:
                test_code = correct + "\n" + test_code
        if "import pytest" not in test_code:
            test_code = "import pytest\n" + test_code

        files["tests/test_main_module.py"] = test_code.strip() + "\n"

    all_code = "\n".join(files.values())
    deps = _detect_imports(all_code)
    files["requirements.txt"] = ("\n".join(deps) + "\n") if deps else "# no external dependencies\n"
    files[".github/workflows/ci.yml"] = _generate_ci_yml()
    files["README.md"] = _generate_readme(feature, files)

    return files


def _generate_ci_yml() -> str:
    return """\
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pytest
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

      - name: Run tests
        run: python -m pytest tests/ -v
"""


def _generate_readme(feature: str, files: dict[str, str]) -> str:
    func_names = _get_top_level_names(files.get("src/main_module.py", ""))
    usage = ""
    if func_names:
        imports = ", ".join(func_names)
        usage = f"""
## Usage

```python
from src.main_module import {imports}

# Example
result = {func_names[0]}(...)
print(result)
```
"""

    tree_lines = []
    for fpath in sorted(files.keys()):
        depth = fpath.count("/")
        name = fpath.split("/")[-1]
        tree_lines.append("  " * depth + f"- {name}")
    tree = "\n".join(tree_lines)

    return f"""\
# {feature}

> Auto-generated by [AI Code Collaboration Crew](https://github.com/darshacharya/ai-code-collaboration-crew) — a multi-agent system that writes, reviews, and tests code.

![CI](https://github.com/OWNER/REPO_NAME/actions/workflows/ci.yml/badge.svg)

## How it was built

| Agent | Role |
|---|---|
| Senior Backend Engineer | Wrote the initial implementation |
| Code Reviewer | Improved code quality, fixed bugs |
| QA Engineer | Generated comprehensive pytest tests |
{usage}
## Project Structure

```
{tree}
```

## Run Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

## License

MIT
"""


# ── Git operations (token-based) ────────────────────────────────────────

def _run(cmd: str, cwd: str | None = None, env: dict | None = None) -> tuple[int, str]:
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, cwd=cwd, env=merged_env,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _write_project(base: str, files: dict[str, str]):
    for fpath, content in files.items():
        full = Path(base) / fpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    for d in ["", "src", "tests"]:
        dir_path = Path(base) / d if d else Path(base)
        if dir_path.exists() and dir_path.is_dir():
            init = dir_path / "__init__.py"
            if not init.exists():
                init.touch()


def _git_env(token: str) -> dict:
    """Build env vars that let git authenticate via token without touching URLs."""
    askpass = shutil.which("git-askpass-helper")
    if not askpass:
        helper_path = os.path.join(tempfile.gettempdir(), "git-askpass-helper.sh")
        Path(helper_path).write_text(f"#!/bin/sh\necho {token}\n")
        os.chmod(helper_path, 0o700)
        askpass = helper_path
    return {
        "GIT_ASKPASS": askpass,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "AI Code Crew",
        "GIT_AUTHOR_EMAIL": "ai-crew@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "AI Code Crew",
        "GIT_COMMITTER_EMAIL": "ai-crew@users.noreply.github.com",
    }


def _git_run(cmd: str, token: str, cwd: str | None = None) -> tuple[int, str]:
    """Run a git command with token-based auth."""
    env = _git_env(token)
    return _run(cmd, cwd=cwd, env=env)


def _ensure_repo_exists(
    repo_name: str, private: bool, token: str,
) -> tuple[bool, str]:
    """Create repo if it doesn't exist. Returns (ok, message)."""
    headers = _gh_headers(token)
    r = requests.post(
        "https://api.github.com/user/repos",
        headers=headers,
        json={"name": repo_name, "private": private, "auto_init": False},
    )
    if r.status_code == 201:
        return True, "created"
    if r.status_code == 422 and "already exists" in r.text.lower():
        return True, "exists"
    return False, f"{r.status_code} — {r.json().get('message', r.text)}"


def create_new_repo_and_push(
    repo_name: str, files: dict[str, str], private: bool, token: str, username: str,
) -> str:
    ok, msg = _ensure_repo_exists(repo_name, private, token)
    if not ok:
        return f"Error creating repo: {msg}"

    repo_url = f"https://github.com/{username}/{repo_name}"
    remote_url = f"https://x-access-token:{token}@github.com/{username}/{repo_name}.git"

    if "README.md" in files:
        files["README.md"] = files["README.md"].replace("OWNER", username).replace("REPO_NAME", repo_name)

    tmpdir = tempfile.mkdtemp()
    try:
        _run("git init", cwd=tmpdir)
        _run("git checkout -b main", cwd=tmpdir)
        _run(f'git config user.name "AI Code Crew"', cwd=tmpdir)
        _run(f'git config user.email "ai-crew@users.noreply.github.com"', cwd=tmpdir)
        _write_project(tmpdir, files)
        _run("git add -A", cwd=tmpdir)
        _run('git commit -m "Initial commit from AI Code Collaboration Crew"', cwd=tmpdir)
        _run(f"git remote add origin {remote_url}", cwd=tmpdir)

        # Retry push once in case GitHub needs a moment after repo creation
        code, out = _run("git push -u origin main", cwd=tmpdir)
        if code != 0:
            time.sleep(2)
            code, out = _run("git push -u origin main --force", cwd=tmpdir)
        if code != 0:
            return f"Push failed: {out}"
        return repo_url
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def push_to_existing_repo(
    repo_url: str, branch: str, files: dict[str, str], token: str, username: str,
) -> str:
    repo_url_clean = repo_url.rstrip("/").replace(".git", "")
    parts = repo_url_clean.split("/")
    owner, repo_name = parts[-2], parts[-1]

    if "README.md" in files:
        files["README.md"] = files["README.md"].replace("OWNER", owner).replace("REPO_NAME", repo_name)

    auth_clone = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"

    tmpdir = tempfile.mkdtemp()
    try:
        code, out = _run(f"git clone {auth_clone} repo", cwd=tmpdir)
        if code != 0:
            return f"Clone failed: {out}"

        repo_dir = os.path.join(tmpdir, "repo")
        _run(f'git config user.name "AI Code Crew"', cwd=repo_dir)
        _run(f'git config user.email "ai-crew@users.noreply.github.com"', cwd=repo_dir)
        _run(f"git checkout -b {branch}", cwd=repo_dir)
        _write_project(repo_dir, files)
        _run("git add -A", cwd=repo_dir)
        _run('git commit -m "Add AI-generated code from Code Collaboration Crew"', cwd=repo_dir)

        code, out = _run(f"git push -u origin {branch}", cwd=repo_dir)
        if code != 0:
            return f"Push failed: {out}"

        pr_resp = requests.post(
            f"https://api.github.com/repos/{owner}/{repo_name}/pulls",
            headers=_gh_headers(token),
            json={
                "title": "AI-generated code",
                "body": "Auto-generated by AI Code Collaboration Crew",
                "head": branch,
                "base": "main",
            },
        )
        if pr_resp.status_code == 201:
            pr_url = pr_resp.json().get("html_url", "")
            return f"Pushed and PR created: {pr_url}"
        return f"Pushed to branch `{branch}`. (PR note: {pr_resp.json().get('message', 'skipped')})"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── UI ──────────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    .stApp { background: #0e1117; }
    .file-tree {
        background: #161b22; border: 1px solid #30363d; border-radius: 10px;
        padding: 1rem; font-family: monospace; font-size: 0.9rem; color: #c9d1d9;
    }
    .file-tree .dir  { color: #58a6ff; }
    .file-tree .file { color: #7ee787; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Sidebar: Settings ───────────────────────────────────────────────────

with st.sidebar:
    st.header("Settings")

    st.subheader("GitHub")
    env_token = os.getenv("GITHUB_TOKEN", "")
    gh_token = st.text_input(
        "GitHub Personal Access Token",
        value=env_token,
        type="password",
        help="Create one at github.com/settings/tokens with `repo` scope. "
             "Or set GITHUB_TOKEN in your .env file.",
    )

    gh_valid = False
    gh_username = ""
    if gh_token:
        gh_valid, gh_username = _validate_github_token(gh_token)
        if gh_valid:
            st.success(f"Authenticated as **{gh_username}**")
        else:
            st.error(gh_username)
    else:
        st.info("Enter a token to enable GitHub push.")

    st.divider()
    st.subheader("LLM")
    st.caption(f"Using: `{os.getenv('GEMINI_API_KEY', '')[::10] and 'Gemini' or 'Check .env'}`")

# ── Main ────────────────────────────────────────────────────────────────

st.title("AI Code Collaboration Crew")
st.caption("Backend Engineer  →  Code Reviewer  →  QA Engineer  →  GitHub")

feature = st.text_area(
    "Describe the feature you want built",
    placeholder='e.g. "Create a function to validate email addresses"',
    height=100,
)

for key in ("crew_result", "feature_desc"):
    if key not in st.session_state:
        st.session_state[key] = None
if "extracted_files" not in st.session_state:
    st.session_state.extracted_files = {}

if st.button("Run Crew", type="primary", disabled=not feature):
    st.session_state.crew_result = None
    st.session_state.extracted_files = {}
    st.session_state.feature_desc = feature

    crew = build_crew(feature)

    agent_names = [
        ("🛠️", "Senior Backend Engineer"),
        ("🔍", "Code Reviewer"),
        ("🧪", "QA Engineer"),
    ]

    progress = st.progress(0, text="Starting crew...")
    cols = st.columns(3)
    placeholders = []
    for i, (icon, name) in enumerate(agent_names):
        with cols[i]:
            st.markdown(f"### {icon} {name}")
            placeholders.append(st.empty())
            placeholders[i].info("Waiting...")

    log_expander = st.expander("Crew logs", expanded=False)
    log_area = log_expander.empty()

    captured = io.StringIO()
    result_holder = {"result": None, "error": None}

    def run_crew():
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = captured
        sys.stderr = captured
        try:
            result_holder["result"] = crew.kickoff()
        except Exception as e:
            result_holder["error"] = str(e)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    t = threading.Thread(target=run_crew)
    t.start()

    stage = 0
    while t.is_alive():
        time.sleep(2)
        raw = captured.getvalue()
        clean = ANSI_RE.sub("", raw)
        log_area.code(clean[-3000:] if len(clean) > 3000 else clean, language="text")

        if "Senior Backend Engineer" in clean and stage == 0:
            placeholders[0].warning("Working...")
            progress.progress(10, text="Backend Engineer is coding...")
            stage = 1
        if "Agent: Code Reviewer" in clean and stage < 2:
            placeholders[0].success("Done")
            placeholders[1].warning("Working...")
            progress.progress(40, text="Code Reviewer is reviewing...")
            stage = 2
        if "Agent: QA Engineer" in clean and stage < 3:
            placeholders[1].success("Done")
            placeholders[2].warning("Working...")
            progress.progress(70, text="QA Engineer is writing tests...")
            stage = 3

    t.join()

    raw = captured.getvalue()
    clean = ANSI_RE.sub("", raw)
    log_area.code(clean[-3000:] if len(clean) > 3000 else clean, language="text")

    if result_holder["error"]:
        progress.progress(100, text="Failed")
        for p in placeholders:
            p.empty()
        st.error(f"Crew failed: {result_holder['error']}")
    else:
        progress.progress(100, text="All agents finished!")
        for p in placeholders:
            p.success("Done")

        crew_output = result_holder["result"]
        st.session_state.crew_result = str(crew_output)
        st.session_state.extracted_files = extract_files_from_tasks(
            crew_output.tasks_output, feature
        )

# ── Step 2: Show result + editable file preview ────────────────────────

if st.session_state.crew_result:
    st.divider()

    with st.expander("Raw crew output", expanded=False):
        st.markdown(st.session_state.crew_result)

    files = st.session_state.extracted_files
    if files:
        st.subheader("Project Files")
        st.caption("Review and edit files before pushing to GitHub.")

        seen_dirs: set[str] = set()
        tree = '📁 <span class="dir">repo/</span>\n'
        for fpath in sorted(files.keys()):
            parts = fpath.split("/")
            for i in range(len(parts) - 1):
                d = "/".join(parts[: i + 1])
                if d not in seen_dirs:
                    seen_dirs.add(d)
                    tree += f'{"│   " * i}<span class="dir">├── 📂 {parts[i]}/</span>\n'
            depth = len(parts) - 1
            tree += f'{"│   " * depth}<span class="file">├── 📄 {parts[-1]}</span>\n'

        st.markdown(
            f'<div class="file-tree"><pre>{tree}</pre></div>',
            unsafe_allow_html=True,
        )

        tab_names = list(files.keys())
        tabs = st.tabs(tab_names)
        edited_files: dict[str, str] = {}

        for tab, fpath in zip(tabs, tab_names):
            with tab:
                edited = st.text_area(
                    f"Edit `{fpath}`",
                    value=files[fpath],
                    height=400,
                    key=f"editor_{fpath}",
                    label_visibility="collapsed",
                )
                edited_files[fpath] = edited

        st.session_state.extracted_files = edited_files

        # ── Step 3: Push to GitHub ──────────────────────────────────────
        st.divider()
        st.subheader("Push to GitHub")

        if not gh_valid:
            st.warning("Add your GitHub token in the sidebar to enable pushing.")
        else:
            mode = st.radio(
                "Choose an option",
                ["Create new repository", "Add to existing repository"],
                horizontal=True,
            )

            push_files = dict(st.session_state.extracted_files)

            if mode == "Create new repository":
                col1, col2 = st.columns([3, 1])
                with col1:
                    repo_name = st.text_input(
                        "Repository name", placeholder="my-awesome-feature"
                    )
                with col2:
                    private = st.checkbox("Private", value=True)

                if st.button("Create & Push", type="primary", disabled=not repo_name):
                    with st.spinner("Creating repo and pushing..."):
                        result = create_new_repo_and_push(
                            repo_name, push_files, private, gh_token, gh_username
                        )
                    if "error" in result.lower():
                        st.error(result)
                    else:
                        st.success(f"Done! {result}")
                        st.balloons()

            else:
                repo_url = st.text_input(
                    "Repository URL",
                    placeholder="https://github.com/username/repo",
                )
                branch = st.text_input("Branch name", value="ai-generated-code")

                if st.button("Push & Open PR", type="primary", disabled=not repo_url):
                    with st.spinner("Cloning, pushing, and creating PR..."):
                        result = push_to_existing_repo(
                            repo_url, branch, push_files, gh_token, gh_username
                        )
                    if "failed" in result.lower() or "error" in result.lower():
                        st.error(result)
                    else:
                        st.success(result)
                        st.balloons()
