"""Tests for disco_config."""

import unittest
import logging

import mock
import netsnmp

from mlab.disco import discovery
from mlab.disco import models

import disco_config


@mock.patch.object(netsnmp, 'Session')
@mock.patch.object(discovery, 'DiscoverySession')
class UpdateSNMPCommunityTest(unittest.TestCase):
    """Tests for disco_config.update_snmp_community."""

    def setUp(self):
        logging.basicConfig(filename='/dev/null')
        self.session = mock.Mock(spec=discovery.DiscoverySession)
        self.model_cisco = models.Model(name='cisco',
                                        pattern='Cisco',
                                        vlan=True)
        self.switch_config = models.Config(models=(self.model_cisco,),
                                           default_oids={})

    def test_update_snmp_community(self, mock_discovery_session, mock_session):
        mock_discovery_session.return_value = self.session

        self.session.auto_discover_vlan.return_value = ['123']
        self.session.auto_discover_ifindex.return_value = [('uplink', '10')]

        actual = disco_config.update_snmp_community(
            self.session, 'hostname', self.switch_config, 'community1')

        self.assertEqual('community1@123', actual)
        mock_session.assert_called_with(DestHost='hostname',
                                        Version=2,
                                        Community='community1@123')

    def test_update_snmp_community_raises_SystemExit_when_no_vlans_found(
            self, mock_discovery_session, mock_session):
        mock_discovery_session.return_value = self.session

        self.session.auto_discover_vlan.return_value = ['123']
        self.session.auto_discover_ifindex.return_value = []

        with self.assertRaises(SystemExit):
            disco_config.update_snmp_community(self.session, 'hostname',
                                               self.switch_config, 'community1')

        mock_session.assert_called_with(DestHost='hostname',
                                        Version=2,
                                        Community='community1@123')

    def test_update_snmp_community_raises_SystemExit_for_ifindex_error(
            self, mock_discovery_session, mock_session):
        mock_discovery_session.return_value = self.session

        self.session.auto_discover_vlan.return_value = ['123']
        self.session.auto_discover_ifindex.side_effect = discovery.Error(
            'whoops')

        with self.assertRaises(SystemExit):
            disco_config.update_snmp_community(self.session, 'hostname',
                                               self.switch_config, 'community1')

        mock_session.assert_called_with(DestHost='hostname',
                                        Version=2,
                                        Community='community1@123')


class GenerateCollectdConfigTest(unittest.TestCase):
    """Tests for disco_config.generate_collectd_snmp_config."""

    def setUp(self):
        logging.basicConfig(filename='/dev/null')
        self.session = mock.Mock(spec=discovery.DiscoverySession)
        oids = {'discards': models.OID(rx='rx.{ifIndex}', tx='tx.{ifIndex}')}
        self.model_cisco = models.Model(name='cisco',
                                        pattern='Cisco',
                                        vlan=True,
                                        oids=oids)

    def test_generate_collectd_snmp_config(self):
        expected_plugin_config = """LoadPlugin snmp
<Plugin snmp>
  <Data "uplink_discards">
    Type "ifx_discards"
    Table false
    Instance "uplink"
    Values "rx.10" "tx.10"
  </Data>

  <Host "hostname">
    Address "hostname"
    Version 2
    Community "community1"
    Collect "uplink_discards"
    Interval 10
  </Host>
</Plugin>"""

        self.session.get_model.return_value = self.model_cisco
        self.session.auto_discover_ifindex.return_value = [('uplink', '10')]

        actual = disco_config.generate_collectd_snmp_config(
            self.session, 'hostname', 'community1')

        self.assertEqual(expected_plugin_config, actual)

    def test_generate_collectd_snmp_config_raises_SystemExit_for_model_error(
            self):
        self.session.get_model.side_effect = models.Error('whoops')

        with self.assertRaises(SystemExit):
            disco_config.generate_collectd_snmp_config(self.session, 'hostname',
                                                       'community1')

    def test_generate_collectd_snmp_config_raises_SystemExit_for_ifindex_error(
            self):
        self.session.get_model.return_value = self.model_cisco
        self.session.auto_discover_ifindex.side_effect = discovery.Error(
            'whoops')

        with self.assertRaises(SystemExit):
            disco_config.generate_collectd_snmp_config(self.session, 'hostname',
                                                       'community1')


if __name__ == '__main__':
    unittest.main()
