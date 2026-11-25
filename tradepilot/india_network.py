"""Offline road planning graph. Corridor references do not verify truck access or rates."""

EXTRA_CITY_COORDS = {
    "agra": (27.18, 78.01), "kanpur": (26.45, 80.33), "varanasi": (25.32, 82.97),
    "prayagraj": (25.44, 81.85), "vijayawada": (16.51, 80.65), "salem": (11.66, 78.15),
    "thrissur": (10.53, 76.21), "krishnagiri": (12.52, 78.21), "hosur": (12.74, 77.83),
    "vadodara": (22.31, 73.18), "belagavi": (15.85, 74.50), "satara": (17.69, 74.00),
    "gwalior": (26.22, 78.18), "jhansi": (25.45, 78.57), "jabalpur": (23.18, 79.99),
    "kharagpur": (22.35, 87.32), "siliguri": (26.73, 88.40), "silchar": (24.83, 92.78),
    "ludhiana": (30.90, 75.86), "ambala": (30.38, 76.78), "jalandhar": (31.33, 75.58),
    "jammu": (32.73, 74.86), "srinagar": (34.08, 74.80), "kanyakumari": (8.09, 77.54),
    "rajkot": (22.30, 70.80), "porbandar": (21.64, 69.63), "udaipur": (24.59, 73.71),
    "ajmer": (26.45, 74.64), "hubballi": (15.36, 75.12), "tumakuru": (13.34, 77.10),
    "bardhaman": (23.23, 87.86), "dhanbad": (23.80, 86.43), "ranchi": (23.34, 85.31),
    "kozhikode": (11.26, 75.78),
}

MORTH_CORRIDORS = (
    "https://morth.nic.in/sites/default/files/circulars_document/Approval%20and%20Expenditure%20on%20Projects.pdf"
)
MORTH_HIGHWAY_MAP = (
    "https://morth.nic.in/sites/default/files/PragatiKiNayiGati/gujarat/files/assets/common/downloads/GUJARAT%20GETS%20GOING%20.pdf"
)

# Selected planning waypoints. Adjacent entries are graph edges. Long segments
# may omit intermediate towns. Regional feeder links are planning assumptions.
ROAD_CHAINS = (
    ("capital-to-west", ("delhi", "jaipur", "ajmer", "udaipur", "ahmedabad", "vadodara", "surat", "mumbai")),
    ("western-deccan", ("mumbai", "pune", "satara", "belagavi", "hubballi", "tumakuru", "bengaluru")),
    ("south-east", ("bengaluru", "hosur", "krishnagiri", "chennai", "vijayawada", "visakhapatnam", "bhubaneswar", "kharagpur", "kolkata")),
    ("gangetic-east", ("delhi", "agra", "kanpur", "prayagraj", "varanasi", "dhanbad", "bardhaman", "kolkata")),
    ("central-north-south", ("delhi", "gwalior", "jhansi", "nagpur", "hyderabad", "bengaluru", "salem", "madurai", "kanyakumari")),
    ("kerala-tamil-nadu", ("kochi", "thrissur", "coimbatore", "salem", "krishnagiri", "chennai")),
    ("northern-spine", ("delhi", "ambala", "ludhiana", "jalandhar", "jammu", "srinagar")),
    ("western-port-feeder", ("porbandar", "rajkot", "ahmedabad")),
    ("regional-feeder", ("pune", "indore", "bhopal", "jabalpur", "nagpur")),
    ("regional-feeder", ("jhansi", "bhopal", "nagpur")),
    ("regional-feeder", ("udaipur", "indore")),
    ("regional-feeder", ("kochi", "thiruvananthapuram", "kanyakumari")),
    ("regional-feeder", ("kochi", "kozhikode", "mangaluru", "hubballi")),
    ("regional-feeder", ("coimbatore", "madurai", "chennai")),
    ("regional-feeder", ("hyderabad", "vijayawada")),
    ("regional-feeder", ("kanpur", "lucknow", "varanasi", "patna", "siliguri", "guwahati", "silchar")),
    ("regional-feeder", ("bhubaneswar", "ranchi", "dhanbad")),
)
