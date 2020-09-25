import cProfile
import io
import json
import logging
import os
import pstats
import signal
import tempfile
import time
import traceback

from django.conf import settings
from django.utils.timezone import now as tz_now
from django.db import DatabaseError, OperationalError, connection as django_connection
from django.db.utils import InterfaceError, InternalError, IntegrityError

import psutil
import psycopg2
import redis

from awx.main.consumers import emit_channel_notification
from awx.main.models import (JobEvent, AdHocCommandEvent, ProjectUpdateEvent,
                             InventoryUpdateEvent, SystemJobEvent, UnifiedJob,
                             Job)
from awx.main.tasks import handle_success_and_failure_notifications
from awx.main.models.events import emit_event_detail

from .base import BaseWorker

logger = logging.getLogger('awx.main.commands.run_callback_receiver')


EVENT_COLS = {
    'main_jobevent': (
        'created', 'modified', 'event', 'event_data', 'failed', 'changed',
        'host_name', 'play', 'role', 'task', 'counter', 'host_id', 'job_id',
        'uuid', 'parent_uuid', 'end_line', 'playbook', 'start_line',
        'stdout', 'verbosity'
    )
}


def clean_csv_value(value):
    # The string "\N" is the default string used by PostgreSQL to indicate NULL in COPY
    if value is None:
        return r'\N'
    return str(value).replace('\n', '\\n').replace('\r', '\\r')


class StringIteratorIO(io.TextIOBase):

    def __init__(self, i):
        self._iter = i
        self._buff = ''

    def readable(self):
        return True

    def _readone(self, n):
        while not self._buff:
            try:
                self._buff = next(self._iter)
            except StopIteration:
                break
        ret = self._buff[:n]
        self._buff = self._buff[len(ret):]
        return ret

    def read(self, n):
        line = []
        if n is None or n < 0:
            while True:
                m = self._readone()
                if not m:
                    break
                line.append(m)
        else:
            while n > 0:
                m = self._readone(n)
                if not m:
                    break
                n -= len(m)
                line.append(m)
        return ''.join(line)


class CallbackBrokerWorker(BaseWorker):
    '''
    A worker implementation that deserializes callback event data and persists
    it into the database.

    The code that *generates* these types of messages is found in the
    ansible-runner display callback plugin.
    '''

    MAX_RETRIES = 2
    last_stats = time.time()
    total = 0
    last_event = ''
    prof = None

    def __init__(self):
        self.buff = {}
        self.pid = os.getpid()
        self.redis = redis.Redis.from_url(settings.BROKER_URL)
        for key in self.redis.keys('awx_callback_receiver_statistics_*'):
            self.redis.delete(key)

    def read(self, queue):
        try:
            res = self.redis.blpop(settings.CALLBACK_QUEUE, timeout=settings.JOB_EVENT_BUFFER_SECONDS)
            if res is None:
                return {'event': 'FLUSH'}
            self.total += 1
            return json.loads(res[1])
        except redis.exceptions.RedisError:
            logger.exception("encountered an error communicating with redis")
            time.sleep(1)
        except (json.JSONDecodeError, KeyError):
            logger.exception("failed to decode JSON message from redis")
        finally:
            self.record_statistics()
        return {'event': 'FLUSH'}

    def record_statistics(self):
        # buffer stat recording to once per (by default) 5s
        if time.time() - self.last_stats > settings.JOB_EVENT_STATISTICS_INTERVAL:
            try:
                self.redis.set(f'awx_callback_receiver_statistics_{self.pid}', self.debug())
                self.last_stats = time.time()
            except Exception:
                logger.exception("encountered an error communicating with redis")
                self.last_stats = time.time()

    def debug(self):
        return f'.  worker[pid:{self.pid}] sent={self.total} rss={self.mb}MB {self.last_event}'

    @property
    def mb(self):
        return '{:0.3f}'.format(
            psutil.Process(self.pid).memory_info().rss / 1024.0 / 1024.0
        )

    def toggle_profiling(self, *args):
        if self.prof:
            self.prof.disable()
            filename = f'callback-{self.pid}.pstats'
            filepath = os.path.join(tempfile.gettempdir(), filename)
            with open(filepath, 'w') as f:
                pstats.Stats(self.prof, stream=f).sort_stats('cumulative').print_stats()
            pstats.Stats(self.prof).dump_stats(filepath + '.raw')
            self.prof = False
            logger.error(f'profiling is disabled, wrote {filepath}')
        else:
            self.prof = cProfile.Profile()
            self.prof.enable()
            logger.error('profiling is enabled')

    def work_loop(self, *args, **kw):
        if settings.AWX_CALLBACK_PROFILE:
            signal.signal(signal.SIGUSR1, self.toggle_profiling)
        return super(CallbackBrokerWorker, self).work_loop(*args, **kw)

    def flush(self, force=False):
        if (
            force or
            any([len(events) >= 1000 for events in self.buff.values()])
        ):
            with django_connection.cursor() as cursor:
                for cls, events in self.buff.items():
                    try:
                        cursor.copy_from(
                            StringIteratorIO(iter(events)),
                            'main_jobevent',
                            sep='~',
                            columns=EVENT_COLS['main_jobevent']
                        )
                    except psycopg2.errors.BadCopyFileFormat as exc:
                        logger.exception('Database Error Saving Job Event')
                        pass
                #    logger.debug(f'{cls.__name__}.objects.bulk_create({len(events)})')
                #    try:
                #        cls.objects.bulk_create(events)
                #    except Exception as exc:
                #        # if an exception occurs, we should re-attempt to save the
                #        # events one-by-one, because something in the list is
                #        # broken/stale (e.g., an IntegrityError on a specific event)
                #        for e in events:
                #            try:
                #                if (
                #                    isinstance(exc, IntegrityError) and
                #                    getattr(e, 'host_id', '')
                #                ):
                #                    # this is one potential IntegrityError we can
                #                    # work around - if the host disappears before
                #                    # the event can be processed
                #                    e.host_id = None
                #                e.save()
                #            except Exception:
                #                logger.exception('Database Error Saving Job Event')
                #    for e in events:
                #        emit_event_detail(e)
                # TODO: FIX WEBSOCKET EMIT
            self.buff = {}

    def perform_work(self, body):
        try:
            flush = body.get('event') == 'FLUSH'
            if flush:
                self.last_event = ''
            if not flush:
                event_map = {
                    'job_id': JobEvent,
                    'ad_hoc_command_id': AdHocCommandEvent,
                    'project_update_id': ProjectUpdateEvent,
                    'inventory_update_id': InventoryUpdateEvent,
                    'system_job_id': SystemJobEvent,
                }

                job_identifier = 'unknown job'
                for key, cls in event_map.items():
                    if key in body:
                        job_identifier = body[key]
                        break

                self.last_event = f'\n\t- {cls.__name__} for #{job_identifier} ({body.get("event", "")} {body.get("uuid", "")})'  # noqa

                if body.get('event') == 'EOF':
                    try:
                        final_counter = body.get('final_counter', 0)
                        logger.info('Event processing is finished for Job {}, sending notifications'.format(job_identifier))
                        # EOF events are sent when stdout for the running task is
                        # closed. don't actually persist them to the database; we
                        # just use them to report `summary` websocket events as an
                        # approximation for when a job is "done"
                        emit_channel_notification(
                            'jobs-summary',
                            dict(group_name='jobs', unified_job_id=job_identifier, final_counter=final_counter)
                        )
                        # Additionally, when we've processed all events, we should
                        # have all the data we need to send out success/failure
                        # notification templates
                        uj = UnifiedJob.objects.get(pk=job_identifier)

                        if isinstance(uj, Job):
                            # *actual playbooks* send their success/failure
                            # notifications in response to the playbook_on_stats
                            # event handling code in main.models.events
                            pass
                        elif hasattr(uj, 'send_notification_templates'):
                            handle_success_and_failure_notifications.apply_async([uj.id])
                    except Exception:
                        logger.exception('Worker failed to emit notifications: Job {}'.format(job_identifier))
                    return

                event = cls.create_from_data(**body)

                now = tz_now().isoformat()
                event = '~'.join([
                    clean_csv_value(v)
                    for v in (
                        now,       # FIXME: created
                        now,               # modified
                        event.get('event', '') or '',
                        json.dumps(event.get('event_data', '{}')) or '',
                        False,                              # failed
                        True,                               # changed
                        event.get('host_name', '') or '',
                        event.get('play', '') or '',
                        event.get('role', '') or '',
                        event.get('task', '') or '',
                        event.get('counter', 0) or 0,
                        1,                                  # FIXME: host_id
                        event.get(key),                     # job_id
                        event['uuid'],
                        event.get('parent_uuid', '') or '',
                        event.get('end_line', 0) or 0,
                        event.get('playbook', '') or '',
                        event.get('start_line', 0) or 0,
                        event['stdout'] or '',
                        event.get('verbosity') or 0,
                    )
                ]) + '\n'
                self.buff.setdefault(cls, []).append(event)

            retries = 0
            while retries <= self.MAX_RETRIES:
                try:
                    self.flush(force=flush)
                    break
                except (OperationalError, InterfaceError, InternalError):
                    if retries >= self.MAX_RETRIES:
                        logger.exception('Worker could not re-establish database connectivity, giving up on one or more events.')
                        return
                    delay = 60 * retries
                    logger.exception('Database Error Saving Job Event, retry #{i} in {delay} seconds:'.format(
                        i=retries + 1,
                        delay=delay
                    ))
                    django_connection.close()
                    time.sleep(delay)
                    retries += 1
                except DatabaseError:
                    logger.exception('Database Error Saving Job Event')
                    break
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error('Callback Task Processor Raised Exception: %r', exc)
            logger.error('Detail: {}'.format(tb))
