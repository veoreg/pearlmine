#!/bin/bash
# Read-only: what the panel shows for the Spark, beyond nvidia-smi. Printed over the panel's one SSH connection.
BASE=/srv/spark/pearlmine
echo PEARLMINE_READY
[ -f "$BASE/run/status.json" ] && echo "PEARLMINE $(cat "$BASE/run/status.json")"
echo "PEARLMINE_HOT $(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)"
echo "PEARLMINE_JOB $(sed -n 's/.*"name": *"\([^"]*\)".*/\1/p' /srv/spark/run/heavy.lock 2>/dev/null | head -1)"
