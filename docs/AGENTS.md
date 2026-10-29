# Documentation

Describe only the current project. Do not include history or speculative plans.
Teaching exercises reconstruct current capabilities. Label advisory production
guidance explicitly and keep it separate from implemented architecture.

## `../README.md`

Keep the project overview, status warning, short usage examples, eight teaching
chapter summaries, and links to detailed documentation here. Match chapter
names and order in `TEACHING.md` and `PRODUCTION.md`. Keep setup, configuration,
and command details in `DEVELOPMENT.md`.

## `DEVELOPMENT.md`

Include local setup, developer tooling, cloud operations, deployment commands,
and instructions for running evaluations.

## `FEATURES.md`

Include only user-visible features, workflows, scope, limitations, and
non-goals. Never include technical details, developer tooling, cloud operations,
deployment information, or evaluation results.

## `ARCHITECTURE.md`

Document only implemented technical design, flows, constraints, limitations,
evaluation architecture, and source links. Link to `FEATURES.md` for product
behavior and `DEVELOPMENT.md` for setup. Exclude proposals and alternatives.

## `PRODUCTION.md`

Organize conditional production guidance by teaching chapter. Distinguish
implemented boundaries, intentional fixtures, and advisory approaches. Explain
the requirement that justifies each recommendation and its tradeoffs.

## `TEACHING.md`

Document the starter-material contract and cumulative reconstruction exercises
for the current implementation. Include coding-agent specifications, chapter
dependencies, observable acceptance checks, recording cues, and social angles.
Source links are presenter references; learner prompts use specifications and
the code accumulated in the rebuild. Keep operational commands authoritative
in `DEVELOPMENT.md`.

## `EVIDENCE.md`

Separate implemented scenario coverage from observed execution results. Report
only actual measurements and working public links, with revision, date,
configuration, scope, and limitations. An evidence capture template is not a
result. Hosted and local execution evidence must be identified separately.
