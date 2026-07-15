from aiohttp import web
from web.stream_routes import routes as stream_routes
from web.miniapp_routes import routes as miniapp_routes
from web.netflix_routes import routes as netflix_routes

web_app = web.Application()
web_app.add_routes(stream_routes)
web_app.add_routes(miniapp_routes)
web_app.add_routes(netflix_routes)
