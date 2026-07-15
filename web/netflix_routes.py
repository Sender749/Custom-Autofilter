import os
from aiohttp import web

routes = web.RouteTableDef()

# Serves the Netflix-style frontend at /netflix.
# Reuses the existing /miniapp/* JSON API (browse, recent, search, poster,
# group_details) — no changes made to stream_routes.py, miniapp_routes.py,
# or any bot/plugin logic. This route only serves a static HTML file.

@routes.get("/netflix")
async def netflix_serve(request):
    html = os.path.join(os.path.dirname(os.path.dirname(__file__)), "netflix.html")
    if os.path.exists(html):
        return web.FileResponse(html)
    return web.Response(text="<h1>Deploy netflix.html to your server root</h1>",
                        content_type="text/html")
