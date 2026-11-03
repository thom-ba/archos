# Woodpecker pilot

A trial of [Woodpecker CI](https://woodpecker-ci.org) beside GitHub Actions.
Actions stays the CI and the deployer of record: nothing here changes what it
runs. The pipelines under `.woodpecker/` call the `task` targets the Actions
jobs call, and the aggregates (`ci:repo`, `ci:workspace`, `ci:infra`,
`ci:packages`) run what a job's steps run, in their order.

## What runs

| Pipeline | Event | Runs | Actions counterpart |
|---|---|---|---|
| `repo.yaml` | pull request, push | `ci:repo` (`lint:repo`, `be:lint`, `be:test:harness`, `ci:release-notes`, `ci:secrets`); on a pull request `ci:release-note-required` | `ci.yml` job `repo` |
| `backend.yaml` | pull request, push | `be:test:shard` 1/4 to 4/4 against Qdrant | `ci.yml` job `backend` |
| `workspace.yaml` | pull request, push | `ci:workspace` (`workspace:install`, `:cards:check`, `:lint`, `:types`, `:blast-radius`) | `ci.yml` job `workspace` |
| `workspace-test.yaml` | pull request, push | `workspace:test:shard` 1/8 to 8/8 | `ci.yml` job `workspace-test` |
| `images.yaml` | pull request | `image:build` per image, nothing pushed | `ci.yml` job `image` |
| `sast.yaml` | pull request | `ci:sast`'s `ci/semgrep_scan.sh` in the Semgrep image it pins, against the fetched base | `ci.yml` job `semgrep` |
| `trivy.yaml` | pull request | `ci:trivy:pins`, then `ci:trivy`'s `ci/trivy_scan.sh` in the trivy image it pins | `ci.yml` job `trivy` |
| `site.yaml` | pull request, push | `ci:site` | `ci.yml` job `site` |
| `infra.yaml` | pull request, push | `ci:infra` (`infra:install`, `:types`, `:test` with otelcol-contrib) | `ci.yml` job `infra` |
| `packages.yaml` | pull request, push | `ci:packages` (`pkg:install`, `pkg:test:ts`, `pkg:test:py`) | `ci.yml` job `packages` |
| `deploy-dev.yaml` | manual, from `develop` | `infra:deploy:dev:prepare`, then `infra:deploy:dev:apply` | `deploy.yml` job `deploy` (staging) |

A step runs `ci/woodpecker_setup.sh` (the tools the `setup-*` actions install,
at their pins), the other `ci/woodpecker_*.sh` helpers and `task` targets,
nothing else. The two scans are the exception: their images have no task, so
the step runs the one script its target runs, and the script uses the image's
binary because its version is the one the script pins.
`tests/test_woodpecker_pipelines.py` holds the pipelines to that, the scan
images to the scripts' pins, and the aggregates to `ci.yml`'s step lists.

The dev deploy is manual on purpose. `deploy.yml` still deploys the dev stack
for every green `develop` push, and a second deployer on every push would race
it for the stack's update lock. Woodpecker pushes no images, so a manual run
deploys what Actions published to `ghcr.io/archos-at`: without variables the
newest image per service on `develop`'s first-parent line, or with the
pipeline variable `EXPLICIT_TAG=sha-<40 hex>` that tag for all three services
(a rollback). The script derives the image tag itself and refuses an
`IMAGE_TAG` that differs from it.

## Dev only

`task infra:deploy:dev:apply` runs `deploy/pulumi/scripts/deploy-dev.sh`, which
fixes the stack to the project's `dev` stack in the `matthiasbigl` Pulumi
account (the project name from `Pulumi.yaml`), selects it, and names it in
every `pulumi` command that writes. No pipeline names the prod stack.

The token is the weak point. `matthiasbigl` is a personal Pulumi account, and a
personal access token reaches every stack its user can, prod included. Pulumi
scopes a token to stacks only through team tokens, which need an organization
on a paid edition. Until then the pilot limits the token by where it is
readable, not by what it may do:

- a token of its own (`pulumi_dev_access_token`), never the one Actions holds,
  so it can be revoked without touching Actions;
- the Woodpecker secret registered for the `manual` event only, so no pull
  request or push pipeline gets it as a variable;
- only the `deploy` step gets it, and that step installs nothing: the
  installers (go-task, bun, both lockfiles, the Pulumi CLI) run in the
  `prepare` step before it, which holds no secret;
- only maintainers on the Woodpecker server, since anyone who can start a
  manual pipeline can start one from a branch whose own `deploy-dev.yaml` is
  changed. Woodpecker cannot tie a secret to a branch;
- the deploy on an agent of its own (see [Agents](#agents)), never on the host
  that runs `images.yaml` or the server.

What is left is the hosts. `images.yaml` needs the repository's volumes trust,
and the trust belongs to the repository while `labels:` sit in the pipeline
file. So a branch that edits a pipeline can mount the Docker socket on any
agent, the deploy agent included, and is root on that host. As root it reads
the agent secret, can register an agent of the `deploy` pool, and so take a
later deploy and its token, which reaches prod. Only the approval gate stops
that: a maintainer approves every pipeline, on every event, after reading what
it changes under `.woodpecker/`. The pools keep the honest pipelines apart.
Run the server on a host with no agent, since its volume holds every secret
and the users' GitHub tokens. Building without the host socket (a rootless or
remote BuildKit) would drop the volumes trust, and this risk with it; the
pilot does not.

## Agents

A workflow runs only on an agent whose `WOODPECKER_AGENT_LABELS` match its
`labels:`. Each pipeline names one pool:

| Pool | Pipelines | Agent host |
|---|---|---|
| `pool=ci` | `repo`, `backend`, `workspace`, `workspace-test`, `sast`, `trivy`, `site`, `infra`, `packages` | any but the server's; no step mounts the socket |
| `pool=build` | `images` | its own: its steps are root on it |
| `pool=deploy` | `deploy-dev` | its own, apart from the build host and the server |

A pipeline with no agent of its pool waits in the queue. For a quick test on
one host, `WOODPECKER_AGENT_LABELS=pool=*` takes all three, with the risk
above.

## Run it

Install guide: <https://woodpecker-ci.org/docs/administration/installation/docker-compose>.
The CLI: <https://github.com/woodpecker-ci/woodpecker/releases>, the
`woodpecker-cli` archive for your platform, checked against `checksums.txt`.

### Lint, no server

```sh
woodpecker-cli lint .woodpecker/
```

### One pipeline, no server

`woodpecker-cli exec` runs a pipeline on a Docker daemon without a server,
webhook or OAuth app. Three things differ from a server run:

- `--local`, the default, mounts this checkout into the steps, and git in the
  step refuses it as owned by another user ("dubious ownership"). Clone from a
  bundle instead, with `--local=false`.
- The `when:` filters match `--commit-branch`: `develop` for a push, and for a
  pull request its target. `--commit-refspec <branch>:develop` gives a pull
  request the target branch that `repo.yaml`'s `base` step fetches.
- The repository trusts are flags: `images.yaml` mounts the host's Docker
  socket and needs `--repo-trusted-volumes`.

```sh
branch=$(git branch --show-current)
mkdir -p ../wp-bundle && git bundle create ../wp-bundle/ci.bundle develop "$branch"
exec_flags="--local=false --repo-clone-url /bundle/ci.bundle
  --backend-docker-volumes $(cd ../wp-bundle && pwd):/bundle:ro
  --commit-sha $(git rev-parse HEAD) --commit-ref refs/heads/$branch --commit-branch develop"
woodpecker-cli exec $exec_flags --pipeline-event push .woodpecker/workspace.yaml
woodpecker-cli exec $exec_flags --pipeline-event pull_request \
  --commit-refspec "$branch:develop" .woodpecker/repo.yaml
```

`exec` runs a matrix's axes one after another, not side by side. On a shared
host, cap each step with `--backend-docker-limit-mem` and
`--backend-docker-limit-cpu-quota`. The steps run as containers on this
machine: the backend and workspace shards want 4 cores and 8 GB, so a laptop
is the wrong place for them. Leave a real `deploy-dev.yaml` to a server, where
its secrets are tied to the `manual` event; `exec` reads them from
`--secrets-file`, which then holds the token.

### On a host

Woodpecker is a server (UI, webhooks, the forge's OAuth app) and one or more
agents that run the steps as containers on the host's Docker. A test needs:

1. A host with Docker, reachable from GitHub for the webhook. The `backend`
   shards and the image builds want 4 cores and 8 GB at least.
2. A GitHub OAuth app (callback `https://<host>/authorize`) and an admin of the
   repository to activate it: activating installs a webhook. A fork or a mirror
   under your own account works without the repository's admins; the deploy
   still reads the images from `ghcr.io/archos-at`, where Actions publishes.
3. The server and the agents, each pool on its host as in [Agents](#agents).
   For example the server, and a `ci` agent on its own host:

   ```sh
   # WOODPECKER_AGENT_SECRET: openssl rand -hex 32, the same for both
   docker run -d --name woodpecker-server -p 8000:8000 -p 9000:9000 \
     -v woodpecker-server-data:/var/lib/woodpecker \
     -e WOODPECKER_HOST=https://<host> \
     -e WOODPECKER_OPEN=false -e WOODPECKER_ADMIN=<your GitHub login> \
     -e WOODPECKER_GITHUB=true \
     -e WOODPECKER_GITHUB_CLIENT -e WOODPECKER_GITHUB_SECRET \
     -e WOODPECKER_AGENT_SECRET \
     -e WOODPECKER_PLUGINS_TRUSTED_CLONE=docker.io/woodpeckerci/plugin-git:2.11.0 \
     woodpeckerci/woodpecker-server:v3.20.1
   docker run -d --name woodpecker-agent \
     -v /var/run/docker.sock:/var/run/docker.sock \
     -e WOODPECKER_SERVER=<server host>:9000 \
     -e WOODPECKER_AGENT_SECRET \
     -e WOODPECKER_AGENT_LABELS=pool=ci \
     woodpeckerci/woodpecker-agent:v3.20.1
   ```

   The `build` and `deploy` agents are the same command with their pool, each
   on its own host. `-e NAME` without a value passes the variable from the
   shell, so no secret lands in the command line. The trusted-clone list is the
   tag the pipelines pin; the default trusts every tag of `plugin-git`.
4. Repository settings in Woodpecker:
   - **Trusted: volumes**, for `images.yaml`, which mounts the host's Docker
     socket;
   - **Require approval** for all events, not only fork pull requests: a
     same-repository branch can edit a pipeline too.
5. The secrets below.

Then a pull request or a push to `develop` runs the CI pipelines, and **Run
pipeline** on `develop` (or `woodpecker-cli pipeline create --branch develop
<repo>`, with `--var EXPLICIT_TAG=sha-<40 hex>` for a rollback) runs the deploy.

### Secrets

Each secret is set on one step only; no step that runs the repository's hooks,
tests or installers sees one.

| Secret | Step | Events | Commands that see it | Content |
|---|---|---|---|---|
| `pulumi_dev_access_token` | `deploy-dev` / `deploy` | manual | `task infra:deploy:dev:apply` | The Pulumi access token for the dev stack, as above |
| `ghcr_read_token` | `deploy-dev` / `deploy` | manual | `task infra:deploy:dev:apply` | A classic personal access token with `read:packages` only, for the GHCR tag lookups. Fine-grained tokens cannot read GHCR |

`repo.yaml` needs no secret while the repository is public: its `base` step
fetches the pull request's target branch anonymously. A private repository
needs a read-only fetch token there, and on that step alone: a fine-grained
token with `contents: read`, as `GH_TOKEN`, a second secret beside the classic
one (a classic token reads private contents only with `repo`, which also
writes). The clone itself uses the server's own forge credentials.

The Pulumi token's value never goes into a file or a log: the step reads it
from the environment, and `tests/test_deploy_dev.py` checks the script does
not print it.

## Tested

On 2026-10-31 the pipelines ran with `woodpecker-cli exec` 3.20.1 as above, on
a borrowed shared Linux host (32 threads, Docker 29), each step capped at
16 GiB and 8 CPUs. Peak is the largest step's memory.

| Pipeline | Event | Result | Time | Peak |
|---|---|---|---|---|
| `repo.yaml` | pull request | passed | 125 s | 1.1 GiB |
| `backend.yaml` | push | 16510 passed, 1 failed: an OCR test's 30 s deadline under the host's load; its file passed three times alone | 992 s | 3.8 GiB |
| `workspace.yaml` | push | passed | 49 s | 2.5 GiB |
| `workspace-test.yaml` | push | all 8 shards passed: 1 to 3 and 6 to 8 in one run, 4 and 5 again after the `HOSTNAME` fix below | 652 s for 8 | 2.9 GiB |
| `images.yaml` | pull request | backend, frontend and web built without layer cache, tagged under `REGISTRY=archos-wp` on a buildx builder of their own, nothing pushed | 454 s for 3 | in BuildKit, not measured |
| `deploy-dev.yaml` | manual | passed with the Pulumi CLI, the ESC check and the registry scripts stubbed and dummy secrets: every `pulumi` call named the dev stack, preview ran before up, and no secret value reached the log | 53 s | 0.3 GiB |
| `sast.yaml` | pull request | passed, no finding, after the Semgrep fix below | 19 s | 0.1 GiB |
| `trivy.yaml` | pull request | passed; the branch moves no pin, so it scanned none. Its `scan` step alone on one real pin (`alpine:3.22.6` by digest) found no vulnerability | 24 s; 98 s for the pin | 0.1 GiB |
| `site.yaml` | push | passed, with the accessibility check's 80 views | 105 s | 0.6 GiB |
| `infra.yaml` | push | 711 tests passed, after the `jq` fix below | 49 s | 1.5 GiB |
| `packages.yaml` | push | 195 TypeScript and 636 Python tests passed | 86 s | 1.6 GiB |

The runs found these pipeline bugs, fixed on this branch: Qdrant's service
started outside its image's workdir, pytest-xdist started a worker per host
core (32) and ran out of memory, and the gateway tests could not reach their
server because Docker sets `HOSTNAME`. The later five found three more: Semgrep
blocked the `buildx` calls in `ci/build_image.py` and `ci/publish_images.py`
(an argument list, no shell; `ci.yml`'s scan would have blocked them too, so
they carry a `nosemgrep`), `node:22-bookworm` has no `jq` for the infra tests'
`resolve-image-refs.sh`, and `packages.yaml` started 32 pytest workers
(5.8 GiB, 106 s) until it got `backend.yaml`'s cap of four.

## What is missing

- **The Hatchet job.** `ci.yml`'s `hatchet` job has no pipeline. It starts its
  containers through Docker (`ci/hatchet_engine.sh`), so it needs the `build` pool;
  its `ci:hatchet:*` targets are ready for one.
- **A server.** The repository has no Woodpecker server, and adding one to it
  needs its admins (webhook, OAuth app). The runs above used `exec`, so no
  webhook, status check, secret store or pull request approval was exercised.
- **A real dev deploy.** `deploy-dev.yaml` ran only with Pulumi and the
  registry stubbed; no real `pulumi preview` or `up`, ESC check or GHCR lookup
  has run from Woodpecker. On the first real run, check that the GHCR lookup
  logs in (`GITHUB_ACTOR` defaults to the repository owner, not the classic
  token's user; set it on the step if GHCR refuses), and expect the backend
  pods to roll once (the PostHog release below).
- **The coverage gates.** Woodpecker workflows share no files, so the matrix
  shards' coverage cannot be combined as `ci.yml`'s `backend-coverage` and
  `workspace-coverage` jobs do. The gate needs the shards as parallel steps of
  one workflow, which share its workspace, and a coverage step after them.
- **The Plan job.** Every pipeline runs on every event: `ci.yml` skips a tier
  its paths did not change (`task ci:changes`), reuses a green pull request's
  result for the same tree, and builds only the images a change moves.
  Woodpecker has none of the three yet.
- **The other `ci.yml` jobs** have no pipeline yet: `backend-suites` (Postgres
  and the API suites), `backend-affected` and `backend-testmon` (the pull
  request's test selection and the database it reads), and the
  row-level-security suite. Their targets exist (`be:test:api`,
  `ci:backend:affected`, `be:test:testmon`, `db:test:rls`).
- **The scans on a push.** `trivy.yaml` and `sast.yaml` run on pull requests
  only, as `ci.yml`'s `semgrep` does. `ci.yml`'s `trivy` also scans what a push
  adds since the last green commit, and keeps the layer analysis in its cache.
  The Semgrep findings summary is Actions' step summary.
- **Image push and publish.** `task image:push` and `task image:publish` exist,
  and Actions' `image-push` and `publish` jobs run them, but no Woodpecker
  pipeline does: `images.yaml` only builds, and the images the deploy pins come
  from Actions. Both need a registry login and a push event.
- **`CI OK`.** `task ci:ok` reads Actions' `needs`; a Woodpecker server gates
  on each workflow's status instead, and records no tested tree for reuse.
- **`images.yaml` as committed.** The run above set its own `REGISTRY` and
  buildx builder, and predates the shared build with labels and push mode, so
  the default path (the host daemon's builder, the GHCR `buildcache` read) has
  not run from Woodpecker.
- **Deploy steps left out:** the PostHog release (the backend's
  `posthogReleaseId` stays unset after a Woodpecker deploy, so its pods roll
  once and errors lose their release link until the next Actions deploy), the
  PostHog annotation, and the ADR-0147 workspace stack preview.
- **`deploy.yml` on the task.** It still runs its own steps. Moving it onto
  `task infra:deploy:dev` has to wait until the script is on `develop`:
  `workflow_run` runs `develop`'s workflow file against an older checkout
  ([gotchas.md](gotchas.md)). Until then `tests/test_deploy_dev.py` keeps the
  stack file preflight in both identical. This is the first follow-up once the
  script is on `develop`.
- **Prod** and its approval gate are out of scope for the pilot.
