import copy
import collections
import hashlib
import json
import logging
import os
import subprocess
import time
from contextlib import contextmanager

import pathspec
from .path import path

log = logging.getLogger('utils')


@contextmanager
def cd(directory, make=False):
    cwd = os.getcwd()
    if not os.path.exists(directory) and make:
        os.makedirs(directory)
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(cwd)


def deepmerge(dest, src):
    """
    Deep merge of two dicts.

    This is destructive (`dest` is modified), but values
    from `src` are passed through `copy.deepcopy`.
    """
    for k, v in src.iteritems():
        if dest.get(k) and isinstance(v, dict):
            deepmerge(dest[k], v)
        else:
            dest[k] = copy.deepcopy(v)
    return dest


def delete_path(path, obj):
    """Delete a dotted path from object, assuming each level is a dict"""
    parts = path.split('.')
    for p in parts[:-1]:
        obj = obj[p]
    del obj[parts[-1]]


class NestedDict(dict):
    def __init__(self, dict_or_iterable=None, **kwargs):
        if dict_or_iterable:
            if isinstance(dict_or_iterable, dict):
                self.update(dict_or_iterable)
            elif isinstance(dict_or_iterable, collections.Iterable):
                for k, v in dict_or_iterable:
                    self[k] = v
        if kwargs:
            self.update(kwargs)

    def __setitem__(self, key, value):
        key = key.split('.')
        o = self
        for part in key[:-1]:
            o = o.setdefault(part, self.__class__())
        dict.__setitem__(o, key[-1], value)

    def __getitem__(self, key):
        o = self
        if '.' in key:
            parts = key.split('.')
            key = parts[-1]
            for part in parts[:-1]:
                o = o[part]

        return dict.__getitem__(o, key)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def update(self, other):
        deepmerge(self, other)


class ProcessResult(object):
    def __init__(self, command, exit_code, stdout, stderr):
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    def __repr__(self):
        return '<ProcessResult "%s" result %s>' % (self.cmd, self.exit_code)

    @property
    def cmd(self):
        return ' '.join(self.command)

    @property
    def output(self):
        result = ''
        if self.stdout:
            result += self.stdout
        if self.stderr:
            result += self.stderr
        return result.strip()

    @property
    def json(self):
        if self.stdout:
            return json.loads(self.stdout)
        return None

    def __eq__(self, other):
        return self.exit_code == other

    def __bool__(self):
        return self.exit_code == 0

    __nonzero__ = __bool__

    def throw_on_error(self):
        if not bool(self):
            raise subprocess.CalledProcessError(
                self.exit_code, self.command, output=self.output)


class Process(object):
    def __init__(self, command=None, throw=False, log=log, **kwargs):
        if isinstance(command, str):
            command = (command, )
        self.command = command
        self._throw_on_error = False
        self.log = log
        self._kw = kwargs

    def __repr__(self):
        return "<Command %s>" % (self.command, )

    def throw_on_error(self, throw=True):
        self._throw_on_error = throw
        return self

    def __call__(self, *args, **kw):
        kwargs = dict(stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT)
        if self._kw:
            kwargs.update(self._kw)
        kwargs.update(kw)
        if self.command:
            all_args = self.command + args
        else:
            all_args = args
        if 'env' not in kwargs:
            kwargs['env'] = os.environ

        p = subprocess.Popen(all_args, **kwargs)
        stdout, stderr = p.communicate()
        self.log.debug(stdout)
        stdout = stdout.strip()
        if stderr is not None:
            stderr = stderr.strip()
            self.log.debug(stderr)
        exit_code = p.poll()
        result = ProcessResult(all_args, exit_code, stdout, stderr)
        self.log.debug("process: %s (%d)", result.cmd, result.exit_code)
        if self._throw_on_error:
            result.throw_on_error()
        return result

command = Process


class Commander(object):
    def __init__(self, log=log):
        self.log = log

    def set_log(self, logger):
        self.log = logger

    def __getattr__(self, key):
        return command((key,), log=self.log)

    def check(self, *args, **kwargs):
        kwargs.update({'log': self.log})
        return command(command=args, **kwargs).throw_on_error()

    def __call__(self, *args, **kwargs):
        kwargs.update({'log': self.log})
        return command(command=args, shell=True, **kwargs)


sh = Commander()
dig = Process(('dig', '+short'))
api_endpoints = Process(('juju', 'api-endpoints'))


def wait_for(timeout, interval, *callbacks, **kwargs):
    """
    Repeatedly try callbacks until all return True

    This will wait interval seconds between attempts and will error out
    after timeout has been exceeded.

    Callbacks will be called with the container as their argument.

    Setting timeout to zero will loop until cancelled, power runs outs,
    hardware fails, or the heat death of the universe.
    """
    start = time.time()
    if timeout:
        end = start + timeout
    else:
        end = 0

    bar = kwargs.get('bar', None)
    message = kwargs.get('message', None)
    once = 1
    while True:
        passes = True
        if end > 0 and time.time() > end:
            raise OSError("Timeout exceeded in wait_for")
        if bar:
            bar.next(once, message=message)
            if once == 1:
                once = 0
        if int(time.time()) % interval == 0:
            for callback in callbacks:
                result = callback()
                passes = passes & bool(result)
                if passes is False:
                    break
            if passes is True:
                break
        time.sleep(1)


def until(*callbacks, **kwargs):
    return wait_for(0, 20, *callbacks, **kwargs)


def retry(attempts, *callbacks, **kwargs):
    """
    Repeatedly try callbacks a fixed number of times or until all return True
    """
    for attempt in xrange(attempts):
        if 'bar' in kwargs:
            kwargs['bar'].next(attempt == 0, message=kwargs.get('message'))
        for callback in callbacks:
            if not callback():
                break
        else:
            break
    else:
        raise OSError("Retry attempts exceeded")
    return True


def which(program):
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for fpath in os.environ["PATH"].split(os.pathsep):
            fpath = fpath.strip('"')
            exe_file = os.path.join(fpath, program)
            if is_exe(exe_file):
                return exe_file
    return None


def load_class(dpath, workingdir=None):
    # we expect the last element of the path
    if not workingdir:
        workingdir = os.getcwd()
    with cd(workingdir):
        modpath, classname = dpath.rsplit('.', 1)
        modpath = path(modpath.replace(".", "/"))
        if not modpath.exists():
            modpath += ".py"
        if not modpath.exists():
            raise OSError("Unable to load {} from {}".format(
                dpath, workingdir))
        namespace = {}
        execfile(modpath, globals(), namespace)
        klass = namespace.get(classname)
        if klass is None:
            raise ImportError("Unable to load class {} at {}".format(
                classname, dpath))
        return klass


def walk(pathobj, fn, matcher=None, kind=None, **kwargs):
    """walk pathobj calling fn on each matched entry yielding each
    result. If kind is 'file' or 'dir' only that type ofd entry will
    be walked. matcher is an optional function returning bool indicating
    if the entry should be processed.
    """
    p = path(pathobj)
    walker = p.walk
    if kind == "files":
        walker = p.walkfiles
    elif kind == "dir":
        walker = p.walkdir

    for entry in walker():
        relpath = entry.relpath(pathobj)
        if matcher and not matcher(relpath):
            continue
        yield (entry, fn(entry, **kwargs))


def ignore_matcher(ignores=[]):
    spec = pathspec.PathSpec.from_lines(pathspec.GitIgnorePattern, ignores)

    def matcher(entity):
        return entity not in spec.match_files((entity,))
    return matcher


def sign(pathobj):
    p = path(pathobj)
    if not p.isfile():
        return None
    return hashlib.sha256(p.text()).hexdigest()


def delta_signatures(metadata_filename):
    md = path(metadata_filename)
    repo = md.normpath().dirname()

    baseline = json.load(md.open())
    current = {}
    for rel, sig in walk(repo, sign):
        rel = rel.relpath(repo)
        current[rel] = sig
    add, change, delete = set(), set(), set()

    for p, s in current.items():
        fp = repo / p
        if not fp.isfile():
            continue

        if p not in baseline:
            add.add(p)
            continue
        # layer, kind, sig
        # don't include items generated only for the last layer
        if baseline[p][0] == "composer":
            continue
        if baseline[p][2] != s:
            change.add(p)

    for p, d in baseline.items():
        if p not in current:
            delete.add(path(p))
    return add, change, delete
