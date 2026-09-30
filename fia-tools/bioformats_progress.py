"""Temporarily route Bio-Formats Logback events to a compact Python display."""

import logging
from contextlib import contextmanager


class _Appender:
    """Implement the Logback Appender interface without changing Java streams."""

    def __init__(self, progress, jclass):
        self.progress = progress
        self.name = 'FIACompactProgress'
        self.started = True
        self.context = None
        self.jclass = jclass

    def doAppend(self, event):
        severity = event.getLevel().toInt()
        level = logging.ERROR if severity >= 40000 else logging.WARNING if severity >= 30000 else logging.INFO
        message = str(event.getFormattedMessage())
        throwable = event.getThrowableProxy()
        if throwable is not None:
            message += '\n' + str(self.jclass('ch.qos.logback.classic.spi.ThrowableProxyUtil').asString(throwable))
        self.progress.bioformats_event(message, level)

    def getName(self):
        return self.name

    def setName(self, name):
        self.name = name

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def isStarted(self):
        return self.started

    def getContext(self):
        return self.context

    def setContext(self, context):
        self.context = context

    def addStatus(self, *args):
        pass

    def addInfo(self, *args):
        pass

    def addWarn(self, *args):
        pass

    def addError(self, *args):
        pass

    def addFilter(self, *args):
        pass

    def clearAllFilters(self):
        pass

    def getCopyOfAttachedFiltersList(self):
        return self.jclass('java.util.ArrayList')()

    def getFilterChainDecision(self, event):
        return self.jclass('ch.qos.logback.core.spi.FilterReply').NEUTRAL


@contextmanager
def bioformats_progress(progress):
    """Restore exact logger settings on success, error or cancellation."""
    if progress is None:
        yield
        return
    logger = proxy = None
    old_appenders = []
    configured = False
    try:
        import jpype
        if jpype.isJVMStarted():
            logger = jpype.JClass('org.slf4j.LoggerFactory').getLogger('loci.formats')
            if not isinstance(logger, jpype.JClass('ch.qos.logback.classic.Logger')):
                raise TypeError('Bio-Formats is not using Logback')
            old_level, old_additive = logger.getLevel(), logger.isAdditive()
            old_appenders = list(logger.iteratorForAppenders())
            adapter = _Appender(progress, jpype.JClass)
            proxy = jpype.JProxy('ch.qos.logback.core.Appender', inst=adapter)
            configured = True
            for appender in old_appenders:
                logger.detachAppender(appender)
            logger.addAppender(proxy)
            logger.setAdditive(False)
            logger.setLevel(jpype.JClass('ch.qos.logback.classic.Level').INFO)
    except Exception as error:
        # Display setup must not prevent scientific processing or hide diagnostics.
        if configured:
            logger.detachAppender(proxy)
            logger.setLevel(old_level)
            logger.setAdditive(old_additive)
            for appender in old_appenders:
                logger.addAppender(appender)
            configured = False
        if not getattr(progress, '_bioformats_warning', False):
            progress.message(f'WARNING: Compact Bio-Formats logging unavailable; retaining normal output: {error}')
            progress._bioformats_warning = True
    try:
        yield
    finally:
        if configured:
            logger.detachAppender(proxy)
            logger.setLevel(old_level)
            logger.setAdditive(old_additive)
            for appender in old_appenders:
                logger.addAppender(appender)
