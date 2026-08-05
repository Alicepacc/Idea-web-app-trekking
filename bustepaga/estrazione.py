"""Dal PDF della busta paga a un oggetto `BustaPaga`.

Strategia (in ordine): pdfplumber -> pypdf -> `pdftotext -layout`.
Se nessuno dei tre restituisce testo, il PDF e' quasi certamente una scansione
e va prima passato in OCR (vedi README).
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import NamedTuple

from . import formati
from .modello import (
    MESI,
    MESI_ABBR,
    BustaPaga,
    Voce,
    normalizza_testo,
    numeri_in_riga,
)
from .pattern import (
    CAMPI,
    CAMPI_PER_CHIAVE,
    PATTERN_ANAGRAFICA,
    TUTTE_LE_ETICHETTE,
    Campo,
)

# Numero con posizione: serve a capire in quale colonna sta l'importo.
_NUMERO_POS = re.compile(r"(?<![\w.,])-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?-?(?![\w,])")
_PERCENTUALE = re.compile(r"-?\d[\d.,]*\s*%")
_CODICE_INIZIALE = re.compile(r"^\s*\d{1,5}\s+")


# ---------------------------------------------------------------------------
# estrazione del testo
# ---------------------------------------------------------------------------


def estrai_testo(percorso: Path) -> tuple[str, str]:
    """Restituisce (testo, nome del motore che ha funzionato)."""
    for motore, funzione in (
        ("pdfplumber", _testo_pdfplumber),
        ("pypdf", _testo_pypdf),
        ("pdftotext", _testo_pdftotext),
    ):
        try:
            testo = funzione(percorso)
        except ImportError:
            continue
        except Exception:  # motore presente ma in difficolta' su questo PDF
            continue
        if testo and testo.strip():
            return testo, motore
    return "", "nessuno"


def _testo_pdfplumber(percorso: Path) -> str:
    import pdfplumber  # noqa: PLC0415  (import opzionale)

    pagine = []
    with pdfplumber.open(str(percorso)) as pdf:
        for pagina in pdf.pages:
            # layout=True conserva l'incolonnamento: ci serve per capire se un
            # importo sta nella colonna "competenze" o "trattenute".
            pagine.append(pagina.extract_text(layout=True) or "")
    return "\n".join(pagine)


def _testo_pypdf(percorso: Path) -> str:
    from pypdf import PdfReader  # noqa: PLC0415

    lettore = PdfReader(str(percorso))
    return "\n".join((pagina.extract_text() or "") for pagina in lettore.pages)


def _testo_pdftotext(percorso: Path) -> str:
    if not shutil.which("pdftotext"):
        raise ImportError("pdftotext non installato")
    risultato = subprocess.run(
        ["pdftotext", "-layout", str(percorso), "-"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return risultato.stdout


# ---------------------------------------------------------------------------
# periodo di riferimento
# ---------------------------------------------------------------------------

# Solo diciture che indicano davvero il mese di paga: un generico "MESE"
# prendeva per buone cose come «CREDITO D.L. 3/2020 MESE».
_ETICHETTA_PERIODO = re.compile(
    r"(?:PERIODO\s+(?:DI\s+)?(?:RETRIBUZIONE|PAGA|COMPETENZA|RIFERIMENTO)"
    r"|MESE\s+(?:DI\s+)?(?:COMPETENZA|RIFERIMENTO|PAGA|RETRIBUZIONE)"
    r"|MESE\s*/\s*ANNO|RETRIBUZIONE\s+DEL|COMPETENZE\s+DEL)[^\n]{0,40}"
)
_MESE_ANNO_TESTO = re.compile(
    r"\b(" + "|".join(MESI) + r")\w*\s*[/\- ]?\s*(\d{4})\b", re.IGNORECASE
)
_MESE_ANNO_ABBR = re.compile(
    r"\b(" + "|".join(MESI_ABBR) + r")\.?\s*[/\- ]?\s*(\d{4})\b", re.IGNORECASE
)
_MESE_ANNO_NUM = re.compile(r"\b(0?[1-9]|1[0-2])\s*[/\-]\s*(20\d{2})\b")


def trova_periodo(testo: str, nome_file: str = "") -> date | None:
    """Cerca il mese di competenza nel testo, poi nel nome del file."""
    for sorgente in (_righe_periodo(testo), testo, nome_file):
        periodo = _cerca_mese_anno(sorgente)
        if periodo:
            return periodo
    return None


def _righe_periodo(testo: str) -> str:
    """Le righe che contengono un'etichetta tipo "periodo"/"mese": piu' affidabili."""
    righe = [r for r in testo.splitlines() if _ETICHETTA_PERIODO.search(r.upper())]
    return "\n".join(righe)


def _cerca_mese_anno(testo: str) -> date | None:
    if not testo:
        return None
    trovato = _MESE_ANNO_TESTO.search(testo)
    if trovato:
        return date(int(trovato.group(2)), MESI[trovato.group(1).lower()], 1)
    trovato = _MESE_ANNO_ABBR.search(testo)
    if trovato:
        return date(int(trovato.group(2)), MESI_ABBR[trovato.group(1).lower()[:3]], 1)
    trovato = _MESE_ANNO_NUM.search(testo)
    if trovato:
        return date(int(trovato.group(2)), int(trovato.group(1)), 1)
    # forma compatta nei nomi file: 2025-03, 2025_03, 202503
    # (niente \b: in "busta_2024_11" l'underscore non e' un confine di parola)
    trovato = re.search(r"(?<!\d)(20\d{2})[-_ ]?(0[1-9]|1[0-2])(?!\d)", testo)
    if trovato:
        return date(int(trovato.group(1)), int(trovato.group(2)), 1)
    return None


# ---------------------------------------------------------------------------
# valori numerici
# ---------------------------------------------------------------------------


TOLLERANZA_COLONNA = 8.0  # caratteri di scarto ammessi rispetto al centro colonna


def estrai_valori(righe: list[str]) -> dict[str, float]:
    contesto = _Contesto(righe)
    valori: dict[str, float] = {}
    for campo in CAMPI:
        valore = _valore_campo(campo, contesto)
        if valore is not None:
            valori[campo.chiave] = valore
    return valori


class _Contesto:
    """Il documento pre-masticato: colonne e confini delle etichette."""

    def __init__(self, righe: list[str]) -> None:
        self.righe = righe
        self.colonne = _colonne_competenze_trattenute(righe)
        etichette = re.compile(TUTTE_LE_ETICHETTE)
        # dove comincia ogni etichetta: il valore di una voce non puo' stare
        # oltre l'inizio della voce successiva sulla stessa riga
        self.confini = [[t.start() for t in etichette.finditer(r)] for r in righe]

    def finestra(self, indice: int, inizio: int, fine: int) -> tuple[int, int]:
        confini = self.confini[indice]
        precedente = max((c for c in confini if c < inizio), default=0)
        successivo = min((c for c in confini if c > fine), default=len(self.righe[indice]))
        return precedente, successivo


def _valore_campo(campo: Campo, contesto: _Contesto) -> float | None:
    trovati: list[float] = []
    for pattern in campo.pattern:
        regex = re.compile(pattern)
        for indice, riga in enumerate(contesto.righe):
            trovato = regex.search(riga)
            if not trovato:
                continue
            if any(re.search(e, riga) for e in campo.esclusioni):
                continue
            valore = _numero_vicino(campo, contesto, indice, trovato.start(), trovato.end())
            if valore is not None:
                trovati.append(valore)
        if trovati:
            # il primo pattern che produce risultati vince: i pattern sono
            # ordinati dal piu' specifico al piu' generico.
            break
    if not trovati:
        return None
    if campo.somma:
        return round(sum(trovati), 2)
    return trovati[-1] if campo.occorrenza == "ultima" else trovati[0]


class Numero(NamedTuple):
    """Un numero e la sua posizione nella riga (serve per capire la colonna)."""

    valore: float
    centro: float
    inizio: int


def _numeri_posizionati(testo: str, scarto: int = 0) -> list[Numero]:
    """I numeri della riga con la loro posizione, percentuali escluse."""
    pulita = _PERCENTUALE.sub(lambda m: " " * len(m.group()), testo)
    trovati = []
    for corrispondenza in _NUMERO_POS.finditer(pulita):
        valori = numeri_in_riga(corrispondenza.group())
        if valori:
            trovati.append(
                Numero(
                    valore=valori[0],
                    centro=scarto + (corrispondenza.start() + corrispondenza.end()) / 2,
                    inizio=scarto + corrispondenza.start(),
                )
            )
    return trovati


def _numero_vicino(
    campo: Campo, contesto: _Contesto, indice: int, inizio: int, fine: int
) -> float | None:
    """Il numero associato all'etichetta: dopo di essa, prima di essa, o sotto."""
    riga = contesto.righe[indice]
    apertura, chiusura = contesto.finestra(indice, inizio, fine)
    dopo = _numeri_posizionati(riga[fine:chiusura], fine)
    # Il valore si cerca nella riga sotto solo quando l'etichetta e' una pura
    # intestazione. Se sulla sua riga un importo c'e' gia', quello e' il suo:
    # andare a pescare sotto vorrebbe dire prendere il numero di un'altra voce.
    sotto = (
        _numero_incolonnato(contesto, indice, inizio, fine)
        if not [n for n in _numeri_posizionati(riga) if not _e_codice(riga, n)]
        else None
    )

    if contesto.colonne and campo.colonna:
        # Cedolino incolonnato: un importo sta per forza sotto COMPETENZE o
        # TRATTENUTE. Prima si cerca dopo l'etichetta, poi su tutta la riga
        # (la descrizione della voce puo' contenere altre parole-etichetta).
        # Se in quella colonna non c'e' niente, meglio nessun valore che un
        # numero preso dalla colonna sbagliata (codice voce, ore, quantita').
        scelto = _per_colonna(campo, contesto, dopo)
        if scelto is None:
            scelto = _per_colonna(campo, contesto, _numeri_posizionati(riga))
        return scelto if scelto is not None else sotto

    if dopo:
        return dopo[0].valore if campo.posizione == "primo" else dopo[-1].valore

    prima = [
        n
        for n in _numeri_posizionati(riga[apertura:inizio], apertura)
        if not _e_codice(riga, n)
    ]
    if prima:
        return prima[-1].valore

    return sotto


def _e_codice(riga: str, numero: Numero) -> bool:
    """Il numero di voce a inizio riga non e' un importo."""
    codice = _CODICE_INIZIALE.match(riga)
    return bool(codice) and numero.inizio < codice.end()


def _centro_colonna(campo: Campo, contesto: _Contesto) -> float | None:
    if not contesto.colonne or not campo.colonna:
        return None
    return contesto.colonne[0] if campo.colonna == "competenze" else contesto.colonne[1]


def _per_colonna(campo: Campo, contesto: _Contesto, candidati: list[Numero]) -> float | None:
    """Sceglie il numero incolonnato sotto COMPETENZE o TRATTENUTE."""
    centro_colonna = _centro_colonna(campo, contesto)
    if not candidati or centro_colonna is None:
        return None
    migliore = min(candidati, key=lambda n: abs(n.centro - centro_colonna))
    if abs(migliore.centro - centro_colonna) <= TOLLERANZA_COLONNA:
        return migliore.valore
    return None


def _numero_incolonnato(
    contesto: _Contesto, indice: int, inizio: int, fine: int
) -> float | None:
    """Etichetta su una riga e valore incolonnato in quella sotto.

    Vale per le intestazioni dei riepiloghi. Una riga di voce numerata non e'
    mai il valore dell'etichetta di sopra: la si salta, altrimenti il codice
    della voce successiva passa per un importo.
    """
    centro_etichetta = (inizio + fine) / 2
    for successiva in contesto.righe[indice + 1 : indice + 3]:
        if _CODICE_INIZIALE.match(successiva):
            continue
        candidati = _numeri_posizionati(successiva)
        if not candidati:
            continue
        migliore = min(candidati, key=lambda n: abs(n.centro - centro_etichetta))
        if abs(migliore.centro - centro_etichetta) <= max(12.0, (fine - inizio) / 2 + 6):
            return migliore.valore
    return None


# ---------------------------------------------------------------------------
# voci di dettaglio (per capire cosa e' comparso o sparito da un mese all'altro)
# ---------------------------------------------------------------------------

_INTESTAZIONE_COLONNE = re.compile(r"COMPETENZ[EA]")
_COLONNA_TRATTENUTE = re.compile(r"TRATTENUT[EA]|RITENUT[EA]")


TOLLERANZA_VOCE = 10.0
_ANNO_SOLO = re.compile(r"^(?:19|20)\d{2}$")


def estrai_voci(righe: list[str]) -> list[Voce]:
    """Le righe di dettaglio, per vedere quali voci compaiono o spariscono.

    Si tengono solo le righe che hanno un importo incolonnato sotto
    COMPETENZE o TRATTENUTE: cosi' intestazioni, dati anagrafici e note
    non finiscono tra le voci.
    """
    colonne = _intestazione_colonne(righe)
    prima_riga = colonne[2] + 1 if colonne else 0
    voci: list[Voce] = []
    for riga in righe[prima_riga:_fine_parte_retributiva(righe)]:
        if len(riga.strip()) < 6:
            continue
        if re.search(r"TOT(?:ALE|\.)?\s+(?:COMPETENZE|TRATTENUTE|RITENUTE)", riga):
            continue
        numeri = _numeri_posizionati(riga)
        if not numeri:
            continue
        competenza = trattenuta = None
        if colonne:
            centro_comp, centro_tratt, _ = colonne
            for numero in numeri:
                if abs(numero.centro - centro_comp) <= TOLLERANZA_VOCE:
                    competenza = numero.valore
                elif abs(numero.centro - centro_tratt) <= TOLLERANZA_VOCE:
                    trattenuta = numero.valore
        elif not _ANNO_SOLO.match(str(int(numeri[-1].valore))) and ":" not in riga:
            competenza = numeri[-1].valore
        if competenza is None and trattenuta is None:
            continue
        # la descrizione arriva fino al primo importo: il codice voce a inizio
        # riga e' un numero, ma non e' un importo
        primo_importo = next(
            (n.inizio for n in numeri if not _e_codice(riga, n)), numeri[0].inizio
        )
        descrizione = _descrizione_voce(riga, primo_importo)
        if descrizione and descrizione.lower() not in MESI:
            voci.append(
                Voce(
                    descrizione=descrizione,
                    competenza=competenza,
                    trattenuta=trattenuta,
                    con_codice=bool(_CODICE_INIZIALE.match(riga)),
                )
            )
    return _solo_voci_vere(voci)


def _solo_voci_vere(voci: list[Voce]) -> list[Voce]:
    """Se il cedolino numera le voci, tiene solo quelle numerate.

    Nei cedolini con il codice voce le righe dei riepiloghi (IMPONIBILE,
    TRATTENUTE, QUOTA TFR...) non ce l'hanno: senza questo filtro finirebbero
    tra le voci e comparirebbero e sparirebbero a ogni mese.
    """
    numerate = [v for v in voci if v.con_codice]
    return numerate if len(numerate) >= 3 else voci


_FINE_RETRIBUTIVA = re.compile(r"SEZIONE\s+PRESENZE|PRESENZE\s*/\s*ASSENZE")


def _fine_parte_retributiva(righe: list[str]) -> int:
    """Dove finiscono le voci di paga: il calendario presenze non e' una voce."""
    for indice, riga in enumerate(righe):
        if _FINE_RETRIBUTIVA.search(riga):
            return indice
    return len(righe)


def _intestazione_colonne(righe: list[str]) -> tuple[float, float, int] | None:
    """Centro (in caratteri) delle colonne competenze/trattenute e riga trovata."""
    for indice, riga in enumerate(righe):
        comp = _INTESTAZIONE_COLONNE.search(riga)
        tratt = _COLONNA_TRATTENUTE.search(riga)
        if comp and tratt and comp.start() != tratt.start():
            return (
                (comp.start() + comp.end()) / 2,
                (tratt.start() + tratt.end()) / 2,
                indice,
            )
    return None


def _colonne_competenze_trattenute(righe: list[str]) -> tuple[float, float] | None:
    colonne = _intestazione_colonne(righe)
    return (colonne[0], colonne[1]) if colonne else None


def _descrizione_voce(riga: str, fino_a: int) -> str:
    testo = _CODICE_INIZIALE.sub("", riga[:fino_a]).strip(" .-:")
    testo = re.sub(r"\s{2,}", " ", testo)
    # le colonne di segnalazione (S, F, asterischi) non fanno parte del nome
    testo = re.sub(r"[\s*]+$", "", testo)
    if len(testo) < 3 or not re.search(r"[A-Z]{3}", testo):
        return ""
    return testo[:60].strip()


# ---------------------------------------------------------------------------
# lettura di uno o piu' file
# ---------------------------------------------------------------------------


def leggi_busta(percorso: Path) -> BustaPaga:
    testo_grezzo, motore = estrai_testo(percorso)
    return busta_da_testo(testo_grezzo, percorso.name, motore)


def busta_da_testo(testo_grezzo: str, nome_file: str, motore: str = "testo") -> BustaPaga:
    """Costruisce una busta dal testo gia' estratto (usata anche dai test)."""
    busta = BustaPaga(file=nome_file, testo=testo_grezzo)

    if not testo_grezzo.strip():
        busta.avvisi.append(
            "nessun testo estraibile (PDF scansionato?): serve un passaggio di OCR"
        )
        return busta

    normalizzato = normalizza_testo(testo_grezzo)
    righe = normalizzato.splitlines()

    busta.periodo = trova_periodo(normalizzato, normalizza_testo(nome_file))
    busta.valori = estrai_valori(righe)
    busta.voci = estrai_voci(righe)
    busta.anagrafica = estrai_anagrafica(normalizzato)

    # Un lettore dedicato al formato, se lo riconosce, sa leggere i blocchi che
    # il motore generico non puo' indovinare: i suoi valori hanno la precedenza.
    formato = formati.riconosci(righe)
    if formato is not None:
        busta.formato = formato.nome
        busta.valori.update(formato.estrai(righe))

    if busta.periodo is None:
        busta.avvisi.append("periodo non riconosciuto: rinomina il file come 2025-03.pdf")
    if "netto" not in busta.valori:
        busta.avvisi.append(f"netto non trovato (motore: {motore})")
    if "totale_competenze" not in busta.valori:
        busta.avvisi.append(f"totale competenze non trovato (motore: {motore})")
    _controlla_quadratura(busta)
    return busta


def _controlla_quadratura(busta: BustaPaga) -> None:
    """Lordo - trattenute deve dare il netto: se non torna, l'estrazione ha sbagliato."""
    lordo = busta.get("totale_competenze")
    trattenute = busta.get("totale_trattenute")
    netto = busta.get("netto")
    if lordo is None or trattenute is None or netto is None:
        return
    scarto = abs(lordo - trattenute - netto)
    if scarto > max(2.0, 0.02 * abs(netto)):
        busta.avvisi.append(
            f"i conti non tornano: lordo - trattenute = {lordo - trattenute:.2f} "
            f"ma il netto letto e' {netto:.2f} (verifica il PDF)"
        )


def leggi_percorsi(percorsi: list[Path]) -> list[BustaPaga]:
    """Legge PDF singoli e/o intere cartelle, ordinando per periodo."""
    file_pdf: list[Path] = []
    for percorso in percorsi:
        if percorso.is_dir():
            file_pdf.extend(sorted(percorso.rglob("*.pdf")))
        elif percorso.suffix.lower() == ".pdf":
            file_pdf.append(percorso)
    buste = [leggi_busta(p) for p in file_pdf]
    return ordina(buste)


def ordina(buste: list[BustaPaga]) -> list[BustaPaga]:
    return sorted(buste, key=lambda b: (b.periodo or date(1900, 1, 1), b.file))


# ---------------------------------------------------------------------------
# CSV: esportazione e reimportazione (per correggere a mano cio' che sfugge)
# ---------------------------------------------------------------------------

INTESTAZIONI_CSV = ["periodo", "file"] + [c.chiave for c in CAMPI]


def scrivi_csv(buste: list[BustaPaga], destinazione: Path) -> None:
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    with destinazione.open("w", newline="", encoding="utf-8") as f:
        scrittore = csv.DictWriter(f, fieldnames=INTESTAZIONI_CSV, delimiter=";")
        scrittore.writeheader()
        for busta in buste:
            riga = {
                "periodo": busta.periodo.strftime("%Y-%m") if busta.periodo else "",
                "file": busta.file,
            }
            for chiave, valore in busta.valori.items():
                riga[chiave] = f"{valore:.2f}".replace(".", ",")
            scrittore.writerow(riga)


def leggi_csv(sorgente: Path) -> list[BustaPaga]:
    """Rilegge un CSV (anche corretto a mano) come elenco di buste."""
    buste: list[BustaPaga] = []
    with sorgente.open(newline="", encoding="utf-8-sig") as f:
        campione = f.read(4096)
        f.seek(0)
        delimitatore = ";" if campione.count(";") >= campione.count(",") else ","
        for riga in csv.DictReader(f, delimiter=delimitatore):
            busta = BustaPaga(file=(riga.get("file") or "").strip())
            periodo = (riga.get("periodo") or "").strip()
            if periodo:
                busta.periodo = _cerca_mese_anno(periodo)
            for chiave in CAMPI_PER_CHIAVE:
                testo = (riga.get(chiave) or "").strip()
                if not testo:
                    continue
                numeri = numeri_in_riga(testo)
                if numeri:
                    busta.valori[chiave] = numeri[0]
            buste.append(busta)
    return ordina(buste)


def estrai_anagrafica(normalizzato: str) -> dict[str, str]:
    """Azienda, livello, qualifica, CCNL: spiegano gli scalini nello stipendio."""
    trovati: dict[str, str] = {}
    for chiave, pattern in PATTERN_ANAGRAFICA.items():
        for singolo in pattern:
            trovato = re.search(singolo, normalizzato)
            if trovato:
                valore = re.sub(r"\s{2,}", " ", trovato.group(1)).strip(" .:-")
                if valore:
                    trovati[chiave] = valore
                break
    return trovati
