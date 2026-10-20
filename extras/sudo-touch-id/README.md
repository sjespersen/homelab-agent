# sudo with Touch ID

`sudo` on a server waits for your fingerprint on the Mac instead of asking for the password.
That lets you (or an agent working over SSH for you) run root commands, while every single one
still needs your finger. The password keeps working as a fallback.

How it works: [Secretive](https://github.com/maxgoedjen/secretive) keeps an SSH key in the Mac's
Secure Enclave and asks for Touch ID each time it signs. The SSH connection forwards only that
agent, and `libpam-ssh-agent-auth` on the server lets sudo accept a signature from it. Works
with sudo-rs (Ubuntu 26.04) and classic sudo.

1. On the Mac: `brew install --cask secretive`, turn on its agent (you can skip the shell setup)
   and create a key that requires authentication. Its public key:
   `SSH_AUTH_SOCK=~/Library/Containers/com.maxgoedjen.Secretive.SecretAgent/Data/socket.ssh ssh-add -L`
2. On each server: `sudo bash sudo-agent-auth.sh "<that public key>"`
3. In `~/.ssh/config` on the Mac, a `-root` host per server that logs in with your normal key
   and forwards only Secretive (`HostKeyAlias` reuses the server's known host key):

   ```
   Host homelab-root
     HostName homelab.local
     HostKeyAlias homelab.local
     User me
     ForwardAgent ~/Library/Containers/com.maxgoedjen.Secretive.SecretAgent/Data/socket.ssh
   ```

   `ssh homelab-root sudo …` then waits for your fingerprint.

The Touch ID prompt says which key is used, not for which command. Only connect to `-root` hosts
when you mean to run something as root, and look at what you approve.
