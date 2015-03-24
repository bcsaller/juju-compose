#
# Copyright 2012 Canonical Ltd.
#
# This file is sourced from lp:openstack-charm-helpers
#
# Authors:
#  James Page <james.page@ubuntu.com>
#  Paul Collins <paul.collins@canonical.com>
#  Adam Gandelman <adamg@ubuntu.com>
#

import json
import os
import subprocess
import socket
import sys
from charmhelpers.core.host import (
    lsb_release
)


def do_hooks(hooks):
    hook = os.path.basename(sys.argv[0])

    try:
        hook_func = hooks[hook]
    except KeyError:
        juju_log('INFO',
                 "This charm doesn't know how to handle '{}'.".format(hook))
    else:
        hook_func()


def install(*pkgs):
    cmd = [
        'apt-get',
        '-y',
        'install']
    for pkg in pkgs:
        cmd.append(pkg)
    subprocess.check_call(cmd)

try:
    import dns.resolver
except ImportError:
    install('python-dnspython')
    import dns.resolver

# Protocols
TCP = 'TCP'
UDP = 'UDP'


def expose(port, protocol='TCP'):
    cmd = [
        'open-port',
        '{}/{}'.format(port, protocol)]
    subprocess.check_call(cmd)


def juju_log(severity, message):
    cmd = [
        'juju-log',
        '--log-level', severity,
        message]
    subprocess.check_call(cmd)


def relation_ids(relation):
    cmd = [
        'relation-ids',
        relation]
    result = str(subprocess.check_output(cmd)).split()
    if result == "":
        return None
    else:
        return result


def relation_list(rid):
    cmd = [
        'relation-list',
        '-r', rid]
    result = str(subprocess.check_output(cmd)).split()
    if result == "":
        return None
    else:
        return result


def relation_get(attribute, unit=None, rid=None):
    cmd = [
        'relation-get']
    if rid:
        cmd.append('-r')
        cmd.append(rid)
    cmd.append(attribute)
    if unit:
        cmd.append(unit)
    value = subprocess.check_output(cmd).strip()  # IGNORE:E1103
    if value == "":
        return None
    else:
        return value


def relation_set(**kwargs):
    cmd = [
        'relation-set']
    args = []
    for k, v in kwargs.items():
        if k == 'rid':
            if v:
                cmd.append('-r')
                cmd.append(v)
        else:
            args.append('{}={}'.format(k, v))
    cmd += args
    subprocess.check_call(cmd)


def unit_get(attribute):
    cmd = [
        'unit-get',
        attribute]
    value = subprocess.check_output(cmd).strip()  # IGNORE:E1103
    if value == "":
        return None
    else:
        return value


def config_get(attribute):
    cmd = [
        'config-get',
        '--format',
        'json']
    out = subprocess.check_output(cmd).strip()  # IGNORE:E1103
    cfg = json.loads(out)

    try:
        return cfg[attribute]
    except KeyError:
        return None


def get_unit_hostname():
    return socket.gethostname()


def get_host_ip(hostname=unit_get('private-address')):
    try:
        # Test to see if already an IPv4 address
        socket.inet_aton(hostname)
        return hostname
    except socket.error:
        answers = dns.resolver.query(hostname, 'A')
        if answers:
            return answers[0].address
    return None


def _svc_control(service, action):
    subprocess.check_call(['service', service, action])


def restart(*services):
    for service in services:
        _svc_control(service, 'restart')


def stop(*services):
    for service in services:
        _svc_control(service, 'stop')


def start(*services):
    for service in services:
        _svc_control(service, 'start')


def reload(*services):
    for service in services:
        try:
            _svc_control(service, 'reload')
        except subprocess.CalledProcessError:
            # Reload failed - either service does not support reload
            # or it was not running - restart will fixup most things
            _svc_control(service, 'restart')


def running(service):
    try:
        output = subprocess.check_output(['service', service, 'status'])
    except subprocess.CalledProcessError:
        return False
    else:
        if ("start/running" in output or "is running" in output):
            return True
        else:
            return False


def is_relation_made(relation, key='private-address'):
    for r_id in (relation_ids(relation) or []):
        for unit in (relation_list(r_id) or []):
            if relation_get(key, rid=r_id, unit=unit):
                return True
    return False


def check_ipv6_compatibility():
    if lsb_release()['DISTRIB_CODENAME'].lower() < "trusty":
        raise Exception("IPv6 is not supported in charms for Ubuntu "
                        "versions less than Trusty 14.04")
