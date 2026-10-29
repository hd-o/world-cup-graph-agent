---
name: python-conventions
description: "Use when writing to Python `.py` files"
paths: ["**/*.py"]
---

# Python Conventions

Use modern Python 3.14+ with typings.

## Avoid

- Avoid deeply nested code that could be simplified
- Avoid `__init__.py` whenever possible, especially empty ones
- Avoid defensive checks or try/catch that are abnormal for trusted code
- Avoid file-level docstring. Prefer clear naming, and self-explanatory code
- Avoid keyword-only arg separator in app code, use only in installable code
- Avoid mutable state. Where suitable, prefer immutable data structures

## Naming

High-level code should read like a concise description of the workflow or business process.
Keep functions at a coherent level of abstraction and prefer well-named domain operations
over mixing orchestration with implementation mechanics. Optimize for local comprehension.

A reader should understand a call site without opening the callee to decode terminology.
Name the domain purpose, outcome, or policy rather than the mechanism: `add_search_metadata()`
over `normalize_request()`; `get_case_insensitive_key()` over `fold()`. Qualify generic names
with the concrete resource or service they refer to: `openrouter_api_key`, not `api_key`;
`get_openrouter_api_key()`, not `get_credential()`. Prefer precise domain terms over
names like `data`, `item`, `result`. Narrow generic locals are acceptable when their function
supplies the missing meaning. Avoid unexplained abbreviations. Private helpers must be as clear
as public. Boolean names read as predicates. lookup mappings state both sides when needed:
`add_ids_by_external_key`. Use one term per concept everywhere.

Before accepting a name, ask:

1. Can a first-time reader predict its exact role and likely type?
2. Does it promise behavior the code does not guarantee?
3. Does it hide a distinction such as ID/object, original/derived, or name/value?
4. Could a clearer name remove a comment explaining what the code does?

## Typing

Design coherent APIs that accurately model supported usage and type relationships
without unnecessary widening. Preserve platform semantics, keep types domain-specific,
use generics where relationships matter, and avoid unnecessary abstractions, assertions,
annotations, workarounds, and fallbacks.

### Enum or Literal

Prefer a `StrEnum` over `type X = Literal[...]` for any closed set of string values the
code assigns or compares. Keep `Literal` where the value set is published as a schema
that another component validates or inspects (wire schemas, structured output, generating
tooling), because an enum-typed field changes the emitted schema.

### Branding

Types document domain. Readers must see *which* type they have.

Avoid:

```python
type ChatEntryMapping = Mapping[str, object]
```

Prefer:

```python
# E.g. "role", "type", "content"
type ChatFieldName = str
# Roles, message types, text, tool-call blocks, JSON
type ChatFieldValue = object
type ChatEntryMapping = Mapping[ChatFieldName, ChatFieldValue]
```

### Do Not Brand

- Incidental `list[str]` of failure, skip, or repair messages
- Every `str` on a Pydantic payload whose field name is already the brand
- Generic or opaque library types (e.g: `Any` for boto3, LangGraph `Command[Any]`)
- Local builder scraps (`parts: list[str]`, CLI argv)

### Weakening Types

When a production annotation change breaks tests, update the test to the same alias.
Never weaken production types to pass tests. Pyright `ignore` is a last resort, always
web search or think harder to exhaust alternatives before ignoring type issues.
Always explain "ignores" with a comment.

Avoid:

```python
def _make_observation(outcome: str) -> Observation:
    return Observation(outcome)  # pyright: ignore[reportArgumentType]
```

Prefer:

```python
def _make_observation(outcome: str) -> Observation:
    return Observation(outcome)  # pyright: ignore[reportArgumentType] explanation...
```

## Multiline Readability

- Middle multiline statements must be surrounded by blank lines
- Prefer triple-quoted strings over concatenated multi-line strings

Avoid:

```python
def do_something():
    span.set_attribute(
        ATTRIBUTE,
        get_default_value(),
    )
    running = update.get("context")
    # Some comment explaining why
    if not running:
        return None
    if not check_validity(running):
        logger.error("Not valid")
        persist_validity(running)
        return None
    return running
```

Prefer:

```python
def do_something():
    span.set_attribute(
        ATTRIBUTE,
        get_default_value(),
    )

    running = update.get("context")
    
    # Some comment explaining why
    if not running:
        return None
    
    if not check_validity(running):
        logger.error("Not valid")
        persist_validity(running)
        return None

    return running
```

## Statement Clarity

Avoid (when not obvious):

```python
report_smoke_result(run_smoke_paths())
```

Prefer:

```python
failures = run_smoke_paths()
report_smoke_result(failures)
```

## Single Source of Truth

- Fix once: one helper at the nearest shared seam beats same logic in 2 callers
- When copies differ only by a constant, parameterize instead of duplicating
- Declare fields on the state or type, even when only one consumer reads it

## Comments

Names explain what; comments explain why. Add concise `why` comments wherever a
first-time reader could understand what code does but not why it exists. Add a
`why` comment immediately before non-obvious code. For non-obvious safeguards,
explain the failure they prevent and cite a stable source when available. Don’t
just describe what the code does. Prefer a few useful lines over forcing readers
to trace helpers or search external documentation. Never narrate syntax, repeat
a truthful name, or claim a reason the code does not enforce. Improve unclear
names before compensating with comments. The goal is not comment count. The goal
is that maintainers can read the source as a walkthrough.

Avoid large docstrings. Docstring orients; the body teaches. Example:

```python
def reserve_seat(
    request: SeatRequest,
    seats: list[Seat],
    system: BookingSystem,
) -> Reservation:
    """Reserve the best available seat for a request.

    Prefers the smallest suitable option and rechecks availability
    before booking because earlier results may already be stale.
    """
    # Eliminate seats that can never satisfy the request so the
    # selection logic below only has to compare valid candidates.
    candidates = [seat for seat in seats if seat.capacity >= request.party_size]

    # Prefer the smallest adequate seat to leave larger
    # ones available for groups that actually need them
    candidates.sort(key=lambda seat: seat.capacity)

    for seat in candidates:
        # Availability can change between listing and booking,
        # this final check belongs immediately before the write
        if system.is_available(seat.id, request.time):
            return system.reserve(seat.id, request)

    raise NoAvailableSeatError(request)
```

Format comments as a justified block, not using max line length.

Avoid:

```python
# Seed identity expects a numeric year even if a custom index encodes it as JSON
# text

# This module sits four directories below the repository root; resolving from __file__
# keeps loading independent of the process working directory
```

Prefer:

```python
# Seed identity expects a numeric year even
# if a custom index encodes it as JSON text

# This module sits four directories below the repository root; resolving
# from __file__ keeps loading independent of the process working directory
```
