"""Extract Steam game fields using regular expressions (no DOM or XPath parser)."""

import html as html_lib
import re
from pathlib import Path


TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
DIV_RE = re.compile(r"</?div\b[^>]*>", re.IGNORECASE)
APP_FILENAME_RE = re.compile(r"app_(\d+)\.html$", re.IGNORECASE)


def text_from_html(fragment: str) -> str:
    """Convert a small extracted HTML fragment to readable text."""
    fragment = re.sub(r"<!--.*?-->", " ", fragment, flags=re.DOTALL)
    fragment = re.sub(
        r"<(script|style|svg|video|picture)\b[^>]*>.*?</\1\s*>",
        " ", fragment, flags=re.IGNORECASE | re.DOTALL,
    )
    fragment = re.sub(r"<br\s*/?>|</(?:p|li|h[1-6]|div)\s*>", " ", fragment, flags=re.IGNORECASE)
    fragment = TAG_RE.sub(" ", fragment)
    return re.sub(r"\s+", " ", html_lib.unescape(fragment)).strip()


def div_content_by_id(source: str, element_id: str) -> str | None:
    """Find one named section and match its closing div, including nested divs.

    Regular expressions identify tag boundaries. A depth counter prevents a
    nested div from truncating the selected section. This is not a DOM parser.
    """
    start = re.search(
        r'<div\b[^>]*\bid=["\']' + re.escape(element_id) + r'["\'][^>]*>',
        source, flags=re.IGNORECASE,
    )
    if not start:
        return None
    depth = 1
    for token in DIV_RE.finditer(source, start.end()):
        if token.group().lower().startswith("</"):
            depth -= 1
        else:
            depth += 1
        if depth == 0:
            return source[start.end():token.start()]
    return None


def meta_content(source: str, key: str) -> str | None:
    """Match meta attributes without requiring a particular attribute order."""
    for tag in re.findall(r"<meta\b[^>]*>", source, flags=re.IGNORECASE):
        name = re.search(r'''\b(?:name|property)\s*=\s*["']([^"']+)["']''', tag, re.I)
        if name and name.group(1).casefold() == key.casefold():
            content = re.search(r'''\bcontent\s*=\s*["']([^"']*)["']''', tag, re.I | re.S)
            if content:
                return text_from_html(content.group(1))
    return None


def labelled_links(section: str, label: str) -> list[str]:
    """Extract a labelled list in the game's metadata section."""
    match = re.search(
        rf"<b>\s*{re.escape(label)}\s*:\s*</b>(.*?)(?:</div>|<br\b)",
        section, flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    return unique([
        text_from_html(anchor)
        for anchor in re.findall(r"<a\b[^>]*>(.*?)</a\s*>", match.group(1), re.I | re.S)
    ])


def unique(values: list[str]) -> list[str]:
    """Preserve ordering while removing duplicate empty or repeated strings."""
    result = []
    seen = set()
    for value in values:
        value = value.strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result


def get_list_by_class(section: str, class_name: str) -> list[str]:
    """Get anchor texts with a specific class token from a limited section."""
    links = re.findall(r"<a\b[^>]*>.*?</a\s*>", section, re.IGNORECASE | re.DOTALL)
    values = []
    for link in links:
        opening = re.match(r"<a\b[^>]*>", link, re.I | re.S)
        if opening and re.search(
            rf'''\bclass\s*=\s*["'][^"']*\b{re.escape(class_name)}\b[^"']*["']''',
            opening.group(), re.I,
        ):
            values.append(text_from_html(link))
    return unique(values)


def get_features(source: str) -> list[str]:
    section = div_content_by_id(source, "category_block") or ""
    # Steam places feature labels inside links; other text in this section is ignored.
    anchors = re.findall(r"<a\b[^>]*>.*?</a\s*>", section, re.I | re.S)
    names = []
    for anchor in anchors:
        opening = re.match(r"<a\b[^>]*>", anchor, re.I | re.S)
        if not opening or "game_area_details_specs_ctn" not in opening.group():
            continue
        label = re.search(r'''<div\b[^>]*\bclass=["'][^"']*\blabel\b[^"']*["'][^>]*>(.*?)</div\s*>''', anchor, re.I | re.S)
        if label:
            names.append(text_from_html(label.group(1)))
    return unique(names)


def get_languages(source: str) -> list[str]:
    section = div_content_by_id(source, "languageTable") or ""
    result = []
    for row in re.findall(r"<tr\b[^>]*>.*?</tr\s*>", section, flags=re.I | re.S):
        first = re.search(r"<td\b[^>]*>(.*?)</td\s*>", row, re.I | re.S)
        if first:
            name = text_from_html(first.group(1))
            if name:
                result.append(name)
    return unique(result)


def get_title(source: str) -> str | None:
    match = re.search(
        r'''<div\b[^>]*\bid=["']appHubAppName["'][^>]*>(.*?)</div\s*>''',
        source, flags=re.IGNORECASE | re.DOTALL,
    )
    return text_from_html(match.group(1)) or None if match else None


class SteamExtractor:
    """Extract a defined collection of game fields from a saved HTML page."""

    def extract(self, html_bytes: bytes, app_id: str) -> dict:
        source = html_bytes.decode("utf-8", errors="replace")
        details = div_content_by_id(source, "genresAndManufacturer") or ""
        title = get_title(source)

        # The short description appears as HTML and frequently as metadata.
        snippet = re.search(
            r'''<div\b[^>]*\bclass=["'][^"']*\bgame_description_snippet\b[^"']*["'][^>]*>(.*?)</div\s*>''',
            source, flags=re.IGNORECASE | re.DOTALL,
        )
        short_description = (
            text_from_html(snippet.group(1)) if snippet else None
        ) or meta_content(source, "og:description") or meta_content(source, "Description")

        about = div_content_by_id(source, "game_area_description")
        full_description = text_from_html(about) if about else None
        if full_description and full_description.casefold().startswith("about this game"):
            full_description = full_description[len("about this game"):].strip()
        # Do not silently replace the full description with the short one.
        full_description = full_description or None

        release = re.search(
            r"<b>\s*Release Date:\s*</b>\s*(.*?)<br\b", details, re.I | re.S,
        )
        if release:
            release_date = text_from_html(release.group(1))
        else:
            match = re.search(
                r'''<div\b[^>]*\bclass=["'][^"']*\brelease_date\b[^"']*["'][^>]*>.*?<div\b[^>]*\bclass=["']date["'][^>]*>(.*?)</div\s*>''',
                source, re.I | re.S,
            )
            release_date = text_from_html(match.group(1)) if match else None

        genre_match = re.search(r"<b>\s*Genre:\s*</b>\s*(.*?)<br\b", details, re.I | re.S)
        genres = unique([
            text_from_html(t) for t in re.findall(r"<a\b[^>]*>(.*?)</a\s*>", genre_match.group(1), re.I | re.S)
        ]) if genre_match else []

        # The page's tags are not all 'app_tag' elements globally: restrict to
        # the tag section to avoid unrelated recommendations and embedded widgets.
        tags_section_match = re.search(
            r'''<div\b[^>]*\bclass=["'][^"']*\bpopular_tags\b[^"']*["'][^>]*>''',
            source, re.I,
        )
        tags_section = ""
        if tags_section_match:
            depth = 1
            for token in DIV_RE.finditer(source, tags_section_match.end()):
                depth += -1 if token.group().lower().startswith("</") else 1
                if depth == 0:
                    tags_section = source[tags_section_match.end():token.start()]
                    break
        tags = get_list_by_class(tags_section, "app_tag")

        return {
            "app_id": app_id,
            "url": f"https://store.steampowered.com/app/{app_id}/?l=english",
            "title": title,
            "developer": labelled_links(details, "Developer"),
            "publisher": labelled_links(details, "Publisher"),
            "release_date": release_date or None,
            "genres": genres,
            "tags": tags,
            "features": get_features(source),
            "short_description": short_description,
            "full_description": full_description,
            "supported_languages": get_languages(source),
        }

    def extract_file(self, path: Path) -> dict:
        match = APP_FILENAME_RE.fullmatch(path.name)
        if not match:
            raise ValueError(f"Not a Steam application HTML filename: {path.name}")
        return self.extract(path.read_bytes(), match.group(1))
