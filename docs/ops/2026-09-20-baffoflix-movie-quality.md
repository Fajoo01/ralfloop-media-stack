# BaffoFlix movie quality incident — 2026-09-20

## Symptom

`Il cigno nero / Black Swan (2010)` was served from an old AVI release:

- `Il.Cigno.Nero.Black.Swan.2010.iTALiAN.AC3.DvDrip.(PapeeteGroup).avi`
- actual video: MPEG-4/DivX, 640x272
- audio: AC3 5.1
- size: ~1.53 GiB

This was not a Jellyfin playback-quality issue. The acquisition pipeline had accepted a low-quality source.

## Root cause

A historical run on 2026-09-14 used the movie fallback path described as `legacy/back-catalog` after better candidates were not confirmed in the aMule queue. That fallback accepted the DVD-rip AVI.

The production pipeline had already become stricter on 2026-09-17, but the old file remained indexed in the library.

## Fix

Movie auto-download now fails closed before invoking the aMule download endpoint:

- reject AVI;
- reject DivX/XviD/DVD-rip/screener/R5/SD-resolution markers;
- require an explicit HD resolution marker: 720p, 1080p, 2160p/4K/UHD.

Production also has an ffprobe-based second gate: movie files below 1200 pixels of actual frame width are marked `movie_resolution_below_hd`. This catches misleading filenames.

## Remediation performed

Both 640x272 copies were removed from the active movie roots and a Jellyfin library refresh was requested successfully. A subsequent Jellyfin search no longer returned the stale movie entry.

A replacement was found and queued:

- `Black Swan 2010 BDRip ITA ENG 1080p x265 Paso77.mkv`
- size: 6,689,178,200 bytes (~6.38 GB)
- aMule hash: `10088FBDB653EE0AC00B2BB0D67DF98B`
- queue insertion verified through EC

The search initially reported one source. Source availability may fluctuate while the item is queued.

## Regression rule

Availability/source count must never override the minimum movie-quality gate. If no qualifying HD candidate exists, automatic acquisition must stop rather than fall back to SD/legacy media.
