"""Lettori dedicati a formati di cedolino specifici.

Il motore generico di `estrazione.py` cerca «etichetta → numero vicino». Alcuni
cedolini pero' mettono i totali in blocchi dove l'etichetta e il valore non si
toccano: intestazioni su una riga e valori su quella dopo, disallineati, con
colonne vuote in mezzo. Per quei casi serve sapere com'e' fatto il modulo.

Ogni formato dichiara come si riconosce e cosa sa leggere. I valori che
restituisce hanno la precedenza su quelli del motore generico.

Per aggiungere un formato: una funzione `riconosce`, una funzione `estrai`, e
una riga in `FORMATI`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .modello import numeri_in_riga

# Date e orari non sono importi: vanno tolti prima di leggere i numeri.
_DATA_ORA = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")


@dataclass(frozen=True)
class Formato:
    nome: str
    riconosce: Callable[[list[str]], bool]
    estrai: Callable[[list[str]], dict[str, float]]


def riconosci(righe: list[str]) -> Formato | None:
    for formato in FORMATI:
        if formato.riconosce(righe):
            return formato
    return None


# ---------------------------------------------------------------------------
# utilita' comuni
# ---------------------------------------------------------------------------


def _numeri(riga: str, fino_a_colonna: int | None = None) -> list[float]:
    """I numeri della riga, senza date e orari."""
    testo = _DATA_ORA.sub(" ", riga)
    if fino_a_colonna is not None:
        testo = testo[:fino_a_colonna]
    return numeri_in_riga(testo)


def _valori_sotto(righe: list[str], indice: int, entro: int = 2) -> list[float]:
    """I numeri della prima riga utile sotto un'intestazione."""
    for riga in righe[indice + 1 : indice + 1 + entro]:
        numeri = _numeri(riga)
        if numeri:
            return numeri
    return []


def _imposta(valori: dict[str, float], chiave: str, valore: float | None) -> None:
    """Scrive solo se c'e' davvero un valore: un blocco vuoto non cancella."""
    if valore is not None:
        valori[chiave] = valore


# ---------------------------------------------------------------------------
# ADP / Libro Unico (usato tra gli altri da Iveco, IDV, Stellantis)
# ---------------------------------------------------------------------------

_ADP_INPS = re.compile(r"SOCIALI\s+I\.?\s?N\.?\s?P\.?\s?S")
_ADP_IRPEF = re.compile(r"FISCALI\s+IRPEF")
_ADP_PROGRESSIVI = re.compile(r"TOTALE\s+TRATTENUTE\s+TOTALE\s+COMPETENZE")
_ADP_NETTO = re.compile(r"ARROTONDAMENTO\s+NETTO")
_ADP_DETRAZIONI = re.compile(r"GG\.?\s?DETRAZIONE")
_ADP_TFR = re.compile(r"MAT\.MESE\s+AL\s+NETTO")
_ADP_PAGA_BASE = re.compile(r"PAGA\s+BASE\s+SUPERM")
_ADP_TEORICA = re.compile(r"RETRIBUZIONE\s+TEORICA\s+MENSILE")
_ADP_PRESENZE = re.compile(r"GG\.\s?INPS\b")
_ADP_FERIE = re.compile(r"^\s*FERIE\s+PAR\b")

# Nel blocco ferie/permessi la colonna di destra e' l'indirizzo del dipendente:
# oltre questa colonna i numeri non sono ore.
_ADP_COLONNA_FERIE = 40


def _adp_riconosce(righe: list[str]) -> bool:
    testa = "\n".join(righe[:120])
    return bool(
        re.search(r"LIBRO\s+UNICO", testa)
        and (_ADP_PROGRESSIVI.search("\n".join(righe)) or _ADP_INPS.search("\n".join(righe)))
    )


def _adp_estrai(righe: list[str]) -> dict[str, float]:
    valori: dict[str, float] = {}
    for indice, riga in enumerate(righe):
        if _ADP_INPS.search(riga):
            _adp_imponibile_trattenute(
                righe, indice, valori, "imponibile_previdenziale", "contributi_inps"
            )
        elif _ADP_IRPEF.search(riga):
            _adp_imponibile_trattenute(
                righe, indice, valori, "imponibile_fiscale", "irpef_netta"
            )
        elif _ADP_PROGRESSIVI.search(riga):
            # La riga dei progressivi si chiude sempre con i due totali del
            # mese: contarli da destra evita di sbagliare quando una colonna
            # in mezzo (per esempio le detrazioni familiari) e' vuota.
            numeri = _valori_sotto(righe, indice)
            if len(numeri) >= 2:
                _imposta(valori, "totale_trattenute", numeri[-2])
                _imposta(valori, "totale_competenze", numeri[-1])
        elif _ADP_NETTO.search(riga):
            numeri = _valori_sotto(righe, indice, entro=3)
            if numeri:
                _imposta(valori, "netto", numeri[-1])
                if len(numeri) >= 2:
                    _imposta(valori, "arrotondamento", numeri[-2])
        elif _ADP_DETRAZIONI.search(riga):
            numeri = _valori_sotto(righe, indice)
            if len(numeri) >= 2:  # la prima colonna e' il numero di giorni
                _imposta(valori, "detrazioni", numeri[1])
        elif _ADP_TFR.search(riga):
            numeri = _valori_sotto(righe, indice)
            if numeri:
                _imposta(valori, "tfr_quota", numeri[0])
                if len(numeri) >= 2:
                    _imposta(valori, "tfr_totale", numeri[-1])
        elif _ADP_PAGA_BASE.search(riga):
            numeri = _valori_sotto(righe, indice)
            if numeri:
                _imposta(valori, "paga_base", numeri[0])
                if len(numeri) >= 2:
                    _imposta(valori, "superminimo", numeri[1])
        elif _ADP_TEORICA.search(riga):
            numeri = _valori_sotto(righe, indice)
            if numeri:
                _imposta(valori, "retribuzione_teorica", numeri[-1])
        elif _ADP_PRESENZE.search(riga):
            numeri = _numeri(riga)
            if numeri:
                _imposta(valori, "giorni_retribuiti", numeri[0])
                if len(numeri) >= 3:
                    _imposta(valori, "ore_lavorate", numeri[-1])
        elif _ADP_FERIE.match(riga):
            valori.update(_adp_ferie(righe, indice))
    return valori


def _adp_imponibile_trattenute(
    righe: list[str],
    indice: int,
    valori: dict[str, float],
    chiave_imponibile: str,
    chiave_trattenute: str,
) -> None:
    """Blocco «SOCIALI I.N.P.S.» / «FISCALI IRPEF»: imponibile e trattenuta."""
    for riga in righe[indice + 1 : indice + 4]:
        numeri = _numeri(riga)
        if not numeri:
            continue
        if re.search(r"^\s*IMPONIBILE", riga):
            _imposta(valori, chiave_imponibile, numeri[0])
        elif re.search(r"^\s*TRATTENUTE", riga):
            _imposta(valori, chiave_trattenute, numeri[0])


def _adp_ferie(righe: list[str], indice: int) -> dict[str, float]:
    """Blocco ferie/permessi: prima colonna ferie, seconda permessi (ROL).

    Il cedolino non stampa il residuo: lo si ricava da quello che risulta
    maturato piu' l'avanzo dell'anno prima, meno quello gia' goduto.
    """
    voci = {"SPETTANZA": [], "FRUITO": [], "MATURATO": [], "RESIDUI AP": []}
    for riga in righe[indice : indice + 10]:
        for etichetta in voci:
            if re.match(rf"\s*{etichetta}", riga):
                voci[etichetta] = _numeri(riga, fino_a_colonna=_ADP_COLONNA_FERIE)

    def colonna(nome: str, quale: int) -> float:
        numeri = voci[nome]
        return numeri[quale] if len(numeri) > quale else 0.0

    valori: dict[str, float] = {}
    for quale, (residue, maturate, godute) in enumerate(
        (
            ("ferie_residue", "ferie_maturate", "ferie_godute"),
            ("permessi_residui", "permessi_maturati", "permessi_goduti"),
        )
    ):
        maturato = colonna("MATURATO", quale)
        fruito = colonna("FRUITO", quale)
        avanzo = colonna("RESIDUI AP", quale)
        if not (maturato or fruito or avanzo):
            continue
        valori[maturate] = maturato
        valori[godute] = fruito
        valori[residue] = round(maturato + avanzo - fruito, 2)
    return valori


ADP_LIBRO_UNICO = Formato(
    nome="ADP - Libro Unico",
    riconosce=_adp_riconosce,
    estrai=_adp_estrai,
)

FORMATI: tuple[Formato, ...] = (ADP_LIBRO_UNICO,)
