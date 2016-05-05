#!/bin/bash

if test -s /home/mlab_utility/conf/snmp.community ; then
    if ! test -f /var/lib/collectd/lastexport.tstamp ; then
        # TODO(soltesz): Fix mlab_export.py to handle initial conditions
        # correctly. Initialize the lastexport timestamp file to one hour ago
        # for first export.
        touch -t $( date +%Y%m%d%H00 -d "-1 hour" ) /var/lib/collectd/lastexport.tstamp
    fi
    /usr/bin/mlab_export.py --noupdate --suffix=switch --compress > /dev/null
fi
/usr/bin/mlab_export.py --compress > /dev/null
