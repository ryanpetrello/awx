from datetime import datetime
import tracemalloc

import redis
import logging

from django.conf.urls import url
from django.conf import settings

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

from . import consumers


TRACE_INTERVAL = 60 * 5  # take a tracemalloc snapshot every 5 minutes


logger = logging.getLogger('awx.main.routing')


class AWXProtocolTypeRouter(ProtocolTypeRouter):
    def __init__(self, *args, **kwargs):
        tracemalloc.start()

        try:
            r = redis.Redis.from_url(settings.BROKER_URL)
            for k in r.scan_iter('asgi:*', 500):
                logger.debug(f"cleaning up Redis key {k}")
                r.delete(k)
        except redis.exceptions.RedisError as e:
            logger.warn("encountered an error communicating with redis.")
            raise e
        super().__init__(*args, **kwargs)


class SnapshottedEventConsumer(consumers.EventConsumer):

    last_snapshot = datetime.min

    async def connect(self):
        if (datetime.now() - SnapshottedEventConsumer.last_snapshot).total_seconds() > TRACE_INTERVAL:
            snapshot = tracemalloc.take_snapshot()
            top_stats = snapshot.statistics('lineno')
            top_stats = [
                stat for stat in top_stats[:10]
                if 'importlib._bootstrap_external' not in str(stat)
            ]
            for stat in top_stats[:5]:
                logger.error('[TRACE] ' + str(stat))
            SnapshottedEventConsumer.last_snapshot = datetime.now()
        await super().connect()


websocket_urlpatterns = [
    url(r'websocket/$', SnapshottedEventConsumer),
    url(r'websocket/broadcast/$', consumers.BroadcastConsumer),
]

application = AWXProtocolTypeRouter({
    'websocket': AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
