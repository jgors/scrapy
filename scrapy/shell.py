"""Scrapy Shell

See documentation in docs/topics/shell.rst

"""
from __future__ import print_function

import signal
import warnings

from twisted.internet import reactor, threads, defer
from twisted.python import threadable
from w3lib.url import any_to_uri

from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest, ScrapyDeprecationWarning
from scrapy.http import Request, Response
from scrapy.item import BaseItem
from scrapy.settings import Settings
from scrapy.spiders import Spider
from scrapy.utils.console import start_python_console
from scrapy.utils.misc import load_object
from scrapy.utils.response import open_in_browser


class Shell(object):

    relevant_classes = (Crawler, Spider, Request, Response, BaseItem,
                        Settings)

    def __init__(self, crawler, update_vars=None, code=None):
        self.crawler = crawler
        self.update_vars = update_vars or (lambda x: None)
        self.item_class = load_object(crawler.settings['DEFAULT_ITEM_CLASS'])
        self.spider = None
        self.inthread = not threadable.isInIOThread()
        self.code = code
        self.vars = {}

    def start(self, url=None, request=None, response=None, spider=None):
        # disable accidental Ctrl-C key press from shutting down the engine
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if url:
            self.fetch(url, spider)
        elif request:
            self.fetch(request, spider)
        elif response:
            request = response.request
            self.populate_vars(response, request, spider)
        else:
            self.populate_vars()
        if self.code:
            print(eval(self.code, globals(), self.vars))
        else:
            start_python_console(self.vars)

    def _schedule(self, request, spider):
        spider = self._open_spider(request, spider)
        d = _request_deferred(request)
        d.addCallback(lambda x: (x, spider))
        self.crawler.engine.crawl(request, spider)
        return d

    def _open_spider(self, request, spider):
        if self.spider:
            return self.spider

        if spider is None:
            spider = self.crawler.spider or self.crawler._create_spider()

        self.crawler.spider = spider
        self.crawler.engine.open_spider(spider, close_if_idle=False)
        self.spider = spider
        return spider

    def fetch(self, request_or_url, spider=None):
        if isinstance(request_or_url, Request):
            request = request_or_url
            url = request.url
        else:
            url = any_to_uri(request_or_url)
            request = Request(url, dont_filter=True)
            request.meta['handle_httpstatus_all'] = True
        response = None
        try:
            response, spider = threads.blockingCallFromThread(
                reactor, self._schedule, request, spider)
        except IgnoreRequest:
            pass
        self.populate_vars(response, request, spider)

    def populate_vars(self, response=None, request=None, spider=None):
        self.vars['crawler'] = self.crawler
        self.vars['item'] = self.item_class()
        self.vars['settings'] = self.crawler.settings
        self.vars['spider'] = spider
        self.vars['request'] = request
        self.vars['response'] = response
        self.vars['sel'] = _SelectorProxy(response)
        if self.inthread:
            self.vars['fetch'] = self.fetch
        self.vars['view'] = open_in_browser
        self.vars['shelp'] = self.print_help
        self.update_vars(self.vars)
        if not self.code:
            self.print_help()

    def print_help(self):
        self.p("Available Scrapy objects:")
        for k, v in sorted(self.vars.items()):
            if self._is_relevant(v):
                self.p("  %-10s %s" % (k, v))
        self.p("Useful shortcuts:")
        self.p("  shelp()           Shell help (print this help)")
        if self.inthread:
            self.p("  fetch(req_or_url) Fetch request (or URL) and update local objects")
        self.p("  view(response)    View response in a browser")

    def p(self, line=''):
        print("[s] %s" % line)

    def _is_relevant(self, value):
        return isinstance(value, self.relevant_classes)


def inspect_response(response, spider):
    """Open a shell to inspect the given response"""
    Shell(spider.crawler).start(response=response)


def _request_deferred(request):
    """Wrap a request inside a Deferred.

    This function is harmful, do not use it until you know what you are doing.

    This returns a Deferred whose first pair of callbacks are the request
    callback and errback. The Deferred also triggers when the request
    callback/errback is executed (ie. when the request is downloaded)

    WARNING: Do not call request.replace() until after the deferred is called.
    """
    request_callback = request.callback
    request_errback = request.errback
    def _restore_callbacks(result):
        request.callback = request_callback
        request.errback = request_errback
        return result

    d = defer.Deferred()
    d.addBoth(_restore_callbacks)
    if request.callback:
        d.addCallbacks(request.callback, request.errback)

    request.callback, request.errback = d.callback, d.errback
    return d


class _SelectorProxy(object):

    def __init__(self, response):
        self._proxiedresponse = response

    def __getattr__(self, name):
        warnings.warn('"sel" shortcut is deprecated. Use "response.xpath()", '
                      '"response.css()" or "response.selector" instead',
                      category=ScrapyDeprecationWarning, stacklevel=2)
        return getattr(self._proxiedresponse.selector, name)
