# Run by bootstrap.sh as root when Pi-hole is enabled.
# systemd-resolved listens on port 53 by default; free it for Pi-hole.
mkdir -p /etc/systemd/resolved.conf.d
printf '[Resolve]\nDNSStubListener=no\n' >/etc/systemd/resolved.conf.d/90-pihole.conf
ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
systemctl restart systemd-resolved
