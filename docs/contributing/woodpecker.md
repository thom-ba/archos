# Woodpecker pilot

A trial of [Woodpecker CI](https://woodpecker-ci.org) beside GitHub Actions.
Actions stays the CI and the deployer of record: nothing here changes what it
runs. The pipelines under `.woodpecker/` call the same
`task` targets the Actions jobs call, so the two run the same commands.

## What runs

| Pipeline | Event | Runs | Actions counterpart |
|---|---|---|---|
| `repo.yaml` | pull request, push | `lint:repo`, `be:lint`, `be:test:harness`, `ci:release-notes`, `ci:secrets`; on a pull request `ci:release-note-required` | `ci.yml` job `repo` |
| `backend.yaml` | pull request, push | `be:test:shard` 1/4 to 4/4 against Qdrant | `ci.yml` job `backend` |
| `workspace.yaml` | pull request, push | `workspace:cards:check`, `workspace:lint`, `workspace:types`, `workspace:blast-radius` | `ci.yml` job `workspace` |
| `workspace-test.yaml` | pull request, push | `workspace:test:shard` 1/8 to 8/8 | `ci.yml` job `workspace-test` |
| `images.yaml` | pull request | `image:build` per image, nothing pushed | `ci.yml` job `image` |
| `deploy-dev.yaml` | manual, from `develop` | `infra:deploy:dev` | `deploy.yml` job `deploy` (staging) |

Every step runs `ci/woodpecker_setup.sh` (the tools the `setup-*` actions
install, at their pins) and then `task` targets only.
`tests/test_woodpecker_pipelines.py` holds the pipelines to that.

The dev deploy is manual on purpose. `deploy.yml` still deploys the dev stack
for every green `develop` push, and a second deployer on every push would race
it for the stack's update lock. Woodpecker pushes no images, so a manual run
deploys what Actions published: without variables the newest image per service
on `develop`'s first-parent line, or with the pipeline variable
`EXPLICIT_TAG=sha-<40 hex>` that tag for all three services (a rollback).

## Dev only

`task infra:deploy:dev` runs `deploy/pulumi/scripts/deploy-dev.sh`, which fixes
the stack to `matthiasbigl/grid-oib/dev` and names it in every `pulumi`
command. No pipeline names the prod stack.

The token is the weak point. `matthiasbigl` is a personal Pulumi account, and a
personal access token reaches every stack its user can, prod included. Pulumi
scopes a token to stacks only through team tokens, which need an organization
on a paid edition. Until then the pilot limits the token by where it is
readable, not by what it may do:

- a token of its own (`pulumi_dev_access_token`), never the one Actions holds,
  so it can be revoked without touching Actions;
- the Woodpecker secret registered for the `manual` event only, so no pull
  request or push pipeline reads it;
- only maintainers on the Woodpecker server, since anyone who can start a
  manual pipeline can start one from a branch whose own `deploy-dev.yaml` is
  changed. Woodpecker cannot tie a secret to a branch.

## Run it

### Lint, no server

```sh
# https://github.com/woodpecker-ci/woodpecker/releases, the CLI for your platform
woodpecker-cli lint .woodpecker/
```

### On a host

Woodpecker is a server (UI, webhooks, the forge's OAuth app) and one or more
agents that run the steps as containers. A test needs:

1. A host with Docker for the agent, reachable from GitHub for the webhook.
   The `backend` shards and the image builds want 4 cores and 8 GB at least.
2. A GitHub OAuth app for the server and an admin of the repository to add
   it: activating a repository installs a webhook. A fork or a mirror under
   your own account works without the ARCHOS admins.
3. Repository settings in Woodpecker:
   - **Trusted: volumes**, for `images.yaml`, which mounts the host's Docker
     socket;
   - **Require approval** for pull requests from forks;
   - the server's `WOODPECKER_PLUGINS_TRUSTED_CLONE` keeps the default, the
     untagged `woodpeckerci/plugin-git` the pipelines name.
4. The secrets below.

Then a pull request or a push to `develop` runs the CI pipelines, and **Run
pipeline** on `develop` (or `woodpecker-cli pipeline create --branch develop
<repo>`, with `--var EXPLICIT_TAG=sha-<40 hex>` for a rollback) runs the deploy.

### Secrets

| Secret | Pipelines | Events | Content |
|---|---|---|---|
| `github_token` | `repo`, `deploy-dev` | pull request, push, manual | A read-only token with `contents: read` and `packages: read`: the target branch fetch of a private repository, the GHCR tag lookups |
| `pulumi_dev_access_token` | `deploy-dev` | manual | The Pulumi access token for the dev stack, as above |

The Pulumi token's value never goes into a file or a log: the step reads it
from the environment, and `tests/test_deploy_dev.py` checks the script does
not print it.

## What is missing

- **A host.** ARCHOS has no Woodpecker server, and adding one to the
  repository needs its admins (webhook, OAuth app). Nothing here has run on a
  Woodpecker agent yet; the pipelines pass `woodpecker-cli lint` 3.20.1 and the
  tests above.
- **The coverage gates.** Woodpecker workflows share no files, so the shards'
  coverage cannot be combined as `ci.yml`'s `backend-coverage` and
  `workspace-coverage` jobs do.
- **Change filters.** Every pipeline runs on every event; `ci.yml` skips a tier
  its paths did not change (`.github/filters.yml`).
- **The other `ci.yml` jobs** have no pipeline yet: `semgrep`,
  `backend-suites`, `backend-affected`, `backend-testmon`, `site`, `infra`,
  `packages`, `trivy`, and the row-level-security suite.
- **Image push.** `images.yaml` builds; `image-push` and `publish` stay with Actions,
  and so do the images the deploy pins.
- **Deploy steps left out:** the PostHog release (the backend's
  `posthogReleaseId` stays unset after a Woodpecker deploy, so its pods roll
  once and errors lose their release link until the next Actions deploy), the
  PostHog annotation, and the ADR-0147 workspace stack preview.
- **`deploy.yml` on the task.** It still runs its own steps. Moving it onto
  `task infra:deploy:dev` has to wait until the script is on `develop`:
  `workflow_run` runs `develop`'s workflow file against an older checkout
  ([gotchas.md](gotchas.md)). Until then `tests/test_deploy_dev.py` keeps the
  stack file preflight in both identical.
- **Prod** and its approval gate are out of scope for the pilot.
