"""Token-level validation for the HTML allowed in canonical navigation."""

from __future__ import annotations

from html.parser import HTMLParser


ALLOWED = {
    "picture": set(),
    "img": {"alt", "src", "height", "title"},
    "a": {"href"},
    "details": set(),
    "summary": set(),
}


class _NavigationHtmlParser(HTMLParser):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(convert_charrefs=False)
        self.errors = errors

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._attributes(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._attributes(tag, attrs)

    def _attributes(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in ALLOWED:
            return
        names = [name.lower() for name, _ in attrs]
        if len(names) != len(set(names)):
            self.errors.append(f"navigation {tag} markup has duplicate attributes")
        unknown = set(names) - ALLOWED[tag]
        if unknown:
            self.errors.append(f"navigation {tag} markup has unknown attributes")
        if any(value is None for _, value in attrs):
            self.errors.append(
                f"navigation {tag} markup attributes require quoted values"
            )


def validate_navigation_html(text: str, errors: list[str]) -> None:
    parser = _NavigationHtmlParser(errors)
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        errors.append("navigation HTML cannot be tokenized")
