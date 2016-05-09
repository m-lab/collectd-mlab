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

import io
import os
import socket
import time
import unittest2 as unittest

# Third-party modules.
import mock

# Module under test.
import check_collectd_mlab

# R0904: Too many public methods. Hard to avoid for unit tests.
# pylint: disable=R0904


class FakeSocketIO(object):
    """An in-memory, IO object with a socket-like interface."""

    def __init__(self, initial_value=''):
        self._writer = io.BytesIO()
        self._reader = io.BytesIO(initial_value)

    def recv(self, count=-1):
        """Reads count bytes from socket, or until EOF when count is -1."""
        return self._reader.read(count)

    def send(self, message):
        """Writes message to socket."""
        return self._writer.write(message)

    def getvalue(self):
        """Returns all bytes written to the socket."""
        return self._writer.getvalue()


class MLabNagiosSocketTests(unittest.TestCase):

    def testunit_sock_sendcmd_RETURNS_successfully(self):
        fake_sock = FakeSocketIO('1 default reply\n')

        returned_value = check_collectd_mlab.sock_sendcmd(fake_sock,
                                                          'GETVAL "whatever"')

        self.assertEqual(returned_value, 1)
        self.assertEqual(fake_sock.getvalue(), 'GETVAL "whatever"\n')

    def testunit_sock_sendcmd_WHEN_receive_bad_reply_RETURNS_zero(self):
        fake_sock = FakeSocketIO('not-a-number junk\n')

        returned_value = check_collectd_mlab.sock_sendcmd(fake_sock,
                                                          'GETVAL "whatever"')

        self.assertEqual(returned_value, 0)
        self.assertEqual(fake_sock.getvalue(), 'GETVAL "whatever"\n')

    def testunit_sock_connect_WHEN_invalid_socket_name_RAISES_error(self):
        with self.assertRaises(check_collectd_mlab.SocketConnectionError):
            check_collectd_mlab.sock_connect('invalid_socket_name')

    def testunit_sock_readline_WHEN_socket_error_RAISES_error(self):
        mock_sock = mock.Mock(spec_set=socket.socket)
        mock_sock.recv.side_effect = socket.error('fake error')

        with self.assertRaises(check_collectd_mlab.SocketReadlineError):
            check_collectd_mlab.sock_readline(mock_sock)


class MLabCollectdAssertionTests(unittest.TestCase):

    def setUp(self):
        self.testdata_dir = os.path.join(
            os.path.dirname(check_collectd_mlab.__file__), 'testdata')
        check_collectd_mlab.COLLECTD_BIN = (
            os.path.join(self.testdata_dir, 'fake_collectd_bin'))
        check_collectd_mlab.COLLECTD_NAGIOS = (
            os.path.join(self.testdata_dir, 'fake_nagios_bin'))
        check_collectd_mlab.COLLECTD_PID = (
            os.path.join(self.testdata_dir, 'fake_pid'))
        check_collectd_mlab.COLLECTD_UNIXSOCK = (
            os.path.join(self.testdata_dir, 'fake_socket'))
        check_collectd_mlab.VSYSPATH_BACKEND = (
            os.path.join(self.testdata_dir, 'fake_backend'))
        check_collectd_mlab.VSYSPATH_SLICE = (
            os.path.join(self.testdata_dir, 'fake_slice'))
        check_collectd_mlab.SNMP_COMMUNITY = (
            os.path.join(self.testdata_dir, 'fake_snmp_community'))

    @mock.patch('check_collectd_mlab.sock_connect')
    def testunit_assert_collectd_responds_WHEN_sock_sendcmd_fails(
            self, mock_sock_connect):
        """Fails when sending a command after successfully creating a connection."""
        mock_sock = mock.Mock(spec_set=socket.socket)
        mock_sock.send.side_effect = socket.error('fake socket error')
        mock_sock_connect.return_value = mock_sock

        with self.assertRaises(check_collectd_mlab.SocketSendCommandError):
            check_collectd_mlab.assert_collectd_responds()

        mock_sock_connect.assert_called_with(
            check_collectd_mlab.COLLECTD_UNIXSOCK)

    @mock.patch('check_collectd_mlab.sock_connect')
    def testunit_assert_collectd_responds_WHEN_sock_readline_fails(
            self, mock_sock_connect):
        """Fails when reading from socket after a successful connection."""
        # After sending a default reply, the fake socket reaches EOF.
        mock_sock_connect.return_value = FakeSocketIO('1 default reply\n')

        with self.assertRaises(check_collectd_mlab.SocketReadlineError):
            check_collectd_mlab.assert_collectd_responds()

        mock_sock_connect.assert_called_with(
            check_collectd_mlab.COLLECTD_UNIXSOCK)

    @mock.patch('os.access')
    def testunit_assert_collectd_responds_WHEN_filesystem_is_readonly(
            self, mock_access):
        # Mock out os.access to guarantee that write access is rejected.
        mock_access.return_value = False

        with self.assertRaises(check_collectd_mlab.ReadonlyFilesystemError):
            check_collectd_mlab.assert_collectd_responds()

        self.assertTrue(mock_access.called)

    def testunit_assert_collectd_responds_WHEN_sock_connect_fails(self):
        """Fails when creating a socket connection."""
        check_collectd_mlab.COLLECTD_UNIXSOCK = 'does_not_exist'

        with self.assertRaises(check_collectd_mlab.SocketConnectionError):
            check_collectd_mlab.assert_collectd_responds()

    def testunit_assert_collectd_installed_WHEN_bin_missing_RAISES_error(self):
        check_collectd_mlab.COLLECTD_BIN = 'does_not_exist'

        with self.assertRaises(check_collectd_mlab.MissingBinaryError):
            check_collectd_mlab.assert_collectd_installed()

    def testunit_assert_collectd_installed_WHEN_nagios_bin_missing_error(self):
        check_collectd_mlab.COLLECTD_NAGIOS = (
            os.path.join(self.testdata_dir, 'does_not_exist'))

        with self.assertRaises(check_collectd_mlab.MissingNagiosBinaryError):
            check_collectd_mlab.assert_collectd_installed()

    def testunit_assert_collectd_installed_WHEN_bad_socket_RAISES_error(self):
        check_collectd_mlab.COLLECTD_UNIXSOCK = 'does_not_exist'

        with self.assertRaises(check_collectd_mlab.MissingSocketError):
            check_collectd_mlab.assert_collectd_installed()

    def testunit_assert_collectd_installed_WHEN_missing_community_RAISES_error(
            self):
        check_collectd_mlab.SNMP_COMMUNITY = 'does_not_exist'

        with self.assertRaises(check_collectd_mlab.MissingSNMPCommunityError):
            check_collectd_mlab.assert_collectd_installed()

    def testunit_assert_collectd_installed_WHEN_missing_updated_RAISES_error(
            self):
        # Use default value for check_collectd_mlab.SNMP_COMMUNITY.
        with self.assertRaises(
                check_collectd_mlab.MissingUpdatedSNMPCommunityError):
            check_collectd_mlab.assert_collectd_installed()

    def testunit_assert_collectd_vsys_setup_WHEN_vsys_backend_is_missing(self):
        check_collectd_mlab.VSYSPATH_BACKEND = 'does_not_exist'

        with self.assertRaises(check_collectd_mlab.MissingVsysBackendError):
            check_collectd_mlab.assert_collectd_vsys_setup()

    def testunit_assert_collectd_vsys_setup_WHEN_vsys_slice_is_missing(self):
        check_collectd_mlab.VSYSPATH_SLICE = 'does_not_exist'

        with self.assertRaises(check_collectd_mlab.MissingVsysFrontendError):
            check_collectd_mlab.assert_collectd_vsys_setup()

    def testunit_assert_collectd_vsys_setup_WHEN_vsys_acl_is_missing(self):
        check_collectd_mlab.VSYSPATH_ACL = 'does_not_exist'

        with self.assertRaises(check_collectd_mlab.MissingVsysAclError):
            check_collectd_mlab.assert_collectd_vsys_setup()

    def testunit_assert_collectd_vsys_setup_WHEN_acl_incomplete(self):
        check_collectd_mlab.VSYSPATH_ACL = os.path.join(
            self.testdata_dir, 'acl_missing_slice_name')

        with self.assertRaises(
                check_collectd_mlab.MissingSliceFromVsysAclError):
            check_collectd_mlab.assert_collectd_vsys_setup()


class MLabNagiosTests(unittest.TestCase):

    @mock.patch('check_collectd_mlab.run_collectd_nagios')
    def testcover_assert_collectd_nagios_levels(self, mock_run_collectd_nagios):
        # This is not ideal. But, it's just a coverage test.
        # Non-zero values cause a failure. So, cause each call to fail in
        # sequence.
        mock_run_collectd_nagios.side_effect = [1]
        with self.assertRaises(check_collectd_mlab.NagiosStateError):
            check_collectd_mlab.assert_collectd_nagios_levels()

        mock_run_collectd_nagios.side_effect = [0, 1]
        with self.assertRaises(check_collectd_mlab.NagiosStateError):
            check_collectd_mlab.assert_collectd_nagios_levels()

        mock_run_collectd_nagios.side_effect = [0, 0, 1]
        with self.assertRaises(check_collectd_mlab.NagiosStateError):
            check_collectd_mlab.assert_collectd_nagios_levels()

        mock_run_collectd_nagios.side_effect = [0, 0, 0, 1]
        with self.assertRaises(check_collectd_mlab.NagiosStateError):
            check_collectd_mlab.assert_collectd_nagios_levels()

        mock_run_collectd_nagios.side_effect = [0, 0, 0, 0, 1]
        with self.assertRaises(check_collectd_mlab.NagiosStateError):
            check_collectd_mlab.assert_collectd_nagios_levels()

    @mock.patch('subprocess.Popen')
    def testcover_run_collectd_nagios(self, mock_popen):
        mock_popen.return_value.wait.return_value = 2

        returned_value = check_collectd_mlab.run_collectd_nagios(
            'host', 'metric', 'value', 'warning', 'critical')

        self.assertEqual(returned_value, 2)
        self.assertTrue(mock_popen.called)

    @mock.patch('check_collectd_mlab.assert_collectd_installed')
    @mock.patch('check_collectd_mlab.assert_collectd_responds')
    @mock.patch('check_collectd_mlab.assert_collectd_vsys_setup')
    @mock.patch('check_collectd_mlab.assert_collectd_nagios_levels')
    @mock.patch('check_collectd_mlab.assert_disk_last_sync_time')
    def testcover_check_collectd(
            self, mock_last_sync_time, mock_collectd_nagios_levels,
            mock_vsys_setup, mock_collectd_responds, mock_collectd_installed):
        state, _ = check_collectd_mlab.check_collectd()

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
            self, mock_last_sync_time, mock_collectd_nagios_levels,
            mock_vsys_setup, mock_collectd_responds, mock_collectd_installed):
        mock_collectd_nagios_levels.side_effect = (
            check_collectd_mlab.NagiosStateError('error'))

        state, message = check_collectd_mlab.check_collectd()

        self.assertEqual(state, check_collectd_mlab.STATE_UNKNOWN)
        self.assertEqual(message, 'error')
        self.assertTrue(mock_collectd_installed.called)
        self.assertTrue(mock_collectd_responds.called)
        self.assertTrue(mock_vsys_setup.called)
        self.assertTrue(mock_collectd_nagios_levels.called)
        self.assertTrue(mock_last_sync_time.called)

    @mock.patch('check_collectd_mlab.assert_collectd_installed')
    def testcover_check_collectd_WHEN_state_critical(self,
                                                     mock_collectd_installed):
        mock_collectd_installed.side_effect = (
            check_collectd_mlab.CriticalError('fail'))

        state, _ = check_collectd_mlab.check_collectd()

        self.assertEqual(state, check_collectd_mlab.STATE_CRITICAL)
        self.assertTrue(mock_collectd_installed.called)

    def testunit_alarm(self):
        with check_collectd_mlab.AlarmAfterTimeout(1):
            try:
                time.sleep(5)
                self.fail('Alarm did not trigger.')  # pragma: no cover.
            except check_collectd_mlab.TimeoutError:
                pass

    @mock.patch('sys.stdout')
    @mock.patch('check_collectd_mlab.check_collectd')
    def testcover_main(self, mock_check_status, mock_stdout):
        mock_check_status.return_value = (check_collectd_mlab.STATE_OK, 'ok')

        with self.assertRaises(SystemExit):
            check_collectd_mlab.main()

        self.assertTrue(mock_stdout.write.called)
        self.assertTrue(mock_check_status.called)

    @mock.patch('sys.stdout')
    @mock.patch('check_collectd_mlab.check_collectd')
    def testcover_main_WHEN_timeout(self, mock_check_status, mock_stdout):
        mock_check_status.side_effect = check_collectd_mlab.TimeoutError(
            'timeout')

        with self.assertRaises(SystemExit):
            check_collectd_mlab.main()

        self.assertTrue(mock_stdout.write.called)
        self.assertTrue(mock_check_status.called)


if __name__ == "__main__":
    unittest.main()
