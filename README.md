# ⚽️ World Cup Graph Agent

A graph-backed chatbot built with DeepAgents and Neo4j. It answers World Cup
questions from stored facts, researches missing information with permission,
and proposes graph updates for a separate approval. OpenRouter supplies model
inference and research; LangSmith visualizes evaluation traces and feedback.

**This is a teaching demo project, not a production service.**

## Try it with GitHub Codespaces

https://github.com/user-attachments/assets/3e605016-0a90-4efd-9182-df426702faab

## What you learn

For software engineers familiar with APIs and databases who want practical
agent-engineering experience. The chapters connect a working demonstration
to an architectural decision, its failure modes, verification, and production
implications. They also explain how to give a coding agent the context and
constraints needed to implement that decision.

The central question is: **what should the model decide, what should the
application enforce, and what evidence establishes that it works?**

## Try the chatbot

Follow [local setup and Agent Chat instructions](docs/DEVELOPMENT.md) to
configure credentials, initialize Neo4j, and connect the browser interface.
Then try:

- “What is the FIFA code for France?” for a grounded lookup.
- “Which teams share a group with Brazil?” for a relationship-based query.
- “What is Mbappe's birthplace?” for enrichment when that fact is missing.

Research and graph changes require separate approve/reject decisions.
Questions describe the seeded knowledge base; coverage depends on its data.
See [Features](docs/FEATURES.md) for supported behavior and limitations.

## Chapters

The [teaching guide](docs/TEACHING.md) uses these same eight chapters for an
edited, cumulative OpenCode rebuild. Each chapter shows the instruction and
the resulting implementation, with agent execution time cut from the video.
The guide includes starter materials, acceptance checks, slide points, and
standalone X and LinkedIn content angles. Its recording budget is 58 minutes.

### 1. System goals and prepared foundation

**Demonstration:** a grounded answer and a preview of the missing-fact flow.

Define the system's promise, data boundaries, and observable outcomes. Prepare
the dataset, dependencies, and project skeleton. Distinguish the football
graph in Neo4j from the execution graph coordinating the agent.

**Coding-agent lesson:** specify the target behavior and acceptance evidence
before asking for implementation.

Read: [two graphs](docs/ARCHITECTURE.md#two-graphs) and
[foundation brief](docs/TEACHING.md#1-system-goals-and-prepared-foundation).

### 2. Neo4j and grounded queries

**Demonstration:** follow a group-membership query from question to graph rows.

Implement schema-aware retrieval with database-enforced read permissions and
bounded results. Explain when relationships make this model useful and how
SQL or document retrieval would address different requirements. This project
does not establish comparative database performance.

**Coding-agent lesson:** require database permissions and explicit result
semantics; a prompt saying “read only” is insufficient enforcement.

Read: [query boundary](docs/ARCHITECTURE.md#query-inputs-and-results) and
[query tool](src/world_cup_graph_agent/tools/query_graph.py).

### 3. DeepAgents, tools, and context

**Demonstration:** inspect a grounded answer's tool call and packaged context.

Assemble the querying agent, model configuration, and local execution path.
Explain tool capabilities, packaged skills, and specialist delegation.
Repository instructions guide the coding agent; packaged application skills
guide the chatbot. These are separate instruction surfaces.

**Coding-agent lesson:** define each agent's capabilities explicitly and
inspect inherited framework tools as well as the configured specialist.

Read: [agent assembly](docs/ARCHITECTURE.md#agent-assembly) and
[agent factory](src/world_cup_graph_agent/agent/factory.py).

### 4. Enrichment, approvals, and controlled writes

**Demonstration:** reject research, then approve a separate enrichment attempt
and inspect the stored fact and supervisor's follow-up query.

Implement research, delegation, distinct approvals, typed mutations, and
atomic batches. Explain why approval and structural validation do not verify
a source's truth, and why a graph transaction does not include checkpoints.

**Coding-agent lesson:** specify exact approval boundaries and observable
side effects, including what rejection must prevent.

Read: [enrichment flow](docs/ARCHITECTURE.md#when-information-is-missing) and
[enrichment scenarios](evals/enrichment/scenarios.py).

### 5. State, identity, and credentials

**Demonstration:** resume a pending action and deny another user's access.

Explain durable state, conversation ownership, and credential resolution.
Football facts are shared; conversations are private. Local Alice/Bob tokens
and shared-key lookup are teaching fixtures. Hosted identities use managed
per-user secrets. `SecretStr` masks display; it is not encryption.

**Coding-agent lesson:** identify exactly which data is private and derive
ownership from trusted authentication rather than caller-supplied metadata.

Read: [conversation access](docs/ARCHITECTURE.md#conversation-access) and
[persistence](docs/ARCHITECTURE.md#persistence-and-approval-resumes).

### 6. Evals and LangSmith

**Demonstration:** inspect scenario outcomes, tool execution, stored graph
state, and a LangSmith trace with feedback.

Implement and explain the existing eval families. Separate objective checks
from the enrichment judge's assessment of prose. A fast grounded-answer
scenario provides a narrow signal; enrichment, access, adapters, and hosted
execution need their own evidence.

**Coding-agent lesson:** ask for verification of the affected behavior and
an explanation of what passing checks leave uncertain.

Read: [eval architecture](docs/ARCHITECTURE.md#evaluations),
[eval commands](docs/DEVELOPMENT.md#evaluations), and
[evidence status and capture requirements](docs/EVIDENCE.md).

### 7. AWS AgentCore Runtime

**Demonstration:** invoke the hosted application and inspect its conversation
and approval responses.

Implement the shared hosted policy and AWS integration. Follow Cognito
identity, scoped Secrets Manager access, private Neo4j TLS connectivity,
NAT egress to OpenRouter, and filtered telemetry through the deployment.

**Coding-agent lesson:** specify identity, networking, packaging, and runtime
contracts together. A successful build or command-help check is distinct
from a successful deployed invocation.

Read: [AWS overview](docs/ARCHITECTURE.md#aws-overview),
[CDK stack](deploy/aws/stack.py), and
[deployment commands](docs/DEVELOPMENT.md#hosted-environments).

### 8. GCP Agent Runtime and comparison

**Demonstration:** exercise the same conversation contract through GCP.

Implement the GCP integration and compare its public Cloud Run gateway,
internal Cloud Run agent, Identity Platform, Secret Manager, private database
connectivity, and network egress policy with AWS. Both hosted paths use JSON
request/response; local Agent Chat has its own serving path. The deployments
have independent data stores.

**Coding-agent lesson:** preserve shared application semantics while making
cloud-specific identity and transport boundaries explicit.

Read: [GCP overview](docs/ARCHITECTURE.md#gcp-overview),
[runtime infrastructure](deploy/gcp/runtime.tf), and
[hosted contract](src/world_cup_graph_agent/runtimes/contracts.py).

## Reference guides

- [Development](docs/DEVELOPMENT.md): setup, configuration, and commands.
- [Features](docs/FEATURES.md): user-visible scope and behavior.
- [Architecture](docs/ARCHITECTURE.md): implemented design and diagrams.
- [Production considerations](docs/PRODUCTION.md): conditional guidance
  organized by chapter, with implementation boundaries made explicit.
- [Teaching guide](docs/TEACHING.md): cumulative build and recording briefs.
- [Evidence](docs/EVIDENCE.md): coverage, results status, and reproducibility.

## Neo4j License

You must provide your own valid Neo4j Enterprise license to run this in production.
The startup scripts set `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`. This repository's
MIT license covers its code; it does not grant a Neo4j Enterprise license.
