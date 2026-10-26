# Sentieri d'autunno

Prototipo di una web app che **suggerisce escursioni in Trentino-Alto Adige in base alle condizioni reali**,
senza contenuti caricati dagli utenti. Il primo criterio è il **foliage di questa settimana**, misurato dalle
immagini satellitari Sentinel-2; panorami, laghi e rifugi sono criteri già attivi e regolabili dall'utente.

## Come funziona

1. **Sentieri**: i percorsi escursionistici numerati (SAT, CAI, AVS…) sono presi da OpenStreetMap.
   La geometria arriva dalla rete di Overture Maps, che conserva gli ID delle vie OSM.
2. **Terreno**: il modello altimetrico Copernicus GLO-30 dà quote, dislivelli e tempi di percorrenza
   (regola DIN 33466 usata dai club alpini). Per ogni cella di 100 m si calcola quanto è aperto l'orizzonte
   in 16 direzioni: è la base del punteggio *panorami*.
3. **Foliage adesso**: per ogni pixel di bosco (ESA WorldCover) si confronta l'ultima osservazione senza
   nuvole delle ultime settimane con la più verde dell'estate. Un albero che ha perso clorofilla e ha virato
   verso il giallo-arancio, ma ha ancora le foglie, conta come "in colore". La neve viene esclusa.
4. **Punteggi**: ogni percorso è campionato ogni 20 m. Ne escono punteggi 0-100 per foliage, panorami,
   laghi e rifugi, più filtri (lunghezza, dislivello, difficoltà CAI, famiglie, accesso, neve).
5. **Pagina web**: mappa e classifica personalizzabile con i pesi scelti dall'utente. Il fondo cartografico
   è generato dai dati stessi (rilievo, copertura del suolo, laghi), perché la pagina pubblicata non può
   caricare mappe da server esterni.

## Fonti (tutte aperte, su AWS, senza chiavi API)

| Dato | Fonte | Licenza |
|---|---|---|
| Sentieri e percorsi | OpenStreetMap (planet ORC su AWS) | ODbL |
| Rete, cime, laghi, rifugi, confini | Overture Maps | ODbL / CDLA |
| Immagini satellitari | Sentinel-2 L2A (Copernicus), archivio Element 84 | Copernicus open |
| Quote | Copernicus DEM GLO-30 | Copernicus open |
| Copertura del suolo | ESA WorldCover 2021 | CC BY 4.0 |

## Rigenerare i dati

Servono Python 3.11+ e circa 6 GB di disco temporaneo.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd pipeline
../.venv/bin/python fetch_overture.py     # rete, cime, laghi, rifugi, confini
../.venv/bin/python fetch_osm_routes.py   # percorsi escursionistici OSM
../.venv/bin/python build_routes.py       # geometria dei percorsi
../.venv/bin/python dem.py                # quote e apertura dell'orizzonte
../.venv/bin/python sentinel.py           # immagini Sentinel-2 (ultime settimane + estate)
../.venv/bin/python foliage.py            # indice foliage
../.venv/bin/python score.py              # metriche e punteggi per percorso
../.venv/bin/python export_web.py         # dati e immagini per web/
```

Per aggiornare il foliage basta rilanciare `sentinel.py`, `foliage.py`, `score.py` ed `export_web.py`
(con `--end AAAA-MM-GG` si sceglie la data di riferimento).

## Limiti noti

- Il foliage è una stima da satellite a 20 m: colori e caduta delle foglie vanno confermati sul posto.
- I pendii molto ripidi esposti a nord, in ombra a fine ottobre, hanno dati meno affidabili.
- La difficoltà è quella indicata in OSM (scala CAI); quando manca non viene stimata.
- I percorsi sono quelli numerati in OSM: il prossimo passo è comporre anelli e andata-ritorno dai parcheggi.
- Aperture dei rifugi, frane e chiusure dei sentieri non sono coperte.
