"""Wildcard-based idea generation."""

import random
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "wildcards"
IDEAS_DIR = DATA_DIR / "ideas"

_cache: dict[str, list[str]] = {}

# Subject type categories
SUBJECT_TYPES = ["any", "person", "creature", "object", "scene"]


def _load_wordlist(path: Path) -> list[str]:
    """Load a wordlist file, caching the result."""
    cache_key = str(path)
    if cache_key in _cache:
        return _cache[cache_key]
    if not path.exists():
        return []
    words = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    _cache[cache_key] = words
    return words


def _load_ideas_wordlist(name: str) -> list[str]:
    """Load a wordlist from the ideas directory."""
    return _load_wordlist(IDEAS_DIR / f"{name}.txt")


def _load_subjects(subject_type: str = "any") -> list[str]:
    """Load subjects, optionally filtered by type."""
    if subject_type == "any":
        # Load from all subject category files
        all_subjects = []
        subjects_dir = IDEAS_DIR / "subjects"
        if subjects_dir.exists():
            for f in subjects_dir.glob("*.txt"):
                all_subjects.extend(_load_wordlist(f))
        # Fallback to old flat file if no category files
        if not all_subjects:
            all_subjects = _load_ideas_wordlist("subjects")
        return all_subjects
    else:
        # Load specific category
        return _load_wordlist(IDEAS_DIR / "subjects" / f"{subject_type}.txt")


def generate_idea(
    seed: int = 0,
    subject_type: str = "any",
) -> str:
    """Generate a combinatorial idea from wildcards.

    Pattern: {adjective} {subject} {action} {setting}

    Args:
        seed: Random seed (0 = random each run)
        subject_type: Filter subjects by category (any, person, creature, object, scene)

    Returns:
        Generated idea string
    """
    if seed == 0:
        rng = random.Random()
    else:
        rng = random.Random(seed)

    adjectives = _load_ideas_wordlist("adjectives")
    subjects = _load_subjects(subject_type)
    actions = _load_ideas_wordlist("actions")
    settings = _load_ideas_wordlist("settings")

    if not all([adjectives, subjects, actions, settings]):
        return "ethereal wanderer exploring unknown realms"

    adj = rng.choice(adjectives)
    subj = rng.choice(subjects)
    act = rng.choice(actions)
    setting = rng.choice(settings)

    return f"{adj} {subj} {act} {setting}"


def get_subject_types() -> list[str]:
    """Return available subject types."""
    return SUBJECT_TYPES.copy()


def reload():
    """Clear cache to reload wordlists."""
    _cache.clear()
