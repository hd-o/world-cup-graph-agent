---
name: world-cup-enrichment
description: Research missing World Cup facts with research_web and propose atomic mutate_graph batches with fill-only updates, sequencing research before mutation and reporting orchestration status.
---

# World Cup Enrichment

Use this skill only in the graph enricher. It covers approved research, atomic mutation, and the post-mutation report.

## Schema reference

Read the schema reference packaged with the querying skill at `/skills/querying/world-cup-querying/schema.json` before proposing any mutation.

It is the source for labels, relationship types, persisted property names, creation-time properties, and fillable properties. Do not guess spellings. The querying skill describes how to read current graph state with `query_graph`.

## research_web usage

Call `research_web` with only these arguments:

```python
research_web(query: str, reason: str)
```

`query` states the fact gap in one question. `reason` states why the graph needs it. Limits and model selection are configuration, never caller arguments. Web access is server search and fetch only, with bounded result counts and content sizes.

The tool allows one initial model attempt and one structured-output repair attempt with the same server limits. When both attempts fail, it returns no findings and names the query unanswered instead of guessing.

Results use strict types. Each finding carries `question`, a scalar `answer`, `explanation`, and `sources` with at least one source. Each source carries `url`, `title`, and `excerpt`. The result also carries `unanswered_questions`. String and list sizes stay bounded so approval payloads stay bounded.

Sources are model-reported and are never verified citations. The application cannot prove that a returned URL or excerpt came from a server search result. Describe them as reported sources in reasoning and approval text, never as verified citations. Source links carry `HttpUrl` validation only.

## Mutation operations

Call `mutate_graph` with one call carrying the complete ordered operation list plus a batch-level `risk_description`:

```python
mutate_graph(operations: list[MutationOperation], risk_description: str)
```

Each flat operation carries the `op` enum, the entity or relationship type selector, reference ids, one typed payload slot, a mutation-specific `reason`, and complete `ResearchFinding` evidence. The four operation kinds are `create_node`, `fill_node_properties`, `create_relationship`, and `fill_relationship_properties`. No update, replace, delete, arbitrary Cypher, or arbitrary property map exists.

Every operation requires a mutation-specific reason and one or more complete research findings as evidence with the exact typed payload. Each evidence entry is one whole `ResearchFinding` object, exactly as `research_web` returned it in its result, carrying `question`, `answer`, `explanation`, and `sources`, where each source carries `url`, `title`, and `excerpt`. Copy findings verbatim: entries carrying only source texts without the `question` field fail argument validation before any write. The check covers typed shape only and cannot detect invented, trimmed, or altered content, so verbatim reuse of the returned findings stays a required instruction even where reshaped content would keep the typed shape. The top-level call requires the complete ordered list and the batch-level risk description. The reviewed arguments and the executed arguments are the same object.

Node creation is available for the creatable types named in the schema reference. The single tournament root has no creation contract. Handlers call the registered `build_id` for the type, then write `id` with every create-model field. Creating against an existing `id` fails the batch.

Node fills identify the existing node by stable `id` and concrete node type and use the registered fill model. Each fill accepts several properties in one operation, excludes `id` and identity properties, and requires at least one supplied property.

Relationship creation uses one strict shape for the propertyless types and a separate concrete shape for `PLAYS_FOR`, the single type with properties. `PLAYS_FOR` creation carries the two endpoint ids with `competition_id` and optional `position` and `shirt_number`, and handlers build its deterministic relationship `id`.

Relationship fills support only `PLAYS_FOR`, identified by stable relationship `id`, and allow only `position` and `shirt_number`.

## Fill-only updates and idempotent creation

Fills only add currently missing values. Every supplied property must be missing. When any supplied property is already populated, the operation fails and the complete batch rolls back. This includes an equal-value replay where the existing value equals the proposed value. No remaining property is partially applied.

Propertyless relationship creation matches both endpoints, then merges only that relationship with no property write. A missing endpoint fails the batch. When the merge creates the relationship, the outcome is `created`. When the start, end, and type are already linked, the existing pair stays unchanged and the outcome is `already_present`. That success does not roll back the batch.

`PLAYS_FOR` creation matches both endpoints and creates the relationship. It never merges. A missing endpoint or a duplicate deterministic relationship `id` fails the batch through the uniqueness check.

The batch runs inside one explicit writer transaction, in approval order, with one handler per operation kind. Labels, relationship types, and endpoint labels come from the schema registries, never from raw text. Property values travel as parameters. Any failed precondition, lookup, validation, constraint, or persistence step rolls back the entire transaction. There is no partial report and no automatic retry. Return failures to the enricher, which may query the graph and decide on a new approved proposal.

Success returns `status` `applied` with per-operation outcomes carrying `index`, `operation`, `result` of `created` or `already_present`, and `target_id` only for a created node or a created `PLAYS_FOR` relationship. Failure returns `status` `failed` with `failed_index`, `code`, a concise bounded message, and `rolled_back` `true`.

## Approval sequencing

Both enrichment tools are separately protected. `research_web` is protected by the first approval. `mutate_graph` is protected by the second approval. Allowed decisions are only `approve` and `reject`. No web spend occurs before research approval and no graph write occurs before mutation approval.

Always execute research before mutation, sequentially. Run `research_web` first, wait for its findings, read the graph state needed to ground the proposal, then propose one `mutate_graph` batch. Do not combine research and mutation in one parallel step. A mixed batch cannot consume the pending research result.

## Rejections

A reject decision ends the enrichment run even when no mutation result
exists. Return `research_rejected` or `mutation_rejected` immediately,
naming the denied operation in the `summary` and the `failure` message.
Never call the rejected tool again in that run, even when the task text
asks to perform the operation; only a new approved request starts new
research or mutation.

## Stable ids

Reference existing nodes and relationship endpoints only by stable `id` values returned from `query_graph`. Use a returned node `id` for fill targets and endpoint fields, and a returned relationship `id` for relationship fill targets.

Never supply an `id` for a new entity. Creation contracts exclude `id`. Handlers build node and `PLAYS_FOR` ids deterministically before persistence.

## Post-mutation report

Return `GraphEnrichmentReport` as orchestration status only, never as answer evidence:

```python
GraphEnrichmentReport(status, summary, changed_ids, failure)
```

`status` is `enriched`, `no_change`, `research_rejected`, `mutation_rejected`, or `failed`. The report carries a short `summary`, a `changed_ids` list, and an optional `failure` message. It carries no full findings, mutation payloads, database rows, or suggested final answer.

Return `enriched` or `no_change` only from a `mutate_graph` tool result;
never report either from graph reads or research findings alone. While
no protected call is rejected, keep following the sequence until
`mutate_graph` returns a result. An approved research result carrying no
usable findings is the one exception: with no evidence no supported
mutation proposal exists, so either request further research, which
keeps its ordinary approval card, or return `failed` immediately with
the unanswered question in the failure message; `no_change` and
`enriched` stay reserved for applied `mutate_graph` results. Return
`enriched` when any outcome is
`created`. Return `no_change` with an empty list when every applied
outcome is `already_present`. `changed_ids` lists created node ids and
created `PLAYS_FOR` ids. A created propertyless relationship has no
`id`, so it contributes to `enriched` without adding an entry.

Expect the supervisor to re-query. After `enriched`, the supervisor calls `query_graph` again for the original question and answers only from those rows.
