#!/usr/bin/env bash
set -euo pipefail

python3 /home/sibilla-cumana/jellyfin-novita-agent/export_real_views_from_playback_db.py >/dev/null

exec /usr/bin/curl -sS -X POST http://127.0.0.1:9079/api/run-agent \
  -H 'Content-Type: application/json' \
  --data-raw '{"views_file":"/home/sibilla-cumana/jellyfin-novita-agent/views_90d.real.json","execute":true}'
