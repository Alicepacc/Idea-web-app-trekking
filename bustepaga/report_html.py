"""Report HTML autonomo (un solo file, nessuna risorsa esterna).

I grafici sono SVG generati qui: niente librerie, niente rete, il file si apre
con un doppio clic e funziona anche offline. Tema chiaro/scuro automatico.
"""

from __future__ import annotations

import html
import math
from datetime import date

from .analisi import Analisi
from .modello import (
    formatta_euro,
    formatta_pct,
    formatta_periodo,
    formatta_valore,
    periodo_breve,
)
from .pattern import CAMPI_PER_CHIAVE, EURO, GRUPPI

LARGHEZZA = 760
ALTEZZA = 320
MARGINE = {"su": 18, "giu": 38, "sx": 62, "dx": 18}

MESI_BREVI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]


def _et(periodo: date | None) -> str:
    if periodo is None:
        return "—"
    return f"{MESI_BREVI[periodo.month - 1]} {str(periodo.year)[2:]}"


def _esc(testo: object) -> str:
    return html.escape(str(testo), quote=True)


# ---------------------------------------------------------------------------
# assi
# ---------------------------------------------------------------------------


def _passo_pulito(intervallo: float, passi: int = 5) -> float:
    if intervallo <= 0:
        return 1.0
    grezzo = intervallo / passi
    magnitudo = 10 ** math.floor(math.log10(grezzo))
    for moltiplicatore in (1, 2, 2.5, 5, 10):
        if grezzo <= magnitudo * moltiplicatore:
            return magnitudo * moltiplicatore
    return magnitudo * 10


def _scala_y(massimo: float, minimo: float = 0.0) -> tuple[float, float, list[float]]:
    passo = _passo_pulito(massimo - minimo)
    alto = math.ceil(massimo / passo) * passo if massimo else passo
    basso = math.floor(minimo / passo) * passo
    if alto == basso:
        alto = basso + passo
    tacche = []
    valore = basso
    while valore <= alto + passo / 2:
        tacche.append(round(valore, 6))
        valore += passo
    return basso, alto, tacche


def _griglia(tacche: list[float], basso: float, alto: float, suffisso: str) -> str:
    pezzi = []
    for tacca in tacche:
        y = _y(tacca, basso, alto)
        pezzi.append(
            f'<line class="griglia" x1="{MARGINE["sx"]}" y1="{y:.1f}" '
            f'x2="{LARGHEZZA - MARGINE["dx"]}" y2="{y:.1f}"/>'
        )
        etichetta = (
            formatta_euro(tacca, 0)
            if suffisso == EURO
            else formatta_valore(tacca, suffisso, 0)
        )
        pezzi.append(
            f'<text class="tacca" x="{MARGINE["sx"] - 8}" y="{y + 4:.1f}" '
            f'text-anchor="end">{_esc(etichetta)}</text>'
        )
    return "".join(pezzi)


def _y(valore: float, basso: float, alto: float) -> float:
    utile = ALTEZZA - MARGINE["su"] - MARGINE["giu"]
    if alto == basso:
        return MARGINE["su"] + utile
    return MARGINE["su"] + utile * (1 - (valore - basso) / (alto - basso))


def _x_banda(indice: int, totale: int) -> tuple[float, float]:
    """(centro, larghezza della banda) per la colonna i-esima."""
    utile = LARGHEZZA - MARGINE["sx"] - MARGINE["dx"]
    banda = utile / max(totale, 1)
    return MARGINE["sx"] + banda * (indice + 0.5), banda


def _etichette_x(periodi: list[date | None], totale: int) -> str:
    salto = max(1, math.ceil(totale / 13))
    pezzi = []
    for indice, periodo in enumerate(periodi):
        if indice % salto:
            continue
        centro, _ = _x_banda(indice, totale)
        pezzi.append(
            f'<text class="tacca" x="{centro:.1f}" y="{ALTEZZA - MARGINE["giu"] + 20}" '
            f'text-anchor="middle">{_esc(_et(periodo))}</text>'
        )
    return "".join(pezzi)


def _linea_base(basso: float, alto: float) -> str:
    y = _y(max(basso, 0.0), basso, alto)
    return (
        f'<line class="base" x1="{MARGINE["sx"]}" y1="{y:.1f}" '
        f'x2="{LARGHEZZA - MARGINE["dx"]}" y2="{y:.1f}"/>'
    )


# ---------------------------------------------------------------------------
# grafici
# ---------------------------------------------------------------------------


def grafico_linee(
    titolo: str,
    sottotitolo: str,
    serie: list[tuple[str, list[tuple[date, float]]]],
    unita: str = EURO,
    da_zero: bool = True,
) -> str:
    """Una o piu' spezzate sullo stesso asse (mai due scale diverse)."""
    serie = [(nome, punti) for nome, punti in serie if punti]
    if not serie:
        return ""
    periodi = sorted({p for _, punti in serie for p, _ in punti})
    indice_di = {periodo: i for i, periodo in enumerate(periodi)}
    tutti = [v for _, punti in serie for _, v in punti]
    if da_zero:
        basso, alto, tacche = _scala_y(max(tutti), min(min(tutti), 0.0))
    else:
        # su una percentuale che oscilla di un punto, partire da zero
        # appiattirebbe la curva: l'asse resta etichettato, quindi si legge
        margine = max((max(tutti) - min(tutti)) * 0.35, 0.5)
        basso, alto, tacche = _scala_y(max(tutti) + margine, max(min(tutti) - margine, 0.0))

    corpo = [_griglia(tacche, basso, alto, unita), _linea_base(basso, alto)]
    for indice_serie, (_, punti) in enumerate(serie):
        colore = f"var(--serie-{indice_serie + 1})"
        coordinate = [
            (_x_banda(indice_di[p], len(periodi))[0], _y(v, basso, alto)) for p, v in punti
        ]
        percorso = " ".join(
            f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coordinate)
        )
        corpo.append(f'<path class="linea" d="{percorso}" stroke="{colore}"/>')
        for x, y in coordinate:
            corpo.append(f'<circle class="punto" cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colore}"/>')
        ultimo_x, ultimo_y = coordinate[-1]
        valore = punti[-1][1]
        testo = formatta_euro(valore, 0) if unita == EURO else formatta_valore(valore, unita)
        ancora = "end" if ultimo_x > LARGHEZZA - 90 else "start"
        scarto = -8 if ancora == "end" else 8
        corpo.append(
            f'<text class="etichetta-diretta" x="{ultimo_x + scarto:.1f}" '
            f'y="{ultimo_y - 10:.1f}" text-anchor="{ancora}">{_esc(testo)}</text>'
        )

    corpo.append(_etichette_x(periodi, len(periodi)))
    corpo.append(_bande_hover(periodi, serie, unita))
    legenda = _legenda([nome for nome, _ in serie]) if len(serie) > 1 else ""
    return _riquadro(titolo, sottotitolo, legenda, "".join(corpo))


def grafico_impilato(
    titolo: str,
    sottotitolo: str,
    periodi: list[date],
    serie: list[tuple[str, list[float | None]]],
) -> str:
    """Colonne impilate: come si divide il lordo tra netto, contributi e imposte."""
    if not periodi or not serie:
        return ""
    totali = [
        sum(valori[i] or 0.0 for _, valori in serie) for i in range(len(periodi))
    ]
    basso, alto, tacche = _scala_y(max(totali) if totali else 0.0)
    corpo = [_griglia(tacche, basso, alto, EURO), _linea_base(basso, alto)]

    for indice in range(len(periodi)):
        centro, banda = _x_banda(indice, len(periodi))
        larghezza = min(24.0, banda * 0.62)
        cumulato = 0.0
        for indice_serie, (_, valori) in enumerate(serie):
            valore = valori[indice]
            if not valore:
                continue
            y_sotto = _y(cumulato, basso, alto)
            y_sopra = _y(cumulato + valore, basso, alto)
            altezza = max(y_sotto - y_sopra - 2, 0.5)  # 2px di stacco tra i segmenti
            colore = f"var(--serie-{indice_serie + 1})"
            # angoli arrotondati solo sul segmento in cima alla pila
            in_cima = indice_serie == max(
                (j for j, (_, v) in enumerate(serie) if v[indice]), default=0
            )
            raggio = 4 if in_cima else 0
            corpo.append(
                f'<rect x="{centro - larghezza / 2:.1f}" y="{y_sopra:.1f}" '
                f'width="{larghezza:.1f}" height="{altezza:.1f}" rx="{raggio}" fill="{colore}"/>'
            )
            cumulato += valore

    corpo.append(_etichette_x(list(periodi), len(periodi)))
    corpo.append(
        _bande_hover(
            periodi,
            [(nome, [(p, v) for p, v in zip(periodi, valori, strict=True) if v is not None]) for nome, valori in serie],
            EURO,
            totale=True,
        )
    )
    return _riquadro(titolo, sottotitolo, _legenda([nome for nome, _ in serie]), "".join(corpo))


def _bande_hover(
    periodi: list[date],
    serie: list[tuple[str, list[tuple[date, float]]]],
    unita: str,
    totale: bool = False,
) -> str:
    """Zone di aggancio a tutta altezza: il puntatore non deve centrare il segno."""
    pezzi = []
    for indice, periodo in enumerate(periodi):
        centro, banda = _x_banda(indice, len(periodi))
        righe = [f"<strong>{_esc(formatta_periodo(periodo))}</strong>"]
        somma = 0.0
        for indice_serie, (nome, punti) in enumerate(serie):
            valore = next((v for p, v in punti if p == periodo), None)
            if valore is None:
                continue
            somma += valore
            testo = f"{formatta_euro(valore)} €" if unita == EURO else formatta_valore(valore, unita)
            righe.append(
                f'<span class="chiave" style="background:var(--serie-{indice_serie + 1})"></span>'
                f"{_esc(nome)}: <b>{_esc(testo)}</b>"
            )
        if totale and len(serie) > 1:
            righe.append(f"Totale: <b>{_esc(formatta_euro(somma))} €</b>")
        pezzi.append(
            f'<rect class="banda" x="{centro - banda / 2:.1f}" y="{MARGINE["su"]}" '
            f'width="{banda:.1f}" height="{ALTEZZA - MARGINE["su"] - MARGINE["giu"]:.1f}" '
            f'data-tip="{_esc("<br>".join(righe))}"/>'
        )
    return "".join(pezzi)


def _legenda(nomi: list[str]) -> str:
    voci = "".join(
        f'<span class="voce-legenda"><span class="chiave" '
        f'style="background:var(--serie-{i + 1})"></span>{_esc(nome)}</span>'
        for i, nome in enumerate(nomi)
    )
    return f'<div class="legenda">{voci}</div>'


def _riquadro(titolo: str, sottotitolo: str, legenda: str, corpo: str) -> str:
    return f"""
<figure class="scheda grafico">
  <figcaption>
    <h3>{_esc(titolo)}</h3>
    <p>{_esc(sottotitolo)}</p>
  </figcaption>
  {legenda}
  <svg viewBox="0 0 {LARGHEZZA} {ALTEZZA}" role="img" preserveAspectRatio="xMidYMid meet">{corpo}</svg>
</figure>"""


# ---------------------------------------------------------------------------
# blocchi non grafici
# ---------------------------------------------------------------------------


def _riquadri_sintesi(analisi: Analisi) -> str:
    statistica = analisi.statistiche.get("netto")
    tessere = []

    if statistica:
        ultimo = analisi.confronti[-1] if analisi.confronti else None
        delta = None
        if ultimo and ultimo.precedente:
            corrente = ultimo.corrente.get("netto")
            precedente = ultimo.precedente.get("netto")
            if corrente is not None and precedente:
                delta = 100.0 * (corrente - precedente) / abs(precedente)
        valore_ultimo = ultimo.corrente.get("netto") if ultimo else None
        nota = f"{formatta_pct(delta, segno=True)} sul mese prima" if delta is not None else ""
        if ultimo and ultimo.corrente.periodo in analisi.mensilita_aggiuntive:
            nota += " · mensilità aggiuntiva"
        tessere.append(
            _tessera(
                "Netto ultimo mese",
                f"{formatta_euro(valore_ultimo)} €" if valore_ultimo is not None else "—",
                nota,
                "su" if (delta or 0) > 0 else ("giu" if (delta or 0) < 0 else ""),
            )
        )
        tessere.append(_tessera("Netto medio", f"{formatta_euro(statistica.media)} €", f"su {statistica.conteggio} buste"))
        tessere.append(_tessera("Totale netto del periodo", f"{formatta_euro(statistica.totale)} €", analisi.periodo_coperto))

    aliquote = [a for _, a in analisi.serie_aliquota()]
    if aliquote:
        media = sum(aliquote) / len(aliquote)
        tessere.append(_tessera("Prelievo medio", formatta_pct(media), "contributi + imposte sul lordo"))

    tendenza = analisi.tendenza_netto
    if tendenza:
        pendenza, _ = tendenza
        tessere.append(
            _tessera(
                "Tendenza del netto",
                f"{'+' if pendenza > 0 else ''}{formatta_euro(pendenza)} €",
                "al mese, mensilità extra escluse",
                "su" if pendenza > 0 else ("giu" if pendenza < 0 else ""),
            )
        )
    return f'<section class="tessere">{"".join(tessere)}</section>'


def _tessera(etichetta: str, valore: str, nota: str = "", verso: str = "") -> str:
    classe = f" delta-{verso}" if verso else ""
    riga_nota = f'<p class="nota{classe}">{_esc(nota)}</p>' if nota else ""
    return f"""<div class="scheda tessera">
      <p class="etichetta">{_esc(etichetta)}</p>
      <p class="valore">{_esc(valore)}</p>{riga_nota}
    </div>"""


def _tabella_mensile(analisi: Analisi) -> str:
    righe = []
    for confronto in analisi.confronti:
        busta = confronto.corrente
        netto = busta.get("netto")
        precedente = confronto.precedente.get("netto") if confronto.precedente else None
        delta = None if netto is None or precedente is None else netto - precedente
        pct = None if not precedente or netto is None else 100.0 * (netto - precedente) / abs(precedente)
        aliquota = busta.aliquota_effettiva
        classe = "delta-su" if (delta or 0) > 0 else ("delta-giu" if (delta or 0) < 0 else "")
        extra = ' <span class="pallino" title="mensilità aggiuntiva">✱</span>' if busta.periodo in analisi.mensilita_aggiuntive else ""
        righe.append(
            f"<tr><th scope='row'>{_esc(formatta_periodo(busta.periodo))}{extra}</th>"
            f"<td>{formatta_euro(busta.get('totale_competenze'))}</td>"
            f"<td>{formatta_euro(busta.get('contributi_inps'))}</td>"
            f"<td>{formatta_euro(busta.imposte)}</td>"
            f"<td>{formatta_euro(busta.get('totale_trattenute'))}</td>"
            f"<td><b>{formatta_euro(netto)}</b></td>"
            f"<td class='{classe}'>{('+' if (delta or 0) > 0 else '') + formatta_euro(delta) if delta is not None else '—'}</td>"
            f"<td class='{classe}'>{formatta_pct(pct, segno=True)}</td>"
            f"<td>{formatta_pct(aliquota)}</td></tr>"
        )
    return f"""
<section class="scheda">
  <h3>Tutti i dati, mese per mese</h3>
  <p class="sottotitolo">Gli stessi numeri dei grafici, in forma leggibile e copiabile.</p>
  <div class="scorri">
  <table>
    <thead><tr><th scope="col">Mese</th><th scope="col">Lordo</th><th scope="col">Contributi</th>
    <th scope="col">Imposte</th><th scope="col">Trattenute</th><th scope="col">Netto</th>
    <th scope="col">Δ netto</th><th scope="col">Δ %</th><th scope="col">Prelievo</th></tr></thead>
    <tbody>{"".join(righe)}</tbody>
  </table>
  </div>
</section>"""


def _tabella_confronto(analisi: Analisi) -> str:
    if not analisi.confronti or analisi.confronti[-1].precedente is None:
        return ""
    confronto = analisi.confronti[-1]
    righe = []
    for gruppo, nome_gruppo in GRUPPI.items():
        variazioni = [
            v for v in confronto.variazioni_rilevanti if CAMPI_PER_CHIAVE[v.chiave].gruppo == gruppo
        ]
        if not variazioni:
            continue
        righe.append(f'<tr class="gruppo"><th colspan="5" scope="colgroup">{_esc(nome_gruppo)}</th></tr>')
        for variazione in variazioni:
            def formatta(valore: float | None, unita: str = variazione.unita) -> str:
                return formatta_valore(valore, unita)

            delta = variazione.delta
            classe = "delta-su" if (delta or 0) > 0 else ("delta-giu" if (delta or 0) < 0 else "")
            righe.append(
                f"<tr><th scope='row'>{_esc(variazione.etichetta)}</th>"
                f"<td>{formatta(variazione.precedente)}</td><td>{formatta(variazione.corrente)}</td>"
                f"<td class='{classe}'>{('+' if (delta or 0) > 0 else '') + formatta(delta) if delta is not None else '—'}</td>"
                f"<td class='{classe}'>{formatta_pct(variazione.delta_pct, segno=True)}</td></tr>"
            )
    voci = ""
    if confronto.voci_nuove:
        voci += f"<p class='nota'>Voci comparse: {_esc(', '.join(confronto.voci_nuove))}</p>"
    if confronto.voci_sparite:
        voci += f"<p class='nota'>Voci sparite: {_esc(', '.join(confronto.voci_sparite))}</p>"
    return f"""
<section class="scheda">
  <h3>Ultimo confronto: {_esc(formatta_periodo(confronto.precedente.periodo))} → {_esc(formatta_periodo(confronto.corrente.periodo))}</h3>
  <p class="sottotitolo">Voce per voce, cosa e' cambiato rispetto al mese precedente.</p>
  <div class="scorri">
  <table>
    <thead><tr><th scope="col">Voce</th><th scope="col">Prima</th><th scope="col">Dopo</th>
    <th scope="col">Δ</th><th scope="col">Δ %</th></tr></thead>
    <tbody>{"".join(righe)}</tbody>
  </table>
  </div>
  {voci}
</section>"""


def _tabella_statistiche(analisi: Analisi) -> str:
    righe = []
    for chiave in analisi.chiavi_presenti():
        statistica = analisi.statistiche[chiave]
        campo = CAMPI_PER_CHIAVE[chiave]
        formatta = formatta_euro  # l'unita' ha la sua colonna, qui basta il numero
        periodo_min, valore_min = statistica.minimo
        periodo_max, valore_max = statistica.massimo
        righe.append(
            f"<tr><th scope='row'>{_esc(statistica.etichetta)}</th>"
            f"<td>{_esc(campo.unita)}</td><td>{statistica.conteggio}</td>"
            f"<td>{formatta(statistica.media)}</td><td>{formatta(statistica.mediana)}</td>"
            f"<td>{formatta(valore_min)} <span class='muto'>({_esc(periodo_breve(periodo_min))})</span></td>"
            f"<td>{formatta(valore_max)} <span class='muto'>({_esc(periodo_breve(periodo_max))})</span></td>"
            f"<td>{formatta(statistica.totale)}</td></tr>"
        )
    return f"""
<section class="scheda">
  <h3>Ogni voce nel periodo</h3>
  <p class="sottotitolo">Media, mediana, estremi e totale di tutte le voci trovate nelle buste.</p>
  <div class="scorri">
  <table>
    <thead><tr><th scope="col">Voce</th><th scope="col">Un.</th><th scope="col">n.</th>
    <th scope="col">Media</th><th scope="col">Mediana</th><th scope="col">Minimo</th>
    <th scope="col">Massimo</th><th scope="col">Totale</th></tr></thead>
    <tbody>{"".join(righe)}</tbody>
  </table>
  </div>
</section>"""


def _elenco_anomalie(analisi: Analisi) -> str:
    if not analisi.anomalie:
        return """<section class="scheda"><h3>Da controllare</h3>
        <p class="sottotitolo">Nessuna segnalazione: le buste sono coerenti tra loro.</p></section>"""
    voci = "".join(
        f'<li class="{"seria" if a.gravita == "seria" else "attenzione"}">'
        f'<span class="segno" aria-hidden="true">{"!!" if a.gravita == "seria" else "!"}</span>'
        f'<span class="quando">{_esc(formatta_periodo(a.periodo)) if a.periodo else "tutto il periodo"}</span>'
        f"<b>{_esc(a.titolo)}</b><span>{_esc(a.dettaglio)}</span></li>"
        for a in analisi.anomalie
    )
    return f"""
<section class="scheda">
  <h3>Da controllare ({len(analisi.anomalie)})</h3>
  <p class="sottotitolo">Scostamenti sopra la soglia del {analisi.soglia:.0f}%, voci comparse o sparite, letture dubbie.</p>
  <ul class="anomalie">{voci}</ul>
</section>"""


# ---------------------------------------------------------------------------
# pagina
# ---------------------------------------------------------------------------


def _grafici(analisi: Analisi) -> str:
    pezzi: list[str] = []

    periodi = [b.periodo for b in analisi.buste if b.periodo]
    if periodi:
        netti = [b.get("netto") for b in analisi.buste if b.periodo]
        contributi = [b.get("contributi_inps") for b in analisi.buste if b.periodo]
        imposte = [b.imposte for b in analisi.buste if b.periodo]
        if any(v is not None for v in netti) and (
            any(v is not None for v in contributi) or any(v is not None for v in imposte)
        ):
            pezzi.append(
                grafico_impilato(
                    "Dove finisce il lordo",
                    "Ogni colonna e' una busta: quanto resta in tasca, quanto va in contributi e quanto in imposte.",
                    periodi,
                    [
                        ("Netto in busta", netti),
                        ("Contributi INPS", contributi),
                        ("Imposte (IRPEF + addizionali)", imposte),
                    ],
                )
            )

    pezzi.append(
        grafico_linee(
            "Lordo e netto mese per mese",
            "Le due curve insieme mostrano quanto si allarga la forbice tra costo lordo e incasso reale.",
            [
                ("Lordo", analisi.serie("totale_competenze")),
                ("Netto", analisi.serie("netto")),
            ],
        )
    )

    aliquota = analisi.serie_aliquota()
    if len(aliquota) > 1:
        pezzi.append(
            grafico_linee(
                "Prelievo effettivo sul lordo",
                "Percentuale del lordo trattenuta tra contributi e imposte. I picchi coincidono di solito con conguagli e mensilità aggiuntive.",
                [("Prelievo effettivo", aliquota)],
                unita="%",
                da_zero=False,
            )
        )

    ferie = analisi.serie("ferie_residue")
    permessi = analisi.serie("permessi_residui")
    if ferie or permessi:
        pezzi.append(
            grafico_linee(
                "Ferie e permessi residui",
                "Il saldo che ti resta da godere, busta dopo busta.",
                [("Ferie residue", ferie), ("Permessi / ROL residui", permessi)],
                unita="ore",
            )
        )

    return "".join(p for p in pezzi if p)


STILE = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 64px;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
 background:var(--piano);color:var(--testo);line-height:1.5}
.pagina{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;gap:20px}
header h1{font-size:1.7rem;margin:0 0 4px}
header p{margin:0;color:var(--testo-2)}
.scheda{background:var(--superficie);border:1px solid var(--bordo);border-radius:14px;padding:20px}
.scheda h3{margin:0 0 2px;font-size:1.05rem}
.sottotitolo,figcaption p{margin:0 0 14px;color:var(--testo-2);font-size:.88rem;max-width:70ch}
.tessere{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.tessera .etichetta{margin:0;color:var(--testo-2);font-size:.82rem}
.tessera .valore{margin:6px 0 0;font-size:1.7rem;font-weight:600;letter-spacing:-.01em;
 white-space:nowrap}
.tessera .nota{margin:2px 0 0;color:var(--testo-2);font-size:.8rem}
.delta-su{color:var(--su)}.delta-giu{color:var(--giu)}
figure.grafico{margin:0}
figure.grafico svg{width:100%;height:auto;display:block;overflow:visible}
.legenda{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:6px;font-size:.82rem;color:var(--testo-2)}
.voce-legenda{display:inline-flex;align-items:center;gap:6px}
.chiave{width:10px;height:10px;border-radius:3px;display:inline-block;flex:none;margin-right:2px}
.griglia{stroke:var(--griglia);stroke-width:1}
.base{stroke:var(--base);stroke-width:1}
.linea{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.punto{stroke:var(--superficie);stroke-width:2}
.tacca{fill:var(--muto);font-size:11px;font-variant-numeric:tabular-nums}
.etichetta-diretta{fill:var(--testo);font-size:12px;font-weight:600}
.banda{fill:transparent}
.banda:hover,.banda:focus{fill:var(--evidenza)}
table{border-collapse:collapse;width:100%;font-size:.87rem;font-variant-numeric:tabular-nums}
.scorri{overflow-x:auto}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--griglia);white-space:nowrap}
thead th{color:var(--testo-2);font-weight:600;font-size:.78rem;text-transform:uppercase;letter-spacing:.03em}
tbody th[scope=row]{text-align:left;font-weight:500}
tr.gruppo th{text-align:left;color:var(--testo-2);font-size:.78rem;text-transform:uppercase;
 letter-spacing:.03em;padding-top:16px;border-bottom:none}
.muto{color:var(--muto);font-size:.85em}
.pallino{color:var(--muto)}
.anomalie{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.anomalie li{display:grid;grid-template-columns:22px 130px 1fr;gap:10px;align-items:baseline;
 padding:9px 4px;border-bottom:1px solid var(--griglia);font-size:.88rem}
.anomalie li:last-child{border-bottom:none}
.anomalie li span:last-child{color:var(--testo-2);grid-column:3}
.anomalie li b{grid-column:3}
.anomalie .segno{font-weight:700;color:var(--attenzione)}
.anomalie .seria .segno{color:var(--critico)}
.anomalie .quando{color:var(--muto);font-size:.82rem}
#suggerimento{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;z-index:10;
 background:var(--superficie);border:1px solid var(--bordo);border-radius:10px;padding:8px 11px;
 font-size:.82rem;box-shadow:0 8px 24px rgba(0,0,0,.16);max-width:280px}
#suggerimento b{font-variant-numeric:tabular-nums}
footer{color:var(--muto);font-size:.8rem;text-align:center}
:root{--piano:#f9f9f7;--superficie:#fcfcfb;--testo:#0b0b0b;--testo-2:#52514e;--muto:#898781;
 --griglia:#e1e0d9;--base:#c3c2b7;--bordo:rgba(11,11,11,.10);--evidenza:rgba(11,11,11,.04);
 --su:#006300;--giu:#d03b3b;--attenzione:#b47700;--critico:#d03b3b;
 --serie-1:#2a78d6;--serie-2:#eb6834;--serie-3:#1baf7a}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){
 --piano:#0d0d0d;--superficie:#1a1a19;--testo:#fff;--testo-2:#c3c2b7;--muto:#898781;
 --griglia:#2c2c2a;--base:#383835;--bordo:rgba(255,255,255,.10);--evidenza:rgba(255,255,255,.05);
 --su:#0ca30c;--giu:#e66767;--attenzione:#fab219;--critico:#e66767;
 --serie-1:#3987e5;--serie-2:#d95926;--serie-3:#199e70}}
:root[data-theme=dark]{--piano:#0d0d0d;--superficie:#1a1a19;--testo:#fff;--testo-2:#c3c2b7;
 --muto:#898781;--griglia:#2c2c2a;--base:#383835;--bordo:rgba(255,255,255,.10);
 --evidenza:rgba(255,255,255,.05);--su:#0ca30c;--giu:#e66767;--attenzione:#fab219;--critico:#e66767;
 --serie-1:#3987e5;--serie-2:#d95926;--serie-3:#199e70}
@media print{body{padding:0}.scheda{break-inside:avoid}}
"""

SCRIPT = """
(function(){
 var tip=document.getElementById('suggerimento');
 function mostra(e){
  var testo=e.currentTarget.getAttribute('data-tip'); if(!testo) return;
  tip.innerHTML=testo; tip.style.opacity='1';
  var r=e.currentTarget.getBoundingClientRect();
  var x=(e.clientX||r.left+r.width/2)+14, y=(e.clientY||r.top)+14;
  if(x+tip.offsetWidth>window.innerWidth-8) x=window.innerWidth-tip.offsetWidth-8;
  if(y+tip.offsetHeight>window.innerHeight-8) y=window.innerHeight-tip.offsetHeight-8;
  tip.style.left=x+'px'; tip.style.top=y+'px';
 }
 function nascondi(){tip.style.opacity='0';}
 document.querySelectorAll('[data-tip]').forEach(function(el){
  el.setAttribute('tabindex','0');
  el.addEventListener('mousemove',mostra); el.addEventListener('mouseenter',mostra);
  el.addEventListener('focus',mostra); el.addEventListener('mouseleave',nascondi);
  el.addEventListener('blur',nascondi);
 });
})();
"""


def costruisci_html(analisi: Analisi, titolo: str = "Analisi buste paga") -> str:
    generato = date.today().strftime("%d/%m/%Y")
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(titolo)}</title>
<style>{STILE}</style>
</head>
<body>
<div class="pagina">
  <header>
    <h1>{_esc(titolo)}</h1>
    <p>{len(analisi.buste)} buste · {_esc(analisi.periodo_coperto)} · report generato il {generato}</p>
    {f'<p>{_esc(analisi.inquadramento)}</p>' if analisi.inquadramento else ''}
  </header>
  {_riquadri_sintesi(analisi)}
  {_grafici(analisi)}
  {_tabella_mensile(analisi)}
  {_tabella_confronto(analisi)}
  {_tabella_statistiche(analisi)}
  {_elenco_anomalie(analisi)}
  <footer>Dati letti dai PDF delle buste paga. In caso di dubbio fa fede il documento originale.</footer>
</div>
<div id="suggerimento" role="status"></div>
<script>{SCRIPT}</script>
</body>
</html>"""
