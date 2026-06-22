"""STEP A: Scrape one story from RollOnFriday.

You give it a link. It pulls out the title, the story text, and the comments,
then saves them to a file. No AI yet, so this step is free.

Usage:
    python scrape.py https://www.rollonfriday.com/news-content/some-story

Output: output/scraped.json
"""
import os
import sys
import json
import requests
from bs4 import BeautifulSoup

OUT_DIR = "output"
os.makedirs(OUT_DIR, exist_ok=True)

# Pretend to be a normal browser so the site gives us the full page.
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def scrape(url: str) -> dict:
    # 1) Download the page.
    print(f"Downloading: {url}")
    html = requests.get(url, headers=HEADERS, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")

    # 2) Title — RollOnFriday puts a clean title in the page's social-share tag.
    title_tag = soup.find("meta", property="og:title")
    title = title_tag["content"].strip() if title_tag else "(no title found)"

    # 3) Story text — lives in the main "body" block.
    body_tag = soup.select_one("div.field--name-body")
    body = body_tag.get_text(" ", strip=True) if body_tag else ""

    # 4) Comments — each comment sits in a "comment-body" block.
    #    The very last one is usually the empty "leave a comment" form, so we
    #    skip anything that looks like the form instead of a real comment.
    comments = []
    for c in soup.select(".field--name-comment-body"):
        text = c.get_text(" ", strip=True)
        if not text:
            continue
        if "About text formats" in text or text.lower().startswith("your comment"):
            continue  # this is the comment form, not a real comment
        comments.append(text)

    return {"url": url, "title": title, "body": body, "comments": comments}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python scrape.py <story-url>")

    data = scrape(sys.argv[1])

    out_path = os.path.join(OUT_DIR, "scraped.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Quick summary so you can see it worked.
    print(f"\nTitle:    {data['title']}")
    print(f"Story:    {len(data['body'])} characters")
    print(f"Comments: {len(data['comments'])} found")
    print(f"\nSaved -> {out_path}")
