#!/usr/bin/python
"""Summary:

  disco_config.py discovers active configuration information from the local
  switch and uses this information to either:

   - identify the VLAN id needed for switches that require VLAN-specific
         community strings.
   - identify the local and uplink switch ports to generate a collectd-snmp
         configuration.

  There are two commands corresponding to these functions:
  update-snmp-community, collectd-snmp-config.

  On success, update-snmp-community outputs to stdout a new <community> string
  with a VLAN-specific suffix, if needed.

  On success, collectd-snmp-config outputs to stdout a collectd-snmp plugin
  configuration for the for the local and uplink switch ports of the local
  switch model.

  On error, both commands print a message and have a non-zero exit status.

Example:

    ./disco_config.py \
        --command update-snmp-community \
        --community_file <snmp.community.original> \
        --hostname <switch_fqdn> > snmp.community.updated

    ./disco_config.py \
        --command collectd-snmp-config \
        --community_file snmp.community.updated \
        --hostname <switch_fqdn> > /etc/collectd-snmp.conf
"""
import logging
import sys

# Third-party modules.
import gflags as flags
from mlab.disco import arp
from mlab.disco import collectd
from mlab.disco import discovery
from mlab.disco import models
from mlab.disco import network
from mlab.disco import simple_session
import netsnmp

flags.DEFINE_string('community_file', None,
                    'Required: the file containing an SNMP community string.')
flags.DEFINE_string('hostname', None,
                    'Required: the switch fully qualified hostname.')
flags.DEFINE_string('switch_config_file',
                    '/usr/share/collectd-mlab/models.yaml',
                    'The full path to the static switch configuration file.')
flags.DEFINE_enum('command', 'collectd-snmp-config', ['collectd-snmp-config',
                                                      'update-snmp-community'],
                  'The operation to perform.')


def parse_args(args):  # pragma: no cover
    try:
        flags.FLAGS(args)  # Parses flags. Any remaining args are unused.
    except flags.FlagsError, err:
        logging.error('%s\nUsage: %s ARGS\n%s', err, sys.argv[0], flags.FLAGS)
        sys.exit(1)

    options = flags.FLAGS

    # Check for required flags.
    if not options.community_file:
        logging.error(
            'The --community_file must be specified.\nUsage: %s ARGS\n%s',
            sys.argv[0], flags.FLAGS)
        sys.exit(1)

    if not options.hostname:
        logging.error('The --hostname must be specified.\nUsage: %s ARGS\n%s',
                      sys.argv[0], flags.FLAGS)
    return options


def generate_collectd_snmp_config(discover_session, hostname, community):
    """Generates a collectd-snmp configuration for the local switch.

    Args:
        discover_session: discovery.DiscoverySession, the session used to
            discover local and uplink ports.
        hostname: str, switch hostname.
        community: str, the SNMP community string.

    Returns:
        str: the collectd-snmp configuration.

    Exits:
        On error.
    """
    try:
        model = discover_session.get_model()
    except (models.Error, simple_session.Error):
        logging.critical('Failed to get switch model: %s' % hostname,
                         exc_info=True)
        sys.exit(1)

    try:
        found_ports = discover_session.auto_discover_ifindex()
    except (arp.Error, discovery.Error, network.Error, simple_session.Error):
        logging.critical('Failed to discover switch ports: %s' % hostname,
                         exc_info=True)
        sys.exit(1)

    plugin = collectd.SnmpConfig(hostname, community, 10)
    for port_name, port_index in found_ports:
        for oid_name in model.oid_names():
            plugin.add_data(port_name, oid_name,
                            model.lookup_oids(oid_name, port_index))

    return plugin.generate()


def update_snmp_community(discover_session, hostname, switch_config, community):
    """Generates a VLAN-specific <community> string if needed by the switch.

    Args:
        discover_session: discovery.DiscoverySession, the session used to
            discover local and uplink ports.
        hostname: str, switch hostname.
        switch_config: models.Config, the switch configuration object.
        community: str, the SNMP community string.

    Returns:
        str: updated community string.

    Exits:
        On error.
    """
    vlan_found = False
    vlans = discover_session.auto_discover_vlan()
    # Default value when there are no vlans discovered.
    vlan_community = community
    for vlan in vlans:

        vlan_community = community + '@' + vlan
        vlan_session = netsnmp.Session(DestHost=hostname,
                                       Version=2,
                                       Community=vlan_community)
        vlan_discover = discovery.DiscoverySession(
            simple_session.SimpleSession(vlan_session), switch_config)

        try:
            found_ports = vlan_discover.auto_discover_ifindex()
        except (discovery.Error, models.Error, network.Error,
                simple_session.Error):
            logging.critical('Failed to discover switch ports: %s' % hostname,
                             exc_info=True)
            sys.exit(1)

        # When the VLAN id is correct, then we will discover the local server
        # and uplink ports.
        if len(found_ports) > 0:
            vlan_found = True
            logging.info('Found VLAN: ' + vlan)
            break

    if vlan_found or not vlans:
        return vlan_community
    else:
        logging.critical('Found no VLANs! Community string not updated.')
        sys.exit(1)


def main(args):  # pragma: no cover
    logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)
    options = parse_args(args)

    with open(options.community_file) as community_file:
        community = community_file.read().strip()

    with open(options.switch_config_file) as config:
        switch_config = models.load_switch_config(config)

    # Version "2" means "2c".
    session = netsnmp.Session(DestHost=options.hostname,
                              Version=2,
                              Community=community)
    discover_session = discovery.DiscoverySession(
        simple_session.SimpleSession(session), switch_config)

    if options.command == 'collectd-snmp-config':
        output = generate_collectd_snmp_config(discover_session,
                                               options.hostname, community)

    elif options.command == 'update-snmp-community':
        output = update_snmp_community(discover_session, options.hostname,
                                       switch_config, community)

    sys.stdout.write(output + '\n')
    sys.stdout.flush()


if __name__ == '__main__':  # pragma: no cover
    main(sys.argv)
