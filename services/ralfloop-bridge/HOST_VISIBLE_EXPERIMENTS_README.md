# Host-visible experiments

Questo file serve a tenere separati gli esperimenti sul ramo host-visible dalla baseline.

Regole:
- NON toccare main_plugin.py per esperimenti host-visible
- lavorare solo su main_plugin.host_visible_experiments.py
- ogni modifica richiede smoke test separati
- se una prova peggiora il comportamento, si butta senza toccare la baseline

Obiettivo futuro:
- il coder deve generare solo il contenuto finale di result.py
- il plugin deve scrivere lui il file host-visible
- niente writer-script autoreferenziali
