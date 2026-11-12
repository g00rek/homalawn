#!/usr/bin/env bash
# A dedicated test AVD for Homa Lawn's device tests, on emulator port 5560 (serial emulator-5560).
# Keep it separate from any emulator you use for other work: the device tests change its home screen.
#   HOMALAWN_AVD        the AVD's name (default homalawn-test)
#   HOMALAWN_AVD_IMAGE  the system image, as sdkmanager names it (default: Android 15, Google Play, x86_64)
set -euo pipefail
avd="${HOMALAWN_AVD:-homalawn-test}"
img="${HOMALAWN_AVD_IMAGE:-system-images;android-35;google_apis_playstore;x86_64}"
case "${1:-}" in
  create) # avdmanager fails on this image (no devices.xml), so write the AVD by hand.
          dir=~/.android/avd/$avd.avd; mkdir -p "$dir"
          sysdir="$(echo "$img" | sed 's|^system-images;||; s|;|/|g')"
          printf 'avd.ini.encoding=UTF-8\npath=%s\npath.rel=avd/%s.avd\ntarget=android-35\n' "$dir" "$avd" > ~/.android/avd/$avd.ini
          cat > "$dir/config.ini" <<CFG
AvdId=$avd
avd.ini.displayname=$avd
abi.type=x86_64
hw.cpu.arch=x86_64
hw.cpu.ncore=4
hw.ramSize=3072
hw.device.manufacturer=Google
hw.device.name=pixel_7
hw.lcd.width=1080
hw.lcd.height=2400
hw.lcd.density=420
hw.keyboard=yes
hw.gpu.enabled=yes
hw.gpu.mode=auto
hw.sdCard=no
disk.dataPartition.size=6G
image.sysdir.1=system-images/$sysdir/
tag.id=google_apis_playstore
tag.display=Google Play
PlayStore.enabled=true
target=android-35
CFG
          ;;
  start)  exec emulator -avd "$avd" -gpu host -no-boot-anim -memory 3072 -port 5560 ;;
  serial) echo emulator-5560 ;;
  wait)   adb -s emulator-5560 wait-for-device
          until [ "$(adb -s emulator-5560 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; do sleep 2; done ;;
  *) echo "usage: avd.sh create|start|wait|serial" >&2; exit 2 ;;
esac
