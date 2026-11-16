"""ISO code lists the guardrails accept. Written out by hand: no dependency, no network.

Faker's lists aren't usable as a source: they miss ISO countries and include codes that
aren't ISO (GGP, IMP, NIS) or were withdrawn (HRK, VEF, ZWD).
"""

# ISO 4217 currencies in circulation, as of 2026. Fund, precious-metal and testing codes
# (BOV, CHE, CLF, XAU, XDR, XTS, XXX, ...) are left out: an invoice is never priced in
# them. Codes in a changeover stay while invoices up to two years old may carry them:
# ANG (to XCG, 2025) and BGN (Bulgaria joined the euro, 2026).
CURRENCIES: frozenset[str] = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD
    BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB
    EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK
    JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA
    MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK
    PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN
    SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VED VES VND VUV
    WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG
    """.split()  # noqa: SIM905 - rows of codes read and review better than a list
)

# ISO 3166-1 alpha-2: all 249 officially assigned codes.
COUNTRIES: frozenset[str] = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE
    BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD
    CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM
    DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF
    GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU
    ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN
    KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME
    MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA
    NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM
    PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI
    SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK
    TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI
    VN VU WF WS YE YT ZA ZM ZW
    """.split()  # noqa: SIM905 - rows of codes read and review better than a list
)
