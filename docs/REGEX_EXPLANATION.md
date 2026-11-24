# Notes for the professor — Steam regex extraction

The crawler stores HTML. The extractor is a separate class (`SteamExtractor`), because it is responsible for finding game attributes, not for fetching URLs.

We use Python's `re` module. For example, the game title is located by the HTML marker `id="appHubAppName"`; the developer/publisher by labelled rows in the game information; the full description by `id="game_area_description"`. Genres, features, language rows, and popular tags are found with separate patterns.

The regexes locate a specific part of the document before extracting its fields. HTML text and character entities are cleaned after matching. The full-description region may contain nested `<div>` elements, so a small depth counter is used to stop at the correct closing tag.

Missing values are represented explicitly (`null` or an empty list), so we can count failures and improve patterns after inspecting the documents. This is not intended to be a general-purpose HTML parser.

Every output document is written to its own JSON file, allowing the extractor to restart without repeating previous work. The original HTML collection stays unchanged.

## Test report (25 real HTML pages)

The test collection is from `run_20261027T211213Z_df402f`:

- HTML input: 25 files
- JSON output: 25 files
- All 25 have a title and AppID
- No missing fields were reported in this sample
- No extraction errors were recorded
- The first 20 pages passed integration checks for AppID, title, short/full description, developer, publisher, and genres

These are coverage and smoke-test results, **not manually labelled accuracy scores**. The rules should be checked again when the Steam collection grows.
