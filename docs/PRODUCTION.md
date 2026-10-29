# Production considerations

This companion follows the [README chapters](../README.md#chapters).
**Production approaches here are conditional guidance, not implemented
capabilities or a production-readiness assessment.** The reference workload
is a small public, multi-user chatbot. Different availability, privacy, and
funding requirements justify different choices.

[Architecture](ARCHITECTURE.md) describes the implementation and cloud
diagrams. [Teaching](TEACHING.md) connects each boundary to coding-agent
instructions; [Evidence](EVIDENCE.md) distinguishes coverage from results.

## 1. System goals and prepared foundation

**Implemented boundary.** The system queries structured football facts and
can enrich supported gaps. Its source data and research have distinct trust
properties. A supported question need not have a known answer.

**Production approach.** Start with a concrete user workflow and acceptance
criteria: grounded answers, permitted side effects, latency, and spending.
Define which sources are authoritative and how incorrect shared facts are
corrected. Confirm data licensing and provenance for the publication context.

Measure representative tasks before choosing service-level objectives
(targets for availability or response quality). A demonstration establishes
observed behavior, not customer adoption or performance at enterprise scale.

**Tradeoff.** Broader coverage increases data stewardship and evaluation work.
A bounded domain makes failure analysis and human review more tractable.

## 2. Neo4j and grounded queries

**Implemented boundary.** The query tool accepts model-written Cypher and
parameters. Neo4j reader permissions enforce read-only execution. Results
have a 25-row limit, a 128 KiB payload limit, a truncation flag, and a
120-second timeout. Empty results and failed queries have different meanings.

**Production approach.** For predictable public requests, consider
parameterized query templates. For exploratory questions, retain flexible
queries with appropriate database permissions and query profiling. Bound
transaction memory and execution as well as serialized output: a small
answer can require expensive computation.

Define request, tool-call, token, and currency budgets where spending requires
enforcement. Approval of research is not a budget for ordinary model calls.
Verify timeout behavior at the database, not just the waiting application.

**Tradeoff.** Templates narrow the query space and simplify review but reduce
question flexibility. Graph traversal fits connected records; SQL joins and
document/vector retrieval deserve consideration for their own access
patterns. This repository supplies no comparative performance benchmark.

Sources: [Neo4j transactions][transactions] and
[database resource limits][limits].

## 3. DeepAgents, tools, and context

**Implemented boundary.** The supervisor has graph reads. Its compiled
enricher has reads, research, and typed mutations. Packaged skills are
read-only application instructions. The enricher's general-purpose worker
is disabled; the supervisor's inherited worker has querying capabilities.

**Production approach.** Choose delegation when a specialized capability or
context boundary warrants it. Compare against a single agent or deterministic
workflow using the actual task requirements. Extra agents introduce more
handoffs and execution evidence to inspect.

Treat graph values, web pages, snippets, and user text as data rather than
trusted instructions. Enforce permissions at tool and database boundaries.
For less-trusted inputs, evaluate adversarial examples that attempt to turn
retrieved content into authorization or change an approved operation.

**Tradeoff.** Prompts express intent and guide reasoning; deterministic
capability restrictions constrain effects. Neither alone establishes that
the system selects correct facts or follows every intended reasoning step.

Source: [DeepAgents customization][customization].

## 4. Enrichment, approvals, and controlled writes

**Implemented boundary.** Research and mutation have separate approvals.
Mutation accepts four structured create/fill operation types, generates
write Cypher in application code, and applies a batch in one transaction.
It does not automatically retry. Citations are model-reported; approval and
schema validation do not establish factual accuracy.

**Production approach.** Where shared-data integrity matters, retain source,
retrieval time, claim, proposal, approver, and outcome provenance. Verify
source support independently and consider curator approval for shared facts.
Separate a user's permission to spend on research from permission to publish.

Graph commits and checkpoints are separate operations. For repeated delivery,
use a stable proposal identifier bound to the exact payload, with a durable
outcome receipt stored in the same graph transaction as the mutation. Test
the crash between graph commit and checkpoint completion. Reconcile uncertain
outcomes before authorizing a retry. Receipt design itself needs concurrency
verification; a check followed by a separate write can race.

Keep conversation-bound approvals in the agent workflow when that meets the
requirement. Scheduled enrichment, independent workers, or curator queues
can justify external workflow orchestration, with additional operational and
authorization responsibilities.

**Tradeoff.** Human review introduces latency and depends on review quality.
Atomic batches prevent partial writes; they do not verify source truth or
make graph changes and checkpoints one transaction. Concurrent fill-only
behavior needs dedicated evidence before making a concurrency guarantee.

Sources: [LangGraph interrupts][interrupts],
[idempotency guidance][idempotency], and [Neo4j transactions][transactions].

## 5. State, identity, and credentials

**Implemented boundary.** Football facts are shared. Conversations are
owner-restricted. Local demo tokens identify Alice and Bob, whose credential
fixture returns the same operator OpenRouter key. `SecretStr` masks display;
it does not encrypt credentials. Hosted resolvers retrieve identity-scoped
secrets through AWS Secrets Manager or Google Secret Manager.

Local recovery needs Neo4j checkpoints and the LangGraph server's ownership
and run records. Hosted policy stores conversation metadata in Neo4j and
denies consumed approval cards. Local interrupt semantics do not provide the
same application-level single-use guarantee.

**Production approach.** Derive ownership from verified identity and recheck
it on reads, message submission, and resumes. Decide whether model calls are
operator-funded or user-funded; align enrollment, budgets, rotation, deletion,
and secret access with that choice. Use managed secrets when suitable.
Application-owned credential storage requires a key-management design, such
as envelope encryption, plus access controls and rotation procedures.

Define recovery time and acceptable data loss for all state stores. Restore
graph facts, pending approvals, and ownership together in an isolated exercise.
Retained VM storage is not proof of a recoverable backup. Test certificate
renewal, secret rotation, and database connectivity with live clients.

**Tradeoff.** Keeping execution state with graph infrastructure simplifies
the storage footprint but couples recovery and availability concerns.
Checkpoint storage alone does not implement conversation authorization.

Sources: [managed-secret practices][secrets],
[envelope encryption][encryption], and [LangGraph persistence][persistence].

## 6. Evals and LangSmith

**Implemented boundary.** Grounded cases check facts in final text and graph
rows. Enrichment checks execution and persisted state, then separately judges
prose. Access, credentials, adapters, and hosted checks cover other boundaries.
LangSmith reporting configuration does not establish a successful run.

**Production approach.** Choose cases from real user tasks and failure modes.
Keep objective side-effect checks separate from model-judged quality. Record
dataset, code, prompts, model/provider settings, judge, latency, and observed
cost when available. Compare configurations using the same inputs and stated
criteria before making superiority claims.

Treat traces as a data export. Set content access and retention deliberately;
masking traces does not sanitize checkpoints or feedback. Local tracing can
contain conversation content. Hosted native exports filter metadata. A
private conversation is still visible to authorized infrastructure operators.

**Tradeoff.** Live evals cost money and can vary with model behavior. A judge
adds semantic coverage and another fallible model. The basic suite provides
focused regression evidence, not universal correctness or a load benchmark.

Sources: [LangSmith evaluations][experiments] and [trace privacy][privacy].

## 7. AWS AgentCore Runtime

**Implemented boundary.** AgentCore and runtime token verification establish
the Cognito identity boundary. Scoped managed secrets and TLS connect the
runtime to private Neo4j on EC2. NAT carries external OpenRouter traffic.
CDK defines resources; filtered OTEL and usage metadata provide telemetry.

**Production approach.** Verify the deployed request path, workload secret
permissions, database reachability, and resume behavior. Runtime session
isolation does not supply application ownership or checkpoints. Set
operational targets and alert ownership for failures, uncertain execution,
database outages, and spending. Include model-provider costs and always-on
database, storage, NAT, and telemetry charges in the workload budget.

Evaluate database replicas, backup restoration, and network redundancy against
availability requirements. The database is single-AZ; retaining its encrypted
EBS volume does not provide a replica or tested disaster recovery.

**Tradeoff.** Managed agent compute reduces serving work while identity,
application state, database operation, and external inference remain distinct
engineering responsibilities. Private VPC attachment does not make
OpenRouter inference private.

Sources: [AgentCore authentication][aws-auth] and
[AWS Generative AI Lens][aws-lens]. The lens includes model-hosting services
that this OpenRouter-backed architecture does not use.

## 8. GCP Agent Runtime and comparison

**Implemented boundary.** A Cloud Run gateway verifies Identity Platform
tokens and invokes an internal, IAM-protected Cloud Run agent with a Google
ID token. Only the gateway workload identity is an agent invoker. Both images
use immutable digests in one Artifact Registry repository. The agent uses
private Neo4j TLS, Private Google Access, and Cloud NAT with source-scoped
Cloud NGFW Standard egress rules. Terraform defines the cloud resources. The
shared hosted contract is JSON request/response; it does not expose common
token streaming.

**Production approach.** Before relying on this boundary, verify gateway-only
invocation, internal ingress, tenant-token checks, secret scope, private
DNS/TLS, and `BEFORE_CLASSIC` firewall evaluation using the
[manual network checklist](DEVELOPMENT.md#gcp-network-smoke-checklist).
Standard FQDN rules permit DNS-derived IPs, including shared addresses; they
do not inspect the HTTPS hostname. Ordinary DNS is not a query allowlist.
The shared private Google API VIP permits Google frontends, with IAM limiting
resource access. A requirement for strict hostname isolation needs a separate
security design and its associated operating cost.

FQDN resolution-failure/cache behavior requires Google clarification or an
isolated controlled-FQDN test before security sign-off. Do not infer fail-closed
behavior from the allow/deny configuration or modify real OpenRouter DNS.
Cloud operations need explicit approval and recorded deploy/test/teardown
evidence. If edge filtering is required, evaluate a load balancer and Cloud
Armor with matching Cloud Run ingress restrictions. Keep application
authentication at the gateway.

Measure both deployments using the same application scenarios. Include the
gateway, agent's always-allocated CPU and minimum instance, Cloud NGFW
Standard, NAT, database, and external inference in cost comparisons. Validate
the agent's 1/2 min/max instances, two CPUs, 4 GiB, and concurrency four against
measured load. Specify recovery independently for each deployment: their
graphs and checkpoints are separate, without cross-cloud replication or
failover.

**Tradeoff.** Shared application semantics reduce policy duplication; cloud
identity, networking, packaging, and lifecycle still differ. Portability is a
contract and verification exercise, not identical infrastructure everywhere.

Sources: [Cloud Run authentication][gcp-auth],
[Cloud Run ingress][gcp-ingress], and [Google AI/ML guidance][gcp-ai].

[transactions]: https://neo4j.com/docs/python-manual/current/transactions/
[limits]: https://neo4j.com/docs/operations-manual/current/performance/
[customization]: https://docs.langchain.com/oss/python/deepagents/customization
[interrupts]: https://docs.langchain.com/oss/python/langgraph/interrupts
[idempotency]: https://docs.langchain.com/oss/python/langgraph/functional-api
[persistence]: https://docs.langchain.com/oss/python/langgraph/persistence
[secrets]: https://cloud.google.com/secret-manager/docs/best-practices
[encryption]: https://cloud.google.com/kms/docs/envelope-encryption
[experiments]: https://docs.langchain.com/langsmith/evaluate-llm-application
[privacy]: https://docs.langchain.com/langsmith/mask-inputs-outputs
[aws-auth]:
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html
[aws-lens]:
https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/
[gcp-auth]:
  https://docs.cloud.google.com/run/docs/authenticating/service-to-service
[gcp-ingress]: https://docs.cloud.google.com/run/docs/securing/ingress
[gcp-ai]:
  https://docs.cloud.google.com/architecture/framework/perspectives/ai-ml
