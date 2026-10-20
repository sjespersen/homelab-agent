# Wake relay

Wakes the box from suspend (and from off, if the BIOS allows it) while it's on Wi-Fi. Wake-on-LAN
only works on an Ethernet port, so a Raspberry Pi Zero 2 W with a USB-Ethernet adapter is cabled
straight to that port and sends the magic packet down the cable. The Pi itself is on Wi-Fi.

```
./ai-model.sh wake ──Wi-Fi──▶ Pi (POST /api/wake) ──LAN cable──▶ box's Ethernet port ──▶ box wakes up
```

The packet goes out as a raw Ethernet frame (EtherType 0x0842), so the cable needs no IP
addresses or DHCP.

## The Pi

`pi/` holds what runs on it: `aiwake.py` + `aiwake.service`, a small HTTP service on port 80.
`POST /api/wake` sends the magic packet; `GET /api/state` reports the cable's link (`up` while
the box's port has power, which it keeps during suspend when Wake-on-LAN is on) and whether the
box answers. `http://aiwake.local/` is a page with a Wake button: bookmark it instead of the
box's page, and it opens that page (`AIWAKE_OPEN`) as soon as the box answers on `AIWAKE_BOX`
(use its IP; the Pi may not resolve `.local` names).

Set it up:

1. Flash Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager: hostname (e.g. `aiwake`), your
   Wi-Fi and your SSH key.
2. Copy `aiwake.py` to `/usr/local/bin/` and `aiwake.service` to `/etc/systemd/system/`, set
   `AIWAKE_MAC` in it to the MAC of the box's Ethernet port (and `AIWAKE_BOX`/`AIWAKE_OPEN`), then
   `systemctl enable --now aiwake`. On a fresh card you can do that from `user-data`
   (cloud-init `write_files` + `runcmd`).
3. Plug the USB-Ethernet adapter into the Pi's **USB** port (the middle one, with a micro-USB OTG
   adapter) and cable it to the box's Ethernet port. Power the Pi from its PWR port.
   `curl http://aiwake.local/api/state` should say `"link": "up"`.

The service uses the first wired interface; set `AIWAKE_IFACE` if there's more than one.

### Keeping it on Wi-Fi

After the router resets its Wi-Fi, the Pi sometimes doesn't reconnect, and then nothing reaches
`/api/wake`. So the Pi gets three fixes:

- `aiwake-wifi-powersave.conf` turns Wi-Fi power saving off. That setting makes the Zero 2 W's
  Wi-Fi drop off.
- `aiwake-watchdog.sh` (via `aiwake-watchdog.timer`, every minute) pings the router over `wlan0`.
  If the router doesn't answer 3 times in a row, NetworkManager reconnects. If that doesn't
  help, the radio is switched off and on, and after 5 more failures the Pi reboots (at most
  every 30 minutes).
- `aiwake-hw-watchdog.conf` makes systemd use the hardware watchdog, so a hung Pi restarts
  itself.

```sh
sudo install -m 755 aiwake-watchdog.sh /usr/local/sbin/
sudo install -m 644 aiwake-watchdog.service aiwake-watchdog.timer /etc/systemd/system/
sudo install -D -m 644 aiwake-wifi-powersave.conf /etc/NetworkManager/conf.d/aiwake-wifi-powersave.conf
sudo install -D -m 644 aiwake-hw-watchdog.conf /etc/systemd/system.conf.d/aiwake-hw-watchdog.conf
sudo systemctl daemon-reload && sudo systemctl enable --now aiwake-watchdog.timer
sudo systemctl restart NetworkManager && sudo systemctl daemon-reexec
```

Check it with `journalctl -u aiwake-watchdog` and `iw dev wlan0 get power_save` (should be `off`).

## The box

Turn on Wake-on-LAN for that port: set `WAKE_ON_LAN_IFACE` and `WAKE_ON_LAN_MAC` and run
`./setup.sh` (see [extras/wake-on-lan](../wake-on-lan/README.md), also for the BIOS settings).
The port doesn't need to be configured otherwise; the box keeps using Wi-Fi.

For `./ai-model.sh wake`, set `WAKE_RELAY_URL` (e.g. `http://aiwake.local`). Away from home, set
`WAKE_RELAY_VIA` to an always-on box on that network (e.g. `me@homelab.local`, over Tailscale)
and the request is sent from there. Hermes can use it too: `WAKE_TARGETS="aibox=http://aiwake.local/api/wake"`.

Anyone on your network can call `/api/wake`; all it does is send the magic packet to the box.
