#!/usr/bin/env bash
set -euo pipefail

cd /home/sibilla-cumana/jellyfin-novita-agent

python3 export_real_views_from_playback_db.py
python3 clean_real_views.py
