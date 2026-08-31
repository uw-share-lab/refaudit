"""A small BibTeX reader.

Only what a checker needs: entry type, cite key, and fields as raw strings. It
does not expand ``@string`` macros or resolve crossrefs, because a checker that
silently mis-parses is worse than one that reports what it saw.

Brace matching is done by walking the source rather than with a regular
expression, since BibTeX values nest braces freely (``title = {The {LLM} Era}``)
and a regex will either stop early or run away.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Entry

_ENTRY_START = re.compile(r"@(?P<type>\w+)\s*[{(]\s*(?P<key>[^,\s{}]+)\s*,", re.MULTILINE)
_FIELD = re.compile(r"(\w+)\s*=\s*", re.MULTILINE)

# Entry types that are not published records and have no external index.
#: Entry types that the citation indexes do not undertake to cover. With no
#: identifier to look up, "not found" would mean "we looked somewhere it was
#: never going to be", so these are reported as skipped instead. Theses belong
#: here: universities rarely register DOIs for them.
NON_ARCHIVAL_TYPES = frozenset({
    "misc", "online", "manual", "unpublished", "software",
    "phdthesis", "mastersthesis",
})


def _match_brace(src: str, open_idx: int) -> int:
    """Index of the brace closing the one at ``open_idx``; ``len(src)`` if unbalanced."""
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\\":          # skip escaped char
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _read_value(src: str, i: int) -> tuple[str, int]:
    """Read a field value starting at ``i``; returns (value, index after it)."""
    n = len(src)
    while i < n and src[i].isspace():
        i += 1
    if i >= n:
        return "", i
    if src[i] == "{":
        end = _match_brace(src, i)
        return src[i + 1:end], min(end + 1, n)
    if src[i] == '"':
        j = i + 1
        while j < n:
            if src[j] == "\\":
                j += 2
                continue
            if src[j] == '"':
                break
            j += 1
        return src[i + 1:j], min(j + 1, n)
    # bare value (number, macro name) up to the next comma or closing brace
    j = i
    while j < n and src[j] not in ",}\n":
        j += 1
    return src[i:j].strip(), j


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_string(src: str) -> list[Entry]:
    entries: list[Entry] = []
    for m in _ENTRY_START.finditer(src):
        etype = m.group("type").lower()
        if etype in {"comment", "preamble", "string"}:
            continue
        body_start = src.index("{", m.start()) if "{" in src[m.start():m.end()] else m.end()
        body_end = _match_brace(src, body_start)
        body = src[m.end():body_end]

        fields: dict[str, str] = {}
        i = 0
        while True:
            fm = _FIELD.search(body, i)
            if not fm:
                break
            value, i = _read_value(body, fm.end())
            fields[fm.group(1).lower()] = _clean(value)
        entries.append(Entry(key=m.group("key").strip(), entry_type=etype, fields=fields))
    return entries


def parse_file(path: str | Path) -> list[Entry]:
    return parse_string(Path(path).read_text(encoding="utf-8", errors="replace"))


_CITE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]*)\}")


def cited_keys(tex_paths: list[Path]) -> set[str]:
    """Cite keys appearing in live (non-commented) LaTeX.

    Uncited entries never reach the reference list, so checking them is optional
    work; separating them also keeps the report focused on what a reviewer sees.
    """
    keys: set[str] = set()
    for path in tex_paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("%"):
                continue
            # drop trailing comments, honouring \%
            line = re.sub(r"(?<!\\)%.*$", "", line)
            for m in _CITE.finditer(line):
                keys.update(k.strip() for k in m.group(1).split(",") if k.strip())
    return keys


def find_tex(root: str | Path) -> list[Path]:
    root = Path(root)
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*.tex") if p.is_file())
