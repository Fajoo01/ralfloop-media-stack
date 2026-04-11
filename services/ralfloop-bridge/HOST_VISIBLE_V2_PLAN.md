# Host-visible v2 refactor plan

1. NON toccare main_plugin.py
2. Lavorare solo su main_plugin.host_visible_experiments.py
3. Aggiungere helper che riconosce candidate finali vs writer-script
4. Rendere _run_python_validation_host_visible un puro writer del candidate finale
5. Rendere il gate ostile a qualsiasi open/write su result.py
6. Testare offline il gate
7. Solo dopo fare un test live temporaneo con rollback immediato
