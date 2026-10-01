# Quieter fan on ITE IT87xx boards

Some BIOSes (the Shuttle DS61's, for example) set a high minimum fan speed on every boot.
This loads the `it87` driver and lowers the minimum on `pwm1` at boot. The chip's own
temperature curve stays in charge, so the fan still speeds up when the CPU gets hot.

Only use it if your board has an IT87xx chip and `pwm1` drives the CPU fan. Check first:

1. `sudo apt install lm-sensors && sudo modprobe it87 && sensors` should list an `it87xx` chip with a fan.
2. Find a safe minimum: watch `sensors` while you lower it by hand, and stop where the RPM
   stops dropping. `echo 50 | sudo tee /sys/class/hwmon/hwmonN/pwm1_auto_start` (N from `sensors`).
3. Put that number into `config.env` as `IT87_FAN_MIN_PWM` and rerun `bootstrap.sh`.

Reference: Shuttle DS61 (IT8728F) default 82 = 2080 RPM; 50 = 1450 RPM; below 50 no change.

If the Beszel dashboard doesn't show the fan, restart its agent once: `docker restart beszel-agent`
(it only looks for sensors at startup).
