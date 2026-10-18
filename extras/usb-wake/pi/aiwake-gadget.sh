#!/bin/bash
# Sets the Pi up as a USB keyboard (configfs gadget) that may wake the host.
# Runs once at boot from aiwake-gadget.service.
set -e
modprobe dwc2
modprobe libcomposite
G=/sys/kernel/config/usb_gadget/aiwake
[ -d "$G" ] && exit 0

mkdir -p "$G"
cd "$G"
echo 0x1d6b > idVendor    # Linux Foundation
echo 0x0104 > idProduct   # Multifunction Composite Gadget
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "aiwake" > strings/0x409/manufacturer
echo "aiwake Wake Keyboard" > strings/0x409/product
cat /proc/device-tree/serial-number 2>/dev/null | tr -d '\0' > strings/0x409/serialnumber || true

# Boot keyboard, 8-byte reports
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length
printf '\x05\x01\x09\x06\xa1\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0' \
  > functions/hid.usb0/report_desc

mkdir -p configs/c.1/strings/0x409
echo "Wake keyboard" > configs/c.1/strings/0x409/configuration
echo 0xa0 > configs/c.1/bmAttributes   # bus powered + remote wakeup
echo 100 > configs/c.1/MaxPower
ln -s functions/hid.usb0 configs/c.1/

ls /sys/class/udc | head -n1 > UDC
