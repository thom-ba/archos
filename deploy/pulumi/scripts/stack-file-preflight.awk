# The stack-file preflight of a dev deploy: fail, naming the reason, while
# Pulumi.dev.yaml is still the template. Usage, with the ESC environment the
# stack file must import (`<project>/dev`):
#   awk -v esc_env=<project>/dev -f stack-file-preflight.awk Pulumi.dev.yaml
#
# deploy-dev.sh runs it; .github/workflows/deploy.yml carries the same program
# inline, with the environment written out, where its comment explains each
# rule. tests/test_deploy_dev.py keeps the two answering alike.

# Documentation and blank lines carry no configuration.
/^[[:space:]]*#/ || /^[[:space:]]*$/ { next }

# Track the current top-level key (column 0 = document root).
/^[^[:space:]]/ { top = $0; sub(/:.*$/, "", top) }

/REPLACE_ME|example\.com/ {
  printf "placeholder value still in the stack file: %s\n", $0
  placeholder = 1
}

# Committed ciphertext: a `secure:` mapping key inside the config tree.
top == "config" && /^[[:space:]]+secure:[[:space:]]*[^[:space:]]/ { encrypted = 1 }

# ESC import: `environment: [<esc_env>]` or a `- <esc_env>` item
# under a top-level `environment:` block. Any other value is a
# different environment and must not satisfy the check.
top == "environment" {
  entry = $0
  sub(/^[[:space:]]*environment:[[:space:]]*/, "", entry)
  gsub(/[][,"]/, " ", entry)
  sub(/^[[:space:]]*-[[:space:]]*/, " ", entry)
  n = split(entry, fields, /[[:space:]]+/)
  for (i = 1; i <= n; i++) {
    if (fields[i] == "") continue
    if (fields[i] == esc_env) esc = 1
    else other = fields[i]
  }
}

BEGIN {
  if (esc_env == "") {
    print "stack-file-preflight.awk needs -v esc_env=<project>/dev"
    exit 2
  }
}

END {
  if (esc_env == "") exit 2
  if (placeholder) exit 1
  if (top == "") {
    print "Pulumi.dev.yaml has no configuration at all."
    exit 1
  }
  if (!encrypted && !esc) {
    if (other != "") printf "environment: imports %s, not the %s ESC environment\n", other, esc_env
    else print "no encrypted config found - neither a committed secure: entry under config: nor an environment: import of " esc_env
    exit 1
  }
}
