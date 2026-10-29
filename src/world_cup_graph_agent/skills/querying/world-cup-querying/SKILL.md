---
name: world-cup-querying
description: Query the World Cup graph with query_graph and Neo4j Cypher, projecting scalar properties and repairing structured failures using the packaged schema reference.
---

# World Cup Querying

Use this skill for every read of the World Cup graph.

## Schema reference

Read `schema.json` in this skill directory once per conversation before
writing Cypher, and reuse that reference instead of re-reading it.

It is the generated reference for labels, relationship types, persisted property names, and Python annotations. Each node and relationship entry carries `create` and `fill` property maps alongside creation-time and fillable properties; a missing `create` or `fill` map means that contract does not exist for the entry. Bare annotation types are required, `X | None` annotations are optional. Use it as the source for every label, type, and property spelling. Do not guess spellings from chat history.

## query_graph contract

Call `query_graph` with this signature:

```python
query_graph(cypher: str, parameters: dict[str, JsonValue])
```

`JsonValue` is `null`, string, boolean, integer, float, list of JSON values, or object with string keys and JSON values.

Parameters travel directly to Neo4j. Do not inspect, rewrite, or wrap the statement in Python. Neo4j reports missing or invalid parameters.

The tool returns a strict tagged union:

- Success carries `status`, `rows`, and `truncated`.
- Failure carries `status`, `code`, and `message`.

The calling model owns Cypher generation, query strategy, result interpretation, query repair, and recovery decisions. Python owns only bounded, read-only execution and typed transport. Neo4j role checks are the read-only boundary. Driver read mode is routing only.

## Cypher guidance

Write read-only Neo4j Cypher with parameters for every value:

```cypher
MATCH (n)
WHERE n.id = $id
RETURN n.id AS id
```

Filter and join on returned values. Pass each value as a parameter such as `$id`. Keep each query focused on one question so failures stay easy to repair. Prefer `WHERE`, `WITH`, `ORDER BY`, and `LIMIT` to narrow the working set before returning rows.

## Identity guidance

Every node carries a deterministic string `id` that handlers build before persistence. Creation and fill contracts exclude it, so never invent an `id` when reading.

Reference existing nodes only by stable `id` values returned from `query_graph`. Use a returned `id` as the `$id` filter in the next query or as the join key across matches. When a lookup needs a human-facing value, read the row first and carry its `id` forward rather than reconstructing identity text.

## Scalar projection

Project JSON-compatible scalar properties explicitly with aliases. Return only `null`, string, boolean, integer, float, lists of those values, and objects with string keys and those values.

Returning whole graph elements fails. Neo4j node objects, relationship objects, path objects, temporal values, spatial values, bytes, and any other driver-native value return an `unsupported_value` failure instead of a stringified value. Mappings with non-string keys fail the same way.

When a query returns `unsupported_value`, rewrite it to project named scalar properties:

```cypher
MATCH (a)-[r]->(b)
WHERE a.id = $start_id
RETURN b.id AS id
ORDER BY b.id ASC
```

## Match results

A `Match` lists its teams as `team1` and `team2`, in the order the source lists them. Neither is a home or away side. The `TEAM1` and `TEAM2` relationships and the `team1_id` and `team2_id` properties carry them.

`score_team1` and `score_team2` are the total score, including extra time and excluding a penalty shootout. The `score_halftime_*`, `score_fulltime_*`, `score_extratime_*`, and `score_penalties_*` pairs hold the stages; extra-time and penalty pairs exist only for matches that reached them. `score_extratime_*` is cumulative, so it already includes the full-time goals.

No property stores a winner. Derive it from the score:

- The team with the higher `score_team1` or `score_team2` won.
- When the totals are level and `score_penalties_*` exists, the team with more penalties won.
- When the totals are level without penalties, the match was a draw.

The World Cup winner is the winner of the `Final` round match of the finals competition. Read that match row and answer from it. A stored `0-0` full-time score next to an extra-time score is normal, not a data gap, so it never needs enrichment.

## Data conventions

- Squad positions live on `PLAYS_FOR.position` as `GK`, `DF`, `MF`, and `FW`. A striker or forward is `FW`; no finer role exists.
- `Match.round` is text. Group matches read `Matchday 1` to `Matchday 17`. Knockout rounds are `Round of 32`, `Round of 16`, `Quarter-final`, `Semi-final`, `Match for third place`, and `Final`, and `Match.knockout` is `true` for them. Order matches by `date`, not by `round` text.
- A goal's `minute` is text such as `45+6`. `minute_base` is the integer part and `minute_stoppage` the added time, absent without stoppage time. Order a match's goals by `minute_base`, then `coalesce(minute_stoppage, 0)`.
- `scorer_name` is the scorer's name as the source writes it. `scorer_id` is absent when no squad player matched, so read `scorer_name` for who scored.
- `Goal.penalty` marks a penalty kick during play. A penalty shootout is `score_penalties_*` on the `Match`, and its kicks are not goals.
- An own goal has `SCORED_BY` on the player who put it in, `CREDITED_TO` on the opposing team that gets the goal on the scoreboard, and `CONCEDED_BY` on the scorer's own team. A team's goals for are the `Goal` nodes credited to it.

## Query patterns

Top scorers, leaving out own goals:

```cypher
MATCH (g:Goal)
WHERE g.own_goal = false
OPTIONAL MATCH (g)-[:SCORED_BY]->(p:Player)
RETURN coalesce(p.name, g.scorer_name) AS name, count(g) AS goals
ORDER BY goals DESC, name ASC
LIMIT 10
```

A team's last match, which shows how far it went:

```cypher
MATCH (t:Team {id: $team_id})<-[:TEAM1|TEAM2]-(m:Match)
RETURN m.round AS round, m.date AS date
ORDER BY m.date DESC
LIMIT 1
```

A team's goals for, including own goals credited to it:

```cypher
MATCH (g:Goal)-[:CREDITED_TO]->(t:Team {id: $team_id})
RETURN count(g) AS goals
```

## Structured errors and repair

Treat every failure as structured data, never as empty rows. A failure preserves the Neo4j error code when one exists and uses a stable project code for transport, payload, timeout, and unsupported-value errors. Messages are bounded and redacted and never carry credentials.

Missing databases, authorization failures, syntax errors, timeouts, and unsupported values are failures. No database is created and no error becomes empty rows.

Repair from the returned `code` and `message`:

- Syntax or missing-parameter errors: fix the statement or the parameter map and run again.
- `unsupported_value`: project scalar properties as shown above.
- Timeout or memory termination: narrow the match, add a selective `WHERE` clause, return fewer aliases, or split the question into smaller reads.
- Authorization errors: rewrite the request as a plain graph read.

## Read bounds

The model can observe four bounds:

- Row bound: at most 25 rows return. The tool fetches one sentinel past the bound and sets `truncated` to `true` when more rows exist. It does not report an exact omitted count. Narrow the query or page with `ORDER BY` plus `LIMIT` when `truncated` is `true`.
- Payload bound: the serialized rows must fit within 128 KiB. An oversized payload is rejected rather than shortened inside cells. Project fewer aliases or narrower ranges and run again.
- Per-query timeout: each query has a 120 second bound applied as a server-side query timeout and as a client-side async timeout. A timeout surfaces as a failure with a bounded message.
- Transaction-memory bound: the application database limits one transaction to 64MiB of server memory. A query that exceeds it is terminated by the database while the database stays healthy. That termination surfaces as a repairable execution failure with the Neo4j code preserved, a bounded and redacted message, and never as empty rows. Repair it the same way as a timeout.
