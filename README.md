# Analisi buste paga

Un programma che legge i PDF delle tue buste paga, le mette a confronto una con
l'altra e mostra come si muovono nel tempo lordo, netto, trattenute, TFR, ferie
e permessi.

Tutto gira **sul tuo computer**: i PDF non vengono caricati da nessuna parte e
non serve la rete.

---

## Cosa fa

- **Legge** i PDF (anche di provider diversi: Zucchetti, TeamSystem, Inaz, ADP...)
  e ne estrae una quarantina di voci: totale competenze, trattenute, netto,
  contributi INPS, IRPEF lorda e netta, detrazioni, addizionali, TFR, ferie e
  permessi residui, ore, straordinari, premi, mensilità aggiuntive.
- **Confronta** ogni busta con quella del mese prima: cosa è cambiato, di quanto,
  in percentuale, e quali voci sono comparse o sparite.
- **Mostra l'andamento**: medie, mediane, minimi e massimi, totali per anno,
  tendenza del netto, prelievo effettivo (quanto del lordo se ne va in contributi
  e imposte), proiezione su dodici mesi.
- **Segnala le stranezze**: scostamenti oltre soglia, voci sparite, buste in cui
  i conti non tornano, cambi di livello o di CCNL.
- **Produce** un report a terminale, un report HTML con i grafici (un file solo,
  si apre con doppio clic anche senza internet) ed esportazioni in CSV/JSON.

Le mensilità aggiuntive (tredicesima, quattordicesima) vengono riconosciute ed
escluse dal calcolo della tendenza, altrimenti dicembre farebbe sembrare che lo
stipendio raddoppi ogni anno.

---

## Installazione

Serve Python 3.10 o successivo.

```bash
pip install -r requirements.txt
```

## Uso

```bash
# 1. metti i PDF in una cartella (va bene anche con sottocartelle)
mkdir -p buste && cp ~/Downloads/cedolino*.pdf buste/

# 2. guarda il riepilogo a terminale
python3 analizza_buste.py buste/

# 3. genera il report con i grafici e aprilo nel browser
python3 analizza_buste.py buste/ --html report.html --apri
```

Per vedere subito com'è fatto il risultato, senza avere PDF sotto mano:

```bash
python3 analizza_buste.py --demo --html report.html
```

### Tutte le opzioni

| Opzione | Cosa fa |
|---|---|
| `--html FILE` | scrive il report HTML con i grafici |
| `--apri` | apre il report nel browser appena creato |
| `--csv FILE` | esporta i dati estratti (utile per correggerli a mano) |
| `--json FILE` | esporta i dati estratti, voci di dettaglio comprese |
| `--da-csv FILE` | rifà l'analisi partendo da un CSV invece che dai PDF |
| `--da 2025-01` / `--a 2025-12` | limita l'analisi a un intervallo di mesi |
| `--soglia 15` | percentuale oltre la quale una variazione viene segnalata (default 10) |
| `--dump-testo CARTELLA` | salva il testo estratto da ogni PDF, per capire cosa non viene letto |
| `--demo` | usa buste finte di esempio |
| `--silenzioso` | non stampa il report a terminale |

---

## Se una voce non viene letta

Alla fine di ogni esecuzione il programma dice quante voci ha riconosciuto, e
nella sezione **Da controllare** elenca i campi letti solo in alcune buste. Le
etichette cambiano da provider a provider, quindi è normale doverne aggiungere
qualcuna. Tre modi, dal più veloce al più definitivo:

1. **Correggere a mano.** `--csv dati.csv`, apri il CSV con un foglio di calcolo,
   sistemi i valori sbagliati o mancanti, e rifai l'analisi con
   `--da-csv dati.csv`.

2. **Aggiungere l'etichetta.** `--dump-testo testo/` salva il testo di ogni PDF
   così com'è stato letto. Cerchi la riga della voce che manca e aggiungi la sua
   etichetta in `bustepaga/pattern.py`, nel campo giusto:

   ```python
   Campo(
       "netto",
       "Netto in busta",
       "riepilogo",
       (
           r"NETTO\s+IN\s+BUSTA",
           r"NETTO\s+A\s+LEI\s+SPETTANTE",   # <- la variante del tuo cedolino
           ...
       ),
   )
   ```

   I pattern sono espressioni regolari applicate al testo in maiuscolo e senza
   accenti. Si aggiungono lì e basta: tabelle, grafici e confronti si adeguano
   da soli.

3. **Aggiungere un campo nuovo.** Sempre in `pattern.py`, un nuovo `Campo` nella
   lista `CAMPI`. Compare automaticamente in tutte le tabelle.

### PDF che sembrano vuoti

Se il programma avvisa che *non c'è testo estraibile*, quel PDF è la scansione di
un foglio di carta: dentro c'è un'immagine, non del testo. Va prima passato in
OCR, per esempio con [`ocrmypdf`](https://ocrmypdf.readthedocs.io/):

```bash
ocrmypdf -l ita busta_scansionata.pdf busta_leggibile.pdf
```

---

## Come sono organizzati i file

```
analizza_buste.py        punto di ingresso
bustepaga/
  pattern.py             le etichette cercate nei cedolini  ← si tocca quasi solo questo
  modello.py             busta paga, numeri e date all'italiana
  estrazione.py          PDF -> testo -> valori
  analisi.py             confronti, statistiche, anomalie
  report_testo.py        report a terminale
  report_html.py         report HTML con i grafici SVG
  demo.py                buste finte per le prove
  cli.py                 riga di comando
tests/                   test, si eseguono senza dipendenze esterne
buste/                   qui vanno i tuoi PDF (esclusi da git)
```

Per l'estrazione del testo si prova `pdfplumber`, poi `pypdf`, poi il comando di
sistema `pdftotext`: basta che uno dei tre funzioni.

### Test

```bash
python3 -m unittest discover -s tests -t .
```

---

## Privacy

Una busta paga contiene nome, codice fiscale, retribuzione e a volte l'IBAN.
Per questo:

- il `.gitignore` esclude `buste/`, tutti i `*.pdf`, i CSV/JSON esportati e i
  report HTML generati;
- il programma non fa nessuna chiamata di rete, e il report HTML non carica
  risorse esterne (niente font, niente librerie da CDN);
- se condividi il report, ricordati che contiene i tuoi importi.

## Attendibilità

I valori vengono letti automaticamente da documenti pensati per gli occhi, non
per i programmi. Il controllo di quadratura (*lordo − trattenute = netto*)
intercetta gli errori più grossi e li segnala, ma in caso di dubbio fa fede il
PDF originale. Questo strumento serve a farsi domande, non a sostituire il
consulente del lavoro.
