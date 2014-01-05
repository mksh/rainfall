# -*- coding: utf-8 -*-
import re
import sys
import asyncio
import signal
import datetime
import traceback

from http import client
from jinja2 import Environment, FileSystemLoader

from .utils import TerminalColors, RainfallException
from .http import HTTPResponse, HTTPRequest, HTTPError


class HTTPHandler(object):
    """
    Used by HTTPServer to react for some url pattern.

    All handling is in handle method.
    """
    @asyncio.coroutine
    def handle(self, request, **kwargs):
        """
        May be coroutine or a regular function.

        :return str (may be rendered with self.render()) or HTTPError
        """
        raise NotImplementedError

    def render(self, template_name, **kwargs):
        template = HTTPServer._jinja_env.get_template(template_name)
        result = template.render(kwargs)
        return result

    @asyncio.coroutine
    def __call__(self, request, **kwargs):
        """
        Is called by HTTPServer.

        :return (code, body)
        """
        code = 200
        body = ''
        # this check is taken form asyncio sources
        if getattr(self.handle, '_is_coroutine', False):
            handler_result = yield from self.handle(request, **kwargs)
        else:
            handler_result = self.handle(request)

        if handler_result:
            if isinstance(handler_result, HTTPError):
                code = handler_result.code
            elif isinstance(handler_result, str):
                body = handler_result
            else:
                raise RainfallException("handle() result must be HTTPError or unicode, found {}".format(type(handler_result)))
        return (code, body)


class HTTPServer(asyncio.Protocol):
    """
    Http server itself, uses asyncio.Protocol.
    Not meant to be created manually, but by Application class.

    TIMEOUT sets disconnect timeout.
    """
    TIMEOUT = 5.0
    _handlers = {}
    _static_path = ''
    _jinja_env = ''

    def timeout(self):
        #print('connection timeout, closing.')
        self.transport.close()

    def connection_made(self, transport):
        self.transport = transport

        # start 5 seconds timeout timer
        self.h_timeout = asyncio.get_event_loop().call_later(
            self.TIMEOUT, self.timeout
        )

    def data_received(self, data):
        decoded_data = data.decode()
        request = HTTPRequest(decoded_data)
        task = asyncio.Task(self._call_handler(request))
        task.add_done_callback(self._finalize_request)

    def _finalize_request(self, *args, **kwargs):
        self.transport.close()
        self.h_timeout.cancel()

    @asyncio.coroutine
    def _call_handler(self, request):
        response = None
        path = request.path.split('?')[0]  # stripping GET params
        exc = None

        response = HTTPResponse()
        for pattern, handler in self._handlers.items():
            result = re.match(pattern, path)
            if result:
                try:
                    code, body = yield from handler(request, **result.groupdict())
                    response.code = code
                    response.body = body
                except Exception as e:
                    response.code = client.INTERNAL_SERVER_ERROR
                    exc = sys.exc_info()
                finally:
                    break
        else:
            response.code=client.NOT_FOUND

        if response.code != 200:
            response.body = "<h1>{} {}</h1>".format(response.code, client.responses[response.code])

        self.transport.write(response.compose().encode())
        print(datetime.datetime.now(), request.method, request.path, response.code)

        if exc:
            traceback.print_exception(*exc)

    def connection_lost(self, exc):
        self.h_timeout.cancel()


class Application(object):
    """
    The core class that is used to create and start server.

    Example:
        app = Application({
            '/': HelloHandler(),
        })
        app.run()

    """
    def __init__(self, handlers, settings={}):
        self._handlers = handlers
        self._settings = settings
        HTTPServer._jinja_env = Environment(loader=FileSystemLoader(settings.get('template_path', '')))
        HTTPServer._handlers = handlers

    def run(self, host='127.0.0.1', port='8888'):
        """
        Starts server on given host and port,
        adds Ctrl-C signal handler.
        """
        loop = asyncio.get_event_loop()
        if signal is not None:
            loop.add_signal_handler(signal.SIGINT, loop.stop)

        self._start_server(loop, host, port)

        loop.run_forever()

    def _start_server(self, loop, host, port):
        f = loop.create_server(HTTPServer, host, port)
        s = loop.run_until_complete(f)
        self._greet(s.sockets[0].getsockname())

    def _greet(self, sock_name):
        print(
            TerminalColors.LIGHTBLUE, '\nRainfall is starting...',TerminalColors.WHITE,
            '\n\u2744 ', '\u2744  '*7,
            TerminalColors.NORMAL, '\nServing on', sock_name, ':'
        )