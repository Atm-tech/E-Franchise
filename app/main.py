"""ASGI application composition for ``uvicorn app.main:app``."""

from app.core import *  # noqa: F401,F403
from app.routes import admin, auth, delivery, franchise, guard, pos, system

for route_module in (auth, admin, franchise, pos, guard, delivery, system):
    app.include_router(route_module.router)

operations.register(__import__(__name__, fromlist=["app"]))
dispatch_rules.register(__import__(__name__, fromlist=["app"]))
from app import mobile_api
mobile_api.register(__import__(__name__, fromlist=["app"]))
