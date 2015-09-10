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

import time

from dfa.common import config
from dfa.common import dfa_logger as logging
from dfa.server.services.firewall.native import fabric_setup_base as FP
from dfa.server.services.firewall.native.drivers import base
import dfa.server.services.firewall.native.fw_constants as fw_const
from dfa.server.dfa_openstack_helper import DfaNeutronHelper as OsHelper

LOG = logging.getLogger(__name__)


class NativeFw(base.BaseDrvr, FP.FabricApi):

    '''Native Firewall Driver'''

    def __init__(self):
        ''' Class init '''
        LOG.debug("Initializing Native Firewall")
        super(NativeFw, self).__init__()
        self.tenant_dict = {}
        self.os_helper = OsHelper()
        self.cfg = config.CiscoDFAConfig().cfg
        self.mgmt_ip_addr = None
        self.dcnm_obj = None
        self.que_obj = None

    def initialize(self, cfg_dict):
        ''' Initialization routine '''
        LOG.debug("Initialize for NativeFw")
        self.mgmt_ip_addr = cfg_dict.get('mgmt_ip_addr')

    def pop_evnt_que(self, que_obj):
        ''' Populate the event queue object '''
        LOG.debug("Pop Event for NativeFw")
        self.que_obj = que_obj

    def pop_dcnm_obj(self, dcnm_obj):
        ''' Populate the DCNM object '''
        LOG.debug("Pop DCNM for NativeFw")
        self.dcnm_obj = dcnm_obj

    def is_device_virtual(self):
        ''' Returns if device is virtual '''
        return True

    def get_name(self):
        ''' Returns the name of the FW appliance '''
        # Put it in a constant fixme(padkrish)
        return 'native'

    def get_max_quota(self):
        # Return the right value fixme
        '''
        Returns the maximum number of FW instance that a single FW can
        support
        '''
        return 1

    def create_router(self, tenant_id, tenant_name):
        ''' Routine to create a Openstack router for the FW '''
        in_sub = self.get_in_subnet_id(tenant_id)
        out_sub = self.get_out_subnet_id(tenant_id)
        # Modify Hard coded Name fixme
        subnet_lst = set()
        subnet_lst.add(in_sub)
        subnet_lst.add(out_sub)
        rtr_list = self.os_helper.get_rtr_by_name('FW_RTR_' + tenant_name)
        if len(rtr_list) == 0:
            rout_id = self.os_helper.create_router('FW_RTR_' + tenant_name,
                                                   tenant_id, subnet_lst)
        else:
            rout_id = rtr_list[0].get('id')
        return rout_id, in_sub, out_sub

    def get_rtr_id(self, tenant_id, tenant_name):
        ''' Retrieve the router ID '''
        rout_id = None
        if tenant_id in self.tenant_dict:
            if 'rout_id' in self.tenant_dict.get(tenant_id):
                rout_id = self.tenant_dict.get(tenant_id).get('rout_id')
        if rout_id is None:
            rtr_list = self.os_helper.get_rtr_by_name('FW_RTR_' + tenant_name)
            if len(rtr_list) > 0:
                rout_id = rtr_list[0].get('id')
        return rout_id

    def delete_router(self, tenant_id, tenant_name):
        ''' Routine to delete the router '''
        in_sub = self.get_in_subnet_id(tenant_id)
        out_sub = self.get_out_subnet_id(tenant_id)
        # Modify Hard coded Name fixme
        subnet_lst = set()
        subnet_lst.add(in_sub)
        subnet_lst.add(out_sub)
        rout_id = None
        rout_id = self.get_rtr_id(tenant_id, tenant_name)
        if rout_id is not None:
            ret = self.os_helper.delete_router('FW_RTR_' + tenant_name,
                                               tenant_id, rout_id, subnet_lst)
            if not ret:
                LOG.error("Failed to delete router id %(rout)s, tenant "
                          "%(tenant)s", {'rout': rout_id, 'tenant': tenant_id})
            return ret
        else:
            return False

    def prepare_rout_vm_msg(self, tenant_id, tenant_name, rout_id, net_id,
                            subnet_id, seg, status):
        '''
        Prepare the message to be sent to Event queue for VDP trigger.
        This is actually called for a subnet add to a router. This function
        prepares a VM's VNIC create/delete message
        '''
        flag = True
        cnt = 0
        while flag:
            port_data = self.os_helper.get_router_port_subnet(subnet_id)
            if port_data is None:
                LOG.error("Unable to get router port data")
                return None
            if port_data.get('binding:host_id') == '':
                time.sleep(3)
                cnt = cnt + 1
                if cnt > 3:
                    flag = False
            else:
                flag = False
        if status is 'up':
            event_type = 'service.vnic.create'
        else:
            event_type = 'service.vnic.delete'
        vnic_data = {}
        vnic_data['status'] = status
        vnic_data['mac'] = port_data.get('mac_address')
        vnic_data['segid'] = seg
        vnic_data['host'] = port_data.get('binding:host_id')
        if vnic_data['host'] == '':
            LOG.error("Null host for seg %(seg)s subnet %(subnet)s",
                      {'seg': seg, 'subnet': subnet_id})
            if self.tenant_dict.get(tenant_id).get('host') is None:
                LOG.error("Null host for tenant %(tenant)s seg %(seg)s "
                          "subnet %(subnet)s",
                          {'tenant': tenant_id, 'seg': seg,
                           'subnet': subnet_id})
                return None
            else:
                vnic_data['host'] = self.tenant_dict.get(tenant_id).get('host')
        else:
            self.tenant_dict[tenant_id]['host'] = vnic_data['host']
        vnic_data['port_id'] = port_data.get('id')
        vnic_data['network_id'] = net_id
        vnic_data['vm_name'] = 'FW_SRVC_RTR_' + tenant_name
        vnic_data['vm_ip'] = port_data.get('fixed_ips')[0].get('ip_address')
        vnic_data['vm_uuid'] = rout_id
        vnic_data['gw_mac'] = None
        vnic_data['fwd_mod'] = 'anycast_gateway'
        payload = {'service': vnic_data}
        data = (event_type, payload)
        return data

    def send_rout_port_msg(self, tenant_id, tenant_name, rout_id, net_id,
                           subnet_id, seg, status):
        ''' Sends the router port message to the queue '''
        data = self.prepare_rout_vm_msg(tenant_id, tenant_name, rout_id,
                                        net_id, subnet_id, seg, status)
        if data is None:
            return False
        timestamp = time.ctime()
        # Remove hardcoding fixme (PRI_LOW_START)
        pri = 30 + 4
        LOG.info("Sending native FW data into queue %(data)s", {'data': data})
        self.que_obj.put((pri, timestamp, data))
        return True

    def create_tenant_dict(self, tenant_id, rout_id=None):
        ''' Tenant dict creation '''
        self.tenant_dict[tenant_id] = {}
        self.tenant_dict[tenant_id]['host'] = None
        self.tenant_dict[tenant_id]['rout_id'] = rout_id

    def _create_fw(self, tenant_id, data):
        ''' Internal routine that gets called when a FW is created '''
        LOG.debug("In creating phy ASA FW data is %s", data)
        tenant_name = data.get('tenant_name')
        in_seg, in_vlan, in_mob_dom = \
            self.get_in_seg_vlan_mob_dom(tenant_id)
        out_seg, out_vlan, out_mob_dom = \
            self.get_out_seg_vlan_mob_dom(tenant_id)
        # self.get_mgmt_ip_addr(tenant_id)
        # self.get_vlan_in_out(tenant_id)
        # Check if router is already added and only then add, needed for
        # restart cases since native doesn't have a special DB fixme
        rout_id, in_sub, out_sub = self.create_router(tenant_id, tenant_name)
        if rout_id is None:
            LOG.error("Native FW: Create router failed for tenant %s",
                      tenant_id)
            return False
        self.create_tenant_dict(tenant_id, rout_id)

        in_ip, in_start, in_end, in_gw = self.get_in_ip_addr(tenant_id)
        out_ip, out_start, out_end, out_gw = self.get_out_ip_addr(tenant_id)
        dummy_net, dummy_subnet, dummy_rtr = (
            self.get_dummy_router_net(tenant_id))
        dummy_cidr = self.os_helper.get_subnet_cidr(dummy_subnet)
        excl_list = []
        excl_list.append(in_ip)
        excl_list.append(out_ip)
        excl_list.append(dummy_cidr.split('/')[0])

        # Program DCNM to update profile's static IP address on OUT part
        ip_list = self.os_helper.get_subnet_nwk_excl(tenant_id, excl_list)
        srvc_node_ip = self.get_out_srvc_node_ip_addr(tenant_id)
        ret = self.dcnm_obj.update_partition_static_route(
            tenant_name,
            fw_const.SERV_PART_NAME, ip_list,
            vrf_prof=self.cfg.firewall.fw_service_part_vrf_profile,
            service_node_ip=srvc_node_ip)
        if not ret:
            LOG.error("Unable to update DCNM ext profile with static route %s",
                      rout_id)
            ret = self.delete_router(tenant_id, tenant_name)
            return False

        # Program the default GW in router namespace
        ret = False
        cnt = 0
        if out_gw != 0:
            while not ret and cnt <= 3:
                time.sleep(3)
                ret = self.os_helper.program_rtr_default_gw(tenant_id, rout_id,
                                                            out_gw)
                cnt = cnt + 1
        if not ret:
            LOG.error("Unable to program default GW in router %s", rout_id)
            ret = self.delete_router(tenant_id, tenant_name)
            return False

        # Program router namespace to have all tenant networks to be routed
        # to IN service network
        ret = False
        if in_gw != 0:
            ret = self.os_helper.program_rtr_all_nwk_next_hop(
                tenant_id, rout_id, in_gw, excl_list)
            if not ret:
                LOG.error("Unable to program default router next hop %s",
                          rout_id)
                ret = self.delete_router(tenant_id, tenant_name)
                return False

        # Send message for router port auto config for in service nwk
        in_net = self.get_in_net_id(tenant_id)
        ret = self.send_rout_port_msg(tenant_id, tenant_name, rout_id, in_net,
                                      in_sub, in_seg, 'up')
        if not ret:
            LOG.error("Sending rout port message failed for in network "
                      "tenant %(tenant)s subnet %(seg)s",
                      {'tenant': tenant_id, 'seg': in_seg})
            ret = self.delete_router(tenant_id, tenant_name)
            return False

        # Send message for router port auto config for out service nwk
        out_net = self.get_out_net_id(tenant_id)
        ret = self.send_rout_port_msg(tenant_id, tenant_name, rout_id, out_net,
                                      out_sub, out_seg, 'up')
        if not ret:
            LOG.error("Sending rout port message failed for out network "
                      "tenant %(tenant)s subnet %(seg)s",
                      {'tenant': tenant_id, 'seg': out_seg})
            ret = self.send_rout_port_msg(tenant_id, tenant_name, rout_id,
                                          in_net, in_sub, in_seg, 'down')
            if not ret:
                LOG.error("Error case, sending rout port message for in nwk"
                          " tenant %(tenant)s subnet %(seg)s",
                          {'tenant': tenant_id, 'seg': in_seg})
            ret = self.delete_router(tenant_id, tenant_name)
            return False
        return True

    def create_fw(self, tenant_id, data):
        ''' Top level routine called when a FW is created '''
        try:
            ret = self._create_fw(tenant_id, data)
            return ret
        except Exception as e:
            LOG.error("Failed to create FW for device native, tenant "
                      "%(tenant)s data %(data)s", {'tenant': tenant_id,
                                                   'data': data})
            return False

    # Create exceptions for all these fixme
    def _delete_fw(self, tenant_id, data):
        ''' Internal routine called when a FW is deleted '''
        LOG.debug("In Delete fw data is %s", data)
        # Do the necessary stuffs in ASA
        tenant_name = data.get('tenant_name')
        in_seg, in_vlan, in_mob_dom = \
            self.get_in_seg_vlan_mob_dom(tenant_id)
        out_seg, out_vlan, out_mob_dom = \
            self.get_out_seg_vlan_mob_dom(tenant_id)
        in_net = self.get_in_net_id(tenant_id)
        out_net = self.get_out_net_id(tenant_id)
        in_sub = self.get_in_subnet_id(tenant_id)
        out_sub = self.get_out_subnet_id(tenant_id)

        rout_id = self.get_rtr_id(tenant_id, tenant_name)
        if rout_id is None:
            LOG.error("Router ID unknown for tenant %s", tenant_id)
            return False

        if tenant_id not in self.tenant_dict:
            self.create_tenant_dict(tenant_id, rout_id)
        ret = self.send_rout_port_msg(tenant_id, tenant_name, rout_id, in_net,
                                      in_sub, in_seg, 'down')
        if not ret:
            LOG.error("Error case, sending rout port message for in nwk"
                      " tenant %(tenant)s subnet %(seg)s",
                      {'tenant': tenant_id, 'seg': in_seg})
        ret = self.send_rout_port_msg(tenant_id, tenant_name, rout_id, out_net,
                                      out_sub, out_seg, 'down')
        if not ret:
            LOG.error("Sending rout port message failed for out network "
                      "tenant %(tenant)s subnet %(seg)s",
                      {'tenant': tenant_id, 'seg': out_seg})
        # Usually sending message to queue doesn't fail!!!

        rout_ret = self.delete_router(tenant_id, tenant_name)
        if not rout_ret:
            LOG.error("Unable to delete router for tenant %s, error case",
                      tenant_id)
            return rout_ret
        del self.tenant_dict[tenant_id]
        return rout_ret

    def delete_fw(self, tenant_id, data):
        ''' Top level routine called when a FW is deleted '''
        try:
            ret = self._delete_fw(tenant_id, data)
            return ret
        except Exception as e:
            LOG.error("Failed to delete FW for device native, tenant "
                      "%(tenant)s data %(data)s", {'tenant': tenant_id,
                                                   'data': data})
            return False

    def modify_fw(self, tenant_id, data):
        '''
        Routine called when FW attributes gets modified. Nothing to be done
        for native FW.
        '''
        LOG.debug("In Modify fw data is %s", data)
