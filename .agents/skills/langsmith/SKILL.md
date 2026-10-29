---
name: langsmith
description: Use when asked to, or need to, fetch a LangSmith data
---
# LangSmith

Three tools, by job:

- **Find runs** (recent, failing, slow, by metadata or tag): the CLI.
- **Read one trace or thread in full**: `fetch.py`.
- **Anything else** (rollups, aggregations, joins): a temp SDK script.

Activate the venv and export `.env` first, so child processes see the keys:

```bash
source .venv/bin/activate
set -a && source .env && set +a
```

## Getting threads or traces

Use `thread` or `trace` from what the user named. If they didn't say, ask.

```bash
python .agents/skills/langsmith/scripts/fetch.py thread <id>
python .agents/skills/langsmith/scripts/fetch.py trace <id>
```

Output is one JSON line and can reach hundreds of KB. Redirect it to
`/tmp/<name>.json` and read it with a short script, never into context.
Inputs, outputs, and metadata are cut at 4,000 characters, so long prompts
and messages are clipped. Use the CLI with `--include-io` or an SDK script
when the full text matters.

## Using the CLI

CLI uses `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` from env.

Add `--format json` for scripts and `-o <file>` to write a file.

<https://docs.langchain.com/langsmith/langsmith-cli.md>

```bash
langsmith --help
```

## Temp SDK scripts

For unanswerable questions by the tools above, write a throwaway `/tmp` script.

Use `langsmith`. Run it, and never commit it. Never print secrets from `.env`.
