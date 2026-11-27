"""Published connections with explicitly simulated shipment economics."""

COCHIN_COLOMBO = "https://www.maersk.com/local-information/imea/india/routes"
MANGALURU_COLOMBO = "https://www.dpworld.com/en/news/india/dp-world-cochin-colombo-second-weekly-service"
CHENNAI_COLOMBO = "https://www.maersk.com/news/articles/2025/02/11/middle-east-to-europe-services"
INDIA_GULF = "https://in.one-line.com/sites/g/files/lnzjqr1596/files/2025-06/India%20Gulf%20Service%20%28IGS%29%20Launch.pdf"
STRAITS_INDIA = "https://www.dpworld.com/en/news/india/dp-world-introduces-a-new-weekly-service-between-india-far-east"
INDIA_EUROPE = "https://www.maersk.com/news/articles/2026/11/02/me1-gemini-service-transitions-to-suez-route"
INDIA_ASIA = "https://www.cma-cgm.com/assets/public/documents/IEX%202_2.pdf"

# USD/kg, hours, risk and capacities are fixtures. A connection can represent
# consecutive calls or a published route segment with intermediate calls.
PUBLISHED_SEA_LEGS = (
    ("NORTH-INDIA-GULF-1", "dubai", "mundra", 144, .035, INDIA_GULF),
    ("NORTH-INDIA-GULF-2", "mundra", "dubai", 168, .037, INDIA_GULF),
    ("NHAVA-SHEVA-JEBEL-ALI", "mumbai", "dubai", 168, .042, INDIA_GULF),
    ("MUNDRA-SINGAPORE", "mundra", "singapore", 288, .039, STRAITS_INDIA),
    ("NHAVA-SHEVA-SINGAPORE", "mumbai", "singapore", 264, .025, STRAITS_INDIA),
    ("KOLKATA-SINGAPORE", "kolkata", "singapore", 216, .043, COCHIN_COLOMBO),
    ("KOCHI-COLOMBO", "kochi", "colombo", 48, .026, COCHIN_COLOMBO),
    ("COLOMBO-KOCHI", "colombo", "kochi", 48, .026, COCHIN_COLOMBO),
    ("MANGALURU-KOCHI", "mangaluru", "kochi", 36, .018, MANGALURU_COLOMBO),
    ("COLOMBO-MANGALURU", "colombo", "mangaluru", 72, .031, MANGALURU_COLOMBO),
    ("COLOMBO-CHENNAI", "colombo", "chennai", 72, .033, CHENNAI_COLOMBO),
    ("CHENNAI-COLOMBO", "chennai", "colombo", 72, .033, CHENNAI_COLOMBO),
    ("SINGAPORE-CHENNAI", "singapore", "chennai", 120, .038, INDIA_ASIA),
    ("CHENNAI-SINGAPORE", "chennai", "singapore", 192, .039, INDIA_ASIA),
    ("NHAVA-SHEVA-SALALAH", "mumbai", "salalah", 144, .023, INDIA_EUROPE),
    ("SALALAH-ROTTERDAM", "salalah", "rotterdam", 456, .035, INDIA_EUROPE),
)

DOMESTIC_SEA_TRANSFERS = {
    ("kochi", "chennai"): ("colombo", COCHIN_COLOMBO, CHENNAI_COLOMBO),
    ("chennai", "kochi"): ("colombo", CHENNAI_COLOMBO, COCHIN_COLOMBO),
}

# Publication references are useful leads. Quotation, cargo acceptance and
# equipment availability require separate evidence.
PUBLISHED_SERVICES = {
    COCHIN_COLOMBO: "Maersk route directory",
    MANGALURU_COLOMBO: "Bengal Tiger Line (published by DP World)",
    CHENNAI_COLOMBO: "Maersk ME2",
    INDIA_GULF: "Ocean Network Express India Gulf Service",
    STRAITS_INDIA: "Feedertech Straits India Service (published by DP World)",
    INDIA_EUROPE: "Maersk and Hapag-Lloyd ME1",
    INDIA_ASIA: "CMA CGM IEX 2",
}
