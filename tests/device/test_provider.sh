#!/usr/bin/env bash
set -euo pipefail
S="${HOMALAWN_SERIAL:-emulator-5560}"
P="${HOMALAWN_PACKAGE:-app.homalawn.debug}"
callj() { adb -s "$S" shell "content call --uri content://$P.omalawn --method $1 --arg '$2'"; }
call() { adb -s "$S" shell content call --uri content://$P.omalawn --method "$@"; }
out="$(call ping)"; echo "$out"; grep -q 'ok=true' <<<"$out"; grep -q "\"package\":\"$P\"" <<<"$out"; grep -q '"model_loaded":' <<<"$out"
out="$(call prefs_list)"; grep -q 'ok=true' <<<"$out"
call pref_set --extra store:s:shared --extra key:s:pref_allowRotation --extra type:s:bool --extra value:s:true | grep -q 'ok=true'
sleep 3; call prefs_list | grep -q '"pref_allowRotation":{"type":"bool","value":true}'
call pref_set --extra store:s:shared --extra key:s:pref_allowRotation --extra type:s:bool --extra value:s:false | grep -q 'ok=true'
callj prefs_set_many '[{"store":"shared","key":"pref_allowRotation","type":"bool","value":"true"},{"store":"datastore","key":"dark_status_bar","type":"bool","value":"false"}]' | grep -q 'ok=true'
sleep 3; call prefs_list | grep -q '"pref_allowRotation":{"type":"bool","value":true}'
callj prefs_set_many '[{"store":"shared","key":"pref_allowRotation","type":"bool","value":"false"}]' | grep -q 'ok=true'
callj prefs_set_many '[{"store":"shared","key":"x","type":"int","value":"nope"}]' | grep -q 'ok=false'
# removal: a key that is set, then removed, is gone from both stores
callj prefs_set_many '[{"store":"shared","key":"omalawn_probe","type":"int","value":"1"},{"store":"datastore","key":"omalawn_probe","type":"string","value":"x"}]' | grep -q 'ok=true'
sleep 3; out="$(call prefs_list)"; [ "$(grep -o '"omalawn_probe"' <<<"$out" | wc -l)" = 2 ]
callj prefs_set_many '[{"store":"shared","key":"omalawn_probe","remove":true},{"store":"datastore","key":"omalawn_probe","remove":true}]' | grep -q 'ok=true'
sleep 3; ! call prefs_list | grep -q omalawn_probe
out="$(call widget_bind --arg x/y)"; grep -q 'ok=false' <<<"$out"   # removed: started activities, leaked ids
out="$(call widgets_list)"; grep -q 'ok=true' <<<"$out"; grep -q '"provider":' <<<"$out"
# layout export -> import of the same rows: in process, launcher pid unchanged, no files left behind
pid="$(adb -s "$S" shell pidof $P)"
out="$(call layout_export)"; grep -q '"how":"vacuum"' <<<"$out"
path="$(grep -o '"path":"[^"]*"' <<<"$out" | cut -d'"' -f4 | sed 's#\\/#/#g')"
adb -s "$S" exec-out run-as $P cat "$path" > "${TMPDIR:-/tmp}/homalawn-export.db"; adb -s "$S" shell run-as $P rm -f "$path"
head -c 16 "${TMPDIR:-/tmp}/homalawn-export.db" | grep -q 'SQLite format 3'
adb -s "$S" shell -T "run-as $P sh -c 'cat > files/omalawn/import-sh.db'" < "${TMPDIR:-/tmp}/homalawn-export.db"; rm -f "${TMPDIR:-/tmp}/homalawn-export.db"
call layout_import --arg "/data/user/0/$P/files/omalawn/import-sh.db" | grep -q '"items":'
call layout_import --arg /data/local/tmp/nope.db | grep -q 'bad_path'
for i in $(seq 1 40); do call ping | grep -q '"model_loaded":true' && break; sleep 0.5; done
[ "$(adb -s "$S" shell pidof $P)" = "$pid" ]
! adb -s "$S" shell run-as $P ls files/omalawn | grep -q 'port-'
out="$(call nope)"; grep -q 'ok=false' <<<"$out"
echo PASS
