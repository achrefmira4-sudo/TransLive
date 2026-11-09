"""Download Arabic + French speech samples for accuracy testing."""
import json
import os
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "VoiceTranslatorTest/1.0 (educational test)"}

TITLES = [
    ("File:History of Sal\u00e9 City Audio article on Arabic Wikipedia, narrated by Yassine Ettysir.mp3", "arabic.mp3"),
    ("File:Fr-Hammurabi 1.ogg", "french_long.ogg"),
]


def main():
    params = {
        "action": "query", "prop": "imageinfo", "iiprop": "url|size|mime",
        "titles": "|".join(t for t, _ in TITLES), "format": "json",
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    data = json.load(urllib.request.urlopen(req, timeout=30))
    by_title = {p.get("title"): p for p in data.get("query", {}).get("pages", {}).values()}

    for title, name in TITLES:
        page = by_title.get(title)
        if not page:
            print("MISSING:", title, "| got:", list(by_title))
            continue
        info = page.get("imageinfo", [{}])[0]
        u = info.get("url")
        print(title, "->", info.get("mime"), info.get("size"))
        if not u:
            continue
        dest = os.path.join(HERE, name)
        r = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(r, timeout=180) as resp, open(dest, "wb") as fh:
            fh.write(resp.read())
        print("   saved", name, os.path.getsize(dest), "bytes")


if __name__ == "__main__":
    main()
