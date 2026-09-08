(function () {
  const countries = (
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ " +
    "CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR " +
    "GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP " +
    "KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT " +
    "MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW " +
    "SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG " +
    "UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW"
  ).split(" ");

  const currencies = [
    "TRY", "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "JPY", "KRW", "CNY", "INR",
    "BRL", "MXN", "CHF", "SEK", "NOK", "DKK", "PLN", "AED", "SAR", "SGD", "HKD",
  ];

  // Codes identify currencies unambiguously; symbols stay stable in all UI languages.
  // Use established text forms where a dedicated glyph lacks broad font support.
  const currencySymbols = Object.freeze({
    TRY: "₺", USD: "$", EUR: "€", GBP: "£", CAD: "$", AUD: "$", NZD: "$",
    JPY: "¥", KRW: "₩", CNY: "¥", INR: "₹", BRL: "R$", MXN: "$",
    CHF: "Fr.", SEK: "kr", NOK: "kr", DKK: "kr", PLN: "zł",
    AED: "د.إ", SAR: "ر.س", SGD: "$", HKD: "$",
  });
  function currencyLabel(code) {
    const symbol = currencySymbols[code];
    // Isolate the Arabic symbol so it cannot reorder the Latin ISO code.
    return symbol ? `${code} \u2068${symbol}\u2069` : code;
  }

  const euroCountries = "AD AT BE CY DE EE ES FI FR GR HR IE IT LT LU LV MC ME MT NL PT SI SK SM VA".split(" ");
  const currencyForCountry = Object.fromEntries(euroCountries.map(code => [code, "EUR"]));
  Object.assign(currencyForCountry, {
    TR: "TRY", US: "USD", PR: "USD", GU: "USD", VI: "USD", AS: "USD", UM: "USD",
    GB: "GBP", CA: "CAD", AU: "AUD", NZ: "NZD", JP: "JPY", KR: "KRW", CN: "CNY",
    IN: "INR", BR: "BRL", MX: "MXN", CH: "CHF", LI: "CHF", SE: "SEK", NO: "NOK",
    DK: "DKK", FO: "DKK", GL: "DKK", PL: "PLN", AE: "AED", SA: "SAR", SG: "SGD",
    HK: "HKD",
  });

  window.LECTURESIFT_LOCALE_DATA = Object.freeze({countries, currencies, currencyForCountry, currencySymbols, currencyLabel});
})();
