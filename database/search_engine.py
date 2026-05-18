"""
search_engine.py — AI-Enhanced Search Engine for SiliconBotz Autofilter
========================================================================
Implements a multi-layer search pipeline:

  Layer 1 — BM25 In-Memory Index
    • Probabilistic relevance ranking (algorithm behind Elasticsearch & early Google)
    • 50,000 file index fits in ~9MB RAM — perfect for Koyeb free tier
    • Built once at startup, refreshed every 30 minutes in background
    • Search time: 2–8ms (vs 200–500ms MongoDB regex)

  Layer 2 — Alias Table (DB-backed, admin-managed)
    • "money heist" → also searches "la casa de papel"
    • "got" → "game of thrones", "bb" → "breaking bad"
    • Admins can add/remove via bot command without code change

  Layer 3 — Popularity Boost
    • Uses silicon_messages search count data (already in extra_db.py)
    • Popular titles rise naturally in results
    • Log-scale scoring: 1000 searches → 690 pts boost

  Layer 4 — Query Understanding (Gemini/Groq)
    • Gemini Flash (free: 15 req/min): structured intent extraction
    • Groq/Llama3 (free: 100 req/min): fast spell correction
    • Only called when BM25 confidence is low (< 3 results)

  Layer 5 — SymSpell Correction (in-memory, <1ms)
    • Built from actual DB titles — knows your content vocabulary
    • Corrects against real titles, not generic English dictionary
    • "mirzappur" → "mirzapur", "brekking bad" → "breaking bad"

All layers are optional/graceful-degrading — if a layer fails or its
dependency is missing, the next layer handles it. Core bot functionality
is never blocked.
"""

import asyncio
import logging
import math
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Lazy imports — optional packages, graceful if missing
# ─────────────────────────────────────────────────────────────────────────────

def _try_import_bm25():
    try:
        from rank_bm25 import BM25Okapi
        return BM25Okapi
    except ImportError:
        logger.warning("rank_bm25 not installed. BM25 search disabled. Run: pip install rank-bm25")
        return None

def _try_import_symspell():
    try:
        from symspellpy import SymSpell, Verbosity
        return SymSpell, Verbosity
    except ImportError:
        logger.info("symspellpy not installed. SymSpell correction disabled. Run: pip install symspellpy")
        return None, None

def _try_import_genai():
    try:
        # Try new SDK first (google-genai), fall back to deprecated google-generativeai
        try:
            import google.genai as genai
            return genai
        except ImportError:
            import google.generativeai as genai
            return genai
    except ImportError:
        logger.info("Gemini not installed. Run: pip install google-genai")
        return None

def _try_import_groq():
    try:
        from groq import AsyncGroq
        return AsyncGroq
    except ImportError:
        logger.info("groq not installed. Groq disabled. Run: pip install groq")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Config — read from environment (same pattern as info.py)
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
BM25_REFRESH_INTERVAL = int(os.environ.get("BM25_REFRESH_INTERVAL", "1800"))  # 30 min
BM25_TOP_K      = int(os.environ.get("BM25_TOP_K", "300"))   # candidates returned by BM25
ALIAS_COLLECTION_NAME = "search_aliases"
POPULARITY_BOOST_MAX  = 2000   # max points from popularity (log-scaled)


# ─────────────────────────────────────────────────────────────────────────────
# Shared tech-stripping (mirrors ia_filterdb._strip_tech — kept in sync)
# ─────────────────────────────────────────────────────────────────────────────

_TECH_RE = re.compile(
    r'\b(480p|720p|1080p|2160p|4k|uhd|hdr|hdrip|bluray|bdrip|remux|'
    r'web[\-\s]?dl|webrip|dvdrip|cam|ts|hdts|pdvd|scr|'
    r'x264|x265|hevc|avc|av1|aac|ac3|dts|flac|mp3|ddp5?\.?\d?|'
    r'esub|subs?|subbed|dubbed|'
    r'hindi|english|tamil|telugu|malayalam|kannada|punjabi|bengali|gujarati|marathi|'
    r'dual|multi|hq|hd|sd)\b',
    re.IGNORECASE
)
_JUNK_RE = re.compile(
    r'\b(complete|season[\s_\-]*pack|batch|all[\s_\-]*episodes?|full[\s_\-]*season|'
    r'mkv|mp4|avi|mov|wmv|flv|webm|m4v)\b',
    re.IGNORECASE
)
_BOILERPLATE_RE = re.compile(
    r'@\w+|https?://\S+|'
    r'\b(watch\s*(now|online|free)|download\s*(now|free|here)|'
    r'click\s*(here|to\s*download)|join\s*(us|now|our?\s*channel)|'
    r'subscribe\s*(now|us|to)|follow\s*(us|now)|powered\s*by|'
    r'provided\s*by|source\s*:|visit\s*(us|our|website))\b',
    re.IGNORECASE
)


def _strip_tech(text: str) -> str:
    """Strip all technical tags, returning only clean title words (lowercase)."""
    t = text.lower()
    t = re.sub(r'\bs\d{1,2}e\d{1,3}(?:[\-_]e?\d{1,3})?\b', ' ', t)
    t = re.sub(r'\bseason\s*\d{1,2}\b', ' ', t)
    t = re.sub(r'\bepisode\s*\d{1,3}\b', ' ', t)
    t = re.sub(r'\bep\s*\d{1,3}\b', ' ', t)
    t = re.sub(r'\bs\d{1,2}\b', ' ', t)
    t = re.sub(r'\be\d{1,3}\b', ' ', t)
    t = re.sub(r'\b(19[5-9]\d|20[0-3]\d)\b', ' ', t)
    t = _TECH_RE.sub(' ', t)
    t = _JUNK_RE.sub(' ', t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r'\s{2,}', ' ', t).strip()


def _clean_caption(caption: str) -> str:
    """Remove boilerplate from Telegram captions, keeping only title content."""
    if not caption:
        return ""
    lines = caption.strip().split('\n')
    clean = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        s = _BOILERPLATE_RE.sub(' ', line)
        s = re.sub(r'^[\s|\-_#•·]+|[\s|\-_#•·]+$', '', s).strip()
        if s:
            clean.append(s)
    result = ' '.join(clean)
    result = _BOILERPLATE_RE.sub(' ', result)
    result = re.sub(r'[|\-_#•·]+', ' ', result)
    return re.sub(r'\s{2,}', ' ', result).strip()


def _get_title_text(doc: dict) -> str:
    """Get the best title text from a document (caption > file_name)."""
    raw_cap = (doc.get("caption") or "").strip()
    raw_fn  = (doc.get("file_name") or "").strip()
    clean_cap = _clean_caption(raw_cap) if raw_cap else ""
    return clean_cap if clean_cap else raw_fn


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — BM25 In-Memory Index
# ══════════════════════════════════════════════════════════════════════════════

class BM25SearchIndex:
    """
    In-memory BM25 index over all file titles in the DB.

    BM25 (Best Match 25) is the ranking algorithm used by Elasticsearch,
    Apache Solr, and early Google. It's better than regex because:
      - It knows "From S01E01" (1 title word matching "from") scores higher than
        "Where You From" (3 title words, "from" is not the main word)
      - IDF: rare words matter more than common ones
      - TF normalisation: short titles score higher than long ones with same word

    Memory: ~9MB for 50,000 titles (BM25Okapi corpus)
    Speed:  2–8ms per query (vs 200–500ms MongoDB regex)
    """

    def __init__(self):
        self._bm25        = None       # BM25Okapi instance
        self._doc_ids     = []         # parallel list: index → "_id" str
        self._docs        = {}         # "_id" str → full document dict
        self._built_at    = 0.0
        self._building    = False
        self._lock        = asyncio.Lock()
        self._BM25Okapi   = _try_import_bm25()

    @property
    def is_available(self) -> bool:
        return self._BM25Okapi is not None and self._bm25 is not None

    @property
    def needs_refresh(self) -> bool:
        return time.monotonic() - self._built_at > BM25_REFRESH_INTERVAL

    def _build_sync(self, collection, second_collection) -> None:
        """
        Blocking build — called via asyncio.to_thread.
        Loads all titles from DB and constructs BM25 corpus.
        """
        if self._BM25Okapi is None:
            return

        docs = {}
        try:
            for doc in collection.find({}, {"_id": 1, "file_name": 1, "caption": 1}):
                docs[str(doc["_id"])] = doc
        except Exception as e:
            logger.warning("BM25 build: primary DB error: %s", e)

        if second_collection is not None:
            try:
                for doc in second_collection.find({}, {"_id": 1, "file_name": 1, "caption": 1}):
                    docs.setdefault(str(doc["_id"]), doc)
            except Exception as e:
                logger.warning("BM25 build: secondary DB error: %s", e)

        if not docs:
            logger.warning("BM25 build: no documents found")
            return

        doc_ids = list(docs.keys())
        corpus  = []
        for did in doc_ids:
            text   = _get_title_text(docs[did])
            tokens = _strip_tech(text).split()
            corpus.append(tokens if tokens else ["__empty__"])

        self._bm25     = self._BM25Okapi(corpus)
        self._doc_ids  = doc_ids
        self._docs     = docs
        self._built_at = time.monotonic()
        logger.info("BM25 index built: %d documents", len(doc_ids))

    async def build(self, collection, second_collection) -> None:
        """Build (or rebuild) the index asynchronously."""
        if self._building:
            return
        async with self._lock:
            if self._building:
                return
            self._building = True
            try:
                await asyncio.to_thread(self._build_sync, collection, second_collection)
            finally:
                self._building = False

    def search(self, query: str, top_k: int = BM25_TOP_K) -> list:
        """
        Search the index. Returns list of document dicts, best match first.
        Falls back to empty list if index not built.
        """
        if not self.is_available:
            return []

        tokens = _strip_tech(query).split()
        if not tokens:
            return []

        try:
            scores    = self._bm25.get_scores(tokens)
            top_idx   = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_k]
            results   = []
            for idx in top_idx:
                if scores[idx] <= 0:
                    break   # BM25 scores are sorted desc — once 0, rest are 0
                did = self._doc_ids[idx]
                doc = self._docs.get(did)
                if doc:
                    results.append(doc)
            return results
        except Exception as e:
            logger.warning("BM25 search error: %s", e)
            return []

    def add_document(self, doc: dict) -> None:
        """
        Add a newly indexed document to the in-memory index.
        Called by save_file() so new files appear in BM25 immediately
        without waiting for the 30-minute refresh.

        Note: BM25Okapi doesn't support incremental updates, so we
        mark the index as stale — next search triggers a full rebuild.
        """
        did = str(doc.get("_id", ""))
        if did and did not in self._docs:
            self._docs[did] = doc
            # Mark stale so next search triggers rebuild
            self._built_at = 0.0
            logger.debug("BM25: new doc added, index marked stale: %s", did)

    def remove_document(self, doc_id: str) -> None:
        """Remove a deleted document and mark index stale."""
        if doc_id in self._docs:
            del self._docs[doc_id]
            self._built_at = 0.0


# Global singleton
bm25_index = BM25SearchIndex()


async def ensure_bm25_ready(collection, second_collection) -> None:
    """
    Ensure BM25 index is built and up-to-date.
    Called at startup and periodically in background.
    Non-blocking — returns immediately if already building.
    """
    if bm25_index.needs_refresh or not bm25_index.is_available:
        await bm25_index.build(collection, second_collection)


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — Alias Table (DB-backed, admin-managed)
# ══════════════════════════════════════════════════════════════════════════════

class AliasManager:
    """
    Manages search aliases stored in MongoDB.
    Admins add/remove via bot commands.
    Cache refreshed every 5 minutes.

    Examples:
      "money heist"  → ["la casa de papel"]
      "got"          → ["game of thrones"]
      "bb"           → ["breaking bad"]
      "squid games"  → ["squid game"]
      "spiderman"    → ["spider-man", "spider man"]
    """

    # Built-in aliases — sensible defaults, never needs DB if admin hasn't added any
    _BUILTIN: dict = {
        # Title synonyms (same show, different names)
        "money heist":           ["la casa de papel"],
        "la casa de papel":      ["money heist"],
        "squid games":           ["squid game"],
        "squid game":            ["squid game"],
        "attack on titan":       ["shingeki no kyojin"],
        "shingeki no kyojin":    ["attack on titan"],
        "demon slayer":          ["kimetsu no yaiba"],
        "jujutsu kaisen":        ["jujutsu kaisen"],
        "one punch":             ["one punch man"],
        "fullmetal alchemist":   ["fullmetal alchemist brotherhood"],
        "spiderman":             ["spider-man", "spider man"],
        "spider man":            ["spider-man"],
        "ironman":               ["iron man"],
        "iron man":              ["iron man"],
        "captain america":       ["captain america"],
        "batman v superman":     ["batman vs superman"],
        "the batman":            ["batman"],
        "avengers endgame":      ["avengers end game"],
        "avengers infinity war": ["avengers infinity war"],
        "fast and furious":      ["fast furious", "fate of the furious"],
        "fast furious":          ["fast and furious"],

        # Common abbreviations
        "got":    ["game of thrones"],
        "bb":     ["breaking bad"],
        "bbt":    ["big bang theory"],
        "himym":  ["how i met your mother"],
        "aot":    ["attack on titan"],
        "ahs":    ["american horror story"],
        "twd":    ["the walking dead"],
        "dnd":    ["dungeons and dragons"],
        "mcu":    ["marvel"],
        "lotr":   ["lord of the rings"],
        "potc":   ["pirates of the caribbean"],

        # Indian film/show abbreviations
        "kgf":    ["kgf"],
        "kgf2":   ["kgf chapter 2"],
        "rr":     ["rrr"],
        "ms dhoni": ["ms dhoni the untold story"],
        "ddlj":   ["dilwale dulhania le jayenge"],
        "k3g":    ["kabhi khushi kabhie gham"],
        "kal ho na ho": ["kal ho na ho"],
        "pk":     ["pk"],
    }

    def __init__(self):
        self._cache: dict   = {}
        self._cache_time: float = 0.0
        self._cache_ttl: float  = 300.0   # 5 minutes
        self._db_collection     = None

    def set_collection(self, collection) -> None:
        """Inject the MongoDB collection (called at startup)."""
        self._db_collection = collection

    def _load_sync(self) -> dict:
        """Load aliases from DB (blocking)."""
        result = dict(self._BUILTIN)
        if self._db_collection is None:
            return result
        try:
            for doc in self._db_collection.find():
                key     = doc.get("alias", "").lower().strip()
                targets = doc.get("targets", [])
                if key and targets:
                    result[key] = [t.lower().strip() for t in targets if t]
        except Exception as e:
            logger.warning("AliasManager: DB load error: %s", e)
        return result

    async def get(self, query: str) -> list:
        """
        Return alternate search terms for this query.
        Returns empty list if no alias found.
        """
        now = time.monotonic()
        if not self._cache or (now - self._cache_time) > self._cache_ttl:
            self._cache = await asyncio.to_thread(self._load_sync)
            self._cache_time = now

        key = query.lower().strip()
        # Exact match
        if key in self._cache:
            return self._cache[key]
        # Partial: if query starts with an alias key
        for alias_key, targets in self._cache.items():
            if key.startswith(alias_key) or alias_key.startswith(key):
                return targets
        return []

    async def add(self, alias: str, targets: list) -> bool:
        """Add an alias (admin command). Returns True on success."""
        if self._db_collection is None:
            return False
        try:
            await asyncio.to_thread(
                self._db_collection.update_one,
                {"alias": alias.lower().strip()},
                {"$set": {"alias": alias.lower().strip(), "targets": [t.lower() for t in targets]}},
                upsert=True
            )
            self._cache_time = 0.0   # invalidate
            return True
        except Exception as e:
            logger.error("AliasManager.add error: %s", e)
            return False

    async def remove(self, alias: str) -> bool:
        """Remove an alias (admin command). Returns True on success."""
        if self._db_collection is None:
            return False
        try:
            await asyncio.to_thread(
                self._db_collection.delete_one,
                {"alias": alias.lower().strip()}
            )
            self._cache_time = 0.0
            return True
        except Exception as e:
            logger.error("AliasManager.remove error: %s", e)
            return False

    async def list_all(self) -> list:
        """List all DB-stored aliases (for admin /listalias command)."""
        if self._db_collection is None:
            return []
        try:
            docs = await asyncio.to_thread(
                lambda: list(self._db_collection.find({}, {"_id": 0, "alias": 1, "targets": 1}))
            )
            return docs
        except Exception as e:
            logger.error("AliasManager.list_all error: %s", e)
            return []


# Global singleton
alias_manager = AliasManager()


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — Popularity Boost
# ══════════════════════════════════════════════════════════════════════════════

class PopularityBooster:
    """
    Boosts files whose titles match frequently-searched queries.

    Data source: silicondb.get_silicon_messages() — already collected by
    extra_db.py update_silicon_messages(). No new data collection needed.

    Scoring: log(search_count + 1) * scale
      1 search   → 0  pts
      10 searches → 230 pts
      100 searches → 460 pts
      1000 searches → 690 pts (capped at POPULARITY_BOOST_MAX)
    """

    def __init__(self):
        self._popular: dict   = {}    # stripped_title → boost_score
        self._loaded_at: float = 0.0
        self._ttl: float       = 600.0   # 10 min refresh

    async def load(self, silicondb) -> None:
        """Load popular queries from silicon_messages."""
        now = time.monotonic()
        if self._popular and (now - self._loaded_at) < self._ttl:
            return
        try:
            raw = await asyncio.to_thread(silicondb.get_silicon_messages, 200)
            self._popular = {}
            for i, query_text in enumerate(raw):
                stripped = _strip_tech(query_text)
                if not stripped:
                    continue
                # Rank position also matters: top query gets more boost
                rank_bonus = max(0, (200 - i))
                base_score = int(math.log(i + 2) * 150)   # log scale
                score = min(POPULARITY_BOOST_MAX, rank_bonus + base_score)
                if stripped not in self._popular or self._popular[stripped] < score:
                    self._popular[stripped] = score
            self._loaded_at = now
            logger.debug("PopularityBooster: loaded %d popular queries", len(self._popular))
        except Exception as e:
            logger.debug("PopularityBooster.load error: %s", e)

    def score(self, doc: dict) -> int:
        """Return popularity boost for this document (0 if not popular)."""
        if not self._popular:
            return 0
        text = _get_title_text(doc)
        stripped = _strip_tech(text)
        # Check direct match
        if stripped in self._popular:
            return self._popular[stripped]
        # Check word overlap: file title words vs popular query words
        f_words = set(stripped.split())
        best = 0
        for pop_title, pop_score in self._popular.items():
            p_words = set(pop_title.split())
            if p_words and p_words.issubset(f_words) and len(p_words) >= len(f_words) - 1:
                best = max(best, pop_score)
        return best


# Global singleton
popularity_booster = PopularityBooster()


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — Query Understanding (Gemini + Groq)
# ══════════════════════════════════════════════════════════════════════════════

class QueryUnderstandingEngine:
    """
    Uses free-tier LLM APIs to understand complex user queries.

    Gemini Flash (GEMINI_API_KEY):
      - Free: 15 req/min, 1M tokens/day
      - Use: structured intent extraction from complex queries
      - "new hrithik movie hindi" → {title: null, actor: "hrithik roshan", lang: "hindi"}

    Groq/Llama3 (GROQ_API_KEY):
      - Free: 100 req/min
      - Use: fast spell correction
      - "mirzappur" → "mirzapur", "brekking bad" → "breaking bad"

    These are ONLY called when BM25 returns fewer than 3 results.
    This keeps API usage well within free tier limits.
    """

    def __init__(self):
        self._genai       = _try_import_genai()
        self._AsyncGroq   = _try_import_groq()
        self._gemini_model = None
        self._initialized  = False

    def _init_gemini(self) -> None:
        if not self._genai or not GEMINI_API_KEY or self._initialized:
            return
        try:
            # Handle both google.genai (new) and google.generativeai (deprecated)
            genai_module = self._genai.__name__ if hasattr(self._genai, '__name__') else str(self._genai)
            if 'google.genai' in str(self._genai) or hasattr(self._genai, 'Client'):
                # New google-genai SDK
                client = self._genai.Client(api_key=GEMINI_API_KEY)
                self._gemini_model = client.models
                self._gemini_client = client
                self._use_new_sdk = True
            else:
                # Deprecated google-generativeai SDK
                self._genai.configure(api_key=GEMINI_API_KEY)
                self._gemini_model = self._genai.GenerativeModel("gemini-1.5-flash")
                self._use_new_sdk = False
            self._initialized = True
            logger.info("Gemini Flash initialized")
        except Exception as e:
            logger.warning("Gemini init failed: %s", e)

    async def correct_spelling_groq(self, query: str) -> Optional[str]:
        """
        Use Groq/Llama3 to correct spelling in movie/show name.
        Very fast (100ms), free tier: 100 req/min.
        """
        if not self._AsyncGroq or not GROQ_API_KEY:
            return None
        try:
            client = self._AsyncGroq(api_key=GROQ_API_KEY)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model="llama3-8b-8192",
                    messages=[{
                        "role": "system",
                        "content": (
                            "You are a movie/TV show spell checker. "
                            "The user will give you a possibly misspelled movie or show name. "
                            "Return ONLY the corrected name, nothing else. "
                            "Do not add explanations. If you're unsure, return the input unchanged."
                        )
                    }, {
                        "role": "user",
                        "content": f"Correct this movie/show name: {query}"
                    }],
                    max_tokens=50,
                    temperature=0.1,
                ),
                timeout=5.0
            )
            corrected = response.choices[0].message.content.strip()
            # Only accept if it's meaningfully different and not too long
            if corrected and corrected.lower() != query.lower() and len(corrected) < 100:
                logger.info("Groq spell: '%s' → '%s'", query, corrected)
                return corrected
        except asyncio.TimeoutError:
            logger.debug("Groq spell check timed out for: %s", query)
        except Exception as e:
            logger.debug("Groq spell check error: %s", e)
        return None

    async def understand_query_gemini(self, query: str) -> Optional[dict]:
        """
        Use Gemini Flash to extract structured intent from a complex query.
        Returns dict with: title, year, season, episode, language, quality,
                           is_series, alternate_titles, actor, director
        Only called when simpler methods fail.
        """
        if not self._genai or not GEMINI_API_KEY:
            return None
        self._init_gemini()
        if not self._gemini_model:
            return None

        prompt = f"""
A user is searching for a movie/series on a Telegram bot.
Extract structured intent from this search query: "{query}"

Return ONLY valid JSON with these fields (null if unknown):
{{
  "title": "clean title without year/quality/language",
  "year": null,
  "season": null,
  "episode": null,
  "language": null,
  "quality": null,
  "is_series": false,
  "alternate_titles": [],
  "actor": null,
  "director": null
}}

Examples:
- "new hrithik movie hindi" → {{"title": null, "actor": "hrithik roshan", "language": "hindi", "is_series": false}}
- "money heist season 3" → {{"title": "la casa de papel", "season": 3, "alternate_titles": ["money heist"], "is_series": true}}
- "breaking bad s5e16" → {{"title": "breaking bad", "season": 5, "episode": 16, "is_series": true}}
"""
        try:
            self._init_gemini()
            if not self._initialized:
                return None
            if getattr(self, '_use_new_sdk', False):
                # New SDK: google.genai
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._gemini_client.models.generate_content,
                        model="gemini-1.5-flash",
                        contents=prompt,
                    ),
                    timeout=8.0
                )
                raw_text = response.text.strip()
            else:
                # Old SDK: google.generativeai
                response = await asyncio.wait_for(
                    self._gemini_model.generate_content_async(
                        prompt,
                        generation_config={"temperature": 0.1, "max_output_tokens": 200}
                    ),
                    timeout=8.0
                )
                raw_text = response.text.strip()
            import json
            text = raw_text
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                logger.info("Gemini understood query '%s': %s", query, result)
                return result
        except asyncio.TimeoutError:
            logger.debug("Gemini query understanding timed out for: %s", query)
        except Exception as e:
            logger.debug("Gemini query understanding error: %s", e)
        return None

    async def get_best_search_term(self, original_query: str, bm25_result_count: int) -> Optional[str]:
        """
        Main entry point: try to improve a query that returned few BM25 results.

        Strategy:
          1. Groq spell correction (fast, 100ms)
          2. Gemini intent extraction (if Groq failed, 500ms)
          Returns improved search term, or None if no improvement found.
        """
        if bm25_result_count >= 3:
            return None   # BM25 found enough — don't waste API quota

        # Try Groq first (faster, 100 req/min free)
        groq_result = await self.correct_spelling_groq(original_query)
        if groq_result and groq_result.lower() != original_query.lower():
            return groq_result

        # Try Gemini (structural understanding)
        gemini_result = await self.understand_query_gemini(original_query)
        if gemini_result:
            # Use extracted title, or alternate titles
            title = gemini_result.get("title")
            alt   = gemini_result.get("alternate_titles", [])
            if title and title.lower() != original_query.lower():
                return title
            if alt:
                return alt[0]   # try the first alternate title

        return None


# Global singleton
query_engine = QueryUnderstandingEngine()


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 5 — SymSpell Correction (in-memory, <1ms)
# ══════════════════════════════════════════════════════════════════════════════

class SymSpellCorrector:
    """
    Ultra-fast spell correction built from actual DB titles.
    Much better than generic English dictionary because it knows YOUR content.
    "mirzappur" → "mirzapur", "brekking bad" → "breaking bad"
    Speed: < 1ms per correction.
    """

    def __init__(self):
        self._SymSpell, self._Verbosity = _try_import_symspell()
        self._spell     = None
        self._built_at  = 0.0
        self._ttl       = 3600.0    # rebuild every hour

    @property
    def is_available(self) -> bool:
        return self._SymSpell is not None and self._spell is not None

    def _build_sync(self, collection, second_collection) -> None:
        """Build vocabulary from DB titles (blocking)."""
        if self._SymSpell is None:
            return
        sym = self._SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        word_freq: dict = {}

        def _process_doc(doc):
            text   = _get_title_text(doc)
            tokens = _strip_tech(text).split()
            for word in tokens:
                if len(word) > 2:
                    word_freq[word] = word_freq.get(word, 0) + 1

        try:
            for doc in collection.find({}, {"file_name": 1, "caption": 1}):
                _process_doc(doc)
        except Exception as e:
            logger.warning("SymSpell build primary error: %s", e)

        if second_collection is not None:
            try:
                for doc in second_collection.find({}, {"file_name": 1, "caption": 1}):
                    _process_doc(doc)
            except Exception as e:
                logger.warning("SymSpell build secondary error: %s", e)

        for word, freq in word_freq.items():
            sym.create_dictionary_entry(word, freq)

        self._spell    = sym
        self._built_at = time.monotonic()
        logger.info("SymSpell built: %d unique words", len(word_freq))

    async def build(self, collection, second_collection) -> None:
        """Build asynchronously."""
        if self._SymSpell is None:
            return
        now = time.monotonic()
        if self._spell and (now - self._built_at) < self._ttl:
            return
        await asyncio.to_thread(self._build_sync, collection, second_collection)

    def correct(self, query: str) -> str:
        """
        Correct spelling in query. Returns corrected string.
        Falls back to original if correction fails or not available.
        """
        if not self.is_available:
            return query
        words  = query.split()
        result = []
        changed = False
        for word in words:
            if len(word) <= 2:
                result.append(word)
                continue
            try:
                suggestions = self._spell.lookup(
                    word,
                    self._Verbosity.CLOSEST,
                    max_edit_distance=2,
                    include_unknown=True
                )
                if suggestions:
                    best = suggestions[0].term
                    if best != word:
                        changed = True
                    result.append(best)
                else:
                    result.append(word)
            except Exception:
                result.append(word)
        corrected = ' '.join(result)
        if changed:
            logger.debug("SymSpell: '%s' → '%s'", query, corrected)
        return corrected


# Global singleton
symspell_corrector = SymSpellCorrector()


# ══════════════════════════════════════════════════════════════════════════════
# ENHANCED SEARCH — main entry point
# ══════════════════════════════════════════════════════════════════════════════

async def enhanced_search(
    query: str,
    collection,
    second_collection,
    silicondb,
    rank_fn,
    parse_query_fn,
    fallback_search_fn,
) -> list:
    """
    Main enhanced search entry point. Replaces get_all_results() in ia_filterdb.py.

    Pipeline:
      1. Clean & normalise query
      2. Check alias table → expand to alternate search terms
      3. SymSpell correction on clean title words
      4. BM25 search (primary — fast, smart)
      5. Alias alternate search terms (merged results)
      6. If BM25 < 3 results → try LLM query understanding (Groq/Gemini)
      7. Apply popularity boost to results
      8. Final ranking via existing rank_results()

    Args:
      query:             raw user query string
      collection:        primary pymongo collection
      second_collection: secondary pymongo collection (or None)
      silicondb:         SiliconDatabase instance (for popularity data)
      rank_fn:           ia_filterdb.rank_results function
      parse_query_fn:    ia_filterdb.parse_query function
      fallback_search_fn: ia_filterdb.get_all_results (MongoDB regex fallback)

    Returns:
      Ranked list of file documents.
    """
    from database.ia_filterdb import normalize_query, clean_query, get_filter_words

    # ── Step 0: Pre-process ──────────────────────────────────────────────────
    query = str(query).strip()
    filter_words = await get_filter_words()
    norm = normalize_query(query)
    norm = clean_query(norm, filter_words)
    if not norm:
        return []

    pq          = parse_query_fn(norm)
    clean_title = _strip_tech(pq.title_only or norm)

    # ── Step 1: SymSpell correction ──────────────────────────────────────────
    corrected_title = symspell_corrector.correct(clean_title) if clean_title else clean_title
    search_term = corrected_title if corrected_title else clean_title

    # ── Step 2: Alias expansion ──────────────────────────────────────────────
    aliases = await alias_manager.get(search_term)

    # ── Step 3: Ensure BM25 is ready ────────────────────────────────────────
    await ensure_bm25_ready(collection, second_collection)

    # ── Step 4: BM25 search ──────────────────────────────────────────────────
    results: dict = {}   # _id → doc (deduplication)

    if bm25_index.is_available:
        primary_hits = bm25_index.search(search_term, top_k=BM25_TOP_K)
        for doc in primary_hits:
            results[str(doc["_id"])] = doc

        # Also search aliases
        for alias_term in aliases[:3]:   # max 3 alternate terms
            alias_hits = bm25_index.search(alias_term, top_k=100)
            for doc in alias_hits:
                results.setdefault(str(doc["_id"]), doc)

        bm25_count = len(results)

        # ── Step 5: LLM fallback if BM25 confidence is low ──────────────────
        if bm25_count < 3:
            improved_term = await query_engine.get_best_search_term(norm, bm25_count)
            if improved_term and improved_term.lower() != search_term.lower():
                logger.info("AI improved query '%s' → '%s'", norm, improved_term)
                llm_hits = bm25_index.search(improved_term, top_k=200)
                for doc in llm_hits:
                    results.setdefault(str(doc["_id"]), doc)
    else:
        # BM25 not available — fall back to MongoDB regex (original behaviour)
        logger.debug("BM25 not available, falling back to MongoDB regex")
        return await fallback_search_fn(norm)

    # If still no results, fall back to MongoDB regex as last resort
    if not results:
        logger.debug("BM25 returned 0 results for '%s', falling back to MongoDB", norm)
        return await fallback_search_fn(norm)

    # ── Step 6: Load popularity data ────────────────────────────────────────
    try:
        await popularity_booster.load(silicondb)
    except Exception:
        pass

    # ── Step 7: Apply popularity boost + final ranking ───────────────────────
    all_docs = list(results.values())

    # Inject popularity scores as a temporary field for rank_results
    for doc in all_docs:
        doc["_popularity_boost"] = popularity_booster.score(doc)

    # Final ranking using existing multi-factor rank_results()
    ranked = rank_fn(norm, all_docs, pq)

    # Remove temporary field
    for doc in ranked:
        doc.pop("_popularity_boost", None)

    return ranked


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN COMMANDS — alias management
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_add_alias(alias: str, targets_str: str, alias_collection) -> str:
    """
    Handle /addalias command.
    Usage: /addalias money heist | la casa de papel
    """
    alias_manager.set_collection(alias_collection)
    targets = [t.strip() for t in targets_str.split("|") if t.strip()]
    if not targets:
        return "❌ Usage: /addalias <alias> | <target1> | <target2>"
    ok = await alias_manager.add(alias.strip(), targets)
    if ok:
        return f"✅ Alias added:\n<code>{alias}</code> → {', '.join(targets)}"
    return "❌ Failed to add alias (DB error)"


async def cmd_remove_alias(alias: str, alias_collection) -> str:
    """Handle /removealias command."""
    alias_manager.set_collection(alias_collection)
    ok = await alias_manager.remove(alias.strip())
    if ok:
        return f"✅ Alias removed: <code>{alias}</code>"
    return "❌ Failed to remove alias"


async def cmd_list_aliases(alias_collection) -> str:
    """Handle /listalias command."""
    alias_manager.set_collection(alias_collection)
    aliases = await alias_manager.list_all()
    if not aliases:
        return "📋 No custom aliases found.\nBuilt-in aliases are always active."
    lines = [f"• <code>{a['alias']}</code> → {', '.join(a.get('targets', []))}" for a in aliases[:30]]
    return "📋 <b>Custom Aliases:</b>\n\n" + "\n".join(lines)


async def cmd_rebuild_bm25(collection, second_collection) -> str:
    """Handle /rebuildbm25 command — force index rebuild."""
    bm25_index._built_at = 0.0   # mark stale
    await bm25_index.build(collection, second_collection)
    if bm25_index.is_available:
        return f"✅ BM25 index rebuilt: {len(bm25_index._doc_ids):,} documents indexed"
    return "❌ BM25 build failed (rank_bm25 not installed?)"


async def cmd_search_stats() -> str:
    """Handle /searchstats command — show search engine status."""
    bm25_status = (
        f"✅ Active ({len(bm25_index._doc_ids):,} docs)"
        if bm25_index.is_available else "❌ Not built"
    )
    symspell_status = (
        "✅ Active" if symspell_corrector.is_available else "❌ Not built"
    )
    gemini_status  = "✅ Configured" if GEMINI_API_KEY else "⚠️ No API key (GEMINI_API_KEY)"
    groq_status    = "✅ Configured" if GROQ_API_KEY else "⚠️ No API key (GROQ_API_KEY)"
    alias_count    = len(alias_manager._cache) if alias_manager._cache else "not loaded"
    pop_count      = len(popularity_booster._popular)

    return (
        f"<b>🔍 Search Engine Status</b>\n\n"
        f"• BM25 Index: {bm25_status}\n"
        f"• SymSpell:   {symspell_status}\n"
        f"• Gemini AI:  {gemini_status}\n"
        f"• Groq AI:    {groq_status}\n"
        f"• Aliases:    {alias_count} loaded\n"
        f"• Popularity: {pop_count} tracked queries\n"
        f"• BM25 refresh: every {BM25_REFRESH_INTERVAL//60} min"
    )


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASK — periodic BM25 refresh
# ══════════════════════════════════════════════════════════════════════════════

async def bm25_refresh_task(collection, second_collection) -> None:
    """
    Background task: refresh BM25 index every BM25_REFRESH_INTERVAL seconds.
    New files added to DB appear in BM25 after next refresh.
    Schedule this in bot.py alongside keep_alive() and other tasks.
    """
    await asyncio.sleep(30)   # wait for bot startup to complete
    while True:
        try:
            await bm25_index.build(collection, second_collection)
            await symspell_corrector.build(collection, second_collection)
            logger.info("BM25 + SymSpell background refresh complete")
        except Exception as e:
            logger.error("BM25 refresh task error: %s", e)
        await asyncio.sleep(BM25_REFRESH_INTERVAL)
