# Debugging Disco Auto-discovery

Login to the `mlab_utility` slice at the site of your choice and setup some
convenience variables.

```
ssh mlab_utility@mlab1.{site}.measurement-lab.org
COMM=`cat /home/mlab_utility/conf/snmp.community.updated`
SWITCH="s1.${HOSTNAME#*.}"
```

## Running Disco config manually

The `disco_config.py` command line tool should generate the contents for
`/etc/collectd-snmp.conf`. Normally this is done every time `service collectd
start` or `service collectd restart`, but the error output may be hidden.

```
disco_config.py --command collectd-snmp-config \
    --community_file=/home/mlab_utility/conf/snmp.community \
    --hostname $SWITCH
```

## Updating Disco snmp.community manually

For Cisco switches, the `disco_config.py` command line tool should be able to
discover the VLAN associated with the current server and update the snmp
community string accordingly.

```
disco_config.py --command update-snmp-community \
    --community_file=/home/mlab_utility/conf/snmp.community \
    --hostname $SWITCH
```

The output from this command should be saved to
`/home/mlab_utility/conf/snmp.community.updated`. If the output does not match
the content of this file (or, the file is empty) there may be an error
discovering VLANs. To diagnose Cisco switches, see the Cisco notes below. To
diagnose other switch types, start with the commands for querying `sysDescr.0`
below.

## QFX and HP Procurve

Learn the switch model.
```
$ snmpget -v 2c -c $COMM $SWITCH sysDescr.0
SNMPv2-MIB::sysDescr.0 = STRING: Juniper Networks, Inc. qfx5100-48s-6q Ethernet Switch, kernel JUNOS 14.1X53-D35.3, Build date: 2016-02-29 23:39:06 UTC Copyright (c) 1996-2016 Juniper Networks, Inc.
```

Lookup the local machine MAC and uplink MAC addresses using `ifconfig` and
`arp`. For example:

```
$ ip link show eth0
2: eth0: <BROADCAST,MULTICAST,PROMISC,UP,LOWER_UP> mtu 1500 qdisc mq state UP qlen 1000
    link/ether f4:52:14:13:33:f0 brd ff:ff:ff:ff:ff:ff

$ ip neighbor
...
165.117.240.1 dev eth0 lladdr 00:12:c0:88:05:01 DELAY
```

Lookup the MAC addresses learned on each port using the
"Q-BRIDGE-MIB::dot1qTpFdbPort" OID. Be aware that MAC addresses are represented
in "dotted decimal" form in SNMP rather than the more common "colon
hexadecimal" form. The MAC is the last six digits of the returned OIDs. The
port number is the value on the right hand side.

```
$ snmpwalk -v 2c -c $COMM $SWITCH .1.3.6.1.2.1.17.7.1.2.2.1.2
SNMPv2-SMI::mib-2.17.7.1.2.2.1.2.196608.0.18.192.136.5.1 = INTEGER: 1010
SNMPv2-SMI::mib-2.17.7.1.2.2.1.2.196608.228.29.45.23.231.128 = INTEGER: 1083
SNMPv2-SMI::mib-2.17.7.1.2.2.1.2.196608.244.82.20.19.51.16 = INTEGER: 1059
SNMPv2-SMI::mib-2.17.7.1.2.2.1.2.196608.244.82.20.19.51.48 = INTEGER: 1035
SNMPv2-SMI::mib-2.17.7.1.2.2.1.2.196608.244.82.20.19.51.240 = INTEGER: 1011
SNMPv2-SMI::mib-2.17.7.1.2.2.1.2.196608.244.142.56.205.83.200 = INTEGER: 1084
SNMPv2-SMI::mib-2.17.7.1.2.2.1.2.196608.244.142.56.205.84.68 = INTEGER: 1060
SNMPv2-SMI::mib-2.17.7.1.2.2.1.2.196608.244.142.56.205.84.72 = INTEGER: 1036
SNMPv2-SMI::mib-2.17.7.1.2.2.1.2.196608.244.142.56.205.84.80 = INTEGER: 1012
```

Lookup the ifIndex for the uplink and local ports using the port values above
for the corresponding MAC address. Append the port value to the end of the
"BRIDGE-MIB::dot1dBasePortIfIndex" OID.

```
$ snmpget -v 2c -c $COMM $SWITCH BRIDGE-MIB::dot1dBasePortIfIndex.1010
BRIDGE-MIB::dot1dBasePortIfIndex.1010 = INTEGER: 517

$ snmpget -v 2c -c $COMM $SWITCH BRIDGE-MIB::dot1dBasePortIfIndex.1011
BRIDGE-MIB::dot1dBasePortIfIndex.1011 = INTEGER: 512
```

If the lookup fails, you may get an error like:
```
$ snmpget -v 2c -c $COMM $SWITCH BRIDGE-MIB::dot1dBasePortIfIndex.1018
BRIDGE-MIB::dot1dBasePortIfIndex.1018 = No Such Instance currently exists at this OID
```

If the above fails, try walking all values at "BRIDGE-MIB::dot1dBasePortIfIndex".
Some ports may be missing.

```
$ snmpwalk -v 2c -c $COMM $SWITCH BRIDGE-MIB::dot1dBasePortIfIndex
BRIDGE-MIB::dot1dBasePortIfIndex.1010 = INTEGER: 517
BRIDGE-MIB::dot1dBasePortIfIndex.1011 = INTEGER: 512
BRIDGE-MIB::dot1dBasePortIfIndex.1012 = INTEGER: 519
BRIDGE-MIB::dot1dBasePortIfIndex.1035 = INTEGER: 513
BRIDGE-MIB::dot1dBasePortIfIndex.1036 = INTEGER: 520
BRIDGE-MIB::dot1dBasePortIfIndex.1059 = INTEGER: 515
BRIDGE-MIB::dot1dBasePortIfIndex.1060 = INTEGER: 521
BRIDGE-MIB::dot1dBasePortIfIndex.1083 = INTEGER: 516
BRIDGE-MIB::dot1dBasePortIfIndex.1084 = INTEGER: 523
```

The VLANs are configured according to "dot1qVlanStaticName"
```
$ snmpwalk -v 2c -c $COMM $SWITCH .1.3.6.1.2.1.17.7.1.4.3.1.1
```

## Cisco

Cisco switches segregate SNMP data per VLAN. SNMP data for each vlan is
accessed via a modified SNMP community string. The SNMP community string is
modified by appending `@<vlanid>` to the end. For example, if the community
string is "banana" and the VLAN id is 102, then the modified community string
would be "banana@102"

So, Disco must first discover which VLAN contains the M-Lab servers. Disco does
this using the CISCO-VTP-MIB vtpVlanIfIndex (.1.3.6.1.4.1.9.9.46.1.3.1.1.18) to
list all VLANs on the switch, followed by sequentially trying a new community
string based each VLAN to auto discover the switch ports.

To list the VLANs on a Cisco switch:

```
VLANID=$( snmpwalk -v 2c -c `cat /home/mlab_utility/conf/snmp.community` \
          s1.${HOSTNAME#*.} .1.3.6.1.4.1.9.9.46.1.3.1.1.18 \
          | grep -vE '1$|0$' | awk '{print $4}' )
echo `cat /home/mlab_utility/conf/snmp.community`@$VLANID \
    > /home/mlab_utility/conf/snmp.community.updated
```

Disco handles multiple VLANs automatically. The above command does not because
it assumes there is only *one* additional VLAN. The command above ignores VLANs
with id zero (0) and VLAN one (1). VLAN 1 is the default VLAN. All other
non-0 and non-1 VLANs are candidates.

As for other switches, we lookup the MAC addresses learned on each port. Though
for Cisco switches, we must use the BRIDGE-MIB::dot1dTpFdbPort OID.

```
# snmpwalk -v 2c -c ${COMM}@$VLANID $SWITCH .1.3.6.1.2.1.17.4.3.1.2
SNMPv2-SMI::mib-2.17.4.3.1.2.116.142.248.247.212.125 = INTEGER: 25
SNMPv2-SMI::mib-2.17.4.3.1.2.144.177.28.53.21.90 = INTEGER: 13
SNMPv2-SMI::mib-2.17.4.3.1.2.144.177.28.53.21.92 = INTEGER: 15
SNMPv2-SMI::mib-2.17.4.3.1.2.144.177.28.53.22.127 = INTEGER: 5
SNMPv2-SMI::mib-2.17.4.3.1.2.144.177.28.53.22.129 = INTEGER: 7
SNMPv2-SMI::mib-2.17.4.3.1.2.144.177.28.53.35.117 = INTEGER: 1
SNMPv2-SMI::mib-2.17.4.3.1.2.144.177.28.53.35.119 = INTEGER: 3
SNMPv2-SMI::mib-2.17.4.3.1.2.204.78.36.29.129.0 = INTEGER: 25
```

After identifying the correct MAC addresses corresponding to the uplink and
local server ports, the steps to identify the ifIndex remain the same for all
switches.
