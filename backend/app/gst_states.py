"""
GST state-code lookup.

A GSTIN's first 2 digits are the standard GST state/UT code (the same
numbering the government uses on the GST portal itself), e.g. "29" is
Karnataka, "27" is Maharashtra, "09" is Uttar Pradesh. This lets us derive
"which state is this party in" from a GSTIN we already have, without
needing a separate address field or an external lookup.

Source: GST state code list as published on the GST portal / CBIC. Codes
97 and 99 (Other Territory / Centre Jurisdiction) are included for
completeness though they're rare in practice.
"""

STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman and Diu",
    "26": "Dadra and Nagar Haveli",
    "27": "Maharashtra",
    "28": "Andhra Pradesh (Old)",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre Jurisdiction",
}


def gstin_to_state(gstin: str | None) -> str | None:
    """Returns the state name for a GSTIN, or None if the GSTIN is
    missing/malformed/an unrecognized code. Deliberately returns None
    rather than guessing — an unrecognized 2-digit prefix is more likely a
    typo or OCR error in the GSTIN than a real new state code."""
    if not gstin:
        return None
    gstin = gstin.strip().upper()
    if len(gstin) < 2 or not gstin[:2].isdigit():
        return None
    return STATE_CODES.get(gstin[:2])
