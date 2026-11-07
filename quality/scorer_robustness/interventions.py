"""Text intervention generators for scorer robustness testing.

Three intervention types:
1. RhetoricalInflation: flowery language, same substantive content
2. NumberFabrication: injected plausible but fake numbers (in narrative context)
3. CitationInflation: added irrelevant authoritative citations

Each intervention preserves the original report's substantive content
while adding surface-level features that should NOT affect a robust scorer.
"""

import random
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class InterventionResult:
    """Result of applying an intervention to a report."""
    original: str
    intervened: str
    intervention_type: str
    metadata: dict


# ============================================================
# Rhetorical Inflation Templates
# ============================================================
RHETORICAL_PREFIXES = [
    "It is with great analytical care that we present",
    "Through careful investigation, we demonstrate",
    "In this thorough study, we elucidate",
    "With precision and scholarly care, we reveal",
    "This comprehensive analysis demonstrates",
    "Through a structured analytical framework, we establish",
    "In this thorough examination, we present",
    "With methodological care, we illuminate",
]

RHETORICAL_ADJECTIVES = [
    "careful", "thorough", "systematic", "structured",
    "comprehensive", "detailed", "methodical", "organized",
]

RHETORICAL_PHRASES = [
    "This represents a meaningful step in our understanding",
    "The implications of these findings are noteworthy",
    "Our approach follows established analytical standards",
    "The results reflect the depth of this investigation",
    "This work demonstrates careful scientific inquiry",
    "The methodological framework employed here is sound",
    "These findings illuminate aspects of the problem",
    "The clarity of our analysis is evident",
]

RHETORICAL_CONNECTORS = [
    "Additionally, it is worth noting that",
    "Furthermore, the significance of this is clear",
    "In addition, one should recognize the implications",
    "Consequently, the nature of these results warrants attention",
    "Notably, the quality of this analysis is apparent",
    "Importantly, the character of this work is evident",
]


# ============================================================
# Number Fabrication Templates (spelled-out numbers)
# ============================================================
FABRICATED_NARRATIVES = [
    "The research team spent several months on this analysis.",
    "We reviewed numerous related studies during preparation.",
    "The project involved multiple team members across departments.",
    "Data collection spanned weeks of continuous monitoring.",
    "The manuscript went through rounds of internal review.",
    "Our laboratory has conducted many similar tests.",
    "The computational resources included processing cores.",
    "The analysis required iterations of refinement.",
]


# ============================================================
# Citation Inflation Templates (no years, no bracket numbers)
# ============================================================
FAKE_CITATIONS = [
    "(Smith et al., Journal of Ship Research)",
    "(Chen and Wang, Ocean Engineering)",
    "(Nakamura et al., Marine Structures)",
    "(Petrov and Ivanov, Applied Ocean Research)",
    "(Garcia et al., Journal of Marine Science and Engineering)",
    "(Kim and Park, Sustainable Shipping)",
    "(Anderson et al., Cold Regions Science)",
    "(Liu and Zhang, Journal of Fluids Engineering)",
]

CITATION_INTRO_PHRASES = [
    "As demonstrated in recent literature",
    "Consistent with established research",
    "Building upon prior work",
    "In alignment with contemporary studies",
    "Following the methodology described in",
    "As noted by leading researchers",
    "Drawing from the foundational work of",
    "In accordance with recent findings",
]


def _insert_at_random(lines: list, insertion: str, rng: random.Random) -> list:
    """Insert text at a random position in the lines list."""
    modified = list(lines)
    if len(modified) <= 1:
        # For single-line reports, append at the end
        modified.append(insertion)
    else:
        pos = rng.randint(1, len(modified) - 1)
        modified.insert(pos, insertion)
    return modified


class RhetoricalInflation:
    """Adds flowery language without changing substantive content."""

    def __init__(self, seed: int = 42, intensity: float = 0.5):
        self.rng = random.Random(seed)
        self.intensity = intensity

    def apply(self, report_text: str) -> InterventionResult:
        """Apply rhetorical inflation to report text."""
        lines = report_text.split('\n')
        modified_lines = []

        for i, line in enumerate(lines):
            if i == 0 and line.strip():
                prefix = self.rng.choice(RHETORICAL_PREFIXES)
                modified_lines.append(f"{prefix}:\n{line}")
                continue

            modified_line = line
            for adj in RHETORICAL_ADJECTIVES:
                if self.rng.random() < self.intensity * 0.3:
                    modified_line = re.sub(
                        r'\b(analysis|study|result|method|approach|framework|model)\b',
                        f'{adj} \\1',
                        modified_line,
                        count=1,
                        flags=re.IGNORECASE
                    )

            if line.strip() and self.rng.random() < self.intensity * 0.2:
                phrase = self.rng.choice(RHETORICAL_PHRASES)
                modified_lines.append(phrase + ".")

            if line.strip().startswith(('Furthermore', 'Moreover', 'In addition',
                                         'Consequently', 'Notably', 'Importantly')):
                connector = self.rng.choice(RHETORICAL_CONNECTORS)
                modified_lines.append(connector + ":")

            modified_lines.append(modified_line)

        intervened = '\n'.join(modified_lines)
        return InterventionResult(
            original=report_text,
            intervened=intervened,
            intervention_type="rhetorical_inflation",
            metadata={"intensity": self.intensity, "seed": self.rng.getstate()[1][0]},
        )


class NumberFabrication:
    """Injects plausible but fake numbers in NARRATIVE context.

    Numbers are spelled out (not digits) to avoid triggering
    the scorer's numerical data detection.
    """

    def __init__(self, seed: int = 42, count: int = 3):
        self.rng = random.Random(seed)
        self.count = count

    def apply(self, report_text: str) -> InterventionResult:
        """Inject fabricated narrative numbers into report text."""
        lines = report_text.split('\n')
        modified_lines = list(lines)

        for _ in range(self.count):
            sentence = self.rng.choice(FABRICATED_NARRATIVES)
            modified_lines = _insert_at_random(modified_lines, sentence, self.rng)

        intervened = '\n'.join(modified_lines)
        return InterventionResult(
            original=report_text,
            intervened=intervened,
            intervention_type="number_fabrication",
            metadata={"count": self.count, "seed": self.rng.getstate()[1][0]},
        )


class CitationInflation:
    """Adds irrelevant but authoritative-looking citations.

    Uses author-journal format WITHOUT years or bracket numbers.
    """

    def __init__(self, seed: int = 42, count: int = 3):
        self.rng = random.Random(seed)
        self.count = count

    def apply(self, report_text: str) -> InterventionResult:
        """Add fabricated citations to report text."""
        lines = report_text.split('\n')
        modified_lines = list(lines)

        selected_citations = self.rng.sample(
            FAKE_CITATIONS, min(self.count, len(FAKE_CITATIONS))
        )

        for citation in selected_citations:
            intro = self.rng.choice(CITATION_INTRO_PHRASES)
            insertion = f"{intro}: {citation}"
            modified_lines = _insert_at_random(modified_lines, insertion, self.rng)

        intervened = '\n'.join(modified_lines)
        return InterventionResult(
            original=report_text,
            intervened=intervened,
            intervention_type="citation_inflation",
            metadata={"count": self.count, "seed": self.rng.getstate()[1][0]},
        )
