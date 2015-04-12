#!/usr/bin/python
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
"""Summary:
  check_collectd_mlab.py is a nagios plugin that checks the health of
  collectd-mlab.

  check_collectd_mlab.py should be installed and run from the host context.

When check_collectd_mlab.py returns an OKAY status, the following are true:
 * collectd is installed in the utility slice.
 * collectd responds to commands sent over the collectd unix socket.
 * collectd is running on a writable filesystem.
 * vsys configuration is correct for the backend, frontend, and slice acl.
 * resource usage by the collectd process and slice are normal.
 * TODO: make resource usage checks configurable.
 * TODO: check the last disk sync time recently and without too much error.

If any of the above checks fail, check_collectd_mlab.py exits with a nagios
error code and descriptive message.

Installation:
  /usr/lib/nagios/plugins/

Example usage:
  $ ./check_collectd_mlab.py
  OKAY: Blue skies smiling at me: 1.27 sec

If collectd is not running:
  $ ./check_collectd_mlab.py
  CRITICAL: collectd unixsock not present:
       /vservers/mlab_utility/var/run/collectd-unixsock.
"""

import os
import signal
import socket
import subprocess
import sys
import time


# These values expect that:
# * This script runs in host context.
# * collectd runs in mlab_utility slice.
# * collectd uses default pidfile name.
# * collectd uses default unixsock name.
SLICENAME = 'mlab_utility'
COLLECTD_BIN = '/vservers/%s/usr/sbin/collectd' % SLICENAME
COLLECTD_NAGIOS = '/vservers/%s/usr/bin/collectd-nagios' % SLICENAME
COLLECTD_PID = '/vservers/%s/var/run/collectd.pid' % SLICENAME
COLLECTD_UNIXSOCK = '/vservers/%s/var/run/collectd-unixsock' % SLICENAME
LD_LIBRARY_PATH = '/vservers/%s/usr/lib' % SLICENAME

HOSTNAME = socket.gethostname()
VSYSPATH_ACL = '/vsys/vs_resource_backend.acl'
VSYSPATH_BACKEND = '/vsys/vs_resource_backend'
VSYSPATH_SLICE = '/vservers/%s/vsys/vs_resource_backend.in' % SLICENAME
DEFAULT_TIMEOUT = 60

# Canonical, nagios exit codes.
STATE_OK = 0
STATE_WARNING = 1
STATE_CRITICAL = 2
STATE_UNKNOWN = 3
STATUS_MESSAGES = {
    STATE_OK: 'OKAY',
    STATE_WARNING: 'WARNING',
    STATE_CRITICAL: 'CRITICAL',
    STATE_UNKNOWN: 'UNKNOWN'
}


class Error(Exception):
  """Base error class for this module."""
  pass


class NagiosStateError(Error):
  """A generic nagios status error."""

  def __init__(self, message, status_code=STATE_UNKNOWN):
    self.status_code = status_code
    Error.__init__(self, message)


class CriticalError(Error):
  """A base class for all critical errors."""
  pass


class MissingBinaryCriticalError(CriticalError):
  """The collectd binary is missing."""
  pass


class MissingNagiosBinaryCriticalError(CriticalError):
  """The collectd-nagios binary is missing."""
  pass


class MissingSocketCriticalError(CriticalError):
  """The collectd socket is missing."""
  pass


class ReadonlyFilesystemCriticalError(CriticalError):
  """Collectd is running on a read-only filesystem."""
  pass


class SocketConnectionCriticalError(CriticalError):
  """Connecting to the collectd unix socket failed."""
  pass


class SocketSendCommandCriticalError(CriticalError):
  """Sending a command to collectd over the unix socket failed."""
  pass


class SocketReadlineCriticalError(CriticalError):
  """Reading the response from collectd over the unix socket failed."""
  pass


class MissingVsysBackendCriticalError(CriticalError):
  """The vsys backend script is missing."""
  pass


class MissingVsysFrontendCriticalError(CriticalError):
  """The vsys frontend FIFO is missing inside slice."""
  pass


class MissingVsysAclCriticalError(CriticalError):
  """The vsys ACL file is missing."""
  pass


class MissingSliceFromVsysAclCriticalError(CriticalError):
  """The expected slice name was not found in the vsys ACL file."""
  pass


class TimeoutError(Exception):
  """A timeout has occurred."""
  pass


def _mb_to_bytes(size_mb):
  """Converts size_mb to a collectd-nagios range in bytes.

  Args:
    size_mb: int, a size in megabytes (not mebibytes).
  Returns:
    str, a nagios range, e.g. 0:1000000
  """
  return '0:%s' % (size_mb * 1000 * 1000)


def sock_connect(path):
  """Creates a unix domain, stream socket and connects to path.

  Args:
    path: str, absolute path to unix socket name.
  Returns:
    AF_UNIX socket.socket of type SOCK_STREAM.
  Raises:
    CriticalError, if socket connection fails.
  """
  try:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    return sock
  except socket.error as err:
    raise SocketConnectionCriticalError(
        'Failed to connect to socket %s! Received socket.error: %s' %
        (path, err))


def sock_sendcmd(sock, command):
  """Writes command to collectd UnixSock socket.

  For more information, see also the collectd UnixSock socket protocol:
    https://collectd.org/wiki/index.php/Plain_text_protocol

  Args:
    sock: socket.socket, connected unix domain socket.
    command: str, the command to send over socket.
  Returns:
    int, the status code returned by collectd. The value is positive on
        success, less than or equal to zero on error.
  Raises:
    CriticalError, if socket send fails.
  """
  try:
    sock.send(command + '\n')
  except socket.error as err:
    raise SocketSendCommandCriticalError(
        'Sending %s failed: %s' % (command, err))

  status_message = sock_readline(sock)
  code = status_message.split(' ', 1)
  try:
    return int(code[0])
  except ValueError:
    return 0


def sock_readline(sock):
  """Reads one line from sock, until first newline character.

  Args:
     sock: socket.socket, to read line from this socket.
  Returns:
     str, the line read, or empty string on error.
  Raises:
    CriticalError, if socket read fails.
  """
  try:
    buf = []
    data = sock.recv(1)
    while data and data != '\n':
      buf.append(data)
      data = sock.recv(1)
    return ''.join(buf)
  except socket.error as err:
    raise SocketReadlineCriticalError(
        'Failed to read message from collectd. Received error: %s' % err)


def assert_collectd_installed():
  """Asserts that collectd is installed in the utility slice.

  Raises:
    CriticalError, if an error occurs.
  """
  # Is collectd installed?
  if not os.path.exists(COLLECTD_BIN):
    raise MissingBinaryCriticalError(
        'collectd binary not present: %s.' % COLLECTD_BIN)

  # Is collectd-nagios plugin installed?
  if not os.path.exists(COLLECTD_NAGIOS):
    raise MissingNagiosBinaryCriticalError(
        'collectd-nagios binary not present: %s.' % COLLECTD_NAGIOS)

  # Is collectd socket available?
  if not os.path.exists(COLLECTD_UNIXSOCK):
    raise MissingSocketCriticalError(
        'collectd unixsock not present: %s.' % COLLECTD_UNIXSOCK)


def assert_collectd_responds():
  """Asserts that collectd is responding over the COLLECTD_UNIXSOCK.

  Raises:
    CriticalError if an error occurs.
  """
  # Is filesystem read-write ok?
  if not os.access(COLLECTD_PID, os.W_OK):
    raise ReadonlyFilesystemCriticalError(
        'collectd filesystem is read only!')

  # Is collectd responsive over unix socket?
  sock = sock_connect(COLLECTD_UNIXSOCK)

  # Can we request a value over socket?
  val = sock_sendcmd(sock, 'GETVAL "%s/meta/timer-read"' % HOSTNAME)
  if val <= 0:
    raise SocketSendCommandCriticalError(
        'collectd unixsock is open, but sending GETVAL command failed.')

  # Read as many lines as reported back from command.
  for _ in xrange(0, val):
    if not sock_readline(sock):
      raise SocketReadlineCriticalError(
          'Read an empty message from collectd socket.')


def assert_collectd_vsys_setup():
  """Asserts that vsys configuration is complete for mlab_utility.

  Raises:
    CriticalError if an error occurs.
  """
  # Is the vsys backend script installed?
  if not os.path.exists(VSYSPATH_BACKEND):
    raise MissingVsysBackendCriticalError(
        'The vsys backend script %s is missing!' % VSYSPATH_BACKEND)

  # Is the vsys frontend FIFO in the slice context?
  if not os.path.exists(VSYSPATH_SLICE):
    raise MissingVsysFrontendCriticalError(
        'The vsys frontend fifo %s is missing in slice!' % VSYSPATH_SLICE)

  # Is mlab_utility in the vsys acl for the backend script?
  try:
    acl = open(VSYSPATH_ACL, 'r').read().strip()
  except IOError:
    raise MissingVsysAclCriticalError(
        'Failed to read the vsys ACL: %s' % VSYSPATH_ACL)

  if SLICENAME not in acl:
    raise MissingSliceFromVsysAclCriticalError(
        'Slice name %s is missing from ACL: %s' % (SLICENAME, VSYSPATH_ACL))


def run_collectd_nagios(host, metric, value, warning, critical):
  """Runs collectd-nagios using given parameters as arguments.

  Please see the collectd-nagios man page for documentation on the format of
  'warning' and 'critical' thresholds.

  Args:
    host: str, experiment hostname.
    metric: str, raw metric path. (e.g. network/if_octets-ipv6)
    value: str, name of value within metric. (e.g. 'value', 'rx')
    warning: str, a collectd-nagios warning threshold. (e.g. '0:20')
    critical: str, a collectd-nagios critical threshold. (e.g. '0:30')
  Returns:
    int, exit code from collectd-nagios. Because this is a nagios plugin, these
        are valid nagios exit states.
  """
  env = os.environ.copy()
  env['LD_LIBRARY_PATH'] = '%s:%s' % (
      LD_LIBRARY_PATH, env.get('LD_LIBRARY_PATH', ''))
  cmd = ('{collectd_nagios} -s {unixsock} -H {host} -n {metric} -d {value} '
         '-w {warning} -c {critical}')
  cmd = cmd.format(
      collectd_nagios=COLLECTD_NAGIOS, unixsock=COLLECTD_UNIXSOCK, host=host,
      metric=metric, value=value, warning=warning, critical=critical)
  child = subprocess.Popen(
      cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
  return child.wait()


def assert_collectd_nagios_levels():
  """Asserts actual values from collectd using collectd-nagios.

  Asserts that storage quota is sufficient, metric collection is fast enough,
  that CPU load is low, and memory usage is reasonable.

  Raises:
    NagiosStateError, if an error occurs.
  """
  # TODO: Make warning & critical thresholds configurable.

  # Is utility slice quota ok?
  exit_code = run_collectd_nagios(
      'utility.mlab.' + HOSTNAME, 'storage/vs_quota_bytes-quota',
      'used', _mb_to_bytes(8000), _mb_to_bytes(9000))
  if exit_code != 0:
    raise NagiosStateError('Storage quota usage is too high',
                           status_code=exit_code)

  # Is collectd cpu usage too high?
  exit_code = run_collectd_nagios(
      HOSTNAME, 'meta-collectd/process_cpu-system',
      'value', '0:5', '0:10')
  if exit_code != 0:
    raise NagiosStateError('Collectd CPU usage is too high',
                           status_code=exit_code)

  # Actual memory usage should be pretty small, though vm size may be larger.
  exit_code = run_collectd_nagios(
      HOSTNAME, 'meta-collectd/process_memory-rss',
      'value', _mb_to_bytes(10), _mb_to_bytes(15))
  if exit_code != 0:
    raise NagiosStateError('Collectd RSS memory usage is too high',
                           status_code=exit_code)

  # Is meta timer too high?
  exit_code = run_collectd_nagios(
      HOSTNAME, 'meta/timer-read',
      'value', '0:6', '0:100')
  if exit_code != 0:
    raise NagiosStateError('Collectd mlab plugin taking too long to run',
                           status_code=exit_code)


def assert_disk_last_sync_time():
  """Asserts the last sync time for vserver disk limits."""
  # TODO: When was vserver quota last sync'd?
  # TODO: How do we want to check this?
  pass


def check_collectd():
  """Checks environment for signs of normal collectd-mlab behavior.
  
  Returns:
    Tuple of (int, str), corresponding to the (nagios_state, error_message).
  """
  t_start = time.time()

  # Check all critical conditions first.
  try:
    assert_collectd_installed()
    assert_collectd_responds()
    assert_collectd_vsys_setup()
    assert_disk_last_sync_time()
  except CriticalError as err:
    return (STATE_CRITICAL, str(err))

  # Since the above establishes that collectd is working, now check collectd
  # resource usage thresholds *from* collectd.
  try:
    assert_collectd_nagios_levels()
  except NagiosStateError as err:
    return (err.status_code, str(err))

  # We've made it this far, so everything appears to be ok.
  msg = 'Blue skies smiling at me: %0.2f sec' % (time.time() - t_start)
  return (STATE_OK, msg)


def init_alarm(timeout):
  """Assigns a SIGALARM handler for timeout seconds."""
  def handler(unused_signum, unused_frame):
    """Raise a TimeoutError when called"""
    raise TimeoutError('Timeout after %s' % timeout)
  signal.signal(signal.SIGALRM, handler)
  signal.alarm(timeout)


def cancel_alarm():
  """Cancels pending alarm."""
  signal.alarm(0)


def main():
  init_alarm(DEFAULT_TIMEOUT)

  try:
    (status_code, message) = check_collectd()
  except Exception as err:  # pylint: disable=W0703
    status_code = STATE_UNKNOWN
    message = str(err)

  cancel_alarm()

  print '%s: %s' % (STATUS_MESSAGES.get(status_code, 'UNKNOWN'), message)
  sys.exit(status_code)


if __name__ == "__main__":
  main()  # pragma: no cover.
