---
name: wake-device
description: Wake a sleeping or switched-off computer on the home network (Wake-on-LAN).
version: 1.0.0
author: homelab-agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [homelab, wake-on-lan, power]
    category: homelab
---

# Wake a device

Use this when the user asks to wake, start or turn on one of these devices, or when a task needs
one of them and it doesn't answer: @NAMES@.

The magic packet has to come from the server itself (broadcasts from this container don't reach
the network), so ask the host to send it:

```sh
echo <name> > /opt/data/wake/request
sleep 3
cat /opt/data/wake/result
```

The result line says whether the packet was sent, or that the name is unknown.

Then wait for the device: about 10 seconds from sleep, about 30-60 seconds from off. If you
know a web address on it, check with `curl -s -o /dev/null -w '%{http_code}\n' <url>` until it
answers (an AI box with the homelab-agent ai module: its Local AI page on port 8005, at
`/_ai/login`; its models need another ~30-60 seconds to load after that).

Tell the user what you did and whether the device came back. Don't send the packet repeatedly:
if the device doesn't answer after two minutes, say so.
