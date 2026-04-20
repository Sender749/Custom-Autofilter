"""
Imdbposter.py  —  Movie/series metadata fetcher
================================================
Fetch order (each step is tried only if the previous failed):
  1. TMDB  — best poster quality, genre IDs resolved, TV-series aware
  2. OMDb  — official IMDb data via REST API (no scraping, never 403s)
  3. None  — graceful fallback: bot sends the update without metadata

Why Cinemagoer was removed
--------------------------
Cinemagoer (formerly IMDbPY) screen-scrapes imdb.com.  IMDb now returns
HTTP 403 Forbidden to all non-browser user-agents, so every call raises
IMDbDataAccessError.  The library is effectively broken for this use-case
and cannot be fixed without IMDb's cooperation.  OMDb hits the same
underlying IMDb dataset through the official API instead.
"""

from __future__ import annotations

import re
import asyncio
import logging
import socket
import warnings
from io import BytesIO

import aiohttp
from PIL import Image

from info import IMAGE_FETCH, TMDB_API_KEY, OMDB_API_KEY

logger = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

# ---------------------------------------------------------------------------
# Internal TMDB genre cache (populated once per process lifetime)
# ---------------------------------------------------------------------------
_tmdb_genre_cache: dict[int, str] = {}
_genre_cache_lock = asyncio.Lock()


def list_to_str(lst) -> str:
    if lst:
        return ", ".join(map(str, lst))
    return ""


# ---------------------------------------------------------------------------
# Image helper
# ---------------------------------------------------------------------------

async def fetch_image(url: str, size: tuple[int, int] = (860, 1200)) -> BytesIO | None:
    """Download an image, resize it, and return a JPEG BytesIO object."""
    if not IMAGE_FETCH:
        return None
    if not url:
        return None
    try:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error("fetch_image: HTTP %s for %s", resp.status, url)
                    return None
                data = await resp.read()
        img = Image.open(BytesIO(data)).convert("RGB")
        img = img.resize(size, Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="JPEG", quality=90)
        out.seek(0)
        return out
    except Exception as exc:
        logger.error("fetch_image error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# TMDB helpers
# ---------------------------------------------------------------------------

async def _ensure_tmdb_genres(session: aiohttp.ClientSession) -> None:
    """Fetch movie + TV genre maps from TMDB once and cache them."""
    global _tmdb_genre_cache
    async with _genre_cache_lock:
        if _tmdb_genre_cache:
            return
        combined: dict[int, str] = {}
        for media_type in ("movie", "tv"):
            try:
                url = f"https://api.themoviedb.org/3/genre/{media_type}/list"
                async with session.get(
                    url,
                    params={"api_key": TMDB_API_KEY},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status == 200:
                        for g in (await r.json()).get("genres", []):
                            combined[g["id"]] = g["name"]
            except Exception as exc:
                logger.warning("TMDB genre fetch (%s) failed: %s", media_type, exc)
        _tmdb_genre_cache = combined


def _resolve_genre_ids(genre_ids: list[int]) -> str:
    if not _tmdb_genre_cache or not genre_ids:
        return ""
    names = [_tmdb_genre_cache[gid] for gid in genre_ids if gid in _tmdb_genre_cache]
    return ", ".join(names)


def _pick_best(results: list[dict], query: str, year: str | None, is_tv: bool) -> dict | None:
    """Return the most relevant item from a TMDB results list."""
    if not results:
        return None
    title_key = "name" if is_tv else "title"
    orig_key  = "original_name" if is_tv else "original_title"
    date_key  = "first_air_date" if is_tv else "release_date"
    ql = query.lower()

    # 1. Year filter
    if year:
        yr_match = [r for r in results if str(r.get(date_key, ""))[:4] == str(year)]
        if yr_match:
            results = yr_match

    # 2. Exact title match
    for r in results:
        if (r.get(title_key) or r.get(orig_key) or "").lower() == ql:
            return r

    # 3. Has poster + some votes
    popular = [r for r in results if r.get("poster_path") and r.get("vote_count", 0) > 5]
    return (popular or results)[0]


async def get_movie_detailsx(query: str, year: str | None = None) -> dict:
    """
    Primary metadata source: TMDB.
    Tries /search/movie first, then /search/tv.
    Returns a normalised dict or {"error": True}.
    """
    if not TMDB_API_KEY:
        return {"error": True}

    clean = re.sub(r"\s*\(?\b(?:19|20)\d{2}\b\)?$", "", query).strip()
    logger.info("TMDB search: '%s' year=%s", clean, year)

    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    timeout   = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            await _ensure_tmdb_genres(session)

            result: dict | None = None
            kind = "movie"

            # --- movie ---
            params: dict = {"api_key": TMDB_API_KEY, "query": clean, "include_adult": "false"}
            if year:
                params["year"] = year
            async with session.get("https://api.themoviedb.org/3/search/movie", params=params) as r:
                if r.status == 200:
                    result = _pick_best((await r.json()).get("results") or [], clean, year, False)

            # --- tv fallback ---
            if not result:
                tv_params: dict = {"api_key": TMDB_API_KEY, "query": clean, "include_adult": "false"}
                if year:
                    tv_params["first_air_date_year"] = year
                async with session.get("https://api.themoviedb.org/3/search/tv", params=tv_params) as r:
                    if r.status == 200:
                        result = _pick_best((await r.json()).get("results") or [], clean, year, True)
                        if result:
                            kind = "tv"

            if not result:
                logger.warning("TMDB: no results for '%s'", clean)
                return {"error": True}

            # --- detail fetch (resolves genres & external IDs) ---
            mid      = result["id"]
            endpoint = "tv" if kind == "tv" else "movie"
            async with session.get(
                f"https://api.themoviedb.org/3/{endpoint}/{mid}",
                params={"api_key": TMDB_API_KEY, "append_to_response": "external_ids"},
            ) as r:
                detail: dict = (await r.json()) if r.status == 200 else result

            # genres
            raw_genres = detail.get("genres", [])
            if raw_genres and isinstance(raw_genres[0], dict):
                genres = ", ".join(g["name"] for g in raw_genres if g.get("name"))
            else:
                genres = _resolve_genre_ids(result.get("genre_ids", []))

            poster_path   = detail.get("poster_path")
            backdrop_path = detail.get("backdrop_path")

            if kind == "tv":
                title       = detail.get("name") or detail.get("original_name", "")
                release_raw = detail.get("first_air_date", "")
                imdb_id     = detail.get("external_ids", {}).get("imdb_id", "")
                info_url    = f"https://www.themoviedb.org/tv/{mid}"
            else:
                title       = detail.get("title") or detail.get("original_title", "")
                release_raw = detail.get("release_date", "")
                imdb_id     = detail.get("imdb_id", "")
                info_url    = f"https://www.themoviedb.org/movie/{mid}"

            result_year = release_raw[:4] if release_raw else (year or "")
            rating_raw  = detail.get("vote_average")
            rating      = f"{float(rating_raw):.1f}" if rating_raw else "N/A"
            overview    = (detail.get("overview") or "")[:800]

            return {
                "title":        title,
                "year":         result_year,
                "rating":       rating,
                "plot":         overview,
                "genres":       genres or "N/A",
                "poster_url":   f"https://image.tmdb.org/t/p/w500{poster_path}"    if poster_path   else None,
                "backdrop_url": f"https://image.tmdb.org/t/p/original{backdrop_path}" if backdrop_path else None,
                "tmdb_url":     info_url,
                "imdb_id":      imdb_id,
                "kind":         kind,
                # keep a unified "url" key pointing to the best info page
                "url":          f"https://www.imdb.com/title/{imdb_id}" if imdb_id else info_url,
            }

    except asyncio.TimeoutError:
        logger.error("TMDB timeout for '%s'", query)
        return {"error": True}
    except Exception as exc:
        logger.error("TMDB error for '%s': %s", query, exc)
        return {"error": True}


# ---------------------------------------------------------------------------
# OMDb  (official IMDb data, never scrapes, never 403s)
# ---------------------------------------------------------------------------

async def get_movie_details(query: str, id: bool = False, file: str | None = None) -> dict | None:
    """
    Secondary metadata source: OMDb API (http://www.omdbapi.com/).

    Replaces the old Cinemagoer/IMDbPY implementation which broke permanently
    because imdb.com returns HTTP 403 to all non-browser scrapers.

    OMDb is the official IMDb data delivered through a REST API.
    Free tier: 1,000 requests/day — plenty for a notification bot.
    """
    if not OMDB_API_KEY:
        logger.warning("OMDB_API_KEY not set — skipping OMDb lookup")
        return None

    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    timeout   = aiohttp.ClientTimeout(total=15)

    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            if id:
                # query is a bare IMDb numeric ID or "tt..." string
                imdb_id = f"tt{query}" if not str(query).startswith("tt") else str(query)
                params  = {"apikey": OMDB_API_KEY, "i": imdb_id, "plot": "full"}
            else:
                # Extract year from query / filename
                year_match = re.findall(r"\b(?:19|20)\d{2}\b", query)
                if not year_match and file:
                    year_match = re.findall(r"\b(?:19|20)\d{2}\b", file)
                year  = year_match[0] if year_match else None
                clean = re.sub(r"\s*\(?\b(?:19|20)\d{2}\b\)?", "", query).strip()

                # First: title search  (?s=…) to find closest match
                s_params = {"apikey": OMDB_API_KEY, "s": clean, "plot": "short"}
                if year:
                    s_params["y"] = year
                best_imdb_id = None
                async with session.get("http://www.omdbapi.com/", params=s_params) as r:
                    if r.status == 200:
                        sdata = await r.json(content_type=None)
                        if sdata.get("Response") == "True":
                            hits = sdata.get("Search", [])
                            # prefer exact title match
                            hits_lower = [(h, (h.get("Title") or "").lower()) for h in hits]
                            exact = [h for h, t in hits_lower if t == clean.lower()]
                            best  = (exact or hits)
                            if best:
                                best_imdb_id = best[0].get("imdbID")

                if best_imdb_id:
                    params = {"apikey": OMDB_API_KEY, "i": best_imdb_id, "plot": "full"}
                else:
                    # Fall back to direct title lookup
                    params = {"apikey": OMDB_API_KEY, "t": clean, "plot": "full"}
                    if year:
                        params["y"] = year

            async with session.get("http://www.omdbapi.com/", params=params) as r:
                if r.status != 200:
                    logger.error("OMDb HTTP %s", r.status)
                    return None
                data = await r.json(content_type=None)

        if data.get("Response") != "True":
            logger.warning("OMDb: '%s' — %s", query, data.get("Error", "no result"))
            return None

        # OMDb returns "N/A" strings for missing fields — normalise to None
        def omdb(key: str) -> str | None:
            v = data.get(key)
            return v if v and v != "N/A" else None

        title    = omdb("Title")
        year_out = omdb("Year")
        genres   = omdb("Genre")
        rating   = omdb("imdbRating")
        poster   = omdb("Poster")    # direct JPEG URL, no auth needed
        plot     = omdb("Plot")
        imdb_id  = omdb("imdbID")
        kind_raw = omdb("Type")      # "movie" | "series" | "episode"
        lang     = omdb("Language")
        country  = omdb("Country")
        runtime  = omdb("Runtime")
        director = omdb("Director")
        cast     = omdb("Actors")
        seasons  = omdb("totalSeasons")

        kind = "tv" if kind_raw == "series" else "movie"
        info_url = f"https://www.imdb.com/title/{imdb_id}" if imdb_id else ""

        if plot and len(plot) > 800:
            plot = plot[:800] + "..."

        return {
            "title":         title,
            "year":          year_out,
            "rating":        rating or "N/A",
            "plot":          plot,
            "genres":        genres or "N/A",
            "poster_url":    poster,
            "backdrop_url":  None,           # OMDb doesn't provide backdrops
            "url":           info_url,
            "imdb_url":      info_url,
            "tmdb_url":      "",
            "imdb_id":       imdb_id,
            "kind":          kind,
            # extra fields for potential future use
            "languages":     lang,
            "countries":     country,
            "runtime":       runtime,
            "director":      director,
            "cast":          cast,
            "seasons":       seasons,
            "votes":         omdb("imdbVotes"),
        }

    except asyncio.TimeoutError:
        logger.error("OMDb timeout for '%s'", query)
        return None
    except Exception as exc:
        logger.error("OMDb error for '%s': %s", query, exc)
        return None
