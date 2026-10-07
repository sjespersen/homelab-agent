# Wi-Fi watchdog

Wi-Fi can get stuck: the server stays connected to the router and keeps its addresses, but
replies stop arriving. Nothing logs an error, and it lasts until someone reconnects it (on
the reference box that took 14 hours and a press of the power button). Ethernet avoids this.

The watchdog pings the router and the internet (Quad9, Cloudflare, Quad9 over IPv6) every
minute, through the Wi-Fi interface:

- router unreachable for 3 minutes: reconnect
- router fine, internet gone for 10 minutes (often the provider): reconnect anyway
- first try: `wpa_cli reassociate`; if that didn't help, restart the Wi-Fi client
  (`netplan-wpa-<iface>.service`, or the link itself) and `networkctl reconfigure`
- after both: one more attempt every 30 minutes, until traffic flows again

Turn it on by putting the interface into `config.env` as `WIFI_WATCHDOG_IFACE` (setup.sh
suggests it when the server is on Wi-Fi) and rerunning `./setup.sh`. Empty turns it off.

See what it did: `journalctl -u wifi-watchdog --since today`
