"""Conversione e formattazione dei numeri all'italiana."""

import unittest
from datetime import date

from bustepaga.modello import (
    BustaPaga,
    formatta_euro,
    formatta_pct,
    formatta_periodo,
    formatta_valore,
    numeri_in_riga,
    normalizza_testo,
)


class TestNumeri(unittest.TestCase):
    def test_formati_italiani(self):
        self.assertEqual(numeri_in_riga("1.234,56"), [1234.56])
        self.assertEqual(numeri_in_riga("1234,56"), [1234.56])
        self.assertEqual(numeri_in_riga("12.345.678,90"), [12345678.90])
        self.assertEqual(numeri_in_riga("168"), [168.0])
        self.assertEqual(numeri_in_riga("0,50"), [0.5])

    def test_segno_negativo_anche_in_coda(self):
        self.assertEqual(numeri_in_riga("-45,00"), [-45.0])
        self.assertEqual(numeri_in_riga("45,00-"), [-45.0])

    def test_piu_numeri_sulla_stessa_riga(self):
        self.assertEqual(
            numeri_in_riga("PAGA BASE   168,00   8,50   1.428,00"),
            [168.0, 8.5, 1428.0],
        )

    def test_non_confonde_codici_e_date(self):
        # una data non deve produrre tre numeri sballati
        self.assertEqual(numeri_in_riga("MATRICOLA 0001234"), [1234.0])

    def test_formattazione(self):
        self.assertEqual(formatta_euro(1234.5), "1.234,50")
        self.assertEqual(formatta_euro(None), "—")
        self.assertEqual(formatta_pct(29.47), "29,5%")
        self.assertEqual(formatta_pct(3.8, segno=True), "+3,8%")
        self.assertEqual(formatta_valore(168.0, "ore"), "168,00 ore")
        self.assertEqual(formatta_periodo(date(2025, 3, 1)), "Marzo 2025")


class TestNormalizzazione(unittest.TestCase):
    def test_maiuscole_e_accenti(self):
        self.assertEqual(normalizza_testo("Indennità"), "INDENNITA")

    def test_spaziatura_conservata(self):
        """L'incolonnamento e' informazione: non va compattato."""
        testo = "PAGA BASE        1.620,00"
        self.assertEqual(normalizza_testo(testo), testo)


class TestGrandezzeDerivate(unittest.TestCase):
    def test_aliquota_dal_totale_trattenute(self):
        busta = BustaPaga(
            file="x.pdf",
            periodo=date(2025, 3, 1),
            valori={"totale_competenze": 2000.0, "totale_trattenute": 600.0, "netto": 1400.0},
        )
        self.assertAlmostEqual(busta.aliquota_effettiva, 30.0)

    def test_aliquota_ricostruita_senza_totale(self):
        busta = BustaPaga(
            file="x.pdf",
            valori={"totale_competenze": 2000.0, "contributi_inps": 200.0, "irpef_netta": 400.0},
        )
        self.assertAlmostEqual(busta.aliquota_effettiva, 30.0)

    def test_trattamento_integrativo_riduce_le_imposte(self):
        busta = BustaPaga(
            file="x.pdf",
            valori={"irpef_netta": 400.0, "trattamento_integrativo": 100.0},
        )
        self.assertAlmostEqual(busta.imposte, 300.0)

    def test_senza_lordo_niente_aliquota(self):
        self.assertIsNone(BustaPaga(file="x.pdf", valori={"netto": 1400.0}).aliquota_effettiva)


if __name__ == "__main__":
    unittest.main()
