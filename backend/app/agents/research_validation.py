from typing import List, Tuple


REQUIRED_SECTIONS = [
    "## Problem Space",
    "## Existing Solutions",
    "## Target Users",
    "## Technical Landscape",
    "## Key Risks",
    "## Recommended Approach",
    "## Research Confidence Score",
]


def validate_research_report(report: str) -> Tuple[bool, List[str]]:
    """
    Returns (is_valid, list_of_missing_sections).
    Used both in tests and at runtime to catch incomplete LLM output.
    """
    missing = []
    for section in REQUIRED_SECTIONS:
        if not any(section.lower() in line.lower() for line in report.split("\n")):
            missing.append(section)
    return len(missing) == 0, missing
