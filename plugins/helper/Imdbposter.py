import re
import asyncio
import aiohttp
import socket
import warnings
import logging
from io import BytesIO
from PIL import Image
from info import IMAGE_FETCH, TMDB_API_KEY

logger = logging.getLogger(__name__)

LONG_IMDB_DESCRIPTION = False
Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

# TMDB genre ID → name map (fetched once and cached)
_tmdb_genre_cache: dict = {}


def list_to_str(lst):
    if lst:
        return ", ".join(map(str, lst))
    return ""


async def _ensure_tmdb_genres(session: aiohttp.ClientSession, api_key: str):
    """Populate _tmdb_genre_cache with both movie and TV genre maps."""
    global _tmdb_genre_cache
    if _tmdb_genre_cache:
        return
    combined = {}
    for media_type in ("movie", "tv"):
        try:
            url = f"https://api.themoviedb.org/3/genre/{media_type}/list"
            async with session.get(url, params={"api_key": api_key}, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    for g in data.get("genres", []):
                        combined[g["id"]] = g["name"]
        except Exception as e:
            logger.warning(f"Could not fetch TMDB {media_type} genres: {e}")
    _tmdb_genre_cache = combined


def _resolve_genres(genre_ids: list) -> str:
    """Convert a list of TMDB genre IDs to a comma-separated genre string."""
    if not _tmdb_genre_cache or not genre_ids:
        return "N/A"
    names = [_tmdb_genre_cache[gid] for gid in genre_ids if gid in _tmdb_genre_cache]
    return ", ".join(names) if names else "N/A"


async def fetch_image(url: str, size=(860, 1200)):
    if not IMAGE_FETCH:
        logger.info("Image fetching is disabled.")
        return None
    if not url:
        return None
    try:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch image: HTTP {response.status} for {url}")
                    return None
                data = await response.read()
                img = Image.open(BytesIO(data))
                img = img.convert("RGB")
                img = img.resize(size, Image.LANCZOS)
                out = BytesIO()
                img.save(out, format="JPEG", quality=90)
                out.seek(0)
                return out
    except aiohttp.ClientError as e:
        logger.error(f"HTTP request error in fetch_image: {e}")
    except IOError as e:
        logger.error(f"I/O error in fetch_image: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in fetch_image: {e}")
    return None


async def get_movie_detailsx(query: str, year: str = None) -> dict:
    """
    Fetch movie/series details from TMDB.
    Tries movie search first; if no results, falls back to TV series search.
    Returns a dict with keys: title, year, rating, plot, genres, poster_url,
    backdrop_url, tmdb_url, kind.  On failure returns {"error": True}.
    """
    api_key = TMDB_API_KEY
    logger.info(f"TMDB search: '{query}' year={year}")

    # Clean year from query string if it appears at the end
    clean_query = re.sub(r'\s*\(?\b(?:19|20)\d{2}\b\)?$', '', query).strip()

    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    timeout = aiohttp.ClientTimeout(total=15)

    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # Populate genre cache once
            await _ensure_tmdb_genres(session, api_key)

            result = None
            kind = "movie"

            # --- 1. Try movie search ---
            params = {"api_key": api_key, "query": clean_query, "include_adult": "false"}
            if year:
                params["year"] = year
            async with session.get(
                "https://api.themoviedb.org/3/search/movie", params=params
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results") or []
                    if results:
                        # Prefer exact or close title match
                        result = _pick_best_result(results, clean_query, year)
                        kind = "movie"

            # --- 2. Fall back to TV search ---
            if not result:
                tv_params = {"api_key": api_key, "query": clean_query, "include_adult": "false"}
                if year:
                    tv_params["first_air_date_year"] = year
                async with session.get(
                    "https://api.themoviedb.org/3/search/tv", params=tv_params
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results") or []
                        if results:
                            result = _pick_best_result(results, clean_query, year, is_tv=True)
                            kind = "tv"

            if not result:
                logger.warning(f"TMDB: No results for '{clean_query}'")
                return {"error": True}

            # --- 3. Fetch full details for genres, runtime, etc. ---
            media_id = result.get("id")
            endpoint = "tv" if kind == "tv" else "movie"
            async with session.get(
                f"https://api.themoviedb.org/3/{endpoint}/{media_id}",
                params={"api_key": api_key, "append_to_response": "external_ids"}
            ) as detail_resp:
                if detail_resp.status == 200:
                    detail = await detail_resp.json()
                else:
                    detail = result  # use search result as fallback

            poster_path = detail.get("poster_path")
            backdrop_path = detail.get("backdrop_path")

            # Genre resolution from detail
            raw_genre_list = detail.get("genres", [])
            if raw_genre_list and isinstance(raw_genre_list[0], dict):
                # Full detail endpoint returns {"id": int, "name": str}
                genres = ", ".join(g["name"] for g in raw_genre_list if isinstance(g, dict) and g.get("name"))
            else:
                # Search results return genre_ids list
                genres = _resolve_genres(result.get("genre_ids", []))

            # Title / year handling
            if kind == "tv":
                title = detail.get("name") or detail.get("original_name", "")
                air_date = detail.get("first_air_date", "")
                result_year = air_date[:4] if air_date else ""
                tmdb_url = f"https://www.themoviedb.org/tv/{media_id}"
                # Also get external IMDB id if available
                ext = detail.get("external_ids", {})
                imdb_id = ext.get("imdb_id", "")
            else:
                title = detail.get("title") or detail.get("original_title", "")
                release_date = detail.get("release_date", "")
                result_year = release_date[:4] if release_date else ""
                tmdb_url = f"https://www.themoviedb.org/movie/{media_id}"
                imdb_id = detail.get("imdb_id", "")

            rating = detail.get("vote_average")
            formatted_rating = f"{rating:.1f}" if rating else "N/A"

            overview = detail.get("overview", "")
            if overview and len(overview) > 800:
                overview = overview[:800] + "..."

            return {
                "title": title,
                "year": result_year or year or "",
                "rating": formatted_rating,
                "plot": overview,
                "genres": genres,
                "poster_url": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None,
                "backdrop_url": f"https://image.tmdb.org/t/p/original{backdrop_path}" if backdrop_path else None,
                "tmdb_url": tmdb_url,
                "imdb_id": imdb_id,
                "kind": kind,
            }

    except asyncio.TimeoutError:
        logger.error(f"TMDB request timed out for '{query}'")
        return {"error": True}
    except Exception as e:
        logger.error(f"TMDB error for '{query}': {e}")
        return {"error": True}


def _pick_best_result(results: list, query: str, year: str = None, is_tv: bool = False) -> dict:
    """Pick the most relevant result from TMDB search results."""
    query_lower = query.lower()
    title_key = "name" if is_tv else "title"
    orig_key = "original_name" if is_tv else "original_title"
    date_key = "first_air_date" if is_tv else "release_date"

    # If year given, prefer year-matching results
    if year:
        year_matches = [r for r in results if str(r.get(date_key, ""))[:4] == str(year)]
        if year_matches:
            results = year_matches

    # Prefer exact title match
    for r in results:
        t = (r.get(title_key) or r.get(orig_key) or "").lower()
        if t == query_lower:
            return r

    # Prefer results with popularity > 1 and a poster
    with_poster = [r for r in results if r.get("poster_path") and r.get("vote_count", 0) > 10]
    if with_poster:
        return with_poster[0]

    return results[0]


async def get_movie_details(query: str, id: bool = False, file: str = None) -> dict:
    """
    Fetch movie/series details from IMDb via Cinemagoer.
    Runs the blocking Cinemagoer calls in a thread pool to avoid blocking the event loop.
    """
    try:
        loop = asyncio.get_event_loop()
        details = await loop.run_in_executor(None, _imdb_fetch_sync, query, id, file)
        return details
    except Exception as e:
        logger.error(f"IMDb fetch error for '{query}': {e}")
        return None


def _imdb_fetch_sync(query: str, by_id: bool = False, file: str = None) -> dict:
    """Synchronous IMDb fetch (runs in thread pool via run_in_executor)."""
    try:
        from imdb import Cinemagoer
        ia = Cinemagoer()

        if not by_id:
            query = query.strip().lower()
            title = query
            year = re.findall(r'[1-2]\d{3}$', query, re.IGNORECASE)
            if year:
                year = list_to_str(year[:1])
                title = query.replace(year, "").strip()
            elif file is not None:
                year_match = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
                year = list_to_str(year_match[:1]) if year_match else None
            else:
                year = None

            search_results = ia.search_movie(title.lower(), results=10)
            if not search_results:
                return None

            if year:
                filtered = [k for k in search_results if str(k.get('year')) == str(year)]
                if not filtered:
                    filtered = search_results
            else:
                filtered = search_results

            typed = [k for k in filtered if k.get('kind') in ['movie', 'tv series']]
            movieid = (typed or filtered)[0].movieID
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
        imdb_id = f"tt{movie.get('imdbID')}"

        return {
            'title': movie.get('title'),
            'votes': movie.get('votes'),
            "aka": list_to_str(movie.get("akas")),
            "seasons": movie.get("number of seasons"),
            "box_office": movie.get('box office'),
            'localized_title': movie.get('localized title'),
            'kind': movie.get("kind"),
            "imdb_id": imdb_id,
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
            'plot': plot,
            'rating': str(movie.get("rating", "N/A")),
            'url': f'https://www.imdb.com/title/tt{movieid}'
        }
    except Exception as e:
        logger.error(f"IMDb sync fetch error: {e}")
        return None
