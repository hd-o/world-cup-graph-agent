# Teaching guide

An English, architecture-led reconstruction of this project's implemented
behavior for experienced software engineers entering AI engineering. The
format demonstrates how to specify, inspect, and verify work performed by a
coding agent. [README](../README.md#chapters) is the reader's chapter hub.

## Recording format

Use OpenCode throughout the cumulative rebuild. Show the task and relevant
context, cut the interval while the agent works, and return to the resulting
code, verification, and working behavior. Keep meaningful corrections when
they explain a design constraint. Label hypothetical traps as hypotheses;
attribute coding-agent mistakes only to observed interactions.

Each chapter opens with its target behavior, then connects implementation to
slides explaining decisions, traps, and conditional production approaches.
The opening can preview the completed reference behavior; identify the preview
and the rebuild workspace clearly. Inspect selected code rather than narrating
every generated file. Record the actual OpenCode and model versions used.

The target is 58 edited minutes:

| Chapter | Minutes |
| --- | ---: |
| 1. System goals and prepared foundation | 4 |
| 2. Neo4j and grounded queries | 7 |
| 3. DeepAgents, tools, and context | 7 |
| 4. Enrichment, approvals, and controlled writes | 9 |
| 5. State, identity, and credentials | 7 |
| 6. Evals and LangSmith | 8 |
| 7. AWS AgentCore Runtime | 8 |
| 8. GCP Agent Runtime and comparison | 8 |

## Prepared foundation contract

The starter workspace contains the following material. Show this inventory
before implementation so viewers can identify the starting assumptions.

- **Data:** the supplied `assets/data/seed/` node and relationship JSON files,
  with source attribution and a recorded dataset revision. Treat the supplied
  data as immutable. `assets/data/2026/` is protected reference material.
- **Dependencies:** Python 3.14+, uv, the reference dependency declarations
  and `uv.lock`, including development and cloud dependency groups. Match
  package metadata to the starter package and verify the locked environment.
- **Environment:** Docker and the required cloud CLIs installed; sanitized
  environment variable templates. Credentials belong in the local environment.
- **Skeleton:** an importable `src/world_cup_graph_agent/` package, empty
  exercise directories, Git ignore rules, and lint/typecheck configuration.
  Expose task commands only as their corresponding implementations exist.
- **Instructions:** project conventions from `AGENTS.md`, the chapter's
  self-contained specification, and the relevant official library guidance.
  Dependency additions require approval; automated behavioral checks use evals.

Bootstrap, database permissions, graph tools, application skills, agent
behavior, eval implementations, and cloud infrastructure are chapter outputs.
Keep the completed source outside OpenCode's rebuild workspace and supplied
context. The presenter references below are for preparing specifications and
reviewing results; do not give the rebuilding agent the completed source or
an instruction to copy it. General project conventions can be supplied.

The exercises target the same architecture and observable behavior. Acceptance
does not depend on generating identical source text. Source paths in this
guide describe the reference; align links with the actual published rebuild.

## Dependency and verification rules

1. Chapter 2 creates local database startup, initialization, reader/writer
   access, and the query boundary. A direct tool invocation needs no chatbot.
2. Chapter 3 supplies local serving, demo identity/credential wiring, and
   checkpoint infrastructure needed to run the first agent. The LangGraph
   server owns local persistence; direct evals supply their own checkpointer.
3. Chapter 4 uses that persistence for separate research and mutation pauses.
   Chapter 5 explains storage, ownership, and credential details in depth.
4. Introduce the existing isolated eval harness and grounded case with the
   first agent, then relevant enrichment/access cases with their capabilities.
   Chapter 6 completes and explains the suite and LangSmith reporting.
5. Chapter 7 establishes the shared hosted contract and policy before the AWS
   edge. Chapter 8 reuses them with the GCP edge.

Use [Development](DEVELOPMENT.md) as the command reference. After Python edits,
run `poe lint` and `poe typecheck` in the activated environment; lint includes
automatic fixes, so inspect its diff. For agent/eval changes, also run
`poe eval-fast` and the affected existing scenarios. While bootstrapping the
harness, identify unavailable checks and implement their prerequisites before
accepting the corresponding milestone. A task name alone is not evidence.

For deployment script changes, run both deploy-help checks, then use actual
deployment and hosted checks to establish hosted behavior. The full eval
fixture starts the development stack; its direct graph scenarios reset the
isolated eval database. Use [Evidence](EVIDENCE.md) to record scope, outcome,
configuration, and limitations. Model calls can incur costs even with uploads
disabled.

## Task brief wrapper

Use this wrapper with each chapter's task block. Context consists of the
chapter specification, accumulated rebuild code, and official documentation.
Architecture links in this guide are presenter reading, not implicit access
to the reference implementation.

> Implement the supplied chapter specification in this workspace. First
> inspect the accumulated code and applicable repository instructions.
> Identify dependencies and the behavior to preserve. Use the configured
> dependencies and project conventions. Make the smallest clear implementation
> that meets the stated contract. Run the applicable checks and inspect their
> results. Report changed boundaries, actual outcomes, and verification gaps.
> Keep implemented behavior distinct from recommendations for production.

## 1. System goals and prepared foundation

**Start:** prepared starter workspace and the separate presenter reference.
**Outcome:** an explicit system contract and a verified development foundation.

**Opening demo:** preview a France FIFA-code answer, then a missing-birthplace
approval card. Show how the second task has a side effect requiring consent.

**OpenCode task:**

> Verify the package, locked dependencies, environment templates, and lint and
> typecheck tasks. Document the application contract: answer World Cup
> questions from successful Neo4j reads; research supported missing facts only
> with approval; apply separately approved structured changes; query stored
> facts again before the final answer. Conversations are private and football
> facts are shared. Identify the configuration needed for local Neo4j and
> model access. Preserve the supplied dataset and require explicit approval
> for dependency additions.

**Verification:** show the starter file inventory, installed environment,
package import, and tooling results. Identify the reference preview as distinct
from this foundation. Ordinary model execution belongs to subsequent stages.

**Slides:** user task and success criteria; football graph versus execution
graph; prepared assets versus implemented capabilities; specification versus
model-generated code; fixtures as an intentional teaching boundary.

**Trap:** a broad “build a chatbot” instruction leaves data ownership,
authorization, and evidence requirements underspecified.

**Presenter reading:** [scope](FEATURES.md#scope),
[two graphs](ARCHITECTURE.md#two-graphs), and
[production context](PRODUCTION.md#1-system-goals-and-prepared-foundation).

**X angle:** “A coding-agent prompt is an engineering contract: define the
observable result before the implementation.”
**LinkedIn angle:** translate a familiar user question into data boundaries,
permitted effects, and acceptance criteria; invite alternative requirements.

## 2. Neo4j and grounded queries

**Start:** foundation, seed files, and environment configuration.
**Outcome:** initialized graph, schema context, and a directly callable reader.

**Opening demo:** show Brazil's group-membership relationships and the rows
that answer “Which teams share a group with Brazil?” Use seeded facts rather
than presenting the dataset as live football reporting.

**OpenCode task:**

> Implement local Neo4j startup and bootstrap for football and checkpoint
> databases, users, constraints, and the supplied seed. Generate the querying
> schema reference from the domain model. Implement a query tool accepting
> Cypher and separate parameters, passing them unchanged through the reader
> account. Return JSON-compatible rows with a truncation flag, at most 25
> rows, a 128 KiB payload bound, and a 120-second query timeout. Distinguish
> errors from successful empty results. Preserve competition-specific squad
> membership and stable entity identifiers.

**Verification:** invoke the tool directly with a relationship query. Inspect
returned rows, serialization, and truncation. Demonstrate database write
denial with the serving reader credentials and a bounded read's behavior.
These are database/tool observations; agent grounding is checked in chapter 3.

**Slides:** graph access patterns and SQL/document alternatives; relationships
and competition scope; database permissions versus prompts; small output
versus expensive computation; query errors versus missing facts.

**Trap:** an agent can implement “read only” as prompt text or a driver hint
while still using a privileged account. Inspect the actual credential path.

**Presenter reading:**
[query contract](ARCHITECTURE.md#query-inputs-and-results),
[bootstrap source][bootstrap], [query source][query], and
[production choices](PRODUCTION.md#2-neo4j-and-grounded-queries).

**X angle:** “A five-row answer can still require an expensive query.”
**LinkedIn angle:** explain why connected-data requirements justify considering
a graph, and which evidence is needed before claiming database superiority.

## 3. DeepAgents, tools, and context

**Start:** initialized databases and the query tool.
**Outcome:** a runnable querying agent with local chat and grounded evidence.

**Opening demo:** submit the France FIFA-code question and inspect a successful
`query_graph` call alongside the final answer.

**OpenCode task:**

> Assemble a DeepAgents supervisor using configurable OpenRouter model and
> provider settings. Give it the query tool and a read-only packaged querying
> skill containing schema instructions. Require answers from successful graph
> evidence and honest reporting of incomplete results. Implement the local
> LangGraph entrypoint, database-driver lifecycle, Neo4j checkpoint saver
> hookup, and Alice/Bob demo identity and shared-key resolution needed to run
> it. Keep credential resolution execution-scoped; introspection must not
> invoke inference. Reconstruct the existing isolated grounded eval harness
> and France FIFA-code case to verify the first complete behavior.

**Verification:** local health and chat, query evidence, `poe eval-fast`, lint,
and typecheck. Show `.agents/skills/` as coding-agent guidance and the packaged
application skill as chatbot context. Inspect inherited tool capabilities.

**Slides:** tools versus instructions; source of context; model/provider
configuration; why a specialist needs an explicit capability boundary;
serving and persistence prerequisites for the next chapter.

**Trap:** schema content helps query generation but does not enforce database
permissions. An impressive answer can still be unsupported by tool results.

**Presenter reading:** [assembly](ARCHITECTURE.md#agent-assembly),
[factory][factory], [local runtime][local], and
[context boundaries](PRODUCTION.md#3-deepagents-tools-and-context).

**X angle:** “Your coding agent's instructions and your chatbot's skills are
two different control surfaces.”
**LinkedIn angle:** show how a role-specific tool list and an inspected trace
make a framework choice concrete rather than a technology checklist.

## 4. Enrichment, approvals, and controlled writes

**Start:** querying agent, persistence, and the isolated eval harness.
**Outcome:** complete missing-fact workflow with separate approvals.

**Opening demo:** use a controlled eval graph with Mbappé's birthplace absent.
Show rejection, then a separate approved attempt with the graph read afterward.

**OpenCode task:**

> Add the graph-enricher specialist with query, research, and mutation tools.
> Keep the supervisor query-only. Package enrichment instructions read-only.
> Gate research and writes independently with native approve/reject
> interrupts; free text does not authorize either action. Preserve sequential
> approval state through the checkpointer. Guard research after rejection or
> usable findings, and prevent inherited workers from bypassing that guard.
> Return structured findings with model-reported sources and a structured
> completion report. Implement create-node, fill-node-properties,
> create-relationship, and fill-relationship-properties operations. Generate
> write Cypher in application code, validate schema and targets, forbid
> replacement/deletion, and commit each approved batch atomically without
> automatic retry. Require the supervisor's post-write graph query. Recreate
> the existing rejected and approved enrichment evals.

**Verification:** run the enrichment scenarios and inspect actual tool order,
graph before/after state, rejection effects, and the final graph read. Explain
which steps rely on model behavior and which the tools enforce. Keep the
mutation tool's transactional guarantee distinct from observed scenario paths.

**Slides:** approval scope; source reliability; typed writes versus generated
read queries; transaction boundary; graph commit/checkpoint failure window;
when external workflow orchestration earns its operational cost.

**Trap:** a prompt saying “research first” does not itself enforce a trajectory,
and consent to research does not authorize publication of a fact.

**Presenter reading:** [workflow](ARCHITECTURE.md#when-information-is-missing),
[mutation tool][mutation], [enrichment evals][enrichment], and
[production choices][enrichment-production].

**X angle:** “Approval authorizes an operation. It does not verify a fact.”
**LinkedIn angle:** use the graph/checkpoint commit gap to explain why an atomic
database operation is only one part of reliable agent execution.

## 5. State, identity, and credentials

**Start:** an agent that can pause and resume approved enrichment.
**Outcome:** verified local conversation boundaries and explained storage roles.

**Opening demo:** show a pending approval, the owner's continuation, and Bob's
denied attempt to read or resume Alice's conversation.

**OpenCode task:**

> Complete conversation authorization using trusted server identity for
> creation, reads, searches, history, messages, and resumes. Reject ownership
> supplied by callers and unsupported state-changing input. Preserve shared
> football knowledge. Resolve the correct model key for every execution and
> resume, with topology inspection avoiding key lookup and inference. Retain
> the explicit local shared-key fixture. Verify checkpoint task-write ordering,
> unique channel versions, and complete history retrieval. Recreate existing
> access and fake-key wiring evals; use distinct fake keys to reveal accidental
> credential reuse without inference.

**Verification:** existing access and credential scenarios plus an owner resume
demonstration. For a recorded restart claim, perform and capture that restart
and inspect both persistence stores. Local API access checks and later hosted
approval replay checks establish different boundaries.

**Slides:** private conversations/shared facts; checkpoints/server records;
trusted identity versus supplied identifiers; local fixture versus managed
secrets; display masking versus encryption; recovery and rotation requirements.

**Trap:** “add tenant isolation” can lead a coding agent to isolate the shared
football graph while leaving conversation ownership incomplete.

**Presenter reading:** [access](ARCHITECTURE.md#conversation-access),
[storage](ARCHITECTURE.md#persistence-and-approval-resumes),
[access evals][access], [credentials eval][credentials], and
[production choices](PRODUCTION.md#5-state-identity-and-credentials).

**X angle:** “Private conversations and shared knowledge need different rules.”
**LinkedIn angle:** use the shared-key fixture to explain why verification with
distinct identities and credentials matters even in a small teaching project.

## 6. Evals and LangSmith

**Start:** runnable local behavior and incrementally introduced existing evals.
**Outcome:** the basic reference suite, reporting, and inspectable evidence.

**Opening demo:** inspect a grounded result and an enrichment trace with
objective checks and separate judge feedback. Show real outcomes, including
failures or limitations relevant to the lesson.

**OpenCode task:**

> Complete the existing grounded, enrichment, access, credential, adapter,
> and opt-in hosted scenario definitions as their prerequisites exist.
> Grounded checks require reference facts in both final text and successful
> graph rows, with no research or mutation execution. Enrichment checks actual
> execution, stored state, and a post-write supervisor query before separately
> judging prose. Collect isolated-subagent tool evidence. Keep direct graph
> scenarios on the isolated eval database and preserve fail-fast reset checks.
> Configure LangSmith pytest reporting for grounded/enrichment cases with
> model metadata and observed costs. Keep access and credential results in
> ordinary pytest reporting. Require explicit authorization for hosted calls.

**Verification:** run the existing local suite, inspect a trace and graph state,
and reconcile reported cases with pytest outcomes. Hosted results belong to
the actual deployment chapters. Record configuration, revision, case counts,
and limitations using [Evidence](EVIDENCE.md). Validate public sharing links
without the author's account before publication.

**Slides:** assertion versus judge; coverage versus correctness guarantees;
why the fast case is narrow; isolation and repeatability; inference costs
with uploads disabled; tracing content and access.

**Trap:** matching facts in text/rows does not establish complete relational
correctness. A model judge does not replace side-effect or ownership checks.

**Presenter reading:** [eval architecture](ARCHITECTURE.md#evaluations),
[scenario source][grounded], [execution evidence][execution], and
[production choices](PRODUCTION.md#6-evals-and-langsmith).

**X angle:** “Green evals are evidence for named cases, not a universal claim.”
**LinkedIn angle:** walk through one result from input to tool evidence to
stored state and explain exactly what each assertion establishes.

## 7. AWS AgentCore Runtime

**Start:** validated local application and cloud tooling/account configuration.
**Outcome:** shared hosted application policy and an exercised AWS deployment.

**Opening demo:** show a hosted grounded answer and a pending approval through
the conversation client. Use readable JSON/API output rather than implying
the local chat UI supplies the hosted transport.

**OpenCode task:**

> Implement the strict hosted JSON conversation contract: create, list, read,
> send, and resume. Preserve immutable ownership, admission fencing, pending
> approval validation, consumed-card denial, and uncertain-execution handling.
> Implement AWS AgentCore integration with Cognito token verification,
> identity-scoped Secrets Manager credentials, and Neo4j checkpoints. Define
> CDK resources for private Neo4j on EC2, verified Bolt TLS, runtime VPC
> connectivity, NAT egress, and filtered telemetry. Package application skills
> and schema with the serving artifact. Reconstruct the build, deploy, inspect,
> enrollment, conversation, status, and teardown command lifecycle, including
> exact artifact timestamps and database replacement safeguards. Update the
> deployment diagrams with the implemented request and network paths.

**Verification:** both deploy-help checks, build inspection, deployment,
authenticated invocation, and AWS-selected hosted scenarios. Inspect actual
database connectivity and credential resolution. Show that local adapter
checks exercise policy while the hosted checks exercise the deployed boundary.
Record deployment identity/timestamp and results. Follow lifecycle commands in
[Development](DEVELOPMENT.md#hosted-environments).

**Slides:** shared policy/cloud edge; runtime identity versus conversation
ownership; private database/external inference; artifacts and state lifecycle;
managed runtime versus database operations; ongoing infrastructure cost.

**Trap:** deployment success does not prove identity isolation or approval
continuation. Conversely, CLI help proves only that a command loads.

**Presenter reading:** [AWS diagrams](ARCHITECTURE.md#aws-overview),
[hosted contract][contracts], [hosted policy][hosted], [AWS stack][aws], and
[production choices](PRODUCTION.md#7-aws-agentcore-runtime).

**X angle:** “Managed agent compute still needs an application state contract.”
**LinkedIn angle:** trace one request through identity, secrets, graph access,
and external inference, connecting each service to a concrete requirement.

## 8. GCP Agent Runtime and comparison

**Start:** shared hosted policy, validated AWS behavior, and GCP configuration.
**Outcome:** an exercised GCP deployment with a grounded cloud comparison.

**Opening demo:** perform the same conversation operation through GCP and
compare the application response, then reveal the different request path.

**OpenCode task:**

> Reuse the hosted application policy and strict conversation contract.
> Implement a Cloud Run gateway that verifies tenant-specific Identity
> Platform tokens and invokes a separate internal Cloud Run agent with a
> trusted normalized principal and audience-bound Google ID token. Grant agent
> invocation only to the gateway workload identity. Build separate agent and
> gateway tar.gz container contexts with Dockerfiles and locked requirements.
> Publishing builds both images with Docker, pushes them to one Artifact
> Registry repository, and records immutable digests for deployment. Configure
> agent min/max instances 1/2, two CPUs, 4 GiB, concurrency four, and
> always-allocated CPU. Use identity-scoped Secret Manager lookup and private
> Neo4j TLS. Define separate IPv4 Direct VPC subnets: gateway
> private-ranges-only with private run.app DNS and Private Google Access;
> agent all-traffic with Cloud NAT, Private Google Access, and private
> googleapis.com DNS. Use private Google
> VIPs 199.36.153.8/30. Associate a global NGFW Standard policy evaluated
> BEFORE_CLASSIC. Source-scope every rule to the agent subnet: allow private
> database TCP 7687, private Google VIP TCP 443, and exact openrouter.ai TCP
> 443, then explicitly deny other egress. Explain DNS-derived IP matching and
> shared-IP access, ordinary DNS, IAM limits on shared Google frontends, and
> unproven FQDN failure semantics. Reconstruct protected artifacts/state and
> metadata-filtered telemetry. Update GCP diagrams and preserve shared
> conversation behavior. Cloud operations require explicit approval.

**Verification:** deploy-help checks, both build contexts, and published image
digests, followed by an explicitly approved isolated deployment, gateway
invocation, GCP-selected hosted scenarios, and the
[manual network checklist](DEVELOPMENT.md#gcp-network-smoke-checklist).
Verify private database/Google APIs/telemetry, credential-free OpenRouter TLS,
unrelated disjoint-IP and port-80 denial, and external/internal agent invocation
denials. Resolve FQDN failure uncertainty through Google clarification or an
isolated controlled-FQDN test, without altering real OpenRouter DNS. Record
teardown; use temporary diagnostics rather than permanent debug endpoints.
Compare the same application scenarios on both clouds; report costs or latency
only when evidence captures comparable conditions.

**Slides:** gateway trust and normalized principals; internal ingress plus IAM;
Direct VPC, NAT, and FQDN/IP-policy limitations; immutable image digests;
application portability versus cloud integrations; independent persistence;
observed deployment evidence and resource lifecycle.

**Trap:** a shared application contract does not imply identical ingress,
streaming, networking, or replicated cross-cloud data.

**Presenter reading:** [GCP diagrams](ARCHITECTURE.md#gcp-overview),
[gateway][gateway], [Terraform][gcp], [hosted scenarios][cloud-evals], and
[production choices](PRODUCTION.md#8-gcp-agent-runtime-and-comparison).

**X angle:** “Portability lives in the contract; cloud identity differs.”
**LinkedIn angle:** compare AWS and GCP by responsibility and observable
behavior, explaining why a service-name substitution is insufficient.

## Recording and publication workflow

1. Prepare the starter inventory and chapter specifications from this
   reference. Rehearse dependency order and keep the reference outside the
   rebuilding agent's context.
2. Record the cumulative OpenCode sessions. Capture actual prompts, selected
   implementation results, verification, and demos at each milestone. Keep
   the unedited execution evidence available during editorial review.
3. Populate the evidence report from the rebuilt implementation. Reconcile
   commands, links, diagrams, and feature descriptions against that code.
4. Rehearse the edited sequence against the 58-minute budget. Use a progress
   review after two focused working weeks to set a readiness-based launch date.
5. Publish the validated rebuild and its documentation in a new public GitHub
   repository. Identify the actual repository and commit in its evidence.
   Video links follow its main branch; viewing notes belong in the video
   description when required. Link only published videos and working traces.
6. Release the video with the repository, then publish chapter-derived X and
   LinkedIn content. Each post carries a useful lesson, concrete evidence or
   diagram, its limits, and a relevant link or technical discussion question.

The public experience consists of reproducible setup, recordings, and dated
cloud execution evidence. Include resource lifecycle instructions; deployment
evidence does not promise a continuously available hosted demo. Publication
requires confirming rights and attribution for the supplied dataset.

[bootstrap]: ../src/world_cup_graph_agent/infrastructure/neo4j/bootstrap.py
[query]: ../src/world_cup_graph_agent/tools/query_graph.py
[factory]: ../src/world_cup_graph_agent/agent/factory.py
[local]: ../src/world_cup_graph_agent/runtimes/local.py
[mutation]: ../src/world_cup_graph_agent/tools/mutate_graph/tool.py
[enrichment]: ../evals/enrichment/scenarios.py
[access]: ../evals/auth/access.py
[credentials]: ../evals/auth/credentials.py
[grounded]: ../evals/grounded/scenarios.py
[execution]: ../evals/evidence.py
[contracts]: ../src/world_cup_graph_agent/runtimes/contracts.py
[hosted]: ../src/world_cup_graph_agent/runtimes/hosted.py
[aws]: ../deploy/aws/stack.py
[gateway]: ../src/world_cup_graph_agent/runtimes/gateway.py
[gcp]: ../deploy/gcp/runtime.tf
[cloud-evals]: ../evals/hosted/scenarios.py
[enrichment-production]:
  PRODUCTION.md#4-enrichment-approvals-and-controlled-writes
