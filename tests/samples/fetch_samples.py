"""Download small public-domain speech samples used by the integration test."""
import json
import os
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "VoiceTranslatorTest/1.0 (educational test)"}

TITLES = [
    ("File:LL-Q150 (fra)-Eihel-sentence.wav", "french.wav"),
    ("File:Gespr\u00e4chVorstellung.ogg", "german.ogg"),
]


def main():
    params = {
        "action": "query",
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "titles": "|".join(t for t, _ in TITLES),
        "format": "json",
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    data = json.load(urllib.request.urlopen(req, timeout=30))
    pages = data.get("query", {}).get("pages", {})

    by_title = {p.get("title"): p for p in pages.values()}
    for title, name in TITLES:
        page = by_title.get(title)
        if not page:
            print("MISSING PAGE:", title, "| got:", list(by_title))
            continue
        info = page.get("imageinfo", [{}])[0]
        u = info.get("url")
        print(title, "->", info.get("mime"), info.get("size"))
        if not u:
            continue
        dest = os.path.join(HERE, name)
        r = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(r, timeout=90) as resp, open(dest, "wb") as fh:
            fh.write(resp.read())
        print("   saved", dest, os.path.getsize(dest), "bytes")


if __name__ == "__main__":
    main()
