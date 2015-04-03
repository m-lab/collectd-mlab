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
COLLECTD_PID = '/vservers/%s/var/run/collectd.pid' % SLICENAME
COLLECTD_UNIXSOCK = '/vservers/%s/var/run/collectd-unixsock' % SLICENAME

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
  status_code = STATE_UNKNOWN
  def __init__(self, message, status_code=None):
    if status_code:
      self.status_code = status_code
    Error.__init__(self, message)


class CriticalError(NagiosStateError):
  """A critical error has occurred."""
  status_code = STATE_CRITICAL


class TimeoutError(Exception):
  """A timeout has occurred."""


def sock_connect(path):
  """Creates a unix domain, stream socket and connects to path.

  Args:
    path: str, absolute path to unix socket name.
  Returns:
    AF_UNIX socket.socket of type SOCK_STREAM on success, None on error.
  """
  try:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    return sock
  except socket.error:
    return None


def sock_sendcmd(sock, command):
  """Writes command to socket.

  Args:
    sock: socket.socket, connected unix domain socket.
    command: str, the command to send over socket.
  Returns:
    int, positive on success, less than or equal to zero on error.
  """
  sock.send(command + '\n')
  status_message = sock_readline(sock)
  if not status_message:
    return 0
  code, _ = status_message.split(' ', 1)
  try:
    return int(code)
  except ValueError:
    return 0


def sock_readline(sock):
  """Reads one line from sock, until first newline character.

  Args:
     sock: socket.socket, to read line from this socket.
  Returns:
     str, the line read, or empty string on error.
  """
  try:
    data = ''
    buf = []
    while data != '\n':
      data = sock.recv(1)
      if not data:
        break
      if data != '\n':
        buf.append(data)
    return ''.join(buf)
  except socket.error:
    return ''


def assert_collectd_installed():
  """Asserts that collectd is installed in the utility slice.

  Raises:
    CriticalError, if an error occurs.
  """
  # Is collectd installed?
  if not os.path.exists(COLLECTD_BIN):
    raise CriticalError('collectd binary not present: %s.' % COLLECTD_BIN)

  # Is collectd socket available?
  if not os.path.exists(COLLECTD_UNIXSOCK):
    raise CriticalError(
        'collectd unixsock not present: %s.' % COLLECTD_UNIXSOCK)


def assert_collectd_responds():
  """Asserts that collectd is responding over the COLLECTD_UNIXSOCK.

  Raises:
    CriticalError if an error occurs.
  """
  # Is filesystem read-write ok?
  if not os.access(COLLECTD_PID, os.W_OK):
    raise CriticalError('collectd filesystem is NOT_WRITEABLE!')

  # Is collectd responsive over unix socket?
  sock = sock_connect(COLLECTD_UNIXSOCK)
  if sock is None:
    raise CriticalError(
        'CONNECT_FAILED but unixsock present (%s)!' % COLLECTD_UNIXSOCK)

  # Can we request a value over socket?
  val = sock_sendcmd(sock, 'GETVAL "%s/meta/timer-read"' % HOSTNAME)
  if val <= 0:
    raise CriticalError(
        'SENDCMD_FAILED but collectd unixsock is open.')

  # Read as many lines as reported back from command.
  for _ in xrange(0, val):
    if not sock_readline(sock):
      raise CriticalError('EMPTY_MESSAGE read from collectd socket.')


def assert_collectd_vsys_setup():
  """Asserts that vsys configuration is complete for mlab_utility.

  Raises:
    CriticalError if an error occurs.
  """
  # Is the vsys backend script installed?
  if not os.path.exists(VSYSPATH_BACKEND):
    raise CriticalError('NO_VSYS_BACKEND %s is missing!' % VSYSPATH_BACKEND)

  # Is the vsys frontend FIFO in the slice context?
  if not os.path.exists(VSYSPATH_SLICE):
    raise CriticalError('NO_VSYS_SLICE %s is missing in slice' % VSYSPATH_SLICE)

  # Is mlab_utility in the vsys acl for the backend script?
  try:
    acl = open(VSYSPATH_ACL, 'r').read().strip()
  except IOError:
    raise CriticalError('NO_VSYS_ACL cannot read vsys acl: %s' % VSYSPATH_ACL)

  if SLICENAME not in acl:
    raise CriticalError(
        'NO_SLICE_IN_ACL %s is missing from ACL %s' % (SLICENAME, VSYSPATH_ACL))


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
    exit code from collectd-nagios. Because this is a nagios plugin, these are
        valid nagios exit states.
  """
  cmd = ('collectd-nagios -s {unixsock} -H {host} '+
         '-n {metric} -d {value} -w {warning} '+
         '-c {critical}')
  cmd = cmd.format(unixsock=COLLECTD_UNIXSOCK, host=host,
                   metric=metric, value=value, warning=warning,
                   critical=critical)
  child = subprocess.Popen(
      cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
      'used', '0:8000000000', '0:9000000000')
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
      'value', '0:10000000', '0:15000000')
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
    return (err.status_code, str(err))

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
