#!/usr/bin/env python3
"""
Zyte Search API CLI

Usage:
    python search.py "your query" [options]   # plain CLI mode
    python search.py --tui                    # interactive TUI (no other args needed)

Output (CLI):
    Logging summary to stderr, JSON to stdout (suitable for agent consumption)
"""

import argparse
import json
import os
import re
import sys
import time
import threading

import requests


ZYTE_API_KEY = os.environ.get("ZYTE_API_KEY", "")


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def slugify(text: str, max_len: int = 40) -> str:
    """Convert query text to a safe filename slug, truncated."""
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:max_len].rstrip("-")


# ---------------------------------------------------------------------------
# Plain logging helpers (stderr)
# ---------------------------------------------------------------------------

def log(msg: str):
    print(msg, file=sys.stderr)

def log_step(icon: str, label: str, detail: str = ""):
    line = f"  {icon}  {label}"
    if detail:
        line += f"  {detail}"
    print(line, file=sys.stderr)

def log_divider():
    print("  " + "─" * 50, file=sys.stderr)

def log_header(title: str):
    print(f"\n{'─' * 54}", file=sys.stderr)
    print(f"  {title}", file=sys.stderr)
    print(f"{'─' * 54}", file=sys.stderr)

def log_summary(stats: dict):
    log_header("Search complete")
    for k, v in stats.items():
        print(f"  {k:<22} {v}", file=sys.stderr)
    print(f"{'─' * 54}\n", file=sys.stderr)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def build_payload(
    query: str,
    domain: str,
    max_results: int,
    geolocation: str | None,
    locale: str | None,
    safe: str | None,
) -> dict:
    payload: dict = {
        "domain": domain,
        "query": query,
        "include": ["aiOverview", "organic", "html"],
        "maxResults": max_results,
    }

    query_params: dict = {}
    if geolocation or locale:
        query_params["style"] = "generic"
        if geolocation:
            query_params["geolocation"] = geolocation
        if locale:
            query_params["locale"] = locale
    if safe:
        if query_params.get("style") == "generic":
            query_params = {"style": "engineSpecific", "safe": safe}
            if geolocation:
                query_params["gl"] = geolocation
            if locale:
                query_params["hl"] = locale.split("-")[0]
        else:
            query_params["style"] = "engineSpecific"
            query_params["safe"] = safe

    if query_params:
        payload["queryParameters"] = query_params

    return payload


def do_search(payload: dict, on_start=None, on_done=None) -> dict:
    if not ZYTE_API_KEY:
        print("Error: ZYTE_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    if on_start:
        on_start()

    t0 = time.time()
    try:
        response = requests.post(
            "https://api.zyte.com/v1/search",
            auth=(ZYTE_API_KEY, ""),
            json=payload,
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"API error: {e}", file=sys.stderr)
        try:
            print(json.dumps(response.json(), indent=2), file=sys.stderr)
        except Exception:
            print(response.text, file=sys.stderr)
        sys.exit(1)

    elapsed = time.time() - t0
    if on_done:
        on_done(elapsed)

    return response.json()


# ---------------------------------------------------------------------------
# AI Overview parsing
# ---------------------------------------------------------------------------

import html as _html_module


def extract_ai_overview_block(html: str) -> str | None:
    idx = html.find('class="Fzsovc"')
    if idx == -1:
        return None
    end_idx = html.find('id="rso"', idx)
    return html[idx:end_idx] if end_idx != -1 else html[idx:idx + 60000]


def strip_tags(html: str) -> str:
    """Strip HTML tags and return plain text (no entity decoding)."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def clean_text(raw: str) -> str:
    """
    Full cleanup pipeline for AI overview text:
    - Decode HTML entities (&nbsp; &amp; etc.)
    - Strip inline source citations: 1-4 Title Case words followed by +N or (+N),
      anchored after sentence-ending punctuation or lowercase text.
    - Strip bare 2-3 word source-name labels between sentences (no +N counter).
    - Collapse whitespace.
    """
    text = _html_module.unescape(raw)
    # Remove citations with +N counter: "Nebraska Medicine +5", "MedlinePlus (.gov) +4"
    # Anchored after a lowercase char or sentence-end to avoid eating section-heading words.
    text = re.sub(
        r"(?<=[a-z\.\!\?\)])\s+([A-Z][A-Za-z&.()]*(?:\s+[A-Z][A-Za-z&.()]*){0,3})(?:\s*\([^)]+\))?\s*(?:\+\d+|\(\+\d+\))",
        " ",
        text,
    )
    # Strip bare 2-3 word source-name labels (no +N) between sentences:
    # e.g. "protocols. Scripps Health Primary" → "protocols. Primary"
    # Require 2+ words to avoid eating single-word section headings / adjectives.
    # Don't strip if the following word ends with ':' (likely a section heading continuation).
    text = re.sub(
        r"(?<=[\.\?\!])\s+([A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+){1,2})\s+(?=[A-Z][A-Za-z]+ )",
        lambda m: " " if not m.group(1).endswith((":", "-")) and not re.search(r"[A-Z][A-Za-z]+:", m.group(0)[len(m.group(1))+2:]) else m.group(0),
        text,
    )
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip()
    return text


def clean_link_text(raw: str) -> str:
    """
    Clean link text from aria-label or inner HTML:
    - Remove "- View related links" suffix
    - Remove "Opens in new tab." suffix
    - Remove "(+N)" counters
    - Decode HTML entities
    - Strip leading/trailing whitespace
    """
    text = _html_module.unescape(raw)
    text = re.sub(r"\s*-\s*View related links?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\.?\s*Opens? in new tab\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(\+\d+\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Strip trailing punctuation artefacts
    text = text.rstrip(" .")
    return text


def extract_ai_overview_text(block: str) -> str:
    heading_end = block.find(">AI Overview")
    if heading_end != -1:
        rest = block[heading_end + len(">AI Overview"):]
        rest = re.sub(r"^[^<]*(<\/[^>]+>)*", "", rest).strip()
        block = rest

    b = re.sub(r"<script[^>]*>.*?</script>", " ", block, flags=re.DOTALL)
    b = re.sub(r"<style[^>]*>.*?</style>", " ", b, flags=re.DOTALL)
    b = re.sub(r"<!--.*?-->", " ", b, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", b)
    text = re.sub(r"\s+", " ", text).strip()
    text = clean_text(text)
    # Strip footer noise after disclaimer (Google boilerplate)
    for footer_marker in ("Disclaimer:", "This is for informational purposes"):
        idx = text.find(footer_marker)
        if idx != -1:
            text = text[:idx].strip()
            break
    return text


def _is_content_link(href: str, text: str) -> bool:
    """Return False for Google chrome/footer links that aren't real sources."""
    if not href or href == "#":
        return False
    skip_domains = ("google.com", "googleapis.com", "gstatic.com", "facebook.com",
                    "twitter.com", "youtube.com", "policies.google.com")
    skip_texts = ("privacy policy", "terms of service", "learn more about generative ai",
                  "feedback", "send feedback")
    if any(d in href.lower() for d in skip_domains):
        return False
    if text.lower() in skip_texts:
        return False
    return True


def parse_links(html: str, dedupe: bool = False) -> list[dict]:
    links = []
    seen_hrefs: set[str] = set()
    for m in re.finditer(r'<a([^>]+)>(.*?)</a>', html, re.DOTALL):
        attrs, inner = m.group(1), m.group(2)
        href_m = re.search(r'href=["\']([^"\']+)["\']', attrs)
        if not href_m:
            continue
        href = href_m.group(1)
        if dedupe:
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
        aria_m = re.search(r'aria-label=["\']([^"\']+)["\']', attrs)
        raw_text = aria_m.group(1) if aria_m else strip_tags(inner)
        text = clean_link_text(raw_text)
        if text and _is_content_link(href, text):
            links.append({"text": text, "href": href})
    return links


def parse_li_items(html: str) -> list[dict]:
    items = []
    for m in re.finditer(r"<li[^>]*>(.*?)</li>", html, re.DOTALL):
        inner = m.group(1)
        raw_text = strip_tags(inner)
        text = clean_text(raw_text)
        links = parse_links(inner)
        if text:
            items.append({"text": text, "links": links})
    return items


def extract_answer_block(block: str) -> str:
    """
    Trim the AI overview block to just the answer content,
    excluding the sources carousel and footer.
    Tries multiple known boundary markers in order.
    """
    boundaries = [
        'class="bTFeG"',    # sources article list
        'class="qnIdo"',    # "Show all related links" panel
    ]
    cutoff = len(block)
    for marker in boundaries:
        i = block.find(marker)
        if i != -1 and i < cutoff:
            cutoff = i
    return block[:cutoff]


def parse_ai_overview(block: str) -> dict:
    answer_block = extract_answer_block(block)
    return {
        "text": extract_ai_overview_text(answer_block),
        "liItems": parse_li_items(answer_block),
        "allLinks": parse_links(block, dedupe=True),
    }


# ---------------------------------------------------------------------------
# Save files
# ---------------------------------------------------------------------------

def save_files(slug: str, html: str, ai_block: str | None, output_path: str | None,
               result_json: str, save_html: bool) -> list[str]:
    saved = []

    if save_html and html:
        path = f"{slug}-response.html"
        with open(path, "w") as f:
            f.write(html)
        saved.append(path)

        if ai_block:
            path = f"{slug}-ai-overview.html"
            with open(path, "w") as f:
                f.write(ai_block)
            saved.append(path)

    if output_path:
        with open(output_path, "w") as f:
            f.write(result_json)
        saved.append(output_path)

    return saved


# ---------------------------------------------------------------------------
# Plain mode
# ---------------------------------------------------------------------------

def run_plain(args, payload: dict, slug: str):
    log_header("Zyte Search")
    log_step("◎", "Query",       f'"{args.query}"')
    log_step("◎", "Domain",      args.domain)
    log_step("◎", "Max results", str(args.max_results))
    if args.geolocation:
        log_step("◎", "Geolocation", args.geolocation)
    if args.locale:
        log_step("◎", "Locale",      args.locale)
    if args.safe:
        log_step("◎", "SafeSearch",  args.safe)
    log_divider()

    t_total = time.time()

    data = do_search(
        payload,
        on_start=lambda: log_step("⟳", "Sending request to Zyte Search API..."),
        on_done=lambda e: log_step("✓", "Response received", f"({e:.1f}s)"),
    )

    html = data.get("html", "")
    organic = data.get("organicResults", [])

    log_step("⟳", "Parsing AI Overview...")
    ai_block = extract_ai_overview_block(html) if html else None
    log_step("✓" if ai_block else "–",
             "AI Overview found" if ai_block else "AI Overview not present")

    output: dict = {
        "query": args.query,
        "url": data.get("url"),
        "fetchedAt": data.get("fetchedAt"),
        "organicResults": organic,
    }
    if ai_block:
        output["aiOverview"] = parse_ai_overview(ai_block)

    result_json = json.dumps(output, indent=2)
    saved = save_files(slug, html, ai_block, args.output, result_json, args.save_html)

    ai_parsed = output.get("aiOverview", {})
    log_summary({
        "Status":           data.get("status", "unknown"),
        "Fetched at":       data.get("fetchedAt", ""),
        "Organic results":  len(organic),
        "AI Overview":      "yes" if ai_block else "no",
        "AI li items":      len(ai_parsed.get("liItems", [])) if ai_block else "–",
        "AI links":         len(ai_parsed.get("allLinks", [])) if ai_block else "–",
        "HTML size":        f"{len(html):,} chars" if html else "–",
        "Files saved":      ", ".join(saved) if saved else "none",
        "Total time":       f"{time.time() - t_total:.1f}s",
    })

    if not args.output:
        print(result_json)


# ---------------------------------------------------------------------------
# TUI mode — interactive Textual app
# ---------------------------------------------------------------------------

def run_tui():
    from textual.app import App, ComposeResult
    from textual.containers import Vertical, Horizontal, ScrollableContainer
    from textual.widgets import (
        Header, Footer, Input, Select, Button, Label,
        Static, LoadingIndicator, Log
    )
    from textual.binding import Binding
    from textual import work
    from textual.worker import Worker, WorkerState

    MAX_RESULTS_OPTIONS = [(str(n), str(n)) for n in range(10, 101, 10)]
    SAFE_OPTIONS = [("— none —", ""), ("active", "active"), ("off", "off")]

    class SearchApp(App):
        CSS = """
        Screen {
            background: $surface;
        }

        #layout {
            height: 1fr;
            padding: 1 2;
        }

        #form-panel {
            width: 40;
            min-width: 36;
            border: solid $primary;
            padding: 1 2;
            height: auto;
        }

        #form-panel Label {
            color: $text-muted;
            margin-top: 1;
        }

        #form-panel Input {
            margin-bottom: 0;
        }

        #form-panel Select {
            margin-bottom: 0;
        }

        #run-btn {
            margin-top: 2;
            width: 100%;
            background: $primary;
        }

        #run-btn:hover {
            background: $primary-lighten-1;
        }

        #results-panel {
            border: solid $primary;
            padding: 1 2;
            height: 1fr;
            margin-left: 2;
        }

        #status-bar {
            height: 1;
            color: $text-muted;
            margin-bottom: 1;
        }

        #results-scroll {
            height: 1fr;
        }

        #results-log {
            height: 1fr;
        }

        #loading {
            display: none;
            margin-top: 1;
        }

        #loading.visible {
            display: block;
        }

        .error {
            color: $error;
        }

        .success {
            color: $success;
        }

        .dim {
            color: $text-muted;
        }
        """

        BINDINGS = [
            Binding("ctrl+r", "run_search", "Run search"),
            Binding("ctrl+c", "quit", "Quit"),
        ]

        TITLE = "Zyte Search"
        SUB_TITLE = "Search API Explorer"

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="layout"):
                with Vertical(id="form-panel"):
                    yield Label("Query *")
                    yield Input(placeholder="e.g. symptoms of vitamin D deficiency", id="query")
                    yield Label("Domain")
                    yield Input(placeholder="google.com", value="google.com", id="domain")
                    yield Label("Max results")
                    yield Select(MAX_RESULTS_OPTIONS, value="10", id="max-results")
                    yield Label("Geolocation")
                    yield Input(placeholder="e.g. US, GB (optional)", id="geolocation")
                    yield Label("Locale")
                    yield Input(placeholder="e.g. en-US (optional)", id="locale")
                    yield Label("Safe search")
                    yield Select(SAFE_OPTIONS, value="", id="safe")
                    yield Button("▶  Run Search  (Ctrl+R)", id="run-btn", variant="primary")
                    yield LoadingIndicator(id="loading")

                with Vertical(id="results-panel"):
                    yield Static("", id="status-bar")
                    with ScrollableContainer(id="results-scroll"):
                        yield Log(id="results-log", highlight=True)

            yield Footer()

        def on_mount(self):
            self.query_one("#query").focus()

        def on_button_pressed(self, event: Button.Pressed):
            if event.button.id == "run-btn":
                self.action_run_search()

        def action_run_search(self):
            query = self.query_one("#query", Input).value.strip()
            if not query:
                self._set_status("⚠  Query is required", "error")
                self.query_one("#query").focus()
                return

            if not ZYTE_API_KEY:
                self._set_status("⚠  ZYTE_API_KEY environment variable not set", "error")
                return

            domain = self.query_one("#domain", Input).value.strip() or "google.com"
            max_results_val = self.query_one("#max-results", Select).value
            max_results = int(max_results_val) if max_results_val else 10
            geolocation = self.query_one("#geolocation", Input).value.strip() or None
            locale = self.query_one("#locale", Input).value.strip() or None
            safe_val = self.query_one("#safe", Select).value
            safe = safe_val if safe_val else None

            payload = build_payload(
                query=query,
                domain=domain,
                max_results=max_results,
                geolocation=geolocation,
                locale=locale,
                safe=safe,
            )

            log = self.query_one("#results-log", Log)
            log.clear()
            self._set_status("⟳  Sending request…", "dim")
            self.query_one("#loading").add_class("visible")
            self.query_one("#run-btn").disabled = True

            self._do_search(query, payload)

        @work(thread=True)
        def _do_search(self, query: str, payload: dict):
            t0 = time.time()
            try:
                response = requests.post(
                    "https://api.zyte.com/v1/search",
                    auth=(ZYTE_API_KEY, ""),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                self.call_from_thread(self._on_search_error, str(e))
                return

            elapsed = time.time() - t0
            self.call_from_thread(self._on_search_done, query, data, elapsed)

        def _on_search_error(self, error: str):
            self.query_one("#loading").remove_class("visible")
            self.query_one("#run-btn").disabled = False
            self._set_status(f"✗  Error: {error}", "error")
            log = self.query_one("#results-log", Log)
            log.write_line(f"[red]Error: {error}[/red]")

        def _on_search_done(self, query: str, data: dict, elapsed: float):
            self.query_one("#loading").remove_class("visible")
            self.query_one("#run-btn").disabled = False

            html = data.get("html", "")
            organic = data.get("organicResults", [])
            ai_block = extract_ai_overview_block(html) if html else None

            output: dict = {
                "query": query,
                "url": data.get("url"),
                "fetchedAt": data.get("fetchedAt"),
                "organicResults": organic,
            }
            if ai_block:
                output["aiOverview"] = parse_ai_overview(ai_block)

            ai_parsed = output.get("aiOverview", {})

            self._set_status(
                f"✓  Done in {elapsed:.1f}s  ·  "
                f"{len(organic)} results  ·  "
                f"AI Overview: {'yes' if ai_block else 'no'}",
                "success"
            )

            log = self.query_one("#results-log", Log)
            log.clear()

            # Summary section
            log.write_line("[bold]─── Summary ───────────────────────────────────[/bold]")
            log.write_line(f"  Query          {query}")
            log.write_line(f"  Fetched at     {data.get('fetchedAt', '')}")
            log.write_line(f"  Status         {data.get('status', 'unknown')}")
            log.write_line(f"  Response time  {elapsed:.1f}s")
            log.write_line(f"  Organic        {len(organic)} results")
            log.write_line(f"  AI Overview    {'yes' if ai_block else 'no'}")
            if ai_block:
                log.write_line(f"  AI li items    {len(ai_parsed.get('liItems', []))}")
                log.write_line(f"  AI links       {len(ai_parsed.get('allLinks', []))}")
            log.write_line("")

            # AI Overview text
            if ai_block:
                log.write_line("[bold]─── AI Overview ────────────────────────────────[/bold]")
                ai_text = ai_parsed.get("text", "")
                # wrap at ~80 chars
                words = ai_text.split()
                line, lines = [], []
                for w in words:
                    line.append(w)
                    if sum(len(x) + 1 for x in line) > 78:
                        lines.append(" ".join(line))
                        line = []
                if line:
                    lines.append(" ".join(line))
                for l in lines:
                    log.write_line(f"  {l}")
                log.write_line("")

            # Organic results
            log.write_line("[bold]─── Organic Results ────────────────────────────[/bold]")
            for i, r in enumerate(organic, 1):
                title = r.get("name") or r.get("title") or "(no title)"
                url = r.get("url", "")
                snippet = r.get("snippet") or r.get("description") or ""
                log.write_line(f"  [bold]{i}.[/bold] {title}")
                log.write_line(f"     [dim]{url}[/dim]")
                if snippet:
                    log.write_line(f"     {snippet[:120]}{'…' if len(snippet) > 120 else ''}")
                log.write_line("")

            # Print JSON to stdout for agent consumption
            print(json.dumps(output, indent=2))

        def _set_status(self, msg: str, style: str = ""):
            bar = self.query_one("#status-bar", Static)
            if style == "error":
                bar.update(f"[red]{msg}[/red]")
            elif style == "success":
                bar.update(f"[green]{msg}[/green]")
            elif style == "dim":
                bar.update(f"[dim]{msg}[/dim]")
            else:
                bar.update(msg)

    app = SearchApp()
    app.run()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search",
        description="Query the Zyte Search API and return JSON results.",
    )
    parser.add_argument("query", nargs="?", default=None, help="Search query string (omit for --tui)")
    parser.add_argument("--domain", default="google.com", help="Search domain (default: google.com)")
    parser.add_argument("--max-results", type=int, default=10, choices=[10,20,30,40,50,60,70,80,90,100],
                        metavar="{10,20,...,100}", help="Number of organic results (default: 10)")
    parser.add_argument("--geolocation", help="2-letter country code, e.g. US, GB")
    parser.add_argument("--locale", help="Locale string, e.g. en-US")
    parser.add_argument("--safe", choices=["active", "off"], help="SafeSearch setting")
    parser.add_argument("--save-html", action="store_true",
                        help="Save <query>-response.html and <query>-ai-overview.html to disk")
    parser.add_argument("-o", "--output", help="Write JSON output to file instead of stdout")
    parser.add_argument("--tui", action="store_true", help="Launch interactive TUI (no other args needed)")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.tui:
        run_tui()
        return

    if not args.query:
        parser.error("query is required unless --tui is used")

    slug = slugify(args.query)

    payload = build_payload(
        query=args.query,
        domain=args.domain,
        max_results=args.max_results,
        geolocation=args.geolocation,
        locale=args.locale,
        safe=args.safe,
    )

    run_plain(args, payload, slug)


if __name__ == "__main__":
    main()
