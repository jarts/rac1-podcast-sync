# rac1-podcast-sync

Descarrega periòdicament els podcasts de RAC1 (per exemple el *Via lliure*) al disc, sense repetits i sense fitxers a mitges.

## Per què RSS i no scraping

`https://www.rac1.cat/a-la-carta/programes/via-lliure` és una SPA: el llistat d'episodis es carrega per JavaScript, així que raspar-la voldria dir Playwright, un navegador headless i un parser que es trencarà el dia que canviïn una classe de CSS.

No cal. RAC1 publica **feeds RSS oberts** a `api.audioteca.rac1.cat`, un per secció del programa, amb els àudios allotjats a Omny Studio. Per exemple:

```
https://api.audioteca.rac1.cat/rss/via-lliure/illustres-execrables
```

Un feed RSS et dona `<guid>` per episodi — una clau natural i estable per deduplicar — i suport per a `ETag`/`Last-Modified`, que et deixa comprovar el feed cada hora sense transferir-lo si no ha canviat.

### Trobar els feeds de les altres seccions

Els slugs de cada secció no estan documentats. L'script d'ajuda els obté de l'API pública d'Apple Podcasts i te'ls imprimeix ja en format TOML:

```bash
python3 scripts/discover_feeds.py "via lliure"
```

## Instal·lació

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml   # i ajusta'l
```

## Ús

```bash
rac1sync sync              # una passada i surt  ← el mode recomanat
rac1sync daemon            # procés llarg, sincronitza cada `interval_minutes`
rac1sync list --limit 20   # què s'ha descarregat ja
rac1sync -v sync           # amb log detallat
```

Sortida: `0` si tot ha anat bé, `1` si alguna descàrrega ha fallat, `2` si la configuració és invàlida.

Els fitxers queden a `download_dir/<feed>/<AAAA-MM-DD>_<títol>.mp3`.

## Programar-ho

Prefereix `sync` + un planificador del sistema abans que `daemon`: si el procés mor, systemd el torna a executar; un dimoni mort es queda mort.

**systemd** (`~/.config/systemd/user/`):

```ini
# rac1sync.service
[Unit]
Description=Sincronitza els podcasts de RAC1

[Service]
Type=oneshot
WorkingDirectory=%h/rac1-podcast-sync
ExecStart=%h/rac1-podcast-sync/.venv/bin/rac1sync sync
```

```ini
# rac1sync.timer
[Unit]
Description=Sincronitza els podcasts de RAC1 cada hora

[Timer]
OnCalendar=hourly
RandomizedDelaySec=10m
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user enable --now rac1sync.timer
```

`Persistent=true` fa que, si l'ordinador estava apagat a l'hora prevista, l'execució es recuperi en engegar. `RandomizedDelaySec` reparteix la càrrega a l'origen.

**cron**, si ho prefereixes:

```cron
17 * * * * cd ~/rac1-podcast-sync && .venv/bin/rac1sync sync >> ~/.local/state/rac1sync.log 2>&1
```

## Arquitectura

```
cli.py          muntatge de dependències, senyals, planificació
 └─ service.py  cas d'ús: llegir feed → deduplicar → descarregar → registrar
     ├─ feed.py        adaptador RSS (feedparser + GET condicional)
     ├─ downloader.py  adaptador HTTP → disc (streaming, atòmic)
     └─ storage.py     adaptador SQLite (índex de descàrregues + estat del feed)
domain.py       objectes de valor immutables
ports.py        Protocols que inverteixen les dependències
config.py       configuració tipada i validada a l'arrencada
```

`PodcastSyncService` només coneix els `Protocol`s de `ports.py`, així que els tests no toquen ni xarxa ni disc real. És la mateixa idea d'inversió de dependències que faries amb interfícies i injecció per constructor a Spring, però amb tipatge estructural: els adaptadors no hereten res.

## Decisions que val la pena conèixer

**Deduplicació amb dues claus.** La primària és el `<guid>` del feed. La secundària és la URL de l'àudio, amb un índex únic a SQLite. Sona redundant fins que la font migra de plataforma i reassigna tots els guids: sense la segona clau et tornaries a baixar l'arxiu sencer.

**Descàrrega atòmica.** S'escriu a `<nom>.mp3.part` i es fa `os.replace` en acabar, que és atòmic dins del mateix sistema de fitxers. Al directori de destí no hi apareix mai un MP3 truncat.

**El registre s'escriu després del fitxer.** Si el procés mor a mitja descàrrega, l'episodi es tornarà a intentar; mai queda marcat com a fet sense ser-ho. L'ordre invers seria més ràpid i et deixaria forats silenciosos.

**Verificació de `Content-Length`.** Una connexió tallada et dona un fitxer que sembla bo. Comparar els bytes escrits amb la capçalera ho detecta.

**L'`ETag` només es desa si no ha fallat res.** Si el desessis havent fallat descàrregues, la comprovació següent respondria 304 i els episodis pendents quedarien amagats fins que el feed tornés a canviar.

**Reintents només del que és transitori.** 429 i 5xx amb backoff exponencial i jitter; un 404 falla de seguida, perquè reintentar-lo només afegeix latència i càrrega a l'origen.

**Concurrència acotada.** Les descàrregues van a un `ThreadPoolExecutor` (és I/O pur), però totes les escriptures a SQLite es fan al thread principal a mesura que arriben els resultats: cap connexió compartida entre threads, cap lock.

**`max_episodes_per_run`.** Posa'l a 5 la primera execució si no vols baixar tot l'arxiu històric de cop.

## Tests

```bash
pytest
ruff check . && mypy src
```

## Nota legal

Els àudios són © RAC1. Els feeds són públics i pensats per a consum amb aplicacions de podcast; això n'és una. Fes-ne un ús personal, deixa un `user_agent` identificable a la configuració i no baixis l'interval per sota d'una comprovació cada hora.
