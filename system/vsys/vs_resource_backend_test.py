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
"""Unit tests for vsys vs_resource_backend script."""

import errno
import os
import pwd

# Third-party packages.
import mock
import unittest2 as unittest

# module under test.
import vs_resource_backend

# R0904: too many public methods.
# W0212: access to a protected member, required to access global values in
#        module under test.
# pylint: disable=R0904
# pylint: disable=W0212


def fake_vc_get_dlimit(
    unused_c_filename, unused_c_xid, unused_c_flags, c_limit):
  """Fake version of vserver library method vc_get_dlimit."""
  limit = c_limit._obj
  limit.space_used = 10
  limit.space_total = 100
  limit.inodes_used = 20
  limit.inodes_total = (2 ** 32) - 1
  limit.reserved = 2
  return 0


class MlabVsResourceBackendTests(unittest.TestCase):

  def setUp(self):
    self._testdata_dir = os.path.join(
        os.path.dirname(vs_resource_backend.__file__), 'testdata')

  def tearDown(self):
    global vs_resource_backend
    vs_resource_backend = reload(vs_resource_backend)

  def testunit_vc_get_dlimit(self):
    mock_vslib = mock.Mock()
    mock_vslib.vc_get_dlimit = fake_vc_get_dlimit
    vs_resource_backend._LIBVSERVER = mock_vslib
    expected_result = [10, 100, 20, -1, 2]

    returned_result = vs_resource_backend.vc_get_dlimit('/vservers', 515)

    self.assertEqual(returned_result, expected_result)

  @mock.patch('vs_resource_backend.ctypes.util')
  def testunit_vc_get_dlimit_WHEN_vserver_lib_not_found_RAISES_LibVserverError(
      self, mock_ctypes_util):
    mock_ctypes_util.find_library.return_value = None

    self.assertRaises(
        vs_resource_backend.LibVserverError, vs_resource_backend.vc_get_dlimit,
        '/vservers', 515)

  @mock.patch('vs_resource_backend.ctypes.util')
  def testunit_vc_get_dlimit_WHEN_vserver_load_fails_RAISES_LibVserverError(
      self, mock_ctypes_util):
    mock_ctypes_util.find_library.return_value = 'not-a-real-library'

    self.assertRaises(
        vs_resource_backend.LibVserverError, vs_resource_backend.vc_get_dlimit,
        '/vservers', 515)

  @mock.patch('vs_resource_backend.vc_get_dlimit')
  def testunit_get_xid_dlimits(self, mock_vc_get_dlimit):
    vs_resource_backend._VS_PREFIX_DIR = self._testdata_dir
    dlim = [0, 1, 2, 3, 4]
    mock_vc_get_dlimit.return_value = dlim
    expected_value = {'515': dlim[:]}

    returned_value = vs_resource_backend.get_xid_dlimits()

    self.assertEqual(expected_value, returned_value)

  @mock.patch('vs_resource_backend.vc_get_dlimit')
  def testunit_get_xid_dlimits_WHEN_vc_get_dlimit_RAISES_Exception(self,
      mock_vc_get_dlimit):
    mock_vc_get_dlimit.side_effect = (
        vs_resource_backend.LibVserverError('broken system call'))
    expected_value = {}

    returned_value = vs_resource_backend.get_xid_dlimits()

    self.assertDictEqual(expected_value, returned_value)

  def testunit_get_backend_stats(self):
    test_stat_path = os.path.join(self._testdata_dir, 'proc_pid_stat')
    expected_value = {'rss': 1740800, 'vsize': 1234321, 'stime': 1503.0,
                      'utime': 255.0}

    returned_value = vs_resource_backend.get_backend_stats(test_stat_path)

    self.assertDictEqual(expected_value, returned_value)

  def testunit_get_backend_stats_WHEN_path_does_not_exist(self):
    bad_stat_path = os.path.join(self._testdata_dir, 'no_proc_pid_stat')
    expected_value = {'rss': 0, 'vsize': 0, 'stime': 0, 'utime': 0}

    returned_value = vs_resource_backend.get_backend_stats(bad_stat_path)

    self.assertDictEqual(expected_value, returned_value)

  @mock.patch('__builtin__.open')
  def testunit_get_backend_stats_WHEN_path_RAISES_IOError(self, mock_open):
    test_stat_path = os.path.join(self._testdata_dir, 'proc_pid_stat')
    mock_open.side_effect = IOError(-1, 'Forced IO error')
    expected_value = {'rss': 0, 'vsize': 0, 'stime': 0, 'utime': 0}

    returned_value = vs_resource_backend.get_backend_stats(test_stat_path)

    self.assertDictEqual(expected_value, returned_value)

  def testunit_get_backend_stats_WHEN_bad_stat_data(self):
    bad_stat_path = os.path.join(self._testdata_dir, 'bad_proc_pid_stat')
    expected_value = {'rss': 0, 'vsize': 0, 'stime': 0, 'utime': 0}

    returned_value = vs_resource_backend.get_backend_stats(bad_stat_path)

    self.assertDictEqual(expected_value, returned_value)

  def testunit_get_backend_stats_WHEN_data_RAISES_ValueError(self):
    bad_stat_path = os.path.join(self._testdata_dir, 'bad_proc_pid_stat2')
    expected_value = {'rss': 0, 'vsize': 0, 'stime': 0, 'utime': 0}

    returned_value = vs_resource_backend.get_backend_stats(bad_stat_path)

    self.assertDictEqual(expected_value, returned_value)

  @mock.patch('vs_resource_backend.pwd.getpwuid')
  def testunit_get_xid_names(self, mock_getpwuid):
    vs_resource_backend._VS_PREFIX_DIR = self._testdata_dir
    mock_getpwuid.return_value = pwd.struct_pwent(
        ('mlab_utility', '*', 515, 505, 1, '/home/mlab_utility', '/bin/bash'))
    expected_value = {'515': 'mlab_utility'}

    returned_value = vs_resource_backend.get_xid_names()

    self.assertDictEqual(expected_value, returned_value)
 
  @mock.patch('vs_resource_backend.pwd.getpwuid')
  def testunit_get_xid_names_WHEN_getpwuid_RAISES_KeyError(self, mock_getpwuid):
    vs_resource_backend._VS_PREFIX_DIR = self._testdata_dir
    mock_getpwuid.side_effect = KeyError('fake key error')
    expected_value = {}

    returned_value = vs_resource_backend.get_xid_names()

    self.assertDictEqual(expected_value, returned_value)

  @mock.patch('vs_resource_backend.pwd.getpwuid')
  def testunit_get_xid_names_WHEN_pw_name_IS_invalid(self, mock_getpwuid):
    vs_resource_backend._VS_PREFIX_DIR = self._testdata_dir
    mock_getpwuid.return_value = pwd.struct_pwent(
        ('', '*', 515, 505, 1, '/home/mlab_utility', '/bin/bash'))
    expected_value = {}

    returned_value = vs_resource_backend.get_xid_names()

    self.assertDictEqual(expected_value, returned_value)

  @mock.patch('vs_resource_backend.time')
  def testunit_report(self, mock_time):
    mock_time.time.return_value = 10
    mock_obj = mock.Mock()
    expected_value = {'version': vs_resource_backend.VS_BACKEND_VERSION,
                      'ts': 10,
                      'data': mock_obj,
                      'message_type': 'banana'}

    returned_value = vs_resource_backend.report('banana', mock_obj)

    self.assertDictEqual(expected_value, returned_value)

  @mock.patch('vs_resource_backend.syslog_err')
  def testunit_handle_message_WHEN_type_IS_unknown(
      self, mock_syslog_err):
    returned_value = vs_resource_backend.handle_message('unknown')

    mock_syslog_err.assert_called_once()
    self.assertEqual(returned_value, None)

  @mock.patch('vs_resource_backend.get_backend_stats')
  @mock.patch('vs_resource_backend.time')
  def testunit_handle_message_WHEN_type_IS_backend_stats(
      self, mock_time, mock_get_backend_stats):
    mock_time.time.return_value = 10
    mock_get_backend_stats.return_value = {
      'rss': 2961408, 'vsize': 4919296, 'stime': 13.0, 'utime': 2.0}
    expected_value = ('{"message_type": "backend_stats", "version": 1, "data":'
                      ' {"utime": 2.0, "vsize": 4919296, "stime": 13.0, '
                      '"rss": 2961408}, "ts": 10}')
  
    returned_value = vs_resource_backend.handle_message('backend_stats')

    mock_get_backend_stats.assert_called_once()
    self.assertEqual(returned_value, expected_value)

  @mock.patch('vs_resource_backend.get_xid_dlimits')
  @mock.patch('vs_resource_backend.time')
  def testunit_handle_message_WHEN_type_IS_vs_xid_dlimits(
      self, mock_time, mock_get_xid_dlimits):
    mock_time.time.return_value = 10
    mock_get_xid_dlimits.return_value = {'515': [386460, 10000000, 9533, -1, 2]}
    expected_value = ('{"message_type": "vs_xid_dlimits", "version": 1, "data":'
                      ' {"515": [386460, 10000000, 9533, -1, 2]}, "ts": 10}')
  
    returned_value = vs_resource_backend.handle_message('vs_xid_dlimits')

    mock_get_xid_dlimits.assert_called_once()
    self.assertEqual(returned_value, expected_value)

  @mock.patch('vs_resource_backend.get_xid_names')
  @mock.patch('vs_resource_backend.time')
  def testunit_handle_message_WHEN_type_IS_vs_xid_names(
      self, mock_time, mock_get_xid_names):
    mock_time.time.return_value = 10
    mock_get_xid_names.return_value = {'515': 'mlab_utility'}
    expected_value = ('{"message_type": "vs_xid_names", "version": 1, "data":'
                      ' {"515": "mlab_utility"}, "ts": 10}')
  
    returned_value = vs_resource_backend.handle_message('vs_xid_names')

    mock_get_xid_names.assert_called_once()
    self.assertEqual(returned_value, expected_value)

  @mock.patch('vs_resource_backend.syslog')
  def testunit_syslog_err(self, mock_syslog):
    mock_syslog.LOG_ERR = 1
    mock_syslog.syslog.side_effect = [None, None]
    fake_message = 'one\ntwo\n'
    expected_calls = [mock.call(1, 'vs_resource_backend: one'),
                      mock.call(1, 'vs_resource_backend: two')]

    vs_resource_backend.syslog_err(fake_message)

    mock_syslog.syslog.assert_has_calls(expected_calls)

  @mock.patch('vs_resource_backend.handle_message')
  @mock.patch('vs_resource_backend.sys.stdout')
  @mock.patch('vs_resource_backend.sys.stdin')
  def testunit_handle_request(
      self, mock_stdin, mock_stdout, mock_handle_message):
    mock_stdin.readline.return_value = 'backend_stats\n'
    mock_handle_message.return_value = '{"version": 1, "data": "fake reply"}'
    expected_value = '{"version": 1, "data": "fake reply"}\n'

    vs_resource_backend.handle_request()
    
    mock_stdout.write.assert_called_with(expected_value)

  @mock.patch('vs_resource_backend.sys.stdin')
  def testunit_handle_request_WHEN_readline_RAISES_eof(self, mock_stdin):
    mock_stdin.readline.return_value = ''

    self.assertRaises(vs_resource_backend.EndOfFileError,
                      vs_resource_backend.handle_request)

  @mock.patch('vs_resource_backend.syslog_err')
  @mock.patch('vs_resource_backend.sys.stdin')
  def testunit_handle_request_WHEN_message_IS_incomplete(
      self, mock_stdin, mock_syslog_err):
    mock_stdin.readline.return_value = 'incomplete_message_without_newline'

    vs_resource_backend.handle_request()

    mock_syslog_err.assert_called_once()

  @mock.patch('vs_resource_backend.handle_message')
  @mock.patch('vs_resource_backend.sys.stdin')
  def testunit_handle_request_WHEN_handle_message_RETURNS_nothing(
      self, mock_stdin, mock_handle_message):
    mock_stdin.readline.return_value = 'token backend_stats\n'
    mock_handle_message.return_value = None

    vs_resource_backend.handle_request()
    
    mock_handle_message.assert_called_once()

  @mock.patch('vs_resource_backend.handle_message')
  @mock.patch('vs_resource_backend.sys.stdout')
  @mock.patch('vs_resource_backend.sys.stdin')
  def testunit_handle_request_WHEN_write_RAISES_ioerror(
      self, mock_stdin, mock_stdout, mock_handle_message):
    mock_stdin.readline.return_value = 'token backend_stats\n'
    mock_handle_message.return_value = (
        'token {"version": 1, "data": "fake reply"}\n')
    mock_stdout.write.side_effect = IOError(errno.EPIPE, 'fake ioerror')

    self.assertRaises(IOError, vs_resource_backend.handle_request)

  @mock.patch('vs_resource_backend.handle_request')
  @mock.patch('vs_resource_backend.sys.exit')
  def testunit_main_WHEN_request_RAISES_eof(
      self, mock_exit, mock_handle_request):
    mock_handle_request.side_effect = (
        vs_resource_backend.EndOfFileError('fake eof'))
    
    vs_resource_backend.main()

    mock_handle_request.assert_called_once()
    mock_exit.assert_called_with(0)
    
  @mock.patch('vs_resource_backend.handle_request')
  @mock.patch('vs_resource_backend.sys.exit')
  def testunit_main_WHEN_request_RAISES_ioerror(
      self, mock_exit, mock_handle_request):
    mock_handle_request.side_effect = IOError(-1, 'fake ioerror')
    
    vs_resource_backend.main()

    mock_handle_request.assert_called_once()
    mock_exit.assert_called_with(1)

  @mock.patch('vs_resource_backend.handle_request')
  @mock.patch('vs_resource_backend.sys.exit')
  def testunit_main_WHEN_request_RAISES_other_exception(
      self, mock_exit, mock_handle_request):
    mock_handle_request.side_effect = Exception('fake eof')
    
    vs_resource_backend.main()

    mock_handle_request.assert_called_once()
    mock_exit.assert_called_with(1)


if __name__ == "__main__":
  unittest.main()
