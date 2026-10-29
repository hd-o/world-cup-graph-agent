import unicodedata


type IdentityText = str


def normalize_identity_text(value: str) -> IdentityText:
    """Convert human-readable text into a stable, URL-like part of an ID.

    IDs ignore capitalization, accents, and repeated whitespace so equivalent
    source values that differ only in accents or spacing share one node.
    """
    # NFKD separates an accented character into its base character and accent mark.
    characters_with_separate_accents = unicodedata.normalize("NFKD", value.strip())

    text_without_accents = "".join(
        character
        for character in characters_with_separate_accents
        if not unicodedata.combining(character)
    )

    text_with_single_spaces = " ".join(text_without_accents.casefold().split())

    return text_with_single_spaces.replace(" ", "-")


def require_identity_input(
    label: str,
    field: str,
    value: str | int | None,
) -> str:
    # Identity inputs may be numeric, but whitespace-only text counts as
    # missing rather than creating an empty ID component.
    if value is None:
        raise ValueError(f"{label} identity requires {field}")

    text = str(value).strip()

    if text == "":
        raise ValueError(f"{label} identity requires {field}")

    return text
