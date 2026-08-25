"""
S&P 500 constituent tickers -- discovery layer.

A static, hand-refreshed list, not a live index feed -- there's no live S&P
500 membership API wired into this project (Alpaca's asset catalog doesn't
carry index membership). This is a deliberate choice over Alpaca's
`ScreenerClient.get_most_actives` (tested live, 2026-08-24): that endpoint
caps at 100 results and ranks by raw share VOLUME, which surfaces mostly
penny stocks and leveraged/inverse ETFs (checked live: top hits included
sub-$5 microcaps and TSLL, a 2x leveraged Tesla ETF) rather than quality
large-caps. A static membership list has the opposite tradeoff: it doesn't
adapt to what's trading heavily today, but every name is an established,
real company.

Sourced 2026-08-24 from Wikipedia's "List of S&P 500 companies" (503 tickers
-- the index holds >500 constituents because some companies have multiple
share classes, e.g. GOOGL/GOOG, BRK.B). Cross-checked against every one of
these 503 tickers resolving to a real, currently active, tradable US equity
in Alpaca's own asset catalog (`TradingClient.get_all_assets`) -- catches
scrape corruption, not membership-list drift. Several tickers that looked
like scrape artifacts on first read turned out to be real, very recent
corporate actions confirmed this way: FDXF (FedEx's 2026 freight spinoff),
HONA (Honeywell's aerospace spinoff), Q (DuPont's Qnity Electronics
spinoff), PSKY (the 2026 Paramount-Skydance merger), SW (Smurfit WestRock),
SOLV (3M's Solventum spinoff) -- current market reality moves faster than
any static list, which is exactly why this needs periodic re-sourcing, not
a one-time copy-paste.

Boundary: static reference data, no I/O, no live trading decision.
"""

from __future__ import annotations

SOURCED_DATE = "2026-08-24"

SP500_TICKERS: list[str] = [
    "MMM", "AOS", "ABT", "ABBV", "ACN", "ADBE", "AMD", "AES", "AFL", "A",
    "APD", "ABNB", "AKAM", "ALB", "ARE", "ALGN", "ALLE", "LNT", "ALL", "GOOGL",
    "GOOG", "MO", "AMZN", "AMCR", "AEE", "AEP", "AXP", "AIG", "AMT", "AWK",
    "AMP", "AME", "AMGN", "APH", "ADI", "AON", "APA", "APO", "AAPL", "AMAT",
    "APP", "APTV", "ACGL", "ADM", "ARES", "ANET", "AJG", "AIZ", "T", "ATO",
    "ADSK", "ADP", "AZO", "AVY", "AXON", "BKR", "BALL", "BAC", "BAX", "BDX",
    "BRK.B", "BBY", "TECH", "BIIB", "BLK", "BX", "XYZ", "BNY", "BA", "BKNG",
    "BSX", "BMY", "AVGO", "BR", "BRO", "BF.B", "BLDR", "BG", "BXP", "CHRW",
    "CDNS", "CPT", "COF", "CAH", "CCL", "CARR", "CVNA", "CASY", "CAT", "CBOE",
    "CBRE", "CDW", "COR", "CNC", "CNP", "CF", "CRL", "SCHW", "CHTR", "CVX",
    "CMG", "CB", "CHD", "CIEN", "CI", "CINF", "CTAS", "CSCO", "C", "CFG",
    "CLX", "CME", "CMS", "KO", "CTSH", "COHR", "COIN", "CL", "CMCSA", "FIX",
    "COP", "ED", "STZ", "CEG", "COO", "CPRT", "GLW", "CPAY", "CTVA", "CSGP",
    "COST", "CRH", "CRWD", "CCI", "CSX", "CMI", "CVS", "DHR", "DRI", "DDOG",
    "DVA", "DECK", "DE", "DELL", "DAL", "DVN", "DXCM", "FANG", "DLR", "DG",
    "DLTR", "D", "DPZ", "DASH", "DOV", "DOW", "DHI", "DTE", "DUK", "DD",
    "ETN", "EBAY", "ECHO", "ECL", "EIX", "EW", "ELV", "EME", "EMR", "ETR",
    "EOG", "EQT", "EFX", "EQIX", "ERIE", "ESS", "EL", "EG", "EVRG", "ES",
    "EXC", "EXE", "EXPE", "EXPD", "EXR", "XOM", "FFIV", "FDS", "FICO", "FAST",
    "FRT", "FDX", "FDXF", "FERG", "FIS", "FITB", "FSLR", "FE", "FISV", "FLEX",
    "F", "FTNT", "FTV", "FOXA", "FOX", "BEN", "FCX", "GRMN", "IT", "GE",
    "GEHC", "GEV", "GEN", "GNRC", "GD", "GIS", "GM", "GPC", "GILD", "GPN",
    "GL", "GDDY", "GS", "HAL", "HIG", "HAS", "HCA", "DOC", "HSIC", "HSY",
    "HPE", "HLT", "HD", "HONA", "HON", "HRL", "HST", "HWM", "HPQ", "HUBB",
    "HUM", "HBAN", "HII", "IBM", "IEX", "IDXX", "ITW", "INCY", "IR", "PODD",
    "INTC", "IBKR", "ICE", "IFF", "IP", "INTU", "ISRG", "IVZ", "INVH", "IQV",
    "IRM", "JBHT", "JBL", "JKHY", "J", "JNJ", "JCI", "JPM", "KVUE", "KDP",
    "KEY", "KEYS", "KMB", "KIM", "KMI", "KKR", "KLAC", "KHC", "KR", "LHX",
    "LH", "LRCX", "LVS", "LDOS", "LEN", "LII", "LLY", "LIN", "LYV", "LMT",
    "L", "LOW", "LULU", "LITE", "LYB", "MTB", "MPC", "MAR", "MRSH", "MLM",
    "MRVL", "MAS", "MA", "MKC", "MCD", "MCK", "MDT", "MRK", "META", "MET",
    "MTD", "MGM", "MCHP", "MU", "MSFT", "MAA", "MRNA", "TAP", "MDLZ", "MPWR",
    "MNST", "MCO", "MS", "MOS", "MSI", "MSCI", "NDAQ", "NTAP", "NFLX", "NEM",
    "NWSA", "NWS", "NEE", "NKE", "NI", "NDSN", "NSC", "NTRS", "NOC", "NCLH",
    "NRG", "NUE", "NVDA", "NVR", "NXPI", "ORLY", "OXY", "ODFL", "OMC", "ON",
    "OKE", "ORCL", "OTIS", "PCAR", "PKG", "PLTR", "PANW", "PSKY", "PH", "PAYX",
    "PYPL", "PNR", "PEP", "PFE", "PCG", "PM", "PSX", "PNW", "PNC", "PPG",
    "PPL", "PFG", "PG", "PGR", "PLD", "PRU", "PEG", "PTC", "PSA", "PHM",
    "PWR", "QCOM", "DGX", "Q", "RL", "RJF", "RDDT", "RTX", "O", "REG",
    "REGN", "RF", "RSG", "RMD", "RVTY", "HOOD", "ROK", "ROL", "ROP", "ROST",
    "RCL", "SPGI", "CRM", "SNDK", "SBAC", "SLB", "STX", "SRE", "NOW", "SHW",
    "SPG", "SWKS", "SJM", "SW", "SNA", "SOLV", "SO", "LUV", "SWK", "SBUX",
    "STT", "STLD", "STE", "SYK", "SMCI", "SYF", "SNPS", "SYY", "TMUS", "TROW",
    "TTWO", "TPR", "TRGP", "TGT", "TEL", "TDY", "TER", "TSLA", "TXN", "TPL",
    "TXT", "TMO", "TJX", "TKO", "TTD", "TSCO", "TT", "TDG", "TRV", "TRMB",
    "TFC", "TYL", "TSN", "USB", "UBER", "UDR", "ULTA", "UNP", "UAL", "UPS",
    "URI", "UNH", "UHS", "VLO", "VEEV", "VTR", "VLTO", "VRSN", "VRSK", "VZ",
    "VRTX", "VRT", "VTRS", "VICI", "V", "VST", "VMRK", "VMC", "WRB", "GWW",
    "WAB", "WMT", "DIS", "WBD", "WM", "WAT", "WEC", "WFC", "WELL", "WST",
    "WDC", "WY", "WSM", "WMB", "WTW", "WDAY", "WYNN", "XEL", "XYL", "YUM",
    "ZBRA", "ZBH", "ZTS",
]
