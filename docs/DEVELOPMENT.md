# Development

This guide covers local development and commands for hosted environments.
Use the [README chapters](../README.md#chapters) for the learning path and
[Teaching](TEACHING.md) for cumulative reconstruction exercises.

**Neo4j Enterprise license required.** You must provide your own valid
Neo4j Enterprise license to run this project locally or in the cloud.
The startup scripts set `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`. This repository's
MIT license covers its code; it does not grant a Neo4j Enterprise license.

## Local chat with Agent Chat

Use Python 3.14 or newer, uv, and a running Docker daemon. Install the locked
environment from the repository root:

```bash
uv sync --locked
source .venv/bin/activate
poe setup
```

Configure `.env` from `.env.example`, including your OpenRouter key, then:

```bash
source .venv/bin/activate
poe dev
poe bootstrap
docker compose restart langgraph
```

Wait for Neo4j to become healthy before bootstrap. Bootstrap provisions the
database users needed for LangGraph startup. The server is ready when
`http://localhost:2024/ok` returns `{"ok":true}`.

Open <https://agentchat.vercel.app> and enter:

| Field | Value |
| --- | --- |
| Deployment URL | `http://localhost:2024` |
| Assistant / Graph ID | `agent` |
| LangSmith API Key | `demo-alice-token` or `demo-bob-token` |
| Built with Agent Builder | Off |

The API-key field carries a public demo identity token, not your LangSmith
or OpenRouter key. The browser sends requests directly to the local server.
Allow local-network access if the browser asks. For a remote devcontainer,
forward port 2024 to your browser's machine.

Try “What is Brazil's FIFA code?” Text chat, conversation history, and
research/change approval decisions are supported. Uploads, message editing,
regeneration, and cancellation are outside the API's scope. To switch demo
users, open the site's root URL and enter the other token.

## Evaluations

Configure the environment and eval credentials, then run lint, type checking,
and fast evals:

```bash
source .venv/bin/activate
poe lint && poe typecheck && poe eval-fast
# Run every eval check.
poe eval
# Select cases through pytest names, IDs, or markers.
poe eval -k grounded_fifa_code
poe eval -m enrichment
# Disable eval-process uploads; server tracing is configured independently.
LANGSMITH_TEST_TRACKING=false LANGSMITH_TRACING=false poe eval
```

Pytest runs the evals as async functions. Grounded cases use parameterization;
enrichment functions show messages, approval decisions, and outcome checks.
Start reading `evals/grounded/scenarios.py` and `evals/enrichment/scenarios.py`;
their supporting modules separate approval handling from evaluation. Shared
modules handle database isolation, conversation execution, execution evidence,
and reporting.

`poe eval-fast` selects the France FIFA-code scenario. It does not exercise
enrichment, ownership, adapter recovery, or deployed cloud behavior. Record
the selected scope and actual outcomes using [Evidence](EVIDENCE.md).

Every eval selection starts the full development stack, including the
dedicated Neo4j eval service, once per pytest session through the shared
`development_server` fixture in `evals/conftest.py`. It runs
`uv run poe dev --wait --wait-timeout 120`. Compose waits for stack health;
startup has a 600-second command timeout. The development stack stays running
afterward. Environment configuration and `poe bootstrap` are prerequisites.

Access checks target the local API at `http://localhost:2024` and create
conversation state on that server. Direct graph scenarios reset only the
isolated Neo4j eval database before each case. `EvalSettings.neo4j_uri` fixes
the eval endpoint at `bolt://localhost:7688`; environment and constructor
values cannot redirect it. `NEO4J_EVAL_BOOTSTRAP_PASSWORD` supplies the
isolated service's admin credential; the example environment and Compose
defaults use the same demo value.

## Hosted environments

Use the AWS and GCP command groups to build, deploy, inspect, enroll, and use
hosted environments. Set `DEPLOYMENT_NAME` and keep cloud account/project and
region defaults in `deploy/.env`, then restrict the file before running cloud
commands:

```bash
source .venv/bin/activate
uv sync --locked
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
poe aws --help
poe gcp --help
```

Cloud writes, paid hosted calls, deployment, and teardown require explicit
approval for the target, scope, and data-loss consequences. Implementation or
local verification does not authorize cloud operations. Use an isolated
deployment for network/security checks and approve its deploy, test, and
teardown lifecycle before starting.

### Before deploying

For a new GCP environment, choose an unused `DEPLOYMENT_NAME`. Its state,
users, secrets, and resources belong to that deployment namespace.

Complete these steps before `poe aws deploy` or `poe gcp deploy`. Building
needs none of the cloud steps.

1. Put these commands on `PATH`:
   - AWS: `aws`, `cdk`, `uv`, `readelf`, `docker`
   - GCP: `gcloud`, `terraform`, `uv`, `docker`
2. Start the Docker daemon.
3. Fill in `deploy/.env`:
   - AWS: `DEPLOYMENT_NAME`, `AWS_PROFILE`, `AWS_ACCOUNT_ID`, `AWS_REGION`
   - GCP: `DEPLOYMENT_NAME`, `GOOGLE_CLOUD_PROJECT`, `GCP_REGION`
   - Both: `DEMO_ALICE_OPENROUTER_API_KEY` and
     `DEMO_BOB_OPENROUTER_API_KEY`; non-preview deployments require them for
     the default `alice` and `bob` users.
4. Log in to the cloud CLI for the account or project in `deploy/.env`:

   ```bash
   # AWS: the profile needs valid credentials for AWS_ACCOUNT_ID (SSO shown)
   aws login --profile "$AWS_PROFILE"

   # GCP: CLI and application default credentials for GOOGLE_CLOUD_PROJECT
   gcloud auth login
   gcloud auth application-default login
   ```

Deployment verifies that the authenticated AWS account or GCP project matches
the configured target and stops on a mismatch. It also creates retained
bootstrap resources after bootstrap approval, so running `poe aws bootstrap`
or `poe gcp bootstrap` first is optional. To build and review without cloud
changes, add `--preview` to either deploy command.

Run a guided deployment for `DEPLOYMENT_NAME`. A deployment without
`--timestamp` builds its own artifacts. Each command prints its UTC
microsecond timestamp and writes generated files beneath
`deploy/artifacts/<command>/<runtime>-<timestamp>/`:

```bash
poe aws deploy
poe gcp deploy
```

`build` is an explicit prerequisite that requires no cloud authentication.
Pass its printed timestamp to commands that consume that exact build.
Standalone consumers create a fresh timestamp for their own output and record
the source timestamp in `metadata.json`. `deploy --timestamp T` instead writes
`deploy/<runtime>-T`, sharing the selected build timestamp:

```bash
poe aws build
poe aws inspect --timestamp "$BUILD_TIMESTAMP"
poe aws deploy --timestamp "$BUILD_TIMESTAMP"

poe gcp build
poe gcp inspect --timestamp "$BUILD_TIMESTAMP"
poe gcp publish --timestamp "$BUILD_TIMESTAMP"
poe gcp deploy --timestamp "$BUILD_TIMESTAMP"
```

The GCP build produces separate agent and gateway `tar.gz` container build
contexts containing Dockerfiles and locked requirements. Publishing uses
Docker to build both images, pushes them to the same Artifact Registry
repository, and records their immutable digests; deployment uses those
digests for the two Cloud Run services. Inspect both build contexts, published
image digests, and the Terraform plan. The public gateway retains Identity
Platform authentication; the agent has internal ingress and grants invocation
only to the gateway workload identity.

`inspect`, `publish`, and `stage` never select the newest build or build as a
side effect. They require the exact successful build timestamp. `converse`
similarly requires the timestamp of a successful, non-preview deployment:

```bash
poe aws status
poe gcp status
poe aws enroll --keyless
poe gcp enroll --keyless
poe aws converse --timestamp "$DEPLOYMENT_TIMESTAMP"
poe gcp converse --timestamp "$DEPLOYMENT_TIMESTAMP"
```

Commands that inspect or change cloud resources read the deployment name from
`DEPLOYMENT_NAME` in `deploy/.env` or the process environment. Account or
project and region can also come from environment variables or root options.
Invocation folders are immutable: an existing destination causes failure, and
successful and failed folders remain available for inspection. Generated login
passwords remain in the protected shared `deploy/private/` store so enrollment
reuses them.

Hosted smoke checks make public cloud calls only when explicitly authorized:

```bash
LANGSMITH_TEST_TRACKING=false LANGSMITH_TRACING=false \
  AWS_DEPLOYMENT_TIMESTAMP="$AWS_DEPLOYMENT_TIMESTAMP" \
  GCP_DEPLOYMENT_TIMESTAMP="$GCP_DEPLOYMENT_TIMESTAMP" \
  poe eval -m hosted --hosted
```

Use `--hosted-cloud aws` or `--hosted-cloud gcp` to check one provider.

### GCP network smoke checklist

These are manual acceptance checks for an explicitly approved isolated
deployment, not observed results. Record the revision, image digests, project,
region, policy/rule priorities, DNS records, timestamps, request outcomes,
logs, and teardown outcome using [Evidence](EVIDENCE.md). Run the normal
GCP-selected hosted eval through the public gateway as one part of this
checklist; it does not establish network enforcement by itself.

1. Inspect both Cloud Run configurations. Confirm separate Direct VPC IPv4
   subnets, gateway `private-ranges-only`, agent `all-traffic`, Private Google
   Access, private `run.app` and `googleapis.com` DNS to `199.36.153.8/30`,
   Cloud NAT, and immutable image digests. Confirm agent min/max instances
   1/2, two CPUs, 4 GiB, concurrency four, and always-allocated CPU.
2. Inspect the global Cloud NGFW Standard policy association and
   `BEFORE_CLASSIC` evaluation. Every rule must select the agent source subnet:
   database TCP 7687, Google private VIP TCP 443, exact `openrouter.ai` TCP 443,
   then explicit deny of all other egress. Confirm gateway and database host
   traffic are outside that source scope.
3. Exercise the public gateway with the normal hosted eval. Verify private
   Neo4j CA-validated TLS, identity-scoped Secret Manager reads, and filtered
   telemetry delivery through the real application path.
4. From a temporary diagnostic workload matching the agent's subnet and
   egress configuration, establish a TLS connection to `openrouter.ai:443`
   without credentials. A completed verified TLS handshake is the positive
   network check; do not send API keys or require model inference.
5. From the same source, attempt unrelated public destinations on TCP 443
   whose resolved IPv4 addresses are disjoint from all allowed destinations,
   and attempt `openrouter.ai:80`. Record denied outcomes and policy evidence.
   A failed application request alone does not establish firewall denial.
6. Confirm direct external agent invocation is denied, including with an
   otherwise valid gateway identity token. From the internal path, verify
   denial with no Google ID token and with a token for a different identity;
   confirm the gateway identity succeeds with the correct audience.
7. Test FQDN DNS-failure behavior only with a controlled test FQDN and isolated
   policy/workload. Establish a successful resolution and connection, induce
   resolution failure, and record fresh and cached-address behavior across
   observed cache/TTL transitions. Never alter real `openrouter.ai` DNS.
   Obtain Google clarification or this isolated evidence before security
   sign-off; failure semantics are not established by the configured rule.
8. Tear down approved diagnostic workloads, controlled DNS/policy resources,
   and the isolated deployment. Inspect retained/shared resources and record
   any remaining costs or pending cleanup.

Use temporary diagnostics only; do not add permanent debug endpoints or
automatic probe infrastructure. FQDN rules allow DNS-derived IPs, including
shared IPs, rather than enforcing the HTTPS hostname. Ordinary DNS queries
are not an application-domain allowlist. The private Google API VIP permits
Google frontend access; resource access remains IAM-controlled. These checks
must report those limitations rather than claiming strict host isolation.

### Teardown

Cloud Run Direct VPC reservations can block subnet deletion for
[1–2 hours][direct-vpc] after services or jobs disconnect. Recheck the native
inventory and retry the guarded destroy command after GCP releases them;
do not manually delete provider-managed `SERVERLESS` addresses. Artifact
cleanup orders image indexes before their referenced manifests.

[direct-vpc]: https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc

Preview teardown before starting an interactive destructive operation:

```bash
poe aws destroy --preview
poe gcp destroy --preview
```

Run `poe aws destroy --help` or `poe gcp destroy --help` for the required
confirmations. Read [Architecture](ARCHITECTURE.md) for the hosted contracts,
identity boundary, persistence, deployment design, observability, evaluation
architecture, and teardown safeguards. [Features](FEATURES.md) covers behavior
and scope.
