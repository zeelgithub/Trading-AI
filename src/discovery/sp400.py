"""
S&P MidCap 400 constituent tickers -- discovery layer.

Static, hand-refreshed list (same rationale as src/discovery/sp500.py -- see
that module's docstring for why a static list over Alpaca's live
most-actives screener). Sourced 2026-08-24 from Wikipedia's "List of S&P 400
companies" and cross-checked: all 400 tickers below resolve to a real,
currently active, tradable US equity in Alpaca's own asset catalog.

Originally shipped the same day as 379/~400, missing the V-Z tail (~21
names): a raw-wikitext fetch hit a hard content-length limit partway through
the table, and every retry either had the model honestly refuse or, once,
fabricate a plausible-looking but entirely invented ticker list that failed
the Alpaca cross-check and was discarded. Completed same day by reading the
live-rendered page through a real browser instead of a summarizing fetch --
that returns the full table as plain text with no length wall and no model
paraphrasing in the loop, so the recovered tickers (VVV through ZION) came
from the page verbatim, not inferred. Each one was still independently
cross-checked against Alpaca's asset catalog before being added, same bar as
every other ticker in this file.

Boundary: static reference data, no I/O, no live trading decision.
"""

from __future__ import annotations

SOURCED_DATE = "2026-08-24"

SP400_TICKERS: list[str] = [
    "AA", "AAL", "AAON", "ACI", "ACM", "ADC", "AEIS", "AFG", "AGCO", "AHR",
    "AIT", "ALGM", "ALK", "ALLY", "ALSN", "ALV", "AM", "AMG", "AMH", "AMKR",
    "AN", "ANF", "APG", "APPF", "AR", "ARMK", "ARW", "ARWR", "ASB", "ASH",
    "ATI", "ATR", "AVAV", "AVNT", "AVT", "AVTR", "AXTA", "AYI", "BAH", "BBWI",
    "BC", "BCO", "BDC", "BHF", "BILL", "BIO", "BJ", "BKH", "BMRN", "BRKR",
    "BROS", "BRX", "BSY", "BTSG", "BURL", "BWA", "BWXT", "BYD", "CACI", "CAR",
    "CART", "CAVA", "CBSH", "CBT", "CCK", "CDE", "CDP", "CELH", "CFR", "CG",
    "CGNX", "CHDN", "CHE", "CHH", "CHRD", "CHWY", "CLF", "CLH", "CMC", "CNH",
    "CNM", "CNO", "CNX", "COKE", "COLB", "COLM", "CPRI", "CR", "CRBG", "CROX",
    "CRS", "CRUS", "CSL", "CTRE", "CUBE", "CUZ", "CVLT", "CW", "CXT", "CYTK",
    "DAR", "DBX", "DCI", "DINO", "DKS", "DLB", "DOCN", "DOCS", "DOCU", "DT",
    "DTM", "DUOL", "DY", "EEFT", "EGP", "EHC", "ELAN", "ELF", "ELS", "ENS",
    "ENSG", "ENTG", "EPR", "EQH", "ESAB", "ESNT", "EVR", "EWBC", "EXEL", "EXLS",
    "EXP", "EXPO", "FAF", "FBIN", "FCFS", "FCN", "FFIN", "FHI", "FHN", "FIVE",
    "FLG", "FLR", "FLS", "FN", "FNB", "FND", "FNF", "FOUR", "FR", "FTI",
    "G", "GAP", "GATX", "GBCI", "GEF", "GGG", "GHC", "GLPI", "GME", "GMED",
    "GNTX", "GPK", "GWRE", "GXO", "H", "HAE", "HALO", "HGV", "HIMS", "HL",
    "HLI", "HLNE", "HOG", "HOMB", "HQY", "HR", "HRB", "HWC", "HXL", "IBOC",
    "IDA", "IDCC", "IESC", "ILMN", "INGR", "IPGP", "IRT", "ITT", "JAZZ", "JEF",
    "JLL", "KBH", "KBR", "KD", "KEX", "KNF", "KNSL", "KNX", "KRC", "KRG",
    "KRYS", "KTOS", "LAD", "LAMR", "LEA", "LECO", "LFUS", "LIVN", "LNTH", "LOPE",
    "LPX", "LSCC", "LSTR", "M", "MANH", "MAT", "MEDP", "MIDD", "MKSI", "MLI",
    "MMS", "MOG.A", "MOH", "MORN", "MP", "MSA", "MSM", "MTDR", "MTG", "MTN",
    "MTSI", "MTZ", "MUR", "MUSA", "MZTI", "NBIX", "NEU", "NFG", "NJR", "NLY",
    "NNN", "NOV", "NOVT", "NTNX", "NVST", "NVT", "NWE", "NXST", "NXT", "NYT",
    "OC", "OGE", "OGS", "OHI", "OKTA", "OLED", "OLLI", "OLN", "ONB", "ONTO",
    "OPCH", "ORA", "ORI", "OSK", "OVV", "OZK", "P", "PAG", "PATH", "PB",
    "PBF", "PCTY", "PEGA", "PEN", "PFGC", "PII", "PINS", "PK", "PLNT", "PNFP",
    "POR", "POST", "PPC", "PR", "PRI", "PSN", "PVH", "QLYS", "R", "RBA",
    "RBC", "REXR", "RGA", "RGEN", "RGLD", "RH", "RLI", "RMBS", "RNR", "ROIV",
    "ROKU", "RPM", "RRC", "RRX", "RS", "RYAN", "RYN", "SAIA", "SAIC", "SAM",
    "SANM", "SARO", "SBRA", "SCI", "SEIC", "SF", "SFM", "SGI", "SHC", "SIGI",
    "SIRI", "SITM", "SLAB", "SLGN", "SLM", "SMG", "SMTC", "SN", "SNX", "SOLS",
    "SON", "SPXC", "SR", "SSB", "SSD", "ST", "STAG", "STRL", "STWD", "SUI",
    "SWX", "SYNA", "TCBI", "TEX", "THC", "THG", "THO", "TKR", "TLN", "TNL",
    "TOL", "TOST", "TREX", "TRU", "TTC", "TTEK", "TTMI", "TWLO", "TXNM", "TXRH",
    "UBSI", "UFPI", "UGI", "ULS", "UMBF", "UNM", "USFD", "UTHR", "VAL", "VC",
    "VFC", "VIAV", "VICR", "VLY", "VMI", "VNO", "VNOM", "VNT", "VOYA", "VVV",
    "WAL", "WCC", "WEX", "WFRD", "WH", "WHR", "WING", "WLK", "WMG", "WMS",
    "WPC", "WSO", "WTFC", "WTRG", "WTS", "WWD", "XPO", "XRAY", "YETI", "ZION",
]
