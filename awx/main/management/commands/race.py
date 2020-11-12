from multiprocessing import Process
from django.conf import settings
from django.core.management.base import BaseCommand
from rest_framework.exceptions import ValidationError


def target():
    try:
        if all([
            getattr(settings, 'AWX_ISOLATED_KEY_GENERATION', False) is True,
            getattr(settings, 'AWX_ISOLATED_PRIVATE_KEY', None)
        ]):
            pass
    except ValidationError as e:
        print(e)
    except Exception:
        pass


def race():
    workers = {}
    while True:
        workers = dict(
            (pid, p)
            for pid, p in workers.items() if p.is_alive()
        )
        while len(workers) < 10:
            p = Process(target=target)
            p.daemon = True
            p.start()
            workers[p.pid] = p



class Command(BaseCommand):
    """Cause a settings lookup race"""
    def handle(self, *args, **options):
        super(Command, self).__init__()
        race()
