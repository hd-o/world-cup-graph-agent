# Evidence

This document separates implemented verification from observed run results.
[Architecture](ARCHITECTURE.md#evaluations) describes the checks;
[Development](DEVELOPMENT.md#evaluations) contains execution commands.
[Teaching](TEACHING.md) identifies the demonstrations for each chapter.

## Published result status

No public LangSmith trace or experiment links are recorded here. The private
observations below identify their implementation and scope; they are not a
public benchmark. Scenario definitions, deployment code, and build artifacts
alone do not establish a live result.

The capture requirements and checklists below describe the evidence contract.

## Private GCP verification

Observed on **2026-10-22–2026-10-23 UTC**, using the working tree based on
commit `036e97d9ebf146a4d51e3cf1c3992c5f9c324dbe`. Runtime, infrastructure,
dependency, evaluation, and documentation changes are uncommitted. Core
prompts, schema, and seed files match that base revision. Protected workspace
artifacts under `deploy/artifacts/` are not public trace URLs.
Repository origin is `git@github.com:hd-o/worldcup-agent.git`;
public source and trace URLs are not established by this capture.

### Configuration

- Project `worldcup-agent`, region `us-central1`, isolated deployment
  `wc-run-1022`; application environment `demo`, runtime `gcp`.
- Final deployment artifact timestamp: `20261022T232950066537Z`.
- Python 3.14.4; Terraform 1.16.5; Google provider 8.6.0. Dependency lock hash:
  `c0b8854ceb84c7bea64dd0732fa8785a8ac88bfb42f3833b1445584a852b121b`.
- Agent: 1–2 instances, 2 vCPU, 4 GiB, concurrency 4, always-allocated CPU.
  The gateway and agent use separate IPv4 Direct VPC subnets and the
  [documented network policy](ARCHITECTURE.md#gcp-network).
- Image repository:
  `us-central1-docker.pkg.dev/worldcup-agent/wc-run-1022`.
  Agent digest:
  `sha256:69085b8b38256de016e5dfe3586ffc802af3aac5b564ddbe089fa10df2388794`.
  Gateway digest:
  `sha256:353df6f7dd4c40781879a3f0eb694c21ec9a378a165142ce7abe410e0c4f1876`.
- Supervisor/enricher model: `anthropic/claude-haiku-5.5`, high reasoning;
  provider choices `claude-on-aws`, `azure/global`, `anthropic`.
  Research configuration: `openai/gpt-oss-120b`, low reasoning; provider
  choices `cerebras/fp16`, `nebius/fp4`, `sambanova`.
  Provider sort is `throughput`. These checks use no model judge and reject
  research approval; they do not execute web research or graph enrichment.

### Observed checks

| Check | Observed outcome |
| --- | --- |
| Lint, typecheck, Terraform validation/format | Pass |
| AWS/GCP deployment command help, image contexts | Pass |
| Local fast selection | 1 passed, 12.18 s |
| Local adapter selection | 3 passed, 25.82 s |
| Final GCP hosted conversation scenario | 1 passed, 55.93 s |
| Keyless user | Credential denial, no fallback, empty history |
| Native telemetry | Spans observed for gateway and agent during the cycle |
| Agent internet egress | OpenRouter TLS 443 allowed; port 80 denied |
| Unrelated destination | `1.1.1.1:443` denied from agent networking |
| External positive control | Verified TLS to `1.1.1.1` succeeds |
| Private database | Private DNS and TCP 7687 connectivity succeed |
| Internal invocation | Gateway succeeds; missing/wrong identity denied |
| External invocation | Valid gateway identity receives 404 outside VPC path |
| Diagnostic resource cleanup | Network and DNS probe resources deleted |
| Deployment teardown | Services/data deleted; network release pending |

The hosted command selects
`evals/hosted/scenarios.py::test_hosted_conversation_access[gcp]` with
`--hosted --hosted-cloud gcp`, `GCP_DEPLOYMENT_TIMESTAMP` set to the final
timestamp, and LangSmith test tracking disabled. It observes a `BRA` response,
cross-user denial, persisted history/approvals, research rejection, replay
denial, unauthenticated gateway denial, and a full-length Unicode message
reaching ownership validation across the private HTTP hop.

Initial attempts include a local bootstrap deadlock, a publication command
time limit, a provisioning failure, a hosted authentication denial, and a
cold diagnostic job's OpenRouter connection failure. Repeated selected checks
succeed; no authentication or firewall allowance is relaxed. Diagnostic
readiness uses bounded retries. These outcomes are not a startup-reliability
measurement. Durations cover selected pytest runs, not the deployment cycle;
billed cost is unmeasured.

### Cleanup inventory

The test services, tenant/users, VM/disks, NAT, firewall policy, application
IAM bindings, and database/model-key secrets are deleted. Only the test VPC
and agent/gateway subnets remain in Terraform state. Subnet deletion is
blocked by provider-managed `SERVERLESS` address reservations. GCP documents
a [1–2 hour release delay][direct-vpc]; those reservations are not manually
deleted. Network cleanup is incomplete until native verification succeeds.

All 18 test image versions and both model-key secrets are deleted; artifact
cleanup reports no pending deletions or remaining versions/key copies.
Backend/bootstrap buckets and object versions, the empty artifact repository,
project initialization, and independent trace/audit retention are retained
boundaries. External OpenRouter accounts and keys are outside deployment
teardown. No existing `worldcup-demo` deployment is changed by this isolated
cycle.

[direct-vpc]: https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc

### Controlled DNS observations

The experiment uses a separate controlled private FQDN in the isolated VPC,
an FQDN-only TCP 443 rule at priority 400, and disjoint public destinations.
Each stage verifies OpenRouter TLS from the same job before assessing denial.
The ordinary application rules remain unchanged, and real `openrouter.ai`
DNS is not modified.

- Initial `NXDOMAIN`: the controlled address and `1.1.1.1` are denied.
- Positive A record, TTL 120 s: verified TLS to the controlled address
  succeeds; `1.1.1.1` remains denied.
- Record withdrawal: after a 300 s controller wait plus job startup, DNS
  returns `NXDOMAIN`, but the cached controlled address remains reachable.
  The unrelated `1.1.1.1` destination remains denied.
- All three stages pass their bounded assertions; temporary rule, DNS zone,
  and jobs are deleted. This establishes no broader access to the unrelated
  tested destination, not a universal failure-closed guarantee.

`SERVFAIL`, resolver outages, the maximum cached-address retention interval,
and exhaustive destinations are untested. Shared-IP access and ordinary DNS
queries remain permitted by design. There is no load, availability, recovery,
or production-readiness claim, and no public trace link is available.

## Implemented verification scope

- **Grounded answers:** four cases cover the France FIFA code, Brazil's group,
  France's forwards, and a winner derived from seeded records. Expected facts
  must occur in the answer and successful graph rows, with no research or
  mutation execution. The fast selection covers only the FIFA-code case.
- **Enrichment:** research rejection and approved enrichment check execution
  and persisted graph state. The approved path checks the supervisor's graph
  read after mutation. A separate model judge assesses final prose.
- **Access:** requests against the local server exercise authentication,
  ownership, state/history privacy, and cross-user message/resume denial.
- **Credentials:** distinct fake keys verify model wiring without inference.
  This is not a managed-secret storage or encryption test.
- **Adapters:** local integration checks cover worker execution and persisted
  approval handling, including ownership and replay denial.
- **Hosted:** explicitly authorized AWS/GCP requests exercise deployed
  authentication, credential denial, grounded answers, ownership, and
  persisted approval rejection/replay denial. These scenarios do not approve
  graph mutation.

## Run summary requirements

For each published run, record:

1. Repository URL, commit SHA, UTC date, and any uncommitted changes.
2. Exact command, selected cases, passed/failed/skipped counts, and failure
   details. Preserve skipped and blocked checks as such.
3. Python/dependency versions, dataset revision, schema/prompt revisions,
   model IDs, provider selection, inference settings, and judge configuration.
   Credentials are not report fields.
4. Local versus adapter versus hosted execution. For hosted runs, include
   cloud, region, and the successful deployment artifact timestamp.
5. Observed answer and tool outcomes, graph before/after state where relevant,
   and the approval decisions actually exercised.
6. Measured duration and billed cost when available, including the scope of
   those measurements. Unavailable metrics remain explicitly unmeasured.
7. Working public trace or experiment links and a readable result summary.
   Check links without an authenticated author session. Select content
   suitable for public sharing before exposing traces or feedback.
8. Limitations: small dataset coverage, model variability, judge dependence,
   and any properties the selected checks do not establish.

## Chapter evidence checklist

- Graph query: seeded relationship question, actual Cypher, returned rows,
  and observed reader permission/result-boundary behavior.
- Agent: final answer tied to successful query evidence and configured tools.
- Enrichment: rejected and approved paths, separate approvals, applied changes,
  and the supervisor's graph re-read. Use isolated, controlled starting data.
- State/identity: owner continuation and cross-user denial; an actual restart
  observation for any restart-recovery claim.
- Evals: pytest outcomes, objective checks, and distinct judge feedback.
- AWS and GCP: public invocation evidence per deployment, with identity and
  approval checks; local adapter evidence identified separately.

## Interpretation

Fact-presence checks do not establish complete relational correctness, source
truth, or exhaustive answer coverage. Judge feedback is model-dependent.
Human approval authorizes an effect; it does not independently verify a fact.

A successful command-help check shows that a CLI loads. A build shows artifact
construction. Hosted invocation evidence establishes the behavior observed on
that deployment at that time. Passing a basic suite does not establish load
capacity, availability, recovery under every failure, or production readiness.

The published repository follows its main branch. Each evidence summary's
revision identifies the code actually measured; it does not imply that every
main-branch revision has the same measured results.
