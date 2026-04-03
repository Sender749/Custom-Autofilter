import re
import aiohttp
import socket
import warnings
import logging
from io import BytesIO
from PIL import Image
from info import IMAGE_FETCH, TMDB_API_KEY, LANDSCAPE_POSTER
from imdb import Cinemagoer

logger = logging.getLogger(__name__)
ia = Cinemagoer()
LONG_IMDB_DESCRIPTION = False

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG  = "https://image.tmdb.org/t/p/w780"   # portrait poster
TMDB_BACK = "https://image.tmdb.org/t/p/w1280"  # landscape backdrop

def list_to_str(lst):
    if lst:
        return ", ".join(map(str, lst))
    return ""

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)


async def fetch_image(url, size=(1280, 720)):
    """Download and resize an image. Returns a BytesIO JPEG or None on failure."""
    if not IMAGE_FETCH:
        logger.info("Image fetching is disabled.")
        return None
    if not url:
        return None
    try:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch image [{response.status}]: {url}")
                    return None
                data = await response.read()

        img = Image.open(BytesIO(data)).convert("RGB")
        img = img.resize(size, Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)

        # Reject if over Telegram's 10 MB photo limit
        size_bytes = out.seek(0, 2)
        if size_bytes > 9 * 1024 * 1024:
            logger.warning(f"Resized image too large ({size_bytes} bytes), skipping.")
            return None
        out.seek(0)
        return out
    except aiohttp.ClientError as e:
        logger.error(f"HTTP error in fetch_image: {e}")
    except IOError as e:
        logger.error(f"I/O error in fetch_image: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in fetch_image: {e}")
    return None


async def _tmdb_search(query: str):
    """
    Query TMDB API directly using the real TMDB API key.
    Returns a normalized details dict or None.
    """
    if not TMDB_API_KEY:
        logger.warning("TMDB_API_KEY is not set, skipping TMDB lookup.")
        return None

    q = str(query).strip()
    year_match = re.search(r'\b((?:19|20)\d{2})\s*$', q)
    year = year_match.group(1) if year_match else None
    title = q[:year_match.start()].strip() if year_match else q

    try:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:

            # 1. Search TMDB
            params = {"api_key": TMDB_API_KEY, "query": title, "page": 1}
            if year:
                params["year"] = year
            async with session.get(f"{TMDB_BASE}/search/multi", params=params) as resp:
                if resp.status == 401:
                    logger.error("TMDB API key is invalid (401). Set a valid TMDB_API_KEY env var from themoviedb.org.")
                    return None
                if resp.status != 200:
                    logger.error(f"TMDB search failed [{resp.status}] for query='{q}'")
                    return None
                data = await resp.json()
                results = [x for x in data.get("results", []) if x.get("media_type") != "person"]

            # Retry without year if no results
            if not results and year:
                async with session.get(
                    f"{TMDB_BASE}/search/multi",
                    params={"api_key": TMDB_API_KEY, "query": title, "page": 1}
                ) as resp2:
                    if resp2.status == 200:
                        results = [x for x in (await resp2.json()).get("results", []) if x.get("media_type") != "person"]

            if not results:
                logger.info(f"TMDB: no results for '{q}'")
                return None

            # Pick best match (exact title preferred)
            tl = title.lower()
            item = next(
                (x for x in results if (x.get("title") or x.get("name") or "").lower() == tl),
                results[0]
            )
            mt  = item.get("media_type", "movie")
            iid = item.get("id")

            # 2. Fetch full details with credits
            detail = None
            async with session.get(
                f"{TMDB_BASE}/{mt}/{iid}",
                params={"api_key": TMDB_API_KEY, "append_to_response": "credits"}
            ) as dr:
                if dr.status == 200:
                    detail = await dr.json()

        src        = detail or item
        genres_raw = [g["name"] for g in src.get("genres", [])] if detail else []
        cr         = (detail or {}).get("credits", {})
        cast       = [c["name"] for c in cr.get("cast", [])[:6]]
        dirs       = [c["name"] for c in cr.get("crew", []) if c.get("job") == "Director"][:2]

        poster_path   = src.get("poster_path")
        backdrop_path = src.get("backdrop_path")

        poster_url   = f"{TMDB_IMG}{poster_path}"   if poster_path   else None
        backdrop_url = f"{TMDB_BACK}{backdrop_path}" if backdrop_path else None

        result = {
            "title":        src.get("title") or src.get("name", query),
            "year":         (src.get("release_date") or src.get("first_air_date") or "")[:4] or year,
            "poster_url":   poster_url,
            "backdrop_url": backdrop_url,
            "rating":       round(float(src.get("vote_average", 0) or 0), 1),
            "genres":       ", ".join(genres_raw),
            "cast":         ", ".join(cast),
            "director":     ", ".join(dirs),
            "tmdb_url":     f"https://www.themoviedb.org/{mt}/{iid}",
            "url":          f"https://www.themoviedb.org/{mt}/{iid}",
        }
        logger.info(f"TMDB success for '{q}': {result['title']} ({result['year']}), poster={bool(poster_url)}, backdrop={bool(backdrop_url)}")
        return result

    except Exception as e:
        logger.error(f"TMDB lookup error for '{q}': {type(e).__name__}: {e}")
        return None


async def get_movie_detailsx(query, id=False, file=None):
    """
    Primary movie details fetcher when TMDB_POSTER=True.

    The bharath-boy-api (previously used) is permanently disabled (returns 402).
    This function now queries the real TMDB API directly.
    Falls back to IMDB via the error flag so channel.py can call get_movie_details().
    """
    q = str(query).strip()
    result = await _tmdb_search(q)
    if result:
        return result
    # Signal to channel.py to fall back to IMDB
    return {"error": True, "message": f"TMDB found no results for '{q}'"}


async def get_movie_details(query, id=False, file=None):
    """IMDB-based details fetcher (fallback when TMDB fails or TMDB_POSTER=False)."""
    try:
        if not id:
            query = query.strip().lower()
            title = query
            year = re.findall(r'[1-2]\d{3}$', query, re.IGNORECASE)
            if year:
                year = list_to_str(year[:1])
                title = query.replace(year, "").strip()
            elif file is not None:
                year = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
                if year:
                    year = list_to_str(year[:1])
            else:
                year = None
            movieid = ia.search_movie(title.lower(), results=10)
            if not movieid:
                return None
            if year:
                filtered = list(filter(lambda k: str(k.get('year')) == str(year), movieid))
                if not filtered:
                    filtered = movieid
            else:
                filtered = movieid
            movieid = list(filter(lambda k: k.get('kind') in ['movie', 'tv series'], filtered))
            if not movieid:
                movieid = filtered
            movieid = movieid[0].movieID
        else:
            movieid = query
        movie = ia.get_movie(movieid)
        ia.update(movie, info=['main', 'vote details'])
        if movie.get("original air date"):
            date = movie["original air date"]
        elif movie.get("year"):
            date = movie.get("year")
        else:
            date = "N/A"
        plot = movie.get('plot')
        if plot and len(plot) > 0:
            plot = plot[0]
        else:
            plot = movie.get('plot outline')
        if plot and len(plot) > 800:
            plot = plot[:800] + "..."
        poster_url = movie.get('full-size cover url')
        return {
            'title': movie.get('title'),
            'votes': movie.get('votes'),
            "aka": list_to_str(movie.get("akas")),
            "seasons": movie.get("number of seasons"),
            "box_office": movie.get('box office'),
            'localized_title': movie.get('localized title'),
            'kind': movie.get("kind"),
            "imdb_id": f"tt{movie.get('imdbID')}",
            "cast": list_to_str(movie.get("cast")),
            "runtime": list_to_str(movie.get("runtimes")),
            "countries": list_to_str(movie.get("countries")),
            "certificates": list_to_str(movie.get("certificates")),
            "languages": list_to_str(movie.get("languages")),
            "director": list_to_str(movie.get("director")),
            "writer": list_to_str(movie.get("writer")),
            "producer": list_to_str(movie.get("producer")),
            "composer": list_to_str(movie.get("composer")),
            "cinematographer": list_to_str(movie.get("cinematographer")),
            "music_team": list_to_str(movie.get("music department")),
            "distributors": list_to_str(movie.get("distributors")),
            'release_date': date,
            'year': movie.get('year'),
            'genres': list_to_str(movie.get("genres")),
            'poster_url': poster_url,
            'backdrop_url': None,   # IMDB has no backdrop
            'plot': plot,
            'rating': str(movie.get("rating", "N/A")),
            'url': f'https://www.imdb.com/title/tt{movieid}'
        }
    except Exception as e:
        logger.error(f"An error occurred in get_movie_details: {e}")
        return None
