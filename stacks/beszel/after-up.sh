# Run by install.sh after the stack is up. $1: the stack's directory.
# The Beszel hub only writes its private key (on first start); the agent needs the public half.
data=$1/data
for _ in $(seq 30); do [[ -f $data/id_ed25519 ]] && break; sleep 1; done
if [[ -f $data/id_ed25519 && ! -f $data/id_ed25519.pub ]]; then
  ssh-keygen -y -f "$data/id_ed25519" >"$data/id_ed25519.pub"
fi
