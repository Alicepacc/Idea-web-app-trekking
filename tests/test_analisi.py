"""Confronti, statistiche, anomalie e andamento."""

import unittest
from datetime import date

from bustepaga.analisi import analizza
from bustepaga.demo import buste_demo
from bustepaga.modello import BustaPaga


def busta(anno, mese, netto, lordo=None, **extra):
    valori = {"netto": netto}
    if lordo is not None:
        valori["totale_competenze"] = lordo
        valori["totale_trattenute"] = round(lordo - netto, 2)
    valori.update(extra)
    return BustaPaga(file=f"{anno}-{mese:02d}.pdf", periodo=date(anno, mese, 1), valori=valori)


class TestConfronti(unittest.TestCase):
    def setUp(self):
        self.analisi = analizza(
            [
                busta(2025, 1, 1500.0, 2100.0),
                busta(2025, 2, 1560.0, 2180.0),
                busta(2025, 3, 1560.0, 2180.0),
            ]
        )

    def test_prima_busta_senza_precedente(self):
        self.assertIsNone(self.analisi.confronti[0].precedente)

    def test_delta_e_percentuale(self):
        variazione = next(
            v for v in self.analisi.confronti[1].variazioni if v.chiave == "netto"
        )
        self.assertEqual(variazione.delta, 60.0)
        self.assertEqual(variazione.delta_pct, 4.0)

    def test_variazione_nulla_non_e_rilevante(self):
        chiavi = {v.chiave for v in self.analisi.confronti[2].variazioni_rilevanti}
        self.assertNotIn("netto", chiavi)


class TestStatistiche(unittest.TestCase):
    def setUp(self):
        self.analisi = analizza(
            [busta(2025, 1, 1000.0), busta(2025, 2, 2000.0), busta(2025, 3, 1500.0)]
        )

    def test_medie_ed_estremi(self):
        statistica = self.analisi.statistiche["netto"]
        self.assertEqual(statistica.media, 1500.0)
        self.assertEqual(statistica.mediana, 1500.0)
        self.assertEqual(statistica.totale, 4500.0)
        self.assertEqual(statistica.minimo, (date(2025, 1, 1), 1000.0))
        self.assertEqual(statistica.massimo, (date(2025, 2, 1), 2000.0))

    def test_totali_per_anno(self):
        self.assertEqual(self.analisi.anni[2025]["netto"], 4500.0)
        self.assertEqual(self.analisi.anni[2025]["buste"], 3)


class TestMensilitaAggiuntive(unittest.TestCase):
    def test_riconosce_la_tredicesima_dall_etichetta(self):
        analisi = analizza(
            [
                busta(2025, 11, 1500.0, 2100.0),
                busta(2025, 12, 1600.0, 2200.0, tredicesima=1500.0),
            ]
        )
        self.assertIn(date(2025, 12, 1), analisi.mensilita_aggiuntive)

    def test_riconosce_un_netto_fuori_scala(self):
        analisi = analizza(
            [
                busta(2025, 10, 1500.0),
                busta(2025, 11, 1500.0),
                busta(2025, 12, 3000.0),
            ]
        )
        self.assertIn(date(2025, 12, 1), analisi.mensilita_aggiuntive)

    def test_le_mensilita_extra_non_diventano_anomalie(self):
        analisi = analizza(
            [
                busta(2025, 11, 1500.0, 2100.0),
                busta(2025, 12, 3000.0, 4200.0, tredicesima=1500.0),
            ]
        )
        titoli = [a.titolo for a in analisi.anomalie]
        self.assertFalse([t for t in titoli if "aumento" in t])


class TestAnomalie(unittest.TestCase):
    def test_segnala_lo_scostamento_oltre_soglia(self):
        analisi = analizza(
            [busta(2025, 1, 1500.0, 2100.0), busta(2025, 2, 1200.0, 1700.0)], soglia=10.0
        )
        self.assertTrue([a for a in analisi.anomalie if "calo" in a.titolo])

    def test_sotto_soglia_niente_segnalazione(self):
        analisi = analizza(
            [busta(2025, 1, 1500.0, 2100.0), busta(2025, 2, 1530.0, 2140.0)], soglia=10.0
        )
        self.assertFalse([a for a in analisi.anomalie if "aumento" in a.titolo])

    def test_segnala_i_campi_letti_solo_a_meta(self):
        analisi = analizza(
            [busta(2025, 1, 1500.0, 2100.0), busta(2025, 2, 1500.0)],
        )
        self.assertTrue([a for a in analisi.anomalie if "non letto" in a.titolo])


class TestInquadramento(unittest.TestCase):
    def test_segnala_il_cambio_di_livello(self):
        prima = busta(2025, 1, 1500.0, 2100.0)
        prima.anagrafica = {"livello": "4"}
        dopo = busta(2025, 2, 1600.0, 2200.0)
        dopo.anagrafica = {"livello": "3"}
        analisi = analizza([prima, dopo])
        self.assertTrue([a for a in analisi.anomalie if a.titolo == "Livello cambiato"])

    def test_niente_segnalazione_se_resta_uguale(self):
        prima, dopo = busta(2025, 1, 1500.0), busta(2025, 2, 1500.0)
        prima.anagrafica = dopo.anagrafica = {"livello": "4"}
        analisi = analizza([prima, dopo])
        self.assertFalse([a for a in analisi.anomalie if "cambiato" in a.titolo])


class TestMesiParziali(unittest.TestCase):
    def test_riconosce_il_mese_di_assunzione(self):
        analisi = analizza(
            [
                busta(2024, 12, 228.0, 2963.0),
                busta(2025, 1, 2140.0, 3055.0),
                busta(2025, 2, 2267.0, 3070.0),
                busta(2025, 3, 2192.0, 3125.0),
            ]
        )
        self.assertIn(date(2024, 12, 1), analisi.mesi_parziali)

    def test_il_mese_parziale_non_falsa_la_tendenza(self):
        buste = [
            busta(2024, 12, 228.0),
            busta(2025, 1, 2100.0),
            busta(2025, 2, 2110.0),
            busta(2025, 3, 2120.0),
            busta(2025, 4, 2130.0),
        ]
        pendenza, _ = analizza(buste).tendenza_netto
        self.assertAlmostEqual(pendenza, 10.0)

    def test_niente_allarme_sul_salto_dal_mese_parziale(self):
        analisi = analizza(
            [
                busta(2024, 12, 228.0, 2963.0),
                busta(2025, 1, 2140.0, 3055.0),
                busta(2025, 2, 2267.0, 3070.0),
                busta(2025, 3, 2192.0, 3125.0),
            ]
        )
        salti = [a for a in analisi.anomalie if "aumento" in a.titolo or "calo" in a.titolo]
        self.assertFalse(salti)
        self.assertTrue([a for a in analisi.anomalie if "solo in parte" in a.titolo])


class TestAndamento(unittest.TestCase):
    def test_tendenza_in_crescita(self):
        analisi = analizza(
            [busta(2025, 1, 1000.0), busta(2025, 2, 1100.0), busta(2025, 3, 1200.0)]
        )
        pendenza, variazione = analisi.tendenza_netto
        self.assertAlmostEqual(pendenza, 100.0)
        self.assertAlmostEqual(variazione, 20.0)

    def test_serve_piu_di_una_busta(self):
        self.assertIsNone(analizza([busta(2025, 1, 1000.0)]).tendenza_netto)

    def test_periodo_coperto(self):
        analisi = analizza([busta(2024, 10, 1000.0), busta(2025, 3, 1200.0)])
        self.assertEqual(analisi.periodo_coperto, "Ottobre 2024 → Marzo 2025")


class TestSuBusteDemo(unittest.TestCase):
    def setUp(self):
        self.analisi = analizza(buste_demo())

    def test_serie_complete(self):
        self.assertEqual(len(self.analisi.serie("netto")), 15)

    def test_mesi_irregolari_esclusi_dalla_serie(self):
        self.assertLess(
            len(self.analisi.serie("netto", solo_mesi_pieni=True)),
            len(self.analisi.serie("netto")),
        )

    def test_aliquota_in_un_intervallo_plausibile(self):
        for _, aliquota in self.analisi.serie_aliquota():
            self.assertGreater(aliquota, 15.0)
            self.assertLess(aliquota, 50.0)


if __name__ == "__main__":
    unittest.main()
