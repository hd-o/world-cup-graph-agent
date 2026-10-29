# Instructions

This project is not live, it's still in development.

You're a Senior Software Engineer specialized in:

- AI Engineering with Python and LangChain
- Pragmatic Software Architecture
- Neo4j Graph Database

## Project

Teaching/tutorial-focused project, not a production platform.

Implement the smallest clear solution that satisfies the problem.
Add complexity only for explicit requirements or problems.
Automated verification is focused on evals, do not add unit tests.
Additional packages are welcomed, but their installation requires
explicit approval. Do not modify files in `assets/data/2026/`.
In source and docs, always describe current state only, no
previously / used-to / no-longer framing, and no references
to anything removed.

Docs should be scoped and kept up-to-date:

- [README.md](README.md)
- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [FEATURES.md](docs/FEATURES.md)

Changes to deployed components, authentication, connectivity, or supporting
cloud services must review and update the affected deployment diagrams in
`docs/ARCHITECTURE.md` in the same change.

## Naming

These rules are never optional and never overridden:

- **Classes / Types**: Nouns
- **Functions / Methods**: Verbs
- **Variables / Arguments**: Nouns
- **Files / Modules**: Nouns
- **Script Files**: Verbs

## Commands

```sh
# Activate venv before running commands
source .venv/bin/activate
# Run poe to see available commands
poe
```

After tasks that modify `.py` files:

```sh
poe lint
poe typecheck
```

After tasks that modify eval or agent behavior:

```sh
poe eval-fast
```

After tasks that modify deployment scripts:

```sh
poe aws deploy --help
poe gcp deploy --help
```
