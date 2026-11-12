# Prompt per Claude Code — PWA di pianificazione escursioni in montagna

> **Come usarlo:** crea un repository vuoto, salva questo file come `docs/SPEC.md` e avvia Claude Code con:
> *"Leggi docs/SPEC.md e inizia dalla Fase 0. Lavora una fase alla volta e fermati ai checkpoint indicati."*
> Tutto ciò che segue è rivolto a Claude Code.

---

## 0. Modalità di lavoro (modello: Claude Fable 5.2)

Stai girando su un modello di fascia alta: sfruttalo. Questo progetto non è un CRUD con una mappa sopra.

- **Lavora in autonomia su orizzonti lunghi.** Dentro ogni fase prendi le decisioni tecniche da solo, documentale in `docs/decisions/` (ADR brevi) e fermati solo ai checkpoint dichiarati o davanti a scelte irreversibili o costose.
- **Usa i subagenti in parallelo** per le attività indipendenti: ricognizione delle fonti, sviluppo dei parser, frontend, test. Unisci i risultati e tieni coerenti interfacce e schema dati.
- **Verifica ciò che costruisci, non limitarti a scriverlo.** Esegui test e pipeline su dati reali. Per la UI fai screenshot con Playwright e guardali. Per le tracce ricostruite renderizza la mappa e confrontala con la descrizione e con la mappa della locandina. Una funzionalità è "fatta" solo quando l'hai vista funzionare.
- **Costruisci harness di valutazione**, non solo test unitari: dataset annotati (locandine → campi attesi, attività → traccia attesa), metriche, report ripetibili. Ogni modifica a parser o prompt di estrazione va rieseguita contro l'harness.
- **Usa la multimodalità.** Leggi le pagine PDF anche come immagini, non solo come testo: layout, tabelle, mappe e loghi delle sezioni contengono informazione.
- **Niente scorciatoie silenziose.** Se un dato manca o una fonte non risponde, dillo nel report invece di inventare valori plausibili.

---

## 1. Ruolo e obiettivo

Sei il lead engineer di una **Progressive Web App mobile-first** (nome provvisorio: **Cordata**) per programmare escursioni in montagna. L'app deve permettere di:

1. **Scegliere** un itinerario da un elenco di proposte filtrabili (distanza, tempo, dislivello, difficoltà, quota, tipo di percorso, stagione, distanza da me…).
2. **Pianificare** un percorso proprio sulla mappa, agganciato ai sentieri reali, con statistiche calcolate in tempo reale.
3. **Organizzare un'uscita di gruppo**: chi pianifica un percorso lo pubblica per una data ("sabato faccio questo giro, vieni?") e invita altre persone a iscriversi.
4. Consultare **meteo in quota e bollettino valanghe** per la data scelta, lavorare **offline** ed **esportare GPX**.

Il tratto distintivo: le proposte non sono i singoli sentieri numerati del catasto (332, 360, 326…), ma **attività vere e proprie** come le gite sociali delle sezioni CAI: anelli e traversate che combinano più sentieri, con partenza, punti salienti, tempi, dislivello, difficoltà e attrezzatura. Si usano **solo dati aperti** (OpenStreetMap e altre fonti con licenza compatibile).

Lingua dell'interfaccia: **italiano**, con struttura i18n pronta per tedesco e inglese (l'area pilota è bilingue: usa `name:it` / `name:de` di OSM).

Area pilota: **Trentino-Alto Adige + area dolomitica/veneta limitrofa** (bbox configurabile). Architettura pensata per estendersi a tutta Italia.

---

## 2. Esempio di riferimento (fixture "golden")

L'utente ha fornito una locandina CAI che rappresenta esattamente il tipo di "attività" da trattare. Usala come **caso di test #0** in tutta la pipeline (estrazione, ricostruzione traccia, calcolo tempi, scheda UI).

```json
{
  "titolo": "Anello in Val d'Inferno e Cima Nassere (2253 m)",
  "gruppo_montuoso": "Catena del Lagorai",
  "data": "2026-11-15",
  "organizzatori": ["CAI Sezione di Merano", "CAI Sezione di Mestre", "Commissione Giovani CAI Alto Adige"],
  "tipo_percorso": "anello",
  "difficolta_cai": "EE",
  "durata_dichiarata": "7:00",
  "distanza_km": 15.5,
  "dislivello_salita_m": 950,
  "dislivello_discesa_m": 950,
  "quota_max_m": 2253,
  "partenza": { "nome": "Parcheggio Tedon", "quota_m": 1350, "comune": "Scurelle (TN)", "ora": "09:00" },
  "sentieri_citati": ["332", "360", "326"],
  "reti_citate": ["Alta Via del Granito", "Sentiero della Pace", "Translagorai", "Sentiero Italia CAI"],
  "tratti_fuori_sentiero": ["traccia dal Lago Nassere a Cima Nassere"],
  "punti_chiave_in_ordine": [
    "Parcheggio Tedon (1350)", "strada forestale Campivelo", "Piana Caldenave (1752)",
    "Rifugio Claudio e Renzo", "Laghi della Val d'Inferno (1950)", "Lago Nassere",
    "Cima Nassere (2253)", "Laghetto del Cengello (1985)", "Cucuzzolo dei Morti (1924)",
    "Malga Conseria (1848)", "Ponte Conseria (1460)", "Parcheggio Tedon"
  ],
  "mezzi": "mezzi propri/condivisi",
  "attrezzatura": ["abbigliamento caldo", "calzature da montagna", "ramponcini consigliati"],
  "note": ["pranzo al sacco", "rifugio chiuso per la stagione", "riservata CAI Giovani 18-35 anni"],
  "cartografia": "Carta Tabacco 058",
  "iscrizione": { "scadenza": "2026-11-13T18:30", "quota_eur": 5, "url_modulo": "<link al modulo della sezione>" }
}
```

Risultati attesi: traccia ricostruita entro ±10% sulla distanza e ±15% sul dislivello; la formula DIN 33466 applicata a 15,5 km e ±950 m restituisce **7:00 h**, identico al dato dichiarato (vedi §8): usalo come test unitario.

Nota privacy: la locandina contiene nome e telefono del capo gita. **Non estrarre né memorizzare dati personali**: mostra solo il link alla locandina/pagina originale.

---

## 3. Principi non negoziabili

- **Solo dati aperti** e servizi senza lock-in. Niente Google Maps, niente contenuti di piattaforme commerciali (Komoot, Outdooractive, AllTrails, Wikiloc): il loro contenuto è proprietario, si possono solo studiare come riferimento UX.
- **Licenze e attribuzioni**: pagina "Fonti e licenze" sempre raggiungibile + attribuzione sulla mappa. ODbL per OSM (© OpenStreetMap contributors) e SAT; CC BY 4.0 per Open-Meteo con citazione Copernicus; licenza per record per Open Data Hub (usa solo record con licenza aperta, leggi `LicenseInfo`). Tieni un registro `docs/data-sources.md` con fonte, licenza, uso, frequenza di aggiornamento, stato.
- **Rispetto dei server altrui**: User-Agent identificativo con contatto, cache aggressiva, rate limit, backoff su 429, `robots.txt` rispettato. Mai chiamare Overpass o servizi pubblici gratuiti direttamente dai client: passa dal backend o da dati pre-calcolati.
- **Dati CAI: uso autorizzato con citazione dei crediti.** Il CAI ha confermato all'utente che i suoi dati si possono scaricare e usare **citando i crediti**. Quindi: attribuzione visibile su ogni scheda che usa dati CAI (es. "Dati sentieri: Club Alpino Italiano – SOSEC / OSM2CAI"; "Attività: CAI Sezione di …" con link alla fonte) e nella pagina Fonti e licenze. Chiedi all'utente di salvare la conferma scritta del CAI in `docs/legal/` e riportane gli estremi in `docs/data-sources.md`. Anche con l'autorizzazione, i dati personali delle locandine (nomi e telefoni dei capigita, email) **non si memorizzano**.
- **Iscrizioni ufficiali**: l'app **non gestisce le iscrizioni alle gite CAI**; rimanda al modulo o alla sede della sezione. Le "uscite di gruppo" create dagli utenti sono **uscite private**, non attività CAI: mostra un disclaimer chiaro (niente copertura assicurativa CAI, responsabilità individuale).
- **Sicurezza**: tempi e difficoltà sono indicativi; avvisi contestuali (non modali) quando un percorso supera il livello impostato dall'utente, supera lo zero termico, attraversa pendii valanghivi in inverno, ecc. Numero di emergenza 112 e link a GeoResQ nella scheda.
- **Privacy/GDPR**: dati minimi, niente tracciamento di terze parti, posizione solo on-device salvo consenso, cancellazione account self-service.

---

## 4. Utenti e flussi principali

| Persona | Bisogno | Flusso chiave |
|---|---|---|
| Escursionista | "Cosa faccio domenica a max 1h da Bolzano, 5-6 h, E/EE?" | Esplora → filtri → scheda → meteo → salva/esporta GPX |
| Pianificatore | "Voglio unire il 332 e il 360 e tornare dal 326" | Pianifica → tap sui punti → statistiche → salva |
| Organizzatore | "Sabato faccio il giro della Val d'Inferno, chi viene?" | Scheda/piano → *Organizza uscita* → data, ritrovo, posti → condividi link |
| Invitato | Riceve un link su WhatsApp | Pagina uscita (senza installare l'app) → *Partecipo* → promemoria e aggiornamenti |
| Curatore (admin) | Validare le attività estratte dalle sezioni | Coda revisione → PDF accanto ai dati estratti + traccia → approva/correggi |

---

## 5. Funzionalità

### 5.1 Esplora (proposte)
- Vista **mappa + lista** sincronizzate. Pulsante "Cerca in quest'area" dopo lo spostamento della mappa.
- Ricerca testuale (luoghi, cime, rifugi, valli, nomi itinerari) + geocoding locale sui POI OSM, con Nominatim self-hosted o rispettandone la policy.
- **Filtri**: distanza (km), durata stimata, dislivello positivo, quota massima, difficoltà CAI (T, E, EE, EEA; EAI per le ciaspolate), tipo (anello / andata-ritorno / traversata), distanza dalla mia posizione o da una località, stagione consigliata, presenza di rifugi aperti o punti acqua, raggiungibile con mezzi pubblici (fase successiva), fonte (attività CAI, archivio CAI, itinerari Open Data Hub, rete OSM), solo attività future con data.
- **Preset rapidi** in chip: "Mezza giornata", "Giornata intera", "Facile con bambini", "Panoramico", "Laghi", "Cima".
- Ordinamento: rilevanza, distanza da me, durata, dislivello, data (per le attività future).
- Card risultato con dati in **ordine fisso**: durata · km · D+ · badge difficoltà · mini-profilo altimetrico · icona anello/traversata · fonte ("CAI Merano, 15/11/2026").

### 5.2 Scheda itinerario (ispirata alla locandina)
- Header con le 4 metriche chiave (tempo, difficoltà, km, D+/D−) come nella locandina CAI.
- Mappa con traccia colorata per pendenza o per sentiero (con segnavia `osmc:symbol` e numero), punti chiave numerati.
- **Profilo altimetrico interattivo**: scorrendolo, un marker si muove sulla mappa; waypoint sul profilo; tratti ripidi evidenziati.
- Elenco sentieri usati (numero, nome, `cai_scale`, rete/alta via), tappe con quota e tempo parziale cumulativo.
- Descrizione sintetica, attrezzatura consigliata, periodo consigliato, note (rifugio chiuso, ecc.), cartografia di riferimento.
- Blocco **Condizioni** per la data scelta: meteo a partenza e alla quota massima, zero termico, vento, rischio temporali; bollettino valanghe nella stagione invernale.
- **Come arrivare**: coordinate del parcheggio, link di navigazione verso app esterne, mezzi pubblici (fase successiva).
- Azioni: Salva · Esporta GPX/KML · Modifica nel planner · **Organizza uscita** · Condividi.
- Per le attività CAI: badge sezione, data, link "Iscriviti sul sito della sezione", link alla locandina originale.

### 5.3 Pianifica (planner)
- Tap sulla mappa per aggiungere punti; il percorso si **aggancia ai sentieri** tramite il motore di routing escursionistico. Trascina per spostare, tocca la linea per inserire un punto intermedio, undo/redo, "chiudi ad anello", "inverti", "andata e ritorno".
- Selettore **difficoltà massima ammessa** (default E; EE solo se scelto): il routing evita i tratti oltre la soglia; segmento "a mano libera" per le tracce fuori sentiero, segnalato come tale.
- **Barra statistiche sempre visibile**: km, D+, D−, quota max, tempo stimato (personalizzabile), difficoltà massima incontrata.
- Avvisi inline: tratti oltre la difficoltà scelta, tratti senza segnavia o a bassa `trail_visibility`, quota sopra lo zero termico previsto, rifugi chiusi.
- Import GPX (traccia propria) → snap opzionale ai sentieri.
- Lo stato del piano è serializzato nell'URL (link condivisibile, il tasto Indietro funziona come undo) e salvato localmente.

### 5.4 Uscite di gruppo e inviti
- Da una scheda o da un piano: **"Organizza un'uscita"** → data, ora e luogo di ritrovo (pin sulla mappa), mezzi (auto condivise/mezzi pubblici), posti massimi, termine iscrizione, difficoltà/requisiti, attrezzatura obbligatoria e consigliata (checklist), note.
- Visibilità: **solo con link** (default), **contatti** (persone già invitate in passato), **pubblica** (compare nel feed "Uscite questo weekend vicino a te").
- **Pagina invito** leggera, apribile senza installare l'app né creare un account per la sola lettura; pulsante grande **Partecipo** → login leggero (magic link email o passkey) → stato: confermato / lista d'attesa / rinunciato.
- L'organizzatore vede i partecipanti, può confermare, spostare in lista d'attesa, **annullare o rinviare** (es. per meteo): tutti ricevono notifica.
- Notifiche web push (VAPID; su iOS richiedono la PWA installata) + promemoria il giorno prima con meteo aggiornato; fallback email.
- Condivisione nativa (Web Share API) con anteprima Open Graph (mappa statica + metriche); QR code.
- Bacheca annunci dell'uscita (solo organizzatore → partecipanti) e commenti semplici; niente chat completa nella v1.
- Opzionale: coordinamento auto condivise (chi offre posti, chi cerca).
- Disclaimer obbligatorio sulla pagina: uscita privata tra persone, non organizzata dal CAI.

### 5.5 Meteo e valanghe
- **Open-Meteo** (gratuito per uso non commerciale, CC BY 4.0, senza chiave): previsione oraria per 2-3 punti della traccia (partenza, quota massima, punto intermedio), passando il parametro `elevation` per il downscaling; variabili: temperatura, precipitazione, `freezing_level_height`, vento e raffiche, nuvolosità, CAPE e codice meteo per i temporali. Cache lato server per cella e ora.
- **Bollettino valanghe Euregio** (Alto Adige, Trentino, Tirolo): JSON CAAMLv6 giornaliero pubblicato da avalanche.report (es. `https://static.avalanche.report/bulletins/{YYYY-MM-DD}/{YYYY-MM-DD}_EUREGIO_it_CAAMLv6.json`; verifica il pattern esatto e la lingua `it` nella Fase 0). Intersechi la traccia con i poligoni delle micro-regioni EAWS (repository `eaws-regions` su GitLab) e mostri grado di pericolo per fascia di quota ed esposizione, problemi valanghivi, tendenza. Fuori dall'Euregio: link ad avalanches.org.
- Indicatore di sintesi "Condizioni" (verde/giallo/rosso) **sempre con spiegazione testuale**, mai solo colore.

### 5.6 Offline ed export
- Service worker (Workbox): app shell, schede salvate, tracce e dati meteo dell'ultima consultazione.
- **Pacchetti mappa offline** per area/itinerario: estratto PMTiles della basemap + sentieri + rilievo, salvato in OPFS (vedi plugin `@makina-corpus/maplibre-offline-pmtiles`), con gestione dello spazio e della quota del browser.
- Posizione GPS live sulla mappa offline con indicazione "sei a X km dalla traccia".
- Export **GPX 1.1** (track + waypoint con nomi e quote), KML, GeoJSON; import GPX.

### 5.7 Funzioni avanzate (il valore aggiunto del progetto)

**a) Generatore di proposte "stile sezione CAI".** Oltre a raccogliere le gite esistenti, l'app **compone nuovi itinerari** come farebbe un accompagnatore:
- Input: punto di partenza (o "raggiungibile entro X minuti da Bolzano"), durata, D+, difficoltà massima, tipo (anello/traversata), stagione, desideri ("un lago", "una cima", "rifugio aperto per pranzo").
- Ricerca sul grafo dei sentieri validati (OSM2CAI `sda` 4 + rete OSM) di anelli e traversate che rispettano i vincoli, con funzione di qualità esplicita: percentuale su sentiero segnato vs strada forestale/asfalto, varietà (no andata e ritorno sulla stessa traccia negli anelli), punti d'interesse (cime, laghi, rifugi, punti panoramici), esposizione al sole per la stagione, assenza di `issues` aperte, parcheggio o fermata alla partenza.
- Output in formato **locandina**: titolo evocativo, descrizione generata dal modello *a partire solo dai dati* (nomi dei luoghi, quote, sentieri, rifugi), tappe con tempi, difficoltà, attrezzatura consigliata per stagione e quota, etichetta chiara "proposta generata, non verificata sul terreno".
- Valutazione: confronta le proposte generate con le gite reali delle sezioni nella stessa zona (quanto spesso il generatore "riscopre" le gite reali). Inseriscilo nell'harness.

**b) Ricerca in linguaggio naturale.** Una barra "Descrivi la gita che vuoi" ("anello di 5-6 ore con un lago, partenza raggiungibile in treno da Bolzano, niente EE") viene tradotta dal modello in filtri strutturati **visibili e modificabili** come chip; mai una risposta opaca.

**c) Briefing pre-uscita.** Per una data scelta, il modello compone un briefing sintetico che incrocia meteo per quota, zero termico, bollettino valanghe, ore di luce, stato dei sentieri (`issues` OSM2CAI), apertura dei rifugi, e conclude con: cosa portare, a che ora partire per rientrare con margine prima del tramonto, punti di rinuncia lungo il percorso. Ogni affermazione deve essere legata al dato da cui deriva (niente consigli generici inventati).

**d) Verifica multimodale delle tracce.** Nella pipeline delle gite, dopo la ricostruzione: renderizza la traccia su mappa e confrontala con la descrizione e, se la locandina contiene una mappa o un profilo altimetrico, con quell'immagine. Il modello assegna un giudizio motivato che entra nel punteggio di corrispondenza.

---

## 6. Fonti dati (da verificare tutte nella Fase 0)

| Fonte | Cosa fornisce | Accesso | Licenza | Uso |
|---|---|---|---|---|
| **OpenStreetMap** via estratto Geofabrik (Nord-Est Italia + Tirolo) | Relazioni `route=hiking` (`ref`, `name`, `cai_scale`, `ref:REI`, `osmc:symbol`, `from`/`to`, `ascent`, `duration:*`, `network`, `operator`), sentieri `highway=path/track` con `sac_scale` e `trail_visibility`, POI (rifugi `tourism=alpine_hut`, bivacchi, cime `natural=peak`, selle, laghi, sorgenti e acqua potabile, parcheggi, punti panoramici) | Download `.osm.pbf` → osmium/osm2pgsql → PostGIS, aggiornamento settimanale | ODbL | Base di tutto: grafo, sentieri, POI, ricostruzione tracce |
| **Overpass API** | Query puntuali | Solo sviluppo/debug, mai dai client | ODbL | Limiti: poche migliaia di query/giorno per progetto ricorrente, header User-Agent; in produzione usa l'estratto locale |
| **Waymarked Trails** | GPX di relazioni ordinate (`https://hiking.waymarkedtrails.org/api/v1/details/relation/{id}/geometry/gpx`) e overlay sentieri | API pubblica, uso moderato | ODbL | Verifica incrociata dell'ordinamento delle relazioni; ispirazione |
| **OSM2CAI – catasto sentieri CAI** (SOSEC), `https://osm2cai.cai.it` | Sentieri CAI con stato di accatastamento (`sda`), validazione, metadati e geometria; POI nel raggio di 1 km da un sentiero; settori CAI; itinerari | **API pubblica verificata** (v1, v2, v3; documentazione su `/api/documentation`), vedi §6.1 | Uso consentito con citazione dei crediti CAI (§3) | **Fonte primaria** per i sentieri CAI: metadati, difficoltà, tempi ufficiali |
| **SAT – sentieri del Trentino** | Tracciati SAT (shapefile/GPX/KML) con numerazione E/O | Download dal sito SAT | ODbL | Confronto e arricchimento in Trentino (precisione non da rilievo GPS: non sovrascrivere OSM) |
| **Open Data Hub Alto Adige** (NOI Techpark) | Itinerari escursionistici turistici con GPX, POI, webcam, mobilità | `https://tourism.api.opendatahub.com/v1/ODHActivityPoi?tagfilter=hiking` + Geo API | Per record (molti CC0) | Proposte aggiuntive, webcam vicino alla traccia |
| **Sezioni CAI** (siti `organizzazione.cai.it/sez-*` e siti propri) | Calendari annuali PDF e locandine delle singole gite | Crawler (vedi §7) | Contenuti delle sezioni | Attività future + archivio storico |
| **Mapterhorn** (Copernicus GLO-30 + DEM nazionali) | Tile del terreno in codifica Terrarium (PMTiles) | Self-host o CDN | Vedi pagina attribuzioni Mapterhorn | Hillshade, 3D, profilo altimetrico lato client |
| **Open-Meteo Elevation API** | Quota da DEM Copernicus 90 m | API, non commerciale | CC BY 4.0 | Fallback per le quote |
| **DTM provinciali** (Alto Adige, Trentino) | Modelli digitali del terreno ad alta risoluzione | Geoportali provinciali (verificare licenza) | Da verificare | Precisione maggiore del dislivello nell'area pilota |
| **Open-Meteo Forecast** | Meteo orario, zero termico | API | CC BY 4.0 | §5.5 |
| **avalanche.report (ALBINA)** + **EAWS regions** | Bollettino CAAMLv6 + poligoni micro-regioni | JSON statici | Verificare | §5.5 |
| **GTFS** Alto Adige (STA/Open Data Hub) e Trentino Trasporti | Fermate e orari | Download GTFS (verificare disponibilità e licenza) | Da verificare | Filtro "raggiungibile con i mezzi" (fase successiva) |
| **Basemap** Protomaps (PMTiles da OSM) o OpenFreeMap | Mappa vettoriale | Self-host PMTiles su object storage | ODbL | Mappa base con stile "topo" personalizzato |

Per ogni fonte, nella Fase 0 produci uno script di prova che scarica un campione dell'area Lagorai e registra in `docs/data-sources.md`: raggiungibile sì/no, formato, licenza letta, limiti, esempi.

### 6.1 API OSM2CAI: cosa è già stato verificato (12/11/2026)

- **Endpoint di elenco** (`/api/v2/hiking-routes/bb/{bbox}/{sda}`, `/api/v2/hiking-routes/region/{region_code}/{sda}`, versioni `-osm` e `-collection`): restituiscono un oggetto `{ id: updated_at }`, senza geometria né nomi. Esempio: la bbox `11.40,46.05,11.62,46.16` (Lagorai) con `sda=4` ha restituito 18 sentieri. **Questo formato è fatto apposta per la sincronizzazione incrementale**: scarica l'indice, confronta gli `updated_at` con quelli salvati e scarica solo i dettagli cambiati.
- **Dettaglio** `/api/v2/hiking-route/{id}`: GeoJSON con geometria MultiLineString e proprietà. Esempio reale (id 12709, sentiero 366B "Cresta del Frate", Lagorai): `relation_id`/`osm_id`, `ref`, `name`, `cai_scale` (EE), `from`, `to`, `distance` (km), `ascent`, `descent`, `ele_from`, `ele_to`, `ele_min`, `ele_max`, `duration_forward`/`duration_backward` (minuti), `network`, `source`, `sda` (4), `validation_date`, `issues_status`/`issues_description`/`issues_last_update` (stato di percorribilità), `taxonomy_where` (comuni), `itinerary`, `public_page`.
- `sda` è lo stato di accatastamento: 4 corrisponde ai percorsi validati, con `validation_date` valorizzata (confermare la legenda completa 0-4 nella documentazione).
- Altri endpoint utili: `/api/v2/ecpois/{hr_osm2cai_id}/{type}` (POI entro 1 km da un sentiero), `/api/v2/ecpois/bb/{bbox}/{type}`, `/api/v2/hiking-route-tdh/{id}`, `/api/v2/itinerary/list` (32 itinerari, formato `{id: updated_at}`), `/api/v3/sectors/list` e `/api/v3/sectors/{id}`, `/api/v2/source_survey/monitorings`. Il dettaglio di un itinerario (`/api/v2/itinerary/{id}`) ha risposto con errore durante la verifica: indaga sulla documentazione l'endpoint corretto.

Come usarla:
1. Sincronizza **tutti i sentieri con `sda` 3-4** dell'area pilota (poi d'Italia) nel database, con job notturno incrementale e richieste sequenziali con pausa.
2. Usa `cai_scale`, `duration_forward`/`backward`, `ascent`/`descent` **ufficiali** del catasto come verità di riferimento per i tratti di sentiero; calcola con il DEM solo ciò che il catasto non dà, e segnala le discrepanze forti.
3. Mostra `issues_status` / `issues_description` nella scheda e nel planner come **avviso di percorribilità** (sentiero interrotto, frana…), con la data dell'ultimo aggiornamento.
4. Il grafo di routing resta quello dell'estratto OSM completo (servono anche strade forestali e sentieri non CAI): collega le way OSM ai sentieri OSM2CAI tramite `relation_id`.
5. Gli ID OSM2CAI sono l'aggancio per la ricostruzione delle tracce delle gite (§7.4): sentiero "332" vicino alla partenza → id OSM2CAI → geometria validata.

---

## 7. Pipeline "attività CAI" con PILOTA DI 20 ATTIVITÀ

L'utente vuole vedere una **prova su 20 attività** prima di caricare tutto. Questa sezione è vincolante.

### 7.1 Discovery
- File di configurazione `config/sources/cai_sections.yaml` con l'elenco delle sezioni (inizia da CAI Alto Adige: Bolzano, Merano, Bressanone, Brunico, Vipiteno, Appiano…; SAT; alcune sezioni venete come Mestre), con URL base, tipo di sito e note.
- Molte sezioni di tutta Italia sono su `organizzazione.cai.it/sez-<nome>/`, una installazione WordPress multisite (osservate: Bolzano, Appiano, Livorno, Brescia, Terni, Veduggio, Domegge di Cadore…). Altre hanno siti propri con CMS diversi (es. `caibergamo.it` su Drupal, `seniores.caiverona.it` con PDF in cartelle statiche): servono adapter dedicati.
- **Verificato il 12/11/2026: l'API REST WordPress è attiva.** `GET https://organizzazione.cai.it/sez-bolzano-caiaa/wp-json/wp/v2/media?mime_type=application/pdf&per_page=100&page=N` restituisce l'elenco dei PDF con `title`, `date`, `source_url` (stesso risultato su `sez-appiano-caiaa`). Strategia:
  1. API REST WordPress (`/wp-json/wp/v2/media`, `/posts`, `/pages`, eventuali custom post type), con paginazione (header `X-WP-Total`/`X-WP-TotalPages`) e filtro `after=` per l'incrementale.
  2. Sitemap XML (`/wp-sitemap.xml`) come fallback.
  3. Crawling limitato delle pagine "Escursionismo / Programma / Gite" per i siti non WordPress.
- **Il rumore è alto, serve un classificatore.** Tra i PDF della sezione di Bolzano ci sono locandine di gite (es. `30_20260927 – Prati di Croda Rossa`, `32_20261011 – Val di Cembra Tappa 2`, `2026-10-04 Locandina Monte Pic`, `2026_09_20 – Müstair – Glorenza`) ma anche moduli di iscrizione, informative GDPR, corsi, comunicati stampa, una castagnata sociale e cicloescursioni (`29a_20260919_Cicloescursione Monte Corno`). Appiano pubblica soprattutto un **libretto annuale** (`PROGRAMMA-ATTIVITA-CAI-APPIANO-2026.pdf`) invece delle singole locandine. I nomi file seguono convenzioni diverse per sezione (`NN_YYYYMMDD-Titolo`, `YYYY_MM_DD-Titolo`, `YYYY-MM-DD Locandina Titolo`): usali come segnale debole insieme al contenuto.
- **Layout eterogenei**: esempio reale `07_20260412-Castel-Coira.pdf` (CAI Bolzano): difficoltà E, dislivello ±238 m, 2 h di cammino, partenza e arrivo Castel Coira (921 m), quota massima Maso Vernalhof (1159 m), sentieri 17 e 18B, trasporto in pullman, **distanza in km non indicata**. I campi mancanti si calcolano dalla traccia ricostruita, marcandoli come "calcolati".
- Per i **calendari annuali** (un PDF con molte gite in tabella: data, destinazione, dislivello, ore, difficoltà, accompagnatori), estrai le righe come attività "leggere" da collegare poi alla locandina dettagliata quando appare.

### 7.2 Fetch e archivio grezzo
- Scarica rispettando `robots.txt`, max 1 richiesta ogni pochi secondi per host, User-Agent con contatto, `If-Modified-Since`/ETag.
- Salva il documento grezzo (object storage) con hash SHA-256, URL, data di fetch: serve per la riproducibilità e per evitare di riscaricare.

### 7.3 Classificazione ed estrazione
- Estrai testo e layout (PyMuPDF/pdfplumber; OCR con Tesseract solo se il PDF è un'immagine).
- Classifica: gita escursionistica / ciaspolata (EAI) / cicloescursionismo / alpinismo / corso / serata culturale / altro. Nella v1 importa solo **escursionismo ed EAI**; il resto viene scartato con motivazione.
- Estrazione strutturata con LLM (Claude API, output vincolato a JSON Schema) secondo lo schema del §2, con un campo `confidence` per ciascun campo e la **citazione del testo sorgente** da cui proviene ogni valore. Validazione con Zod/Pydantic. Rimuovi i dati personali (nomi e telefoni dei capigita, email) prima di salvare.

### 7.4 Ricostruzione della traccia (il cuore del problema)
1. **Geocodifica della partenza**: cerca nei POI OSM locali (parcheggi, località, comuni) il nome della partenza, filtrando per comune/provincia; quota dichiarata come vincolo di verifica.
2. **Raccolta candidati**: sentieri OSM2CAI (e relazioni OSM) con `ref` uguale ai numeri citati (in Trentino anche con prefisso SAT `E`/`O`, es. `E332`) entro ~25 km dalla partenza, preferendo quelli con `sda` 4; POI con i nomi dei punti chiave (rifugi, laghi, cime, malghe, ponti), con fuzzy matching italiano/tedesco.
3. **Sequenza**: ordina i punti chiave nell'ordine della descrizione; costruisci i waypoint.
4. **Routing vincolato**: instrada tra i waypoint privilegiando fortemente le way che appartengono alle relazioni citate (penalità alta fuori da esse); consenti tratti brevi fuori rete per "sentiero su traccia".
5. **Verifica**: confronta distanza, D+, D− e quota massima calcolati con quelli dichiarati. Punteggio di corrispondenza e stato: `auto_ok` (entro ±10% km e ±15% D+, quota max ±50 m), `da_rivedere`, `fallita` (con motivo: partenza non trovata, sentiero non in OSM, ordine ambiguo…).
6. Per le attività "anello", verifica che inizio e fine coincidano.

### 7.5 Deduplica e archivio
- Lo stesso itinerario ripetuto negli anni o da sezioni diverse viene raggruppato in un **itinerario canonico** (somiglianza della traccia: distanza di Fréchet/Hausdorff su geometria semplificata + punti chiave).
- Le attività passate diventano **proposte d'archivio** ("proposta da CAI Merano il 15/11/2026"), riutilizzabili da tutti; le future restano anche come eventi con data.

### 7.6 Il pilota (CHECKPOINT OBBLIGATORIO)
- Seleziona **20 attività** reali da **almeno 4 sezioni** diverse, con un mix: almeno 6 anelli, 4 traversate, 4 andata/ritorno, almeno 3 EE, 2 EAI/ciaspolate, 2 da calendari annuali, 2 PDF "difficili" (scansioni o layout complessi). Includi la fixture Val d'Inferno come #0 (fuori conteggio).
- Produci `reports/pilot-YYYY-MM-DD/`:
  - `pilot.html` navigabile: per ogni attività il PDF originale, i campi estratti con la confidence, la traccia su mappa, il profilo, il confronto dichiarato/calcolato e lo stato;
  - `metrics.md`: precisione per campo (verificata a mano da te sul PDF), tasso di tracce `auto_ok`, cause di fallimento più frequenti, tempo e costo LLM per attività, stima di costo e tempo per l'ingestion completa;
  - `decisions.md`: problemi trovati e proposte di correzione.
- **FERMATI QUI.** Presenta il report all'utente e attendi approvazione esplicita prima di estendere l'ingestion a tutte le sezioni. Non schedulare crawler periodici prima dell'approvazione.

### 7.7 Revisione (admin)
- Coda delle attività `da_rivedere`: PDF a sinistra, campi modificabili a destra, mappa con traccia editabile (stesso componente del planner), pulsanti Approva / Correggi e approva / Scarta.

---

## 8. Calcolo di tempi, dislivello e difficoltà

- **Dislivello**: ricampiona la traccia ogni 10-20 m, interroga il DEM e applica uno smoothing con soglia di isteresi (5-10 m) per non gonfiare il D+ con il rumore del DEM. Rendi i parametri configurabili e calibrali sulla fixture e sul pilota.
- **Tempo, due modelli selezionabili**:
  1. **DIN 33466** (usata da DAV/AVS): 300 m/h in salita, 500 m/h in discesa, 4 km/h in orizzontale; tempo = maggiore tra tempo orizzontale e tempo verticale + metà del minore. Test: 15,5 km e ±950 m → 7:00 h (fixture).
  2. **Modello CAI / Sentieri Svizzeri** basato sulla pendenza, calcolato per segmenti: recupera la formula esatta dalle pubblicazioni CAI (es. "Sentieri – Quaderno n. 1", capitolo sul calcolo dei tempi di percorrenza; una copia è pubblicata dalla sezione di Appiano: `https://organizzazione.cai.it/sez-appiano-caiaa/wp-content/uploads/sites/152/2026/02/SENTIERI-QUADERNO-NR.-1-CCE.pdf`). Calibra anche contro i `duration_forward`/`duration_backward` ufficiali di OSM2CAI su centinaia di sentieri validati e riporta l'errore medio e verificala con questi casi su 1 km: +100 m ≈ 20 min, +300 m ≈ 49 min, −100 m ≈ 14 min, −300 m ≈ 27-28 min.
  Default: modello CAI per i sentieri, DIN come confronto. Mostra sempre il **tempo netto (senza soste)** e un **tempo con soste** (+15-20%, configurabile).
- **Fattore personale**: slider "allenamento" (0,7×–1,4×) e fattore gruppo (es. 1,5× con bambini), salvati nel profilo.
- **Difficoltà**: il valore `cai_scale` delle relazioni attraversate prevale; dove manca, mappatura **indicativa** da `sac_scale`: `hiking` → T/E, `mountain_hiking` → E, `demanding_mountain_hiking` → EE, da `alpine_hiking` in su → EE/EEA o alpinistico (esclusi dal routing di default). La difficoltà dell'itinerario è la **massima** incontrata; mostra dove si trova il tratto più difficile.
- I valori dichiarati da una sezione CAI prevalgono nella scheda, con i valori calcolati come confronto.

---

## 9. UX/UI: principi da applicare

Sintesi di pattern consolidati delle app outdoor (komoot, Outdooractive, gpx.studio, AllTrails, Bikemap) e di casi di studio UX:

1. **Mappa al centro + bottom sheet** a tre altezze (anteprima / metà / piena), utilizzabile con il pollice; su desktop diventa un pannello laterale. Navigazione inferiore: **Esplora · Pianifica · Uscite · Profilo**.
2. **Filtri progressivi**: chip rapidi sempre visibili; il pannello filtri completo usa slider doppi con istogramma della distribuzione e un pulsante con conteggio live ("Mostra 37 itinerari"). Nessun filtro vuoto: se zero risultati, suggerisci quale filtro allargare.
3. **Mappa e lista sincronizzate**: scorrere le card evidenzia la traccia sulla mappa e viceversa.
4. **Profilo altimetrico come elemento di prima classe**: interattivo, sincronizzato con la mappa, colorato per pendenza, con waypoint.
5. **Planner diretto**: tocca per aggiungere, trascina per spostare, tocca la linea per inserire; statistiche sempre visibili in alto; suggerimenti contestuali al primo utilizzo di una funzione invece di un tutorial iniziale.
6. **Lessico CAI** riconoscibile: badge T/E/EE/EEA/EAI con lettere e colore (mai solo colore), metriche nell'ordine della locandina (tempo, difficoltà, km, dislivello).
7. **Leggibilità all'aperto**: contrasto alto (WCAG AA minimo, testo su mappa con alone), touch target ≥ 48 px, font ≥ 16 px, tema scuro, uso con guanti (pulsanti grandi per le azioni principali).
8. **Avvisi inline e spiegati**, mai modali bloccanti; stato offline sempre visibile.
9. **Pagina invito** pensata per chi arriva da WhatsApp: si carica velocemente, mostra subito data, ritrovo, metriche, meteo e un unico pulsante principale.
10. **Prestazioni**: primo caricamento rapido (LCP < 2,5 s su 4G), skeleton al posto degli spinner, tracce semplificate per zoom bassi.

Prima di scrivere codice UI, crea in `docs/ux/` dei wireframe a bassa fedeltà (anche in HTML statico) per: Esplora, Filtri, Scheda, Planner, Organizza uscita, Pagina invito. Mostrali all'utente insieme al report della Fase 1.

---

## 10. Architettura e stack consigliato

Sei libero di proporre alternative motivate nella Fase 0, ma il default è:

- **Frontend**: TypeScript, SvelteKit o Next.js (scegli e motiva), **MapLibre GL JS** + `pmtiles`, stile mappa topo personalizzato (hillshade + curve di livello + overlay sentieri con numeri CAI), profilo altimetrico su canvas/SVG fatto in casa o con libreria leggera; PWA con Workbox; IndexedDB (Dexie) per i dati locali; OPFS per i pacchetti mappa.
- **Backend**: PostgreSQL + **PostGIS** (Supabase va bene per avere Auth, Storage, Realtime e Row Level Security pronti); API REST/RPC tipizzate.
- **Routing**: **GraphHopper** self-hosted (Docker) con profilo `hike` e custom model che penalizza i tratti oltre la difficoltà scelta e premia le way appartenenti a relazioni escursionistiche. In alternativa **BRouter** con profilo hiking-mountain. Nella Fase 0 fai uno spike comparativo sull'area Lagorai e scegli.
- **Ingestion**: worker Python (osmium, shapely/geopandas, PyMuPDF, httpx), job schedulati (cron container o GitHub Actions), code di lavoro in Postgres.
- **Tile**: pipeline che genera PMTiles (tippecanoe) per sentieri/POI dell'area e li pubblica su object storage (es. Cloudflare R2) con CDN.
- **LLM**: Claude API per estrazione e sintesi, con output strutturato, cache per hash del documento e budget massimo configurabile.
- **Notifiche**: Web Push (VAPID) + email transazionale.
- **Osservabilità**: log strutturati, Sentry o equivalente open source, dashboard dello stato delle fonti (ultimo aggiornamento, errori).

### Modello dati (minimo)
- `sources`, `raw_documents` (url, hash, fetched_at, mime, storage_key)
- `trails` (osm_relation_id, ref, ref_rei, name, cai_scale, osmc_symbol, network, geom, …)
- `pois` (osm_id, type, name_it, name_de, ele, seasonal_status, geom)
- `itineraries` (canonico: geom, stats, difficulty, type, season, summary, source_ids, quality_score)
- `cai_activities` (itinerary_id, section, date, raw_document_id, extracted_json, confidence, match_score, status, official_signup_url)
- `user_plans` (owner, waypoints, geom, stats, visibility)
- `outings` (organizer, itinerary_or_plan_id, datetime, meeting_point, seats, deadline, requirements, visibility, status)
- `outing_participants` (outing_id, user_id, status, created_at), `outing_announcements`
- `users` (profilo minimo, fattore personale, difficoltà massima preferita), `saved_items`, `offline_packs`
- Row Level Security su tutto ciò che appartiene agli utenti.

---

## 11. Fasi di lavoro e criteri di accettazione

Lavora **una fase alla volta**. Alla fine di ogni fase: riepilogo di cosa è stato fatto, decisioni prese (in `docs/decisions/`), problemi aperti, domande per l'utente.

**Fase 0 — Ricognizione e fondamenta**
- Setup repo (monorepo: `apps/web`, `services/ingest`, `services/routing`, `infra/`), linting, test, CI.
- Verifica **ogni fonte** del §6 con script di campionamento sull'area Lagorai; `docs/data-sources.md` compilato.
- Spike del routing (GraphHopper vs BRouter) sulla fixture Val d'Inferno.
- Checkpoint: presenta lo stato delle fonti (cosa funziona, cosa no) e lo stack scelto.

**Fase 1 — Dati OSM e mappa**
- Sincronizzazione incrementale OSM2CAI (§6.1) e import dell'estratto OSM in PostGIS, sentieri e POI dell'area pilota, PMTiles, mappa con hillshade, scheda base di un sentiero/itinerario con profilo altimetrico.
- Calcolo di dislivello e tempi (§8) con test unitari (fixture DIN 7:00, casi CAI).
- Wireframe UX (§9).
- Checkpoint: demo della mappa + wireframe.

**Fase 2 — Pilota 20 attività CAI** (§7.6)
- Discovery, fetch, estrazione, ricostruzione traccia, report.
- **CHECKPOINT BLOCCANTE**: nessuna ingestion massiva senza approvazione.

**Fase 3 — Esplora + archivio + altre fonti**
- Lista/mappa/filtri/preset, scheda completa, archivio attività, integrazione Open Data Hub e SAT, deduplica, ricerca in linguaggio naturale (§5.7 b).

**Fase 4 — Planner**
- Routing vincolato alla difficoltà, editing completo, statistiche live, avvisi (inclusi gli `issues` OSM2CAI), import GPX, stato nell'URL.
- Generatore di proposte stile sezione CAI (§5.7 a) con la sua valutazione nell'harness.

**Fase 5 — Uscite di gruppo**
- Creazione uscita, pagina invito, iscrizione con magic link/passkey, lista d'attesa, annullamento/rinvio, notifiche push ed email, anteprima social, disclaimer.

**Fase 6 — Meteo e valanghe**
- Open-Meteo multi-punto con cache, bollettino Euregio con intersezione delle micro-regioni, indicatore Condizioni spiegato, briefing pre-uscita (§5.7 c).

**Fase 7 — Offline ed export**
- Pacchetti mappa offline, GPS live, export GPX/KML/GeoJSON, gestione dello spazio.

**Fase 8 — Rifinitura e lancio**
- Accessibilità (axe), Lighthouse PWA ≥ 90, test e2e Playwright dei flussi principali, pagina Fonti e licenze, privacy policy, termini d'uso con disclaimer di sicurezza, piano di aggiornamento dati.

Criteri trasversali: test automatici per formule e parser; nessuna chiave API nel client; tutte le chiamate esterne passano da adapter con cache e retry; ogni dato mostrato ha la sua fonte tracciabile.

---

## 12. Progetti open source da studiare (spunti, non copia)

Controlla le licenze prima di riusare codice; dai progetti GPL prendi solo idee, a meno che l'app non adotti una licenza compatibile.

- **gpx.studio** (MIT, SvelteKit + MapLibre + GraphHopper): UX del planner, profilo altimetrico, gestione GPX.
- **brouter / brouter-web**: routing escursionistico consapevole della quota, profili `.brf`, filtro del rumore altimetrico.
- **Carreau/brx**: routing nel browser che funziona offline, tutto lo stato nell'URL (link condivisibili, Indietro = annulla).
- **trails-cool/trails**: pianificazione collaborativa in tempo reale con link condiviso.
- **hike-finder-mcp**: ricerca di relazioni escursionistiche OSM filtrate per dislivello calcolato localmente, cache delle aree Overpass con TTL.
- **hikeratlas/routes**: trasformazione di oggetti OSM in "sentieri canonici".
- **osmItalia/cai_scripts**: estrazione dei dati CAI da OSM (relazioni, `cai_scale`, report) via Overpass.
- **waymarkedtrails/waymarked-trails-site**: ordinamento delle relazioni, export GPX, rendering dei segnavia.
- **simon04/eaws-bulletin-map** + **albina-euregio/pyAvaCore** + **eaws/eaws-regions**: parsing CAAMLv6 e poligoni delle micro-regioni.
- **open-meteo/open-meteo**: API meteo ed elevazione.
- **@makina-corpus/maplibre-offline-pmtiles**: mappe offline in PWA con OPFS.
- **HduSy/x-route**: route planner offline-first con filtraggio dell'elevazione.

---

## 13. Fuori ambito per la v1
- Navigazione turn-by-turn vocale; registrazione completa dell'attività stile Strava (solo posizione live).
- Pagamenti e quote di iscrizione.
- Alpinismo, ferrate EEA avanzate, sci alpinismo (solo come dati informativi, esclusi dal routing di default).
- Social network completo (follower, feed globale di foto).

---

## 13-bis. Account da socia CAI dell'utente

L'utente è socia CAI e ha un account. Le fonti pubbliche verificate (OSM2CAI e i siti delle sezioni) **bastano per tutto l'MVP**; l'account può aggiungere dati, ma con regole precise:
- Nella Fase 0 chiedi all'utente di descrivere (o mostrarti con screenshot) cosa vede da socia: calendario centralizzato delle attività sezionali, iscrizioni online alle gite, dati del catasto riservati, export.
- Le credenziali **non entrano mai nel repository** né nel codice: se serve un accesso automatizzato, usa variabili d'ambiente o un secret manager, solo dopo il consenso esplicito dell'utente e verificando che i termini d'uso del portale lo permettano.
- I contenuti riservati ai soci **non vanno ripubblicati** a utenti non autenticati senza un'autorizzazione specifica del CAI: usali per arricchire o validare i dati pubblici, oppure mostrali solo a utenti che dichiarano di essere soci con un rimando al portale ufficiale.
- Se il portale espone un calendario strutturato delle attività, diventa la fonte primaria per le gite future e il crawler dei PDF passa a fonte secondaria: descrivi l'impatto nel report di Fase 0.

---

## 14. Domande da fare all'utente prima di iniziare (se non già chiarite)
1. Nome definitivo dell'app e dominio.
2. Budget mensile per hosting (routing, database, tile) e per l'API LLM dell'ingestion.
3. Uso previsto: personale/associativo non commerciale (compatibile con Open-Meteo free) oppure commerciale.
4. Elenco iniziale delle sezioni CAI da includere nel pilota oltre a quelle proposte.
5. Disponibilità a contattare CAI Alto Adige / sezioni per autorizzare la ripubblicazione dei testi.
