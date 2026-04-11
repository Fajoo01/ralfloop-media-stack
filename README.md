# Ralfloop Media Stack

Stack multi-servizio per orchestrazione media su nodi collegati in VPN.

## Componenti

- Ralfloop
- Ralfloop Bridge
- TV Server
- Stream Scraper
- aMule Wrapper
- Jellyfin Novità Agent

## Architettura

- Sibilla: nodo principale
- Ildello: nodo secondario
- VPN: collegamento tra nodi
- MinIO: storage/object layer
- Jellyfin: media server
- aMule wrapper: API per controllo download
- Peppule: orchestrazione media/download
- Ralfloop: automazione e bridge
- TV Server / Stream Scraper: gestione stream live

## Note

Le configurazioni sensibili non sono incluse.
Usare file di esempio e variabili ambiente per la configurazione locale.
