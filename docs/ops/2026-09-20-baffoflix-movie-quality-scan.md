# BaffoFlix movie quality scan — 2026-09-20

Scanned `/mnt/origin_media/Film` with ffprobe.

- physical movie files checked: 166
- ffprobe failures: 0
- suspicious files: 37
- rule: flag actual width < 1200 px, legacy SD name markers, or MPEG-4 Part 2/MSMPEG4 codecs

Worst confirmed examples:
- Fast And Furious 6 — 640x272 MPEG-4 Part 2
- Trash (2014) — 640x272 MPEG-4 Part 2
- Now You See Me — 684x284 H.264
- Cut Bank — 688x288 MPEG-4 Part 2
- Le Paludi Della Morte — 716x298 DVDRip
- Dark Places — 720x298
- Giovanna d'Arco — 720x300
- Kill Bill vol.2 — 720x300
- September 5 — 720x300 SD
- Zodiac — 720x300 DVDRip
- Bastardi senza gloria — 720x302 DVDRip
- Columbus Circle — 720x304 MPEG-4 Part 2 AVI
- La Preda Perfetta — 720x304 XviD AVI
- Gozu — 640x350 MPEG-4 Part 2 AVI
- Passion (2012) — 720x384 MPEG-4 Part 2 AVI

Notes: 1196x720 and some 848x460/864x720 files are borderline rather than necessarily defective. Replacement should prioritize the low-width/legacy-codec group first.
