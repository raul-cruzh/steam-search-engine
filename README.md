# Steam Search Engine

Information Retrieval course project in Python. The pipeline crawls Steam game pages, extracts information using regular expressions, processes Steam and Wikipedia data with Apache Spark, and supports searching with a custom inverted index and a PyLucene index.

## Setup

Use Python 3.13 with Java 21 for the verified Spark environment (Spark runs in local[2] mode). From the project folder:

```powershell
python -m pip install -r requirements.txt
```

## Crawler

Crawler settings are in `crawler/config.py`: starting URLs, maximum game and navigation pages, request delay, timeout, and HTTP headers. `crawler/crawler.py` contains the BFS queue, URL normalization, `robots.txt` check, filtering, downloads, and checkpoint logic.

Start a new crawl:

```powershell
python -m crawler.main
```

Continue an existing crawl, including after `Ctrl+C` or an interruption:

```powershell
python -m crawler.main --resume "data/pages/run_20261027T235528Z_676afa"
```

Each run uses `data/pages/run_.../`. The crawler saves accepted pages as `app_<id>.html`, records results in `manifest.jsonl` and `stats.json`, and keeps the queue and progress in `state.json`. Navigation pages are visited for links but not saved as game documents. URLs are filtered before requesting them; downloaded product pages are checked again before saving. Keep `state.json` if you want to resume, and do not run two crawlers on the same run directory.

## Extract, index, search

Replace the run name in these commands if yours is different.

```powershell
# Extract game information from saved HTML (REGEX)
python -m extractor.main "data/pages/run_20261027T235528Z_676afa"

# Build the TF/DF vocabulary and inverted index
python -m indexer.main "data/extracted/run_20261027T235528Z_676afa"

# Search using TF-IDF/cosine similarity and Okapi BM25
python -m searcher.main "data/index/run_20261027T235528Z_676afa" --query "action adventure" --method both --limit 10
```

The extractor stores `app_<id>.json` and `stats.json` in `data/extracted/run_.../`. Running it again skips documents already extracted. The indexer creates `documents.json`, `vocabulary.json`, `inverted_index.json`, and `stats.json` in `data/index/run_.../`; running it again rebuilds the index. The custom searcher supports OR queries and `--method tfidf`, `bm25`, or `both`. The new PyLucene searcher is available through `--method lucene`.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The real-page extraction test uses 20 saved Steam HTML files when they are available under `data/pages/`; otherwise it is skipped.

## Notes

The crawler follows a sequential request delay and checks Steam's `robots.txt`. Filtering is heuristic, so a few product types may be misclassified. The original inverted index was implemented without external search/indexing libraries. Consultation 4 introduces PyLucene.

## Consultation 3: Apache Spark + Wikipedia

The existing `crawler/`, `extractor/`, `indexer/` and `searcher/` remain unchanged. The new `spark/` package reuses `extractor.extractor.SteamExtractor` instead of duplicating its REGEX logic.

**Run from the project root** (working directory `steam-search-engine`):

```powershell
# Print previously generated outputs, without re-running Spark or reading the dump
python -m spark.main

# The full pipeline, only if a complete rebuild is needed
python -m spark.main --step all

# Individual stages when input data are already available
python -m spark.main --step steam
python -m spark.main --step wiki
python -m spark.main --step join
```

- `spark/steam.py`: Spark `mapPartitions` over the downloaded Steam HTML files, reusing the original extractor. Output: `data/spark_extracted/<run>/app_<id>.json`.
- `spark/wikipedia.py`: matches Steam titles against the compressed Wikipedia multistream index, groups selected pages by compressed block, decompresses them on Spark workers, and uses REGEX to extract the `Infobox video game` fields. Outputs: `data/wiki/title_candidates.json` and `data/wiki/wiki_candidate_articles.json`.
- `spark/join.py`: Spark DataFrame inner join on normalized exact title, constrained by the candidate Steam App IDs; prints the exact count of unique Wikipedia page IDs. Also flags mismatched developers/years for review, keeping the original candidate relations. Outputs: `steam_wikipedia_join.json`, `steam_wikipedia_audited.json`, and `steam_wikipedia_review.json` under `data/wiki/`.

**Required input files for wiki processing:** `data/wiki/enwiki-20261101-pages-articles-multistream-index.txt.bz2` and `data/wiki/enwiki-20261101-pages-articles-multistream.xml.bz2`. The downloaded dump is large and is not included in source-code ZIPs.

**Latest observed results (19 November 2026):** 8,762 processed Steam HTML files; 8,755 unique Steam titles; 2,989 candidate Wikipedia pages; 1,089 Wikipedia pages with extracted video-game attributes; 1,089 candidate join rows with 1,089 distinct Wikipedia page IDs. Automated audit: 908 with agreeing developer and year, 181 requiring manual review, including 24 with year gaps larger than 5 years. The title join produces *candidate* links, not 1,089 human-confirmed matches.

The relevant Wikipedia attributes are `developer`, `publisher`, `released`, `genre`, `platforms` and `engine`, useful for enriching the game data, comparing release editions, and possible future searching/filtering. Extracted attributes retain raw wikitext; they are not yet normalised for display or indexed by the original searcher.

**Limitations:** tested on Windows with Spark `local[2]`. File paths refer to a shared local filesystem; deploying to a multi-node cluster would require shared/distributed storage and packaging the Python modules for remote workers. The title matcher performs case-insensitive exact matching and can confuse remakes, rereleases, and unrelated games with the same title. The conservative audit is diagnostic rather than an authoritative ground truth.

## Consultation 4: PyLucene

The new index uses PyLucene 10.0.0, StandardAnalyzer and BM25. It indexes Steam data processed with Apache Spark and includes selected Wikipedia attributes.

### Installation

PyLucene is compiled from the official Apache source code using our Dockerfile. Docker is required to build and run the PyLucene environment.

Build the Docker image from the project root:

```powershell
docker build -t steam-pylucene .
```

Verify the installation:

```powershell
docker run --rm steam-pylucene python -c "import lucene; lucene.initVM(); print(lucene.VERSION)"
```

### Build the index

The index uses the existing Spark output and audited Wikipedia join.

```powershell
docker run --rm -v "${PWD}:/app" steam-pylucene python -m lucene_index.indexer
```

The index is stored in `data/lucene_index/`.

### Index fields

| Field | Type | Purpose |
|---|---|---|
| app_id | StringField | Unique game identifier |
| url | StoredField | Steam page URL |
| title | TextField | Game title |
| developer | TextField | Game developer |
| publisher | TextField | Game publisher |
| genres | TextField | Game genres |
| tags | TextField | Game topics and categories |
| short_description | TextField | Short game description |
| full_description | TextField | Detailed game description |
| wiki_engine | TextField | Game engine from Wikipedia |
| wiki_platforms | TextField | Platforms from Wikipedia |

The Wikipedia attributes are included only for matches classified as consistent evidence by the automated join audit.

### Search

The existing command-line application supports the new index through `--method lucene`.

```powershell
docker run --rm -v "${PWD}:/app" steam-pylucene python -m searcher.main data/lucene_index --method lucene --query "open world survival" --limit 5
```

Supported query types:

| Type | Example |
|---|---|
| Boolean AND | `football AND horror` |
| Boolean OR | `football OR horror` |
| Phrase | `"open world"` |
| Fuzzy | `survial~1` |

### Results

Indexing results obtained on 24 November 2026:

- Indexed Steam games: 8,762.
- Games enriched with Wikipedia attributes: 892.
- Ranking method: BM25.

The original inverted index was also rebuilt using the same Spark-processed Steam documents.

```powershell
docker run --rm -v "${PWD}:/app" steam-pylucene python -m indexer.main data/spark_extracted/run_20261027T235528Z_676afa --output data/index/spark
```

Both indices were compared using three queries.

| Query | Custom BM25: first result | PyLucene: first result | Common top 5 results |
|---|---|---|---:|
| open world survival | Permafrost | Captain Bones: A Pirate's Journey | 1 |
| turn based strategy | Crown Wars: The Black Prince | Future War Tactics: SOF vs Alien Invasion – Turn-Based Strategy | 2 |
| unreal engine | NINJA GAIDEN 2 Black | NINJA GAIDEN 2 Black | 2 |

The differences in ranking can be explained by tokenization, field-based indexing, BM25 configuration and additional Wikipedia information.

The comparison shows differences between the two search systems but does not establish which system has better relevance.

### AI assistance

ChatGPT was used to assist with Docker configuration, implementation understanding and troubleshooting. The software was executed and tested locally using the project data.