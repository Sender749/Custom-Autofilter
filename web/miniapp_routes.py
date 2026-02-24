import re
import json
import time
import hmac
import random
import hashlib
import logging
import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, unquote
from aiohttp import web
from aiohttp.web_request import Request

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_executor = ThreadPoolExecutor(max_workers=6)

try:
    from database.ia_filterdb import (
        collection, second_collection,
        get_search_results, get_file_details,
        is_second_db_configured,
    )
    from utils import get_size, temp
    from info import TMDB_API_KEY, BOT_TOKEN
    DB_AVAILABLE = True
except ImportError as _ie:
    DB_AVAILABLE = False
    TMDB_API_KEY = ''
    BOT_TOKEN = ''
    logger.warning(f"DB not available for miniapp routes: {_ie}")

_META_CACHE: dict = {}
_META_CACHE_TTL = 3600

def _cache_get(store, key, ttl):
    entry = store.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None

def _cache_set(store, key, val, ttl):
    store[key] = (time.time(), val)

_EXT  = re.compile(r'\.(mkv|mp4|avi|mov|flv|wmv|webm|ts|m4v)$', re.IGNORECASE)
_PUNC = re.compile(r'[@\[\]()\-_\+\.]+')
_SPC  = re.compile(r'\s{2,}')
_YEAR = re.compile(r'\b(19[5-9]\d|20[0-3]\d)\b')
_SE   = re.compile(r'\b[Ss](\d{1,2})[Ee](\d{1,2})\b')
_S    = re.compile(r'\b[Ss](\d{1,2})\b')
_E    = re.compile(r'\b[Ee](\d{1,2})\b')
_JUNK = re.compile(r'\b(480p|720p|1080p|2160p|4k|uhd|hdr10?[\+]?|hdrip|bluray|bdrip|remux|web[\-\s]?dl|webrip|webdl|hdtv|dvdrip|dvdscr|dvd|cam|telesync|x264|x265|hevc|avc|aac|ac3|mp3|dts|ddp|atmos|eac3|proper|repack|extended|unrated|theatrical|retail|limited|internal|sample|trailer)\b.*', re.IGNORECASE)
_LANG_STRIP = re.compile(r'\b(hindi|english|tamil|telugu|kannada|malayalam|bengali|punjabi|dual[\s.\-]?audio|multi[\s.\-]?audio|dubbed|subbed|hardsub|esub|hin|eng|tam|tel|kan|mal|ben|pun)\b.*', re.IGNORECASE)
_TAG_STRIP = re.compile(r'@\w+|\[[\w.\-]+\]', re.IGNORECASE)
_QUAL_MAP = [('2160p','2160p'),('4k','4K'),('uhd','4K'),('1080p','1080p'),('720p','720p'),('480p','480p'),('360p','360p'),('cam','CAM')]
_PRINT_MAP = [('bluray','BluRay'),('bdrip','BDRip'),('remux','Remux'),('web-dl','WEB-DL'),('webdl','WEB-DL'),('webrip','WEBRip'),('hdrip','HDRip'),('hdtv','HDTV'),('dvdrip','DVDRip'),('dvdscr','DVDScr'),('cam','CAM'),('ts','TS')]
_LANG_MAP = {'hindi':'Hindi','english':'English','tamil':'Tamil','telugu':'Telugu','kannada':'Kannada','malayalam':'Malayalam','bengali':'Bengali','punjabi':'Punjabi','dual audio':'Dual Audio','dual':'Dual Audio','multi audio':'Multi Audio','multi':'Multi Audio'}
_SUB_MAP = [('esub','English'),('hardsub','Hardcoded'),('subbed','Yes'),('subtitle','Yes')]
_QUAL_SCORE = {'2160p':100,'4k':100,'uhd':100,'1080p':80,'720p':60,'480p':30,'360p':20,'cam':1,'ts':1}
_ANIME_KEYWORDS = re.compile(r'\b(anime|hentai|ecchi|ova|oad|ona|manhwa|manhua|donghua|shonen|seinen|shoujo|josei|isekai|mecha|yaoi|yuri|dubbed\s*anime|sub\s*anime)\b|[\u3040-\u30FF\u4E00-\u9FFF]', re.IGNORECASE)
_SERIES_KEYWORDS = re.compile(r'\b(web\s*series|mini\s*series|limited\s*series|tv\s*show|complete\s*series|full\s*series|season\s*\d+|s\d{1,2}\s*complete|s\d{1,2}\s*pack|episodes?\s*\d+[-\u2013]\d+|ep\s*\d+\s*to\s*\d+)\b', re.IGNORECASE)

def extract_year(text):
    h = _YEAR.findall(text); return h[0] if h else ''
def extract_quality(text):
    t=text.lower()
    for k,l in _QUAL_MAP:
        if k in t: return l
    return ''
def extract_print_type(text):
    t=text.lower()
    for k,l in _PRINT_MAP:
        if k in t: return l
    return ''
def extract_language(text):
    t=text.lower(); langs=[]
    for k,l in _LANG_MAP.items():
        if k in t and l not in langs: langs.append(l)
    if 'Dual Audio' in langs or 'Multi Audio' in langs:
        langs=[l for l in langs if l not in ('Hindi','English','Tamil','Telugu','Kannada','Malayalam','Bengali','Punjabi')]
    return langs[:3]
def extract_subtitles(text):
    t=text.lower()
    for k,l in _SUB_MAP:
        if k in t: return l
    return ''
def extract_season_ep(text):
    m=_SE.search(text)
    if m: return int(m.group(1)),int(m.group(2))
    s=_S.search(text); e=_E.search(text)
    return (int(s.group(1)) if s else 0),(int(e.group(1)) if e else 0)
def quality_score(text):
    t=text.lower()
    for k,v in _QUAL_SCORE.items():
        if k in t: return v
    return 40
def clean_title(filename):
    n=_EXT.sub('',filename); n=_TAG_STRIP.sub(' ',n); n=_PUNC.sub(' ',n)
    m=_SE.search(n)
    if m: n=n[:m.start()]
    else:
        ms=_S.search(n)
        if ms: n=n[:ms.start()]
    my=_YEAR.search(n)
    if my: n=n[:my.start()]
    n=_JUNK.sub('',n); n=_LANG_STRIP.sub('',n); n=_SPC.sub(' ',n).strip()
    return n
def title_key(filename): return clean_title(filename).lower().strip()
def is_series(text): s,_=extract_season_ep(text); return s>0
def is_anime(text): return bool(_ANIME_KEYWORDS.search(text))
def detect_content_type(text,tmdb_type=None,tmdb_genres=None,tmdb_origin_country=None,tmdb_original_language=None):
    if is_anime(text): return 'anime'
    if tmdb_genres:
        gs=' '.join(g.lower() for g in tmdb_genres)
        if 'animation' in gs or 'anime' in gs: return 'anime'
    oc=tmdb_origin_country if isinstance(tmdb_origin_country,list) else [tmdb_origin_country or '']
    if 'JP' in oc and tmdb_type=='tv': return 'anime'
    if tmdb_original_language=='ja' and tmdb_type=='tv': return 'anime'
    if tmdb_type=='tv': return 'series'
    if is_series(text): return 'series'
    if _SERIES_KEYWORDS.search(text): return 'series'
    return 'movie'
def fmt_label(fname,size_str=''):
    parts=[]; q=extract_quality(fname)
    if q: parts.append(q)
    langs=extract_language(fname)
    if langs: parts.append('/'.join(langs))
    s,e=extract_season_ep(fname)
    if s and e: parts.append(f'S{s:02d}E{e:02d}')
    elif s: parts.append(f'Season {s}')
    if size_str: parts.append(size_str)
    return ' · '.join(parts) if parts else fname[:55]

async def _run_sync(fn,*args):
    loop=asyncio.get_event_loop()
    return await loop.run_in_executor(_executor,fn,*args)

def _sync_fetch_all_by_type(content_type):
    """Fetch ALL unique title docs from DB for a given content type."""
    try:
        docs=list(collection.find({}).sort('_id',-1).limit(6000))
        if is_second_db_configured():
            extra=list(second_collection.find({}).sort('_id',-1).limit(6000))
            seen_ids={d['_id'] for d in docs}
            docs+=[d for d in extra if d['_id'] not in seen_ids]
        def _classify(d):
            stored=d.get('category')
            if stored in ('movie','series','anime'): return stored
            return detect_content_type(d.get('caption') or d.get('file_name',''))
        seen_keys=set(); result=[]
        for d in docs:
            if _classify(d)!=content_type: continue
            fname=d.get('caption') or d.get('file_name','')
            tk=title_key(fname)
            if not tk or tk in seen_keys: continue
            seen_keys.add(tk); result.append(d)
        return result
    except Exception as exc:
        logger.error(f'_sync_fetch_all_by_type error: {exc}'); return []

def _sync_search_all(query):
    """Search ALL files in DB matching the query word. Returns unique title docs."""
    try:
        pattern=re.compile(re.escape(query),re.IGNORECASE)
        seen_keys=set(); result=[]
        cols=[collection]
        if is_second_db_configured(): cols.append(second_collection)
        for col in cols:
            for doc in col.find({'$or':[{'file_name':pattern},{'caption':pattern}]}).sort('_id',-1).limit(3000):
                fname=doc.get('caption') or doc.get('file_name','')
                tk=title_key(fname)
                if not tk or tk in seen_keys: continue
                seen_keys.add(tk); result.append(doc)
        result.sort(key=lambda d:d['_id'],reverse=True)
        return result
    except Exception as exc:
        logger.error(f'_sync_search_all error: {exc}'); return []

def _sync_search_by_title_key(key,limit,fuzzy=False):
    words=key.split()
    if not words: return []
    pattern=re.compile(re.escape(words[0]),re.IGNORECASE)
    results=[]; seen_ids=set()
    cols=[collection]
    if is_second_db_configured(): cols.append(second_collection)
    for col in cols:
        try:
            for doc in col.find({'$or':[{'file_name':pattern},{'caption':pattern}]}).sort('_id',-1).limit(limit*6):
                fname=doc.get('caption') or doc.get('file_name','')
                fk=title_key(fname)
                match=(fk==key) if not fuzzy else (key in fk or fk in key or words[0] in fk.lower())
                if match and doc['_id'] not in seen_ids:
                    seen_ids.add(doc['_id']); results.append(doc)
        except Exception as exc:
            logger.warning(f'_search_by_title_key col error: {exc}')
    results.sort(key=lambda d:d['_id'],reverse=True)
    return results[:limit]

async def _search_by_title_key(key,limit=500,fuzzy=False):
    return await _run_sync(_sync_search_by_title_key,key,limit,fuzzy)

def _fuzzy_normalize(q):
    q=q.lower().strip(); q=re.sub(r'(.)\1+',r'\1',q)
    q=re.sub(r'[^a-z0-9\u0080-\uffff\s]',' ',q); q=re.sub(r'\s{2,}',' ',q).strip()
    return q

def _organise_files(docs):
    any_series=any(is_series(d.get('caption') or d.get('file_name','')) for d in docs)
    if not any_series:
        return {'type':'movie','movie_files':sorted(docs,key=lambda d:-quality_score(d.get('caption') or d.get('file_name',''))),'seasons':None}
    tree=defaultdict(lambda:defaultdict(list))
    for doc in docs:
        fname=doc.get('caption') or doc.get('file_name',''); s,e=extract_season_ep(fname); tree[s][e].append(doc)
    result={}
    for s_num in sorted(tree):
        result[s_num]={}
        for e_num in sorted(tree[s_num]):
            result[s_num][e_num]=sorted(tree[s_num][e_num],key=lambda d:-quality_score(d.get('caption') or d.get('file_name','')))
    return {'type':'series','movie_files':None,'seasons':result}

def doc_to_obj(doc):
    caption=doc.get('caption','').strip(); fname=doc.get('file_name','')
    display=caption if caption else fname; raw_meta=caption if caption else fname
    size_str=get_size(doc.get('file_size',0)); s,e=extract_season_ep(raw_meta)
    return {'id':str(doc['_id']),'caption':display,'raw_name':fname,'label':fmt_label(raw_meta,size_str),'size':size_str,'quality':extract_quality(raw_meta),'print_type':extract_print_type(raw_meta),'languages':extract_language(raw_meta),'subtitles':extract_subtitles(raw_meta),'season':s,'episode':e}

def _doc_to_card(doc):
    """Convert a DB doc to card dict. Uses cached TMDB meta if available, else returns bare card for lazy enrichment."""
    fname=doc.get('caption') or doc.get('file_name','')
    title=clean_title(fname); year=extract_year(fname)
    ck=title.lower()+'|'+year; meta=_cache_get(_META_CACHE,ck,_META_CACHE_TTL)
    ctype=detect_content_type(fname,tmdb_type=(meta or {}).get('type'),tmdb_genres=(meta or {}).get('genres',[]),tmdb_origin_country=(meta or {}).get('origin_country',[]),tmdb_original_language=(meta or {}).get('original_language',''))
    return {'group_title':title,'id':str(doc['_id']),'name':(meta or {}).get('title') or title,'year':(meta or {}).get('year') or year,'poster':(meta or {}).get('poster') if meta else None,'rating':(meta or {}).get('rating') if meta else None,'genres':(meta or {}).get('genres',[]) if meta else [],'type':ctype,'file_count':1}

TMDB_IMG='https://image.tmdb.org/t/p/w500'
TMDB_BACK='https://image.tmdb.org/t/p/w1280'

async def _tmdb_fetch(title,year=''):
    api_key=TMDB_API_KEY
    if not api_key: return None
    try:
        import aiohttp,socket
        to=aiohttp.ClientTimeout(total=8,connect=4)
        connector=aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=connector,timeout=to) as sess:
            params={'api_key':api_key,'query':title,'page':1}
            if year: params['year']=year
            async with sess.get('https://api.themoviedb.org/3/search/multi',params=params) as r:
                if r.status!=200: return None
                data=await r.json()
                results=[x for x in data.get('results',[]) if x.get('media_type')!='person']
            if not results and year:
                async with sess.get('https://api.themoviedb.org/3/search/multi',params={'api_key':api_key,'query':title,'page':1}) as r2:
                    if r2.status==200: results=[x for x in (await r2.json()).get('results',[]) if x.get('media_type')!='person']
            if not results: return None
            tl=title.lower()
            item=next((x for x in results if (x.get('title') or x.get('name') or '').lower()==tl),results[0])
            mt=item.get('media_type','movie'); iid=item.get('id'); detail=None
            async with sess.get(f'https://api.themoviedb.org/3/{mt}/{iid}',params={'api_key':api_key,'append_to_response':'credits'}) as dr:
                if dr.status==200: detail=await dr.json()
        src=detail or item
        genres=[g['name'] for g in src.get('genres',[])] if detail else []
        cr=(detail or {}).get('credits',{}); cast=[c['name'] for c in cr.get('cast',[])[:6]]
        dirs=[c['name'] for c in cr.get('crew',[]) if c.get('job')=='Director'][:2]
        rt=(detail or {}).get('runtime') or (((detail or {}).get('episode_run_time') or [None])[0])
        return {'title':src.get('title') or src.get('name',title),'year':(src.get('release_date') or src.get('first_air_date') or '')[:4],'poster':f"{TMDB_IMG}{src['poster_path']}" if src.get('poster_path') else None,'backdrop':f"{TMDB_BACK}{src['backdrop_path']}" if src.get('backdrop_path') else None,'rating':round(float(src.get('vote_average',0) or 0),1),'plot':src.get('overview',''),'genres':genres,'cast':cast,'director':', '.join(dirs),'runtime':f'{rt} min' if rt else '','type':mt,'seasons':(detail or {}).get('number_of_seasons'),'episodes':(detail or {}).get('number_of_episodes'),'tagline':(detail or {}).get('tagline',''),'status':(detail or {}).get('status',''),'imdb_id':(detail or {}).get('imdb_id',''),'origin_country':(detail or src).get('origin_country',[]),'original_language':(detail or src).get('original_language','')}
    except Exception as exc:
        logger.warning(f'TMDB error for "{title}": {type(exc).__name__}: {exc}'); return None

async def _imdb_fetch(title,year=''):
    try:
        from imdb import Cinemagoer; ia=Cinemagoer()
        hits=await asyncio.wait_for(_run_sync(lambda:ia.search_movie(title,results=8)),timeout=6)
        if not hits: return None
        if year:
            filtered=[h for h in hits if str(h.get('year',''))==year]; hits=filtered or hits
        typed=[h for h in hits if h.get('kind') in ('movie','tv series')]; pick=(typed or hits)[0]
        mv=await asyncio.wait_for(_run_sync(lambda:ia.get_movie(pick.movieID)),timeout=8)
        raw_plot=mv.get('plot',[]); plot=''
        if raw_plot: plot=raw_plot[0].split('::')[0] if '::' in raw_plot[0] else raw_plot[0]
        cast=[str(c) for c in (mv.get('cast') or [])[:6]]
        dirs_raw=mv.get('directors') or mv.get('director') or []
        dirs=[str(d) for d in (dirs_raw[:2] if isinstance(dirs_raw,list) else [])]
        rt=(mv.get('runtimes') or [''])[0]; genres=mv.get('genres',[])[:4]
        poster=mv.get('full-size cover url') or mv.get('cover url')
        mt='tv' if mv.get('kind')=='tv series' else 'movie'
        return {'title':mv.get('title',title),'year':str(mv.get('year',year or '')),'poster':poster,'backdrop':None,'rating':float(mv.get('rating',0) or 0),'plot':plot[:600],'genres':genres if isinstance(genres,list) else [],'cast':cast,'director':', '.join(dirs),'runtime':f'{rt} min' if rt else '','type':mt,'seasons':mv.get('number of seasons'),'episodes':None,'tagline':'','status':'','imdb_id':f"tt{pick.movieID}"}
    except Exception as exc:
        logger.warning(f'IMDB fallback error for "{title}": {type(exc).__name__}: {exc}'); return None

async def _get_meta(title,year=''):
    if not title: return None
    cache_key=f"{title.lower()}|{year}"
    cached=_cache_get(_META_CACHE,cache_key,_META_CACHE_TTL)
    if cached is not None: return cached
    tmdb=await _tmdb_fetch(title,year)
    if tmdb and tmdb.get('poster'): _cache_set(_META_CACHE,cache_key,tmdb,_META_CACHE_TTL); return tmdb
    imdb=await _imdb_fetch(title,year)
    if imdb:
        if tmdb: imdb['backdrop']=imdb.get('backdrop') or tmdb.get('backdrop'); imdb['genres']=imdb.get('genres') or tmdb.get('genres',[])
        _cache_set(_META_CACHE,cache_key,imdb,_META_CACHE_TTL); return imdb
    _cache_set(_META_CACHE,cache_key,tmdb,_META_CACHE_TTL); return tmdb

CORS={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'GET, POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type, X-Telegram-Init-Data'}

def json_resp(data,status=200):
    return web.Response(text=json.dumps(data,default=str),status=status,content_type='application/json',headers=CORS)

def cors_preflight():
    return web.Response(headers=CORS)

async def miniapp_health(request):
    return json_resp({'ok':True,'db':DB_AVAILABLE,'tmdb_key':bool(TMDB_API_KEY)})


async def miniapp_browse(request):
    """
    GET /miniapp/browse?type=movies|series&page=0&limit=24&ts=<timestamp>

    SHUFFLE logic:
    - Fetches ALL unique titles from DB for this type
    - Shuffles them randomly and caches the shuffled deck for 10 minutes
    - ts param (refresh button) rebuilds + reshuffles the deck immediately
    - Pages through the full shuffled deck — no title ever repeats
    - No artificial cap on total results
    """
    if request.method=='OPTIONS': return cors_preflight()
    if not DB_AVAILABLE: return json_resp({'ok':False,'error':'DB not available'},500)
    try:
        raw_type=request.rel_url.query.get('type','movies').lower()
        content_type='series' if raw_type in ('serie','series') else 'movie'
        page=max(0,int(request.rel_url.query.get('page',0)))
        limit=max(1,int(request.rel_url.query.get('limit',24)))
        is_refresh=bool(request.rel_url.query.get('ts'))
    except (ValueError,TypeError):
        page,limit,content_type,is_refresh=0,24,'movie',False

    deck_key=f'_deck_{content_type}'

    # Rebuild shuffled deck on refresh OR page 0 OR cache miss
    if is_refresh or page==0 or _cache_get(_META_CACHE,deck_key,600) is None:
        all_docs=await _run_sync(_sync_fetch_all_by_type,content_type)
        random.shuffle(all_docs)
        deck=[_doc_to_card(d) for d in all_docs]
        _cache_set(_META_CACHE,deck_key,deck,600)
        logger.info(f'Browse deck rebuilt+shuffled: {content_type}, {len(deck)} titles')
    else:
        deck=_cache_get(_META_CACHE,deck_key,600) or []

    start=page*limit; end=start+limit
    results=deck[start:end]; has_more=end<len(deck)
    return json_resp({'ok':True,'results':results,'count':len(results),'page':page,'total':len(deck),'has_more':has_more})


async def miniapp_search(request):
    """
    GET /miniapp/search?q=<query>&year_filter=<year>

    Searches ALL files in DB for the exact query word (any file whose
    filename/caption contains the word). Returns ALL unique titles found.
    No cap. Posters enriched lazily by frontend.
    """
    if request.method=='OPTIONS': return cors_preflight()
    if not DB_AVAILABLE: return json_resp({'ok':False,'error':'DB not available'},500)
    q=request.rel_url.query.get('q','').strip()
    year_filter=request.rel_url.query.get('year_filter','').strip()
    if not q: return json_resp({'ok':False,'error':"Missing 'q'"},400)

    # Primary: direct regex search in DB
    all_docs=await _run_sync(_sync_search_all,q)

    # Secondary: try get_search_results for any extra matches
    try:
        extra_files,_,_=await get_search_results(q,max_results=500)
        seen_keys={title_key(d.get('caption') or d.get('file_name','')) for d in all_docs}
        for doc in extra_files:
            tk=title_key(doc.get('caption') or doc.get('file_name',''))
            if tk and tk not in seen_keys:
                seen_keys.add(tk); all_docs.append(doc)
    except Exception: pass

    # If still nothing, try fuzzy
    if not all_docs:
        fq=_fuzzy_normalize(q)
        if fq and fq!=q:
            all_docs=await _run_sync(_sync_search_all,fq)

    if year_filter:
        all_docs=[d for d in all_docs if extract_year(d.get('caption') or d.get('file_name',''))==year_filter]

    all_docs.sort(key=lambda d:d['_id'],reverse=True)
    results=[_doc_to_card(d) for d in all_docs]
    return json_resp({'ok':True,'results':results,'total':len(results)})


async def miniapp_group_details(request):
    if request.method=='OPTIONS': return cors_preflight()
    if not DB_AVAILABLE: return json_resp({'ok':False,'error':'DB not available'},500)
    file_id=request.rel_url.query.get('id','').strip()
    if not file_id: return json_resp({'ok':False,'error':"Missing 'id'"},400)
    try:
        rep=await get_file_details(file_id)
    except Exception as exc:
        logger.error(f'get_file_details error: {exc}'); return json_resp({'ok':False,'error':'DB lookup failed'},500)
    if not rep: return json_resp({'ok':False,'error':'File not found'},404)
    caption=rep.get('caption','').strip(); fname=rep.get('file_name','')
    primary=caption if caption else fname
    title=clean_title(primary); year=extract_year(primary); key=title_key(primary)
    try:
        all_variants=await _search_by_title_key(key,fuzzy=False)
    except Exception as exc:
        logger.error(f'_search_by_title_key error: {exc}'); all_variants=[]
    if not all_variants: all_variants=[rep]
    all_variants.sort(key=lambda d:d['_id'],reverse=True)
    meta=await _get_meta(title,year)
    organised=_organise_files(all_variants)
    def ser_season(ep_dict):
        out={}
        for ep_num,ep_docs in sorted(ep_dict.items()):
            out[str(ep_num)]={'label':f'Episode {ep_num}' if ep_num>0 else 'Season Pack','files':[doc_to_obj(d) for d in ep_docs]}
        return out
    if organised['type']=='series' and organised['seasons']:
        seasons_out={str(s):ser_season(ep_dict) for s,ep_dict in sorted(organised['seasons'].items())}
        files_payload={'seasons':seasons_out}
    else:
        files_payload={'movie_files':[doc_to_obj(d) for d in (organised['movie_files'] or [])]}
    sample_langs,sample_subs,sample_qual,sample_print=[],'',' ',''
    for doc in all_variants[:5]:
        fn=doc.get('caption') or doc.get('file_name','')
        if not sample_langs: sample_langs=extract_language(fn)
        if not sample_subs: sample_subs=extract_subtitles(fn)
        if not sample_qual: sample_qual=extract_quality(fn)
        if not sample_print: sample_print=extract_print_type(fn)
    return json_resp({'ok':True,'content_type':organised['type'],'meta':meta,'caption_title':primary,'file_count':len(all_variants),'db_languages':sample_langs,'db_subtitles':sample_subs,'db_quality':sample_qual,'db_print':sample_print,**files_payload})


async def miniapp_html(request):
    import os
    html_path=os.path.join(os.path.dirname(os.path.dirname(__file__)),'miniapp.html')
    if os.path.exists(html_path): return web.FileResponse(html_path)
    return web.Response(text='miniapp.html not found',status=404)


def _validate_init_data(init_data,bot_token):
    if not init_data or not bot_token: return None
    try:
        parsed=parse_qs(init_data,strict_parsing=True)
        received_hash=parsed.pop('hash',[None])[0]
        if not received_hash: return None
        data_check_string='\n'.join(sorted(f'{k}={v[0]}' for k,v in parsed.items()))
        secret_key=hmac.new(b'WebAppData',bot_token.encode(),hashlib.sha256).digest()
        expected=hmac.new(secret_key,data_check_string.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,received_hash): return None
        user_raw=parsed.get('user',[None])[0]
        if not user_raw: return None
        return json.loads(unquote(user_raw))
    except Exception as exc:
        logger.error(f'_validate_init_data exception: {exc}'); return None


async def miniapp_poster(request):
    if request.method=='OPTIONS': return cors_preflight()
    title=request.rel_url.query.get('title','').strip()
    year=request.rel_url.query.get('year','').strip()
    if not title: return json_resp({'ok':False,'error':"Missing 'title'"},400)
    meta=await _get_meta(title,year)
    if meta and meta.get('poster'): return json_resp({'ok':True,'meta':meta})
    return json_resp({'ok':False,'meta':None})


async def miniapp_send_file(request):
    if request.method=='OPTIONS': return cors_preflight()
    if not DB_AVAILABLE: return json_resp({'ok':False,'error':'server_error'},500)
    try:
        body=await request.json()
    except Exception:
        return json_resp({'ok':False,'error':'bad_request'},400)
    file_id=(body.get('file_id') or '').strip()
    init_data=(body.get('initData') or '').strip()
    if not file_id: return json_resp({'ok':False,'error':'bad_request'},400)
    user_data=_validate_init_data(init_data,BOT_TOKEN)
    if not user_data: return json_resp({'ok':False,'error':'Unauthorized'},401)
    user_id=user_data.get('id')
    bot=getattr(temp,'BOT',None)
    if bot is None: return json_resp({'ok':False,'error':'server_error'},500)
    try:
        file_doc=await get_file_details(file_id)
    except Exception:
        return json_resp({'ok':False,'error':'server_error'},500)
    if not file_doc: return json_resp({'ok':False,'error':'file_not_found'},404)
    try:
        from plugins.miniapp_plugin import _send_file_with_checks
        class _FakeUser:
            id=user_id; mention=f'<a href="tg://user?id={user_id}">User</a>'
        class _FakeMsg:
            from_user=_FakeUser()
            async def reply_text(self,text,**kw):
                try: await bot.send_message(chat_id=user_id,text=text,**{k:v for k,v in kw.items() if k!='protect_content'})
                except Exception as e: logger.error(f'reply_text failed: {e}')
            async def reply(self,text,reply_to_message_id=None,**kw):
                try: await bot.send_message(chat_id=user_id,text=text,**kw)
                except Exception as e: logger.error(f'reply failed: {e}')
            async def reply_photo(self,photo,caption='',**kw):
                try: await bot.send_photo(chat_id=user_id,photo=photo,caption=caption,**kw)
                except Exception as e: logger.error(f'reply_photo failed: {e}')
            async def delete(self): pass
        asyncio.ensure_future(_send_file_with_checks(bot,_FakeMsg(),user_id,file_id))
        return json_resp({'ok':True})
    except Exception as exc:
        logger.error(f'send_file dispatch exception: {type(exc).__name__}: {exc}',exc_info=True)
        return json_resp({'ok':False,'error':'server_error'},500)


routes=[
    web.route('GET',     '/miniapp',              miniapp_html),
    web.route('GET',     '/miniapp/health',        miniapp_health),
    web.route('GET',     '/miniapp/browse',        miniapp_browse),
    web.route('GET',     '/miniapp/search',        miniapp_search),
    web.route('GET',     '/miniapp/group_details', miniapp_group_details),
    web.route('GET',     '/miniapp/poster',        miniapp_poster),
    web.route('POST',    '/miniapp/send_file',     miniapp_send_file),
    web.route('OPTIONS', '/miniapp/browse',        miniapp_browse),
    web.route('OPTIONS', '/miniapp/search',        miniapp_search),
    web.route('OPTIONS', '/miniapp/group_details', miniapp_group_details),
    web.route('OPTIONS', '/miniapp/send_file',     miniapp_send_file),
    web.route('OPTIONS', '/miniapp/poster',        miniapp_poster),
]
