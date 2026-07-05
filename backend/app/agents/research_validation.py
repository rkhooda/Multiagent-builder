import json
from typing import List, Tuple

# Only permanent sections are required regardless of optional section flags
PERMANENT_SECTIONS = [
    "## Problem Space",
    "## Technical Landscape",
    "## Key Risks",
    "## Recommended Approach",
    "## Research Confidence Score",
]

OPTIONAL_SECTION_HEADERS = {
    "existing_solutions": "## Existing Solutions",
    "target_users": "## Target Users",
    "market_risks": "Market Risks",
}


def validate_research_report(
    report: str, optional_sections_str: str = "{}"
) -> Tuple[bool, List[str]]:
    """
    Returns (is_valid, list_of_missing_sections).

    Validates that all permanent sections are present. When optional_sections_str
    is provided, also checks that enabled optional sections are present.
    """
    try:
        optional = json.loads(optional_sections_str) if optional_sections_str else {}
    except json.JSONDecodeError:
        optional = {}

    required = list(PERMANENT_SECTIONS)
    for key, header in OPTIONAL_SECTION_HEADERS.items():
        if optional.get(key, False):
            required.append(header)

    missing = []
    for section in required:
        if not any(section.lower() in line.lower() for line in report.split("\n")):
            missing.append(section)

    return len(missing) == 0, missing
