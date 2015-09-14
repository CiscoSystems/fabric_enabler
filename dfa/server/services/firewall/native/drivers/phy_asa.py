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

from dfa.common import dfa_logger as logging
from dfa.server.services.firewall.native import fabric_setup_base as FP
from dfa.server.services.firewall.native.drivers import base


LOG = logging.getLogger(__name__)


class PhyAsa(base.BaseDrvr, FP.FabricApi):

    '''Physical ASA Driver'''

    def __init__(self):
        LOG.debug("Initializing physical ASA")
        super(PhyAsa, self).__init__()

    def initialize(self, cfg_dict):
        LOG.debug("Initialize for PhyAsa")
        self.mgmt_ip_addr = cfg_dict.get('mgmt_ip_addr')

    def pop_evnt_que(self, que_obj):
        LOG.debug("Pop Event for PhyAsa")
        pass

    def nwk_create_notif(self, tenant_id, tenant_name, cidr):
        ''' Network Create Notification '''
        LOG.debug("Nwk Create Notif PhyAsa")
        pass

    def is_device_virtual(self):
        return False

    def get_name(self):
        # Put it in a constant TODO(padkrish)
        return 'phy_asa'

    def get_max_quota(self):
        # Return the right value TODO
        return 4

    def create_fw(self, tenant_id, data):
        LOG.debug("In creating phy ASA FW data is %s" % data)
        tenant_name = data.get('tenant_name')
        in_subnet, in_ip_start, in_ip_end, in_gw = (
            self.get_in_ip_addr(tenant_id))
        out_subnet, out_ip_start, out_ip_end, out_ip_gw = (
            self.get_out_ip_addr(tenant_id))
        in_seg, in_vlan = self.get_in_seg_vlan(tenant_id)
        out_seg, out_vlan = self.get_out_seg_vlan(tenant_id)
        # Setup the physical ASA appliance
        # self.get_mgmt_ip_addr(tenant_id)
        # self.get_vlan_in_out(tenant_id)
        return True

    def delete_fw(self, tenant_id, data):
        LOG.debug("In Delete fw data is %s" % data)
        # Do the necessary stuffs in ASA
        tenant_name = data.get('tenant_name')
        return True

    def modify_fw(self, tenant_id, data):
        LOG.debug("In Modify fw data is %s" % data)
