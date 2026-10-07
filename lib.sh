# Shared by the scripts in this repo. Set $repo to the repo's directory, then source this file.

# The settings a module can have in stacks/<module>/module.env (see README, "Modules").
module_vars=(TITLE DESCRIPTION ORDER CORE HOST PORT LINK FIREWALL_PORTS BACKUP_STOP)

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

# Reads config.env. MODULES defaults to every optional module.
load_config() {
  if [[ ! -f $repo/config.env ]]; then
    echo "No config.env yet: run ./setup.sh, or copy config.example.env to config.env." >&2
    exit 1
  fi
  set -a
  source "$repo/config.env"
  set +a
  if [[ -z ${MODULES+set} ]]; then MODULES=$(echo $(optional_modules)); fi
  local m
  for m in $MODULES; do
    [[ -f $repo/stacks/$m/module.env ]] || { echo "Unknown module in MODULES: $m" >&2; exit 1; }
  done
}

has_module() { [[ " $MODULES " == *" $1 "* ]]; }

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
    cat bootstrap.sh lib.sh config.env stacks/*/module.env stacks/*/root-setup.sh extras/it87-fan/*.s* extras/wifi-watchdog/*.s* extras/wifi-watchdog/*.timer 2>/dev/null
  ) | $sum | cut -c1-16
}
