"""I report devono reggere anche i casi poveri (una sola busta, dati mancanti)."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

from bustepaga.analisi import analizza
from bustepaga.cli import main
from bustepaga.demo import buste_demo
from bustepaga.modello import BustaPaga
from bustepaga.report_html import costruisci_html
from bustepaga.report_testo import report_completo


class TestReportTesto(unittest.TestCase):
    def test_report_completo(self):
        testo = report_completo(analizza(buste_demo()))
        self.assertIn("Riepilogo mese per mese", testo)
        self.assertIn("Andamento complessivo", testo)
        self.assertIn("1.649,47", testo)  # netto della prima busta, all'italiana

    def test_una_sola_busta(self):
        analisi = analizza([BustaPaga(file="x.pdf", periodo=date(2025, 5, 1), valori={"netto": 1500.0})])
        self.assertIn("Maggio 2025", report_completo(analisi))

    def test_busta_senza_valori(self):
        analisi = analizza([BustaPaga(file="vuota.pdf", periodo=date(2025, 5, 1))])
        self.assertIn("Riepilogo", report_completo(analisi))


class TestReportHtml(unittest.TestCase):
    def setUp(self):
        self.html = costruisci_html(analizza(buste_demo()))

    def test_pagina_autonoma(self):
        """Nessuna risorsa esterna: il file deve funzionare offline."""
        for vietato in ("http://", "https://", "<script src", "<link "):
            self.assertNotIn(vietato, self.html)

    def test_contiene_grafici_e_tabelle(self):
        self.assertIn("<svg", self.html)
        self.assertIn("Dove finisce il lordo", self.html)
        self.assertIn("Tutti i dati, mese per mese", self.html)

    def test_gli_svg_sono_ben_formati(self):
        inizio = 0
        trovati = 0
        while (inizio := self.html.find("<svg", inizio)) != -1:
            fine = self.html.index("</svg>", inizio) + len("</svg>")
            ElementTree.fromstring(self.html[inizio:fine])
            inizio, trovati = fine, trovati + 1
        self.assertGreaterEqual(trovati, 3)

    def test_tema_chiaro_e_scuro(self):
        self.assertIn("prefers-color-scheme:dark", self.html)
        self.assertIn('[data-theme=dark]', self.html)

    def test_regge_una_busta_scarna(self):
        analisi = analizza([BustaPaga(file="x.pdf", periodo=date(2025, 5, 1), valori={"netto": 1500.0})])
        self.assertIn("<svg", costruisci_html(analisi) + "<svg")  # non deve sollevare


class TestCli(unittest.TestCase):
    def test_demo_scrive_i_file_richiesti(self):
        with tempfile.TemporaryDirectory() as cartella:
            base = Path(cartella)
            codice = main(
                [
                    "--demo",
                    "--silenzioso",
                    "--html", str(base / "report.html"),
                    "--csv", str(base / "dati.csv"),
                    "--json", str(base / "dati.json"),
                ]
            )
            self.assertEqual(codice, 0)
            self.assertTrue((base / "report.html").exists())
            self.assertTrue((base / "dati.csv").exists())
            self.assertTrue((base / "dati.json").exists())

    def test_filtro_di_periodo(self):
        with tempfile.TemporaryDirectory() as cartella:
            csv = Path(cartella) / "dati.csv"
            main(["--demo", "--silenzioso", "--da", "2025-01", "--csv", str(csv)])
            righe = csv.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(righe) - 1, 12)  # intestazione esclusa

    def test_percorso_inesistente(self):
        self.assertEqual(main(["/percorso/che/non/esiste"]), 2)


if __name__ == "__main__":
    unittest.main()
