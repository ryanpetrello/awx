import logging
import string
from urllib import parse as urlparse
from logging.handlers import SysLogHandler

from django.conf import settings
from jinja2 import Template

from awx.main.utils.reload import reload_syslog


__all__ = ['SysLogNGHandler', 'ColorHandler']


CONFIG = Template('''
@version: 3.5
@include "scl.conf"

destination d_tcp {
    tcp("{{destination_host}}" port({{destination_port}}));
};
destination d_udp {
    udp("{{destination_host}}" port({{destination_port}}));
};
destination d_http {
    http(
        url("{{destination_url}}")
        method("POST")
        workers(2)
    );
};
log {
    source {
        unix-dgram("/tmp/syslog-ng.sock");
    };
    {% if protocol == 'tcp': %}destination(d_tcp);{% endif %}
    {% if protocol == 'udp': %}destination(d_udp);{% endif %}
    {% if protocol.startswith('http'): %}destination(d_http);{% endif %}
};
''')


class SysLogNGHandler(SysLogHandler):
    append_nul = False

    def format(self, record):
        msg = super(SysLogNGHandler, self).format(record)
        msg = msg.decode('utf-8')
        return msg

    @classmethod
    def reconfigure(cls):
        if settings.LOG_AGGREGATOR_ENABLED:
            netloc = cls.netloc()
            with open('/etc/syslog-ng/syslog-ng.conf', 'w') as f:
                f.write(CONFIG.render(
                    protocol=settings.LOG_AGGREGATOR_PROTOCOL,
                    destination_url=urlparse.urlunsplit(netloc),
                    destination_host=netloc.hostname,
                    destination_port=netloc.port or getattr(settings, 'LOG_AGGREGATOR_PORT', '')
                ))
            reload_syslog()

    @classmethod
    def netloc(self, scheme='https'):
        host = getattr(settings, 'LOG_AGGREGATOR_HOST', '')
        port = getattr(settings, 'LOG_AGGREGATOR_PORT', '')
        # urlparse requires '//' to be provided if scheme is not specified
        original_parsed = urlparse.urlsplit(host)
        if (not original_parsed.scheme and not host.startswith('//')) or original_parsed.hostname is None:
            host = '%s://%s' % (scheme, host) if scheme else '//%s' % host
        return urlparse.urlsplit(host)



ColorHandler = logging.StreamHandler

if settings.COLOR_LOGS is True:
    try:
        from logutils.colorize import ColorizingStreamHandler

        class ColorHandler(ColorizingStreamHandler):

            def format(self, record):
                message = logging.StreamHandler.format(self, record)
                return '\n'.join([
                    self.colorize(line, record)
                    for line in message.splitlines()
                ])

            level_map = {
                logging.DEBUG: (None, 'green', True),
                logging.INFO: (None, None, True),
                logging.WARNING: (None, 'yellow', True),
                logging.ERROR: (None, 'red', True),
                logging.CRITICAL: (None, 'red', True),
            }
    except ImportError:
        # logutils is only used for colored logs in the dev environment
        pass
