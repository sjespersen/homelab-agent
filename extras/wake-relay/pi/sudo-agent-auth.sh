#!/bin/bash
# Usage: sudo bash sudo-agent-auth.sh "<ssh public key>"
# Lets sudo on the Pi accept a signature from your forwarded SSH agent instead of the password.
# With a key that asks for Touch ID on every use (Secretive on a Mac), sudo then waits for your
# fingerprint. The password still works as a fallback.
set -eu
key=$1
[[ $key == ssh-* || $key == ecdsa-* ]] || { echo "not an SSH public key: $key" >&2; exit 1; }
apt-get install -y libpam-ssh-agent-auth >/dev/null
install -m 644 /dev/null /etc/security/sudo_authorized_keys
echo "$key" >/etc/security/sudo_authorized_keys
line="auth sufficient pam_ssh_agent_auth.so file=/etc/security/sudo_authorized_keys"
grep -qF "$line" /etc/pam.d/sudo || sed -i "/^@include common-auth/i $line" /etc/pam.d/sudo
echo 'Defaults env_keep += "SSH_AUTH_SOCK"' >/etc/sudoers.d/020_ssh-agent-auth
chmod 440 /etc/sudoers.d/020_ssh-agent-auth
visudo -c >/dev/null || { rm /etc/sudoers.d/020_ssh-agent-auth; echo "sudoers check failed" >&2; exit 1; }
echo "sudo accepts the key now"
