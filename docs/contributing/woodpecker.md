# Woodpecker pilot

A trial of [Woodpecker CI](https://woodpecker-ci.org) beside GitHub Actions.
Actions stays the CI and the deployer of record: nothing here changes what it
runs. The pipelines under `.woodpecker/` call the `task` targets the Actions
jobs call, and the aggregates `ci:repo` and `ci:workspace` run what a job's
steps run, in their order. One exception: Actions still builds images through
`.github/actions/build-image`, Woodpecker through `task image:build`.
`tests/test_build_image.py` holds the two to the same tags, cache, build args
and secret.

## What runs

| Pipeline | Event | Runs | Actions counterpart |
|---|---|---|---|
| `repo.yaml` | pull request, push | `ci:repo` (`lint:repo`, `be:lint`, `be:test:harness`, `ci:release-notes`, `ci:secrets`); on a pull request `ci:release-note-required` | `ci.yml` job `repo` |
| `backend.yaml` | pull request, push | `be:test:shard` 1/4 to 4/4 against Qdrant | `ci.yml` job `backend` |
| `workspace.yaml` | pull request, push | `ci:workspace` (`workspace:install`, `:cards:check`, `:lint`, `:types`, `:blast-radius`) | `ci.yml` job `workspace` |
| `workspace-test.yaml` | pull request, push | `workspace:test:shard` 1/8 to 8/8 | `ci.yml` job `workspace-test` |
| `images.yaml` | pull request | `image:build` per image, nothing pushed | `ci.yml` job `image` |
| `deploy-dev.yaml` | manual, from `develop` | `infra:deploy:dev:prepare`, then `infra:deploy:dev:apply` | `deploy.yml` job `deploy` (staging) |

A step runs `ci/woodpecker_setup.sh` (the tools the `setup-*` actions install,
at their pins), the other `ci/woodpecker_*.sh` helpers and `task` targets,
nothing else. `tests/test_woodpecker_pipelines.py` holds the pipelines to that
and the aggregates to `ci.yml`'s step lists.

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
  request or push pipeline reads it;
- only the `deploy` step gets it, and that step installs nothing: the
  installers (go-task, bun, both lockfiles, the Pulumi CLI) run in the
  `prepare` step before it, which holds no secret;
- only maintainers on the Woodpecker server, since anyone who can start a
  manual pipeline can start one from a branch whose own `deploy-dev.yaml` is
  changed. Woodpecker cannot tie a secret to a branch.

## Run it

Install guide: <https://woodpecker-ci.org/docs/administration/installation/docker-compose>.
The CLI: <https://github.com/woodpecker-ci/woodpecker/releases>, the
`woodpecker-cli` archive for your platform, checked against `checksums.txt`.

### Lint, no server

```sh
woodpecker-cli lint .woodpecker/
```

### One pipeline, no server

`woodpecker-cli exec` runs a pipeline from this checkout on the local Docker
daemon, without a server, webhook or OAuth app. Run it as a push: `exec`
cannot set a pull request's target branch, which `repo.yaml`'s `base` step
fetches.

```sh
woodpecker-cli exec --local --repo-path . --pipeline-event push .woodpecker/repo.yaml
woodpecker-cli exec --local --repo-path . --pipeline-event push .woodpecker/workspace.yaml
```

It runs the steps as containers on this machine: the backend and workspace
shards want 4 cores and 8 GB, so a laptop is the wrong place for them. Leave
`deploy-dev.yaml` to a server: `exec` takes secrets as flags, which puts them
in the shell history.

### On a host

Woodpecker is a server (UI, webhooks, the forge's OAuth app) and one or more
agents that run the steps as containers on the host's Docker. A test needs:

1. A host with Docker, reachable from GitHub for the webhook. The `backend`
   shards and the image builds want 4 cores and 8 GB at least.
2. A GitHub OAuth app (callback `https://<host>/authorize`) and an admin of the
   repository to activate it: activating installs a webhook. A fork or a mirror
   under your own account works without the repository's admins; the deploy
   still reads the images from `ghcr.io/archos-at`, where Actions publishes.
3. The server and an agent, for example:

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
     woodpeckerci/woodpecker-agent:v3.20.1
   ```

   `-e NAME` without a value passes the variable from the shell, so no secret
   lands in the command line. The trusted-clone list is the tag the pipelines
   pin; the default trusts every tag of `plugin-git`.
4. Repository settings in Woodpecker:
   - **Trusted: volumes**, for `images.yaml`, which mounts the host's Docker
     socket;
   - **Require approval** for pull requests from forks.
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

## What is missing

- **A host.** The repository has no Woodpecker server, and adding one to it
  needs its admins (webhook, OAuth app). Nothing here has run on a Woodpecker
  agent yet; the pipelines pass `woodpecker-cli lint` 3.20.1 and the tests
  above.
- **The coverage gates.** Woodpecker workflows share no files, so the shards'
  coverage cannot be combined as `ci.yml`'s `backend-coverage` and
  `workspace-coverage` jobs do.
- **Change filters.** Every pipeline runs on every event; `ci.yml` skips a tier
  its paths did not change (`.github/filters.yml`).
- **The other `ci.yml` jobs** have no pipeline yet: `semgrep`,
  `backend-suites`, `backend-affected`, `backend-testmon`, `site`, `infra`,
  `packages`, `trivy`, and the row-level-security suite.
- **Image push.** `images.yaml` builds; `image-push` and `publish` stay with
  Actions, and so do the images the deploy pins. `task image:build` has no
  push mode until a pipeline publishes.
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
