# AI Code Collaboration Crew

A multi-agent AI system built with CrewAI that generates production-ready code, reviews it, writes tests, and pushes the result to GitHub — all from a Streamlit UI.

## Overview

Three specialized AI agents work in sequence:

1. **Senior Backend Engineer** — writes clean, typed Python code from your feature description
2. **Code Reviewer** — reviews and improves the code (bug fixes, performance, best practices)
3. **QA Engineer** — generates comprehensive pytest test suites

The Streamlit app lets you run the crew, edit the generated files, and push directly to GitHub (new repo or existing repo via PR).

## Tech Stack

* CrewAI + LiteLLM (multi-agent orchestration)
* Gemini / OpenAI / Groq / Ollama (configurable LLM backend)
* Streamlit (web UI)
* GitHub API (repo creation, push, PR)
* Python 3.10+

## Architecture

```
Feature Request
      │
      ▼
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│   Backend    │───▶│    Code      │───▶│     QA       │
│   Engineer   │    │   Reviewer   │    │   Engineer   │
└─────────────┘    └──────────────┘    └──────────────┘
      │                   │                    │
      ▼                   ▼                    ▼
  Initial Code      Improved Code        Test Suite
                          │                    │
                          ▼                    ▼
                   src/main_module.py   tests/test_main_module.py
                          │
                          ▼
                  ┌───────────────┐
                  │    GitHub     │
                  │  (new repo /  │
                  │   PR to repo) │
                  └───────────────┘
```

## Getting Started

### 1. Clone and setup

```bash
git clone https://github.com/darshacharya/ai-code-collaboration-crew.git
cd ai-code-collaboration-crew
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e .
uv pip install streamlit "crewai[google-genai]"
```

### 2. Configure `.env`

```bash
cp .env.example .env
```

Add your keys:

```
GEMINI_API_KEY=your_gemini_key
GITHUB_TOKEN=your_github_token
```

* **GEMINI_API_KEY** — get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
* **GITHUB_TOKEN** — create at [github.com/settings/tokens](https://github.com/settings/tokens) with `repo` scope (or enter it in the sidebar at runtime)

### 3. Run the app

```bash
streamlit run app.py
```

### 4. CLI mode (optional)

```bash
python main.py --feature "Create a function to validate email addresses"
```

## Features

* **Multi-agent collaboration** — Backend Engineer → Code Reviewer → QA Engineer
* **Streamlit UI** — live progress, agent status, expandable logs
* **Editable file preview** — review and tweak code before pushing
* **GitHub integration** — create new repos or push to existing repos with auto-PR
* **Auto-generated project scaffolding** — `requirements.txt`, GitHub Actions CI, README
* **Token-based auth** — any user can plug in their own GitHub token

## Generated Repo Structure

Every pushed repo includes:

```
.github/workflows/ci.yml   # pytest CI for Python 3.10-3.12
src/main_module.py          # reviewed source code
tests/test_main_module.py   # pytest test suite
requirements.txt            # auto-detected dependencies
README.md                   # with usage examples and CI badge
```

## Supported LLM Providers

Configured in `src/config.py` with automatic fallback:

| Priority | Provider | Model | Env Var |
|----------|----------|-------|---------|
| 1 | Gemini | gemini-2.5-flash | `GEMINI_API_KEY` |
| 2 | OpenAI | gpt-4o-mini | `OPENAI_API_KEY` |
| 3 | Groq | llama-3.1-8b-instant | `GROQ_API_KEY` |
| 4 | Ollama | llama3:8b | (local, no key) |

## Deploy on Streamlit Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your repo and set `app.py` as the main file
4. Add `GEMINI_API_KEY` and `GITHUB_TOKEN` as secrets

## License

MIT
