"""
S&P SmallCap 600 constituent tickers -- discovery layer.

Static, hand-refreshed list (same rationale as src/discovery/sp500.py -- see
that module's docstring). Sourced 2026-08-24 from Wikipedia's "List of S&P
600 companies" and cross-checked: all 603 tickers below resolve to a real,
currently active, tradable US equity in Alpaca's own asset catalog. One
adjustment from the raw source: "CWEN.A" (Clearway Energy Class A) isn't
separately tradable on Alpaca -- substituted with "CWEN", the class that is.

Originally shipped the same day as 510/~600, missing everything from
roughly STEP through Z (~93 names): the same hard content-length wall
documented in src/discovery/sp400.py, worse here (small-caps generate more,
denser wiki rows than mid-caps, so the cutoff lands earlier alphabetically).
One attempt at extending the raw-wikitext fetch produced a batch of clearly
fabricated tickers (a repeating VOxx/VMxx pattern) that the model itself
flagged as unverifiable, discarded rather than partially trusted. Completed
same day by reading the live-rendered page through a real browser instead --
returns the full table as plain text with no length wall and no summarizing
model in the loop, so the recovered tickers came from the page verbatim.
Each one was still independently cross-checked against Alpaca's asset
catalog before being added, same bar as every other ticker in this file.

Boundary: static reference data, no I/O, no live trading decision.
"""

from __future__ import annotations

SOURCED_DATE = "2026-08-24"

SP600_TICKERS: list[str] = [
    "AAMI", "AAP", "AAT", "ABCB", "ABG", "ABM", "ABR", "ACA", "ACAD", "ACHC",
    "ACIW", "ACLS", "ACMR", "ACT", "ADAM", "ADEA", "ADIG", "ADMA", "ADNT", "ADT",
    "ADUS", "AEO", "AESI", "AGNT", "AGO", "AGX", "AGYS", "AHCO", "AIN", "AIR",
    "AKR", "ALG", "ALGT", "ALHC", "ALKS", "ALRM", "AMN", "AMPH", "AMR", "AMRX",
    "AMSF", "AMTM", "ANDE", "ANIP", "AORT", "AOSL", "APAM", "APLE", "APOG", "ARCB",
    "ARLO", "AROC", "ARR", "ASO", "ASTE", "ASTH", "ATEN", "ATMU", "AUB", "AVA",
    "AWI", "AWR", "AX", "AZTA", "AZZ", "BANC", "BANF", "BANR", "BBT", "BCC",
    "BCPC", "BFAM", "BFH", "BFS", "BGC", "BHE", "BJRI", "BKE", "BKU", "BL",
    "BLFS", "BLKB", "BMI", "BNL", "BOH", "BOOT", "BOX", "BRC", "BTU", "BXMT",
    "CACC", "CAG", "CAKE", "CALM", "CALX", "CALY", "CARG", "CASH", "CATY", "CBRL",
    "CBU", "CC", "CCOI", "CCS", "CE", "CENT", "CENTA", "CENX", "CERT", "CFFN",
    "CHCO", "CHEF", "CLSK", "CNK", "CNMD", "CNR", "CNS", "CNXC", "CNXN", "COCO",
    "COHU", "COLL", "CON", "CORT", "COTY", "CPB", "CPF", "CPK", "CRC", "CRGY",
    "CRI", "CRK", "CRSR", "CRVL", "CSR", "CSW", "CTS", "CUBI", "CURB", "CVBF",
    "CVCO", "CVI", "CVSA", "CWEN", "CWK", "CWST", "CWT", "CXM", "CXW", "CZR",
    "DAN", "DAVE", "DBD", "DCH", "DCOM", "DEA", "DEI", "DFH", "DFIN", "DGII",
    "DIOD", "DLX", "DMC", "DNOW", "DORM", "DRH", "DV", "DXC", "DXPE", "EAT",
    "EBC", "ECG", "ECPG", "EFC", "EFOR", "EGBN", "EIG", "EMN", "ENOV", "ENPH",
    "ENR", "ENVA", "EPAC", "EPAM", "EPC", "EPRT", "ESE", "ESI", "ETSY", "EVTC",
    "EXTR", "EYE", "EZPW", "FA", "FBK", "FBNC", "FBP", "FBRT", "FCF", "FCPT",
    "FELE", "FFBC", "FG", "FHB", "FIBK", "FIVN", "FIZZ", "FLO", "FMC", "FORM",
    "FOXF", "FRPT", "FSS", "FTDR", "FTRE", "FUL", "FULT", "FUN", "GBX", "GEO",
    "GFF", "GIII", "GKOS", "GNL", "GNW", "GO", "GOLF", "GPI", "GPOR", "GRBK",
    "GSHD", "GT", "GTES", "GTM", "GTY", "GVA", "HAFC", "HASI", "HAYW", "HCC",
    "HCI", "HCSG", "HE", "HFWA", "HIW", "HLIT", "HLX", "HMN", "HNI", "HOPE",
    "HP", "HRMY", "HSTM", "HTH", "HTLD", "HTO", "HUBG", "HWKN", "HZO", "IART",
    "IBP", "ICHR", "ICUI", "IIPR", "INDB", "INDV", "INSP", "INSW", "INVA", "INVX",
    "IOSP", "IPAR", "IRDM", "ITGR", "ITRI", "IVT", "JBGS", "JBLU", "JBSS", "JBTM",
    "JJSF", "JOE", "JXN", "KAI", "KALU", "KFY", "KGS", "KLIC", "KMPR", "KMT",
    "KMX", "KN", "KNTK", "KOP", "KRMN", "KSS", "KTB", "KWR", "LAUR", "LAZ",
    "LBRT", "LCII", "LEG", "LEU", "LFST", "LGIH", "LIF", "LGND", "LKFN", "LKQ",
    "LMAT", "LNC", "LNN", "LPG", "LQDA", "LQDT", "LRN", "LTC", "LTH", "LUMN",
    "LW", "LXP", "LYFT", "LZ", "LZB", "MAC", "MAN", "MARA", "MATW", "MATX",
    "MBC", "MBGL", "MBIN", "MC", "MCRI", "MCY", "MD", "MDU", "MFP", "MGEE",
    "MGY", "MHK", "MHO", "MIR", "MKTX", "MLKN", "MMI", "MMSI", "MPT", "MRCY",
    "MRP", "MRTN", "MSEX", "MSGS", "MTCH", "MTH", "MTRN", "MTUS", "MTX", "MWA",
    "MXL", "MYRG", "NABL", "NATL", "NAVI", "NBHC", "NBTB", "NE", "NEO", "NEOG",
    "NGVT", "NHC", "NHI", "NIC", "NMIH", "NOG", "NPK", "NPO", "NSIT", "NSP",
    "NSSC", "NTCT", "NTST", "NWBI", "NWL", "NWN", "NX", "NXRT", "OFG", "OGN",
    "OI", "OII", "OMCL", "OPLN", "OSIS", "OSW", "OTTR", "OUT", "PAHC", "PARR",
    "PAYC", "PAYO", "PATK", "PBH", "PBI", "PCRX", "PDFS", "PEB", "PECO", "PENG",
    "PENN", "PFBC", "PFS", "PGNY", "PHIN", "PI", "PIPR", "PJT", "PLAB", "PLMR",
    "PLUS", "PLXS", "PMT", "POOL", "POWI", "POWL", "PPLI", "PRDO", "PRG", "PRGO",
    "PRGS", "PRIM", "PRK", "PRKS", "PRLB", "PRSU", "PRVA", "PSMT", "PTCT", "PTEN",
    "PTGX", "PTON", "PZZA", "QDEL", "QNST", "QRVO", "QTWO", "RAMP", "RAL", "RCUS",
    "RDN", "RDNT", "RELY", "RES", "REYN", "REX", "REZI", "RHI", "RHP", "RITM",
    "RNG", "RNST", "ROAD", "ROCK", "ROG", "RRR", "RSI", "RUN", "RUSHA", "RXO",
    "SAFE", "SABR", "SAFT", "SAH", "SBCF", "SBH", "SBSI", "SCHL", "SCL", "SCSC",
    "SDGR", "SEDG", "SEI", "SEZL", "SFBS", "SFNC", "SHAK", "SHEN", "SHO", "SHOO",
    "SIG", "SIGI", "SKT", "SKY", "SKYW", "SLG", "SLVM", "SM", "SMP", "SMPL",
    "SNDR", "SNEX", "SONO", "SPHR", "SPNT", "SPSC", "SRPT", "STAA", "STBA", "STC",
    "STEP", "STRA", "SUPN", "SXI", "SXT", "TALO", "TBBK", "TDC", "TDS", "TDW",
    "TFIN", "TFX", "TGTX", "THRM", "TILE", "TMDX", "TMP", "TNC", "TNDM", "TPC",
    "TR", "TRIP", "TRMK", "TRN", "TRNO", "TRST", "TRUP", "UA", "UAA", "UCB",
    "UCTT", "UE", "UFCS", "UFPT", "UNF", "UNFI", "UNIT", "UPBD", "UPWK", "URBN",
    "USLM", "USPH", "UTI", "UTL", "UVV", "VAC", "VCEL", "VCTR", "VCYT", "VECO",
    "VGNT", "VIR", "VIRT", "VRRM", "VRTS", "VSAT", "VSEC", "VSH", "VSNT", "VSTS",
    "VSXY", "VTOL", "VVX", "VYX", "WABC", "WAFD", "WAY", "WD", "WDFC", "WEN",
    "WERN", "WGO", "WHD", "WINA", "WKC", "WLY", "WOR", "WRBY", "WRLD", "WS",
    "WSBC", "WSC", "WSFS", "WT", "WU", "WWW", "XHR", "XNCR", "XPEL", "YELP",
    "YOU", "ZD", "ZWS",
]
