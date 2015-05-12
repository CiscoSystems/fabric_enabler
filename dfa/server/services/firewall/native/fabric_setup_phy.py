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
# @author: Padmanabhan Krishnan, Cisco Systems, Inc.

import netaddr
from six import moves

from dfa.common import config
from dfa.common import utils
from dfa.common import dfa_exceptions as dexc
from dfa.common import dfa_logger as logging
from dfa.server.dfa_openstack_helper import DfaNeutronHelper as OsHelper
import dfa.server.services.firewall.native.fw_constants as fw_const

LOG = logging.getLogger(__name__)


class ServiceIpSegTenantMap(object):

    ''' Class for storing the local IP/Seg for service tenants'''

    def __init__(self):
        self.fabric_status = False

    def store_in_ip_addr(self, subnet, start, end, gateway):
        self.in_subnet = subnet
        self.in_start_ip = start
        self.in_end_ip = end
        self.in_gateway = gateway

    def store_out_ip_addr(self, subnet, start, end, gateway):
        self.out_subnet = subnet
        self.out_start_ip = start
        self.out_end_ip = end
        self.out_gateway = gateway

    def store_in_vlan_mob_dom(self, mob_dom, vlan):
        self.in_mob_dom = mob_dom
        self.in_vlan = vlan

    def store_out_vlan_mob_dom(self, mob_dom, vlan):
        self.out_mob_dom = mob_dom
        self.out_vlan = vlan

    def store_dummy_router_net(self, net_id, subnet_id, rout_id):
        self.dummy_net_id = net_id
        self.dummy_subnet_id = subnet_id
        self.dummy_router_id = rout_id

    def get_in_vlan_mob_dom(self):
        return self.in_vlan, self.in_mob_dom

    def get_out_vlan_mob_dom(self):
        return self.out_vlan, self.out_mob_dom

    def get_in_ip_addr(self):
        return self.in_subnet, self.in_start_ip, self.in_end_ip,\
            self.in_gateway

    def get_out_ip_addr(self):
        return self.out_subnet, self.out_start_ip, self.out_end_ip,\
            self.out_gateway

    def get_dummy_router_net(self):
        return self.dummy_net_id, self.dummy_subnet_id, self.dummy_router_id

    def set_fabric_create(self, status):
        self.fabric_status = status

    def is_fabric_create(self):
        return self.fabric_status


class FabricPhys(object):

    '''Class to implement Fabric configuration for Physical FW'''

    def __init__(self):
        cfg = config.CiscoDFAConfig().cfg
        LOG.debug("Entered FabricPhys")
        self.auto_nwk_create = cfg.firewall.fw_auto_serv_nwk_create
        self.serv_vlan_min = int(cfg.firewall.fw_service_vlan_id_min)
        self.serv_vlan_max = int(cfg.firewall.fw_service_vlan_id_max)
        self.mob_domain_name = cfg.firewall.mob_domain_name
        self.serv_host_prof = cfg.firewall.fw_service_host_profile
        self.serv_host_mode = cfg.firewall.fw_service_host_fwd_mode
        self.serv_ext_prof = cfg.firewall.fw_service_ext_profile
        self.serv_ext_mode = cfg.firewall.fw_service_ext_fwd_mode
        self.serv_part_vrf_prof = cfg.firewall.fw_service_part_vrf_profile
        self.serv_mgmt_ip = cfg.firewall.fw_mgmt_ip
        self.service_vlans = set(moves.xrange(
                                 self.serv_vlan_min, self.serv_vlan_max))
        self.service_in_ip_start = cfg.firewall.fw_service_in_ip_start
        self.service_in_ip_end = cfg.firewall.fw_service_in_ip_end
        self.service_out_ip_start = cfg.firewall.fw_service_out_ip_start
        self.service_out_ip_end = cfg.firewall.fw_service_out_ip_end
        self.servicedummy_ip_subnet = cfg.firewall.fw_service_dummy_ip_subnet
        self.in_ip_next = self.service_in_ip_start.partition("/")[0]
        self.max_in_ip = self.service_in_ip_end.partition("/")[0]
        self.out_ip_next = self.service_out_ip_start.partition("/")[0]
        self.max_out_ip = self.service_out_ip_end.partition("/")[0]
        self.service_attr = {}
        self.os_helper = OsHelper()

    def get_next_ip(self, direc):
        '''This needs to be put in a common functionality for services'''

        if direc == 'in':
            temp_ip = self.in_ip_next
            self.in_ip_next = str(netaddr.IPAddress(temp_ip) + 256)
            max_ip = self.max_in_ip
        else:
            temp_ip = self.out_ip_next
            self.out_ip_next = str(netaddr.IPAddress(temp_ip) + 256)
            max_ip = self.max_out_ip
        if netaddr.IPAddress(temp_ip) > netaddr.IPAddress(max_ip):
            LOG.error("Max IP address allocated for Firewall service"
                      " %s network" % direc)
            return None, None, None
        gateway = str(netaddr.IPAddress(temp_ip) + 1)
        start = str(netaddr.IPAddress(temp_ip) + 2)
        end = str(netaddr.IPAddress(temp_ip) + 254)
        return temp_ip, start, end, gateway

    def fill_subnet_info(self, tenant_id, direc):
        subnet_dict = {}
        subnet_dict['enable_dhcp'] = False
        subnet_dict['tenant_id'] = tenant_id
        subnet_dict['allocation_pools'] = {}
        if direc == 'in':
            subnet_dict['name'] = fw_const.IN_SERVICE_SUBNET
            subnet, start, end, gateway = self.get_next_ip(direc)
            self.service_attr[tenant_id].store_in_ip_addr(subnet, start, end,
                                                          gateway)
        else:
            subnet_dict['name'] = fw_const.OUT_SERVICE_SUBNET
            subnet, start, end, gateway = self.get_next_ip(direc)
            self.service_attr[tenant_id].store_out_ip_addr(subnet, start, end,
                                                           gateway)
        subnet_dict['cidr'] = subnet + '/24'
        alloc_pool_dict = {}
        alloc_pool_dict['start'] = start
        alloc_pool_dict['end'] = end
        subnet_dict['allocation_pools'] = []
        subnet_dict['allocation_pools'].append(alloc_pool_dict)
        subnet_dict['gateway_ip'] = gateway
        # NWK ID and subnet ID are not filled TODO
        subnet_dict['ip_version'] = 4
        return subnet_dict

    def fill_net_info(self, tenant_id, direc):
        net_dict = {}
        net_dict['status'] = 'ACTIVE'
        net_dict['admin_state_up'] = True
        net_dict['tenant_id'] = tenant_id
        net_dict['provider:network_type'] = 'local'
        # This needs to be put in a common functionality for services TODO
        net_dict['segmentation_id'] = self.service_vlans.pop()
        net_dict['mob_domain'] = True
        net_dict['mob_domain_name'] = self.mob_domain_name
        serv_obj = self.service_attr[tenant_id]
        # NWK ID are not filled TODO
        if direc == 'in':
            net_dict['name'] = fw_const.IN_SERVICE_NWK
            net_dict['part_name'] = None
            net_dict['config_profile'] = self.serv_host_prof
            net_dict['fwd_mode'] = self.serv_host_mode
            serv_obj.store_in_vlan_mob_dom(self.mob_domain_name,
                                           net_dict['segmentation_id'])
        else:
            net_dict['name'] = fw_const.OUT_SERVICE_NWK
            net_dict['config_profile'] = self.serv_ext_prof
            net_dict['part_name'] = fw_const.SERV_PART_NAME
            net_dict['fwd_mode'] = self.serv_ext_mode
            serv_obj.store_out_vlan_mob_dom(self.mob_domain_name,
                                            net_dict['segmentation_id'])
        return net_dict

    def _create_service_nwk(self, tenant_id, tenant_name, direc):
        ''' Function to create the service in network in DCNM'''

        subnet_dict = self.fill_subnet_info(tenant_id, direc)
        subnet = utils.dict_to_obj(subnet_dict)
        net_dict = self.fill_net_info(tenant_id, direc)
        net = utils.dict_to_obj(net_dict)
        try:
            self.dcnm_obj.create_service_network(tenant_name, net, subnet)
        except dexc.DfaClientRequestFailed:
            # Dump the whole message
            LOG.exception("Failed to create network in DCNM %s" % direc)

    def _delete_service_nwk(self, tenant_id, tenant_name, direc):
        ''' Function to delete the service in network in DCNM'''

        net_dict = {}
        if direc == 'in':
            vlan, mob_dom = self.get_in_vlan_mob_domain(tenant_id)
            net_dict['part_name'] = None
        else:
            vlan, mob_dom = self.get_out_vlan_mob_domain(tenant_id)
            net_dict['part_name'] = fw_const.SERV_PART_NAME
        net_dict['segmentation_id'] = vlan
        net_dict['mob_domain_name'] = mob_dom
        net = utils.dict_to_obj(net_dict)
        try:
            self.dcnm_obj.delete_service_network(tenant_name, net)
        except dexc.DfaClientRequestFailed:
            # Dump the whole message
            LOG.exception("Failed to delete network in DCNM %s" % direc)

    def get_in_vlan_mob_domain(self, tenant_id):
        if tenant_id not in self.service_attr:
            LOG.error("Fabric not prepared for tenant %s, %s" %
                      (tenant_id, tenant_name))
            return None, None
        tenant_obj = self.service_attr[tenant_id]
        return tenant_obj.get_in_vlan_mob_dom()

    def get_out_vlan_mob_domain(self, tenant_id):
        if tenant_id not in self.service_attr:
            LOG.error("Fabric not prepared for tenant %s, %s" %
                      (tenant_id, tenant_name))
            return None, None
        tenant_obj = self.service_attr[tenant_id]
        return tenant_obj.get_out_vlan_mob_dom()

    def get_in_ip_addr(self, tenant_id):
        if tenant_id not in self.service_attr:
            LOG.error("Fabric not prepared for tenant %s, %s" %
                      (tenant_id, tenant_name))
            return None, None
        tenant_obj = self.service_attr[tenant_id]
        return tenant_obj.get_in_ip_addr()

    def get_out_ip_addr(self, tenant_id):
        if tenant_id not in self.service_attr:
            LOG.error("Fabric not prepared for tenant %s, %s" %
                      (tenant_id, tenant_name))
            return None, None
        tenant_obj = self.service_attr[tenant_id]
        return tenant_obj.get_out_ip_addr()

    def get_dummy_router_net(self, tenant_id):
        if tenant_id not in self.service_attr:
            LOG.error("Fabric not prepared for tenant %s, %s" %
                      (tenant_id, tenant_name))
            return None, None, None
        tenant_obj = self.service_attr[tenant_id]
        return tenant_obj.get_dummy_router_net()

    def _create_out_partition(self, tenant_id, tenant_name):
        ''' Function to create a service partition'''

        vrf_prof_str = self.serv_part_vrf_prof
        self.dcnm_obj.create_partition(tenant_name, fw_const.SERV_PART_NAME,
                                       None, vrf_prof_str,
                                       desc="Service Partition")

    def _update_partition(self, tenant_id, tenant_name, ip, vrf_prof=None,
                          part_name=None):
        ''' Function to update a  partition'''

        self.dcnm_obj.update_project(tenant_name, part_name, None,
                                     service_node_ip=ip, vrf_prof=vrf_prof,
                                     desc="Service Partition")

    def _update_partition_in_create(self, tenant_id, tenant_name):
        ''' Function to update a  partition'''

        sub, in_ip, in_ip_end, gw = self.get_in_ip_addr(tenant_id)
        self._update_partition(tenant_id, tenant_name, in_ip)

    def _update_partition_in_delete(self, tenant_id, tenant_name):
        ''' Function to update a  partition'''

        self._update_partition(tenant_id, tenant_name, None)

    def _update_partition_out_create(self, tenant_id, tenant_name):
        ''' Function to update a  partition'''

        vrf_prof = self.serv_part_vrf_prof
        sub, out_ip, out_ip_end, gw = self.get_out_ip_addr(tenant_id)
        self._update_partition(tenant_id, tenant_name, out_ip,
                               vrf_prof=vrf_prof,
                               part_name=fw_const.SERV_PART_NAME)

    def _delete_partition(self, tenant_id, tenant_name):
        ''' Function to delete a service partition'''

        vrf_prof_str = self.serv_part_vrf_prof
        self.dcnm_obj.delete_partition(tenant_name, fw_const.SERV_PART_NAME)

    def _create_dummy_router_and_intf(self, tenant_id, tenant_name):
        '''Function to create a dummy router and interface'''

        net_id, subnet_id = self.os_helper.create_network(
            fw_const.DUMMY_SERVICE_NWK, tenant_id,
            self.servicedummy_ip_subnet)
        rout_id = self.os_helper.create_router(fw_const.DUMMY_SERVICE_RTR,
                                               tenant_id, subnet_id)
        serv_obj = self.service_attr[tenant_id]
        serv_obj.store_dummy_router_net(net_id, subnet_id, rout_id)

    def _delete_dummy_router_and_intf(self, tenant_id, tenant_name):
        '''Function to create a dummy router and interface'''

        net_id, subnet_id, rout_id = self.get_dummy_router_net(tenant_id)
        self.os_helper.delete_router(fw_const.DUMMY_SERVICE_RTR,
                                     tenant_id, subnet_id, rout_id)
        self.os_helper.delete_network(fw_const.DUMMY_SERVICE_NWK, tenant_id,
                                      subnet_id, net_id)

    def prepare_fabric_fw(self, tenant_id, tenant_name):
        if not self.auto_nwk_create:
            LOG.info("Auto network creation disabled")
            return
        try:
            if tenant_id in self.service_attr and (
               self.service_attr[tenant_id].is_fabric_create()):
                LOG.error("Fabric already prepared for tenant %s, %s" %
                          (tenant_id, tenant_name))
                return
            # Exceptions to be added for everything TODO
            # Also retry mechanism and state for which failed TODO
            self.service_attr[tenant_id] = ServiceIpSegTenantMap()
            self._create_service_nwk(tenant_id, tenant_name, 'in')
            LOG.info("In Service network created for tenant %s" % tenant_id)
            self._update_partition_in_create(tenant_id, tenant_name)
            LOG.info("In partition updated with service ip addr")
            self._create_out_partition(tenant_id, tenant_name)
            LOG.info("Out partition created")
            self._create_service_nwk(tenant_id, tenant_name, 'out')
            LOG.info("out Service network created for tenant %s" % tenant_id)
            self._update_partition_out_create(tenant_id, tenant_name)
            LOG.info("Out partition updated with service ip addr")
            self._create_dummy_router_and_intf(tenant_id, tenant_name)
            LOG.info("Dummy Router and interface created")
            self.service_attr[tenant_id].set_fabric_create(True)
        except Exception as e:
            LOG.error("Exception raised in create fabric %s" % e)

    def delete_fabric_fw(self, tenant_id, tenant_name):
        if tenant_id not in self.service_attr:
            LOG.error("Fabric not prepared for tenant %s, %s" %
                      (tenant_id, tenant_name))
            return
        if not self.service_attr[tenant_id].is_fabric_create():
            LOG.error("Fabric for tenant %s, %s already deleted" %
                      (tenant_id, tenant_name))
        try:
            self._delete_service_nwk(tenant_id, tenant_name, 'in')
            LOG.info("In Service network deleted for tenant %s" % tenant_id)
            self._delete_service_nwk(tenant_id, tenant_name, 'out')
            LOG.info("Out Service network deleted for tenant %s" % tenant_id)
            self._update_partition_in_delete(tenant_id, tenant_name)
            LOG.info("In partition service ip addr deleted")
            self._delete_partition(tenant_id, tenant_name)
            LOG.info("Out partition deleted")
            self._delete_dummy_router_and_intf(tenant_id, tenant_name)
            LOG.info("Dummy Router and interface deleted")
            del(self.service_attr[tenant_id])
        except Exception as e:
            LOG.error("Exception raised in delete fabric %s" % e)
