# USB wake

Wakes the box from suspend with a USB keyboard that you can press over the network: a Raspberry
Pi Zero 2 W in USB gadget mode, plugged into the box. Unlike Wake-on-LAN it works while the box
is on Wi-Fi.

```
./ai-model.sh wake ──▶ Pi (POST /api/wake) ──USB remote wakeup──▶ box wakes up
```

## The Pi

`pi/` holds what runs on it:

- `aiwake-gadget.sh` + `aiwake-gadget.service`: at boot, sets the Pi up as a boot keyboard
  (configfs, USB id `1d6b:0104`) that may wake the host (`bmAttributes 0xa0`).
- `aiwake.py` + `aiwake.service`: a small HTTP service on port 80.
  `POST /api/wake` sends a USB remote wakeup while the box sleeps (and taps Shift while it's
  awake); `GET /api/state` reports `configured` (box awake), `suspended` (asleep) or
  `not attached`.

Set it up:

1. Flash Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager: hostname (e.g. `aiwake`),
   your Wi-Fi and your SSH key.
2. On the card's boot partition, add `dtoverlay=dwc2,dr_mode=peripheral` under `[all]` in
   `config.txt`, and copy the four files from `pi/` to `/usr/local/sbin/aiwake-gadget`,
   `/usr/local/bin/aiwake.py` and `/etc/systemd/system/`, then enable both services. On a
   fresh card you can do that from `user-data` (cloud-init `write_files` + `runcmd`).
3. Plug the Pi's **USB** port (the middle one, not PWR) into the box with a data cable. The box
   powers it. `curl http://aiwake.local/api/state` should say `configured`.

## The box

Set `USB_WAKE_DEVICE="1d6b:0104"` and run `./setup.sh`. Bootstrap installs a udev rule that
allows wakeup on the keyboard and on every hub and PCIe bridge between it and the CPU (root hubs
and the X570 chipset bridges come with it off, and the wake signal never arrives).

For `./ai-model.sh wake`, set `USB_WAKE_URL` (e.g. `http://aiwake.local`). Away from home, set
`USB_WAKE_VIA` to an always-on box on that network (e.g. `me@homelab.local`, over Tailscale)
and the request is sent from there.

From suspend this works without BIOS changes on many boards. From off it needs the BIOS to keep
USB powered and listen to it: on MSI boards, Settings → Advanced → Wake Up Event Setup →
Resume By USB Device: Enabled, and Power Management Setup → ErP Ready: Disabled.

Anyone on your network can call `/api/wake`; all it does is wake the box or tap Shift.
