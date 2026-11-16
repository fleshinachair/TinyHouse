#!/usr/bin/env bash
#
# Check that the Move's directory ownership is still healthy. Run it before and after ANY copy to the Move.
#
# 2026-11-16: a root `tar -C / -xf -` of an archive that carried directory entries changed the owner of
# /, /data and /data/UserData to the PC user. sshd StrictModes then refused every login and a factory
# reset did not undo it. Nothing in this repo may extract or copy directories as root: copy single files
# (scp / `tar -T filelist`, files only) into paths below /data/UserData/schwung.
#
#   ./scripts/move_check_owner.sh [host]      exit 0 = healthy, 1 = a directory is not root-owned
set -euo pipefail
HOST="${1:-move.local}"
bad=0
while read -r owner mode path; do
    case "$path" in
        /|/data) want=root ;;       # sshd StrictModes: these must be root-owned and not group/world writable
        *) continue ;;
    esac
    if [ "$owner" != "$want" ] || [ "${mode:5:1}" = "w" ] || [ "${mode:8:1}" = "w" ]; then
        echo "BAD  $path owner=$owner mode=$mode (want owner=$want, not group/world writable)" >&2
        bad=1
    else
        echo "ok   $path owner=$owner mode=$mode"
    fi
done < <(ssh "$HOST" 'stat -c "%U %A %n" / /data')
[ "$bad" = 0 ] || { echo "STOP: do not copy anything else to the Move until this is fixed." >&2; exit 1; }
