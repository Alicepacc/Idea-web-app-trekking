"""Report a terminale: tabelle, confronti, andamento."""

from __future__ import annotations

from .analisi import Analisi, Confronto
from .modello import (
    formatta_euro,
    formatta_pct,
    formatta_periodo,
    formatta_valore,
    periodo_breve,
)
from .pattern import CAMPI_PER_CHIAVE, EURO, GRUPPI


def _tabella(intestazioni: list[str], righe: list[list[str]], allinea_dx: set[int]) -> str:
    if not righe:
        return "  (nessun dato)\n"
    larghezze = [len(i) for i in intestazioni]
    for riga in righe:
        for indice, cella in enumerate(riga):
            larghezze[indice] = max(larghezze[indice], len(cella))

    def formatta(riga: list[str]) -> str:
        celle = [
            cella.rjust(larghezze[i]) if i in allinea_dx else cella.ljust(larghezze[i])
            for i, cella in enumerate(riga)
        ]
        return "  " + " │ ".join(celle)

    separatore = "  " + "─┼─".join("─" * larghezza for larghezza in larghezze)
    return "\n".join([formatta(intestazioni), separatore, *(formatta(r) for r in righe)]) + "\n"


def _titolo(testo: str) -> str:
    return f"\n\033[1m{testo}\033[0m\n" + "─" * (len(testo) + 2) + "\n"


def _delta(valore: float | None, unita: str = EURO) -> str:
    if valore is None:
        return "—"
    segno = "+" if valore > 0 else ""
    return segno + formatta_valore(valore, unita)


def _delta_pct(valore: float | None) -> str:
    return formatta_pct(valore, segno=True)


def riepilogo_mensile(analisi: Analisi) -> str:
    righe = []
    for confronto in analisi.confronti:
        busta = confronto.corrente
        netto = busta.get("netto")
        precedente = confronto.precedente.get("netto") if confronto.precedente else None
        delta = None if netto is None or precedente is None else netto - precedente
        delta_pct = (
            None if not precedente or netto is None else 100.0 * (netto - precedente) / abs(precedente)
        )
        marcatore = (
            " ✱"
            if busta.periodo in analisi.mensilita_aggiuntive
            else " ◦"
            if busta.periodo in analisi.mesi_parziali
            else ""
        )
        aliquota = busta.aliquota_effettiva
        righe.append(
            [
                periodo_breve(busta.periodo) + marcatore,
                formatta_euro(busta.get("totale_competenze")),
                formatta_euro(busta.get("totale_trattenute")),
                formatta_euro(netto),
                _delta(delta),
                _delta_pct(delta_pct),
                formatta_pct(aliquota),
            ]
        )
    testo = _tabella(
        ["Mese", "Lordo", "Trattenute", "Netto", "Δ netto", "Δ %", "Prelievo"],
        righe,
        allinea_dx={1, 2, 3, 4, 5, 6},
    )
    if analisi.mensilita_aggiuntive:
        testo += "\n  ✱ mese fuori scala: tredicesima, premio o arretrati\n"
    if analisi.mesi_parziali:
        testo += "  ◦ mese lavorato solo in parte (assunzione, cessazione, assenze)\n"
    return testo


def statistiche(analisi: Analisi) -> str:
    righe = []
    for chiave in analisi.chiavi_presenti():
        statistica = analisi.statistiche[chiave]
        campo = CAMPI_PER_CHIAVE[chiave]
        formatta = formatta_euro  # l'unita' ha la sua colonna, qui basta il numero
        periodo_min, valore_min = statistica.minimo
        periodo_max, valore_max = statistica.massimo
        righe.append(
            [
                statistica.etichetta,
                campo.unita,
                str(statistica.conteggio),
                formatta(statistica.media),
                formatta(statistica.mediana),
                f"{formatta(valore_min)} ({periodo_breve(periodo_min)})",
                f"{formatta(valore_max)} ({periodo_breve(periodo_max)})",
                formatta(statistica.totale),
            ]
        )
    return _tabella(
        ["Voce", "Un.", "n.", "Media", "Mediana", "Minimo", "Massimo", "Totale periodo"],
        righe,
        allinea_dx={2, 3, 4, 5, 6, 7},
    )


def andamento(analisi: Analisi) -> str:
    parti: list[str] = []
    tendenza = analisi.tendenza_netto
    if tendenza:
        pendenza, variazione = tendenza
        verso = "in crescita" if pendenza > 0 else ("in calo" if pendenza < 0 else "stabile")
        parti.append(
            f"  Netto {verso}: {_delta(pendenza)} € al mese in media "
            f"({_delta_pct(variazione)} dal primo all'ultimo mese pieno; "
            "mensilità aggiuntive e mesi parziali esclusi)."
        )
    statistica_netto = analisi.statistiche.get("netto")
    if statistica_netto:
        parti.append(
            f"  Netto medio {formatta_euro(statistica_netto.media)} €, "
            f"mediana {formatta_euro(statistica_netto.mediana)} €, "
            f"oscillazione tipica ±{formatta_euro(statistica_netto.deviazione)} €."
        )
        if statistica_netto.conteggio >= 6:
            proiezione = statistica_netto.media * 12
            parti.append(
                f"  Proiezione su 12 mesi al ritmo attuale: "
                f"{formatta_euro(proiezione)} € netti."
            )
    aliquote = [a for _, a in analisi.serie_aliquota()]
    if aliquote:
        media = sum(aliquote) / len(aliquote)
        parti.append(
            f"  Prelievo medio (contributi + imposte): {formatta_pct(media)} del lordo."
        )
    return "\n".join(parti) + "\n" if parti else "  (dati insufficienti)\n"


def totali_annui(analisi: Analisi) -> str:
    if not analisi.anni:
        return "  (nessun anno completo)\n"
    chiavi = [
        c
        for c in ("totale_competenze", "totale_trattenute", "netto", "contributi_inps", "irpef_netta")
        if any(c in dati for dati in analisi.anni.values())
    ]
    righe = []
    for anno, dati in analisi.anni.items():
        righe.append(
            [str(anno), f"{int(dati['buste'])}"]
            + [formatta_euro(dati.get(chiave)) for chiave in chiavi]
        )
    intestazioni = ["Anno", "Buste"] + [CAMPI_PER_CHIAVE[c].etichetta for c in chiavi]
    return _tabella(intestazioni, righe, allinea_dx=set(range(1, len(intestazioni))))


def dettaglio_confronto(confronto: Confronto) -> str:
    busta = confronto.corrente
    if confronto.precedente is None:
        return f"  {formatta_periodo(busta.periodo)}: nessuna busta precedente con cui confrontare.\n"

    titolo = (
        f"  {formatta_periodo(confronto.precedente.periodo)} → {formatta_periodo(busta.periodo)}\n\n"
    )
    righe = []
    for gruppo, nome_gruppo in GRUPPI.items():
        variazioni = [
            v
            for v in confronto.variazioni_rilevanti
            if CAMPI_PER_CHIAVE[v.chiave].gruppo == gruppo
        ]
        if not variazioni:
            continue
        righe.append([f"— {nome_gruppo} —", "", "", "", ""])
        for variazione in variazioni:
            def formatta(valore: float | None, unita: str = variazione.unita) -> str:
                return formatta_valore(valore, unita)

            righe.append(
                [
                    variazione.etichetta,
                    formatta(variazione.precedente),
                    formatta(variazione.corrente),
                    _delta(variazione.delta, variazione.unita),
                    _delta_pct(variazione.delta_pct),
                ]
            )
    testo = titolo + _tabella(
        ["Voce", "Prima", "Dopo", "Δ", "Δ %"], righe, allinea_dx={1, 2, 3, 4}
    )
    if confronto.voci_nuove:
        testo += "\n  Voci comparse: " + ", ".join(confronto.voci_nuove) + "\n"
    if confronto.voci_sparite:
        testo += "  Voci sparite: " + ", ".join(confronto.voci_sparite) + "\n"
    return testo


def anomalie(analisi: Analisi) -> str:
    if not analisi.anomalie:
        return "  Nessuna segnalazione: tutte le buste sono coerenti tra loro.\n"
    righe = []
    for anomalia in analisi.anomalie:
        simbolo = "!!" if anomalia.gravita == "seria" else "! "
        righe.append(
            f"  {simbolo} [{periodo_breve(anomalia.periodo) if anomalia.periodo else '  -  '}] "
            f"{anomalia.titolo}\n       {anomalia.dettaglio}"
        )
    return "\n".join(righe) + "\n"


def report_completo(analisi: Analisi) -> str:
    parti = [
        _titolo(f"Buste paga analizzate: {len(analisi.buste)} — {analisi.periodo_coperto}"),
        f"  {analisi.inquadramento}\n" if analisi.inquadramento else "",
        _titolo("Riepilogo mese per mese"),
        riepilogo_mensile(analisi),
        _titolo("Andamento complessivo"),
        andamento(analisi),
        _titolo("Statistiche per voce"),
        statistiche(analisi),
        _titolo("Totali per anno"),
        totali_annui(analisi),
    ]
    if analisi.confronti:
        parti.append(_titolo("Ultimo confronto mese su mese"))
        parti.append(dettaglio_confronto(analisi.confronti[-1]))
    parti.append(_titolo("Da controllare"))
    parti.append(anomalie(analisi))
    return "".join(parti)
