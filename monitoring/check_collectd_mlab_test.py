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
"""Unit tests for check_collectd_mlab check."""

import os
import time
import socket
import threading
import unittest2 as unittest

# Third-party modules.
import mock

# Module under test.
import check_collectd_mlab

# R0904: Too many public methods. Hard to avoid for unit tests.
# pylint: disable=R0904


class FakeCollectd(object):
  """A fake version of collectd that listens to a unix domain socket."""

  def __init__(self, unixsock):
    self._unixsock = unixsock
    self._sock = None
    self._thread = threading.Thread(target=self._serve)
    self._thread.daemon = True

  def start(self):
    """Starts the FakeCollectd service, listening on the unix domain socket."""
    if os.path.exists(self._unixsock):
      os.unlink(self._unixsock)
    self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    self._sock.bind(self._unixsock)
    self._sock.listen(1)
    self._thread.start()

  def shutdown(self):
    """Waits up to 5 seconds for the FakeCollectd thread to shutdown.

    Returns:
      bool, True if shutdown succeeds, False if shutdown fails.
    """
    self._thread.join(5)
    return not self._thread.isAlive()

  def _serve(self):
    """Runs a fake server that can respond to a single request.

    This fake service emulates GETVAL with the option to inject errors.
      https://collectd.org/wiki/index.php/Plain_text_protocol#GETVAL

    The response is corrupted if the request contains one of these keywords.
      send_bad_reply -- the number of values is bad.
      send_empty_reply -- the request is received but no reply is sent.
      close_after_reply -- the connection is closed after sending the number of
          results to client.
    """
    # E1101: attribute access for nonexisting member. pylint 0.21.1 rejects
    # the calls to sendall. Disable this check because the method is present.
    # pylint: disable=E1101
    # Wait for a connection
    client, _ = self._sock.accept()
    request = check_collectd_mlab.sock_readline(client)

    # Send "number of values" response.
    if 'send_bad_reply' in request:
      client.sendall('not-a-number junk\n')
    elif 'send_empty_reply' in request:
      client.sendall('\n')
    else:
      client.sendall('1 junk\n')

    # Send "actual values" response.
    if 'close_after_reply' in request:
      # Skip the response, so client read will fail.
      client.close()
      return
    try:
      client.sendall('default=1.0\n')
    except socket.error:
      # Incase of sigpipe due to client closing connection first.
      pass
    # pylint: enable=E1101
    client.close()
    self._sock.close()


class MlabNagiosCollectdTests(unittest.TestCase):

  def setUp(self):
    self._testdata_dir = os.path.join(
        os.path.dirname(check_collectd_mlab.__file__), 'testdata')
    self.fake_socket = os.path.join(self._testdata_dir, 'fake_socket')
    self.fake_collectd = FakeCollectd(self.fake_socket)
    self.fake_collectd.start()

  def tearDown(self):
    self.assertTrue(self.fake_collectd.shutdown())

  def testunit_sock_sendcmd(self):
    check_collectd_mlab.HOSTNAME = 'my.hostname'
    sock = check_collectd_mlab.sock_connect(self.fake_socket)

    returned_value = check_collectd_mlab.sock_sendcmd(sock, 'GETVAL "whatever"')

    self.assertEqual(returned_value, 1)

  def testunit_sock_sendcmd_WHEN_receive_bad_reply_RETURNS_zero(self):
    check_collectd_mlab.HOSTNAME = 'my.hostname'
    sock = check_collectd_mlab.sock_connect(self.fake_socket)

    returned_value = check_collectd_mlab.sock_sendcmd(sock, 'send_bad_reply')

    self.assertEqual(returned_value, 0)

  def testunit_assert_collectd_responds_WHEN_sock_sendcmd_fails(self):
    check_collectd_mlab.COLLECTD_PID = (
        os.path.join(self._testdata_dir, 'fake_pid'))
    check_collectd_mlab.COLLECTD_UNIXSOCK = self.fake_socket
    check_collectd_mlab.HOSTNAME = 'send_empty_reply'

    self.assertRaises(
        check_collectd_mlab.SocketSendCommandCriticalError,
        check_collectd_mlab.assert_collectd_responds)

  def testunit_assert_collectd_responds_WHEN_sock_readline_fails(self):
    check_collectd_mlab.COLLECTD_PID = (
        os.path.join(self._testdata_dir, 'fake_pid'))
    check_collectd_mlab.COLLECTD_UNIXSOCK = self.fake_socket
    check_collectd_mlab.HOSTNAME = 'close_after_reply'

    self.assertRaises(
        check_collectd_mlab.SocketReadlineCriticalError,
        check_collectd_mlab.assert_collectd_responds)


class MlabNagiosTests(unittest.TestCase):

  def setUp(self):
    self._testdata_dir = os.path.join(
        os.path.dirname(check_collectd_mlab.__file__), 'testdata')

  def testunit_sock_connect_WHEN_no_socket_RAISES_CriticalError(self):
    self.assertRaises(
        check_collectd_mlab.SocketConnectionCriticalError,
        check_collectd_mlab.sock_connect, 'no_socket_name')

  def testunit_sock_readline_WHEN_socket_error_RETURNS_empty_string(self):
    mock_sock = mock.Mock(spec_set=socket.socket)
    mock_sock.recv.side_effect = socket.error('fake error')

    returned_value = check_collectd_mlab.sock_readline(mock_sock)

    self.assertEqual(returned_value, '')
    self.assertTrue(mock_sock.recv.called)

  def testunit_assert_collectd_installed_WHEN_bin_missing_RAISES_CriticalError(
      self):
    check_collectd_mlab.COLLECTD_BIN = 'does_not_exist'

    self.assertRaises(
        check_collectd_mlab.MissingBinaryCriticalError,
        check_collectd_mlab.assert_collectd_installed)

  def testunit_assert_collectd_installed_WHEN_bad_socket_RAISES_CriticalError(
      self):
    check_collectd_mlab.COLLECTD_BIN = (
        os.path.join(self._testdata_dir, 'fake_bin'))
    check_collectd_mlab.COLLECTD_UNIXSOCK = 'does_not_exist'

    self.assertRaises(
        check_collectd_mlab.MissingSocketCriticalError,
        check_collectd_mlab.assert_collectd_installed)

  @mock.patch('os.access')
  def testunit_assert_collectd_responds_WHEN_filesystem_is_readonly(
      self, mock_access):
    # Mock out os.access to guarantee that write access is rejected.
    mock_access.return_value = False

    self.assertRaises(
        check_collectd_mlab.ReadonlyFilesystemCriticalError,
        check_collectd_mlab.assert_collectd_responds)
    self.assertTrue(mock_access.called)

  def testunit_assert_collectd_responds_WHEN_sock_connect_fails(self):
    check_collectd_mlab.COLLECTD_PID = (
        os.path.join(self._testdata_dir, 'fake_pid'))
    check_collectd_mlab.COLLECTD_UNIXSOCK = 'does_not_exist'

    self.assertRaises(
        check_collectd_mlab.SocketConnectionCriticalError,
        check_collectd_mlab.assert_collectd_responds)

  def testunit_assert_collectd_vsys_setup_WHEN_vsys_backend_is_missing(self):
    check_collectd_mlab.VSYSPATH_BACKEND = 'does_not_exist'

    self.assertRaises(
        check_collectd_mlab.MissingVsysBackendCriticalError,
        check_collectd_mlab.assert_collectd_vsys_setup)

  def testunit_assert_collectd_vsys_setup_WHEN_vsys_slice_is_missing(self):
    check_collectd_mlab.VSYSPATH_BACKEND = os.path.join(
        self._testdata_dir, 'fake_backend')
    check_collectd_mlab.VSYSPATH_SLICE = 'does_not_exist'

    self.assertRaises(
        check_collectd_mlab.MissingVsysFrontendCriticalError,
        check_collectd_mlab.assert_collectd_vsys_setup)

  def testunit_assert_collectd_vsys_setup_WHEN_vsys_acl_is_missing(self):
    check_collectd_mlab.VSYSPATH_BACKEND = os.path.join(
        self._testdata_dir, 'fake_backend')
    check_collectd_mlab.VSYSPATH_SLICE = os.path.join(
        self._testdata_dir, 'fake_slice')
    check_collectd_mlab.VSYSPATH_ACL = 'does_not_exist'

    self.assertRaises(
        check_collectd_mlab.MissingVsysAclCriticalError,
        check_collectd_mlab.assert_collectd_vsys_setup)

  def testunit_assert_collectd_vsys_setup_WHEN_acl_incomplete(self):
    check_collectd_mlab.VSYSPATH_BACKEND = os.path.join(
        self._testdata_dir, 'fake_backend')
    check_collectd_mlab.VSYSPATH_SLICE = os.path.join(
        self._testdata_dir, 'fake_slice')
    check_collectd_mlab.VSYSPATH_ACL = os.path.join(
        self._testdata_dir, 'fake_acl')

    self.assertRaises(
        check_collectd_mlab.MissingSliceFromVsysAclCriticalError,
        check_collectd_mlab.assert_collectd_vsys_setup)

  @mock.patch('subprocess.Popen')
  def testcover_run_collectd_nagios(self, mock_popen):
    mock_popen.return_value.wait.return_value = 2

    returned_value = check_collectd_mlab.run_collectd_nagios(
        'host', 'metric', 'value', 'warning', 'critical')

    self.assertEqual(returned_value, 2)
    self.assertTrue(mock_popen.called)

  @mock.patch('check_collectd_mlab.run_collectd_nagios')
  def testcover_assert_collectd_nagios_levels(self, mock_run_collectd_nagios):
    # This is not ideal. But, it's just a coverage test.
    # Non-zero values cause a failue. Cause each call to to fail in sequence.
    mock_run_collectd_nagios.side_effect = [1]
    self.assertRaises(check_collectd_mlab.NagiosStateError,
                      check_collectd_mlab.assert_collectd_nagios_levels)

    mock_run_collectd_nagios.side_effect = [0, 1]
    self.assertRaises(check_collectd_mlab.NagiosStateError,
                      check_collectd_mlab.assert_collectd_nagios_levels)

    mock_run_collectd_nagios.side_effect = [0, 0, 1]
    self.assertRaises(check_collectd_mlab.NagiosStateError,
                      check_collectd_mlab.assert_collectd_nagios_levels)

    mock_run_collectd_nagios.side_effect = [0, 0, 0, 1]
    self.assertRaises(check_collectd_mlab.NagiosStateError,
                      check_collectd_mlab.assert_collectd_nagios_levels)

  def teststub_assert_disk_last_sync_time(self):
    check_collectd_mlab.assert_disk_last_sync_time()

  @mock.patch('check_collectd_mlab.assert_collectd_installed')
  @mock.patch('check_collectd_mlab.assert_collectd_responds')
  @mock.patch('check_collectd_mlab.assert_collectd_vsys_setup')
  @mock.patch('check_collectd_mlab.assert_collectd_nagios_levels')
  @mock.patch('check_collectd_mlab.assert_disk_last_sync_time')
  def testcover_check_collectd(
      self, mock_last_sync_time, mock_collectd_nagios_levels, mock_vsys_setup,
      mock_collectd_responds, mock_collectd_installed):

    (state, _) = check_collectd_mlab.check_collectd()

    self.assertEqual(state, check_collectd_mlab.STATE_OK)
    self.assertTrue(mock_collectd_installed.called)
    self.assertTrue(mock_collectd_responds.called)
    self.assertTrue(mock_vsys_setup.called)
    self.assertTrue(mock_collectd_nagios_levels.called)
    self.assertTrue(mock_last_sync_time.called)

  @mock.patch('check_collectd_mlab.assert_collectd_installed')
  @mock.patch('check_collectd_mlab.assert_collectd_responds')
  @mock.patch('check_collectd_mlab.assert_collectd_vsys_setup')
  @mock.patch('check_collectd_mlab.assert_collectd_nagios_levels')
  @mock.patch('check_collectd_mlab.assert_disk_last_sync_time')
  def testcover_check_collectd_WHEN_nagios_error(
      self, mock_last_sync_time, mock_collectd_nagios_levels, mock_vsys_setup,
      mock_collectd_responds, mock_collectd_installed):
    mock_collectd_nagios_levels.side_effect = (
        check_collectd_mlab.NagiosStateError('error'))

    (state, message) = check_collectd_mlab.check_collectd()

    self.assertEqual(state, check_collectd_mlab.STATE_UNKNOWN)
    self.assertEqual(message, 'error')
    self.assertTrue(mock_collectd_installed.called)
    self.assertTrue(mock_collectd_responds.called)
    self.assertTrue(mock_vsys_setup.called)
    self.assertTrue(mock_collectd_nagios_levels.called)
    self.assertTrue(mock_last_sync_time.called)

  @mock.patch('check_collectd_mlab.assert_collectd_installed')
  def testcover_check_collectd_WHEN_state_critical(
      self, mock_collectd_installed):
    mock_collectd_installed.side_effect = (
        check_collectd_mlab.CriticalError('fail'))

    (state, _) = check_collectd_mlab.check_collectd()

    self.assertEqual(state, check_collectd_mlab.STATE_CRITICAL)
    self.assertTrue(mock_collectd_installed.called)

  def testunit_alarm(self):
    check_collectd_mlab.init_alarm(1)
    try:
      time.sleep(5)
      self.fail('Alarm did not trigger.')  # pragma: no cover.
    except check_collectd_mlab.TimeoutError:
      pass
    check_collectd_mlab.cancel_alarm()

  @mock.patch('sys.stdout')
  @mock.patch('check_collectd_mlab.check_collectd')
  def testcover_main(self, mock_check_status, mock_stdout):
    mock_check_status.return_value = (check_collectd_mlab.STATE_OK, 'ok')
    self.assertRaises(SystemExit, check_collectd_mlab.main)
    self.assertTrue(len(mock_stdout.mock_calls) > 0)
    self.assertTrue(mock_check_status.called)

  @mock.patch('sys.stdout')
  @mock.patch('check_collectd_mlab.check_collectd')
  def testcover_main_WHEN_timeout(self, mock_check_status, mock_stdout):
    mock_check_status.side_effect = check_collectd_mlab.TimeoutError('timeout')
    self.assertRaises(SystemExit, check_collectd_mlab.main)
    self.assertTrue(len(mock_stdout.mock_calls) > 0)
    self.assertTrue(mock_check_status.called)


if __name__ == "__main__":
  unittest.main()
