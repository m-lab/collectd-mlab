#!/bin/bash
#
# collectd-setup Startup script to setup collectd-mlab switch configuration.
# chkconfig: - 85 16
# description: collectd-setup performs auto-discovery of the local switch
#   configuration to complete collectd setup. collectd-setup should run before
#   collectd.


# NOTE: Perform SNMP auto-discovery for switch configuration.
# If we cannot update the snmp community string, then the script should halt.
COLLECTD_SNMP="/etc/collectd-snmp.conf"
COMMUNITY="/home/mlab_utility/conf/snmp.community"
SWITCH="s1.${HOSTNAME#*.}"

function start() {

    # If the community string file is missing or zero size.
    if ! test -s "${COMMUNITY}" ; then
        echo "Warning: SNMP community string is not available!"
        echo "Warning: No switch statistics can be collected!"

    else
        # If the updated community string file is missing or zero size.
        if ! test -s "${COMMUNITY}.updated" ; then
            # Regenerate the community string only once.
            disco_config.py \
                --command update-snmp-community \
                --community_file "${COMMUNITY}" \
                --hostname "${SWITCH}" > "${COMMUNITY}.updated"
            if [[ $? -ne 0 ]]; then
                echo "Warning: failed to update SNMP community string."
                echo "Warning: No switch statistics can be collected!"
            fi
        fi

        # If the updated community string file is present and non-zero size.
        if test -s "${COMMUNITY}.updated" ; then
            # Regenerate the switch configuration on every re-start.
            disco_config.py \
                --command collectd-snmp-config \
                --community_file "${COMMUNITY}.updated" \
                --hostname "${SWITCH}" > "${COLLECTD_SNMP}.tmp" \
                && mv "${COLLECTD_SNMP}.tmp" "${COLLECTD_SNMP}"
        fi
    fi

    # /etc/collectd-mlab.conf includes the $COLLECTD_SNMP configuration file
    # unconditionally. So, we must touch the file to create an empty
    # configuration even when we cannot generate the SNMP configuration.
    touch "${COLLECTD_SNMP}"
}

case "$1" in
    start)
        start
        ;;
    stop)
        ;;
    status)
        ;;
    restart|reload)
        start
        ;;
    *)
        echo $"Usage: $0 {start|stop|status|restart|reload}"
        exit 1
esac

exit $?
