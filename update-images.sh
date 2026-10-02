#!/usr/bin/env bash
# From your computer: moves every image in stacks/*/compose.yaml to the newest build of the tag
# it follows, pinned by digest, so the repo says exactly what runs:
#   image: caddy:2@sha256:0c99...  # v2.11.4
#          tag it follows, exact build, version (for humans)
# The server does the pulling. Review with `git diff`, then deploy and commit.
# ./deploy.sh --update runs this for you (with a backup first).
set -euo pipefail
cd "$(dirname "$0")"
source config.env

refs=$(sed -n 's/^ *image: *\([^@ ]*\).*/\1/p' stacks/*/compose.yaml | sort -u)
echo "== resolving $(echo "$refs" | wc -l | tr -d ' ') images on the server"

# Prints "<ref> <digest> <version>" per image. The digest is the multi-platform index, so the
# pin works on any CPU architecture.
resolved=$(ssh "$SERVER_USER@$SERVER_HOST" bash -s -- $refs <<'EOF'
set -euo pipefail
for ref in "$@"; do
  docker pull -q "$ref" >/dev/null
  digest=$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$ref" \
    | grep -m1 "^${ref%:*}@" | cut -d@ -f2)
  version=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$ref")
  if [[ -z $version || $version == "<no value>" ]]; then
    version="built $(docker image inspect -f '{{.Created}}' "$ref" | cut -c1-10)"
  fi
  echo "$ref $digest $version"
done
EOF
)

while read -r ref digest version; do
  REF=$ref DIGEST=$digest VER=$version perl -pi -e \
    's{^(\s*image:\s*)\Q$ENV{REF}\E(?:\@sha256:[0-9a-f]+)?.*$}{$1$ENV{REF}\@$ENV{DIGEST}  # $ENV{VER}}' \
    stacks/*/compose.yaml
done <<<"$resolved"

if git diff --quiet -- stacks; then
  echo "All images are already the newest."
else
  git --no-pager diff -U0 -- stacks | grep '^[-+] *image:' | sed 's/@sha256:[0-9a-f]*//'
fi
