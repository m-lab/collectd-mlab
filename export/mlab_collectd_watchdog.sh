#!/bin/bash
#
# The collectd watchdog will restart collectd if it is not running and confirm
# that no RRD files are corrupted. This script should be scheduled to run via
# cron every few minutes.
#
#     */5 * * * * root   flock -xn /tmp/lock -c /usr/bin/mlab_collectd_watchdog.sh > /dev/null
#
# The use of 'flock' guarantees that only one instance of the script will run at
# a time.
#
# RRD files created by collectd can become corrupted under some circumstances:
# bad disk, unscheduled shutdown, gremlins. Corrupted RRD files can crash
# collectd. So, if collectd is not running, we check all RRD files under
# /var/lib/collectd/rrd and remove any corrupted files.
#
# All file removals are logged in /var/log/mlab-collectd-watchdog.log* on a
# monthly rotation.

COLLECTD_PATTERN="/usr/sbin/collectd"
COLLECTD_RRDDIR="/var/lib/collectd/rrd"
# Rotate log monthly.
LOGFILE=/var/log/mlab-collectd-watchdog.log.$( date +%m )

# Checks the integrity of all RRD files in rrddir. Corrupt RRDs are removed and
# log message is written to LOGFILE.
#
# Arguments:
#  rrddir: directory name, recursively check all RRD files under this directory.
# Returns:
#  None.
function check_rrd_integrity () {
  local rrddir=$1
  local rrdfile=

  # For all files ending with .rrd under the path $rrddir.
  for rrdfile in $( find ${rrddir} -name "*.rrd" ); do
    # If dumping the rrd file fails,
    if ! rrdtool dump ${rrdfile} > /dev/null ; then
      # then log the file name and remove it.
      echo "$(date): Removing corrupt RRD: ${rrdfile}" >> ${LOGFILE}
      rm -f ${rrdfile} \
        || echo "$(date): Failed to remove: ${rrdfile}" >> ${LOGFILE}
    fi
  done
}

function start_collectd () {
  echo "$(date): Starting collectd." >> ${LOGFILE}
  service collectd start \
    || echo "$(date): Failed to start collectd." >> ${LOGFILE}
}

# If collectd is not running, check RRD file integrity and restart.
if ! pgrep -f ${COLLECTD_PATTERN} > /dev/null ; then
  check_rrd_integrity ${COLLECTD_RRDDIR}
  start_collectd
fi
