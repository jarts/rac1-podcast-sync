#!/usr/bin/env python3
"""Descobreix les URL dels feeds RSS d'un programa de RAC1.

RAC1 no publica un índex de feeds, però tots els seus podcasts estan a Apple
Podcasts i l'API pública de cerca retorna el `feedUrl` de cadascun. Això evita
haver d'endevinar l'slug de cada secció.

    python3 scripts/discover_feeds.py "via lliure"
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

SEARCH_URL = "https://itunes.apple.com/search"


def discover(term: str, limit: int = 50) -> list[tuple[str, str]]:
    query = urllib.parse.urlencode(
        {"term": term, "entity": "podcast", "country": "es", "limit": limit}
    )
    with urllib.request.urlopen(f"{SEARCH_URL}?{query}", timeout=20) as response:
        payload = json.load(response)

    results = []
    for item in payload.get("results", []):
        name = item.get("collectionName", "")
        feed = item.get("feedUrl")
        if feed and term.lower() in name.lower():
            results.append((name, feed))
    return sorted(results)


def main() -> int:
    term = sys.argv[1] if len(sys.argv) > 1 else "via lliure"
    feeds = discover(term)
    if not feeds:
        print(f"Cap podcast trobat per a «{term}»", file=sys.stderr)
        return 1

    print(f"# Enganxa les entrades que vulguis al teu config.toml\n")
    for name, feed in feeds:
        slug = urllib.parse.urlparse(feed).path.strip("/").replace("/", "-")
        print(f"# {name}")
        print("[[feeds]]")
        print(f'name = "{slug}"')
        print(f'url  = "{feed}"\n')
    return 0


if __name__ == "__main__":
    sys.exit(main())
