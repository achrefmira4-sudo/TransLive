"""Find longer French + Arabic speech samples on Wikimedia Commons."""
import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "VoiceTranslatorTest/1.0 (educational test)"}


def search(q, limit=10):
    params = {
        "action": "query", "list": "search", "srsearch": q,
        "srnamespace": 6, "srlimit": limit, "format": "json",
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    data = json.load(urllib.request.urlopen(req, timeout=30))
    return [s["title"] for s in data.get("query", {}).get("search", [])]


for label, q in [
    ("ARABIC", 'insource:/Ar-/ filetype:audio'),
    ("ARABIC2", 'Arabic spoken Wikipedia filetype:audio'),
    ("FRENCH", 'insource:/Fr-/ filetype:audio article'),
]:
    print("==", label)
    try:
        for t in search(q):
            print("   ", t)
    except Exception as e:
        print("   err", e)
