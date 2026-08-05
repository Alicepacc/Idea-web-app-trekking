"""Registro dei campi cercati nelle buste paga.

Ogni busta paga italiana ha un layout diverso (Zucchetti, TeamSystem, ADP,
Inaz, ...) ma le etichette delle voci sono in gran parte le stesse. Qui sta
tutto il "sapere" sulle etichette: per adattare il programma a una busta che
non viene letta bene basta aggiungere una variante in `pattern` — nient'altro
va toccato.

I pattern sono espressioni regolari applicate al testo NORMALIZZATO
(maiuscolo, senza accenti, spazi compattati), quindi si scrivono in maiuscolo
e senza accenti.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

EURO = "€"
ORE = "ore"
GIORNI = "gg"
PERC = "%"


@dataclass(frozen=True)
class Campo:
    chiave: str
    etichetta: str
    gruppo: str
    pattern: tuple[str, ...]
    unita: str = EURO
    # quale numero prendere, tra quelli che seguono l'etichetta fino
    # all'etichetta successiva sulla stessa riga
    posizione: str = "ultimo"  # "primo" | "ultimo"
    # colonna del cedolino in cui sta l'importo, quando le colonne sono
    # riconoscibili: aiuta a non confondere ore, quantita' e importi
    colonna: str = ""  # "competenze" | "trattenute" | ""
    # se l'etichetta compare piu' volte nel documento, quale occorrenza vale
    occorrenza: str = "ultima"  # "ultima" | "prima"
    # se True il valore va sommato quando l'etichetta compare piu' volte
    somma: bool = False
    # righe da ignorare anche se l'etichetta corrisponde (regex)
    esclusioni: tuple[str, ...] = ()
    descrizione: str = ""


CAMPI: tuple[Campo, ...] = (
    # ---------------------------------------------------------------- riepilogo
    Campo(
        "totale_competenze",
        "Totale competenze (lordo)",
        "riepilogo",
        (
            r"TOT(?:ALE|\.)?\s+COMPETENZE",
            r"TOT(?:ALE|\.)?\s+COMPENSI",
            r"RETRIBUZIONE\s+LORDA",
            r"TOT(?:ALE|\.)?\s+LORDO",
            r"LORDO\s+(?:DEL\s+)?MESE",
            r"TOT(?:ALE|\.)?\s+RETRIBUZIONE",
        ),
        descrizione="Quanto ti riconosce l'azienda prima di contributi e tasse.",
    ),
    Campo(
        "totale_trattenute",
        "Totale trattenute",
        "riepilogo",
        (
            r"TOT(?:ALE|\.)?\s+TRATTENUTE",
            r"TOT(?:ALE|\.)?\s+RITENUTE",
            r"TOT(?:ALE|\.)?\s+DEDUZIONI",
        ),
        descrizione="Somma di contributi, imposte e altre ritenute.",
    ),
    Campo(
        "netto",
        "Netto in busta",
        "riepilogo",
        (
            r"NETTO\s+IN\s+BUSTA",
            r"NETTO\s+(?:DEL\s+)?MESE",
            r"NETTO\s+(?:DA\s+|A\s+)?PAGARE",
            r"NETTO\s+CORRISPOSTO",
            r"NETTO\s+BUSTA",
            r"RETRIBUZIONE\s+NETTA",
            r"TOT(?:ALE|\.)?\s+NETTO",
            r"\bNETTO\b(?!\s*(?:ACCANTONATO|TFR|MATURATO))",
        ),
        descrizione="Quello che arriva davvero sul conto.",
    ),
    Campo(
        "arrotondamento",
        "Arrotondamento",
        "riepilogo",
        (r"ARROTONDAMENT[OI]",),
    ),
    # ----------------------------------------------------------- retribuzione
    Campo(
        "paga_base",
        "Paga base / minimo",
        "retribuzione",
        (
            r"PAGA\s+BASE",
            r"MINIMO\s+(?:CONTRATTUALE|TABELLARE)",
            r"STIPENDIO\s+BASE",
            r"RETRIBUZIONE\s+BASE",
        ),
    ),
    Campo(
        "contingenza",
        "Contingenza",
        "retribuzione",
        (r"CONTINGENZA",),
    ),
    Campo(
        "superminimo",
        "Superminimo",
        "retribuzione",
        (r"SUPER\s?MINIMO", r"AD\s+PERSONAM"),
    ),
    Campo(
        "scatti_anzianita",
        "Scatti di anzianità",
        "retribuzione",
        (r"SCATTI\s+(?:DI\s+)?ANZIANITA", r"\bSCATTI\b"),
    ),
    Campo(
        "edr",
        "E.D.R.",
        "retribuzione",
        (r"\bE\.?\s?D\.?\s?R\.?\b", r"ELEMENTO\s+DISTINTO"),
    ),
    Campo(
        "straordinari",
        "Straordinari",
        "retribuzione",
        (r"(?:LAVORO\s+)?STRAORDINARI[OE]", r"\bSTRAORD\.?\b"),
        somma=True,
    ),
    Campo(
        "indennita",
        "Indennità",
        "retribuzione",
        (r"INDENNITA(?!\s+DI\s+CONTINGENZA)",),
        somma=True,
    ),
    Campo(
        "premi",
        "Premi / una tantum",
        "retribuzione",
        (
            r"PREMIO\s+(?:DI\s+)?RISULTATO",
            r"UNA\s+TANTUM",
            r"\bPREMIO\b",
            r"\bBONUS\b(?!\s+IRPEF)",
            r"\bMBO\b",
        ),
        somma=True,
    ),
    Campo(
        "rimborsi",
        "Rimborsi / trasferte",
        "retribuzione",
        (r"RIMBORS[OI]", r"TRASFERT[AE]", r"DIARIA"),
        somma=True,
    ),
    Campo(
        "buoni_pasto",
        "Buoni pasto",
        "retribuzione",
        (r"BUONI\s+PASTO", r"TICKET(?:\s+RESTAURANT)?", r"MENSA"),
    ),
    Campo(
        "tredicesima",
        "Tredicesima",
        "retribuzione",
        (r"TREDICESIMA", r"13\s?[.^]?\s?MENSILITA", r"GRATIFICA\s+NATALIZIA"),
    ),
    Campo(
        "quattordicesima",
        "Quattordicesima",
        "retribuzione",
        (r"QUATTORDICESIMA", r"14\s?[.^]?\s?MENSILITA"),
    ),
    # ------------------------------------------------------------- previdenza
    Campo(
        "imponibile_previdenziale",
        "Imponibile previdenziale",
        "previdenza",
        (
            r"IMPONIBILE\s+PREVIDENZIALE",
            r"IMPON\.?\s+PREV\.?",
            r"IMPONIBILE\s+(?:INPS|CONTRIBUTIVO)",
        ),
    ),
    Campo(
        "contributi_inps",
        "Contributi INPS a tuo carico",
        "previdenza",
        (
            r"(?:TOT(?:ALE|\.)?\s+)?(?:RIT(?:ENUTE|\.)?\s+)?PREVIDENZIALI",
            r"CONTRIBUT[OI]\s+(?:INPS\s+)?C(?:ARICO)?/?\s?DIP",
            r"INPS\s+C(?:ARICO)?/?\s?DIP",
            r"(?:TOT(?:ALE|\.)?\s+)?CONTRIBUTI\s+INPS",
            r"(?:TOT(?:ALE|\.)?\s+)?CONTRIBUTI\s+SOCIALI",
            r"TOT(?:ALE|\.)?\s+CONTRIBUTI",
        ),
        esclusioni=(r"C(?:ARICO)?/?\s?(?:AZIENDA|DITTA)", r"A\s+CARICO\s+(?:AZIENDA|DITTA)"),
        descrizione="I contributi trattenuti a te (non quelli a carico azienda).",
    ),
    Campo(
        "fondo_pensione",
        "Fondo pensione / previdenza compl.",
        "previdenza",
        (
            r"FONDO\s+PENSIONE",
            r"PREVIDENZA\s+COMPLEMENTARE",
            r"FONDO\s+COMPLEMENTARE",
            r"\bCOMETA\b|\bFONCHIM\b|\bFONTE\b|\bPREVINDAI\b",
        ),
    ),
    # ------------------------------------------------------------------ fisco
    Campo(
        "imponibile_fiscale",
        "Imponibile fiscale",
        "fisco",
        (r"IMPONIBILE\s+FISCALE", r"IMPONIBILE\s+IRPEF", r"IMPON\.?\s+FISC\.?"),
    ),
    Campo(
        "irpef_lorda",
        "IRPEF lorda",
        "fisco",
        (r"IRPEF\s+LORDA", r"IMPOSTA\s+LORDA"),
    ),
    Campo(
        "detrazioni",
        "Detrazioni",
        "fisco",
        (
            r"TOT(?:ALE|\.)?\s+DETRAZIONI",
            r"DETRAZIONI\s+(?:LAVORO\s+)?DIPENDENTE",
            r"DETRAZIONI\s+D['\s]?IMPOSTA",
            r"\bDETRAZIONI\b",
        ),
    ),
    Campo(
        "irpef_netta",
        "IRPEF netta",
        "fisco",
        (
            r"IRPEF\s+NETTA",
            r"IMPOSTA\s+NETTA",
            r"RIT(?:ENUTE|\.)?\s+IRPEF",
            r"IRPEF\s+(?:DEL\s+)?MESE",
            r"\bIRPEF\b(?!\s*(?:LORDA|ANNUA))",
        ),
    ),
    Campo(
        "addizionale_regionale",
        "Addizionale regionale",
        "fisco",
        (r"ADD(?:IZIONALE|\.?LE|\.)?\s+REGIONALE", r"\bA\.?\s?REG(?:IONALE)?\b"),
        somma=True,
    ),
    Campo(
        "addizionale_comunale",
        "Addizionale comunale",
        "fisco",
        (r"ADD(?:IZIONALE|\.?LE|\.)?\s+COMUNALE", r"\bA\.?\s?COM(?:UNALE)?\b"),
        somma=True,
    ),
    Campo(
        "trattamento_integrativo",
        "Trattamento integrativo",
        "fisco",
        (
            r"TRATT(?:AMENTO|\.)?\s+INTEGRATIVO",
            r"BONUS\s+IRPEF",
            r"CREDITO\s+D\.?L\.?\s*3",
            r"ULTERIORE\s+DETRAZIONE",
        ),
    ),
    # -------------------------------------------------------------------- TFR
    Campo(
        "tfr_quota",
        "TFR maturato nel mese",
        "tfr",
        (
            r"QUOTA\s+TFR",
            r"TFR\s+(?:DEL\s+)?MESE",
            r"ACCANTONAMENTO\s+TFR",
            r"TFR\s+MATURATO\s+(?:NEL\s+)?MESE",
        ),
    ),
    Campo(
        "tfr_totale",
        "TFR accantonato (totale)",
        "tfr",
        (
            r"TFR\s+NETTO(?:\s+ACCANTONATO)?",
            r"FONDO\s+TFR",
            r"TOT(?:ALE|\.)?\s+TFR",
            r"TFR\s+ACCANTONATO",
        ),
    ),
    # ---------------------------------------------------------------- assenze
    Campo(
        "ferie_residue",
        "Ferie residue",
        "assenze",
        (
            r"FERIE\s+RESIDUE?",
            r"RESIDUO\s+FERIE",
            r"SALDO\s+FERIE",
            r"FERIE\s+SALDO",
        ),
        unita=ORE,
        posizione="ultimo",
    ),
    Campo(
        "ferie_maturate",
        "Ferie maturate",
        "assenze",
        (r"FERIE\s+MATURATE", r"MATURATE\s+FERIE"),
        unita=ORE,
    ),
    Campo(
        "ferie_godute",
        "Ferie godute",
        "assenze",
        (r"FERIE\s+GODUTE", r"GODUTE\s+FERIE", r"FERIE\s+USUFRUITE"),
        unita=ORE,
    ),
    Campo(
        "permessi_residui",
        "Permessi / ROL residui",
        "assenze",
        (
            r"(?:PERMESSI|ROL)\s+RESIDU[IO]",
            r"RESIDUO\s+(?:PERMESSI|ROL)",
            r"SALDO\s+(?:PERMESSI|ROL)",
            r"\bROL\b",
        ),
        unita=ORE,
        posizione="ultimo",
    ),
    Campo(
        "ex_festivita_residue",
        "Ex festività residue",
        "assenze",
        (r"EX\s+FESTIVITA", r"FESTIVITA\s+SOPPRESSE"),
        unita=ORE,
        posizione="ultimo",
    ),
    # ----------------------------------------------------------------- orario
    Campo(
        "ore_lavorate",
        "Ore lavorate",
        "orario",
        (r"ORE\s+(?:LAVORATE|ORDINARIE|RETRIBUITE)", r"TOT(?:ALE|\.)?\s+ORE"),
        unita=ORE,
    ),
    Campo(
        "ore_straordinario",
        "Ore di straordinario",
        "orario",
        (r"ORE\s+STRAORD(?:INARI[OE]|\.)?", r"STRAORD(?:INARIO)?\s+ORE"),
        unita=ORE,
        somma=True,
    ),
    Campo(
        "giorni_retribuiti",
        "Giorni retribuiti",
        "orario",
        (r"(?:GIORNI|GG\.?)\s+(?:RETRIBUIT[IO]|LAVORAT[IO])",),
        unita=GIORNI,
    ),
)

# In quale colonna del cedolino finisce l'importo di ciascuna voce. Serve solo
# quando la busta ha le intestazioni COMPETENZE/TRATTENUTE riconoscibili: in
# quel caso l'importo viene cercato sotto la colonna giusta, cosi' le colonne
# "ore" e "quantita'" non vengono scambiate per un importo.
_IN_COMPETENZE = {
    "totale_competenze", "netto", "arrotondamento", "paga_base", "contingenza",
    "superminimo", "scatti_anzianita", "edr", "straordinari", "indennita", "premi",
    "rimborsi", "buoni_pasto", "tredicesima", "quattordicesima", "trattamento_integrativo",
}
_IN_TRATTENUTE = {
    "totale_trattenute", "contributi_inps", "fondo_pensione", "irpef_netta",
    "addizionale_regionale", "addizionale_comunale",
}

CAMPI = tuple(
    replace(
        campo,
        colonna=(
            "competenze"
            if campo.chiave in _IN_COMPETENZE
            else "trattenute"
            if campo.chiave in _IN_TRATTENUTE
            else ""
        ),
    )
    for campo in CAMPI
)

CAMPI_PER_CHIAVE: dict[str, Campo] = {c.chiave: c for c in CAMPI}

# Regex unica con tutte le etichette: serve a capire dove finisce il "campo
# visivo" di una voce quando su una riga ce ne sono due o piu'.
TUTTE_LE_ETICHETTE = "|".join(f"(?:{p})" for campo in CAMPI for p in campo.pattern)

GRUPPI: dict[str, str] = {
    "riepilogo": "Riepilogo",
    "retribuzione": "Composizione del lordo",
    "previdenza": "Previdenza",
    "fisco": "Fisco",
    "tfr": "TFR",
    "assenze": "Ferie e permessi",
    "orario": "Orario",
}

# Campi su cui si concentrano confronti e grafici.
CAMPI_CHIAVE: tuple[str, ...] = (
    "totale_competenze",
    "totale_trattenute",
    "netto",
    "contributi_inps",
    "irpef_netta",
)

# Etichette che identificano una mensilita' aggiuntiva.
PATTERN_MENSILITA_AGGIUNTIVA = ("tredicesima", "quattordicesima")


@dataclass
class Anagrafica:
    """Dati non numerici utili a controllare che i PDF siano coerenti."""

    azienda: str | None = None
    livello: str | None = None
    qualifica: str | None = None
    ccnl: str | None = None
    campi_extra: dict[str, str] = field(default_factory=dict)


# Il valore finisce a fine riga, dove si apre uno spazio largo, oppure dove
# comincia il campo successivo: pdfplumber a volte comprime gli spazi, quindi
# il doppio spazio da solo non basta come separatore.
_CAMPO_SUCCESSIVO = (
    r"C\.?\s?C\.?\s?N\.?\s?L|LIVELLO|QUALIFICA|MANSIONE|MATRICOLA|DITTA|AZIENDA"
    r"|CODICE|POSIZIONE|SEDE|FILIALE|DIPENDENTE|COD\.|DATA"
)
_FINE_CAMPO = rf"(?=\s{{2,}}|\n|$|\s+(?:{_CAMPO_SUCCESSIVO})\b)"

PATTERN_ANAGRAFICA: dict[str, tuple[str, ...]] = {
    "livello": (rf"LIVELLO\s*:?\s+([A-Z0-9][^\n]{{0,14}}?){_FINE_CAMPO}",),
    "qualifica": (rf"QUALIFICA\s*:?\s+([A-Z][^\n]{{2,28}}?){_FINE_CAMPO}",),
    "ccnl": (rf"C\.?\s?C\.?\s?N\.?\s?L\.?\s*:?\s+([A-Z0-9][^\n]{{2,38}}?){_FINE_CAMPO}",),
    "azienda": (rf"(?:DITTA|AZIENDA|DATORE DI LAVORO)\s*:?\s+([A-Z0-9][^\n]{{2,38}}?){_FINE_CAMPO}",),
}

ETICHETTE_ANAGRAFICA: dict[str, str] = {
    "azienda": "Azienda",
    "ccnl": "CCNL",
    "livello": "Livello",
    "qualifica": "Qualifica",
}
