"""Analisi e confronto delle buste paga italiane in PDF."""

from .analisi import Analisi, analizza
from .estrazione import leggi_busta, leggi_csv, leggi_percorsi, scrivi_csv
from .modello import BustaPaga, Voce

__all__ = [
    "Analisi",
    "BustaPaga",
    "Voce",
    "analizza",
    "leggi_busta",
    "leggi_csv",
    "leggi_percorsi",
    "scrivi_csv",
]

__version__ = "0.1.0"
