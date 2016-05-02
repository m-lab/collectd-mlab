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

import logging
import os
import StringIO
import unittest

# Third-party modules.
import mock

# Module under test.
import mlab_export

# Too many public methods. Hard to avoid for unit tests.
# pylint: disable=R0904

# Static timestamp for unittests: 2014-09-06 10:56:40.
FAKE_TIMESTAMP = 1410001000

logging.basicConfig(filename='/dev/null')


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


class MlabExport_FakeTimestampTests(unittest.TestCase):

    def setUp(self):
        self._testdata_dir = os.path.join(
            os.path.dirname(mlab_export.__file__), 'testdata')
        self.fake_tstamp = os.path.join(self._testdata_dir,
                                        'no_such_file.tstamp')

    def tearDown(self):
        # Make sure that fake timestamp file is removed.
        if os.path.exists(self.fake_tstamp):
            os.remove(self.fake_tstamp)

    def testunit_get_mtime_WHEN_timestamp_file_created_RETURNS_zero(self):
        returned_tstamp = mlab_export.get_mtime(self.fake_tstamp)

        self.assertEqual(0, returned_tstamp)


class MlabExport_GlobalTests(unittest.TestCase):

    def setUp(self):
        self._testdata_dir = os.path.join(
            os.path.dirname(mlab_export.__file__), 'testdata')

    def testcover_init_global(self):
        mlab_export.init_global()

    def testunit_LockFile_WHEN_locked_twice_RAISES_LockFileError(self):
        fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')

        with mlab_export.LockFile(fake_tstamp):
            try:
                with mlab_export.LockFile(fake_tstamp):
                    self.fail(
                        'Acquiring lockfile twice should not be possible!')
            except mlab_export.LockFileError:
                # A lock file error is expected when locked twice.
                pass

    def testunit_get_mtime_RETURNS_mtime(self):
        fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')
        os.utime(fake_tstamp, (FAKE_TIMESTAMP, FAKE_TIMESTAMP))

        returned_tstamp = mlab_export.get_mtime(fake_tstamp)

        self.assertEqual(FAKE_TIMESTAMP, returned_tstamp)

    @mock.patch('mlab_export.os.utime')
    def testunit_update_mtime(self, mock_utime):
        fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')

        mlab_export.update_mtime(fake_tstamp, FAKE_TIMESTAMP)

        mock_utime.assert_called_with(fake_tstamp,
                                      (FAKE_TIMESTAMP, FAKE_TIMESTAMP))

    def testunit_align_timestamp(self):
        self.assertEqual(mlab_export.align_timestamp(16, 6), 12)
        self.assertEqual(mlab_export.align_timestamp(32, 10), 30)
        self.assertEqual(mlab_export.align_timestamp(64, 30), 60)

    def testunit_default_start_time_WHEN_ts_previous_IS_nonzero(self):
        # Typical behavior occurs when the timestamp file aleady exists and
        # ts_previous is non-zero.
        mock_options = mock.Mock()
        mock_options.step = 10
        ts_previous = FAKE_TIMESTAMP + 9  # ts_previous is not aligned with step.
        expected_start = FAKE_TIMESTAMP  # But, it will be.

        returned_start = mlab_export.default_start_time(mock_options,
                                                        ts_previous)

        self.assertEqual(returned_start, expected_start)

    @mock.patch('mlab_export.time.time')
    def testunit_default_start_time_WHEN_ts_previous_IS_zero(self, mock_time):
        # On first run, ts_previous is zero b/c the timestamp file was just created.
        mock_time.return_value = FAKE_TIMESTAMP
        mock_options = mock.Mock()
        mock_options.update = True
        mock_options.step = 10
        mock_options.length = 1000
        expected_start = FAKE_TIMESTAMP - 1000

        returned_start = mlab_export.default_start_time(mock_options, 0)

        self.assertEqual(returned_start, expected_start)

    def testunit_assert_start_and_end_times(self):
        mock_options = mock.Mock()
        mock_options.length = 3600
        mock_options.ts_end = FAKE_TIMESTAMP + mock_options.length
        mock_options.ts_start = FAKE_TIMESTAMP

        try:
            mlab_export.assert_start_and_end_times(mock_options)
        except mlab_export.TimeOptionError as err:
            self.fail('Unexpected exception: %s' % err)

    def testunit_assert_times_WHEN_end_is_before_start_RAISES_TimeOptionError(
            self):
        mock_options = mock.Mock()
        mock_options.ts_end = FAKE_TIMESTAMP
        mock_options.ts_start = FAKE_TIMESTAMP + 1

        self.assertRaises(mlab_export.TimeOptionError,
                          mlab_export.assert_start_and_end_times, mock_options)

    def testunit_assert_times_WHEN_length_too_short_RAISES_TimeOptionError(
            self):
        mock_options = mock.Mock()
        mock_options.length = 3600
        # Start & end should be at least 'length' apart. Subtract 1 so the
        # difference is too short.
        mock_options.ts_start = FAKE_TIMESTAMP
        mock_options.ts_end = FAKE_TIMESTAMP + mock_options.length - 1

        self.assertRaises(mlab_export.TimeOptionError,
                          mlab_export.assert_start_and_end_times, mock_options)

    def testunit_default_output_name(self):
        mlab_export.HOSTNAME = 'mlab2.nuq0t'
        expected_value = ('/output/utilization/2014/09/06/mlab2.nuq0t/'
                          '20140906T10:56:40-to-20140906T11:56:40-metrics.json')
        start = FAKE_TIMESTAMP
        end = start + 3600

        returned_value = mlab_export.default_output_name(
            start, end, '/output', 'utilization', 'metrics')

        self.assertEqual(returned_value, expected_value)

    @mock.patch('mlab_export.os.makedirs')
    def testunit_make_output_dirs(self, mock_makedirs):
        fake_outputpath = '/some/path/to/a/basedir'

        mlab_export.make_output_dirs(os.path.join(fake_outputpath, 'file.json'))

        mock_makedirs.assert_called_with(fake_outputpath)

    def testunit_get_canonical_names(self):
        mock_options = enable_show_options(mock.Mock())
        mock_options.rrddir_prefix = '/var/lib/collectd/rrd/'
        fake_filename = '/var/lib/collectd/rrd/mlab2.nuq0t/disk-dm-0/disk_time.rrd'
        value_name = 'write'
        expected_host = 'mlab2.nuq0t'
        expected_experiment = 'system'
        expected_metric = 'disk.swap.io.time.write'
        mlab_export.HOSTNAME = 'mlab2.nuq0t'
        mlab_export.METRIC_MAP = {
            'disk-dm-0.disk_time.write': 'disk.swap.io.time.write'
        }

        returned_host, returned_experiment, returned_metric = (
            mlab_export.get_canonical_names(fake_filename, value_name,
                                            mock_options))

        self.assertEqual(returned_host, expected_host)
        self.assertEqual(returned_experiment, expected_experiment)
        self.assertEqual(returned_metric, expected_metric)

    def testunit_get_canonical_names_WHEN_experiment_and_skip_metric(self):
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

        returned_host, returned_experiment, returned_metric = (
            mlab_export.get_canonical_names(fake_filename, value_name,
                                            mock_options))

        self.assertEqual(returned_host, expected_host)
        self.assertEqual(returned_experiment, expected_experiment)
        self.assertEqual(returned_metric, expected_metric)

    def testunit_get_json_record(self):
        timestamps = [1, 2, 3]
        values = [10.0, 11.0, 12.0]
        expected_value = {'sample': [
                             {'timestamp': 1, 'value': 10.0},
                             {'timestamp': 2, 'value': 11.0},
                             {'timestamp': 3, 'value': 12.0}],
                          'metric': 'network.ipv4.bytes.rx',
                          'hostname': 'mlab2.nuq0t',
                          'experiment': 'utility.mlab'}  # yapf: disable

        returned_value = mlab_export.get_json_record(
            'mlab2.nuq0t', 'utility.mlab', 'network.ipv4.bytes.rx', timestamps,
            values)

        self.assertEqual(returned_value, expected_value)

    def testunit_write_json_record(self):
        output = StringIO.StringIO()
        record = {'sample': [{'timestamp': 1,
                              'value': 10.0}],
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

        self.assertSequenceEqual(
            sorted(mlab_export.get_rrd_files(rrd_dir)), sorted(expected_files))

    @mock.patch('mlab_export.get_rrd_files')
    @mock.patch('mlab_export.rrdtool.fetch')
    @mock.patch('mlab_export.get_canonical_names')
    def testunit_rrd_list(self, mock_get_canonical_names, mock_rrdtool_fetch,
                          mock_get_rrd_files):
        mock_get_rrd_files.return_value = ['file1']
        mock_rrdtool_fetch.return_value = (None, ['value'], None)
        mock_options = mock.Mock()
        mock_options.rrddir_prefix = os.path.join(self._testdata_dir, 'rrd')
        mock_options.ts_start = 123
        mock_options.ts_end = 456

        mlab_export.rrd_list(mock_options)

        mock_get_canonical_names.assert_called_with('file1', 'value',
                                                    mock_options)

    @mock.patch('mlab_export.make_output_dirs')
    @mock.patch('mlab_export.get_rrd_files')
    @mock.patch('mlab_export.rrdtool.fetch')
    @mock.patch('mlab_export.get_canonical_names')
    @mock.patch('gzip.open')
    @mock.patch('StringIO.StringIO.close')
    def testunit_rrd_export(self, mock_close, mock_open,
                            mock_get_canonical_names, mock_rrdtool_fetch,
                            mock_get_rrd_files, mock_make_output_dirs):
        str_fd = StringIO.StringIO()
        mock_open.return_value = str_fd
        mock_get_rrd_files.return_value = ['file1']
        mock_rrdtool_fetch.return_value = [
            (0, 10, 10), ['value', 'unknown_metric', 'ignored_metric'],
            [(0.0, 1.0, 2.0)]
        ]
        mock_get_canonical_names.side_effect = [
            ('fake_hostname', 'fake_experiment', 'fake_metric_name'),
            ('fake_hostname', 'fake_experiment', None),
            ('fake_hostname', 'ignore_experiment', 'fake_metric_name')
        ]
        mock_options = mock.Mock()
        mock_options.rrddir_prefix = os.path.join(self._testdata_dir, 'rrd')
        mock_options.ignored_experiments = ['ignore_experiment']
        mock_options.pretty_json = None
        mock_options.compress = True
        mock_options.output = 'output'
        mock_options.ts_start = 0
        mock_options.ts_end = 10
        expected_value = (
            '{"sample": [{"timestamp": 0, "value": 0.0}], '
            '"metric": "fake_metric_name", "hostname": "fake_hostname", '
            '"experiment": "fake_experiment"}\n')

        mlab_export.rrd_export(mock_options)
        returned_value = str_fd.getvalue()

        self.assertEqual(returned_value, expected_value)
        mock_get_canonical_names.assert_any_call('file1', 'value', mock_options)
        mock_get_canonical_names.assert_any_call('file1', 'unknown_metric',
                                                 mock_options)
        mock_get_canonical_names.assert_any_call('file1', 'ignored_metric',
                                                 mock_options)
        mock_open.assert_called_with('output.gz', 'w')
        self.assertTrue(mock_close.called)
        self.assertTrue(mock_make_output_dirs.called)

    def testunit_read_metric_map(self):
        fake_metric_conf = os.path.join(self._testdata_dir,
                                        'sample_metrics.conf')
        expected_map = {'cpu_cores.gauge': 'cpu.cores',
                        'uptime.uptime': 'system.uptime'}

        returned_map = mlab_export.read_metric_map(fake_metric_conf, 'metrics')

        self.assertEqual(returned_map, expected_map)

    def testunit_read_metric_map_WHEN_no_file(self):
        fake_metric_conf = os.path.join(self._testdata_dir,
                                        'no_such_metrics.conf')

        with self.assertRaises(SystemExit):
            mlab_export.read_metric_map(fake_metric_conf, 'metrics')

    def testunit_read_metric_map_WHEN_config_parse_error(self):
        fake_metric_conf = os.path.join(self._testdata_dir,
                                        'sample_metrics_with_error.conf')

        with self.assertRaises(SystemExit):
            mlab_export.read_metric_map(fake_metric_conf, 'metrics')

    def testunit_read_metric_map_WHEN_config_is_missing_metrics_section(self):
        fake_metric_conf = os.path.join(self._testdata_dir,
                                        'sample_metrics_missing_section.conf')

        with self.assertRaises(SystemExit):
            mlab_export.read_metric_map(fake_metric_conf, 'metrics')

    @mock.patch('mlab_export.default_output_name')
    @mock.patch('mlab_export.default_start_time')
    @mock.patch('mlab_export.read_metric_map')
    def testunit_init_args(self, mock_read_metric_map, mock_default_start_time,
                           mock_default_output_name):
        mock_options = disable_show_options(mock.Mock())
        mock_options.rrddir_prefix = os.path.join(self._testdata_dir, 'rrd')
        mock_options.exclude = []
        mock_options.pretty_json = None
        mock_options.compress = True
        mock_options.verbose = True
        mock_options.update = True
        mock_options.length = 1000
        mock_options.step = 10
        mock_options.ts_end = None
        mock_options.ts_start = None
        mock_options.output = None
        mock_options.suffix = 'metrics'
        mock_read_metric_map.return_value = {}
        mlab_export.HOSTNAME = 'mlab2.nuq0t'
        mock_default_output_name.return_value = 'fake-output-name'
        mock_default_start_time.return_value = FAKE_TIMESTAMP
        ts_previous = 0

        mlab_export.init_args(mock_options, ts_previous)

        self.assertTrue(mock_read_metric_map.called)
        self.assertEqual(mock_options.ts_start, FAKE_TIMESTAMP)
        self.assertEqual(mock_options.ts_end, FAKE_TIMESTAMP + 1000)
        self.assertEqual(mock_options.output, 'fake-output-name')
        self.assertTrue(mock_options.rrddir_prefix.endswith('/'))

    def testcover_parse_args_WHEN_bad_option_CAUSES_exit(self):
        mlab_export.sys.argv = [
            'mlab_export.py', '--bad-argument', '--causes-error'
        ]

        self.assertRaises(SystemExit, mlab_export.parse_args, 0)

    @mock.patch('mlab_export.read_metric_map')
    def testcover_parse_args_WHEN_bad_time_options_CAUSES_exit(
            self, mock_read_metric_map):
        mock_read_metric_map.return_value = {}
        mlab_export.sys.argv = [
            'mlab_export.py', '--ts_start', '10', '--ts_end', '9'
        ]

        self.assertRaises(SystemExit, mlab_export.parse_args, 0)

    @mock.patch('mlab_export.LockFile.__enter__')
    def testcover_main_exit(self, mock_lock_file_enter):
        fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')
        mlab_export.EXPORT_LOCKFILE = fake_tstamp
        mock_lock_file_enter.side_effect = mlab_export.LockFileError(
            'already locked.')

        self.assertRaises(SystemExit, mlab_export.main)

        self.assertTrue(mock_lock_file_enter.called)

    @mock.patch('mlab_export.parse_args')
    @mock.patch('mlab_export.rrd_list')
    def testcover_main_list(self, mock_rrd_list, mock_parse_args):
        fake_tstamp = os.path.join(self._testdata_dir, 'fake.tstamp')
        mlab_export.EXPORT_LOCKFILE = fake_tstamp
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
        mock_options.ts_start = FAKE_TIMESTAMP - 60 * 60
        mock_options.ts_end = FAKE_TIMESTAMP
        mock_parse_args.return_value = mock_options

        mlab_export.main()

        self.assertTrue(mock_parse_args.called)
        self.assertTrue(mock_rrd_export.called)


if __name__ == "__main__":
    unittest.main()
