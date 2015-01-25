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
import errno
import StringIO
import time
import unittest

# third-party
import mock

# module under test.
import mlab_export

# Too many public methods. Hard to avoid for unit tests.
# pylint: disable=R0904


def disable_show_options(options):
  """Sets all show options to False."""
  options.show_nagios = False
  options.show_rrdfile = False
  options.show_rawmetric = False
  options.show_metric = False
  options.show_skipped = False
  return options


def enable_show_options(options):
  """Sets all show options to True."""
  options.show_nagios = True
  options.show_rrdfile = True
  options.show_rawmetric = True
  options.show_metric = True
  options.show_skipped = True
  return options


class MlabExport_GlobalTests(unittest.TestCase):

  def setUp(self):
    self._testdata_dir = os.path.join(
        os.path.dirname(mlab_export.__file__), 'testdata')

  def testunit_LastTime(self):
    fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')
    # Artificially set fake timestamp to one hour ago.
    one_hour_ago = int(time.time() - 3600)
    os.utime(fake_tstamp, (one_hour_ago, one_hour_ago))
    
    with mlab_export.LastTime(fake_tstamp) as last_time:
      return_lock = last_time.lock()
      return_tstamp = last_time.get_mtime()

    self.assertTrue(return_lock)
    self.assertEqual(one_hour_ago, return_tstamp)

  def testunit_LastTime_first_creation_RETURNS_zero(self):
    fake_tstamp = os.path.join(self._testdata_dir, 'no_such_file.tstamp')
    # Make sure that fake timestamp file actually doesn't exist.
    if os.path.exists(fake_tstamp):
      os.remove(fake_tstamp)
    
    last_time = mlab_export.LastTime(fake_tstamp)
    last_time.open()
    return_lock = last_time.lock()
    return_tstamp = last_time.get_mtime()
    last_time.close()

    self.assertTrue(return_lock)
    self.assertEqual(0, return_tstamp)

  @mock.patch('mlab_export.os.utime')
  def testunit_LastTime_update_mtime(self, mock_utime):
    fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')
    fake_time = int(time.time())

    last_time = mlab_export.LastTime(fake_tstamp)
    last_time.open()
    last_time.update_mtime(fake_time)
    last_time.close()
    
    mock_utime.assert_called_with(fake_tstamp, (fake_time, fake_time))

  @mock.patch('mlab_export.fcntl')
  def testunit_LastTime_already_locked_RETURNS_False(self, mock_fcntl):
    mock_fcntl.flock.side_effect = IOError(errno.EAGAIN, 'fake ioerror')
    fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')
    
    last_time = mlab_export.LastTime(fake_tstamp)
    last_time.open()
    return_lock = last_time.lock()  # Should fail.
    last_time.close()

    self.assertFalse(return_lock)

  @mock.patch('mlab_export.logging.error')
  @mock.patch('mlab_export.LastTime.lock')
  def testcover_main_exit(self, mock_last_time_lock, mock_error):
    fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')
    mlab_export.LAST_EXPORT_FILENAME = fake_tstamp
    mock_last_time_lock.return_value = False

    self.assertRaises(SystemExit, mlab_export.main)

    self.assertTrue(mock_last_time_lock.called)
    self.assertTrue(mock_error.called)

  @mock.patch('mlab_export.parse_args')
  @mock.patch('mlab_export.rrd_list')
  def testcover_main_list(self, mock_rrd_list, mock_parse_args):
    fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')
    mlab_export.LAST_EXPORT_FILENAME = fake_tstamp
    mock_options = disable_show_options(mock.Mock())
    mock_options.show_nagios = True  # Enable one show option.
    mock_parse_args.return_value = mock_options

    mlab_export.main()

    self.assertTrue(mock_parse_args.called)
    self.assertTrue(mock_rrd_list.called)

  @mock.patch('mlab_export.parse_args')
  @mock.patch('mlab_export.rrd_export')
  def testcover_main_export(self, mock_rrd_export, mock_parse_args):
    fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')
    mlab_export.LAST_EXPORT_FILENAME = fake_tstamp
    mock_options = disable_show_options(mock.Mock())
    mock_options.update = True
    mock_options.rrddir_prefix = mlab_export.RRD_PREFIX
    mock_options.ts_start = time.time() - 60*60
    mock_options.ts_end = time.time()
    mock_parse_args.return_value = mock_options

    mlab_export.main()

    self.assertTrue(mock_parse_args.called)
    self.assertTrue(mock_rrd_export.called)

  def testcover_init_global(self):
    mlab_export.init_global()

  @mock.patch('mlab_export.logging.error')
  def testcover_parse_args(self, mock_error):
    mlab_export.sys.argv = [
        'mlab_export.py', '--bad-argument', '--causes-error']

    self.assertRaises(SystemExit, mlab_export.parse_args, 0)

    self.assertTrue(mock_error.called)

  def testunit_align_timestamp(self):
    fake_ts = 1410341339
    expected_6 = fake_ts - 5
    expected_10 = fake_ts - 9
    expected_30 = fake_ts - 29

    ts_6 = mlab_export.align_timestamp(fake_ts, 6)
    ts_10 = mlab_export.align_timestamp(fake_ts, 10)
    ts_30 = mlab_export.align_timestamp(fake_ts, 30)

    self.assertEqual(ts_6, expected_6)
    self.assertEqual(ts_10, expected_10)
    self.assertEqual(ts_30, expected_30)

  @mock.patch('mlab_export.time.time')
  def testunit_default_end_time(self, mock_time):
    mock_time.return_value = 1401401409
    mock_options = mock.Mock()
    mock_options.length = 3600
    mock_options.step = 10
    mock_options.update = False
    expected_ts_end = 1401400800

    returned_ts_end = mlab_export.default_end_time(mock_options)

    self.assertEqual(returned_ts_end, expected_ts_end)

  @mock.patch('mlab_export.time.time')
  def testunit_default_end_time_WHEN_short_length_RAISES_TimeOptionError(
      self, mock_time):
    mock_time.return_value = 1401401409
    mock_options = mock.Mock()
    mock_options.length = 600
    mock_options.ts_offset = 100
    mock_options.step = 10
    mock_options.update = True

    self.assertRaises(
        mlab_export.TimeOptionError, mlab_export.default_end_time, mock_options)

  def testunit_default_start_time_WHEN_using_default_behavior(self):
    # Default behavior is --update=True and a non-zero ts_previous value
    # because the timestamp file aleady exists.
    mock_options = mock.Mock()
    mock_options.update = True
    mock_options.step = 10
    ts_previous = 1401401409
    expected_start = 1401401400

    returned_start = mlab_export.default_start_time(mock_options, ts_previous)

    self.assertEqual(returned_start, expected_start)

  def testunit_default_start_time_WHEN_first_run(self):
    # On first run, ts_previous is zero b/c the timestamp file was just created.
    mock_options = mock.Mock()
    mock_options.update = True
    mock_options.step = 10
    mock_options.length = 3600
    mock_options.ts_end = 1401401409 + mock_options.length
    expected_start = 1401401400
    ts_previous = 0

    returned_start = mlab_export.default_start_time(mock_options, ts_previous)

    self.assertEqual(returned_start, expected_start)

  @mock.patch('mlab_export.logging.debug')
  def testunit_assert_start_and_end_times(self, mock_logging):
    mock_options = mock.Mock()
    mock_options.length = 3600
    mock_options.ts_end = 1401401409 + mock_options.length
    mock_options.ts_start = 1401401409

    try:
      mlab_export.assert_start_and_end_times(mock_options)
    except mlab_export.TimeOptionError as err:
      self.fail('Unexpected exception: %s' % err)

    self.assertTrue(mock_logging.called)

  def testunit_assert_times_WHEN_end_is_before_start_RAISES_TimeOptionError(
      self):
    mock_options = mock.Mock()
    mock_options.length = 3600
    mock_options.ts_end = 1401401408
    mock_options.ts_start = 1401401409

    self.assertRaises(mlab_export.TimeOptionError,
                      mlab_export.assert_start_and_end_times, mock_options)

  def testunit_assert_start_and_end_times_WHEN_too_short_RAISES_TimeOptionError(
      self):
    mock_options = mock.Mock()
    mock_options.length = 3600
    # Start & end should be at least 'length' apart. Subtract 1 to the
    # difference too short.
    mock_options.ts_end = 1401401409 + mock_options.length - 1
    mock_options.ts_start = 1401401409

    self.assertRaises(mlab_export.TimeOptionError,
                      mlab_export.assert_start_and_end_times, mock_options)

  def testunit_read_metric_map(self):
    fake_metric_conf = os.path.join(self._testdata_dir, 'sample_metrics.conf')
    expected_map = {'cpu_cores.gauge': 'cpu.cores',
                    'uptime.uptime': 'system.uptime'}

    returned_map = mlab_export.read_metric_map(fake_metric_conf)

    self.assertEqual(returned_map, expected_map)

  @mock.patch('mlab_export.logging.error')
  def testunit_read_metric_map_WHEN_no_file(self, mock_error):
    fake_metric_conf = os.path.join(self._testdata_dir, 'no_such_metrics.conf')

    self.assertRaises(SystemExit, mlab_export.read_metric_map, fake_metric_conf)
    self.assertTrue(mock_error.called)

  @mock.patch('mlab_export.logging.error')
  def testunit_read_metric_map_WHEN_config_parse_error(self, mock_error):
    fake_metric_conf = os.path.join(
        self._testdata_dir, 'sample_metrics_with_error.conf')

    self.assertRaises(SystemExit, mlab_export.read_metric_map, fake_metric_conf)
    self.assertTrue(mock_error.called)

  @mock.patch('mlab_export.logging.error')
  def testunit_read_metric_map_WHEN_config_is_missing_metrics_section(
      self, mock_error):
    fake_metric_conf = os.path.join(
        self._testdata_dir, 'sample_metrics_missing_section.conf')

    self.assertRaises(SystemExit, mlab_export.read_metric_map, fake_metric_conf)
    self.assertTrue(mock_error.called)

  @mock.patch('mlab_export.logging.info')
  def testunit_get_canonical_names(self, mock_logging):
    mock_options = enable_show_options(mock.Mock())
    mock_options.rrddir_prefix = '/var/lib/collectd/rrd/'
    fake_filename = '/var/lib/collectd/rrd/mlab2.nuq0t/disk-dm-0/disk_time.rrd'
    value_name = 'write'
    expected_host = 'mlab2.nuq0t'
    expected_experiment = 'system'
    expected_metric = 'disk.swap.io.time.write'
    mlab_export.HOSTNAME = 'mlab2.nuq0t'
    mlab_export.METRIC_MAP = {
        'disk-dm-0.disk_time.write': 'disk.swap.io.time.write'}

    (returned_host, returned_experiment, returned_metric) = (
        mlab_export.get_canonical_names(
            fake_filename, value_name, mock_options))
    
    self.assertEqual(returned_host, expected_host)
    self.assertEqual(returned_experiment, expected_experiment)
    self.assertEqual(returned_metric, expected_metric)
    self.assertTrue(mock_logging.called)

  @mock.patch('mlab_export.logging.info')
  def testunit_get_canonical_names_WHEN_experiment_and_skip_metric(
      self, mock_logging):
    mock_options = enable_show_options(mock.Mock())
    mock_options.rrddir_prefix = '/var/lib/collectd/rrd/'
    fake_filename = ('/var/lib/collectd/rrd/utility.mlab.mlab2.nuq0t/'
                     'network/if_octets-ipv4.rrd')
    value_name = 'tx'
    expected_host = 'mlab2.nuq0t'
    expected_experiment = 'utility.mlab'
    expected_metric = None
    mlab_export.HOSTNAME = 'mlab2.nuq0t'
    mlab_export.METRIC_MAP = {}

    (returned_host, returned_experiment, returned_metric) = (
        mlab_export.get_canonical_names(
            fake_filename, value_name, mock_options))
    
    self.assertEqual(returned_host, expected_host)
    self.assertEqual(returned_experiment, expected_experiment)
    self.assertEqual(returned_metric, expected_metric)
    self.assertTrue(mock_logging.called)

  @mock.patch('mlab_export.logging.debug')
  def testunit_get_json_record(self, mock_logging):
    timestamps = [1, 2, 3]
    values = [10.0, 11.0, 12.0]
    expected_value = {'sample': [
                       {'timestamp': 1, 'value': 10.0},
                       {'timestamp': 2, 'value': 11.0},
                       {'timestamp': 3, 'value': 12.0}],
                      'metric': 'network.ipv4.bytes.rx',
                      'hostname': 'mlab2.nuq0t',
                      'experiment': 'utility.mlab'}

    returned_value = mlab_export.get_json_record(
        'mlab2.nuq0t', 'utility.mlab', 'network.ipv4.bytes.rx', timestamps,
        values)

    self.assertEqual(returned_value, expected_value)
    self.assertTrue(mock_logging.called)

  def testunit_write_json_record(self):
    output = StringIO.StringIO()
    record = {'sample': [{'timestamp': 1, 'value': 10.0}],
              'hostname': 'mlab2.nuq0t',
              'experiment': 'utility.mlab',
              'metric': 'network.ipv4.bytes.rx'}
    pretty_json = None
    expected_value = ('{"sample": [{"timestamp": 1, "value": 10.0}], '
                      '"metric": "network.ipv4.bytes.rx", "hostname":'
                      ' "mlab2.nuq0t", "experiment": "utility.mlab"}\n')

    mlab_export.write_json_record(output, record, pretty_json)
    returned_value = output.getvalue()
 
    self.assertEqual(returned_value, expected_value)

  def testunit_get_rrd_files(self):
    rrd_dir = os.path.join(os.path.abspath(self._testdata_dir), 'rrd')
    expected_files = [rrd_dir + '/mlab2.nuq0t/file2.rrd',
                      rrd_dir + '/mlab2.nuq0t/file1.rrd']

    returned_files = [ fname for fname in mlab_export.get_rrd_files(rrd_dir) ]

    self.assertEqual(returned_files, expected_files)

  @mock.patch('mlab_export.get_rrd_files')
  @mock.patch('mlab_export.rrdtool.fetch')
  @mock.patch('mlab_export.get_canonical_names')
  def testunit_rrd_list(
      self, mock_get_canonical_names, mock_rrdtool_fetch, mock_get_rrd_files):
    mock_get_rrd_files.return_value = ['file1']
    mock_rrdtool_fetch.return_value = (None, ['value'], None)
    mock_options = mock.Mock()
    mock_options.rrddir_prefix = os.path.join(self._testdata_dir, 'rrd')
    mock_options.ts_start = 123
    mock_options.ts_end = 456

    mlab_export.rrd_list(mock_options)

    mock_get_canonical_names.assert_called_with('file1', 'value', mock_options)

  @mock.patch('mlab_export.get_rrd_files')
  @mock.patch('mlab_export.rrdtool.fetch')
  @mock.patch('mlab_export.get_canonical_names')
  @mock.patch('gzip.open')
  @mock.patch('StringIO.StringIO.close')
  def testunit_rrd_export(
      self, mock_close, mock_open, mock_get_canonical_names, mock_rrdtool_fetch,
      mock_get_rrd_files):
    str_fd = StringIO.StringIO()
    mock_open.return_value = str_fd
    mock_get_rrd_files.return_value = ['file1']
    mock_rrdtool_fetch.return_value = [
        (0, 10, 10), ['value', 'skipped_metric'], [(0.0,0.0)]]
    mock_get_canonical_names.side_effect = [
        ('host', 'exp', 'metric'), ('host', 'exp', None)]
    mock_options = mock.Mock()
    mock_options.rrddir_prefix = os.path.join(self._testdata_dir, 'rrd')
    mock_options.ignored_experiments = []
    mock_options.pretty_json = None
    mock_options.compress = True
    mock_options.output = 'output'
    mock_options.ts_start = 0
    mock_options.ts_end = 10
    expected_value = ('{"sample": [{"timestamp": 0, "value": 0.0}], '
                      '"metric": "metric", "hostname": "host", '
                      '"experiment": "exp"}\n')

    mlab_export.rrd_export(mock_options)
    returned_value = str_fd.getvalue()

    self.assertEqual(returned_value, expected_value)
    mock_get_canonical_names.assert_called_with(
        'file1', 'skipped_metric', mock_options)
    mock_open.assert_called_with('output.gz', 'w')
    self.assertTrue(mock_close.called)

  @mock.patch('mlab_export.default_output_name')
  @mock.patch('mlab_export.assert_start_and_end_times')
  @mock.patch('mlab_export.default_start_time')
  @mock.patch('mlab_export.default_end_time')
  @mock.patch('mlab_export.logging.basicConfig')
  @mock.patch('mlab_export.read_metric_map')
  def testunit_init_args(self, mock_read_metric_map, mock_logging,
                         mock_default_end_time, mock_default_start_time,
                         mock_assert_start_and_end_times,
                         mock_default_output_name):
    mock_options = disable_show_options(mock.Mock())
    mock_options.rrddir_prefix = os.path.join(self._testdata_dir, 'rrd')
    mock_options.exclude = []
    mock_options.pretty_json = None
    mock_options.compress = True
    mock_options.verbose = True
    mock_options.ts_end = None
    mock_options.ts_start = None
    mock_options.update = True
    mock_options.output = None
    mock_options.output_dir = '/output'
    mock_read_metric_map.return_value = {}
    mlab_export.HOSTNAME = 'mlab2.nuq0t'
    ts_previous = 0

    mlab_export.init_args(mock_options, ts_previous)

    self.assertTrue(mock_read_metric_map.called)
    self.assertTrue(mock_logging.called)
    self.assertTrue(mock_default_start_time.called)
    self.assertTrue(mock_default_end_time.called)
    self.assertTrue(mock_assert_start_and_end_times.called)
    self.assertTrue(mock_default_output_name.called)
    self.assertEqual(mock_options.rrddir_prefix[-1], '/')

  @mock.patch('mlab_export.os.makedirs')
  def testunit_default_output_name(self, mock_makedirs):
    mlab_export.HOSTNAME = 'mlab2.nuq0t'
    expected_value = ('/output/resource-utilization/2014/09/12/mlab2.nuq0t/'
                      'metrics-20140912T105000-to-20140912T115000.json')
    start = 1410519000
    end = start + 3600

    returned_value = mlab_export.default_output_name(start, end, '/output')

    self.assertEqual(returned_value, expected_value)
    self.assertTrue(mock_makedirs.called)


if __name__ == "__main__":
  unittest.main()
