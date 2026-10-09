# Wake-on-LAN

Lets the server be woken over the network after it was suspended or shut down, e.g. from the
Local AI page's Power buttons. Set `WAKE_ON_LAN_IFACE` (an Ethernet port; Wi-Fi can't) and
`WAKE_ON_LAN_MAC` in the config and run `./setup.sh`: bootstrap installs `wake-on-lan.service`,
which turns on magic-packet wake (`ethtool -s <iface> wol g`) at every boot and allows wakeup on
the card and every PCIe bridge between it and the CPU (on X570 boards the chipset bridges come
with it off, and the wake signal never arrives).

Wake it: `./ai-model.sh wake` sends the magic packet from your computer, which has to be on the
same network. Away from home, set `WAKE_ON_LAN_VIA` to an always-on box on that network (e.g.
`me@homelab.local`, over Tailscale) and the packet is sent from there.

The BIOS has to allow it too, for waking from suspend as well as from off. On MSI boards: Settings → Advanced →
Wake Up Event Setup → Resume By PCI-E Device: Enabled, and Power Management Setup → ErP Ready:
Disabled (ErP cuts the network card's standby power). Check with `sudo ethtool <iface> | grep
Wake-on` (should say `g`).
