"""
Small/micro-cap discovery-universe tickers -- discovery layer.

NOT an index membership list (unlike sp500.py/sp400.py/sp600.py) -- there is
no free, scrapable Russell 2000 (or similar) constituent list (see
scripts/build_smallcap_universe.py's module docstring for what was tried and
why it doesn't exist as a static list here). This is instead a data-derived
screen, built by that script: Nasdaq+NYSE bulk listing files, filtered to
real common stock (no ETFs/warrants/units/rights/preferred/ADRs/SPACs),
excluding anything already in the existing discovery universe, cross-checked
against Alpaca's tradable asset catalog, price/liquidity-screened via
Alpaca's own 30-day bars, and market-cap-banded via yfinance ($50M-$6B
-- below/adjacent to S&P 600's range, not a re-hash of it).

UPDATED 2026-08-24, same day as the initial build: the price and liquidity
floors were REMOVED at the user's explicit request (originally price >= $5 /
avg dollar volume >= $500k/day, matching discovery.min_price -- see
docs/ROADMAP.md Phase H for the full request and risk disclosure). Now only
requires last close > $0 and avg dollar volume > $0 (excludes bad-data and
never-traded tickers, nothing more) -- so this list CAN and DOES include
sub-$5, thin-volume names as long as their market cap fits the band above.
The runtime `discovery.min_price` guard (config/settings.yaml) is the only
remaining price-based protection, and it is ALSO set to $0 in this
deployment -- penny-stock inclusion is deliberate here, not an oversight.

Sourced 2026-08-24. Regenerate periodically with
`python -m scripts.build_smallcap_universe` -- this is a snapshot, not a
live feed; names drift in and out of the market-cap band over time.

Boundary: static reference data, no I/O, no live trading decision.
"""

from __future__ import annotations

SOURCED_DATE = "2026-08-24"

SMALLCAP_TICKERS: list[str] = [
    "AARD", "ABAT", "ABOS", "ABTC", "ABX", "ACB", "ACCO", "ACDC", "ACEL", "ACET",
    "ACFN", "ACH", "ACIC", "ACIU", "ACNB", "ACNT", "ACOG", "ACR", "ACRE", "ACRV",
    "ACTG", "ACU", "AD", "ADCT", "ADUR", "ADV", "AEBI", "AEC", "AEI", "AENT",
    "AEYE", "AFCG", "AFYA", "AGBK", "AGM", "AGMB", "AGPU", "AGRO", "AIAI", "AIB",
    "AIBZ", "AIFC", "AII", "AIOT", "AIRG", "AIRJ", "AIRO", "AIRS", "AIRT", "AISP",
    "AIV", "AKA", "AKBA", "AKTS", "ALCO", "ALDX", "ALEC", "ALIT", "ALLO", "ALMU",
    "ALOT", "ALRS", "ALT", "ALTG", "ALTI", "ALTO", "ALX", "ALXO", "AMAL", "AMC",
    "AMCI", "AMPG", "AMPY", "AMTB", "AMTX", "AMWL", "ANGI", "ANGO", "ANGX", "ANIK",
    "ANIX", "ANNA", "ANTX", "ANVS", "AOMR", "AOUT", "AP", "APC", "API", "APMD",
    "APT", "APWC", "APYX", "AQN", "AQST", "ARDT", "ARDX", "AREC", "ARI", "ARKO",
    "ARL", "ARMP", "AROW", "ARQ", "ARRY", "ARTNA", "ARTV", "ARVN", "ASA", "ASIC",
    "ASIX", "ASLE", "ASMB", "ASPI", "ASPN", "ASPS", "ASRV", "ASTL", "ASUR", "ASYS",
    "ATAI", "ATKR", "ATLO", "ATLX", "ATNI", "ATOM", "ATRA", "ATS", "ATYR", "AUBN",
    "AURA", "AVBC", "AVBH", "AVD", "AVIR", "AVLN", "AVNW", "AVR", "AVXL", "AXR",
    "AZ", "BAER", "BAFN", "BAK", "BALY", "BARK", "BATL", "BATRA", "BBAI", "BBCP",
    "BBDC", "BBOT", "BCAL", "BCBP", "BCML", "BCSF", "BCX", "BDL", "BDSX", "BDTX",
    "BEEP", "BETR", "BFC", "BFST", "BGS", "BGSF", "BGSI", "BHB", "BHR", "BHRB",
    "BKKT", "BKTI", "BLDP", "BLND", "BLNK", "BLSM", "BMBL", "BMEA", "BMM", "BMRC",
    "BNAI", "BNC", "BNED", "BNTC", "BOC", "BOF", "BOLD", "BOOM", "BORR", "BOT",
    "BOTJ", "BOW", "BPRN", "BRAI", "BRBS", "BRCB", "BRCC", "BRID", "BRR", "BRSP",
    "BRT", "BRVE", "BSBK", "BSEM", "BSET", "BSIN", "BSRR", "BSVN", "BTCS", "BTE",
    "BTGO", "BTQ", "BUDA", "BUSE", "BVFL", "BWB", "BWEN", "BWFG", "BWLP", "BXBL",
    "BY", "BYFC", "BYND", "BYRN", "BZAI", "BZFD", "BZH", "CAAP", "CABA", "CABO",
    "CAC", "CAL", "CAMP", "CARL", "CARS", "CASS", "CAST", "CATO", "CATX", "CBAN",
    "CBFV", "CBIO", "CBK", "CBNA", "CBNK", "CBUS", "CBZ", "CCAP", "CCBG", "CCCC",
    "CCEC", "CCLD", "CCNE", "CCO", "CCSI", "CCU", "CDXS", "CDZI", "CEPL", "CERS",
    "CET", "CFBK", "CFFI", "CGC", "CGTX", "CHCI", "CHGG", "CHMG", "CHMI", "CHPT",
    "CHRN", "CHRS", "CIA", "CIM", "CING", "CINT", "CION", "CIRC", "CITR", "CIVB",
    "CIX", "CLAR", "CLB", "CLDT", "CLFD", "CLMB", "CLNE", "CLNN", "CLOV", "CLPR",
    "CLPT", "CLW", "CMCL", "CMDB", "CMPX", "CMRC", "CMRE", "CMT", "CMTL", "CMTV",
    "CNDT", "CNL", "CNNE", "CNOB", "CNTN", "CNVS", "CNXU", "COCH", "CODA", "CODI",
    "COFS", "COOK", "COPR", "COSO", "COYA", "CPBI", "CPHC", "CPIX", "CPS", "CPSH",
    "CPSS", "CRBP", "CRBU", "CRCT", "CRDF", "CRDL", "CRMD", "CRNC", "CRON", "CSAN",
    "CSBR", "CSPI", "CSV", "CSWC", "CTEV", "CTGO", "CTKB", "CTM", "CTMX", "CTNM",
    "CTO", "CTOR", "CTRN", "CUE", "CURI", "CURV", "CV", "CVEO", "CVGI", "CVLG",
    "CVRX", "CVU", "CVV", "CWBC", "CXDO", "CYD", "CYH", "CYPH", "CZFS", "CZNC",
    "CZWI", "DAKT", "DBI", "DBRG", "DCBO", "DDD", "DEFT", "DERM", "DFDV", "DGICA",
    "DGXX", "DH", "DHX", "DIBS", "DIN", "DLHC", "DMAC", "DMRC", "DNA", "DNUT",
    "DOMH", "DOUG", "DPRO", "DRIO", "DRUG", "DSGR", "DSP", "DSWL", "DSX", "DTCX",
    "DTI", "DTIL", "DVLT", "DWSN", "DWTX", "DX", "EAF", "EARN", "EBF", "EBMT",
    "EBS", "ECBK", "ECC", "ECOR", "EDIT", "EDRY", "EFSC", "EFSI", "EGAN", "EGHT",
    "EGY", "EH", "EIC", "EIKN", "ELA", "ELBM", "ELDN", "ELE", "ELMD", "ELME",
    "ELMT", "ELOX", "ELTX", "ELVA", "EMAT", "EMBC", "EML", "EMPD", "ENGN", "ENHA",
    "ENTA", "ENVX", "EOSE", "EP", "EPM", "EPRX", "EPSN", "EQ", "EQBK", "ERII",
    "EROK", "ESCA", "ESEA", "ESOA", "ESP", "ETD", "ETO", "EU", "EVCM", "EVEX",
    "EVGO", "EVH", "EVI", "EVMN", "EXFY", "EXOD", "EXOZ", "EYPT", "FAC", "FATE",
    "FATN", "FBDT", "FBIO", "FBIZ", "FBLA", "FBRX", "FBYD", "FC", "FCAP", "FCBC",
    "FCBM", "FCCO", "FDBC", "FDSB", "FEAM", "FRME", "FSUN", "GBTG", "GSBD", "HBNC",
    "HTGC", "IVR", "JMKE", "KARD", "KRNY", "KRUS", "LADR", "LYNX", "MAIN", "MAMA",
    "MEI", "MFA", "MGRC", "NBBK", "NERV", "NEWP", "NEWT", "NEXA", "NEXM", "NFE",
    "NFGC", "NGEN", "NGNE", "NGS", "NGVC", "NINE", "NKSH", "NKTX", "NL", "NLOP",
    "NMAD", "NMFC", "NMG", "NMRA", "NNBR", "NNI", "NOA", "NODK", "NOEM", "NPB",
    "NPCE", "NPWR", "NRC", "NRDY", "NREF", "NRGV", "NRIM", "NRP", "NRXP", "NRXS",
    "NSTS", "NTHI", "NTIC", "NTRB", "NUAI", "NUCL", "NUS", "NVA", "NVCT", "NVRI",
    "NWAX", "NWFL", "NXDR", "NXH", "NXP", "OABI", "OBE", "OBK", "OBT", "OBX",
    "OCC", "OCFC", "OCGN", "ODTX", "ODYS", "OEC", "OESX", "OFIX", "OFLX", "OFRM",
    "OGG", "OGI", "OIS", "OKUR", "OLP", "OM", "OMEX", "ONCY", "ONEW", "ONIT",
    "ONL", "OOMA", "OPAL", "OPBK", "OPEN", "OPFI", "OPHC", "OPK", "OPRT", "OPRX",
    "OPTU", "OPTX", "OPXS", "OPY", "ORBS", "ORC", "ORGO", "ORMP", "ORN", "ORRF",
    "OSBC", "OSG", "OSPN", "OSS", "OSTX", "OSUR", "OTLK", "OVBC", "OVID", "OVLY",
    "OWLS", "OWLT", "OXM", "PACB", "PACK", "PAGP", "PAL", "PALI", "PAMT", "PANL",
    "PARK", "PAYS", "PBAM", "PBFS", "PBHC", "PBYI", "PCB", "PCYO", "PDCC", "PDEX",
    "PDLB", "PDYN", "PEBK", "PEBO", "PED", "PEPG", "PESI", "PEW", "PFIS", "PFLT",
    "PFX", "PGC", "PHI", "PICS", "PKBK", "PKE", "PKOH", "PLBC", "PLBY", "PLCE",
    "PLRX", "PLTK", "PLUG", "PLX", "PLYX", "PMN", "PMTS", "PMVP", "PNBK", "PNNT",
    "PNRG", "PODC", "POWW", "PPHC", "PPIH", "PRAA", "PRLD", "PRME", "PROF", "PROP",
    "PROV", "PRPO", "PRTH", "PRTS", "PSBD", "PSFE", "PSNY", "PSNYW", "PTHS", "PTLO",
    "QTTB", "REF", "RNW", "SMA", "SPIR", "STLN", "SYBT", "TCBK", "TOWN", "TRIN",
    "TSLX", "TWO", "UMH", "UTZ", "UVSP", "WMK", "WTM", "XNDU",
]
