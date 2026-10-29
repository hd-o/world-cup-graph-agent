import re
import unicodedata


def assert_required_facts(text: str, required_facts: list[str], source: str) -> None:
    """Match complete facts independently of capitalization and accents."""
    normalized_text = _normalize_fact_text(text)

    missing = [
        fact
        for fact in required_facts
        if re.search(
            rf"(?<!\w){re.escape(_normalize_fact_text(fact))}(?!\w)",
            normalized_text,
        )
        is None
    ]

    assert not missing, f"{source} is missing required facts: {missing!r}"


def _normalize_fact_text(text: str) -> str:
    # Seeded names have accents; ordinary user-facing spellings may not.
    decomposed = unicodedata.normalize("NFKD", text)

    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )

    return without_marks.casefold()
