---
name: markdown
description: Use when writing markdown files
---
# Markdown

Keep markdown source tidy and easy to read:

- Follow formatting best practices
- Avoid horizontal lines before headings
- Max line length equal or below 80 chars

## Multiline

Format multiline text as a justified block.

Avoid:

```md
Seed identity expects a numeric year even if a custom index encodes it as JSON
text

This module sits four directories below the repository root; resolving from __file__
keeps loading independent of the process working directory
```

Prefer:

```md
Seed identity expects a numeric year even
if a custom index encodes it as JSON text

This module sits four directories below the repository root; resolving
from __file__ keeps loading independent of the process working directory
```

## Linting

After editing `.md` files, run (and fix issues):

```bash
npx -y markdownlint-cli2 <edited-file-paths>
```
