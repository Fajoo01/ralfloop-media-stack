# BaffoFlix movie quality replacements — 2026-09-20

Follow-up to the 166-film ffprobe scan. The replacement set contains the 37 physical movie files with actual width below 1200 px.

Policy used for acquisition:
- accept only explicit 720p/1080p releases;
- require Italian audio markers and at least one active source;
- reject AVI, DivX/XviD, DVDRip, 480p/576p/360p, CAM/TS/screener;
- keep the old file until the replacement is present in Jellyfin and passes ffprobe;
- then remove both origin/cache copies and refresh Jellyfin.

Result of KAD + global search:
- 14 valid HD replacements queued;
- 23 titles currently have no acceptable HD candidate;
- one false positive, `Passion.2012.1080p.BluRay.x264.ENG.Sub.iTALiAN.mp4`, was detected and cancelled because it had English audio with Italian subtitles only.

Queued replacements:
- Fast & Furious 6 -> 1080p HEVC, GER/SPA/ENG/ITA
- Trash -> 1080p HEVC, GER/SPA/ENG/ITA
- Now You See Me -> 1080p HDR ITA/ENG
- Le paludi della morte -> 1080p ITA/ENG DTS
- Dark Places -> 1080p ITA/ENG DTS
- Giovanna d'Arco -> 720p BluRay ITA/ENG
- Zodiac -> 1080p H265 ITA/ENG
- Bastardi senza gloria -> 1080p BluRay ITA/ENG
- La ricerca della felicità -> 1080p WEB-DL ITA/ENG
- Non dirlo a nessuno -> 1080p BluRay ITA/ENG
- La preda perfetta -> 1080p ITA/ENG
- Crudelia -> 1080p ITA/ENG
- I figli degli uomini -> 1080p H265 ITA/ENG
- November Criminals -> 1080p ITA/ENG

No acceptable HD candidate found in the current searches:
- Odissea; Gozu; Cut Bank; Lars e una ragazza tutta sua
- Kill Bill: Volume 2; September 5; Beyond the Edge; Columbus Circle
- Conspiracy of Faith; L'illusione perfetta; Passion; Tre fratelli
- The Horsemen; No Good Deed; Una battaglia dopo l’altra
- La banda Baader Meinhof; Nella rete del serial killer
- C'era una volta a… Hollywood; Dheepan; Fuochi d'artificio in pieno giorno
- M - Il mostro di Düsseldorf; Red Riding: 1974; Il pranzo di Babette

Production runtime:
- manifest: `/home/sibilla-cumana/jellyfin-novita-agent/data/movie_quality_replacements.json`
- watcher: `/home/sibilla-cumana/jellyfin-novita-agent/movie_quality_replacement_watcher.py`
- watcher interval: 300 seconds
- initial state after activation: 14 pending, 0 swapped

The watcher is deliberately scoped to this manifest. It does not enable the global destructive `jellyfin_bad_media_autofix_execute_enabled` switch.