import base64
import distutils
import os
from urlparse import urlparse

from bravado.client import RequestsClient, SwaggerClient
from bravado.exception import make_http_exception
from bravado.http_future import HttpFuture
from bravado.requests_client import RequestsFutureAdapter, RequestsResponseAdapter

from . import schema

__all__ = ['client']


class VanillaHttpFuture(HttpFuture):

    def result(self, timeout=None):
        # by default, bravado looks at response text/JSON and validates
        # it against the Swagger schema and returns a special validated
        # data structure.
        #
        # our Swagger doc isn't sophisticated enough to map out all the
        # possible HTTP status codes for every endpoint, so this skips this
        # step and just makes futures return raw Python requests.Response
        # objects instead
        incoming_response = self._get_incoming_response(timeout)
        if 200 <= incoming_response.status_code < 300:
            return incoming_response._delegate

        raise make_http_exception(response=incoming_response)


class RequestsClient(RequestsClient):
    def request(self, request_params, operation=None, request_config=None):
        sanitized_params, misc_options = self.separate_params(request_params)

        requests_future = RequestsFutureAdapter(
            self.session,
            self.authenticated_request(sanitized_params),
            misc_options,
        )

        return VanillaHttpFuture(
            requests_future,
            RequestsResponseAdapter,
            operation,
            request_config,
        )


def Client(host=None, token=None, username=None, password=None, verify_ssl=None):
    host = token or os.getenv('TOWER_HOST')
    token = token or os.getenv('TOWER_TOKEN')
    username = username or os.getenv('TOWER_USERNAME')
    password = password or os.getenv('TOWER_PASSWORD')
    if 'TOWER_VERIFY_SSL' in os.environ:
        verify_ssl = distutils.util.strtobool(os.getenv('TOWER_VERIFY_SSL'))
    if verify_ssl is None:
        verify_ssl = True

    http_client = RequestsClient(ssl_verify=verify_ssl)
    if token:
        http_client.set_api_key(
            urlparse(host).hostname, 'Bearer ' + token,
            param_name='Authorization', param_in='header'
        )
    elif username and password:
        http_client.set_basic_auth(urlparse(host).hostname, username, password)
    client = SwaggerClient.from_spec(schema.schema, http_client=http_client)
    client.swagger_spec.api_url = host
    client.swagger_spec.config['also_return_response'] = True
    return client


client = Client()
