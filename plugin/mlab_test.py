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
"""Unit tests for collectd-mlab."""

import os
import sys
import threading
import time
import unittest

# third-party
import mock

_DEBUG = False

# W0212: Access to a protected member, required to access mlab global values.
# pylint: disable=W0212


def log(msg, *args):
  """Prints additional information when _DEBUG is true."""
  if _DEBUG:  
    # Only used during interactive testing. So, no coverage needed.
    print threading.current_thread().name, msg % args  # pragma: no cover.


class MockCollectd(mock.Mock):
  """A mock to replace the collectd module for the mlab plugin.
  
  The collectd module provides three methods for reporting to syslog. Adding
  these methods to the mock permits more verbose output during tests when
  mlab_test._DEBUG is True.
  """

  def info(self, msg):
    log(msg)

  def error(self, msg):
    log(msg)

  def warning(self, msg):
    log(msg)


# NOTE: the 'collectd' module (imported by 'mlab' plugin) does not actually
# exist as a regular python module. The 'collectd' module can only be imported
# when the mlab module is loaded by collectd as a plugin. The following line
# adds a mock to sys.modules so that when the 'mlab' plugin attempts to load
# 'collectd' as a module, it returns this mock instead of raising ImportError.
sys.modules['collectd'] = MockCollectd()
import mlab


class FakeValues(object):
  """FakeValues emulates the collectd.Values class for mlab plugin testing.

  collectd.Values.dispatch is part of the collectd, python Values API.
  Dispatched values are sent to collectd and passed to all configured output
  plugins, such as RRD. However, to support testing we need to be able to
  inspect all dispatched values. FakeValues.dispatch makes this possible by
  saving all values reported by the mlab plugin.

  Typical usage will follow this pattern:

    @mock.patch('mlab.collectd.Values', new=FakeValues)
    def test_some_method(self):
      FakeValues.setup()
      # ... additional setup ...

      mlab.method_under_test()
      metrics = FakeValues.get_metrics()

      self.assertEqual(metrics.get('<expected-metric-key>'), expected_value)
      # ... additional asserts ...
      
  The test begins by calling FakeValues.setup(). Because the mlab plugin
  creates a new collectd.Value for each metric reported, and because we want to
  record each value for verification after testing, FakeValues saves each
  dispatched value in a class attribute (which is the same for all instances of
  the class). FakeValues.setup clears this class attribute at the start of a 
  new test.

  After running the method_under_test, all metrics dispatched by that method
  can be retrieved using FakeValues.get_metrics(). The returned value is a
  standard python dict.

  The dict keys consist of the "expected-metric" in the form:

     <host>/<plugin>[/<plugin_instance>]/<type>[/<type_instance>]

  And, the value is the actual, dispatched values array.
  """

  # NOTE: A new FakeValues instance is created for every dispatched value.
  # This class attribute is accessible to every instance, and this is where
  # dispatch saves values from all instances.
  _VALUE_MAP = {}

  @classmethod
  def setup(cls):
    """FakeValues.setup() should be called at the beginning of each test.

    Because FakeValues relies on a class attribute to record all dispatched
    values, setup() must be called to clear this attribute across tests.
    """
    cls._VALUE_MAP = {}

  @classmethod
  def get_metrics(cls):
    """Returns a dict of <metric-key>-to-<values> for all dispatched values."""
    return cls._VALUE_MAP.copy()

  def dispatch(self):
    """Saves values recorded during tests to verify after testing."""

    # NOTE: The order here makes metric_keys more readable. Every metric should
    # have host, plugin, and type assigned (if not, there will be an error).
    keys = []
    keys.append(self.host)
    keys.append(self.plugin)
    keys.append(getattr(self, 'plugin_instance', None))  # may be unassigned.
    keys.append(self.type)
    keys.append(getattr(self, 'type_instance', None))  # may be unassigned.

    metric_key = '/'.join([ key for key in keys if key is not None ])
    if metric_key in self._VALUE_MAP:  # pragma: no cover.
      # All metrics should be distinct. So, a duplicate key is an error.
      raise ValueError('Dispatching duplicate metric key: %s' % metric_key)

    self._VALUE_MAP[metric_key] = self.values


class FakeVsysBackend(threading.Thread):
  """FakeVsysBackend acts as a fake Vsys backend.

  See mlab.VsysFrontend for an overview of Vsys and the VsysFrontend class.

  Vsys is more sophisticated than this Fake, of course. But, its basic behavior
  is mimicked here. For testing, the sequence of FIFO opens looks like:

    1. backend opens target.in for reading, blocks.
    2. frontend opens target.in for writing (blocking or non-blocking).
    3. frontend opens target.out for reading, blocks.
    4. backend opens target.out for writing, blocks.
  
  FakeVsysBackend allows the caller to set up controlled request and response
  messages to test a frontend end to end. As well, if any file descriptor is
  unexpectedly closed or the backend misbehaves, different failures can occur.
  To inject these failure scenarios, FakeVsysBackend provides some special
  commands that produce predictable failures.

  == Custom Requests & Responses ==

  Callers can set custom request messages and responses. This is helpful for
  testing normal operations without injected errors.

  Example:
    backend = FakeVsysBackend(fifo_in, fifo_out)
    backend.set('custom_command', 'custom_response'):

    response = vsys.sendrecv('custom_command')
    assert(response == 'custom_response')

  == Fault Injection ==

  Command: shutdown_reader
  
    The shutdown_reader command closes the backend read file descriptor so
    that future frontend writes will fail (path 1 & 2 above). Caller should
    expect 'vsys.sendrecv' to return successfully, and for the next call to
    vsys.sendrecv to fail. To clean up after this command, the caller MUST call
    complete_shutdown_reader, or the backend thread will not exit, and the
    backend write file descriptor will not be closed which could affect future
    tests.
  
    Example:
      _ = vsys.sendrecv('shutdown_reader')
      self.assertRaises(mlab.VsysException, vsys.sendrecv, 'any_request')
      backend.complete_shutdown_reader()
  
  Command: shutdown_writer
  
    The shutdown_writer command closes the backend write file descriptor, so
    no response is written (or received by vsys.sendrecv). Also, as a result,
    any other thread with a corresponding open read file descriptor will
    receive EOF and exit (path 3 & 4 above). Because the vsys frontend reads 
    data using a timeout, it will either observe that the backend reader has
    died (EOF), or timeout waiting. In either case, it will raise an exception.
  
    Example:
      self.assertRaises(mlab.VsysException, vsys.sendrecv, 'shutdown_writer', 1)

  Command: send_empty_reply
  
    The send_empty_reply command behaves as a normal command, except that it
    returns an empty value in the response message. This simulates a response
    from a backend command being empty.
  
    Example:
      response = vsys.sendrecv('send_empty_reply')
      assert(response == '')
  
  Command: take_too_long
  
    The take_too_long command waits indefinitely before returning a reply.
    This is helpful for verifying vsys.sendrecv timeout behavior. This command
    simulates a situation where the backend has stopped responding as quickly
    as the frontend expects or requires. To clean up after this command, the
    caller MUST call complete_take_too_long, or the backend thread will not
    continue so will not be able to exit, which could affect later tests.

    Example:
      response = vsys.sendrecv('take_too_long', 1)
      backend.complete_take_too_long()
      assert(response == '')
  """

  def __init__(self, target):
    """Initializes a new VsysFrontend.

    Example usage:
      backend = FakeVsysBackend('target')
      backend.start()

    Args:
      target: str, name of vsys target. Uses mlab.get_vsys_fifo_names to
          translate target name to actual FIFO path names.
    """
    (self._fifo_in, self._fifo_out) = mlab.get_vsys_fifo_names(target)

    # Predefined request/response pairs with special meanings.
    self._responses = {'shutdown_reader': 'whatever',
                       'shutdown_writer': 'whatever',
                       'send_empty_reply': '',
                       'take_too_long': 'whatever'}

    self._writer_closed = False
    self._reader_closed = False
    self._complete_shutdown_reader = threading.Event()
    self._complete_take_too_long = threading.Event()

    # NOTE: When we override Thread.run (typical for derived class), the
    # 'coverage' utility cannot see it. So, as a work-around, assign target
    # in Thread.__init__ to achieve the same effect.
    super(FakeVsysBackend, self).__init__(target=self._runbackend)

    # Daemon threads don't require a join to clean up thread after exit.
    self.daemon = True

  def set(self, key, value):
    """Use the set method to assign request/response pairs during tests."""
    self._responses[key] = value

  def complete_take_too_long(self):
    """Must be called after sending 'take_too_long' command."""
    self._complete_take_too_long.set()

  def complete_shutdown_reader(self):
    """Must be called after sending 'shutdown_reader' command."""
    self._complete_shutdown_reader.set()

  def _runbackend(self):
    """Runs the handling loop for the backend thread."""

    log('backend: open %s in r', self._fifo_in)
    f_read = open(self._fifo_in, 'r')

    log('backend: open %s out w', self._fifo_out)
    f_write = open(self._fifo_out, 'w')

    while True:
      if self._reader_closed:
        self._complete_shutdown_reader.wait()
        break

      val = f_read.readline()
      log('backend: received %s', val)
      if not val:
        log('backend: eof')
        break

      if 'shutdown_reader' in val:
        # NOTE: the next attempt to by the frontend writer will fail.
        f_read.close()
        log('backend: reader closed')
        self._reader_closed = True

      elif 'shutdown_writer' in val:
        # NOTE: the frontend reader will receive EOF.
        f_write.close()
        log('backend: writer closed')
        self._writer_closed = True

      elif 'take_too_long' in val:
        self._complete_take_too_long.wait()
        break

      if not self._writer_closed:
        message_type = val.strip()
        response = self._responses.get(message_type, 'default')
        log('backend: writing: %s', response)
        f_write.write(response + '\n')
        f_write.flush()

    f_read.close()
    f_write.close()
    log('backend: exit')


class MlabCollectdPlugin_VsysFrontendTests(unittest.TestCase):

  def setUp(self):
    global mlab
    # NOTE: Reload the mlab module after each test.
    mlab = reload(mlab)
    self._testdata_dir = os.path.join(
        os.path.dirname(mlab.__file__), 'testdata')
    mlab._VSYS_FMT_IN = os.path.join(self._testdata_dir, '%s.in')
    mlab._VSYS_FMT_OUT = os.path.join(self._testdata_dir, '%s.out')
    (fifo_in, fifo_out) = mlab.get_vsys_fifo_names('mock_target')
    if not os.path.exists(fifo_in):
      os.mkfifo(fifo_in)
    if not os.path.exists(fifo_out):
      os.mkfifo(fifo_out)

  @mock.patch('os.open')
  def testunit_open_RAISES_OSError(
      self, mock_open):
    def side_effect(*unused_args):
      """Manages a two-step return value."""
      # Hi! welcome to an old version of the mock module. In this old version,
      # side_effect behavior is managed manually. For this test the second call
      # to os.open should raise an OSError. So, on the first call we reset the
      # side_effect reference to 'second_call' which raises the exception we
      # need the second time os.open is called.
      #
      # More recent versions of mock allow side_effect to be an array, and
      # automatically raises objects of type Exception.
      # 
      # If mock is updated, this will keep working, but a better approach is:
      #     mock_open.side_effect = [3, OSError(-1, 'Forced OS error')]
      def second_call(*unused_args):
        """Raises OSError."""
        raise OSError(-1, 'Forced OS error')
      mock_open.side_effect = second_call
      return 3  # Any integer like a file descriptor from os.open.
    mock_open.side_effect = side_effect
    (fifo_in, fifo_out) = mlab.get_vsys_fifo_names('mock_target')
    expected_calls = [ mock.call(fifo_in, os.O_WRONLY),
                       mock.call(fifo_out, os.O_RDONLY) ]
    vsys = mlab.VsysFrontend('mock_target', open_nonblock=False)

    self.assertRaises(mlab.VsysOpenException, vsys.open)
    self.assertEqual(mock_open.mock_calls, expected_calls)

  def testunit_init_WHEN_no_target_RAISES_VsysCreateException(self):
    self.assertRaises(mlab.VsysCreateException, mlab.VsysFrontend, 'no_target')

  def testunit_open_WHEN_no_reader_RAISES_VsysOpenException(self):
    vsys = mlab.VsysFrontend('mock_target')

    self.assertRaises(mlab.VsysOpenException, vsys.open)

  def testunit_sendrecv_WHEN_recving_bad_extra_data_RETURNS_good_reply(self):
    expected_response = '{"version": 1, "data": {"rss": 3000000}}'
    backend = FakeVsysBackend('mock_target')
    backend.set('fake_request', 'bad-extra-data\n' + expected_response)
    backend.start()

    vsys = mlab.VsysFrontend('mock_target', open_nonblock=False)
    vsys.open()
    received_result = vsys.sendrecv('fake_request')
    vsys.close()
    backend.join()

    self.assertEqual(received_result, expected_response)
    self.assertFalse(backend.isAlive())

  def testunit_sendrecv_RETURNS_correctly(self):
    expected_response = '{"version": 1, "data": {"rss": 3000000}}'
    backend = FakeVsysBackend('mock_target')
    backend.set('fake_request', expected_response)
    backend.start()

    vsys = mlab.VsysFrontend('mock_target', open_nonblock=False)
    vsys.open()
    received_result = vsys.sendrecv('fake_request')
    vsys.close()
    backend.join()

    self.assertEqual(received_result, expected_response)
    self.assertFalse(backend.isAlive())

  def testunit_sendrecv_AFTER_close_RAISES_VsysException(
      self):
    backend = FakeVsysBackend('mock_target')
    backend.set('fake_request', 'will not be returned')
    backend.start()

    vsys = mlab.VsysFrontend('mock_target', open_nonblock=False)
    vsys.open()
    vsys.close()
    self.assertRaises(mlab.VsysException, vsys.sendrecv, 'fake_request')

    backend.join()
    self.assertFalse(backend.isAlive())

  def testunit_sendrecv_AFTER_shutdown_reader_RAISES_VsysException(self):
    backend = FakeVsysBackend('mock_target')
    backend.set('fake_request', 'will not be returned')
    backend.start()

    vsys = mlab.VsysFrontend('mock_target', open_nonblock=False)
    vsys.open()
    _ = vsys.sendrecv('shutdown_reader')
    self.assertRaises(mlab.VsysException, vsys.sendrecv, 'fake_request')

    backend.complete_shutdown_reader()
    vsys.close()
    backend.join()
    self.assertFalse(backend.isAlive())

  def testunit_sendrecv_WHEN_shutdown_writer_RAISES_VsysException(self):
    backend = FakeVsysBackend('mock_target')
    backend.start()

    vsys = mlab.VsysFrontend('mock_target', open_nonblock=False)
    vsys.open()
    self.assertRaises(mlab.VsysException, vsys.sendrecv, 'shutdown_writer', 1)

    vsys.close()
    backend.join()
    self.assertFalse(backend.isAlive())

  def testunit_sendrecv_WHEN_request_take_too_long_RAISES_VsysException(self):
    backend = FakeVsysBackend('mock_target')
    backend.start()

    vsys = mlab.VsysFrontend('mock_target', open_nonblock=False)
    vsys.open()
    self.assertRaises(mlab.VsysException, vsys.sendrecv, 'take_too_long', 1)

    backend.complete_take_too_long()
    vsys.close()
    backend.join()
    self.assertFalse(backend.isAlive())

  def testunit_sendrecv_WHEN_send_empty_reply_RAISES_VsysException(self):
    backend = FakeVsysBackend('mock_target')
    backend.start()

    vsys = mlab.VsysFrontend('mock_target', open_nonblock=False)
    vsys.open()
    returned_value = vsys.sendrecv('send_empty_reply')

    vsys.close()
    backend.join()
    self.assertEqual(returned_value, '')
    self.assertFalse(backend.isAlive())

  @mock.patch('os.read')
  def testunit_sendrecv_WHEN_read_fails_RAISES_VsysException(
      self, mock_read):
    mock_read.side_effect = OSError(-1, 'fake os error')
    backend = FakeVsysBackend('mock_target')
    backend.set('fake_request', 'expected_response')
    backend.start()

    vsys = mlab.VsysFrontend('mock_target', open_nonblock=False)
    vsys.open()
    self.assertRaises(mlab.VsysException, vsys.sendrecv, 'fake_request', 1)
    vsys.close()
    backend.join()

    self.assertFalse(backend.isAlive())


class MlabCollectdPlugin_CoverageTests(unittest.TestCase):
  """Tests to increase coverage on minor methods."""

  def setUp(self):
    global mlab
    # NOTE: Reload the mlab module after each test.
    mlab = reload(mlab)
    self._testdata_dir = os.path.join(
        os.path.dirname(mlab.__file__), 'testdata')

  @mock.patch('mlab.collectd.info')
  def testcover_plugin_shutdown(self, mock_info):
    # Nothing to test now. But, if new logic is added to shutdown, these tests
    # should be updated.
    mlab.plugin_shutdown()

    mock_info.assert_called_once()

  @mock.patch('mlab.os.getpid')
  def testcover_plugin_initialize(self, mock_getpid):
    fake_pid = 1234
    mock_getpid.return_value = fake_pid
    expected_value = '/proc/%s/stat' % fake_pid

    mlab.plugin_initialize()

    self.assertEqual(mlab._PROC_PID_STAT, expected_value)

  @mock.patch('collectd.error')
  def testcover_submit_generic_WHEN_wrong_values_type(self, mock_error):
    # Wrong value types are logged and ignored.
    mlab.submit_generic('fake.host', 'plugin', 'type', 'wrong value type')

    mock_error.assert_called_once()


class MlabCollectdPlugin_UnitTests(unittest.TestCase):
  """Unit tests for collectd-mlab plugin."""

  def setUp(self):
    global mlab
    # NOTE: Reload the mlab module after each test.
    mlab = reload(mlab)
    self._testdata_dir = os.path.join(
        os.path.dirname(mlab.__file__), 'testdata')

  def testunit_plugin_configure(self):
    class MockCfg(object):
      def __init__(self, key, values=(), children=()):
        (self.key, self.values, self.children) = (key, values, children)
    # NOTE: the configuration passed to mlab plugin from collectd has all
    # directives as children of an empty root config object.
    root_config = MockCfg(
        '', (), (MockCfg('ExcludeSlice', ['pl_default']),
                 MockCfg('ExcludeSlice', ['pl_netflow']),
                 MockCfg('ExcludeSlice', ['pl_sfacm']),))
    expected_value = {'pl_default': True, 'pl_netflow': True, 'pl_sfacm': True}

    mlab.plugin_configure(root_config)

    self.assertEqual(mlab._config_exclude_slices, expected_value)

  def testunit_get_self_stats(self):
    test_stat_path = os.path.join(self._testdata_dir, 'proc_pid_stat')

    stats = mlab.get_self_stats(test_stat_path)

    self.assertEqual(stats['stime'], 1503.0)
    self.assertEqual(stats['utime'], 255.0)
    self.assertEqual(stats['vsize'], 1234321)

  def testunit_get_self_stats_WHEN_no_stat_file(self):
    test_stat_path = os.path.join(self._testdata_dir, 'no_stat')

    stats = mlab.get_self_stats(test_stat_path)

    self.assertEqual(stats, {})

  def testunit_get_self_stats_WHEN_bad_stat_data(self):
    test_stat_path = os.path.join(self._testdata_dir, 'stat_wrong')

    stats = mlab.get_self_stats(test_stat_path)

    self.assertEqual(stats, {})

  def testunit_read_system_uptime(self):
    mlab._PROC_UPTIME = os.path.join(self._testdata_dir, 'uptime')
    expected_value = 1050501.0

    returned_value = mlab.read_system_uptime()

    self.assertEqual(returned_value, expected_value)

  def testunit_read_system_uptime_WHEN_no_uptime_file(self):
    mlab._PROC_UPTIME = os.path.join(self._testdata_dir, 'no_uptime')
    expected_value = 0

    returned_value = mlab.read_system_uptime()

    self.assertEqual(returned_value, expected_value)

  def testunit_slicename_to_hostname_WITH_one_part(self):
    mlab._root_hostname = 'host.domain'
    expected_hostname = 'fakeslice.host.domain'

    returned_hostname = mlab.slicename_to_hostname('fakeslice')

    self.assertEqual(returned_hostname, expected_hostname)

  def testunit_slicename_to_hostname_WITH_two_parts(self):
    mlab._root_hostname = 'host.domain'
    expected_hostname = 'slice.fake.host.domain'

    returned_hostname = mlab.slicename_to_hostname('fake_slice')

    self.assertEqual(returned_hostname, expected_hostname)

  def testunit_slicename_to_hostname_WITH_three_parts(self):
    mlab._root_hostname = 'host.domain'
    expected_hostname = 'slice.name.fake.host.domain'

    returned_hostname = mlab.slicename_to_hostname('fake_slice_name')

    self.assertEqual(returned_hostname, expected_hostname)


class MlabCollectdPlugin_VsysTests(unittest.TestCase):
  """Test vsys methods for collectd-mlab plugin."""

  def setUp(self):
    global mlab
    # NOTE: Reload the mlab module after each test.
    mlab = reload(mlab)
    self._testdata_dir = os.path.join(
        os.path.dirname(mlab.__file__), 'testdata')

  def testunit_vsys_fifo_exists_WHEN_given_regular_file_RETURNS_False(self):
    filename = os.path.join(self._testdata_dir, 'uptime')

    self.assertFalse(mlab.vsys_fifo_exists(filename))

  @mock.patch('mlab.read_vsys_data_direct')
  def testunit_read_vsys_data_WHEN_missing_data_RETURNS_empty_result(
      self, mock_read_vsys_data_direct):
    mock_read_vsys_data_direct.return_value = {'version': 1} 
    
    returned_value = mlab.read_vsys_data('fake_script', 1)

    self.assertEqual(returned_value, {})

  @mock.patch('mlab.read_vsys_data_direct')
  def testunit_read_vsys_data_WHEN_missing_version_RETURNS_empty_result(
      self, mock_read_vsys_data_direct):
    mock_read_vsys_data_direct.return_value = {'data': {'rss': 2957312}}
    
    returned_value = mlab.read_vsys_data('fake_script', 2)

    self.assertEqual(returned_value, {})

  @mock.patch('mlab.read_vsys_data_direct')
  def testunit_read_vsys_data_WHEN_wrong_message_type_RETURNS_empty_result(
      self, mock_read_vsys_data_direct):
    mock_read_vsys_data_direct.return_value = {
        'data': 'x', 'version': 2, 'message_type': 'not_fake_script'}
    
    returned_value = mlab.read_vsys_data('fake_script', 2)

    self.assertEqual(returned_value, {})

  @mock.patch('mlab.read_vsys_data_direct')
  def testunit_read_vsys_data_WHEN_wrong_version_RETURNS_data_anyway(
      self, mock_read_vsys_data_direct):
    mock_read_vsys_data_direct.return_value = {'data': 'x', 'version': 1}
    
    returned_value = mlab.read_vsys_data('fake_script', 2)

    self.assertEqual(returned_value, 'x')

  @mock.patch('mlab.VsysFrontend')
  def testunit_read_vsys_data_direct_WHEN_VsysFrontend_RAISES_CreateException(
      self, mock_vsysfrontend):
    mlab._vs_vsys = None
    mock_vsysfrontend.side_effect = mlab.VsysCreateException('fake exception')

    returned_value = mlab.read_vsys_data_direct('fake_request')

    self.assertEqual(returned_value, {})

  def testunit_read_vsys_data_direct_WHEN_sendrecv_RAISES_VsysException(self):
    mock_vs = mock.Mock()
    mock_vs.sendrecv.side_effect = mlab.VsysException('fake exception')
    mlab._vs_vsys = mock_vs

    returned_value = mlab.read_vsys_data_direct('fake_request')

    self.assertEqual(returned_value, {})

  def testunit_read_vsys_data_direct_WHEN_sendrecv_RETURNS_empty_response(self):
    mock_vs = mock.Mock()
    mock_vs.sendrecv.return_value = ''
    mlab._vs_vsys = mock_vs

    returned_value = mlab.read_vsys_data_direct('fake_request')

    self.assertEqual(returned_value, {})

  def testunit_read_vsys_data_direct_WHEN_sendrecv_RETURNS_bad_json(self):
    mock_vs = mock.Mock()
    mock_vs.sendrecv.return_value = '{"data": '  # an incomplete json reply.
    mlab._vs_vsys = mock_vs

    returned_value = mlab.read_vsys_data_direct('fake_request')

    self.assertEqual(returned_value, {})

  @mock.patch('mlab.read_vsys_data')
  def testunit_init_vserver_xid_names(self, mock_read_vsys_data):
    expected_result = {'515': 'mlab_utility'}
    mock_read_vsys_data.return_value = expected_result

    mlab.init_vserver_xid_names()

    self.assertEqual(mlab._vs_xid_names, expected_result)


class MlabCollectdPlugin_MetricTests(unittest.TestCase):
  """Test metric methods for collectd-mlab plugin."""

  def setUp(self):
    global mlab
    # NOTE: Reload the mlab module after each test.
    mlab = reload(mlab)
    self._testdata_dir = os.path.join(
        os.path.dirname(mlab.__file__), 'testdata')

  @mock.patch('mlab.collectd.Values', new=FakeValues)
  @mock.patch('mlab.get_self_stats')
  @mock.patch('mlab.read_vsys_data')
  def testunit_report_meta_metrics(
      self, mock_read_vsys_data, mock_get_self_stats):
    FakeValues.setup()
    mlab._root_hostname = 'fake.host'
    test_stat_path = os.path.join(self._testdata_dir, 'proc_stat')
    expected_response = {
        "rss": 2902900, "vsize": 4904900, "stime": 1.0, "utime": 1.0}
    mock_read_vsys_data.return_value = expected_response
    mock_get_self_stats.return_value = expected_response

    mlab.report_meta_metrics(test_stat_path)
    metrics = FakeValues.get_metrics()

    self.assertEqual(
        metrics.get('fake.host/meta/collectd/process_cpu/user'), [1.0])
    self.assertEqual(
        metrics.get('fake.host/meta/collectd/process_cpu/system'), [1.0])
    self.assertEqual(
        metrics.get('fake.host/meta/collectd/process_memory/vm'), [4904900])
    self.assertEqual(
        metrics.get('fake.host/meta/collectd/process_memory/rss'), [2902900])
    self.assertEqual(
        metrics.get('fake.host/meta/backend/process_cpu/user'), [1.0])
    self.assertEqual(
        metrics.get('fake.host/meta/backend/process_cpu/system'), [1.0])
    self.assertEqual(
        metrics.get('fake.host/meta/backend/process_memory/vm'), [4904900])
    self.assertEqual(
        metrics.get('fake.host/meta/backend/process_memory/rss'), [2902900])

  @mock.patch('mlab.collectd.Values', new=FakeValues)
  def testunit_report_quota_for_vserver(self):
    FakeValues.setup()
    raw_dlimit = [374744, 10000000, 9450, -1, 2]  # used kb, total kb, ...
    expected_result = [374744000, 9625256000]  # used bytes, free bytes

    mlab.report_quota_for_vserver('fake.host', raw_dlimit)
    metrics = FakeValues.get_metrics()

    self.assertEqual(
        metrics.get('fake.host/storage/vs_quota_bytes/quota'), expected_result)

  @mock.patch('mlab.collectd.Values', new=FakeValues)
  def testunit_report_threads_for_vserver(self):
    FakeValues.setup()
    fake_uptime = 950000.0
    test_entry_path = os.path.join(self._testdata_dir, '515')

    mlab.report_threads_for_vserver('fake.host', test_entry_path, fake_uptime)
    metrics = FakeValues.get_metrics()

    self.assertEqual(
        metrics.get('fake.host/context/vs_threads_basic/running'), [0])
    self.assertEqual(
        metrics.get('fake.host/context/vs_threads_basic/other'), [19])
    self.assertEqual(
        metrics.get('fake.host/context/vs_uptime'), [948500.0])

  @mock.patch('mlab.collectd.Values', new=FakeValues)
  def testunit_report_limits_for_vserver(self):
    FakeValues.setup()
    test_entry_path = os.path.join(self._testdata_dir, '515')

    mlab.report_limits_for_vserver('fake.host', test_entry_path)
    metrics = FakeValues.get_metrics()

    self.assertEqual(
        metrics.get('fake.host/memory/vs_memory/vml'), [0.0])
    self.assertEqual(
        metrics.get('fake.host/memory/vs_memory/vm'), [192905216.0])
    self.assertEqual(
        metrics.get('fake.host/memory/vs_memory/anon'), [8388608.0])
    self.assertEqual(
        metrics.get('fake.host/memory/vs_memory/rss'), [20004864.0])
    self.assertEqual(
        metrics.get('fake.host/context/vs_vlimit/openfd'), [51.0])
    self.assertEqual(
        metrics.get('fake.host/context/vs_vlimit/files'), [218.0])
    self.assertEqual(
        metrics.get('fake.host/context/vs_vlimit/processes'), [19.0])
    self.assertEqual(
        metrics.get('fake.host/context/vs_vlimit/sockets'), [10.0])
    self.assertEqual(
        metrics.get('fake.host/context/vs_vlimit/locks'), [1.0])

  @mock.patch('mlab.collectd.Values', new=FakeValues)
  def testunit_report_network_for_vserver(self):
    FakeValues.setup()
    test_entry_path = os.path.join(self._testdata_dir, '515')

    mlab.report_network_for_vserver('fake.host', test_entry_path)
    metrics = FakeValues.get_metrics()

    self.assertEqual(
        metrics.get('fake.host/network/if_octets/ipv4'),
        [125176907, 9462020])
    self.assertEqual(
        metrics.get('fake.host/network/vs_network_syscalls/ipv4'),
        [38061, 3082])

  @mock.patch('mlab.collectd.Values', new=FakeValues)
  def testunit_report_cpuavg_for_system(self):
    FakeValues.setup()
    test_stat_path = os.path.join(self._testdata_dir, 'proc_stat')
    mlab._root_hostname = 'fake.host'

    mlab.report_cpuavg_for_system(test_stat_path)
    metrics = FakeValues.get_metrics()

    self.assertEqual(
        metrics.get('fake.host/cpu_total/cpu/user'), [3147080.0])
    self.assertEqual(
        metrics.get('fake.host/cpu_total/cpu/system'), [10233120.0])
    self.assertEqual(
        metrics.get('fake.host/cpu_total/cpu/idle'), [163426289.0])
    self.assertEqual(
        metrics.get('fake.host/cpu_total/cpu/nice'), [87438.0])
    self.assertEqual(
        metrics.get('fake.host/cpu_total/cpu/steal'), [0.0])
    self.assertEqual(
        metrics.get('fake.host/cpu_total/cpu/wait'), [632620.0])
    self.assertEqual(
        metrics.get('fake.host/cpu_total/cpu/softirq'), [559433.0])
    self.assertEqual(
        metrics.get('fake.host/cpu_total/cpu/interrupt'), [33350.0])
    self.assertEqual(
        metrics.get('fake.host/cpu_cores/gauge'), [2])

  @mock.patch('mlab.collectd.Values', new=FakeValues)
  def testunit_report_cpuavg_for_system_WHEN_path_is_wrong(self):
    FakeValues.setup()
    test_stat_path = os.path.join(self._testdata_dir, 'wrong_proc_stat')
    mlab._root_hostname = 'fake.host'

    mlab.report_cpuavg_for_system(test_stat_path)
    metrics = FakeValues.get_metrics()

    self.assertEqual(0, len(metrics.keys()))

  @mock.patch('mlab.collectd.Values', new=FakeValues)
  def testunit_report_cpu_for_vserver(self):
    FakeValues.setup()
    test_entry_path = os.path.join(self._testdata_dir, '515')

    mlab.report_cpu_for_vserver('fake.host', test_entry_path)
    metrics = FakeValues.get_metrics()

    self.assertEqual(
        metrics.get('fake.host/cpu_total/vs_cpu/system'), [722311.5])
    self.assertEqual(
        metrics.get('fake.host/cpu_total/vs_cpu/user'), [165204.0])

  @mock.patch('mlab.collectd.Values', new=FakeValues)
  @mock.patch('mlab.time.time')
  def testunit_logger(self, mock_time):
    FakeValues.setup()
    mlab._root_hostname = 'fake.host'
    mock_time.side_effect = [0, 10]  # first call, second call

    @mlab.logger('read')
    def fake_read():
      pass
    fake_read()  # Invokes decorator.
    metrics = FakeValues.get_metrics()

    self.assertEqual(metrics.get('fake.host/meta/timer/read'), [10])


class MlabCollectdPlugin_IntegrationTests(unittest.TestCase):
  """End-to-end tests."""

  def setUp(self):
    global mlab
    # NOTE: Reload the mlab module after each test.
    mlab = reload(mlab)
    self._testdata_dir = os.path.join(
        os.path.dirname(mlab.__file__), 'testdata')
    mlab._VSYS_FMT_IN = os.path.join(self._testdata_dir, '%s.in')
    mlab._VSYS_FMT_OUT = os.path.join(self._testdata_dir, '%s.out')
    (fifo_in, fifo_out) = mlab.get_vsys_fifo_names('mock_target')
    if not os.path.exists(fifo_in):
      os.mkfifo(fifo_in)
    if not os.path.exists(fifo_out):
      os.mkfifo(fifo_out)

  # NOTE: this is the largest most comprehensive, end-to-end test of everything.
  # So, setup is more involved, and the minimum objects are patched.
  @mock.patch('mlab.collectd.Values', new=FakeValues)
  def testintegration_plugin_read(self):
    FakeValues.setup()
    mlab._PROC_PID_STAT = os.path.join(self._testdata_dir, 'proc_pid_stat')
    mlab._PROC_STAT = os.path.join(self._testdata_dir, 'proc_stat')
    mlab._PROC_UPTIME = os.path.join(self._testdata_dir, 'uptime')
    mlab._PROC_VIRTUAL = self._testdata_dir
    mlab._root_hostname = 'fake.host'
    mlab._config_exclude_slices = {'ignore_slice': True}
    mlab._VSYS_FRONTEND_TARGET = 'mock_target'
    vs_dlimit_resp = (
        '{"version": 1, "data": {"515": [386460, 10000000, 9533, -1, 2]}}')
    vs_xidname_resp = ('{"version": 1, "data": {"515": "mlab_utility", '
                       '"516": "ignore_slice"}}')
    backend_stat_resp = ('{"version": 1, "data": {"rss": 2961408, "vsize": '
                      '4919296, "stime": 13.0, "utime": 2.0}}')
    backend = FakeVsysBackend('mock_target')
    backend.set('vs_xid_dlimits', vs_dlimit_resp)
    backend.set('vs_xid_names', vs_xidname_resp)
    backend.set('backend_stats', backend_stat_resp)
    backend.start()
    time.sleep(0.5)  # let the backend read before vsys opens for writing.
    expected_length = 39  # Total number of values reported.

    mlab.plugin_read()
    metrics = FakeValues.get_metrics()
    key_length = len(metrics.keys())

    # Shutdown.
    mlab._vs_vsys.close()
    backend.join()
    # NOTE: these should be tested by other tests, but pick one from each
    # subsystem.
    self.assertEqual(
        metrics.get('utility.mlab.fake.host/cpu_total/vs_cpu/user'),
        [165204.0])
    self.assertEqual(
        metrics.get('utility.mlab.fake.host/memory/vs_memory/rss'),
        [20004864.0])
    self.assertEqual(
        metrics.get('utility.mlab.fake.host/network/if_octets/ipv4'),
        [125176907, 9462020])
    self.assertEqual(
        metrics.get('utility.mlab.fake.host/context/vs_uptime'),
        [1049001.0])
    self.assertEqual(
        metrics.get('utility.mlab.fake.host/storage/vs_quota_bytes/quota'),
        [386460000, 9613540000])
    self.assertEqual(
        metrics.get('utility.mlab.fake.host/context/vs_threads_basic/other'),
        [19])
    self.assertEqual(metrics.get('fake.host/cpu_cores/gauge'), [2])
    self.assertEqual(metrics.get('fake.host/cpu_total/cpu/user'), [3147080.0])
    self.assertEqual(
        metrics.get('fake.host/meta/backend/process_cpu/user'), [2.0])
    self.assertEqual(
        metrics.get('fake.host/meta/backend/process_memory/vm'), [4919296])
    self.assertEqual(
        metrics.get('fake.host/meta/collectd/process_cpu/user'), [255.0])
    self.assertEqual(
        metrics.get('fake.host/meta/collectd/process_memory/vm'), [1234321])
    self.assertEqual(key_length, expected_length)
    self.assertFalse(backend.isAlive())

  def testintegration_register_WHEN_plugin_loaded(self):
    # Confirm that all expected registration methods are called.
    mlab.collectd.register_config.assert_called_with(mlab.plugin_configure)
    mlab.collectd.register_init.assert_called_with(mlab.plugin_initialize)
    mlab.collectd.register_read.assert_called_with(mlab.plugin_read)
    mlab.collectd.register_shutdown.assert_called_with(mlab.plugin_shutdown)
    # Or, not called.
    self.assertFalse(mlab.collectd.register_write.called)


if __name__ == "__main__":
  unittest.main()
