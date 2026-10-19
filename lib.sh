# Shared by the scripts in this repo. Set $repo to the repo's directory, then source this file.

# The settings a module can have in stacks/<module>/module.env (see README, "Modules").
module_vars=(TITLE DESCRIPTION ORDER CORE OPT_IN HOST PORT LINK FIREWALL_PORTS BACKUP_STOP ENV SITES)

# One config file per server: config.env, or another one in the repo named by CONFIG
# (CONFIG=config.ai.env ./deploy.sh). On the server it is always config.env.
config_file=${CONFIG:-config.env}

# module_var <module> <setting>: prints one setting from stacks/<module>/module.env.
module_var() {
  (
    unset "${module_vars[@]}"
    source "$repo/stacks/$1/module.env"
    printf '%s' "${!2:-}"
  )
}

# All modules in start order, core ones (always on) included.
all_modules() {
  local dir
  for dir in "$repo"/stacks/*/; do
    [[ -f $dir/module.env ]] || continue
    printf '%s %s\n' "$(module_var "$(basename "$dir")" ORDER)" "$(basename "$dir")"
  done | sort -n | cut -d' ' -f2
}

# The modules you can switch on and off in config.env.
optional_modules() {
  local m
  for m in $(all_modules); do
    [[ $(module_var "$m" CORE) == yes ]] || printf '%s\n' "$m"
  done
}

# The modules MODULES means when it isn't set: the optional ones, minus those that need special
# hardware (OPT_IN=yes, like ai's GPU).
default_modules() {
  local m
  for m in $(optional_modules); do
    [[ $(module_var "$m" OPT_IN) == yes ]] || printf '%s\n' "$m"
  done
}

# Reads the config file. MODULES defaults to default_modules.
load_config() {
  if [[ ! -f $repo/$config_file ]]; then
    echo "No $config_file yet: run ./setup.sh, or copy config.example.env to $config_file." >&2
    exit 1
  fi
  set -a
  source "$repo/$config_file"
  set +a
  if [[ -z ${MODULES+set} ]]; then MODULES=$(echo $(default_modules)); fi
  local m
  for m in $MODULES; do
    [[ -f $repo/stacks/$m/module.env ]] || { echo "Unknown module in MODULES: $m" >&2; exit 1; }
  done
}

has_module() { [[ " $MODULES " == *" $1 "* ]]; }

# A module's web UIs, one "<site> <host> <port> <caddy file>" line each (port "-" when it has
# none): the main one (HOST, PORT, site.caddy) and any in SITES, e.g. SITES=swarm with
# SITE_swarm_HOST, SITE_swarm_PORT, SITE_swarm_TITLE, SITE_swarm_DESCRIPTION, site.swarm.caddy.
module_sites() {
  local host port s
  host=$(module_var "$1" HOST)
  port=$(module_var "$1" PORT)
  [[ -z $host ]] || echo "main $host ${port:--} site.caddy"
  for s in $(module_var "$1" SITES); do
    port=$(module_var "$1" "SITE_${s}_PORT")
    echo "$s $(module_var "$1" "SITE_${s}_HOST") ${port:--} site.$s.caddy"
  done
}

# The ports Caddy serves a module on when there are no Pi-hole names.
module_ports() { module_sites "$1" | awk '$3 != "-" { print $3 }'; }

# module_site_var <module> <site> <setting>: TITLE, DESCRIPTION or LINK of one of its web UIs.
module_site_var() {
  if [[ $2 == main ]]; then module_var "$1" "$3"; else module_var "$1" "SITE_$2_$3"; fi
}

# Enabled modules plus the core ones, in start order.
enabled_modules() {
  local m
  for m in $(all_modules); do
    if [[ $(module_var "$m" CORE) == yes ]] || has_module "$m"; then printf '%s\n' "$m"; fi
  done
}

# Changes whenever a re-run of bootstrap.sh would change something. setup.sh compares it with
# the one bootstrap.sh saved on the server to decide whether bootstrap needs to run again.
bootstrap_fingerprint() {
  local sum=sha256sum
  command -v sha256sum >/dev/null || sum="shasum -a 256"
  (
    cd "$repo" || exit
    cat bootstrap.sh lib.sh config.env stacks/*/module.env stacks/*/root-setup.sh stacks/*/host/* extras/wake-on-lan/*.service extras/it87-fan/*.s* extras/wifi-watchdog/*.s* extras/wifi-watchdog/*.timer 2>/dev/null
  ) | $sum | cut -c1-16
}
