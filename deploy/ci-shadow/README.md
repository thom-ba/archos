# ci-shadow: try CI on the cluster beside the real one

Runs `ci.yml` on runner pods in the dev (staging) cluster, for the private test copy
`thom-ba/archos-ci`. Nothing on `archos-at/archos` changes: its CI, its runners and its
variables stay as they are, and every result lands on the test copy.

## Run it

```bash
kubectl -n ci-shadow get configmap ci-shadow-scripts -o jsonpath='{.data.run\.sh}' | bash -s light
```

Only `kubectl` and cluster access are needed. `light` is the light job set, `full` the
full one. The log gives a progress line a minute, then a line per job and
`RESULT: success` or `failure`. A dropped connection only stops the log, not the run.

Idle, it runs no pods and uses no CPU or memory. `./ci-shadow off` removes everything.

## How it protects staging

- **Priority -100, and it never preempts.** A staging pod that needs room evicts a runner
  within seconds; a runner never evicts anything. Evicted slots come back and rejoin the
  run, whose interrupted jobs go red after GitHub's 10-minute timeout.
- **Small requests, capped.** 3 pods at most, 0.25 CPU and 4 GiB requested each. The
  autoscaler is at its maximum of 4 nodes, so a run cannot add nodes.
- **Off staging's node disks.** Each slot gets its own 15 GiB `single-replica` volume for
  the job folder, tools, caches and `/tmp`, deleted 10 minutes after the run: 3 volumes,
  45 GiB at most. A synced write takes 0.6 ms there, 22-651 ms on the node disks, and a
  full volume stops only its slot. Jobs also run under `eatmydata`, without disk syncs.
- **It waits for deploys.** A run does not start while `Deploy (staging)` is running.
- **Isolation.** Own namespace at the Baseline Pod Security Standard, no service-account
  token, no privileges, and egress to DNS and the public internet only: not Postgres, not
  Qdrant, not the cluster API. The rules are ADR-0119's.
- **The token stays out of CI's reach.** Only the feeder container holds the GitHub
  token, limited to the test copy. The runner beside it gets single-use registrations.

## What it covers

No Docker daemon, as ADR-0119 decides, so `vars.CI_NO_DOCKER` on the test copy switches
Qdrant, gitleaks, semgrep and trivy to binaries from the image. **Hatchet and the image
builds need a daemon and are skipped.** On `archos-at/archos`, where the variable is
unset, `ci.yml` behaves as before.

It tests the test copy's branch `tb/ci-shadow`, which is `develop` at its last refresh
plus light runs and these changes, not your latest commit.

## Install

```bash
KUBECONFIG=... CI_SHADOW_GITHUB_TOKEN=... ./ci-shadow install
```

The token is a fine-grained one for `thom-ba/archos-ci` only: Administration, Actions,
Contents and Variables read and write. The image is public, built from `Dockerfile`, and
needs no pull secret.

## Why not actions-runner-controller

ADR-0119's plan is the better lasting setup. This is for trying it now: no CRDs, no
controller and no always-on listener, so nothing here can clash with it. Run
`./ci-shadow off` before that lands.
