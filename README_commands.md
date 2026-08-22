# Comandes d'Ús Habitual

A continuació es documenten les comandes principals comentades anteriorment, tant per executar l'sincronització de manera manual com per automatitzar-la via Cron.

### 1. Executar la sincronització manualment
Per fer una baixada puntual de tots els episodis nous sota demanda, utilitza:
```bash
.venv/bin/rac1sync sync
```
*(Cal executar-ho des del directori arrel del projecte, per exemple `/Users/jordi/Downloads/files`)*

---

### 2. Afegir una execució automàtica (Cron)
Per afegir un programador automàtic al sistema, pots utilitzar l'script de creació i passar-li la freqüència en format Cron. Per exemple, per executar-ho cada hora (al minut 15):
```bash
./scripts/cron_management/add_cron.sh "15 * * * *"
```

---

### 3. Eliminar l'execució automàtica (Cron)
Si vols deixar de descarregar automàticament els podcasts i esborrar el cron job del sistema:
```bash
./scripts/cron_management/remove_cron.sh
```
