"""Confronti tra buste paga e andamento complessivo."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from .modello import BustaPaga, formatta_euro, formatta_pct, formatta_periodo
from .pattern import (
    CAMPI,
    CAMPI_PER_CHIAVE,
    ETICHETTE_ANAGRAFICA,
    PATTERN_MENSILITA_AGGIUNTIVA,
)

SOGLIA_ANOMALIA = 10.0  # variazione percentuale oltre la quale segnalare
SCARTO_ALIQUOTA = 8.0  # punti di prelievo di scarto dalla mediana prima di segnalare


@dataclass
class Variazione:
    chiave: str
    etichetta: str
    unita: str
    precedente: float | None
    corrente: float | None

    @property
    def delta(self) -> float | None:
        if self.precedente is None or self.corrente is None:
            return None
        return round(self.corrente - self.precedente, 2)

    @property
    def delta_pct(self) -> float | None:
        if self.precedente in (None, 0) or self.corrente is None:
            return None
        return round(100.0 * (self.corrente - self.precedente) / abs(self.precedente), 2)

    @property
    def rilevante(self) -> bool:
        delta = self.delta
        return delta is not None and abs(delta) >= 0.01


@dataclass
class Confronto:
    """Una busta messa a fianco della precedente."""

    corrente: BustaPaga
    precedente: BustaPaga | None
    variazioni: list[Variazione] = field(default_factory=list)
    voci_nuove: list[str] = field(default_factory=list)
    voci_sparite: list[str] = field(default_factory=list)

    @property
    def variazioni_rilevanti(self) -> list[Variazione]:
        return [v for v in self.variazioni if v.rilevante]


@dataclass
class Statistica:
    chiave: str
    etichetta: str
    unita: str
    valori: list[tuple[date | None, float]]

    @property
    def numeri(self) -> list[float]:
        return [v for _, v in self.valori]

    @property
    def conteggio(self) -> int:
        return len(self.valori)

    @property
    def totale(self) -> float:
        return round(sum(self.numeri), 2)

    @property
    def media(self) -> float:
        return round(statistics.fmean(self.numeri), 2)

    @property
    def mediana(self) -> float:
        return round(statistics.median(self.numeri), 2)

    @property
    def deviazione(self) -> float:
        if self.conteggio < 2:
            return 0.0
        return round(statistics.stdev(self.numeri), 2)

    @property
    def minimo(self) -> tuple[date | None, float]:
        return min(self.valori, key=lambda v: v[1])

    @property
    def massimo(self) -> tuple[date | None, float]:
        return max(self.valori, key=lambda v: v[1])


@dataclass
class Anomalia:
    periodo: date | None
    titolo: str
    dettaglio: str
    gravita: str = "attenzione"  # "attenzione" | "seria"


@dataclass
class Analisi:
    buste: list[BustaPaga]
    confronti: list[Confronto]
    statistiche: dict[str, Statistica]
    anomalie: list[Anomalia]
    anni: dict[int, dict[str, float]]
    mensilita_aggiuntive: set[date]
    mesi_parziali: set[date]
    soglia: float = SOGLIA_ANOMALIA

    # --- viste comode per i report ------------------------------------------

    def serie(self, chiave: str, solo_mesi_pieni: bool = False) -> list[tuple[date, float]]:
        """I valori di una voce nel tempo.

        `solo_mesi_pieni` toglie le mensilita' aggiuntive e i mesi incompleti
        (assunzione, cessazione, aspettativa): servono a leggere la tendenza,
        che altrimenti verrebbe dettata da quei due o tre mesi anomali.
        """
        punti = []
        for busta in self.buste:
            valore = busta.get(chiave)
            if busta.periodo is None or valore is None:
                continue
            if solo_mesi_pieni and busta.periodo in self.mesi_irregolari:
                continue
            punti.append((busta.periodo, valore))
        return punti

    @property
    def mesi_irregolari(self) -> set[date]:
        return self.mensilita_aggiuntive | self.mesi_parziali

    def serie_aliquota(self) -> list[tuple[date, float]]:
        return [
            (b.periodo, b.aliquota_effettiva)
            for b in self.buste
            if b.periodo is not None and b.aliquota_effettiva is not None
        ]

    def chiavi_presenti(self) -> list[str]:
        """Campi trovati in almeno una busta, nell'ordine del registro."""
        return [c.chiave for c in CAMPI if c.chiave in self.statistiche]

    @property
    def inquadramento(self) -> str:
        """Azienda / CCNL / livello dell'ultima busta, se leggibili."""
        for busta in reversed(self.buste):
            if busta.anagrafica:
                return " · ".join(
                    f"{nome}: {busta.anagrafica[chiave].title()}"
                    for chiave, nome in ETICHETTE_ANAGRAFICA.items()
                    if chiave in busta.anagrafica
                )
        return ""

    @property
    def periodo_coperto(self) -> str:
        periodi = [b.periodo for b in self.buste if b.periodo]
        if not periodi:
            return "periodo non determinato"
        if len(periodi) == 1:
            return formatta_periodo(periodi[0])
        return f"{formatta_periodo(min(periodi))} → {formatta_periodo(max(periodi))}"

    @property
    def tendenza_netto(self) -> tuple[float, float] | None:
        """(euro al mese, variazione % totale) sul netto, solo mesi pieni."""
        punti = self.serie("netto", solo_mesi_pieni=True)
        if len(punti) < 3:
            return None
        x = list(range(len(punti)))
        y = [v for _, v in punti]
        pendenza = _pendenza(x, y)
        if y[0] == 0:
            return (round(pendenza, 2), 0.0)
        return (round(pendenza, 2), round(100.0 * (y[-1] - y[0]) / abs(y[0]), 2))


def _pendenza(x: list[float], y: list[float]) -> float:
    """Regressione lineare minima: pendenza della retta ai minimi quadrati."""
    media_x = statistics.fmean(x)
    media_y = statistics.fmean(y)
    numeratore = sum((xi - media_x) * (yi - media_y) for xi, yi in zip(x, y, strict=True))
    denominatore = sum((xi - media_x) ** 2 for xi in x)
    return numeratore / denominatore if denominatore else 0.0


# ---------------------------------------------------------------------------


def analizza(buste: list[BustaPaga], soglia: float = SOGLIA_ANOMALIA) -> Analisi:
    buste = [b for b in buste if b.valori or b.periodo]
    extra = _mensilita_aggiuntive(buste)
    parziali = _mesi_parziali(buste)
    confronti = _confronti(buste)
    statistiche = _statistiche(buste)
    analisi = Analisi(
        buste=buste,
        confronti=confronti,
        statistiche=statistiche,
        anomalie=[],
        anni=_totali_per_anno(buste),
        mensilita_aggiuntive=extra,
        mesi_parziali=parziali,
        soglia=soglia,
    )
    analisi.anomalie = _anomalie(analisi)
    return analisi


def _confronti(buste: list[BustaPaga]) -> list[Confronto]:
    prima_di, dopo_di = _voci_intorno(buste)
    confronti: list[Confronto] = []
    for indice, busta in enumerate(buste):
        precedente = buste[indice - 1] if indice else None
        variazioni = []
        for campo in CAMPI:
            valore_corrente = busta.get(campo.chiave)
            valore_precedente = precedente.get(campo.chiave) if precedente else None
            if valore_corrente is None and valore_precedente is None:
                continue
            variazioni.append(
                Variazione(
                    chiave=campo.chiave,
                    etichetta=campo.etichetta,
                    unita=campo.unita,
                    precedente=valore_precedente,
                    corrente=valore_corrente,
                )
            )
        nuove, sparite = _differenza_voci(buste, indice, prima_di, dopo_di)
        confronti.append(
            Confronto(
                corrente=busta,
                precedente=precedente,
                variazioni=variazioni,
                voci_nuove=nuove,
                voci_sparite=sparite,
            )
        )
    return confronti


def _voci_intorno(
    buste: list[BustaPaga],
) -> tuple[list[set[str]], list[set[str]]]:
    """Per ogni busta: le voci gia' viste prima e quelle che torneranno dopo."""
    descrizioni = [{v.descrizione for v in b.voci} for b in buste]
    prima_di: list[set[str]] = []
    accumulate: set[str] = set()
    for voci in descrizioni:
        prima_di.append(set(accumulate))
        accumulate |= voci
    dopo_di: list[set[str]] = [set() for _ in buste]
    accumulate = set()
    for indice in range(len(buste) - 1, -1, -1):
        dopo_di[indice] = set(accumulate)
        accumulate |= descrizioni[indice]
    return prima_di, dopo_di


def _differenza_voci(
    buste: list[BustaPaga],
    indice: int,
    prima_di: list[set[str]],
    dopo_di: list[set[str]],
) -> tuple[list[str], list[str]]:
    """Voci mai viste prima, e voci che da qui in poi non tornano piu'.

    Segnalare tutto quello che cambia da un mese all'altro sommergerebbe di
    rumore: straordinari, indennita' e rimborsi vanno e vengono per natura.
    Sono notizie solo la prima comparsa e la sparizione definitiva.
    """
    if indice == 0 or not buste[indice].voci or not buste[indice - 1].voci:
        return [], []
    correnti = {v.descrizione for v in buste[indice].voci}
    precedenti = {v.descrizione for v in buste[indice - 1].voci}
    nuove = correnti - prima_di[indice]
    sparite = precedenti - correnti - dopo_di[indice]
    return sorted(nuove), sorted(sparite)


def _statistiche(buste: list[BustaPaga]) -> dict[str, Statistica]:
    statistiche: dict[str, Statistica] = {}
    for campo in CAMPI:
        valori = [
            (b.periodo, b.valori[campo.chiave]) for b in buste if campo.chiave in b.valori
        ]
        if valori:
            statistiche[campo.chiave] = Statistica(
                chiave=campo.chiave,
                etichetta=campo.etichetta,
                unita=campo.unita,
                valori=valori,
            )
    return statistiche


def _totali_per_anno(buste: list[BustaPaga]) -> dict[int, dict[str, float]]:
    anni: dict[int, dict[str, float]] = {}
    for busta in buste:
        if busta.periodo is None:
            continue
        riga = anni.setdefault(busta.periodo.year, {"buste": 0.0})
        riga["buste"] += 1
        for chiave, valore in busta.valori.items():
            if CAMPI_PER_CHIAVE[chiave].unita == "€":
                riga[chiave] = round(riga.get(chiave, 0.0) + valore, 2)
    return dict(sorted(anni.items()))


def _mensilita_aggiuntive(buste: list[BustaPaga]) -> set[date]:
    """Mesi con tredicesima/quattordicesima: vanno esclusi dal confronto "normale"."""
    extra: set[date] = set()
    netti = [b.get("netto") for b in buste if b.get("netto") is not None]
    mediana = statistics.median(netti) if netti else None
    for busta in buste:
        if busta.periodo is None:
            continue
        if any(busta.get(chiave) for chiave in PATTERN_MENSILITA_AGGIUNTIVA):
            extra.add(busta.periodo)
            continue
        netto = busta.get("netto")
        if mediana and netto and netto > mediana * 1.4:
            extra.add(busta.periodo)
    return extra


def _plurale(quante: int, singolare: str, plurale: str) -> str:
    return f"{quante} {singolare if quante == 1 else plurale}"


def _mesi_parziali(buste: list[BustaPaga]) -> set[date]:
    """Mesi lavorati solo in parte: il netto crolla senza che sia cambiato nulla.

    Si riconoscono dal netto molto sotto la mediana; e' il caso del mese di
    assunzione o di cessazione, e delle assenze non retribuite.
    """
    netti = [b.get("netto") for b in buste if b.get("netto") is not None]
    if len(netti) < 3:
        return set()
    mediana = statistics.median(netti)
    return {
        b.periodo
        for b in buste
        if b.periodo is not None
        and b.get("netto") is not None
        and b.get("netto") < mediana * 0.6
    }


def _anomalie(analisi: Analisi) -> list[Anomalia]:
    anomalie: list[Anomalia] = []

    for busta in analisi.buste:
        for avviso in busta.avvisi:
            anomalie.append(
                Anomalia(
                    periodo=busta.periodo,
                    titolo=f"Lettura di {busta.file}",
                    dettaglio=avviso,
                    gravita="seria" if "non tornano" in avviso else "attenzione",
                )
            )

    for confronto in analisi.confronti:
        if confronto.precedente is None:
            continue
        periodo = confronto.corrente.periodo
        irregolare = (
            periodo in analisi.mesi_irregolari
            or confronto.precedente.periodo in analisi.mesi_irregolari
        )
        for variazione in confronto.variazioni:
            if variazione.chiave not in ("netto", "totale_competenze"):
                continue
            delta_pct = variazione.delta_pct
            if delta_pct is None or abs(delta_pct) < analisi.soglia:
                continue
            if irregolare:
                continue  # mensilita' extra o mese parziale: lo scarto e' atteso
            verso = "in aumento" if delta_pct > 0 else "in calo"
            anomalie.append(
                Anomalia(
                    periodo=periodo,
                    titolo=f"{variazione.etichetta} {verso} del {formatta_pct(abs(delta_pct))}",
                    dettaglio=(
                        f"da {formatta_euro(variazione.precedente)} € "
                        f"({formatta_periodo(confronto.precedente.periodo)}) "
                        f"a {formatta_euro(variazione.corrente)} € "
                        f"({formatta_periodo(periodo)})"
                    ),
                )
            )
        if confronto.voci_nuove:
            anomalie.append(
                Anomalia(
                    periodo=periodo,
                    titolo=_plurale(len(confronto.voci_nuove), "voce mai vista prima", "voci mai viste prima"),
                    dettaglio=", ".join(confronto.voci_nuove),
                )
            )
        if confronto.voci_sparite:
            anomalie.append(
                Anomalia(
                    periodo=periodo,
                    titolo=_plurale(len(confronto.voci_sparite), "voce non compare piu'", "voci non compaiono piu'"),
                    dettaglio=", ".join(confronto.voci_sparite),
                )
            )

    for confronto in analisi.confronti:
        if confronto.precedente is None:
            continue
        for chiave, nome in ETICHETTE_ANAGRAFICA.items():
            prima = confronto.precedente.anagrafica.get(chiave)
            dopo = confronto.corrente.anagrafica.get(chiave)
            if prima and dopo and prima != dopo:
                anomalie.append(
                    Anomalia(
                        periodo=confronto.corrente.periodo,
                        titolo=f"{nome} cambiato",
                        dettaglio=f"da «{prima}» a «{dopo}»",
                    )
                )

    for periodo in sorted(analisi.mesi_parziali):
        anomalie.append(
            Anomalia(
                periodo=periodo,
                titolo="Mese lavorato solo in parte",
                dettaglio=(
                    "il netto e' molto sotto la media: assunzione, cessazione "
                    "o assenze non retribuite. Escluso dal calcolo della tendenza."
                ),
            )
        )

    aliquote = [a for _, a in analisi.serie_aliquota()]
    if len(aliquote) >= 3:
        mediana = statistics.median(aliquote)
        for periodo, aliquota in analisi.serie_aliquota():
            # I mesi irregolari hanno gia' la loro segnalazione, e un'aliquota
            # diversa e' proprio quello che ci si aspetta.
            if periodo in analisi.mesi_irregolari:
                continue
            if abs(aliquota - mediana) > SCARTO_ALIQUOTA:
                anomalie.append(
                    Anomalia(
                        periodo=periodo,
                        titolo=f"Prelievo fuori linea: {formatta_pct(aliquota)}",
                        dettaglio=(
                            f"la mediana del periodo e' {formatta_pct(mediana)}: "
                            "di solito dipende da un conguaglio fiscale"
                        ),
                    )
                )

    numero_buste = len(analisi.buste)
    for chiave, statistica in analisi.statistiche.items():
        if chiave not in ("netto", "totale_competenze", "contributi_inps"):
            continue
        if 0 < statistica.conteggio < numero_buste:
            mancanti = numero_buste - statistica.conteggio
            anomalie.append(
                Anomalia(
                    periodo=None,
                    titolo=f"«{statistica.etichetta}» non letto in {mancanti} buste",
                    dettaglio="probabile etichetta diversa: aggiungila in bustepaga/pattern.py",
                )
            )

    return anomalie
