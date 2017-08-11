from logging import getLogger

log = getLogger('crawler')


def catching(func):
    def wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            log.exception('Exception in %s: %s' % (func.func_name, e))
    return wrapped


