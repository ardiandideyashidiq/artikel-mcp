"""Citation formatting engine supporting APA 7th, Chicago (Author-Date & Notes),

IEEE, MLA 9th, Harvard, and BibTeX styles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from artikel_mcp.models import PaperRecord

_INITIAL_RE = re.compile(r"^[A-Z]\.?$")


@dataclass
class AuthorName:
    """Normalized representation of a personal author name."""

    first: str = ""
    middle: str = ""
    last: str = ""

    @property
    def initials(self) -> str:
        parts = []
        if self.first:
            parts.append(f"{self.first[0].upper()}.")
        if self.middle:
            for m in self.middle.split():
                if m:
                    parts.append(f"{m[0].upper()}.")
        return " ".join(parts)

    @property
    def full_name(self) -> str:
        names = [p for p in [self.first, self.middle, self.last] if p]
        return " ".join(names)

    @property
    def inverted_name(self) -> str:
        given = f"{self.first} {self.middle}".strip()
        if self.last and given:
            return f"{self.last}, {given}"
        return self.last or given


def parse_author_name(raw: str) -> AuthorName:
    """Parse raw author string into structured AuthorName."""
    clean = re.sub(r"\s+", " ", raw.strip())
    if not clean:
        return AuthorName()

    if "," in clean:
        parts = [p.strip() for p in clean.split(",", 1)]
        last = parts[0]
        given_parts = parts[1].split()
        first = given_parts[0] if given_parts else ""
        middle = " ".join(given_parts[1:]) if len(given_parts) > 1 else ""
        return AuthorName(first=first, middle=middle, last=last)

    parts = clean.split()
    if len(parts) == 1:
        return AuthorName(last=parts[0])
    elif len(parts) == 2:
        return AuthorName(first=parts[0], last=parts[1])
    else:
        return AuthorName(first=parts[0], middle=" ".join(parts[1:-1]), last=parts[-1])


def _title_case(title: str) -> str:
    """Standard English/academic title casing."""
    minor_words = {
        "and",
        "as",
        "but",
        "for",
        "if",
        "nor",
        "or",
        "so",
        "yet",
        "a",
        "an",
        "the",
        "at",
        "by",
        "in",
        "of",
        "off",
        "on",
        "per",
        "to",
        "up",
        "via",
    }
    words = title.split()
    if not words:
        return ""
    result = []
    for i, w in enumerate(words):
        lw = w.lower()
        if w.isupper() and len(w) > 1:
            result.append(w)
        elif i == 0 or i == len(words) - 1 or lw not in minor_words or words[i - 1].endswith(":"):
            result.append(w.capitalize())
        else:
            result.append(lw)
    return " ".join(result)


def _sentence_case(title: str) -> str:
    """APA sentence casing: capitalize first word and after colon; lowercase rest."""
    if not title:
        return ""
    parts = re.split(r"(:\s+)", title)
    res = []
    for p in parts:
        if p.startswith(":"):
            res.append(p)
        else:
            words = p.split()
            if words:
                c_words = [words[0].capitalize()] + [w.lower() for w in words[1:]]
                res.append(" ".join(c_words))
    return "".join(res)


def _get_pub_details(record: PaperRecord) -> dict[str, str]:
    extra = record.extra or {}
    volume = str(extra.get("volume") or "")
    issue = str(extra.get("issue") or extra.get("number") or "")
    pages = str(extra.get("pages") or "")
    return {"volume": volume, "issue": issue, "pages": pages}


def format_in_text(
    record: PaperRecord,
    style: str = "apa7",
    narrative: bool = False,
) -> str:
    """Format in-text parenthetical or narrative citation."""
    style_norm = style.lower().replace("-", "").replace(" ", "").replace("_", "")
    authors = [parse_author_name(a) for a in record.authors if a.strip()]
    year_str = str(record.year) if record.year else "n.d."

    # Determine author string
    if not authors:
        lead = f'"{record.title[:30]}..."' if record.title else "Anonymous"
    elif len(authors) == 1:
        lead = authors[0].last
    elif len(authors) == 2:
        sep = " and " if (narrative or "chicago" in style_norm or "mla" in style_norm) else " & "
        lead = f"{authors[0].last}{sep}{authors[1].last}"
    else:
        lead = f"{authors[0].last} et al."

    if style_norm.startswith("ieee"):
        return "[1]"

    if style_norm.startswith("mla"):
        return lead if narrative else f"({lead})"

    if narrative:
        return f"{lead} ({year_str})"

    if "chicago" in style_norm:
        return f"({lead} {year_str})"

    # Default APA 7 / Harvard
    return f"({lead}, {year_str})"


def _format_apa7(record: PaperRecord) -> str:
    authors = [parse_author_name(a) for a in record.authors if a.strip()]
    year_str = f"({record.year})." if record.year else "(n.d.)."
    title_sc = _sentence_case(record.title.rstrip("."))

    # Authors block
    if not authors:
        author_block = "Unknown Author."
    elif len(authors) == 1:
        a = authors[0]
        author_block = f"{a.last}, {a.initials}" if a.initials else a.last
    elif len(authors) == 2:
        a1, a2 = authors[0], authors[1]
        author_block = (
            f"{a1.last}, {a1.initials}, & {a2.last}, {a2.initials}"
            if a1.initials and a2.initials
            else f"{a1.last} & {a2.last}"
        )
    elif len(authors) <= 20:
        parts = [f"{a.last}, {a.initials}" if a.initials else a.last for a in authors[:-1]]
        last = authors[-1]
        last_str = f"{last.last}, {last.initials}" if last.initials else last.last
        author_block = f"{', '.join(parts)}, & {last_str}"
    else:
        parts = [f"{a.last}, {a.initials}" if a.initials else a.last for a in authors[:19]]
        last = authors[-1]
        last_str = f"{last.last}, {last.initials}" if last.initials else last.last
        author_block = f"{', '.join(parts)}, ... {last_str}"

    if author_block and not author_block.endswith("."):
        author_block += "."

    pub = record.publication or ""
    details = _get_pub_details(record)
    pub_parts = []
    if pub:
        pub_parts.append(f"*{_title_case(pub)}*")
    if details["volume"]:
        v_str = f"*{details['volume']}*"
        if details["issue"]:
            v_str += f"({details['issue']})"
        pub_parts.append(v_str)
    if details["pages"]:
        pub_parts.append(details["pages"])

    pub_block = ", ".join(pub_parts)
    if pub_block and not pub_block.endswith("."):
        pub_block += "."

    doi_block = f"https://doi.org/{record.doi}" if record.doi else (record.url or "")
    components = [author_block, year_str, f"{title_sc}."]
    if pub_block:
        components.append(pub_block)
    if doi_block:
        components.append(doi_block)

    return " ".join(c for c in components if c)


def _format_chicago_author_date(record: PaperRecord) -> str:
    authors = [parse_author_name(a) for a in record.authors if a.strip()]
    year_str = f"{record.year}." if record.year else "n.d."
    title = f'"{_title_case(record.title.rstrip("."))}"'

    if not authors:
        author_block = "Unknown."
    elif len(authors) == 1:
        author_block = f"{authors[0].inverted_name}."
    elif len(authors) == 2:
        author_block = f"{authors[0].inverted_name}, and {authors[1].full_name}."
    else:
        middle = [a.full_name for a in authors[1:-1]]
        last = authors[-1].full_name
        author_block = f"{authors[0].inverted_name}, {', '.join(middle)}, and {last}."

    pub = record.publication or ""
    details = _get_pub_details(record)
    pub_parts = []
    if pub:
        pub_parts.append(f"*{_title_case(pub)}*")
    vol_issue = ""
    if details["volume"]:
        vol_issue += details["volume"]
        if details["issue"]:
            vol_issue += f", no. {details['issue']}"
    if vol_issue:
        pub_parts.append(vol_issue)
    if details["pages"]:
        pub_parts.append(f": {details['pages']}")

    pub_block = " ".join(pub_parts)
    if pub_block and not pub_block.endswith("."):
        pub_block += "."

    doi_block = f"https://doi.org/{record.doi}" if record.doi else (record.url or "")
    components = [author_block, year_str, title, pub_block, doi_block]
    return " ".join(c for c in components if c)


def _format_chicago_notes_bib(record: PaperRecord) -> str:
    authors = [parse_author_name(a) for a in record.authors if a.strip()]
    title = f'"{_title_case(record.title.rstrip("."))}"'

    if not authors:
        author_block = "Unknown."
    elif len(authors) == 1:
        author_block = f"{authors[0].inverted_name}."
    elif len(authors) == 2:
        author_block = f"{authors[0].inverted_name}, and {authors[1].full_name}."
    else:
        middle = [a.full_name for a in authors[1:-1]]
        last = authors[-1].full_name
        author_block = f"{authors[0].inverted_name}, {', '.join(middle)}, and {last}."

    pub = record.publication or ""
    details = _get_pub_details(record)
    year_str = f"({record.year})" if record.year else ""

    pub_parts = []
    if pub:
        pub_parts.append(f"*{_title_case(pub)}*")
    vol_issue = ""
    if details["volume"]:
        vol_issue += details["volume"]
        if details["issue"]:
            vol_issue += f", no. {details['issue']}"
    if vol_issue:
        pub_parts.append(vol_issue)
    if year_str:
        pub_parts.append(f"{year_str}:")
    if details["pages"]:
        pub_parts.append(details["pages"])

    pub_block = " ".join(pub_parts)
    if pub_block and not pub_block.endswith("."):
        pub_block += "."

    doi_block = f"https://doi.org/{record.doi}" if record.doi else (record.url or "")
    components = [author_block, title, pub_block, doi_block]
    return " ".join(c for c in components if c)


def _format_ieee(record: PaperRecord) -> str:
    authors = [parse_author_name(a) for a in record.authors if a.strip()]
    if not authors:
        author_block = "Anon."
    elif len(authors) == 1:
        author_block = f"{authors[0].initials} {authors[0].last}".strip()
    elif len(authors) == 2:
        author_block = (
            f"{authors[0].initials} {authors[0].last} and {authors[1].initials} {authors[1].last}"
        ).strip()
    elif len(authors) <= 6:
        parts = [f"{a.initials} {a.last}".strip() for a in authors[:-1]]
        author_block = f"{', '.join(parts)}, and {authors[-1].initials} {authors[-1].last}"
    else:
        author_block = f"{authors[0].initials} {authors[0].last} *et al.*"

    title = f'"{_title_case(record.title.rstrip("."))},"'
    pub = record.publication or ""
    details = _get_pub_details(record)

    pub_parts = []
    if pub:
        pub_parts.append(f"*{_title_case(pub)}*,")
    if details["volume"]:
        pub_parts.append(f"vol. {details['volume']},")
    if details["issue"]:
        pub_parts.append(f"no. {details['issue']},")
    if details["pages"]:
        pub_parts.append(f"pp. {details['pages']},")
    if record.year:
        pub_parts.append(f"{record.year},")
    if record.doi:
        pub_parts.append(f"doi: {record.doi}.")
    elif record.url:
        pub_parts.append(f"[Online]. Available: {record.url}")

    pub_block = " ".join(pub_parts)
    return f"{author_block}, {title} {pub_block}".strip()


def _format_mla9(record: PaperRecord) -> str:
    authors = [parse_author_name(a) for a in record.authors if a.strip()]
    title = f'"{_title_case(record.title.rstrip("."))}"'

    if not authors:
        author_block = ""
    elif len(authors) == 1:
        author_block = f"{authors[0].inverted_name}."
    elif len(authors) == 2:
        author_block = f"{authors[0].inverted_name}, and {authors[1].full_name}."
    else:
        author_block = f"{authors[0].inverted_name}, et al."

    pub = record.publication or ""
    details = _get_pub_details(record)
    pub_parts = []
    if pub:
        pub_parts.append(f"*{_title_case(pub)}*")
    if details["volume"]:
        pub_parts.append(f"vol. {details['volume']}")
    if details["issue"]:
        pub_parts.append(f"no. {details['issue']}")
    if record.year:
        pub_parts.append(str(record.year))
    if details["pages"]:
        pub_parts.append(f"pp. {details['pages']}")

    pub_block = ", ".join(pub_parts)
    if pub_block and not pub_block.endswith("."):
        pub_block += "."

    link_block = f"https://doi.org/{record.doi}" if record.doi else (record.url or "")
    components = [c for c in [author_block, title, pub_block, link_block] if c]
    return " ".join(components)


def _format_harvard(record: PaperRecord) -> str:
    authors = [parse_author_name(a) for a in record.authors if a.strip()]
    year_str = f"({record.year})" if record.year else "(n.d.)"
    title = f"'{_sentence_case(record.title.rstrip('.'))}',"

    if not authors:
        author_block = "Anon."
    elif len(authors) == 1:
        author_block = f"{authors[0].last}, {authors[0].initials}".strip()
    elif len(authors) == 2:
        author_block = (
            f"{authors[0].last}, {authors[0].initials} and {authors[1].last}, {authors[1].initials}"
        ).strip()
    else:
        author_block = f"{authors[0].last}, {authors[0].initials} et al."

    pub = record.publication or ""
    details = _get_pub_details(record)
    pub_parts = []
    if pub:
        pub_parts.append(f"*{_title_case(pub)}*")
    if details["volume"]:
        v_str = details["volume"]
        if details["issue"]:
            v_str += f"({details['issue']})"
        pub_parts.append(v_str)
    if details["pages"]:
        pub_parts.append(f"pp. {details['pages']}")

    pub_block = ", ".join(pub_parts)
    if pub_block and not pub_block.endswith("."):
        pub_block += "."

    link_block = (
        f"Available at: https://doi.org/{record.doi}"
        if record.doi
        else (f"Available at: {record.url}" if record.url else "")
    )
    components = [author_block, year_str, title, pub_block, link_block]
    return " ".join(c for c in components if c)


def _format_bibtex(record: PaperRecord) -> str:
    first_author = parse_author_name(record.authors[0]).last if record.authors else "item"
    clean_last = re.sub(r"\W+", "", first_author).lower()
    year_val = str(record.year) if record.year else "nodate"
    cite_key = f"{clean_last}{year_val}"

    author_field = " and ".join(record.authors) if record.authors else "Anonymous"
    details = _get_pub_details(record)

    lines = [
        f"@article{{{cite_key},",
        f"  author = {{{author_field}}},",
        f"  title = {{{record.title}}},",
    ]
    if record.publication:
        lines.append(f"  journal = {{{record.publication}}},")
    if record.year:
        lines.append(f"  year = {{{record.year}}},")
    if details["volume"]:
        lines.append(f"  volume = {{{details['volume']}}},")
    if details["issue"]:
        lines.append(f"  number = {{{details['issue']}}},")
    if details["pages"]:
        lines.append(f"  pages = {{{details['pages']}}},")
    if record.doi:
        lines.append(f"  doi = {{{record.doi}}},")
    if record.url:
        lines.append(f"  url = {{{record.url}}},")
    lines.append("}")
    return "\n".join(lines)


SUPPORTED_STYLES = {
    "apa7": "APA 7th Edition",
    "apa": "APA 7th Edition",
    "chicago": "Chicago 18th/17th Edition (Author-Date)",
    "chicago_authordate": "Chicago 18th/17th Edition (Author-Date)",
    "chicago_notes": "Chicago 18th/17th Edition (Notes & Bibliography)",
    "ieee": "IEEE Citation Style",
    "mla": "MLA 9th Edition",
    "mla9": "MLA 9th Edition",
    "harvard": "Harvard Referencing Style",
    "bibtex": "BibTeX Entry",
}


def format_reference(record: PaperRecord, style: str = "apa7") -> str:
    """Format a single paper record into a bibliography reference entry."""
    norm = style.lower().replace("-", "").replace(" ", "").replace("_", "")

    if "bibtex" in norm:
        return _format_bibtex(record)
    elif "chicagonotes" in norm:
        return _format_chicago_notes_bib(record)
    elif "chicago" in norm:
        return _format_chicago_author_date(record)
    elif "ieee" in norm:
        return _format_ieee(record)
    elif "mla" in norm:
        return _format_mla9(record)
    elif "harvard" in norm:
        return _format_harvard(record)
    else:
        # Default to APA 7th
        return _format_apa7(record)


def format_bibliography(records: list[PaperRecord], style: str = "apa7") -> str:
    """Format an entire list of papers as a sorted bibliography section."""
    norm = style.lower().replace("-", "").replace(" ", "").replace("_", "")
    if "bibtex" in norm:
        return "\n\n".join(_format_bibtex(r) for r in records)

    # Sort alphabetically by first author's last name, then year
    def _sort_key(r: PaperRecord):
        last = parse_author_name(r.authors[0]).last.lower() if r.authors else ""
        return (last, r.year or 0)

    sorted_records = sorted(records, key=_sort_key)

    if "ieee" in norm:
        entries = [f"[{i + 1}] {_format_ieee(r)}" for i, r in enumerate(sorted_records)]
        return "\n\n".join(entries)

    entries = [format_reference(r, style=style) for r in sorted_records]
    return "\n\n".join(entries)
