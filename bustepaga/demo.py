"""Buste paga finte, per provare il programma senza dati veri.

Il testo generato imita il layout tipico di un cedolino italiano (colonne
COMPETENZE / TRATTENUTE) e viene poi dato in pasto allo stesso lettore usato
per i PDF: cosi' la demo verifica anche che l'estrazione funzioni.
"""

from __future__ import annotations

from datetime import date

from .estrazione import busta_da_testo
from .modello import NOMI_MESE, BustaPaga, formatta_euro

PAGA_BASE = 1620.00
CONTINGENZA = 524.22
SUPERMINIMO = 180.00
ALIQUOTA_INPS = 0.0919
ALIQUOTA_IRPEF = 0.25
DETRAZIONI_MESE = 96.50
ADD_REGIONALE = 21.40
ADD_COMUNALE = 8.60


def _riga(descrizione: str, competenza: float | None = None, trattenuta: float | None = None,
          quantita: str = "") -> str:
    colonna_comp = formatta_euro(competenza) if competenza is not None else ""
    colonna_tratt = formatta_euro(trattenuta) if trattenuta is not None else ""
    return (
        f"  {descrizione:<38}{quantita:>8}"
        f"{colonna_comp:>16}{colonna_tratt:>16}"
    )


def testo_busta(periodo: date, mese_indice: int) -> str:
    """Cedolino sintetico con un aumento a meta' periodo e le mensilita' extra."""
    aumento = 85.00 if mese_indice >= 8 else 0.0
    scatti = 45.00 if mese_indice >= 3 else 0.0
    straordinari = [0, 0, 132.40, 0, 88.20, 0, 0, 210.60, 0, 0, 64.80, 0, 0, 0, 96.00][
        mese_indice % 15
    ]
    premio = 900.00 if periodo.month == 4 else 0.0
    tredicesima = PAGA_BASE + CONTINGENZA + SUPERMINIMO + aumento if periodo.month == 12 else 0.0
    quattordicesima = PAGA_BASE + CONTINGENZA + SUPERMINIMO + aumento if periodo.month == 7 else 0.0

    base = PAGA_BASE + aumento
    lordo = base + CONTINGENZA + SUPERMINIMO + scatti + straordinari + premio + tredicesima + quattordicesima
    contributi = round(lordo * ALIQUOTA_INPS, 2)
    imponibile_fiscale = round(lordo - contributi, 2)
    irpef_lorda = round(imponibile_fiscale * ALIQUOTA_IRPEF, 2)
    irpef_netta = round(max(irpef_lorda - DETRAZIONI_MESE, 0.0), 2)
    trattenute = round(contributi + irpef_netta + ADD_REGIONALE + ADD_COMUNALE, 2)
    netto = round(lordo - trattenute, 2)
    tfr_quota = round(lordo / 13.5, 2)
    tfr_totale = round(tfr_quota * (mese_indice + 14), 2)
    ferie = round(66.0 + mese_indice * 13.33 - (80.0 if periodo.month == 8 else 0.0), 2)
    permessi = round(24.0 + mese_indice * 2.0 - (8.0 if periodo.month in (5, 11) else 0.0), 2)

    righe = [
        "  AZIENDA ESEMPIO S.R.L. - VIA ROMA 1, MILANO",
        "  C.C.N.L.: COMMERCIO TERZIARIO      LIVELLO: 4      QUALIFICA: IMPIEGATO",
        f"  PROSPETTO PAGA - PERIODO DI RETRIBUZIONE: {NOMI_MESE[periodo.month].upper()} {periodo.year}",
        "",
        f"  {'DESCRIZIONE':<38}{'ORE':>8}{'COMPETENZE':>16}{'TRATTENUTE':>16}",
        "  " + "-" * 76,
        _riga("PAGA BASE", base, quantita="168,00"),
        _riga("EX INDENNITA DI CONTINGENZA", CONTINGENZA),
        _riga("SUPERMINIMO ASSORBIBILE", SUPERMINIMO),
    ]
    if scatti:
        righe.append(_riga("SCATTI DI ANZIANITA", scatti))
    if straordinari:
        righe.append(_riga("LAVORO STRAORDINARIO 25%", straordinari, quantita="8,00"))
    if premio:
        righe.append(_riga("PREMIO DI RISULTATO", premio))
    if tredicesima:
        righe.append(_riga("TREDICESIMA MENSILITA", tredicesima))
    if quattordicesima:
        righe.append(_riga("QUATTORDICESIMA MENSILITA", quattordicesima))
    righe += [
        _riga("CONTRIBUTI INPS C/DIPENDENTE 9,19%", trattenuta=contributi),
        _riga("RITENUTE IRPEF", trattenuta=irpef_netta),
        _riga("ADDIZIONALE REGIONALE", trattenuta=ADD_REGIONALE),
        _riga("ADDIZIONALE COMUNALE", trattenuta=ADD_COMUNALE),
        "  " + "-" * 76,
        _riga("TOTALE COMPETENZE", lordo),
        _riga("TOTALE TRATTENUTE", trattenuta=trattenute),
        "",
        f"  IMPONIBILE PREVIDENZIALE {formatta_euro(lordo):>12}"
        f"      IMPONIBILE FISCALE {formatta_euro(imponibile_fiscale):>12}",
        f"  IRPEF LORDA {formatta_euro(irpef_lorda):>12}"
        f"      TOTALE DETRAZIONI {formatta_euro(DETRAZIONI_MESE):>12}",
        f"  QUOTA TFR {formatta_euro(tfr_quota):>12}"
        f"      TFR NETTO ACCANTONATO {formatta_euro(tfr_totale):>12}",
        f"  FERIE RESIDUE {formatta_euro(ferie):>10}"
        f"      PERMESSI RESIDUI {formatta_euro(permessi):>10}"
        f"      ORE LAVORATE {formatta_euro(168.0):>10}",
        "",
        f"  {'NETTO IN BUSTA':<38}{'':>8}{formatta_euro(netto):>16}",
    ]
    return "\n".join(righe)


def buste_demo(quante: int = 15, inizio: date = date(2024, 10, 1)) -> list[BustaPaga]:
    buste = []
    for indice in range(quante):
        mese = (inizio.month - 1 + indice) % 12 + 1
        anno = inizio.year + (inizio.month - 1 + indice) // 12
        periodo = date(anno, mese, 1)
        nome = f"demo_{anno}-{mese:02d}.pdf"
        buste.append(busta_da_testo(testo_busta(periodo, indice), nome, motore="demo"))
    return buste
