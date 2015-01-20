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
"""
The vs_resource_backend is the vsys backend for collectd-mlab plugin.

The collectd-mlab plugin runs in a vserver context, and the vs_resource_backend
runs in the host context of an M-Lab server. This script is needed to provide
access to system information not otherwise accessible from inside a vserver
context.

When vsys detects an open on the vsys frontend FIFOs, this script is executed.
Stdin and stdout are connected to the FIFOs used by the frontend (in /vsys/*).
The vs_resource_backend does not exit as long as the frontend keeps the FIFOs
open (e.g. stdin / stdout are open). The vs_resource_backend exits when
communication with the frontend is no longer possible (e.g. due to EOF, or
EPIPE).

The vs_resource_backend reads commands from stdin separated by a newline. The
vs_resource_backend writes replies to stdout separated by a newline.

Command format:
  <command><NL>

Reply format:
  <json reply><NL>

The vs_resource_backend supports three commands:
  vs_xid_names: returns a mapping from xid to slice names.
  vs_xid_dlimits: returns a mapping from xid to storage quota usage.
  backend_stats: returns memory and cpu usage statistics for this process.

The JSON reply has some standard attributes:
  version: int, the version of this backend script.
  ts: float, timestamp of request in seconds.
  message_type: str, the name of the command in request.
  data: obj, the result of the command.

== Installation ==

In the host context, install vs_resource_backend.py (without .py extension) to:

    /vsys/vs_resource_backend

The mlab_utility slice (or another slice that should access this backend)
should have a PlanetLab slice attribute for this script:

    vsys=vs_resource_backend
"""

import ctypes
import ctypes.util
import errno
import json
import os
import pwd
import sys
import syslog
import time
import traceback


VS_BACKEND_VERSION = 1

_LIBVSERVER = None
_MAX_MESSAGE_LENGTH = 128
_PAGESIZE = os.sysconf(os.sysconf_names['SC_PAGESIZE'])
_PROC_PID_STAT = None
_VS_PREFIX_DIR = '/proc/virtual'


class Error(Exception):
  """Base for errors in this module."""


class EndOfFileError(Error):
  """Raised when receiving an EOF from stdin."""


class LibVserverError(Error):
  """Raised when an error occurs with libvserver."""


# Invalid class name. This is a special case needed for a C-data type.
# pylint: disable=C0103
class vc_ctx_dlimit(ctypes.Structure):
  """A ctypes representation of vserver, struct vc_ctx_dlimit."""
  _fields_ = [('space_used', ctypes.c_uint32),
              ('space_total', ctypes.c_uint32),
              ('inodes_used', ctypes.c_uint32),
              ('inodes_total', ctypes.c_uint32),
              ('reserved', ctypes.c_uint32),]
# pylint: enable=C0103


def init_libvserver():
  """Loads the libvserver library into the global _LIBVSERVER.

  Returns:
     True on success, False on failure.
  """
  global _LIBVSERVER
  lib = ctypes.util.find_library('vserver')
  if lib is None:
    syslog_err('Failed to find vserver library.')
    return False

  try:
    _LIBVSERVER = ctypes.cdll.LoadLibrary(lib)
  except OSError as err:
    syslog_err('Failed to load vserver library: %s' % err)
    return False

  return True


def vc_get_dlimit(filename, xid):
  """Calls libvserver.vc_get_dlimit to get dlimit values for xid.

  Dlimits are applied to a specific directory path.
  
  Args:
    filename: str, path to slice root dir, where quota limits are applied.
    xid: int, xid of vserver.
  Returns:
    list of int, [space_used, space_total, inodes_used, inodes_total, reserved]
  """
  if _LIBVSERVER is None:
    if not init_libvserver():
      raise LibVserverError('Failed to initialize libvserver.')

  c_limit = vc_ctx_dlimit()
  c_filename = ctypes.c_char_p(filename)
  c_xid = ctypes.c_uint32(xid)

  err = _LIBVSERVER.vc_get_dlimit(
      c_filename, c_xid, ctypes.c_uint32(0), ctypes.byref(c_limit))

  if err == -1:
    raise LibVserverError('Failed to get dlimits for %s' % xid)

  # '-1' means no quota.
  ret = [c_limit.space_used,
         c_limit.space_total,
         c_limit.inodes_used,
         -1 if (2 ** 32) - 1 == c_limit.inodes_total else c_limit.inodes_total,
         c_limit.reserved]
  return ret


def syslog_err(msg):
  """Logs msg to syslog."""
  for message in msg.split('\n'):
    if message:
      syslog.syslog(syslog.LOG_ERR, 'vs_resource_backend: %s' % message)


def get_vserver_xids(vs_prefix_dir):
  """Lists all vserver xids found in the vs_prefix_dir directory.

  Args:
    vs_prefix_dir: str, path to the root of vserver /proc directory.
  Returns:
    list of int, where each int is an xid found in vs_prefix_dir.
  """
  xids = []
  for entry in os.listdir(vs_prefix_dir):
    entry_path = os.path.join(vs_prefix_dir, entry)
    if not os.path.isdir(entry_path):
      continue
    try:
      xid = int(entry)
    except ValueError:
      syslog_err('Failed to convert xid entry to int: %s' % entry)
      continue
    xids.append(xid)
  return xids


def get_xid_names():
  """Fulfills the vs_xid_names request.

  Returns:
    dict, keys are vserver xids as string, values are vserver names as string.
  """
  xid_names = {}

  for xid in get_vserver_xids(_VS_PREFIX_DIR):
    try:
      # On PlanetLab, uid is set to equal the vserver xid.
      pw_entry = pwd.getpwuid(xid)
    except KeyError:
      # This is serious. A vserver is running without passwd entry.
      syslog_err('Failed to find /etc/passwd entry for xid: %d' % xid)
      continue

    vs_name = pw_entry.pw_name
    if not vs_name:
      syslog_err('pw_entry.pw_name is zero length for xid: %d' % xid)
      continue

    xid_names[str(xid)] = vs_name

  return xid_names


def get_xid_dlimits():
  """Fulfills the vs_xid_dlimits request.

  Returns:
    dict, keys are xids as string, values are 5-element lists with dlimits.
  """
  xid_dlimits = {}

  for xid in get_vserver_xids(_VS_PREFIX_DIR):
    try:
      dlim = vc_get_dlimit('/vservers', xid)
    except LibVserverError as err:
      # Errors calling vc_get_dlimit are likely to end fatally.
      syslog_err('vc_get_dlimit failed: %s' % err)
      continue

    xid_dlimits[str(xid)] = dlim

  return xid_dlimits


def get_backend_stats(stat_path):
  """Fulfills the backend_stats request.

  Returns:
    dict, with process time and memory usage stats. Includes:
        utime: user time, cumulative USER_HZ.
        stime: system time, cumulative USER_HZ.
        vsize: virtual memory size, bytes,
        rss: resident set size, bytes.
  """
  index_utime = 13
  index_stime = 14
  index_cutime = 15
  index_cstime = 16
  index_vsize = 22
  index_rss = 23
  index_max = 24

  backend_stats = {'utime': 0, 'stime': 0, 'vsize': 0, 'rss': 0}

  if not os.path.exists(stat_path):
    return backend_stats

  try:
    stat_fields = open(stat_path, 'r').read().strip().split()
  except IOError as err:
    syslog_err('get_backend_stats: failed to read stats: %s' % str(err))
    return backend_stats

  if len(stat_fields) < index_max:
    syslog_err('get_backend_stats found only %s fields.' % len(stat_fields))
    return backend_stats

  try:
    backend_stats['utime'] = (
      float(stat_fields[index_utime]) + float(stat_fields[index_cutime]))
    backend_stats['stime'] = (
        float(stat_fields[index_stime]) + float(stat_fields[index_cstime]))
    backend_stats['vsize'] = int(stat_fields[index_vsize])
    backend_stats['rss'] = int(stat_fields[index_rss]) * _PAGESIZE
  except ValueError as err:
    syslog_err('get_backend_stats failed to convert stats: %s' % str(err))
    return backend_stats

  return backend_stats


def report(message_type, obj):
  """Returns a dict that wraps the given object with additional metadata."""
  data = {
    'version': VS_BACKEND_VERSION,
    'ts': time.time(),
    'data': obj,
    'message_type': message_type,
  }
  return data


def handle_message(message_type):
  """Dispatches message type to right handler and returns json result."""
  if message_type == 'vs_xid_names':
    raw_data = get_xid_names()
  elif message_type == 'vs_xid_dlimits':
    raw_data = get_xid_dlimits()
  elif message_type == 'backend_stats':
    raw_data = get_backend_stats(_PROC_PID_STAT)
  else:
    syslog_err('Unknown message type: %s' % message_type)
    return None

  data = report(message_type, raw_data)
  return json.dumps(data)


def handle_request():
  """Handles request by reading from stdin, and dispatching request."""
  line = sys.stdin.readline(_MAX_MESSAGE_LENGTH)
  if not line:
    raise EndOfFileError('Received EOF. Exiting.')

  if line[-1] != '\n':
    syslog_err('Ignoring incomplete message: %s' % line)
    return 

  json_data = handle_message(line.strip())
  if not json_data:
    return 

  sys.stdout.write('%s\n' % json_data)
  sys.stdout.flush()


def main():
  global _PROC_PID_STAT
  _PROC_PID_STAT = '/proc/%s/stat' % os.getpid()

  try:
    while True:
      # Loop forever, until handle_request raises an exception.
      handle_request()
  except EndOfFileError as err:
    # EOF on stdin is a clean exit, likely due to a frontend close.
    exit_code = 0
    exit_message = ''
  except IOError as err:
    # EPIPE is the only ok reason to exit. The rest are unexpected.
    exit_code = 0 if err.errno == errno.EPIPE else 1
    exit_message = '' if err.errno == errno.EPIPE else str(err)
  except Exception as err:
    # And any other exception type, log all the details.
    exit_code = 1
    exit_message = traceback.format_exc()
  finally:
    if exit_message:
      syslog_err(exit_message)
    sys.exit(exit_code)


if __name__ == '__main__':  # pragma: no cover
  main()
