import requests
import re
import urllib3
import json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class StreamResolver:
    def __init__(self):
        self.session = requests.Session()
        
        # Identità: Apple TV è la migliore per avere Akamai
        self.apple_headers = {
            "User-Agent": "AppleCoreMedia/1.0.0.16M601 (Apple TV; U; CPU OS 10_0 like Mac OS X; en_us)",
            "Referer": "https://www.raiplay.it/"
        }
        self.generic_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.raiplay.it/"
        }

    def _extract_rai_id(self, url):
        match = re.search(r'cont=([a-zA-Z0-9\-]+)', url)
        if match: return match.group(1)
        match = re.search(r'contentId=([a-zA-Z0-9\-]+)', url)
        if match: return match.group(1)
        return None

    def _id_to_channel_name(self, cid):
        mapping = {
            "308718": "rai1", "308702": "rai2", "308709": "rai3",
            "746966": "rai4", "395276": "rai5", "747011": "raimovie",
            "747012": "raipremium", "746992": "raigulp", "746899": "raiyoyo",
            "746963": "raistoria", "746953": "rainews24", "746990": "raisport",
            "12181": "raiscuola"
        }
        return mapping.get(str(cid))

    def _find_urls(self, text):
        # Cerca link m3u8
        matches = re.findall(r'(https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*)', text)
        xml_matches = re.findall(r'<(?:url|video)[^>]*>(https?://[^<]+)</(?:url|video)>', text, re.IGNORECASE)
        all_cands = matches + xml_matches
        return [u.strip().replace("&amp;", "&").replace("\\", "") for u in all_cands]

    def _probe(self, url, headers):
        print(f"   ↳ Probing: {url[-35:]}...")
        try:
            r = self.session.get(url, headers=headers, allow_redirects=True, timeout=5, verify=False)
            
            # 1. Redirect
            if ".m3u8" in r.url and r.url != url:
                return [r.url]
            
            # 2. Body
            return self._find_urls(r.text)
        except: pass
        return []

    def resolve_rai(self, url, extra_headers=None):
        content_id = self._extract_rai_id(url)
        if not content_id: return url

        channel_name = self._id_to_channel_name(content_id)
        print(f"🕵️ [Resolver] Rai ID: {content_id} ({channel_name})")

        candidates = []

        # TENTATIVO 1: OUTPUT 45 (Apple TV - Spesso Akamai)
        # Questo è il trucco per evitare MSVDN
        url_45 = f"https://mediapolis.rai.it/relinker/relinkerServlet.htm?cont={content_id}&output=45"
        links = self._probe(url_45, self.apple_headers)
        candidates.extend(links)

        # TENTATIVO 2: API DIRETTE JSON
        if channel_name:
            links = self._probe(f"https://www.raiplay.it/dirette/{channel_name}.json", self.generic_headers)
            candidates.extend(links)

        # TENTATIVO 3: Output 62 (Smart TV)
        url_62 = f"https://mediapolis.rai.it/relinker/relinkerServlet.htm?cont={content_id}&output=62"
        links = self._probe(url_62, self.generic_headers)
        candidates.extend(links)

        # SELEZIONE VINCENTE
        msvdn_backup = None
        
        for link in candidates:
            if "akamaized" in link:
                print(f"   🏆 TROVATO AKAMAI (GOLD): {link[:60]}...")
                return link
            if "cloudfront" in link:
                print(f"   ✅ TROVATO CLOUDFRONT: {link[:60]}...")
                return link
            if "msvdn" in link and not msvdn_backup:
                msvdn_backup = link

        # Se proprio non c'è altro...
        if msvdn_backup:
            print(f"   ⚠️ Solo MSVDN disponibile. Lo uso con cautela.")
            return msvdn_backup

        # Fallback finale
        return f"https://mediapolis.rai.it/relinker/relinkerServlet.htm?cont={content_id}&output=54"

    # --- DISCOVERY ---
    def resolve_discovery(self, url, extra_headers):
        # Qui usiamo la stessa logica di scraping della pagina se necessario
        # Ma ci affidiamo al fatto che lo scraper ci dia l'URL buono o che la pagina contenga uplynk
        print(f"   🕵️ Discovery Parse: {url}")
        
        headers = self.generic_headers.copy()
        if extra_headers:
            if "Cookie" in extra_headers: headers["Cookie"] = extra_headers["Cookie"]
            if "User-Agent" in extra_headers: headers["User-Agent"] = extra_headers["User-Agent"]

        try:
            r = self.session.get(url, headers=headers, timeout=8, verify=False)
            # Cerca uplynk
            match = re.search(r'(https?://[^"\'\s]+\.uplynk\.com/[^"\'\s]+\.m3u8[^"\'\s]*)', r.text)
            if match:
                clean = match.group(1).replace("\\", "")
                print(f"   ✅ Uplynk estratto: {clean[:60]}...")
                return clean
        except Exception as e:
            print(f"   ❌ Errore Discovery: {e}")
            
        return url

    def get_real_url(self, url, headers=None):
        if not url: return None
        if ".m3u8" in url or ".mp4" in url: return url
        
        if "raiplay.it" in url or "mediapolis" in url:
            if "relinker" in url or "video-url" in url:
                return self.resolve_rai(url, headers)
        
        if "discoveryplus.com" in url and "watch" in url:
            return self.resolve_discovery(url, headers)
            
        return url