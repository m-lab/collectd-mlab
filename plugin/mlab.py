# Copyright 2014 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
The mlab, collectd-python plugin for VServer and M-Lab experiment monitoring.

Regard this file as a plugin rather than a standard python module. It is a
plugin because it should only execute within the collectd runtime environment.

This plugin measures VServer resource utilization and other system metrics.
The plugin uses the collectd, python API to 'dispatch' measurements to collectd
for output to all configured write plugins (e.g. RRD).

To configure collectd to load this plugin, use the example stanza below as a
template.

The mlab plugin supports one configuration option: ExcludeSlice. The directive
can be specified multiple times. No metrics will be reported for excluded
slices.

Example /etc/collectd.conf config (or /etc/collectd-mlab.conf) for mlab python
plugin:

  <Plugin python>
    # 'mlab.py' should be in the ModulePath directory.
    ModulePath "/var/lib/collectd/python"
    # Report python exceptions to syslog.
    LogTraces true
    Interactive false

    # 'mlab' is the name of this file.
    Import "mlab"
    <Module mlab>
      ExcludeSlice "some_slicename1"
      ExcludeSlice "some_slicename2"
    </Module>
  </Plugin>

Dependencies:
 - Read access to /proc/virtual/*
 - Access to Vsys frontend in /vsys/vs_resource_backend* (see PlanetLab
   configuration for vsys.)
"""

import json
import os
import select
import socket
import stat
import time

# This import is part of collectd runtime, and not a real python module.
# F0401 means 'cannot import module'.
try:
    import collectd  # pylint: disable=F0401
    should_register_plugin = True
except ImportError:
    # Since collectd is not a real python module, catching ImportError allows
    # pydoc to run. Setting should_register_plugin=False prevents the module from
    # trying to register callback methods (e.g. configure, read) with collectd.
    should_register_plugin = False

# Constants.
# "Number of processors online"
_CPU_COUNT = os.sysconf(os.sysconf_names['SC_NPROCESSORS_ONLN'])
_PAGESIZE = os.sysconf(os.sysconf_names['SC_PAGESIZE'])
_USER_HZ = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
_CONFIG_HZ = 1000.0  # NOTE: This must match the kernel config value.
_SCALE_HZ = _CONFIG_HZ / _USER_HZ
_PLUGIN_PATH = '/var/lib/collectd/python'
_POLL_TIMEOUT_SEC = 10
_PROC_PID_STAT = None
_PROC_STAT = '/proc/stat'
_PROC_UPTIME = '/proc/uptime'
_PROC_VIRTUAL = '/proc/virtual'
_READ_BLOCK_SIZE = 1024

# VSys constants.
_VSYS_FMT_IN = '/vsys/%s.in'
_VSYS_FMT_OUT = '/vsys/%s.out'
_VSYS_FRONTEND_TARGET = 'vs_resource_backend'
_VSYS_FRONTEND_VERSION = 1

# Global variables.
# NOTE: This should be the hostname of root context, not slice context.
_root_hostname = socket.gethostname()
_config_exclude_slices = {}
_vs_vsys = None
_vs_xid_names = {}


def submit_generic(host,
                   plugin,
                   typename,
                   values,
                   type_instance=None,
                   plugin_instance=None):
    """Reports a new value to collectd.

  Every value is associated with a unique combination of host, plugin,
  typename, and instance parameters.

  For complete notes on the collectd.Values interface, see:
  http://collectd.org/documentation/manpages/collectd-python.5.shtml#plugindata

  Ultimately, the RRD output plugin uses these parameters to create RRD files
  under the RRD directory tree like this:

    <host>/<plugin>[-<plugin_instance>]/<typename>[-<type_instance>].rrd

  Depending on the value's typename, there may be multiple time series in one
  RRD file; interface/if_octets-eth0.rrd is an example with multiple time
  series (for 'rx' and 'tx').

  For more documentation on value types (defined in types.db), see:
    http://collectd.org/documentation/manpages/types.db.5.shtml

  M-Lab adds custom types defined in /usr/share/collectd-mlab/types.db.

  Args:
    host: str, the hostname associated with this value.
    plugin: str, the group in which this value belongs, e.g. 'memory', 'disk'.
    typename: str, the name of a type defined in types.db.
    values: list, int, or float: the actual measurements to record.
    type_instance: str or None, use to specify an instance name when multiple
        objects have the same typename. e.g. 'ipv4', 'ipv6' network usage.
    plugin_instance: str or None, use to specify an instance name when multiple
        plugins need the same set of types. e.g. the meta metrics for 'collectd'
        and 'backend'.
  """
    metric = collectd.Values()
    metric.host = host
    metric.plugin = plugin
    metric.type = typename
    if plugin_instance:
        metric.plugin_instance = plugin_instance
    if type_instance:
        metric.type_instance = type_instance
    if type(values) == list:
        metric.values = values
    elif type(values) in [int, float]:
        metric.values = [values]
    else:
        collectd.error('Unsupported values type. %s' % type(values))
        return
    metric.dispatch()


def submit_cpucores():
    """Reports number of CPU cores to collectd."""
    # TODO(soltesz): move static value to an external, inventory table.
    submit_generic(_root_hostname, 'cpu_cores', 'gauge', _CPU_COUNT)


def submit_cputotal(type_instance, value):
    """Reports total CPU usage to collectd."""
    submit_generic(_root_hostname, 'cpu_total', 'cpu', value / 1.0,
                   type_instance)


def submit_meta_timer(type_instance, value):
    """Reports meta-metric timer to collectd."""
    submit_generic(_root_hostname, 'meta', 'timer', value, type_instance)


def submit_meta_memory(plugin_instance, type_instance, value):
    """Reports meta-metric memory usage to collectd."""
    submit_generic(_root_hostname, 'meta', 'process_memory', value,
                   type_instance, plugin_instance)


def submit_meta_cpu(plugin_instance, type_instance, value):
    """Reports meta-metric process cpu usage to collectd."""
    submit_generic(_root_hostname, 'meta', 'process_cpu', value, type_instance,
                   plugin_instance)


def submit_vserver_cputotal(vs_host, type_instance, value):
    """Reports vserver total CPU usage to collectd."""
    # NOTE: VServer scheduling counters are measured in scheduler ticks,
    # e.g. CONFIG_HZ (1/1000th of sec). Most Linux system counters use USER_HZ
    # (1/100th of sec). Values in USER_HZ units approximate a percentage. Here
    # the raw VServer value is divided by _SCALE_HZ to convert units to 1/100th
    # sec, so VServer cpu usage will be comparable to system cpu values.
    submit_generic(vs_host, 'cpu_total', 'vs_cpu', value / _SCALE_HZ,
                   type_instance)


def submit_vserver_network_bytes(vs_host, type_instance, values):
    """Reports vserver network bytes to collectd."""
    submit_generic(vs_host, 'network', 'if_octets', values, type_instance)


def submit_vserver_network_syscalls(vs_host, type_instance, values):
    """Reports vserver network system calls to collectd."""
    submit_generic(vs_host, 'network', 'vs_network_syscalls', values,
                   type_instance)


def submit_vserver_memory(vs_host, type_instance, value):
    """Reports vserver memory usage to collectd."""
    submit_generic(vs_host, 'memory', 'vs_memory', value, type_instance)


def submit_vserver_quota(vs_host, type_instance, values):
    """Reports vserver storage quota usage to collectd."""
    submit_generic(vs_host, 'storage', 'vs_quota_bytes', values, type_instance)


def submit_vserver_uptime(vs_host, value):
    """Reports vserver uptime to collectd."""
    submit_generic(vs_host, 'context', 'vs_uptime', value)


def submit_vserver_limit(vs_host, type_instance, value):
    """Reports vserver vlimit metrics to collectd."""
    submit_generic(vs_host, 'context', 'vs_vlimit', value, type_instance)


def submit_vserver_threads(vs_host, type_instance, value):
    """Reports vserver thread metrics to collectd."""
    submit_generic(vs_host, 'context', 'vs_threads_basic', value, type_instance)


def meta_timer(type_instance):
    """Decorates a function to record its run time as a collectd metric.

  Every time the decorated function is called, its run time is reported to
  collectd as a meta timer metric. Any number of functions can be decorated,
  but two conditions should be met: 1) each decoration uses a distinct
  type_instance name, 2) the decorated function is called exactly once per
  sample period. This guarantees that no measurements are lost and that a
  measurement occurs every sample period.

  Example usage:
    @meta_timer('read')
    def my_read_method():
      pass

  Args:
    type_instance: str, the name of the meta timer type.
  Returns:
    callable, a function decorator.
  """

    def meta_timer_decorator(func):
        """Returns wrapper method around given function."""

        def wrapper(*args, **kwargs):
            """Calls function and reports execution time as a meta-metric."""
            t_start = time.time()
            val = func(*args, **kwargs)
            t_end = time.time()
            submit_meta_timer(type_instance, t_end - t_start)
            return val

        return wrapper

    return meta_timer_decorator


def report_meta_metrics(stat_path):
    """Reports meta metrics associated with vsys backend and collectd itself.

  Args:
    stat_path: str, path to proc/<pid>/stat file.
  """
    collectd_stats = get_self_stats(stat_path)
    backend_stats = read_vsys_data('backend_stats', _VSYS_FRONTEND_VERSION)
    submit_meta('collectd', collectd_stats)
    submit_meta('backend', backend_stats)


def submit_meta(plugin_instance, stats):
    """Reports "meta" stats associated with plugin_instance to collectd.

  Args:
    plugin_instance: str, name of plugin_instance to assiciate stats with.
    stats: dict, should include keys for 'vsize', 'rss', 'utime', and 'stime'.
  """
    if 'vsize' in stats:
        submit_meta_memory(plugin_instance, 'vm', stats['vsize'])
    if 'rss' in stats:
        submit_meta_memory(plugin_instance, 'rss', stats['rss'])
    if 'utime' in stats:
        submit_meta_cpu(plugin_instance, 'user', stats['utime'])
    if 'stime' in stats:
        submit_meta_cpu(plugin_instance, 'system', stats['stime'])


def get_self_stats(stat_path):
    """Parses stat file ane returns dict containing metrics of interest.

  Args:
    stat_path: str, path to proc/<pid>/stat file.
  Returns:
    dict, with 'utime', 'stime', 'vsize', 'rss' key/values from stat file.
  """
    index_utime = 13
    index_stime = 14
    index_cutime = 15
    index_cstime = 16
    index_vsize = 22
    index_rss = 23
    self_stats = {'utime': 0, 'stime': 0, 'vsize': 0, 'rss': 0}

    if not os.path.exists(stat_path):
        collectd.error('mlab: get_self_stats stat path does not exist: %s' %
                       stat_path)
        return {}

    with open(stat_path, 'r') as stat_file:
        stat_fields = stat_file.read().strip().split()

    if len(stat_fields) < 24:
        collectd.error('mlab: get_self_stats found only %s fields.' %
                       len(stat_fields))
        return {}

    self_stats['utime'] = (
        float(stat_fields[index_utime]) + float(stat_fields[index_cutime]))
    self_stats['stime'] = (
        float(stat_fields[index_stime]) + float(stat_fields[index_cstime]))
    self_stats['vsize'] = int(stat_fields[index_vsize])
    self_stats['rss'] = int(stat_fields[index_rss]) * _PAGESIZE
    return self_stats


def report_quota_for_vserver(vs_host, dlimits):
    """Reports disk quota usage for vs_host.

  Args:
    vs_host: str, hostname of vserver context.
    dlimits: list, dlimit values as returned from get_dlimits.
  """
    quota_free = 1000 * (dlimits[1] - dlimits[0])
    quota_used = 1000 * dlimits[0]
    submit_vserver_quota(vs_host, 'quota', [quota_used, quota_free])


def read_system_uptime():
    """Parses and returns system uptime as float in seconds."""
    if os.path.exists(_PROC_UPTIME):
        with open(_PROC_UPTIME) as proc_uptime:
            uptime_fields = proc_uptime.read().split()
            return float(uptime_fields[0])
    collectd.error('read_system_uptime: %s does not exist.' % _PROC_UPTIME)
    return 0


def report_threads_for_vserver(vs_host, vs_directory, sys_uptime):
    """Reports thread and uptime metrics for vs_host.

  Args:
    vs_host: str, hostname of vserver context.
    vs_directory: str, path to vserver directory containing 'cvirt' stats.
    sys_uptime: float, current uptime of whole system in seconds. Used to
        calculate uptime of vserver.
  """
    cvirt_path = os.path.join(vs_directory, 'cvirt')
    with open(cvirt_path, 'r') as cvirt:
        vm_threads = None
        vm_running = None
        vm_bias = None
        for line in cvirt:
            fields = line.strip().split()
            # NOTE: nr_uninterruptible is deprecated.
            # NOTE: nr_onhold is never updated by vserver (always zero).
            if fields[0] == 'nr_threads:':
                vm_threads = int(fields[1])
            elif fields[0] == 'nr_running:':
                vm_running = int(fields[1])
            elif fields[0] == 'BiasUptime:':
                vm_bias = float(fields[1])

        # Context uptime := (System uptime - BiasUptime)
        if vm_bias is not None:
            submit_vserver_uptime(vs_host, sys_uptime - vm_bias)
        if vm_running is not None and vm_threads is not None:
            submit_vserver_threads(vs_host, 'running', vm_running)
            submit_vserver_threads(vs_host, 'other', (vm_threads - vm_running))


def report_limits_for_vserver(vs_host, vs_directory):
    """Reports system and memory metrics for vs_host.

  Args:
    vs_host: str, hostname of vserver context.
    vs_directory: str, path to vserver directory containing 'limit' stats.
  """
    vm_prefix_map = {'VM:': 'vm', 'VML:': 'vml', 'RSS:': 'rss', 'ANON:': 'anon'}
    sys_prefix_map = {'PROC:': 'processes',
                      'FILES:': 'files',
                      'OFD:': 'openfd',
                      'LOCKS:': 'locks',
                      'SOCK:': 'sockets'}

    limit_path = os.path.join(vs_directory, 'limit')
    with open(limit_path, 'r') as limit:
        _ = limit.readline()  # Discard header.

        for line in limit:
            vm_value = None
            sys_value = None
            type_instance = 'unknown'

            fields = line.strip().split()
            if fields[0] in sys_prefix_map:
                sys_value = float(fields[1])
                type_instance = sys_prefix_map[fields[0]]
                submit_vserver_limit(vs_host, type_instance, sys_value)

            elif fields[0] in vm_prefix_map:
                vm_value = float(fields[1]) * _PAGESIZE
                type_instance = vm_prefix_map[fields[0]]
                submit_vserver_memory(vs_host, type_instance, vm_value)


def split_network_line(line):
    """Parses line of /proc/virtual/<xid>/cacct for network usage.

  The cacct file has a header followed by usage counts for multiple protocols.

  Header (provided for context):
    Type      recv #/bytes       send #/bytes       fail #/bytes

  Each protocol's usage counts are formatted like this example:
    INET:         32/9909               43/253077              0/0

  Args:
    line: str, a line of text from cacct file.
  Returns:
    4-tuple of int: representing
        ('recv' syscalls, received octets, 'send' syscalls, sent octets).
  """
    fields = line.strip().split()
    receive_field = fields[1]
    transmit_field = fields[2]
    (recv_calls, rx_octets) = receive_field.split('/')
    (send_calls, tx_octets) = transmit_field.split('/')
    return (int(recv_calls), int(rx_octets), int(send_calls), int(tx_octets))


def report_network_for_vserver(vs_host, vs_directory):
    """Report network usage metrics for vs_host.

  Args:
    vs_host: str, hostname of vserver context.
    vs_directory: str, path to vserver directory containing 'cacct' stats.
  """
    cacct_path = os.path.join(vs_directory, 'cacct')
    with open(cacct_path, 'r') as cacct:
        _ = cacct.readline()  # Discard header.

        for line in cacct:
            if not (line.startswith('INET:') or line.startswith('INET6:') or
                    line.startswith('UNIX:')):
                continue
            (recv_calls, rx_octets, send_calls,
             tx_octets) = split_network_line(line)
            if line.startswith('INET:'):
                submit_vserver_network_bytes(vs_host, 'ipv4', [rx_octets,
                                                               tx_octets])
                submit_vserver_network_syscalls(vs_host, 'ipv4',
                                                [recv_calls, send_calls])
            elif line.startswith('INET6:'):
                submit_vserver_network_bytes(vs_host, 'ipv6', [rx_octets,
                                                               tx_octets])
                submit_vserver_network_syscalls(vs_host, 'ipv6',
                                                [recv_calls, send_calls])
            elif line.startswith('UNIX:'):
                submit_vserver_network_bytes(vs_host, 'unix', [rx_octets,
                                                               tx_octets])


def report_cpuavg_for_system(stat_path):
    """Reports whole-system, average cpu usage.

  Args:
    stat_path: str, path to filename with /proc/stat contents.
  """
    if not os.path.exists(stat_path):
        collectd.error('stat path does not exist: %s' % stat_path)
        return

    with open(stat_path, 'r') as stat_file:
        lines = [line for line in stat_file if line.startswith('cpu ')]
        if len(lines) == 1:  # There can be only one [cpu avg].
            fields = lines[0].strip().split()
            if len(fields) >= 9:
                submit_cputotal('user', int(fields[1]))
                submit_cputotal('nice', int(fields[2]))
                submit_cputotal('system', int(fields[3]))
                submit_cputotal('idle', int(fields[4]))
                submit_cputotal('wait', int(fields[5]))
                submit_cputotal('interrupt', int(fields[6]))
                submit_cputotal('softirq', int(fields[7]))
                submit_cputotal('steal', int(fields[8]))
            else:
                collectd.warning('Found too few fields (%s) in stat file: %s' %
                                 (len(fields), stat_path))

    submit_cpucores()


def report_cpu_for_vserver(vs_host, vs_directory):
    """Reports cpu usage for vs_host.

  Args:
    vs_host: str, hostname of vserver context.
    vs_directory: str, path to vserver directory containing 'sched' stats.
  """
    total = {'user': 0, 'system': 0, 'onhold': 0}
    sched_path = os.path.join(vs_directory, 'sched')

    with open(sched_path, 'r') as sched:
        _ = sched.readline()  # Discard header.

        for line in sched:
            if line.startswith('cpu'):
                fields = line.split()
                total['user'] += int(fields[2])
                total['system'] += int(fields[3])
                total['onhold'] += int(fields[4])

    submit_vserver_cputotal(vs_host, 'user', total['user'])
    submit_vserver_cputotal(vs_host, 'system', total['system'])
    # A context is 'onhold' if it uses all its scheduling tokens.
    # On a normal system, 'onhold' is expected to be zero.
    submit_vserver_cputotal(vs_host, 'onhold', total['onhold'])


def init_vserver_xid_names():
    """Initializes global _vs_xid_names to map vserver xids to slice names."""
    global _vs_xid_names
    collectd.info('mlab: requesting vs_xid_names.')
    _vs_xid_names = read_vsys_data('vs_xid_names', _VSYS_FRONTEND_VERSION)


def read_vsys_data(command, version):
    """Runs vsys 'command' and returns results as dict.

  See command notes for description of returned data format.

  Args:
    command: str, name of script or command to execute in vsys backend.
    version: int, expected version of backend response.
  Returns:
    dict, results of 'command'.
  """
    # Send request through vsys (for slice context).
    data = read_vsys_data_direct(command)

    if 'data' not in data:
        collectd.error('%s: returned value has no "data" field.' % command)
        return {}

    if 'version' not in data:
        collectd.error('%s: returned value has no "version" field.' % command)
        return {}

    if 'message_type' in data and data['message_type'] != command:
        collectd.error('Returned message_type does not match request.')
        collectd.error('Requested: %s' % command)
        collectd.error('Received : %s' % data['message_type'])
        return {}

    if data['version'] != version:
        msg = '%s: version mismatch: found (%d), expected (%d)' % (
            command, data['version'], version)
        collectd.warning(msg)

    return data['data']


def read_vsys_data_direct(command):
    """Runs command through vsys backend and returns result as dict.

  Args:
    command: str, name of vsys backend command to run.
  Returns:
    dict, result of command.
  """
    global _vs_vsys
    if _vs_vsys is None:
        try:
            _vs_vsys = VsysFrontend(_VSYS_FRONTEND_TARGET)
            _vs_vsys.open()
        except VsysException as err:
            collectd.error('Failed to setup VsysFrontend: %s' % err)
            return {}

    try:
        raw_data = _vs_vsys.sendrecv(command)
    except VsysException as err:
        collectd.error('Failed to receive message: %s' % err)
        _vs_vsys.close()
        _vs_vsys = None  # Will be re-opened on next call.
        return {}

    try:
        data = json.loads(raw_data)
    except ValueError as err:
        collectd.error('Failed to load json from raw data: -%s-' % raw_data)
        collectd.error(str(err))
        return {}

    return data


def vsys_fifo_exists(path):
    """Checks whether the path name exists and is a FIFO."""
    if not os.path.exists(path):
        collectd.error('File does not exist: %s' % path)
        return False
    if not stat.S_ISFIFO(os.stat(path).st_mode):
        collectd.error('File is not a fifo: %s' % path)
        return False
    return True


class Error(Exception):
    """Base class for exceptions in this plugin."""
    pass


class VsysException(Error):
    """This exception is raised when an error occurs during run-time."""


class VsysCreateException(VsysException):
    """This exception is raised when an error occurs during setup."""


class VsysOpenException(VsysException):
    """This exception is raised when an error occurs during open."""


def get_vsys_fifo_names(backend):
    """Returns a tuple with the vsys (backend.in, backend.out) filenames."""
    return (_VSYS_FMT_IN % backend, _VSYS_FMT_OUT % backend)


class VsysFrontend(object):
    """VsysFrontend manages interaction with a PlanetLab Vsys backend.

  An overview of Vsys and this class follow. But, please, also see the official
  docs for Vsys for more information: http://www.sapanbhatia.org/vsys/docs/

  # Vsys Overview

  Processes in a slice context can execute and communicate with whitelisted
  processes in the host context using Vsys. The process in the slice context is
  the Vsys "frontend", and the process in the host context is the Vsys
  "backend." The frontend and backend processes communicate through two named
  pipes (FIFO files) in the slice filesystem.
  
  Vsys FIFO files are located in /vsys/. For a single backend, the FIFOs are
  named /vsys/<backend>.in and /vsys/<backend>.out.

    <backend>.in corresponds to the backend process standard input (read).
    <backend>.out corresponds to the backend process standard output (write).

  In normal operation, the frontend opens <backend>.in for writing, and
  <backend>.out for reading. Vsys recognizes that the opens have occurred,
  executes the backend process, and connects standard input and output of the
  backend to the correct FIFOs already opened by the the frontend.

  # VsysFrontend Class

  Vsys does not specify how frontend and backend communicate once both
  processes are running. Also, the backend could exit immediately or be
  long-lived.

  The VsysFrontend class expects the backend process to be long-lived and to
  read commands terminated by a new line from standard input, and to return
  responses terminated by a new line on standard output.

  VsysFrontend is not thread safe. Only one caller should use a VsysFrontend
  instance at a time.

  Example usage:

  try:
    vs = VsysFrontend('vsys_backend_name')
    vs.open()
  except VsysException as err:
    print 'vsys create failed, cannot continue:', err
    return

  while True:
    try:
      print vs.sendrecv('request_command', optional_timeout)
    except VsysException as err:
      print 'vsys exception, closing connection:', err
      vs.close()
      break
  """

    def __init__(self, backend, open_nonblock=True):
        """Creates a new vsys object.

    Args:
      backend: str, vsys backend name.
      open_nonblock: bool, whether to open FIFOs nonblocking. Only for testing.
    Raises:
      VsysCreateException when the FIFOs for backend are not found.
    """

        (self._path_in, self._path_out) = get_vsys_fifo_names(backend)
        self._open_nonblock = open_nonblock
        self._fd_in = None
        self._fd_out = None

        # Check that file exists.
        if (not vsys_fifo_exists(self._path_in) or
                not vsys_fifo_exists(self._path_out)):
            raise VsysCreateException('vsys FIFOs not found: %s, %s' %
                                      (self._path_in, self._path_out))

    def open(self):
        """Opens the vsys frontend.

    Raises:
      VsysOpenException when an error occurs opening FIFOs.
    """
        # NOTE: caller MUST open for writing BEFORE opening for reading.
        self._fd_out = self._open_fifo(self._path_in, os.O_WRONLY)
        self._fd_in = self._open_fifo(self._path_out, os.O_RDONLY)

    def close(self):
        """Closes the vsys file descriptors."""
        if self._fd_out is not None:
            os.close(self._fd_out)
            self._fd_out = None
        if self._fd_in is not None:
            os.close(self._fd_in)
            self._fd_in = None

    def sendrecv(self, message, timeout=_POLL_TIMEOUT_SEC):
        """Sends message to backend and waits up to timeout seconds for reply.

    Args:
      message: str, the complete message to send to backend.
      timeout: int, maximum time to wait for reply, in seconds.
    Returns:
      str, the complete response from backend.
    Raises:
      VsysException, if timeout occurs or premature EOF received from backend.
    """
        self._send(message + '\n')
        return self._recv(timeout).strip()

    def _open_fifo(self, path, flags):
        """Opens the vsys FIFO using given flags or raises VsysOpenException.

    If self._open_nonblock is True, then os.O_NONBLOCK is added to flags.

    Args:
      path: str, path to a vsys FIFO.
      flags: int, flags to use when opening path with os.open.
    Returns:
      int, file descriptor for FIFO.
    Raises:
      VsysOpenException, if opening path fails.
    """
        collectd.info('Opening: %s' % path)
        if self._open_nonblock:
            # NOTE: Open non-blocking, to detect when there is no reader. Or, so
            # reads can timeout using select or poll.
            flags |= os.O_NONBLOCK

        try:
            return os.open(path, flags)
        except OSError as err:
            # If opening for write, the error is likely errno.ENXIO. ENXIO occurs
            # when no reader has the other end open. e.g. when vsys is not running in
            # root context.
            raise VsysOpenException('Opening vsys fifo (%s) failed: %s' %
                                    (path, err))

    def _send(self, message):
        """Sends message to backend.

    Args:
      message: str, the message to send to backend.
    Returns:
      int, number of bytes written.
    Raises:
      VsysException, if an error occurs during write or the vsys frontend is
          not open.
    """
        if not self._fd_out:
            raise VsysException('vsys: call open before sendrecv')
        try:
            return os.write(self._fd_out, message)
        except OSError as err:
            raise VsysException('Failed to send message: %s' % err)

    def _recv(self, timeout):
        """Receives vsys response. Waits up to timeout seconds.

    Args:
      timeout: int, maximum time to wait for reply, in seconds.
    Returns:
      str, the complete vsys response minus terminating newline.
    Raises:
      VsysException, if timeout occurs, premature EOF is read from backend or
          other IO error.
    """

        rlist, _, _ = select.select([self._fd_in], [], [], timeout)
        if not rlist:
            # NOTE: Timeout with no available data.
            raise VsysException('vsys read timeout: %s sec.' % timeout)

        data = []
        val = ''

        while val[-1:] != '\n':
            try:
                val = os.read(self._fd_in, _READ_BLOCK_SIZE)
            except OSError as err:
                # NOTE: This is likely due to EAGAIN, Resource temporarily unavailable.
                # Because no more data is available to read, and we've not gotten a
                # newline, the backend is either very slow, or broken in another way.
                # Complete reads should happen quickly, so raise an exception to signal
                # the issue.
                raise VsysException('vsys reader readline failed: %s' % err)

            if not val:
                # NOTE: Premature EOF; the backend may have crashed or been killed.
                raise VsysException('Error reading from vsys. read EOF.')

            data.append(val)

        block = ''.join(data)
        # NOTE: The loop above only breaks when the last character is '\n'. If a
        # timeout has occurred previously, then extra, possibly incomplete data may
        # prefix the current response. This expression splits at most once from the
        # right on other newlines in the block. Only the last message is returned.
        return block.strip().rsplit('\n', 1)[-1]


def slicename_to_hostname(vs_name):
    """Converts a vserver slice name into a canonical FQDN.

  Slice names use a pattern like: <some site>_<some name>.

  Example:
    If vs_name is 'mlab_utility' and the system hostname is
    'mlab4.nuq01.measurement-lab.org', then slicename_to_hostname will return
    'utility.mlab.mlab4.nuq01.measurement-lab.org'.
  Args:
    vs_name: str, name of a vserver slice, e.g. mlab_utility.
  Returns:
    str, the canonical FQDN based on system hostname and slice name.
  """
    fields = vs_name.split('_')
    if len(fields) == 1:
        prefix = vs_name
    else:
        # The vs_name prefix is the PlanetLab site name.
        # The rest is user-chosen. Place the site name after user-chosen name.
        prefix = '.'.join(fields[1:] + [fields[0]])
    return '%s.%s' % (prefix, _root_hostname)


@meta_timer('read')
def plugin_read(unused_input_data=None):
    """Handles collectd's 'read' interface for mlab plugin."""

    vs_prefix = _PROC_VIRTUAL
    vs_dlimits = read_vsys_data('vs_xid_dlimits', _VSYS_FRONTEND_VERSION)
    report_cpuavg_for_system(_PROC_STAT)
    report_meta_metrics(_PROC_PID_STAT)
    uptime = read_system_uptime()
    for entry in os.listdir(vs_prefix):
        entry_path = os.path.join(vs_prefix, entry)
        if not os.path.isdir(entry_path):
            continue

        if entry not in _vs_xid_names:
            # Try reloading names to get new vserver names.
            init_vserver_xid_names()
            # Skip, if still not present.
            if entry not in _vs_xid_names:
                collectd.error(('mlab: no vserver name found for xid %s after '
                                'reloading names.') % entry)
                continue

        vs_name = _vs_xid_names[entry]
        if vs_name in _config_exclude_slices:
            # Do not collect any stats for this slice.
            continue

        vs_host = slicename_to_hostname(vs_name)

        report_cpu_for_vserver(vs_host, entry_path)
        report_network_for_vserver(vs_host, entry_path)
        report_limits_for_vserver(vs_host, entry_path)
        report_threads_for_vserver(vs_host, entry_path, uptime)
        if entry in vs_dlimits:
            report_quota_for_vserver(vs_host, vs_dlimits[entry])


def parse_config(config, depth=0):
    """Parses collectd configuration given to 'configure' handler.

  Also saves ExcludeSlice settings in global _config_exclude_slices.

  Args:
    depth: int, used for padding in logging. Config is a nested structure,
        and parse_config is called recursively. Depth tracks how far the
        recursion has progressed.
  """
    padding = '  ' * depth
    if config.key == 'ExcludeSlice':
        if len(config.values) == 1:
            collectd.info('%sExcluding slice %s' %
                          (padding, str(config.values[0])))
            _config_exclude_slices[config.values[0]] = True
        else:
            collectd.warning('%sIgnoring directive: %s %s' %
                             (padding, config.key, config.values))

    if len(config.children) > 0:
        collectd.info('%sChildren:' % padding)
        for child in config.children:
            parse_config(child, depth + 1)


def plugin_configure(config):
    """Handles configuring for this module. Called by collectd."""
    collectd.info('Configuring collectd-mlab plugin.')
    parse_config(config)


def plugin_initialize():
    """Initializes global variables during collectd plugin initialization."""
    global _PROC_PID_STAT
    collectd.info('Initializing collectd-mlab plugin.')
    _PROC_PID_STAT = '/proc/%s/stat' % os.getpid()


def plugin_shutdown():
    """Runs any shutdown routines during collectd plugin shutdown."""
    collectd.info('Shutting down collectd-mlab plugin.')


if should_register_plugin:
    # Register callbacks. Order is important.
    collectd.register_config(plugin_configure)
    collectd.register_init(plugin_initialize)
    collectd.register_read(plugin_read)
    # The mlab plugin has no write support today.
    # collectd.register_write(write)
    collectd.register_shutdown(plugin_shutdown)
