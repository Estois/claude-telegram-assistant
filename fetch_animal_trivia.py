"""One-off / periodic script: build trivia_cache.json from a public domain
of scraped animal facts, for the bot's wrong-password replies.

Source: ekohrt/animal-fun-facts-dataset (MIT licensed), a dataset of animal
facts scraped from various sites across the web.
https://github.com/ekohrt/animal-fun-facts-dataset

Usage:
    python fetch_animal_trivia.py

No credentials required.
"""

import csv
import io
import json
import random
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "trivia_cache.json"

DATASET_URL = (
    "https://raw.githubusercontent.com/ekohrt/animal-fun-facts-dataset"
    "/main/animal-fun-facts-dataset.csv"
)
TARGET_COUNT = 1000
MIN_FACT_LENGTH = 20  # drops fragments like "50-100" or "Fast and Furious!"


def _clean(text: str) -> str:
    return " ".join(text.split())


def fetch_facts() -> list:
    resp = httpx.get(DATASET_URL, timeout=30)
    resp.raise_for_status()

    reader = csv.DictReader(io.StringIO(resp.text))
    seen_facts = set()
    facts = []

    for row in reader:
        raw_text = row.get("text") or ""
        fact = _clean(raw_text)
        if len(fact) < MIN_FACT_LENGTH:
            continue
        if fact in seen_facts:
            continue
        seen_facts.add(fact)

        animal = _clean(row.get("animal_name") or "").title()
        facts.append({"animal": animal, "fact": fact})

    random.shuffle(facts)
    return facts[:TARGET_COUNT]


def main() -> None:
    facts = fetch_facts()
    OUTPUT_FILE.write_text(json.dumps(facts, indent=2))
    print(f"Saved {len(facts)} unique animal facts to {OUTPUT_FILE}")

    # Reset rotation state so the bot reshuffles against the fresh set.
    state_file = BASE_DIR / "trivia_state.json"
    if state_file.exists():
        state_file.unlink()


if __name__ == "__main__":
    main()
