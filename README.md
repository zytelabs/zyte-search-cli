# zyte-search

> **Community project** — this is not officially supported by Zyte and may be subject to breaking changes.

A command-line tool for the [Zyte Search API](https://docs.zyte.com/zyte-api/usage/search.html). Returns organic search results and AI Overviews as clean, structured JSON — suitable for agent pipelines or direct inspection.

## Features

- Fetches organic results and AI Overviews from Google via the Zyte Search API
- Outputs structured JSON to stdout; all logging goes to stderr
- Clean AI Overview extraction: prose text, bullet items, and source links — all deduplicated and stripped of citation noise. **Note:** AI Overview parsing is best-effort and not guaranteed to be perfect; the output is designed to be consumed by an LLM rather than parsed programmatically.
- Interactive TUI mode (`--tui`) built with [Textual](https://textual.textualize.io/)
- Optional HTML snapshot saves for debugging

## Requirements

- Python 3.12+
- A [Zyte API key](https://www.zyte.com/sign-up) — sign up free at **https://www.zyte.com/sign-up**

## Setup

```bash
# Clone and enter the project
git clone https://github.com/zytelabs/zyte-search-cli.git
cd zyte-search-cli

# Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Set your Zyte API key
export ZYTE_API_KEY=your_api_key_here
```

If you use [uv](https://docs.astral.sh/uv/):

```bash
uv sync
export ZYTE_API_KEY=your_api_key_here
```

## Usage

### CLI mode

```bash
python search.py "symptoms of vitamin d deficiency"
```

The JSON result is written to stdout. All progress logging goes to stderr.

```bash
# Pipe JSON to a file
python search.py "what causes inflation" > results.json

# Write JSON directly to a file
python search.py "what causes inflation" -o results.json

# Increase result count
python search.py "machine learning" --max-results 50

# Target a specific country
python search.py "best laptops" --geolocation GB --locale en-GB

# Enable SafeSearch
python search.py "kids science projects" --safe active

# Save the raw HTML response for debugging
python search.py "climate change" --save-html
```

### TUI mode

Launch the interactive terminal UI — no query argument needed:

```bash
python search.py --tui
```

Fill in the form on the left, press **Run** or `Ctrl+R`, and watch results appear on the right.

### All options

| Flag | Default | Description |
|---|---|---|
| `query` | — | Search query (positional, omit with `--tui`) |
| `--domain` | `google.com` | Search domain |
| `--max-results` | `10` | Number of results (10–100, step 10) |
| `--geolocation` | — | 2-letter country code, e.g. `US`, `GB` |
| `--locale` | — | Locale string, e.g. `en-US` |
| `--safe` | — | SafeSearch: `active` or `off` |
| `--save-html` | — | Save raw HTML snapshots alongside JSON |
| `-o / --output` | stdout | Write JSON to a file instead of stdout |
| `--tui` | — | Launch the interactive terminal UI |

## Output format

```json
{
  "query": "symptoms of vitamin d deficiency",
  "url": "https://www.google.com/search?q=...",
  "fetchedAt": "2025-05-21T20:00:00Z",
  "organicResults": [
    {
      "rank": 1,
      "title": "Vitamin D Deficiency: Causes, Symptoms & Treatment",
      "url": "https://my.clevelandclinic.org/...",
      "description": "..."
    }
  ],
  "aiOverview": {
    "text": "Vitamin D deficiency can cause a range of symptoms...",
    "liItems": [
      {
        "text": "Fatigue and tiredness",
        "links": [{ "text": "Cleveland Clinic", "href": "https://..." }]
      }
    ],
    "allLinks": [
      { "text": "Cleveland Clinic", "href": "https://..." }
    ]
  }
}
```

The `aiOverview` key is omitted when Google does not return an AI Overview for the query.

## Resources

- [Zyte API documentation](https://docs.zyte.com/zyte-api/get-started.html)
- [Zyte Search API reference](https://docs.zyte.com/zyte-api/usage/search.html)
- [Sign up for Zyte API](https://www.zyte.com/sign-up)
- [API key management](https://app.zyte.com/o/profile/apikey)

## License

MIT
