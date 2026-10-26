# Sentieri d'autunno

Prototipo di una web app che **propone escursioni in Trentino-Alto Adige in base alle condizioni reali**,
senza contenuti caricati dagli utenti: il colore dei boschi misurato dalle immagini satellitari Sentinel-2,
la neve, il meteo del weekend, i panorami calcolati sul terreno. L'utente sceglie cosa conta per lui
(foliage, panorami, laghi, rifugi) e filtra per tempo, dislivello, difficoltà, punto di partenza.

## Come funziona

1. **Rete e sentieri**: la rete percorribile a piedi viene da Overture Maps; i sentieri numerati (SAT, CAI, AVS)
   e la loro difficoltà da OpenStreetMap.
2. **Terreno**: Copernicus DEM GLO-30 per quote, dislivelli e per l'apertura dell'orizzonte in 16 direzioni.
3. **Foliage adesso**: per ogni pixel di bosco (ESA WorldCover, frutteti e vigneti esclusi) l'ultima immagine
   Sentinel-2 limpida delle ultime settimane è confrontata con la più verde dell'estate. Conta "in colore" un
   albero virato al giallo-arancio, con colori vivi (non foschia) e foglie ancora sui rami. Neve esclusa.
4. **Escursioni**: dalle partenze (parcheggi e fermate bus vicini ai sentieri segnati) alle mete (rifugi, malghe,
   bivacchi, cime, laghi, punti panoramici), con costi di cammino DIN 33466 e preferenza per i sentieri segnati.
   Il ritorno evita l'andata: se esiste una via diversa non troppo più lunga, l'escursione è un anello.
5. **Contesto**: meteo del weekend (Open-Meteo, letto alla quota dell'escursione), webcam che inquadrano la zona
   (foto-webcam.eu), foto con licenza aperta delle mete (Wikidata e Wikimedia Commons), panorama a 360° con le
   cime visibili calcolate sulla linea di vista.
6. **Pagina web**: mappa con tasselli propri fino a zoom 12 (circa 13 m per pixel) e classifica personalizzabile.
   La pagina pubblicata non può caricare risorse da altri server, quindi tasselli, foto e dati viaggiano con lei.

## Struttura

| Passo | Script | Cosa produce |
|---|---|---|
| 1 | `fetch_overture.py` | rete, cime, laghi, rifugi, parcheggi, frutteti, confini |
| 2 | `fetch_osm_routes.py` | sentieri escursionistici OSM (dal planet su AWS) |
| 3 | `build_routes.py` | geometria dei sentieri numerati |
| 4 | `dem.py` | quote a 20 e 100 m, apertura dell'orizzonte |
| 5 | `sentinel.py` | compositi Sentinel-2: ultime settimane (20 m e colori a 10 m) ed estate |
| 6 | `foliage.py` | indice foliage |
| 7 | `score.py` | punteggi dei sentieri numerati, punti d'interesse |
| 8 | `itineraries.py` | escursioni ad anello e di andata e ritorno |
| 9 | `weather.py` | meteo del weekend |
| 10 | `photos.py` | foto delle mete con autore e licenza |
| 11 | `webcams.py` | webcam che inquadrano ogni escursione |
| 12 | `panorama.py` | orizzonte e cime visibili dal punto più panoramico |
| 13 | `tiles.py` | tasselli della mappa (rilievo, satellite, foliage, neve) |
| 14 | `export_web.py` | dati per la pagina |
| — | `web/build.py` | `web/index.html` dal sorgente `web/src/index.html` |

Aggiornamento completo (in un ambiente nuovo rigenera anche i dati stabili, circa un'ora):

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd pipeline && ../.venv/bin/python update.py
```

Un'attività programmata lo esegue ogni giovedì sera, ripubblica la pagina e aggiorna il sito della demo.

## Aprire la demo fuori da claude.ai

Il ramo `gh-pages` contiene il sito pronto, pagina e dati (circa 85 MB), creato da `web/deploy.py`.
A ogni aggiornamento il ramo viene sostituito, così i dati vecchi non si accumulano nella storia del repository.
Si può pubblicare in uno di questi modi:

- **GitHub Pages**: Settings → Pages → Build and deployment → "Deploy from a branch" → `gh-pages`, cartella `/ (root)`.
  Indirizzo: `https://alicepacc.github.io/Idea-web-app-trekking/`. Con il repository privato serve un piano
  GitHub a pagamento; il sito pubblicato è comunque visibile a tutti.
- **Cloudflare Pages** (gratuito anche con il repository privato): Workers & Pages → Create → Pages → collega
  il repository, ramo di produzione `gh-pages`, nessun framework, comando di build vuoto, cartella di output `/`.
- **Sul computer**: scarica il ramo `gh-pages` (Code → Download ZIP), estrailo e nella cartella esegui
  `python3 -m http.server`, poi apri http://localhost:8000. Aprire `index.html` con un doppio clic non funziona:
  la pagina carica i dati dalla sua cartella e il browser lo permette solo da un server.

Fuori da claude.ai la pagina non ha i limiti degli artifact: si potranno aggiungere il download delle tracce GPX,
le immagini delle webcam dal vivo e mappe di altri servizi.

## Fonti e licenze (da rispettare per il lancio pubblico)

| Dato | Fonte | Licenza e condizioni |
|---|---|---|
| Sentieri, rete, punti d'interesse | OpenStreetMap, Overture Maps | ODbL: attribuzione; i database derivati vanno condivisi con la stessa licenza |
| Immagini satellitari | Copernicus Sentinel-2 (archivio Element 84 su AWS) | libere, con attribuzione "Copernicus Sentinel data 2026" |
| Quote | Copernicus DEM GLO-30 | libera, con attribuzione |
| Copertura del suolo | ESA WorldCover 2021 | CC BY 4.0 |
| Meteo | Open-Meteo | dati CC BY 4.0; **l'API gratuita è solo per uso non commerciale**, per un servizio commerciale serve il piano a pagamento |
| Webcam | foto-webcam.eu | immagini dei rispettivi gestori: solo link, nessuna copia |
| Foto | Wikimedia Commons via Wikidata | licenza di ogni foto (spesso CC BY-SA): autore e licenza sempre visibili |

## Limiti noti

- Foliage e neve sono stime da satellite a 20 m: colori e caduta delle foglie vanno confermati sul posto.
- I pendii ripidi esposti a nord, in ombra a fine ottobre, hanno dati meno affidabili.
- La difficoltà è quella dei sentieri OSM attraversati (scala CAI); quando manca non viene stimata.
- Funivie escluse dalle partenze: gli orari stagionali non sono disponibili e in autunno molti impianti sono chiusi.
- Aperture dei rifugi, frane e chiusure dei sentieri non sono coperte.
