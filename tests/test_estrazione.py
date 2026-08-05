"""Lettura dei cedolini: e' la parte piu' fragile, quindi la piu' testata."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from bustepaga.demo import buste_demo, testo_busta
from bustepaga.estrazione import busta_da_testo, leggi_csv, scrivi_csv, trova_periodo
from bustepaga.modello import normalizza_testo

# Cedolino con le colonne incolonnate, come quelli veri.
CON_COLONNE = """\
  AZIENDA ESEMPIO S.R.L.
  PERIODO DI RETRIBUZIONE: MARZO 2025
  DESCRIZIONE                             ORE      COMPETENZE      TRATTENUTE
  PAGA BASE                            168,00        1.620,00
  SUPERMINIMO ASSORBIBILE                             180,00
  CONTRIBUTI INPS C/DIPENDENTE 9,19%                                  165,44
  RITENUTE IRPEF                                                      280,10
  TOTALE COMPETENZE                                 1.800,00
  TOTALE TRATTENUTE                                                   445,54
  FERIE RESIDUE      66,00      PERMESSI RESIDUI      24,00
  NETTO IN BUSTA                                    1.354,46
"""

# Cedolino "piatto": nessuna intestazione di colonna, valori sulla riga sotto.
SENZA_COLONNE = """\
BUSTA PAGA APRILE 2025
Retribuzione lorda   2.000,00
Totale ritenute        600,00
Netto del mese       1.400,00
"""

SOTTO_ETICHETTA = """\
CEDOLINO 05/2025
   TOT. COMPETENZE      TOT. TRATTENUTE      NETTO DEL MESE
         2.500,00              750,00            1.750,00
"""


class TestEstrazioneConColonne(unittest.TestCase):
    def setUp(self):
        self.busta = busta_da_testo(CON_COLONNE, "marzo.pdf")

    def test_periodo(self):
        self.assertEqual(self.busta.periodo, date(2025, 3, 1))

    def test_totali(self):
        self.assertEqual(self.busta.get("totale_competenze"), 1800.00)
        self.assertEqual(self.busta.get("totale_trattenute"), 445.54)
        self.assertEqual(self.busta.get("netto"), 1354.46)

    def test_non_scambia_le_ore_per_un_importo(self):
        """La colonna ORE contiene 168,00: la paga base e' 1.620,00."""
        self.assertEqual(self.busta.get("paga_base"), 1620.00)

    def test_non_sfora_sull_etichetta_successiva(self):
        """Su «FERIE RESIDUE 66,00 PERMESSI RESIDUI 24,00» ognuno prende il suo."""
        self.assertEqual(self.busta.get("ferie_residue"), 66.00)
        self.assertEqual(self.busta.get("permessi_residui"), 24.00)

    def test_percentuale_nella_descrizione_ignorata(self):
        self.assertEqual(self.busta.get("contributi_inps"), 165.44)

    def test_quadratura_ok(self):
        self.assertEqual(self.busta.avvisi, [])

    def test_voci_di_dettaglio(self):
        descrizioni = {v.descrizione for v in self.busta.voci}
        self.assertIn("PAGA BASE", descrizioni)
        self.assertIn("RITENUTE IRPEF", descrizioni)
        # l'anagrafica non e' una voce
        self.assertNotIn("AZIENDA ESEMPIO S.R.L.", descrizioni)


class TestAnagrafica(unittest.TestCase):
    def test_campi_separati_da_spazi_larghi(self):
        busta = busta_da_testo(
            "C.C.N.L.: COMMERCIO TERZIARIO      LIVELLO: 4      QUALIFICA: IMPIEGATO\n",
            "x.pdf",
        )
        self.assertEqual(busta.anagrafica["ccnl"], "COMMERCIO TERZIARIO")
        self.assertEqual(busta.anagrafica["livello"], "4")

    def test_campi_con_spazi_compressi(self):
        """pdfplumber a volte comprime gli spazi: il campo finisce comunque."""
        busta = busta_da_testo(
            "C.C.N.L.: COMMERCIO TERZIARIO LIVELLO: 4 QUALIFICA: IMPIEGATO\n", "x.pdf"
        )
        self.assertEqual(busta.anagrafica["ccnl"], "COMMERCIO TERZIARIO")
        self.assertEqual(busta.anagrafica["livello"], "4")
        self.assertEqual(busta.anagrafica["qualifica"], "IMPIEGATO")


class TestEstrazioneSenzaColonne(unittest.TestCase):
    def test_layout_piatto(self):
        busta = busta_da_testo(SENZA_COLONNE, "aprile.pdf")
        self.assertEqual(busta.periodo, date(2025, 4, 1))
        self.assertEqual(busta.get("totale_competenze"), 2000.00)
        self.assertEqual(busta.get("totale_trattenute"), 600.00)
        self.assertEqual(busta.get("netto"), 1400.00)

    def test_valori_nella_riga_sotto(self):
        busta = busta_da_testo(SOTTO_ETICHETTA, "maggio.pdf")
        self.assertEqual(busta.get("totale_competenze"), 2500.00)
        self.assertEqual(busta.get("totale_trattenute"), 750.00)
        self.assertEqual(busta.get("netto"), 1750.00)


class TestControlli(unittest.TestCase):
    def test_avvisa_se_i_conti_non_tornano(self):
        busta = busta_da_testo(
            "PERIODO: GIUGNO 2025\nTOTALE COMPETENZE 2.000,00\n"
            "TOTALE TRATTENUTE 600,00\nNETTO IN BUSTA 900,00\n",
            "giugno.pdf",
        )
        self.assertTrue(any("non tornano" in a for a in busta.avvisi))

    def test_pdf_senza_testo(self):
        busta = busta_da_testo("", "scansione.pdf")
        self.assertTrue(any("OCR" in a for a in busta.avvisi))


class TestPeriodo(unittest.TestCase):
    def test_dal_nome_file(self):
        self.assertEqual(trova_periodo("", "2025-07.pdf"), date(2025, 7, 1))
        self.assertEqual(trova_periodo("", "busta_2024_11.pdf"), date(2024, 11, 1))

    def test_mese_scritto_e_abbreviato(self):
        self.assertEqual(trova_periodo(normalizza_testo("Cedolino Dicembre 2024")), date(2024, 12, 1))
        self.assertEqual(trova_periodo(normalizza_testo("Periodo: 03/2025")), date(2025, 3, 1))

    def test_preferisce_la_riga_col_periodo(self):
        testo = normalizza_testo(
            "DATA DI ASSUNZIONE 01/2019\nPERIODO DI RETRIBUZIONE: SETTEMBRE 2025\n"
        )
        self.assertEqual(trova_periodo(testo), date(2025, 9, 1))


class TestCsv(unittest.TestCase):
    def test_andata_e_ritorno(self):
        buste = buste_demo(3)
        with tempfile.TemporaryDirectory() as cartella:
            percorso = Path(cartella) / "dati.csv"
            scrivi_csv(buste, percorso)
            rilette = leggi_csv(percorso)
        self.assertEqual(len(rilette), 3)
        self.assertEqual(rilette[0].periodo, buste[0].periodo)
        self.assertEqual(rilette[0].get("netto"), buste[0].get("netto"))


class TestDemo(unittest.TestCase):
    def test_tutte_le_buste_demo_sono_leggibili(self):
        for busta in buste_demo():
            self.assertTrue(busta.completa, f"{busta.file} non letta")
            self.assertEqual(busta.avvisi, [], f"{busta.file}: {busta.avvisi}")

    def test_tredicesima_riconosciuta(self):
        dicembre = [b for b in buste_demo() if b.periodo.month == 12][0]
        self.assertIsNotNone(dicembre.get("tredicesima"))

    def test_coerenza_interna(self):
        for busta in buste_demo():
            lordo = busta.get("totale_competenze")
            trattenute = busta.get("totale_trattenute")
            self.assertAlmostEqual(lordo - trattenute, busta.get("netto"), places=2)

    def test_testo_demo_contiene_le_colonne(self):
        testo = testo_busta(date(2025, 3, 1), 5)
        self.assertIn("COMPETENZE", testo)
        self.assertIn("TRATTENUTE", testo)


if __name__ == "__main__":
    unittest.main()
