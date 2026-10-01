# Run by install.sh after the stack is up.
# Containers don't notice edited config files; reload Caddy so Caddyfile changes apply.
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
