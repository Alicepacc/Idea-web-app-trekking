"""Interfaccia a riga di comando."""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import date
from pathlib import Path

from . import analisi as mod_analisi
from . import estrazione, report_html, report_testo
from .modello import BustaPaga
from .pattern import CAMPI_PER_CHIAVE

DESCRIZIONE = """\
Legge i PDF delle buste paga, li confronta mese per mese e mostra l'andamento.

Esempi:
  python3 analizza_buste.py buste/
  python3 analizza_buste.py buste/ --html report.html --apri
  python3 analizza_buste.py buste/ --csv dati.csv        # poi correggi il CSV a mano
  python3 analizza_buste.py --da-csv dati.csv --html report.html
  python3 analizza_buste.py buste/ --dump-testo testo/   # per aggiungere pattern mancanti
"""


def costruisci_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analizza_buste",
        description=DESCRIZIONE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "percorsi",
        nargs="*",
        type=Path,
        help="file PDF o cartelle che li contengono (default: ./buste)",
    )
    parser.add_argument("--da-csv", type=Path, help="parti da un CSV gia' estratto (e corretto)")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="usa buste finte di esempio, per vedere subito come funziona",
    )
    parser.add_argument("--csv", type=Path, help="esporta i dati estratti in CSV")
    parser.add_argument("--json", type=Path, help="esporta i dati estratti in JSON")
    parser.add_argument("--html", type=Path, help="scrive il report HTML con i grafici")
    parser.add_argument("--apri", action="store_true", help="apre il report HTML nel browser")
    parser.add_argument("--dump-testo", type=Path, help="salva il testo estratto da ogni PDF")
    parser.add_argument("--da", help="considera solo le buste da questo mese (AAAA-MM)")
    parser.add_argument("--a", help="considera solo le buste fino a questo mese (AAAA-MM)")
    parser.add_argument(
        "--soglia",
        type=float,
        default=mod_analisi.SOGLIA_ANOMALIA,
        help="variazione percentuale oltre la quale segnalare (default: %(default)s)",
    )
    parser.add_argument("--silenzioso", action="store_true", help="non stampare il report a terminale")
    return parser


def _mese(testo: str | None) -> date | None:
    if not testo:
        return None
    pezzi = testo.replace("/", "-").split("-")
    try:
        if len(pezzi) == 1:
            return date(int(pezzi[0]), 1, 1)
        anno, mese = (pezzi[0], pezzi[1]) if len(pezzi[0]) == 4 else (pezzi[1], pezzi[0])
        return date(int(anno), int(mese), 1)
    except ValueError:
        return None


def _filtra(buste: list[BustaPaga], da: date | None, a: date | None) -> list[BustaPaga]:
    def dentro(busta: BustaPaga) -> bool:
        if busta.periodo is None:
            return True
        if da and busta.periodo < da:
            return False
        return not (a and busta.periodo > a)

    return [b for b in buste if dentro(b)]


def _scrivi_json(buste: list[BustaPaga], destinazione: Path) -> None:
    dati = [
        {
            "file": b.file,
            "periodo": b.periodo.strftime("%Y-%m") if b.periodo else None,
            "valori": b.valori,
            "aliquota_effettiva": b.aliquota_effettiva,
            "voci": [
                {"descrizione": v.descrizione, "competenza": v.competenza, "trattenuta": v.trattenuta}
                for v in b.voci
            ],
            "avvisi": b.avvisi,
        }
        for b in buste
    ]
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text(
        json.dumps(dati, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _salva_testo(buste: list[BustaPaga], cartella: Path) -> None:
    cartella.mkdir(parents=True, exist_ok=True)
    for busta in buste:
        (cartella / f"{Path(busta.file).stem}.txt").write_text(busta.testo, encoding="utf-8")


def _riepilogo_lettura(buste: list[BustaPaga]) -> str:
    lette = sum(1 for b in buste if b.completa)
    trovati = {c for b in buste for c in b.valori}
    return (
        f"Lette {len(buste)} buste ({lette} complete), "
        f"{len(trovati)} voci riconosciute su {len(CAMPI_PER_CHIAVE)} cercate."
    )


def main(argv: list[str] | None = None) -> int:
    argomenti = costruisci_parser().parse_args(argv)

    if argomenti.demo:
        from .demo import buste_demo  # import locale: serve solo qui

        buste = buste_demo()
    elif argomenti.da_csv:
        buste = estrazione.leggi_csv(argomenti.da_csv)
    else:
        percorsi = argomenti.percorsi or [Path("buste")]
        mancanti = [p for p in percorsi if not p.exists()]
        if mancanti:
            print(
                "Percorso inesistente: " + ", ".join(str(p) for p in mancanti),
                file=sys.stderr,
            )
            print("Metti i PDF in una cartella e passala come argomento.", file=sys.stderr)
            return 2
        buste = estrazione.leggi_percorsi(percorsi)

    if not buste:
        print("Nessuna busta paga trovata (cerco file .pdf).", file=sys.stderr)
        return 1

    if argomenti.dump_testo:
        _salva_testo(buste, argomenti.dump_testo)
        print(f"Testo estratto salvato in {argomenti.dump_testo}/")

    buste = _filtra(buste, _mese(argomenti.da), _mese(argomenti.a))
    if not buste:
        print("Nessuna busta nel periodo richiesto.", file=sys.stderr)
        return 1

    risultato = mod_analisi.analizza(buste, soglia=argomenti.soglia)

    if argomenti.csv:
        estrazione.scrivi_csv(buste, argomenti.csv)
        print(f"Dati esportati in {argomenti.csv}")
    if argomenti.json:
        _scrivi_json(buste, argomenti.json)
        print(f"Dati esportati in {argomenti.json}")
    if argomenti.html:
        argomenti.html.parent.mkdir(parents=True, exist_ok=True)
        argomenti.html.write_text(report_html.costruisci_html(risultato), encoding="utf-8")
        print(f"Report HTML scritto in {argomenti.html}")
        if argomenti.apri:
            webbrowser.open(argomenti.html.resolve().as_uri())

    if not argomenti.silenzioso:
        print(report_testo.report_completo(risultato))
    print(_riepilogo_lettura(buste))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
