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

  mlab_export.py serializes RRD data collected by the M-Lab, collectd plugin.

  mlab_export.py supports many options for use by a human operator. However,
  typical usage should be automated using something like crond. Because typical
  usage is automated, all parameters have sensible defaults, tuned to running on
  M-Lab.

Overview of default operation:
  * Script finds all RRD files under --rrddir_prefix.
  * Script opens a file for output under --output_dir (optionally compressed).
  * Script reads values from each RRD between --ts_start and --ts_end.
  * Script determines ts_start and ts_end from 'mtime' on LAST_EXPORT_FILENAME.
    - On first run, LAST_EXPORT_FILENAME is created. ts_end is set to nearest
      hour from current time, and ts_start is set to ts_end minus --length.
    - On later runs, ts_start is set to the 'mtime' of LAST_EXPORT_FILENAME.
      And, ts_end is set to the most recent hour.
  * Script only exports metrics defined in the export_metrics.conf config file.
  * On success, script sets mtime on LAST_EXPORT_FILENAME to ts_end, in
    preparation for the next run.

Examples:
  # Default operation should not require additional parameters.
  ./mlab_export.py

  # Useful for testing, --noupdate exports the last hour without the
  # side-effects of modifying the mtime of LAST_EXPORT_FILENAME.
  ./mlab_export.py --noupdate --output example.json

  # To export a different set of metrics than the global default.
  ./mlab_export.py --noupdate --output example.json --export_metrics metrics.cfg

  # To export "pretty" json more suitable for debugging.
  ./mlab_export.py --noupdate --pretty_json --output example.json

  # List rrd file names, the raw metric names, or the canonical metric names.
  ./mlab_export.py --show_rrdfile
  ./mlab_export.py --show_rawmetric --show_metric

  # Show the collectd-nagios command line for checking each metric.
  ./mlab_export.py --show_nagios
"""
import ConfigParser
import contextlib
import fcntl
import gzip
import json
import logging
import os
import socket
import sys
import time

# Third-party modules.
import gflags as flags
import rrdtool

COLLECTD_INTERVAL = 10
EXPORT_DIR = '/var/spool/mlab_utility'
EXPORT_LOCKFILE = os.path.join(EXPORT_DIR, 'mlab_export.lock')
HOSTNAME = None
LAST_EXPORT_FILENAME = '/var/lib/collectd/lastexport.tstamp'
LIST_OPTIONS = ('rrdfile', 'metric', 'metric_raw', 'metric_skipped')
METRIC_MAP = None
METRIC_MAP_CONF = '/usr/share/collectd-mlab/export_metrics.conf'
RRD_PREFIX = '/var/lib/collectd/rrd/'

flags.DEFINE_string('rrddir_prefix', RRD_PREFIX,
                    'Root directory of RRD files to export.')
flags.DEFINE_integer('length',
                     3600,
                     'Length of time to export in seconds. Length should be '
                     'a multiple of step.',
                     lower_bound=0)
flags.DEFINE_integer(
    'step',
    COLLECTD_INTERVAL,
    'Time between RRD values in seconds. This value '
    'must equal the value in the collectd config. Inaccurate values will not '
    'change the intervals of exported data, but could result in samples being '
    'skipped at the beginning or end of the export window.',
    lower_bound=1)
flags.DEFINE_integer(
    'ts_start',
    None,
    'Timestamp to start export, in seconds since the epoch. Only use this '
    'option for debugging. Normally ts_start is calculated automatically from '
    'the previous export end time. If given, ts_start should be a multiple of '
    'step.',
    lower_bound=0)
flags.DEFINE_integer(
    'ts_end',
    None,
    'Timestamp to end export, in seconds since the epoch. Only use this option '
    'for debugging. Normally, ts_end is calculated automatically from: '
    'ts_start + length.',
    lower_bound=0)
flags.DEFINE_integer(
    'ts_offset',
    600,
    'Amount of time (seconds) that must have passed after '
    'ts_end to ensure that values cached by collectd have been flushed to disk '
    'before attempting an export.',
    lower_bound=0)
flags.DEFINE_multistring('ignored_experiments', [],
                         'List of experiment names to ignore. Experiment '
                         'must be in "slice.site" form not "site_slice".')
flags.DEFINE_bool('pretty_json', None,
                  'Add extra indenting to json output (for debugging).')
flags.DEFINE_string('output_dir', EXPORT_DIR,
                    'Root directory of json output files.')
flags.DEFINE_string('output', None,
                    'Name of json output file. Set automatically if not given.')
flags.DEFINE_string(
    'export_metrics', METRIC_MAP_CONF, 'File name with metric map. The metric '
    'map defines canonical metric names for raw, metric names taken from '
    'collectd RRD files.')
flags.DEFINE_bool('verbose', False, 'Increase verbosity level.')
flags.DEFINE_bool('show_nagios', False,
                  'Shows collectd-nagios commands to monitor metrics.')
flags.DEFINE_bool('show_rrdfile', False,
                  'Shows the RRD files opened during export.')
flags.DEFINE_bool('show_metric', False,
                  'Shows the canonical metric names during export.')
flags.DEFINE_bool(
    'show_rawmetric', False, 'Shows the raw metric name before translating to '
    'the canonical name. This can be helpful to know before adding new metrics '
    'to the export metrics configuration file.')
flags.DEFINE_bool(
    'show_skipped', False, 'Shows the raw metric names that are not exported. '
    'This option may be helpful when adding new metrics to the export_metrics.')
flags.DEFINE_bool('update', True,
                  'Update timestamps on successful export. Update is always '
                  'disabled when any --show_* option is enabled.')
flags.DEFINE_bool(
    'compress', False,
    'Compresses output and adds .gz extension to output filename.')
flags.DEFINE_string('suffix', 'metrics',
                    ('The suffix is appended to file names during export, e.g. '
                     '*-<suffix>.json.gz, and the suffix is used as a section '
                     'header in the "--export_metrics" configuration file.'))
flags.DEFINE_bool('counts', False,
                  ('Export metric counts rather than rates. Counts are '
                   'recovered by multiplying rates by the stepsize.'))


class Error(Exception):
    """Base error type for this file."""
    pass


class TimeOptionError(Error):
    """An error related to export times or ranges."""
    pass


class LockFileError(Error):
    """An exclusive lock could not be acquired for a lock file."""
    pass


def init_global():
    global HOSTNAME
    # NOTE: This should be the hostname of root context, not slice context.
    HOSTNAME = socket.gethostname()
    logging.basicConfig(format='%(message)s', level=logging.INFO)


class LockFile(object):
    """Provides a file-based lock."""

    def __init__(self, filename):
        self._filename = filename
        self._handle = None

    def __enter__(self):
        """Acquires file lock on filename.

    Raises:
      LockFileError, if the lock cannot be acquired.
    """
        try:
            self._handle = open(self._filename, 'w')
            fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError as err:
            raise LockFileError(err)

    def __exit__(self, *args):
        """Releases file lock on filename."""
        self._handle.close()


def get_mtime(last_export_filename):
    """Returns the file mtime, or zero if last_export_filename was created.

    Args:
      last_export_filename: str, absolute path to last export time stamp file.

    Returns:
      int, 0 if the file was created, or the mtime of existing file.

    Raises:
      IOError, if last_export_filename does not exist and cannot be created.
      OSError, if last_export_filename exists but stat info cannot be read.
    """
    if not os.path.exists(last_export_filename):
        open(last_export_filename, 'w').close()
        # Indicate that there is no timestamp, so the caller can use a default.
        return 0
    return int(os.stat(last_export_filename).st_mtime)


def update_mtime(last_export_filename, mtime):
    """Updates the atime & mtime on the export timestamp file.

    Args:
      last_export_filename: str, absolute path to last export time stamp file.
      mtime: int, timestamp in seconds since epoch; used for file atime & mtime.

    Raises:
      OSError, if last_export_filename mtime cannot be updated.
    """
    os.utime(last_export_filename, (mtime, mtime))


def align_timestamp(timestamp, step):
    """Adjusts 'timestamp' down to a multiple of 'step' size.

    Args:
      timestamp: int, timestamp in seconds since the epoch.
      step: int, interval to align.

    Returns:
      int, timestamp adjusted to multiple of step.
    """
    return timestamp - (timestamp % step)


def default_start_time(options, ts_previous):
    """Calculates a default start timestamp.

    Args:
      options: flags.FlagValues, the runtime options. These values are read:
          options.length, options.step, options.update, options.ts_offset.
      ts_previous: int, timestamp in seconds since epoch of last
          successful export. On first run, this value should be zero.

    Returns:
      int, timestamp in seconds since the epoch.
    """
    if ts_previous:
        # Typical: start from the end time of previous runs.
        return align_timestamp(ts_previous, options.step)

    ts_current = int(time.time())

    # Since ts_previous is not set, this is a "first run" scenario.
    if not options.update:
        # Unlikely: first run by a user. Start as close to 'now' as possible.
        start = ts_current - options.length - options.ts_offset
    else:
        # Likely: first, automated export. Start at previous 'length' aligned time.
        start = align_timestamp(ts_current, options.length) - options.length

    # Align start ts to a multiple of step size (just in case).
    return align_timestamp(start, options.step)


def default_end_time(step):
    """Calculates a default end timestamp aligned to step based on current time.

    Args:
      step: int, interval to align end time.

    Returns:
      int, timestamp in seconds since the epoch.
    """
    return align_timestamp(int(time.time()), step)


def assert_start_and_end_times(options):
    """Performs a sanity check on start and end timestamps.

    This method asserts that both ts_end is less than ts_start and that the
    difference between them is greater than options.length.

    Args:
      options: flags.FlagValues, the runtime options. These values are read:
          options.length, options.ts_start, options.ts_end.

    Raises:
      TimeOptionError, if a start & end time constraint is violated.
    """
    # Always check if basic constratins are respected.
    if options.ts_end <= options.ts_start:
        raise TimeOptionError('Start time must precede end time.')

    if options.ts_end - options.ts_start != options.length:
        msg = (
            'Difference between ts_start and ts_end times must equal length: ' +
            '%s - %s = %s < %s' %
            (options.ts_end, options.ts_start,
             (options.ts_end - options.ts_start), options.length))
        raise TimeOptionError(msg)

    logging.debug('Exporting: %s to %s', time.ctime(options.ts_start),
                  time.ctime(options.ts_end))


def default_output_name(ts_start, ts_end, output_dir, rsync_name, suffix):
    """Creates a default output filename based on time range and output dir.

    Filenames are formatted with time stamps as:
        <output_dir>/<rsync_name>/YYYY/MM/DD/<HOSTNAME>/
            <ts_start>-to-<ts_end>-<suffix>.json

    The YYYY, MM, DD in the path are taken from ts_start.
    Both <ts_start> and <ts_end> are formatted as: YYYYMMDDTHHMMSS

    Args:
      ts_start: int, starting timestamp of export in seconds since the epoch.
      ts_end: int, ending timestamp of export in seconds since the epoch.
      output_dir: str, base path of directory for output.
      rsync_name: str, a directory path for the rsync dropbox.
      suffix: str, the suffix to append to the end of a file name, e.g.
          <ts_start>-to-<ts_end>-<suffix>.json

    Returns:
      str, absolute path of generated output file name.
    """
    filename = '%s-to-%s-%s.json' % (
        time.strftime('%Y%m%dT%H:%M:%S', time.gmtime(ts_start)),
        time.strftime('%Y%m%dT%H:%M:%S', time.gmtime(ts_end)), suffix)
    date_path = time.strftime('%Y/%m/%d', time.gmtime(ts_start))
    full_path = os.path.join(output_dir, rsync_name, date_path, HOSTNAME)
    return os.path.join(full_path, filename)


def make_output_dirs(output_name):
    """Creates directory path to filename, if it does not exist.

    Args:
      output_name: str, absolute path of an output file.

    Raises:
      OSError, if directory cannot be created.
    """
    dir_name = os.path.dirname(output_name)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name)


def get_canonical_names(filename, value_name, options):
    """Converts raw filename and value names from RRD into canonical export names.

    Args:
      filename: str, the absolute path of an rrd file.
      value_name: str, the name of the value being exported from the RRD.
      options: flags.FlagValues, the runtime options. This method uses
          options.rrddir_prefix and all option.show_* flags.

    Returns:
      (str, str, str), with HOSTNAME, experiment name, metric name.
    """
    # Strip rrddir_prefix, remove rrd extension, and split directory components.
    short_filename = filename.replace(options.rrddir_prefix, '', 1)
    short_filename, _ = os.path.splitext(short_filename)
    file_fields = short_filename.split(os.path.sep)

    # The zeroth field is always the context hostname.
    if HOSTNAME == file_fields[0]:
        # The root context represents whole-system metrics.
        experiment = 'system'
    else:
        # A slice hostname. Everything remaining after stripping hostname.
        experiment = file_fields[0].replace('.' + HOSTNAME, '')

    metric_raw = '.'.join(file_fields[1:])
    if value_name != 'value':
        metric_raw += '.' + value_name

    # NOTE: convert the raw metric name into the canonical form, or None.
    metric = METRIC_MAP.get(metric_raw, None)

    # Optionally print extra information.
    if options.show_nagios:
        cmd = ('collectd-nagios -s $COLLECTD_UNIXSOCK -H {host} ' +
               '-n {metric} -d {value} [-w <l:h> -c <l:h>]')
        cmd = cmd.format(host=file_fields[0],
                         metric=os.path.join(file_fields[1:]),
                         value=value_name)
        logging.info(cmd)
    if options.show_rrdfile:
        logging.info('rrdfile: %s', filename)
    if options.show_rawmetric:
        logging.info('metric_raw: %s', metric_raw)
    if options.show_skipped and not metric:
        logging.info('metric_skipped: %s', metric_raw)
    if options.show_metric and metric:
        logging.info('metric: %s', metric)

    return (HOSTNAME, experiment, metric)


def get_json_record(hostname, experiment, metric, timestamps, values, scale):
    """Creates a dict suitable for export to json.

    Args:
      hostname: str, hostname of host system.
      experiment: str, name of experiment running on host.
      metric: str, the canonical metric name for values.
      timestamps: iterable of int, timestamps corresponding to each value.
      values: iterable of float, values corresponding to each timestamp.
      scale: int, a constant used to scale values.

    Returns:
      dict, with keys for hostname, experiment, metric, and sample.
    """
    logging.debug('%s %s %s', hostname, experiment, metric)
    json_data = {
        'hostname': hostname,
        'experiment': experiment,
        'metric': metric
    }
    json_data['sample'] = get_json_record_samples(timestamps, values, scale)
    return json_data


def get_json_record_samples(timestamps, values, scale):
    """Converts a sequences of timestampes and values for a json record.

    The timestamps and values arguments must be the same length. Each value is
    multiplied by scale and the result is saved.

    Args:
      timestamps: iterable of int, timestamps corresponding to each value.
      values: iterable of float, values corresponding to each timestamp.
      scale: int, a constant used to scale values.

    Returns:
      list of dict, each dict has keys timestamp and value.
    """
    samples = []
    assert (len(timestamps) == len(values))
    for i in xrange(len(timestamps)):
        if values[i] is not None:
            samples.append({'timestamp': timestamps[i],
                            'value': values[i] * scale})
    return samples


def write_json_record(fd_output, record, pretty_json):
    """Writes json record to fd_output.

    Args:
      fd_output: file object open for writing, the record is written to this fd.
      record: dict, the record of data to serialize as json.
      pretty_json: bool, whether to write the json with extra spacing.
    """
    json.dump(record, fd_output, indent=pretty_json)
    fd_output.write('\n')  # separate each record with newline.


def get_rrd_files(rrddir_prefix):
    """Returns the absolute path of all rrd files found under rrddir_prefix.

    Args:
      rrddir_prefix: str, base directory where rrd files are stored.

    Returns:
      list of str, where each element is the absolute path to a single rrd file.
    """
    rrdfiles = []
    for root, _, filenames in os.walk(rrddir_prefix):
        for filename in filenames:
            if filename.endswith('.rrd'):
                full_path = os.path.abspath(os.path.join(root, filename))
                rrdfiles.append(full_path)
    return rrdfiles


def rrd_list(options):
    """Processes all options.show_* flags without performing an export."""
    for filename in get_rrd_files(options.rrddir_prefix):
        _, value_names, _ = rrdtool.fetch(filename, 'AVERAGE', '--start',
                                          str(options.ts_start), '--end',
                                          str(options.ts_end))
        for value_name in value_names:
            get_canonical_names(filename, value_name, options)


def rrd_export(options):
    """Exports all RRD data.

    Raises:
      OSError, if output directory cannot be created.
      IOError, if output file cannot be created or written.
    """
    open_func = open
    if options.compress:
        open_func = gzip.open
        options.output += '.gz'
    make_output_dirs(options.output)
    scale = options.step if options.counts else 1
    with contextlib.closing(open_func(options.output, 'w')) as fd_output:
        for filename in get_rrd_files(options.rrddir_prefix):
            time_range, value_names, data = rrdtool.fetch(
                filename, 'AVERAGE', '--start', str(options.ts_start), '--end',
                str(options.ts_end))
            # W0142 is the use of "* magic". These are legitimate use-cases.
            # 1) time_range is a 3-tuple (start, end, step): i.e. arguments to range.
            # 2) data is a list of tuples, which are transposed by zip.
            #    i.e. [(a,), (b,), ...] -> [(a,b,...)]
            # pylint: disable=W0142
            timestamps = range(*time_range)
            values = zip(*data)
            # pylint: enable=W0142

            for i, value_name in enumerate(value_names):
                hostname, experiment, metric = get_canonical_names(
                    filename, value_name, options)
                if metric is None or experiment in options.ignored_experiments:
                    continue
                record = get_json_record(hostname, experiment, metric,
                                         timestamps, values[i], scale)
                write_json_record(fd_output, record, options.pretty_json)


def read_metric_map(filename, section):
    """Reads content of metric name conversion configuration file.

    The format of filename should be supported by python ConfigParser. The file
    must contain at least one section named <section>.

    Example:
      [metrics]
      raw_metric.name:  canonical_metric.name

    Args:
      filename: str, the name of the metrics configuration.
      section: str, the section name that defines the metric mapping.

    Returns:
      dict, keys are raw metric names, values are canonical metric names.

    Exits:
      When filename is missing, has bad configuration, or is missing metrics
          section.
    """

    # ConfigParser.read ignores non-existent files, so check that the file
    # exists.
    if not os.path.exists(filename):
        logging.error('Config file does not exist: %s', filename)
        sys.exit(1)

    try:
        # Catch parsing or format errors.
        parser = ConfigParser.SafeConfigParser()
        parser.read(filename)
    except ConfigParser.Error as err:
        logging.error('Error while reading %s: %s', filename, err)
        sys.exit(1)

    if parser.has_section(section):
        metric_map = dict(parser.items(section))
    else:
        logging.error('Config file is missing "[%s]" section' % section)
        sys.exit(1)

    return metric_map


def any_show_options(options):
    """Checks if any show options are True."""
    return any([options.show_nagios, options.show_rrdfile, options.
                show_rawmetric, options.show_metric, options.show_skipped])


def init_args(options, ts_previous):
    """Initializes flags with default values and asserts sanity checks.

    Args:
      options: flags.FlagValues, the unprocessed defaults from flags.FLAGS.
      ts_previous: int, timestamp in seconds since epoch of last export.

    Returns:
      flags.FlagValues, options with updated defaults.
    """
    global METRIC_MAP
    METRIC_MAP = read_metric_map(options.export_metrics, options.suffix)

    if options.verbose:
        logging.basicConfig(level=logging.DEBUG)

    if options.ts_start is None:
        options.ts_start = default_start_time(options, ts_previous)

    if options.ts_end is None:
        options.ts_end = default_end_time(options.length)
        options.length = options.ts_end - options.ts_start

    if any_show_options(options):
        options.update = False

    if options.rrddir_prefix[-1] != os.path.sep:
        # Ensure that the last character of rrddir_prefix includes path separator.
        options.rrddir_prefix += os.path.sep

    assert_start_and_end_times(options)

    if options.output is None:
        rsync_name = options.suffix
        if options.suffix == 'metrics':
            # A legacy name. Ideally, suffix and rsync_name should be the same.
            rsync_name = 'utilization'

        options.output = default_output_name(options.ts_start, options.ts_end,
                                             options.output_dir, rsync_name,
                                             options.suffix)

    return options


def parse_args(ts_previous):
    """Parses command line arguments and initialize defaults.

    Args:
      ts_previous: int, timestamp in seconds since epoch of last successful
          export. On first run, this value should be zero.

    Returns:
      flags.FlagValues, all options.
    """
    try:
        # Parses flags. Any remaining args are unused.
        flags.FLAGS(sys.argv)
    except flags.FlagsError, err:
        logging.error('%s\nUsage: %s ARGS\n%s', err, sys.argv[0], flags.FLAGS)
        sys.exit(1)
    try:
        return init_args(flags.FLAGS, ts_previous)
    except TimeOptionError as err:
        logging.error(err)
        sys.exit(1)


def main():
    init_global()

    try:
        with LockFile(EXPORT_LOCKFILE):
            options = parse_args(get_mtime(LAST_EXPORT_FILENAME))
            if any_show_options(options):
                rrd_list(options)
            else:
                rrd_export(options)
                # Update last_export mtime only after everything completes
                # successfully.
                if options.update:
                    update_mtime(LAST_EXPORT_FILENAME, options.ts_end)

    except (OSError, IOError) as err:
        logging.error('Export failure: %s', err)
        sys.exit(1)

    except LockFileError as err:
        logging.error('Failed to acquire lockfile %s: %s', EXPORT_LOCKFILE, err)
        sys.exit(1)


if __name__ == '__main__':  # pragma: no cover.
    main()
