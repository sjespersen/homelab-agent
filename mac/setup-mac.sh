#!/bin/bash
# One-time Mac setup for backup pulls (needs Homebrew). Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"
source ../config.env
host="$SERVER_USER@$SERVER_HOST"
key="$HOME/.ssh/${NAME}_backup_pull"
label="home.$NAME.backup-pull"
plist="$HOME/Library/LaunchAgents/$label.plist"
dir="$HOME/Backups/$NAME"

# A passphrase-less key is needed for an unattended job, so the server limits it to
# read-only access to the backup folder.
[[ -f $key ]] || ssh-keygen -q -t ed25519 -N '' -C "$NAME backup pull (read-only)" -f "$key"
entry="restrict,command=\"rrsync -ro backups/restic\" $(cat "$key.pub")"
ssh "$host" "grep -qF '$(cut -d' ' -f2 "$key.pub")' ~/.ssh/authorized_keys || echo '$entry' >>~/.ssh/authorized_keys"

# Keep the repo password in the Keychain so backups can be restored without the server.
if ! security find-generic-password -s "$NAME-restic" >/dev/null 2>&1; then
  security add-generic-password -a "$SERVER_USER" -s "$NAME-restic" \
    -w "$(ssh "$host" 'cat ~/.config/restic/password')"
fi

command -v restic >/dev/null || brew install restic
# Apple's openrsync sends --delete to the server even when pulling; rrsync -ro rejects that.
[[ -x /opt/homebrew/bin/rsync ]] || brew install rsync

mkdir -p "$dir" "$HOME/Library/LaunchAgents"
install -m 755 pull-backup.sh "$dir/pull-backup.sh"
cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>
    <string>$dir/pull-backup.sh</string><string>$NAME</string><string>$host</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$dir/pull.log</string>
  <key>StandardErrorPath</key><string>$dir/pull.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "Installed. Log: $dir/pull.log"
