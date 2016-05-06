#!/bin/bash
#
# A script to setup collectd-mlab switch configuration. This script performs
# auto-discovery of the local switch configuration to complete collectd setup.
# This setup should be run before starting collectd.

COLLECTD_SNMP="/etc/collectd-snmp.conf"
COMMUNITY="/home/mlab_utility/conf/snmp.community"
SWITCH="s1.${HOSTNAME#*.}"


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
        # NOTE: Perform SNMP auto-discovery for switch configuration.
        # Regenerate the switch configuration on every re-start.
        disco_config.py \
            --command collectd-snmp-config \
            --community_file "${COMMUNITY}.updated" \
            --hostname "${SWITCH}" > "${COLLECTD_SNMP}.tmp"
        # If the configuration was generated successfully,
        if [[ $? -eq 0 ]] && test -s "${COLLECTD_SNMP}.tmp" ; then
           # if the destination is either missing, or different from source,
           if ! test -f "${COLLECTD_SNMP}" \
               || ! diff -q "${COLLECTD_SNMP}.tmp" "${COLLECTD_SNMP}" ; then
               # Update the configuration.
               mv "${COLLECTD_SNMP}.tmp" "${COLLECTD_SNMP}"
        fi
    fi
fi

# /etc/collectd-mlab.conf includes the $COLLECTD_SNMP configuration file
# unconditionally. So, we must touch the file to create an empty
# configuration even when we cannot generate the SNMP configuration.
touch "${COLLECTD_SNMP}"
