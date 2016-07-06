# Copyright 2015 Cisco Systems, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#


import mock

from neutron.tests import base

from networking_cisco.plugins.saf.common import config
from networking_cisco.plugins.saf.server import cisco_dfa_rest as dc

"""This file includes test cases for cisco_dfa_rest.py."""

FAKE_DCNM_IP = '1.1.1.1'
FAKE_DCNM_USERNAME = 'dcnmuser'
FAKE_DCNM_PASSWD = 'dcnmpass'


class TestNetwork(object):
    segmentation_id = 123456
    name = 'cisco_test_network'
    config_profile = 'defaultL2ConfigProfile'


class TestCiscoDFAClient(base.BaseTestCase):
    """Test cases for DFARESTClient."""

    def setUp(self):
        # Declare the test resource.
        super(TestCiscoDFAClient, self).setUp()

        # Setting DCNM credentials.
        config.default_dcnm_opts['dcnm']['dcnm_ip'] = FAKE_DCNM_IP
        config.default_dcnm_opts['dcnm']['dcnm_user'] = FAKE_DCNM_USERNAME
        config.default_dcnm_opts['dcnm']['dcnm_password'] = FAKE_DCNM_PASSWD
        config.default_dcnm_opts['dcnm']['timeout_resp'] = 0.01
        self.cfg = config.CiscoDFAConfig().cfg

        self.dcnm_client = dc.DFARESTClient(self.cfg)
        mock.patch.object(self.dcnm_client, '_send_request').start()
        mock.patch.object(self.dcnm_client, '_login').start()
        mock.patch.object(self.dcnm_client, '_logout').start()
        self.dcnm_client._send_request.return_value = mock.Mock()
        self.dcnm_client._send_request.return_value.status_code = 200
        self.testnetwork = TestNetwork()

    def test_create_project(self):
        """Test create project."""

        orch_id = 'Openstack'
        org_name = 'Cisco'
        part_name = self.dcnm_client._part_name
        dci = 100
        self.dcnm_client.create_project(orch_id, org_name, part_name, dci)
        call_cnt = self.dcnm_client._send_request.call_count
        self.assertEqual(2, call_cnt)

        # Check call to partition and organization happens.
        org_pyld = {
            'organizationName': org_name,
            'description': org_name,
            'orchestrationSource': "Openstack Controller"}
        part_pyld = {
            'partitionName': part_name,
            'organizationName': org_name,
            'description': org_name}
        org_url = self.dcnm_client._org_url
        part_url = self.dcnm_client._create_part_url % org_name
        expected_calls = [mock.call('POST', org_url, org_pyld, 'organization'),
                          mock.call('POST', part_url, part_pyld, 'partition')]
        self.assertEqual(expected_calls,
                         self.dcnm_client._send_request.call_args_list)

    def test_create_network(self):
        """Test create network."""

        network_info = {}
        cfg_args = []
        seg_id = str(self.testnetwork.segmentation_id)
        config_profile = self.testnetwork.config_profile
        network_name = self.testnetwork.name
        tenant_name = 'Cisco'
        part_name = self.dcnm_client._part_name
        url = self.dcnm_client._create_network_url % (tenant_name, part_name)

        cfg_args.append("$segmentId=" + seg_id)
        cfg_args.append("$netMaskLength=16")
        cfg_args.append("$gatewayIpAddress=30.31.32.1")
        cfg_args.append("$networkName=" + network_name)
        cfg_args.append("$vlanId=0")
        cfg_args.append("$vrfName=%s:%s" % (tenant_name, part_name))
        cfg_args = ';'.join(cfg_args)

        dhcp_scopes = {'ipRange': '10.11.12.14-10.11.12.254',
                       'subnet': '10.11.12.13',
                       'gateway': '10.11.12.1'}

        network_info = {"segmentId": seg_id,
                        "vlanId": "0",
                        "mobilityDomainId": "None",
                        "profileName": config_profile,
                        "networkName": network_name,
                        "configArg": cfg_args,
                        "organizationName": tenant_name,
                        "partitionName": part_name,
                        "description": network_name,
                        "dhcpScope": dhcp_scopes}

        self.dcnm_client._create_network(network_info)
        self.dcnm_client._send_request.assert_called_with('POST', url,
                                                          network_info,
                                                          'network')

    def test_delete_network(self):
        """Test delete network."""

        seg_id = self.testnetwork.segmentation_id
        tenant_name = 'cisco'
        part_name = self.dcnm_client._part_name
        url = self.dcnm_client._network_url % (tenant_name, part_name, seg_id)
        self.dcnm_client.delete_network(tenant_name, self.testnetwork)
        self.dcnm_client._send_request.assert_called_with('DELETE', url,
                                                          '', 'network')

    def test_delete_project(self):
        """Test delete tenant."""

        tenant_name = 'cisco'
        part_name = self.dcnm_client._part_name
        self.dcnm_client.delete_project(tenant_name, part_name)
        call_cnt = self.dcnm_client._send_request.call_count
        self.assertEqual(2, call_cnt)

        # Check the calls to delete partition and org happens.
        del_org_url = self.dcnm_client._del_org_url % tenant_name
        del_part_url = self.dcnm_client._del_part % (tenant_name, part_name)
        expected_calls = [mock.call('DELETE', del_part_url, '', 'partition'),
                          mock.call('DELETE', del_org_url, '', 'organization')]
        self.assertEqual(expected_calls,
                         self.dcnm_client._send_request.call_args_list)
