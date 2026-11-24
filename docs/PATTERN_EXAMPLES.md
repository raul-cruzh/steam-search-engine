# Regex patterns used by the Steam extractor

The extractor is designed for the Steam HTML downloaded by our crawler. Here are representative patterns from `extractor/extractor.py`.

## Title

```regex
<div\b[^>]*\bid=["']appHubAppName["'][^>]*>(.*?)</div\s*>
```

## Release date

```regex
<b>\s*Release Date:\s*</b>\s*(.*?)<br\b
```

## Genres

The extractor first finds the *Genre* label, then extracts anchor texts from that fragment:

```regex
<b>\s*Genre:\s*</b>\s*(.*?)<br\b
<a\b[^>]*>(.*?)</a\s*>
```

## Descriptions

The short description uses the `game_description_snippet` class and falls back to the HTML description metadata. The full description is found using the `game_area_description` ID; nested `<div>` boundaries are counted so we do not stop on the first inner `</div>`.

## Features and languages

Features are located by `game_area_details_specs_ctn` links and their `label` elements. Supported languages are extracted from the named `languageTable` section and its table rows.

## Limitations

These patterns depend on recognizable Steam markers, **not** the exact position of every HTML element. They are still tied to Steam and may need updating when the site's HTML changes. The sample results should be reviewed manually rather than assumed to be correct for every game.
