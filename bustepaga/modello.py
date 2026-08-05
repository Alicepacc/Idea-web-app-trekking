"""Modello dati e utilita' di conversione per le buste paga italiane."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

MESI = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}
MESI_ABBR = {nome[:3]: numero for nome, numero in MESI.items()}
NOMI_MESE = {numero: nome.capitalize() for nome, numero in MESI.items()}

# Numero all'italiana: 1.234,56 / 1234,56 / 1.234 / 45 / 12,5
# Il segno meno puo' precedere il numero oppure seguirlo (uso contabile).
_NUMERO = re.compile(
    r"(?<![\w.,])(-)?(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(-)?(?![\w,])"
)


def normalizza_testo(testo: str) -> str:
    """Maiuscole e accenti rimossi, ma spaziatura INTATTA.

    L'incolonnamento e' informazione: dice se un numero sta nella colonna
    "competenze" o in quella "trattenute". Per questo i doppi spazi non vanno
    compattati (i pattern usano comunque `\\s+`).
    """
    senza_accenti = "".join(
        c
        for c in unicodedata.normalize("NFD", testo.expandtabs(4))
        if unicodedata.category(c) != "Mn"
    )
    return senza_accenti.replace("\xa0", " ").upper()


def numeri_in_riga(riga: str) -> list[float]:
    """Tutti i numeri in formato italiano presenti nella riga."""
    valori: list[float] = []
    for meno_prima, intero, decimali, meno_dopo in _NUMERO.findall(riga):
        valore = float(intero.replace(".", ""))
        if decimali:
            valore += float(f"0.{decimali}")
        if meno_prima or meno_dopo:
            valore = -valore
        valori.append(valore)
    return valori


def a_numero(testo: str) -> float | None:
    """Converte una singola stringa numerica italiana in float."""
    valori = numeri_in_riga(testo)
    return valori[0] if len(valori) == 1 else None


def formatta_euro(valore: float | None, decimali: int = 2) -> str:
    if valore is None:
        return "—"
    testo = f"{valore:,.{decimali}f}"
    # da 1,234.56 (formato inglese) a 1.234,56 (formato italiano)
    return testo.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def formatta_valore(valore: float | None, unita: str = "€", decimali: int | None = None) -> str:
    """Numero all'italiana con la sua unita' (2.345,60 € / 168,00 ore / 29,4%)."""
    if valore is None:
        return "—"
    if unita == "%":
        return formatta_euro(valore, 1 if decimali is None else decimali) + "%"
    testo = formatta_euro(valore, 2 if decimali is None else decimali)
    return f"{testo} {unita}" if unita and unita != "€" else testo


def formatta_pct(valore: float | None, segno: bool = False) -> str:
    """Percentuale all'italiana: 29,5% oppure +3,8%."""
    if valore is None:
        return "—"
    testo = formatta_euro(valore, 1) + "%"
    return "+" + testo if segno and valore > 0 else testo


def formatta_periodo(periodo: date | None) -> str:
    if periodo is None:
        return "periodo ignoto"
    return f"{NOMI_MESE[periodo.month]} {periodo.year}"


def periodo_breve(periodo: date | None) -> str:
    if periodo is None:
        return "??"
    return f"{periodo.month:02d}/{str(periodo.year)[2:]}"


@dataclass
class Voce:
    """Riga di dettaglio della busta (una singola voce di competenza o trattenuta)."""

    descrizione: str
    competenza: float | None = None
    trattenuta: float | None = None
    # il cedolino numera le voci di paga: distingue una voce vera da una
    # riga di riepilogo
    con_codice: bool = False

    @property
    def importo(self) -> float:
        return (self.competenza or 0.0) - (self.trattenuta or 0.0)


@dataclass
class BustaPaga:
    """Una busta paga normalizzata.

    `valori` contiene le chiavi definite in `pattern.CAMPI`; le chiavi assenti
    significano "non trovata nel PDF", non "pari a zero".
    """

    file: str
    periodo: date | None = None
    valori: dict[str, float] = field(default_factory=dict)
    voci: list[Voce] = field(default_factory=list)
    anagrafica: dict[str, str] = field(default_factory=dict)
    formato: str = ""
    avvisi: list[str] = field(default_factory=list)
    testo: str = ""

    def get(self, chiave: str) -> float | None:
        return self.valori.get(chiave)

    @property
    def etichetta(self) -> str:
        return formatta_periodo(self.periodo)

    # --- grandezze derivate -------------------------------------------------

    @property
    def imposte(self) -> float | None:
        """Tutto quello che viene trattenuto oltre ai contributi.

        Quando si conoscono il totale trattenute e i contributi, la differenza
        e' esatta e quadra sempre col netto. Le singole voci d'imposta si
        prestano invece a doppi conteggi (rate, conguagli, importi da
        rateizzare), quindi si usano solo se non c'e' il totale.
        """
        trattenute = self.get("totale_trattenute")
        contributi = self.get("contributi_inps")
        if trattenute is not None and contributi is not None:
            return round(trattenute - contributi, 2)
        pezzi = [
            self.get("irpef_netta"),
            self.get("addizionale_regionale"),
            self.get("addizionale_comunale"),
        ]
        if all(p is None for p in pezzi):
            return None
        totale = sum(p for p in pezzi if p is not None)
        integrativo = self.get("trattamento_integrativo")
        if integrativo:
            totale -= integrativo
        return totale

    @property
    def aliquota_effettiva(self) -> float | None:
        """Percentuale del lordo che non arriva in tasca (contributi + imposte)."""
        lordo = self.get("totale_competenze")
        trattenute = self.get("totale_trattenute")
        if lordo is None or not lordo:
            return None
        if trattenute is None:
            contributi = self.get("contributi_inps")
            imposte = self.imposte
            if contributi is None and imposte is None:
                return None
            trattenute = (contributi or 0.0) + (imposte or 0.0)
        return 100.0 * trattenute / lordo

    @property
    def completa(self) -> bool:
        return self.get("netto") is not None and self.periodo is not None
