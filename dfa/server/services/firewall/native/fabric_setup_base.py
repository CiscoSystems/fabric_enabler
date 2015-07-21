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
#    under the License.update_fw_local_result
#
# @author: Padmanabhan Krishnan, Cisco Systems, Inc.

import netaddr
from six import moves

from dfa.common import config
from dfa.common import constants as const
from dfa.common import utils
from dfa.common import dfa_exceptions as dexc
from dfa.common import dfa_logger as logging
from dfa.common import utils as sys_utils
from dfa.db import dfa_db_models as dfa_dbm
from dfa.server.dfa_openstack_helper import DfaNeutronHelper as OsHelper
import dfa.server.services.firewall.native.fw_constants as fw_const

LOG = logging.getLogger(__name__)


class ServiceIpSegTenantMap(dfa_dbm.DfaDBMixin):

    ''' Class for storing the local IP/Seg for service tenants'''

    def __init__(self):
        self.fabric_status = False
        self.in_dcnm_net_dict = {}
        self.out_dcnm_net_dict = {}
        self.in_dcnm_subnet_dict = {}
        self.out_dcnm_subnet_dict = {}
        self.state = fw_const.INIT_STATE
        self.result = fw_const.RESULT_FW_CREATE_INIT
        self.fw_dict = {}

    def update_fw_dict(self, fw_dict):
        self.fw_dict.update(fw_dict)

    def get_fw_dict(self):
        return self.fw_dict

    def store_dummy_router_net(self, net_id, subnet_id, rout_id):
        self.dummy_net_id = net_id
        self.dummy_subnet_id = subnet_id
        self.dummy_router_id = rout_id

    def get_dcnm_net_dict(self, direc):
        if direc == 'in':
            return self.in_dcnm_net_dict
        else:
            return self.out_dcnm_net_dict

    def store_dcnm_net_dict(self, net_dict, direc):
        if direc == 'in':
            self.in_dcnm_net_dict = net_dict
        else:
            self.out_dcnm_net_dict = net_dict

    def get_dcnm_subnet_dict(self, direc):
        if direc == 'in':
            return self.in_dcnm_subnet_dict
        else:
            return self.out_dcnm_subnet_dict

    def parse_subnet(self, subnet_dict):
        if len(subnet_dict) == 0:
            return 0, 0, 0, 0
        alloc_pool = subnet_dict.get('allocation_pools')
        cidr = subnet_dict.get('cidr')
        subnet = cidr.split('/')[0]
        start = alloc_pool[0].get('start')
        end = alloc_pool[0].get('end')
        gateway = subnet_dict.get('gateway_ip')
        return subnet, start, end, gateway

    def store_dcnm_subnet_dict(self, subnet_dict, direc):
        if direc == 'in':
            self.in_dcnm_subnet_dict = subnet_dict
            self.in_subnet, self.in_start_ip, self.in_end_ip,\
                self.in_gateway = self.parse_subnet(subnet_dict)
        else:
            self.out_dcnm_subnet_dict = subnet_dict
            self.out_subnet, self.out_start_ip, self.out_end_ip,\
                self.out_gateway = self.parse_subnet(subnet_dict)

    def get_in_seg_vlan_mob_dom(self):
        return self.in_dcnm_net_dict.get('segmentation_id'), \
            self.in_dcnm_net_dict.get('vlan_id'), \
            self.in_dcnm_net_dict.get('mob_domain_name')

    def get_out_seg_vlan_mob_dom(self):
        return self.out_dcnm_net_dict.get('segmentation_id'), \
            self.out_dcnm_net_dict.get('vlan_id'), \
            self.out_dcnm_net_dict.get('mob_domain_name')

    def get_in_ip_addr(self):
        if len(self.in_dcnm_subnet_dict) == 0:
            return 0, 0, 0, 0
        return self.in_subnet, self.in_start_ip, self.in_end_ip,\
            self.in_gateway

    def get_out_ip_addr(self):
        if len(self.out_dcnm_subnet_dict) == 0:
            return 0, 0, 0, 0
        return self.out_subnet, self.out_start_ip, self.out_end_ip,\
            self.out_gateway

    def get_dummy_router_net(self):
        return self.dummy_net_id, self.dummy_subnet_id, self.dummy_router_id

    def set_fabric_create(self, status):
        self.fabric_status = status

    def is_fabric_create(self):
        return self.fabric_status

    def create_fw_db(self, fw_id, fw_name, tenant_id):
        '''Create FW entry in DB'''

        fw_dict = dict()
        fw_dict['fw_id'] = fw_id
        fw_dict['name'] = fw_name
        fw_dict['tenant_id'] = tenant_id
        # FW DB is already created by FW Mgr
        # self.add_fw_db(fw_id, fw_dict)
        self.update_fw_dict(fw_dict)

    def destroy_local_fw_db(self):
        del self.fw_dict
        del self.in_dcnm_net_dict
        del self.in_dcnm_subnet_dict
        del self.out_dcnm_net_dict
        del self.out_dcnm_subnet_dict

    def update_fw_local_cache(self, net, direc, start):
        fw_dict = self.get_fw_dict()
        if direc == 'in':
            fw_dict['in_network_id'] = net
            fw_dict['in_service_ip'] = start
        else:
            fw_dict['out_network_id'] = net
            fw_dict['out_service_ip'] = start
        self.update_fw_dict(fw_dict)

    def update_fw_local_result_str(self, os_result=None, dcnm_result=None,
                                   dev_result=None):
        fw_dict = self.get_fw_dict()
        if os_result is not None:
            fw_dict['os_status'] = os_result
        if dcnm_result is not None:
            fw_dict['dcnm_status'] = dcnm_result
        if dev_result is not None:
            fw_dict['dev_status'] = dev_result
        self.update_fw_dict(fw_dict)

    def update_fw_local_result(self, os_result=None, dcnm_result=None,
                               dev_result=None):
        fw_dict = self.get_fw_dict()
        if self.result == fw_const.RESULT_FW_CREATE_INIT or (
           self.result == fw_const.RESULT_FW_CREATE_DONE):
            state_dict = fw_const.fw_state_dict
        else:
            if self.result == fw_const.RESULT_FW_DELETE_INIT or (
               self.result == fw_const.RESULT_FW_DELETE_DONE):
                state_dict = fw_const.fw_state_del_dict
            else:
                LOG.error("Error in updating local result")
                return
        if os_result is not None:
            fw_dict['os_status'] = state_dict.get(os_result)
        if dcnm_result is not None:
            fw_dict['dcnm_status'] = state_dict.get(dcnm_result)
        if dev_result is not None:
            fw_dict['dev_status'] = dev_result
        self.update_fw_dict(fw_dict)

    def update_fw_local_router(self, net_id, subnet_id, router_id, os_result):
        fw_dict = self.get_fw_dict()
        fw_dict['router_id'] = router_id
        fw_dict['router_net_id'] = net_id
        fw_dict['router_subnet_id'] = subnet_id
        self.store_dummy_router_net(net_id, subnet_id, router_id)
        self.update_fw_local_result(os_result=os_result)

    def commit_fw_db(self):
        fw_dict = self.get_fw_dict()
        self.update_fw_db(fw_dict.get('fw_id'), fw_dict)

    def commit_fw_db_result(self):
        fw_dict = self.get_fw_dict()
        self.update_fw_db_result(fw_dict.get('fw_id'), fw_dict)

    def store_local_final_result(self, final_res):
        self.result = final_res

    def get_store_local_final_result(self):
        fw_dict = self.get_fw_dict()
        fw_data = self.get_fw(fw_dict.get('fw_id'))
        res = fw_data.result
        self.store_local_final_result(res)

    def get_local_final_result(self):
        return self.result

    def store_state(self, state):
        self.state = state

    def get_state(self):
        return self.state


class FabricApi(object):
    serv_obj_dict = {}

    @classmethod
    def store_obj(cls, tenant_id, obj):
        cls.serv_obj_dict[tenant_id] = obj

    @classmethod
    def get_in_ip_addr(cls, tenant_id):
        if tenant_id not in cls.serv_obj_dict:
            LOG.error("Fabric not prepared for tenant %s" % tenant_id)
            return 0, 0, 0, 0
        tenant_obj = cls.serv_obj_dict.get(tenant_id)
        return tenant_obj.get_in_ip_addr()

    @classmethod
    def get_out_ip_addr(cls, tenant_id):
        if tenant_id not in cls.serv_obj_dict:
            LOG.error("Fabric not prepared for tenant %s" % tenant_id)
            return 0, 0, 0, 0
        tenant_obj = cls.serv_obj_dict.get(tenant_id)
        return tenant_obj.get_out_ip_addr()

    @classmethod
    def get_in_seg_vlan_mob_dom(cls, tenant_id):
        if tenant_id not in cls.serv_obj_dict:
            LOG.error("Fabric not prepared for tenant %s" % tenant_id)
            return None, None, None
        tenant_obj = cls.serv_obj_dict.get(tenant_id)
        return tenant_obj.get_in_seg_vlan_mob_dom()

    @classmethod
    def get_out_seg_vlan_mob_dom(cls, tenant_id):
        if tenant_id not in cls.serv_obj_dict:
            LOG.error("Fabric not prepared for tenant %s" % tenant_id)
            return None, None, None
        tenant_obj = cls.serv_obj_dict.get(tenant_id)
        return tenant_obj.get_out_seg_vlan_mob_dom()

    @classmethod
    def is_network_source_fw(cls, nwk, nwk_name):
        # Check if SOURCE is FIREWALL, if yes return TRUE
        # if source is None or entry not in NWK DB, check from Name
        # Name should have constant AND length should match
        if nwk is not None:
            if nwk.source == fw_const.FW_CONST:
                return True
            return False
        else:
            if nwk_name in fw_const.DUMMY_SERVICE_NWK and (
               len(nwk_name) == len(fw_const.DUMMY_SERVICE_NWK) + 8):
                return True
            if nwk_name in fw_const.IN_SERVICE_NWK and (
               len(nwk_name) == len(fw_const.IN_SERVICE_NWK) + 8):
                return True
            if nwk_name in fw_const.OUT_SERVICE_NWK and (
               len(nwk_name) == len(fw_const.OUT_SERVICE_NWK) + 8):
                return True
            return False

    def is_subnet_source_fw(cls, tenant_id, subnet):
        cfg = config.CiscoDFAConfig().cfg
        subnet = subnet.split('/')[0]
        sub, start, end, gw = cls.get_in_ip_addr(tenant_id)
        if sub == 0 or start == 0 or end == 0 or gw == 0:
            return False
        if sub == subnet:
            return True
        sub, start, end, gw = cls.get_out_ip_addr(tenant_id)
        if sub == 0 or start == 0 or end == 0 or gw == 0:
            return False
        if sub == subnet:
            return True
        dummy_sub = cfg.firewall.fw_service_dummy_ip_subnet
        dummy_sub = dummy_sub.split('/')[0]
        if subnet == dummy_sub:
            return True
        else:
            return False


class FabricBase(dfa_dbm.DfaDBMixin, FabricApi):

    '''Class to implement Fabric configuration for Physical FW'''

    def __init__(self):
        LOG.debug("Entered FabricPhys")
        # super(FabricBase, self).__init__()
        cfg = config.CiscoDFAConfig().cfg
        self.auto_nwk_create = cfg.firewall.fw_auto_serv_nwk_create
        self.serv_vlan_min = int(cfg.firewall.fw_service_vlan_id_min)
        self.serv_vlan_max = int(cfg.firewall.fw_service_vlan_id_max)
        self.serv_seg_min = int(cfg.dcnm.segmentation_id_min)
        self.serv_seg_max = int(cfg.dcnm.segmentation_id_max)
        self.mob_domain_name = cfg.firewall.mob_domain_name
        self.serv_host_prof = cfg.firewall.fw_service_host_profile
        self.serv_host_mode = cfg.firewall.fw_service_host_fwd_mode
        self.serv_ext_prof = cfg.firewall.fw_service_ext_profile
        self.serv_ext_mode = cfg.firewall.fw_service_ext_fwd_mode
        self.serv_part_vrf_prof = cfg.firewall.fw_service_part_vrf_profile
        self.serv_mgmt_ip = cfg.firewall.fw_mgmt_ip
        self.state = fw_const.INIT_STATE
        self.service_vlans = dfa_dbm.DfaSegmentTypeDriver(self.serv_vlan_min,
                                                          self.serv_vlan_max,
                                                          const.RES_VLAN,
                                                          cfg)
        self.service_segs = dfa_dbm.DfaSegmentTypeDriver(self.serv_seg_min,
                                                         self.serv_seg_max,
                                                         const.RES_SEGMENT,
                                                         cfg)
        self.service_in_ip_start = cfg.firewall.fw_service_in_ip_start
        self.service_in_ip_end = cfg.firewall.fw_service_in_ip_end
        self.mask = int(self.service_in_ip_start.split('/')[1])
        self.service_in_ip = dfa_dbm.DfasubnetDriver(self.service_in_ip_start,
                                                     self.service_in_ip_end,
                                                     const.RES_IN_SUBNET)
        self.service_out_ip_start = cfg.firewall.fw_service_out_ip_start
        self.service_out_ip_end = cfg.firewall.fw_service_out_ip_end
        self.service_out_ip = dfa_dbm.DfasubnetDriver(
            self.service_out_ip_start, self.service_out_ip_end,
            const.RES_OUT_SUBNET)
        self.servicedummy_ip_subnet = cfg.firewall.fw_service_dummy_ip_subnet
        self.service_attr = {}
        self.os_helper = OsHelper()
        self.fabric_fsm = dict()
        self.fabric_state_map = {
            fw_const.INIT_STATE: fw_const.OS_IN_NWK_STATE,
            fw_const.OS_IN_NETWORK_CREATE_FAIL:
                fw_const.OS_IN_NWK_STATE,
            fw_const.OS_IN_NETWORK_CREATE_SUCCESS:
                fw_const.OS_OUT_NETWORK_STATE,
            fw_const.OS_OUT_NETWORK_CREATE_FAIL:
                fw_const.OS_OUT_NETWORK_STATE,
            fw_const.OS_OUT_NETWORK_CREATE_SUCCESS:
                fw_const.OS_DUMMY_RTR_STATE,
            fw_const.OS_DUMMY_RTR_CREATE_FAIL:
                fw_const.OS_DUMMY_RTR_STATE,
            fw_const.OS_DUMMY_RTR_CREATE_SUCCESS:
                fw_const.DCNM_IN_NETWORK_STATE,
            fw_const.DCNM_IN_NETWORK_CREATE_FAIL:
                fw_const.DCNM_IN_NETWORK_STATE,
            fw_const.DCNM_IN_NETWORK_CREATE_SUCCESS:
                fw_const.DCNM_IN_PART_UPDATE_STATE,
            fw_const.DCNM_IN_PART_UPDATE_FAIL:
                fw_const.DCNM_IN_PART_UPDATE_STATE,
            fw_const.DCNM_IN_PART_UPDATE_SUCCESS:
                fw_const.DCNM_OUT_PART_STATE,
            fw_const.DCNM_OUT_PART_CREATE_FAIL:
                fw_const.DCNM_OUT_PART_STATE,
            fw_const.DCNM_OUT_PART_CREATE_SUCCESS:
                fw_const.DCNM_OUT_NETWORK_STATE,
            fw_const.DCNM_OUT_NETWORK_CREATE_FAIL:
                fw_const.DCNM_OUT_NETWORK_STATE,
            fw_const.DCNM_OUT_NETWORK_CREATE_SUCCESS:
                fw_const.DCNM_OUT_PART_UPDATE_STATE,
            fw_const.DCNM_OUT_PART_UPDATE_FAIL:
                fw_const.DCNM_OUT_PART_UPDATE_STATE,
            fw_const.DCNM_OUT_PART_UPDATE_SUCCESS:
                fw_const.FABRIC_PREPARE_DONE_STATE}
        self.fabric_fsm = {
            fw_const.OS_IN_NWK_STATE:
                [self.create_os_in_nwk, self.delete_os_in_nwk],
            fw_const.OS_OUT_NETWORK_STATE:
                [self.create_os_out_nwk, self.delete_os_out_nwk],
            fw_const.OS_DUMMY_RTR_STATE:
                [self.create_os_dummy_rtr, self.delete_os_dummy_rtr],
            fw_const.DCNM_IN_NETWORK_STATE:
                [self.create_dcnm_in_nwk, self.delete_dcnm_in_nwk],
            fw_const.DCNM_IN_PART_UPDATE_STATE:
                [self.update_dcnm_in_part, self.clear_dcnm_in_part],
            fw_const.DCNM_OUT_PART_STATE:
                [self.create_dcnm_out_part, self.delete_dcnm_out_part],
            fw_const.DCNM_OUT_NETWORK_STATE:
                [self.create_dcnm_out_nwk, self.delete_dcnm_out_nwk],
            fw_const.DCNM_OUT_PART_UPDATE_STATE:
                [self.update_dcnm_out_part, self.clear_dcnm_out_part],
            fw_const.FABRIC_PREPARE_DONE_STATE:
                [self.prepare_fabric_done, self.prepare_fabric_done]}
        self.mutex_lock = sys_utils.lock()
        self.correct_db_restart()
        self.populate_local_cache()

    def store_dcnm(self, dcnm_obj):
        self.dcnm_obj = dcnm_obj

    def get_service_obj(self, tenant_id):
        return self.service_attr[tenant_id]

    def create_serv_obj(self, tenant_id):
        self.service_attr[tenant_id] = ServiceIpSegTenantMap()
        self.store_obj(tenant_id, self.service_attr[tenant_id])

    def store_net_db(self, tenant_id, net, subnet, net_dict, subnet_dict,
                     direc, result):
        '''Store service network in DB'''

        network_dict = dict()
        network_dict['name'] = net_dict.get('name')
        network_dict['config_profile'] = net_dict.get('config_profile')
        network_dict['segmentation_id'] = net_dict.get('segmentation_id')
        network_dict['tenant_id'] = tenant_id
        network_dict['fwd_mode'] = net_dict.get('fwd_mode')
        network_dict['vlan'] = net_dict.get('vlan_id')
        network_dict['mob_domain_name'] = net_dict.get('mob_domain_name')
        self.add_network_db(net, network_dict, fw_const.FW_CONST, result)

    def store_fw_db(self, tenant_id, net, net_dict, subnet_dict, direc):
        serv_obj = self.get_service_obj(tenant_id)
        sub = subnet_dict.get('allocation_pools')[0].get('start')
        serv_obj.update_fw_local_cache(net, direc, sub)
        serv_obj.commit_fw_db()

    def update_fw_db_result(self, tenant_id, os_status=None, dcnm_status=None,
                            dev_status=None):
        serv_obj = self.get_service_obj(tenant_id)
        serv_obj.update_fw_local_result(os_status, dcnm_status, dev_status)
        serv_obj.commit_fw_db_result()

    def store_fw_db_router(self, tenant_id, net_id, subnet_id, router_id,
                           os_status):
        serv_obj = self.get_service_obj(tenant_id)
        serv_obj.update_fw_local_router(net_id, subnet_id, router_id,
                                        os_status)
        serv_obj.commit_fw_db()
        serv_obj.commit_fw_db_result()

    def store_net_fw_db(self, tenant_id, net, subnet, net_dict, subnet_dict,
                        direc, result, os_status=None, dcnm_status=None,
                        dev_status=None):
        self.store_net_db(tenant_id, net, subnet, net_dict, subnet_dict, direc,
                          result)
        self.store_fw_db(tenant_id, net, net_dict, subnet_dict, direc)
        self.update_fw_db_result(tenant_id, os_status, dcnm_status, dev_status)

    def get_gateway(self, subnet):
        return str(netaddr.IPAddress(subnet) + 1)

    def get_start_ip(self, subnet):
        return str(netaddr.IPAddress(subnet) + 2)

    def get_end_ip(self, subnet):
        return str(netaddr.IPAddress(subnet) + (1 << (32 - self.mask)) - 2)

    def check_allocate_ip(self, obj, direc):
        flag = True
        while flag:
            ip_next = obj.allocate_subnet()
            if ip_next is None:
                return None
            # Check if a subnet is already allocated in Openstack with this
            # address
            sub_next = str(ip_next) + '/' + str(self.mask)
            ret = self.os_helper.is_subnet_present(sub_next)
            if ret:
                self.release_subnet(ip_next, direc)
            else:
                flag = False
        return ip_next

    def get_next_ip(self, tenant_id, direc):
        '''This needs to be put in a common functionality for services'''

        if direc == 'in':
            temp_store_ip, start, end, gateway = self.get_in_ip_addr(tenant_id)
        else:
            temp_store_ip, start, end, gateway = self.\
                get_out_ip_addr(tenant_id)
        if temp_store_ip != 0 and start != 0 and end != 0 and gateway != 0:
            return temp_store_ip, start, end, gateway
        if direc == 'in':
            # ip_next = self.service_in_ip.allocate_subnet()
            ip_next = self.check_allocate_ip(self.service_in_ip, "in")
        else:
            # ip_next = self.service_out_ip.allocate_subnet()
            ip_next = self.check_allocate_ip(self.service_out_ip, "out")
        return ip_next, self.get_start_ip(ip_next), self.get_end_ip(ip_next),\
            self.get_gateway(ip_next)

    def release_subnet(self, cidr, direc):
        if direc == 'in':
            ip_next = self.service_in_ip.release_subnet(cidr)
        else:
            ip_next = self.service_out_ip.release_subnet(cidr)

    def fill_dcnm_subnet_info(self, tenant_id, subnet, start, end, gateway,
                              direc):
        serv_obj = self.get_service_obj(tenant_id)
        fw_dict = serv_obj.get_fw_dict()
        fw_id = fw_dict.get('fw_id')
        subnet_dict = {}
        subnet_dict['enable_dhcp'] = False
        subnet_dict['tenant_id'] = tenant_id
        subnet_dict['allocation_pools'] = {}
        if direc == 'in':
            name = fw_id[0:4] + fw_const.IN_SERVICE_SUBNET + (
                fw_id[len(fw_id) - 4:])
            subnet_dict['name'] = name
            subnet_dict['name'] = fw_const.IN_SERVICE_SUBNET
            # self.service_attr[tenant_id].store_in_ip_addr(subnet, start, end,
            #                                              gateway)
        else:
            name = fw_id[0:4] + fw_const.OUT_SERVICE_SUBNET + (
                fw_id[len(fw_id) - 4:])
            subnet_dict['name'] = name
            # self.service_attr[tenant_id].store_out_ip_addr(subnet, start,
            #                                                end, gateway)
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

    def retrieve_dcnm_subnet_info(self, tenant_id, direc):
        serv_obj = self.get_service_obj(tenant_id)
        subnet_dict = serv_obj.get_dcnm_subnet_dict(direc)
        return subnet_dict

    def alloc_retrieve_subnet_info(self, tenant_id, direc):
        serv_obj = self.get_service_obj(tenant_id)
        subnet_dict = self.retrieve_dcnm_subnet_info(tenant_id, direc)
        if len(subnet_dict) != 0:
            return subnet_dict
        subnet, start, end, gateway = self.get_next_ip(tenant_id, direc)
        subnet_dict = self.fill_dcnm_subnet_info(tenant_id, subnet, start,
                                                 end, gateway, direc)
        serv_obj.store_dcnm_subnet_dict(subnet_dict, direc)
        return subnet_dict

    def retrieve_dcnm_net_info(self, tenant_id, direc):
        serv_obj = self.get_service_obj(tenant_id)
        net_dict = serv_obj.get_dcnm_net_dict(direc)
        return net_dict

    def update_dcnm_net_info(self, tenant_id, direc, vlan_id,
                             segmentation_id):
        net_dict = self.retrieve_dcnm_net_info(tenant_id, direc)
        if len(net_dict) == 0:
            return None
        net_dict['vlan_id'] = vlan_id
        if vlan_id != 0:
            net_dict['mob_domain'] = True
            net_dict['mob_domain_name'] = self.mob_domain_name
        net_dict['segmentation_id'] = segmentation_id
        return net_dict

    def fill_dcnm_net_info(self, tenant_id, direc, vlan_id=0,
                           segmentation_id=0):
        serv_obj = self.get_service_obj(tenant_id)
        fw_dict = serv_obj.get_fw_dict()
        fw_id = fw_dict.get('fw_id')
        net_dict = {}
        net_dict['status'] = 'ACTIVE'
        net_dict['admin_state_up'] = True
        net_dict['tenant_id'] = tenant_id
        net_dict['provider:network_type'] = 'local'
        net_dict['vlan_id'] = vlan_id
        net_dict['segmentation_id'] = segmentation_id
        if vlan_id == 0:
            net_dict['mob_domain'] = False
            net_dict['mob_domain_name'] = None
        else:
            net_dict['mob_domain'] = True
            net_dict['mob_domain_name'] = self.mob_domain_name
        # NWK ID are not filled TODO
        if direc == 'in':
            name = fw_id[0:4] + fw_const.IN_SERVICE_NWK + (
                fw_id[len(fw_id) - 4:])
            net_dict['name'] = name
            net_dict['part_name'] = None
            net_dict['config_profile'] = self.serv_host_prof
            net_dict['fwd_mode'] = self.serv_host_mode
        else:
            name = fw_id[0:4] + fw_const.OUT_SERVICE_NWK + (
                fw_id[len(fw_id) - 4:])
            net_dict['name'] = name
            net_dict['config_profile'] = self.serv_ext_prof
            net_dict['part_name'] = fw_const.SERV_PART_NAME
            net_dict['fwd_mode'] = self.serv_ext_mode
        return net_dict

    def fill_retrieve_net_info(self, tenant_id, direc):
        serv_obj = self.get_service_obj(tenant_id)
        net_dict = self.retrieve_dcnm_net_info(tenant_id, direc)
        if len(net_dict) != 0:
            return net_dict
        net_dict = self.fill_dcnm_net_info(tenant_id, direc)
        serv_obj.store_dcnm_net_dict(net_dict, direc)
        return net_dict

    def alloc_seg(self, net_id):
        # Does allocation happen here or at ServiceIpSegTenantMap class? TODO
        segmentation_id = self.service_segs.allocate_segmentation_id(net_id)
        return segmentation_id

    def alloc_vlan(self, net_id):
        # Does allocation happen here or at ServiceIpSegTenantMap class? TODO
        vlan_id = self.service_vlans.allocate_segmentation_id(net_id)
        return vlan_id

    def update_subnet_db_info(self, tenant_id, direc, net_id, subnet_id):
        serv_obj = self.get_service_obj(tenant_id)
        subnet_dict = self.retrieve_dcnm_subnet_info(tenant_id, direc)
        if len(subnet_dict) == 0:
            LOG.error("Subnet dict not found")
            return
        subnet = subnet_dict['cidr'].split('/')[0]
        if direc == 'in':
            self.service_in_ip.update_subnet(subnet, net_id, subnet_id)
        else:
            self.service_out_ip.update_subnet(subnet, net_id, subnet_id)

    def update_net_info(self, tenant_id, direc, vlan_id, segmentation_id):
        serv_obj = self.get_service_obj(tenant_id)
        net_dict = self.update_dcnm_net_info(tenant_id, direc, vlan_id,
                                             segmentation_id)
        serv_obj.store_dcnm_net_dict(net_dict, direc)
        return net_dict

    def _create_service_nwk(self, tenant_id, tenant_name, direc):
        ''' Function to create the service in network in DCNM'''

        net_dict = self.retrieve_dcnm_net_info(tenant_id, direc)
        net = utils.dict_to_obj(net_dict)
        subnet_dict = self.retrieve_dcnm_subnet_info(tenant_id, direc)
        subnet = utils.dict_to_obj(subnet_dict)
        try:
            self.dcnm_obj.create_service_network(tenant_name, net, subnet)
        except dexc.DfaClientRequestFailed:
            # Dump the whole message
            LOG.exception("Failed to create network in DCNM %s" % direc)
            return False
        return True

    def _delete_service_nwk(self, tenant_id, tenant_name, direc):
        ''' Function to delete the service in network in DCNM'''

        net_dict = {}
        if direc == 'in':
            seg, vlan, mob_dom = self.get_in_seg_vlan_mob_dom(tenant_id)
            net_dict['part_name'] = None
        else:
            seg, vlan, mob_dom = self.get_out_seg_vlan_mob_dom(tenant_id)
            net_dict['part_name'] = fw_const.SERV_PART_NAME
        net_dict['segmentation_id'] = seg
        net_dict['vlan_id'] = vlan
        net_dict['mob_domain_name'] = mob_dom
        net = utils.dict_to_obj(net_dict)
        ret = True
        try:
            self.dcnm_obj.delete_service_network(tenant_name, net)
        except dexc.DfaClientRequestFailed:
            # Dump the whole message
            LOG.exception("Failed to delete network in DCNM %s" % direc)
            ret = False
        return ret
        # Already released
        # self.service_vlans.release_segmentation_id(vlan)
        # self.service_segs.release_segmentation_id(seg)

    def get_dummy_router_net(self, tenant_id):
        if tenant_id not in self.service_attr:
            LOG.error("Fabric not prepared for tenant %s, %s" %
                      (tenant_id, tenant_name))
            return None, None, None
        tenant_obj = self.get_service_obj(tenant_id)
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

    def _create_os_nwk(self, tenant_id, tenant_name, direc, is_fw_virt=False):
        '''Function to create Openstack network'''

        # Step 1.a below (This fills IP DB after allocation)
        subnet_dict = self.alloc_retrieve_subnet_info(tenant_id, direc)
        # 1. Fill net parameters w/o vlan, seg allocated (becos we don't have
        # netid to store in DB)
        # 2. Create OS Network which gives NetID
        # 3. Allocate vlan, seg thereby storing NetID atomically
        # This saves an extra step to update DB with NetID after allocation.
        # Also may save an extra step after restart, if process crashed
        # after allocation but before updating DB with NetID. Now, since
        # both steps are combined, Vlan/Seg won't be allocated w/o NetID.
        # But for subnet, IP address has to be passed for Openstack
        # network create for which it has to be allocated. So, it's a
        # two step operation.
        # 1.a Allocate IP with source not filled.
        # 2.a After creating OS network, update IP DB with netid as source,
        # So, after restart deallocate any IP DB entries that does not have
        # a source.
        # Step 1
        net_dict = self.fill_retrieve_net_info(tenant_id, direc)
        # Step 2
        try:
            net_id, subnet_id = self.os_helper.create_network(
                net_dict['name'], tenant_id, subnet_dict['cidr'])
        except Exception as e:
            self.release_subnet(subnet_dict['cidr'], direc)
            LOG.error("Create network for name %s direct %s" %
                      (net_dict['name'], direc))
            return None, None
        # Step 3 (This fills the Seg/Vlan DB with net_id)
        seg = self.alloc_seg(net_id)
        vlan = 0
        # VLAN allocation is only needed for physical case
        if not is_fw_virt:
            vlan = self.alloc_vlan(net_id)
        # Updating the local cache
        self.update_net_info(tenant_id, direc, vlan, seg)
        # Step 2.a (Updating IP DB with netid)
        self.update_subnet_db_info(tenant_id, direc, net_id, subnet_id)
        return net_id, subnet_id

    def _create_dummy_router_and_intf(self, tenant_id, tenant_name):
        '''Function to create a dummy router and interface'''

        serv_obj = self.get_service_obj(tenant_id)
        fw_dict = serv_obj.get_fw_dict()
        fw_id = fw_dict.get('fw_id')
        rtr_nwk = fw_id[0:4] + fw_const.DUMMY_SERVICE_NWK + (
            fw_id[len(fw_id) - 4:])
        rtr_name = fw_id[0:4] + fw_const.DUMMY_SERVICE_RTR + (
            fw_id[len(fw_id) - 4:])
        net_id, subnet_id = self.os_helper.create_network(
            rtr_nwk, tenant_id, self.servicedummy_ip_subnet)
        if net_id is None or subnet_id is None:
            return None, None, None
        net_dict = {}
        subnet_dict = {}
        net_dict['name'] = rtr_nwk
        self.store_net_db(tenant_id, net_id, subnet_id, net_dict, subnet_dict,
                          None, 'SUCCESS')
        rout_id = self.os_helper.create_router(rtr_name, tenant_id, subnet_id)
        if rout_id is None:
            self.os_helper.delete_network(rtr_nwk, tenant_id, subnet_id,
                                          net_id, self.servicedummy_ip_subnet)
            return net_id, subnet_id, None
        return net_id, subnet_id, rout_id

    def _delete_dummy_router_and_intf(self, tenant_id, tenant_name):
        '''Function to delete a dummy router and interface'''

        serv_obj = self.get_service_obj(tenant_id)
        fw_dict = serv_obj.get_fw_dict()
        fw_id = fw_dict.get('fw_id')
        net_id, subnet_id, rout_id = self.get_dummy_router_net(tenant_id)
        ret = self.delete_os_dummy_rtr_nwk(rout_id, net_id, subnet_id)
        # Release the network DB entry
        self.delete_network_db(net_id)
        return ret

    def create_os_in_nwk(self, tenant_id, tenant_name, is_fw_virt=False):
        try:
            net, subnet = self._create_os_nwk(tenant_id, tenant_name, "in",
                                              is_fw_virt=is_fw_virt)
            if net is None or subnet is None:
                return False
        except Exception as e:
            # If Openstack network creation fails, IP address is released.
            # Seg, VLAN creation happens only after network creation in
            # Openstack is successful.
            LOG.error("Creation of In Openstack Network failed tenant %s" %
                      tenant_id)
            return False
        ret = fw_const.OS_IN_NETWORK_CREATE_SUCCESS
        net_dict = self.retrieve_dcnm_net_info(tenant_id, "in")
        subnet_dict = self.retrieve_dcnm_subnet_info(tenant_id, "in")
        # Very unlikely case, so nothing released.
        if len(net_dict) == 0 or len(subnet_dict) == 0:
            LOG.error("Allocation of net,subnet failed %s %s" %
                      len(net_dict), len(subnet_dict))
            ret = fw_const.OS_IN_NETWORK_CREATE_FAIL
        # Updating the FW and Nwk DB
        self.store_net_fw_db(tenant_id, net, subnet, net_dict, subnet_dict,
                             "in", 'SUCCESS', os_status=ret)
        return True

    def create_os_out_nwk(self, tenant_id, tenant_name, is_fw_virt=False):
        try:
            net, subnet = self._create_os_nwk(tenant_id, tenant_name, "out")
            if net is None or subnet is None:
                return False
        except Exception as e:
            # If Openstack network creation fails, IP address is released.
            # Seg, VLAN creation happens only after network creation in
            # Openstack is successful.
            LOG.error("Creation of Out Openstack Network failed tenant %s" %
                      tenant_id)
            return False
        ret = fw_const.OS_OUT_NETWORK_CREATE_SUCCESS
        net_dict = self.retrieve_dcnm_net_info(tenant_id, "out")
        subnet_dict = self.retrieve_dcnm_subnet_info(tenant_id, "out")
        # Very unlikely case, so nothing released.
        if len(net_dict) == 0 or len(subnet_dict) == 0:
            LOG.error("Allocation of net,subnet failed %s %s" %
                      len(net_dict), len(subnet_dict))
            ret = fw_const.OS_OUT_NETWORK_CREATE_FAIL
        # Updating the FW and Nwk DB
        self.store_net_fw_db(tenant_id, net, subnet, net_dict, subnet_dict,
                             "out", 'SUCCESS', os_status=ret)
        return True

    def _delete_os_nwk(self, tenant_id, tenant_name, direc, is_fw_virt=False):
        '''Function to delete Openstack network'''

        serv_obj = self.get_service_obj(tenant_id)
        fw_dict = serv_obj.get_fw_dict()
        fw_id = fw_dict.get('fw_id')
        fw_data = self.get_fw(fw_id)
        if fw_data is None:
            LOG.error("Unable to get fw_data")
            return False
        if direc == 'in':
            net_id = fw_data.in_network_id
            seg, vlan, mob_dom = self.get_in_seg_vlan_mob_dom(tenant_id)
            sub, ip, ip_end, gw = self.get_in_ip_addr(tenant_id)
        else:
            net_id = fw_data.out_network_id
            seg, vlan, mob_dom = self.get_out_seg_vlan_mob_dom(tenant_id)
            sub, ip, ip_end, gw = self.get_out_ip_addr(tenant_id)
        # Delete the Openstack Network
        try:
            self.os_helper.delete_network_all_subnets(net_id)
        except Exception as e:
            LOG.error("Delete network for ID %s direct %s failed" %
                      (net_id, direc))
            return False

        # Release the segment, VLAN and subnet allocated
        if not is_fw_virt:
            self.service_vlans.release_segmentation_id(vlan)
        self.service_segs.release_segmentation_id(seg)
        self.release_subnet(sub, direc)
        # Release the network DB entry
        self.delete_network_db(net_id)
        return True

    def delete_os_in_nwk(self, tenant_id, tenant_name, is_fw_virt=False):
        ret = True
        try:
            ret = self._delete_os_nwk(tenant_id, tenant_name, "in")
        except Exception as e:
            LOG.error("Deletion of In Openstack Network failed tenant %s" %
                      tenant_id)
            ret = False
        # Updating the FW DB
        if ret:
            res = fw_const.OS_IN_NETWORK_DEL_SUCCESS
        else:
            res = fw_const.OS_IN_NETWORK_DEL_FAIL
        self.update_fw_db_result(tenant_id, os_status=res)
        return ret

    def delete_os_out_nwk(self, tenant_id, tenant_name, is_fw_virt=False):
        ret = True
        try:
            ret = self._delete_os_nwk(tenant_id, tenant_name, "out")
        except Exception as e:
            LOG.error("Deletion of Out Openstack Network failed tenant %s" %
                      tenant_id)
            ret = False
        # Updating the FW DB
        if ret:
            res = fw_const.OS_OUT_NETWORK_DEL_SUCCESS
        else:
            res = fw_const.OS_OUT_NETWORK_DEL_FAIL
        self.update_fw_db_result(tenant_id, os_status=res)
        return ret

    def create_os_dummy_rtr(self, tenant_id, tenant_name, is_fw_virt=False):
        res = fw_const.OS_DUMMY_RTR_CREATE_SUCCESS
        try:
            net_id, subnet_id, rout_id = self._create_dummy_router_and_intf(
                tenant_id, tenant_name)
            if net_id is None or subnet_id is None or rout_id is None:
                return False
        except Exception as e:
            # Function _create_dummy_router_and_intf already took care of
            # cleanup for error cases.
            LOG.error("Creation of Openstack Router failed tenant %s" %
                      tenant_id)
            res = fw_const.OS_DUMMY_RTR_CREATE_FAIL
        self.store_fw_db_router(tenant_id, net_id, subnet_id, rout_id, res)
        return True

    def delete_os_dummy_rtr(self, tenant_id, tenant_name, is_fw_virt=False):
        ret = True
        try:
            ret = self._delete_dummy_router_and_intf(tenant_id, tenant_name)
        except Exception as e:
            # Function _create_dummy_router_and_intf already took care of
            # cleanup for error cases.
            LOG.error("Deletion of Openstack Router failed tenant %s" %
                      tenant_id)
            ret = False
        if ret:
            res = fw_const.OS_DUMMY_RTR_DEL_SUCCESS
        else:
            res = fw_const.OS_DUMMY_RTR_DEL_FAIL
        self.update_fw_db_result(tenant_id, os_status=res)
        return ret

    def create_dcnm_in_nwk(self, tenant_id, tenant_name, is_fw_virt=False):
        ret = self._create_service_nwk(tenant_id, tenant_name, 'in')
        if ret:
            res = fw_const.DCNM_IN_NETWORK_CREATE_SUCCESS
            LOG.info("In Service network created for tenant %s" % tenant_id)
        else:
            res = fw_const.DCNM_IN_NETWORK_CREATE_FAIL
            LOG.info("In Service network create failed for tenant %s" %
                     tenant_id)
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        return ret

    def delete_dcnm_in_nwk(self, tenant_id, tenant_name, is_fw_virt=False):
        ret = self._delete_service_nwk(tenant_id, tenant_name, 'in')
        if ret:
            res = fw_const.DCNM_IN_NETWORK_DEL_SUCCESS
            LOG.info("In Service network deleted for tenant %s" % tenant_id)
        else:
            res = fw_const.DCNM_IN_NETWORK_DEL_FAIL
            LOG.info("In Service network deleted failed for tenant %s" %
                     tenant_id)
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        return ret

    def update_dcnm_in_part(self, tenant_id, tenant_name, is_fw_virt=False):
        res = fw_const.DCNM_IN_PART_UPDATE_SUCCESS
        ret = True
        try:
            self._update_partition_in_create(tenant_id, tenant_name)
        except Exception as e:
            LOG.error("Update of In Partition failed for tenant %s" %
                      tenant_id)
            res = fw_const.DCNM_IN_PART_UPDATE_FAIL
            ret = False
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        LOG.info("In partition updated with service ip addr")
        return ret

    def clear_dcnm_in_part(self, tenant_id, tenant_name, is_fw_virt=False):
        res = fw_const.DCNM_IN_PART_UPDDEL_SUCCESS
        ret = True
        try:
            self._update_partition_in_delete(tenant_id, tenant_name)
        except Exception as e:
            LOG.error("Clear of In Partition failed for tenant %s" %
                      tenant_id)
            res = fw_const.DCNM_IN_PART_UPDDEL_FAIL
            ret = False
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        LOG.info("In partition cleared off service ip addr")
        return ret

    def create_dcnm_out_part(self, tenant_id, tenant_name, is_fw_virt=False):
        res = fw_const.DCNM_OUT_PART_CREATE_SUCCESS
        ret = True
        try:
            self._create_out_partition(tenant_id, tenant_name)
        except Exception as e:
            LOG.error("Create of Out Partition failed for tenant %s" %
                      tenant_id)
            res = fw_const.DCNM_OUT_PART_CREATE_FAIL
            ret = False
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        LOG.info("Out partition created")
        return ret

    def delete_dcnm_out_part(self, tenant_id, tenant_name, is_fw_virt=False):
        res = fw_const.DCNM_OUT_PART_DEL_SUCCESS
        ret = True
        try:
            self._delete_partition(tenant_id, tenant_name)
        except Exception as e:
            LOG.error("deletion of Out Partition failed for tenant %s" %
                      tenant_id)
            res = fw_const.DCNM_OUT_PART_DEL_FAIL
            ret = False
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        LOG.info("Out partition deleted")
        return ret

    def create_dcnm_out_nwk(self, tenant_id, tenant_name, is_fw_virt=False):
        ret = self._create_service_nwk(tenant_id, tenant_name, 'out')
        if ret:
            res = fw_const.DCNM_OUT_NETWORK_CREATE_SUCCESS
            LOG.info("out Service network created for tenant %s" % tenant_id)
        else:
            res = fw_const.DCNM_OUT_NETWORK_CREATE_FAIL
            LOG.info("out Service network create failed for tenant %s" %
                     tenant_id)
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        return ret

    def delete_dcnm_out_nwk(self, tenant_id, tenant_name, is_fw_virt=False):
        ret = self._delete_service_nwk(tenant_id, tenant_name, 'out')
        if ret:
            res = fw_const.DCNM_OUT_NETWORK_DEL_SUCCESS
            LOG.info("out Service network deleted for tenant %s" % tenant_id)
        else:
            res = fw_const.DCNM_OUT_NETWORK_DEL_FAIL
            LOG.info("out Service network deleted failed for tenant %s" %
                     tenant_id)
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        return ret

    def update_dcnm_out_part(self, tenant_id, tenant_name, is_fw_virt=False):
        res = fw_const.DCNM_OUT_PART_UPDATE_SUCCESS
        ret = True
        try:
            self._update_partition_out_create(tenant_id, tenant_name)
        except Exception as e:
            LOG.error("Update of Out Partition failed for tenant %s" %
                      tenant_id)
            res = fw_const.DCNM_OUT_PART_UPDATE_FAIL
            ret = False
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        LOG.info("Out partition updated with service ip addr")
        return ret

    # Didn't work, Recheck TODO
    def clear_dcnm_out_part(self, tenant_id, tenant_name, is_fw_virt=False):
        res = fw_const.DCNM_OUT_PART_UPDDEL_SUCCESS
        self.update_fw_db_result(tenant_id, dcnm_status=res)
        LOG.info("Out partition cleared -noop- with service ip addr")
        return True

    def prepare_fabric_done(self, tenant_id, tenant_name, is_fw_virt=False):
        return True

    def get_next_create_state(self, state, ret):
        if ret:
            if state == fw_const.FABRIC_PREPARE_DONE_STATE:
                return state
            else:
                return state + 1
        else:
            return state

    def get_next_del_state(self, state, ret):
        if ret:
            if state == fw_const.INIT_STATE:
                return state
            else:
                return state - 1
        else:
            return state

    def get_next_state(self, state, ret, op):
        if op == 'CREATE':
            return self.get_next_create_state(state, ret)
        else:
            return self.get_next_del_state(state, ret)

    def run_create_sm(self, fw_id, fw_name, tenant_id, tenant_name,
                      is_fw_virt):
        # Read the current state from the DB
        ret = True
        serv_obj = self.get_service_obj(tenant_id)
        serv_obj.get_store_local_final_result()
        state = serv_obj.get_state()
        while ret:
            try:
                ret = self.fabric_fsm[state][0](tenant_id, tenant_name,
                                                is_fw_virt=is_fw_virt)
            except Exception as e:
                LOG.error("Exception %s for state %s" % (e,
                          fw_const.fw_state_fn_dict.get(state)))
                ret = False
            state = self.get_next_state(state, ret, 'CREATE')
            serv_obj.store_state(state)
            if state == fw_const.FABRIC_PREPARE_DONE_STATE:
                break
        return ret

    def run_delete_sm(self, fw_id, fw_name, tenant_id, tenant_name,
                      is_fw_virt):
        # Read the current state from the DB
        ret = True
        serv_obj = self.get_service_obj(tenant_id)
        serv_obj.get_store_local_final_result()
        state = serv_obj.get_state()
        while ret:
            try:
                ret = self.fabric_fsm[state][1](tenant_id, tenant_name,
                                                is_fw_virt=is_fw_virt)
            except Exception as e:
                LOG.error("Exception %s for state %s" % (e,
                          fw_const.fw_state_fn_del_dict.get(state)))
                ret = False
            if state == fw_const.INIT_STATE:
                break
            state = self.get_next_state(state, ret, 'DELETE')
            serv_obj.store_state(state)
        return ret

    def get_key_state(self, status):
        for key, val in fw_const.fw_state_dict.items():
            if val == status:
                return key

    def pop_create_fw_state(self, os_status, dcnm_status):
        os_status_num = self.get_key_state(os_status)
        dcnm_status_num = self.get_key_state(dcnm_status)
        if os_status_num < fw_const.OS_CREATE_SUCCESS:
            state = os_status_num
        else:
            state = dcnm_status_num
        state_fn = self.fabric_state_map.get(state)
        return state_fn

    def pop_del_fw_state(self, os_status, dcnm_status):
        os_status_num = self.get_key_state(os_status)
        dcnm_status_num = self.get_key_state(dcnm_status)
        if os_status_num > fw_const.DCNM_DEL_SUCCESS:
            state = dcnm_status_num
        else:
            state = os_status_num
        state_fn = self.fabric_state_map.get(state)
        return state_fn

    def pop_fw_local(self, tenant_id, net_id, direc, node_ip):
        net = self.get_network(net_id)
        serv_obj = self.get_service_obj(tenant_id)
        serv_obj.update_fw_local_cache(net_id, direc, node_ip)
        net_dict = self.fill_dcnm_net_info(tenant_id, direc, net.vlan,
                                           net.segmentation_id)
        serv_obj.store_dcnm_net_dict(net_dict, direc)
        if direc == "in":
            subnet = self.service_in_ip.get_subnet_by_netid(net_id)
        else:
            subnet = self.service_out_ip.get_subnet_by_netid(net_id)
        subnet_dict = self.fill_dcnm_subnet_info(tenant_id, subnet,
                                                 self.get_start_ip(subnet),
                                                 self.get_end_ip(subnet),
                                                 self.get_gateway(subnet),
                                                 direc)
        serv_obj.store_dcnm_subnet_dict(subnet_dict, direc)

    # Tested for 1 FW
    def populate_local_cache_tenant(self, fw_id, fw_data):
        tenant_id = fw_data.get('tenant_id')
        self.create_serv_obj(tenant_id)
        serv_obj = self.get_service_obj(tenant_id)
        serv_obj.create_fw_db(fw_id, fw_data.get('name'), tenant_id)
        self.pop_fw_local(tenant_id, fw_data.get('in_network_id'), "in",
                          fw_data.get('in_service_node_ip'))
        self.pop_fw_local(tenant_id, fw_data.get('out_network_id'), "out",
                          fw_data.get('out_service_node_ip'))
        serv_obj.update_fw_local_result_str(fw_data.get('os_status'),
                                            fw_data.get('dcnm_status'),
                                            fw_data.get('device_status'))
        serv_obj.store_local_final_result(fw_data.get('result'))
        if fw_data.get('result') == fw_const.RESULT_FW_CREATE_INIT or \
                fw_data.get('result') == fw_const.RESULT_FW_CREATE_DONE:
            state = self.pop_create_fw_state(fw_data.get('os_status'),
                                             fw_data.get('dcnm_status'))
        else:
            state = self.pop_del_fw_state(fw_data.get('os_status'),
                                          fw_data.get('dcnm_status'))
        serv_obj.store_state(state)
        if state == fw_const.FABRIC_PREPARE_DONE_STATE:
            serv_obj.set_fabric_create(True)
        router_id = fw_data.get('router_id')
        rout_net_id = fw_data.get('router_net_id')
        rout_subnet_id = fw_data.get('router_subnet_id')
        # Result is already populated above, so pass None below.
        # And, the result passed should be a string
        serv_obj.update_fw_local_router(rout_net_id, rout_subnet_id, router_id,
                                        None)

    def populate_local_cache(self):
        fw_dict = self.get_all_fw_db()
        for fw_id in fw_dict:
            fw_data = fw_dict[fw_id]
            self.populate_local_cache_tenant(fw_id, fw_data)

    def delete_os_dummy_rtr_nwk(self, rtr_id, net_id, subnet_id):
        ret = self.os_helper.delete_router(None, None, rtr_id, subnet_id)
        if not ret:
            return ret
        ret = self.os_helper.delete_network_all_subnets(net_id)
        return ret

    def delete_os_nwk_db(self, net_id, seg, vlan):
        self.service_segs.release_segmentation_id(seg)
        self.service_vlans.release_segmentation_id(vlan)
        self.os_helper.delete_network_all_subnets(net_id)
        # There's a chance that OS network got created but it's ID
        # was not put in DB
        # So, deleting networks in os that has part of the special
        # name
        self.os_helper.delete_network_subname(fw_const.IN_SERVICE_NWK)
        self.delete_network_db(net_id)
        self.clear_fw_entry_by_netid(net_id)
        self.service_in_ip.release_subnet_by_netid(net_id)
        self.service_out_ip.release_subnet_by_netid(net_id)

    # Tested for positive case, no delete happened
    def correct_db_restart(self):

        # Any Segments allocated that's not in Network or FW DB, release it
        seg_netid_dict = self.service_segs.get_all_seg_netid()
        vlan_netid_dict = self.service_vlans.get_all_seg_netid()
        for netid in seg_netid_dict:
            net = self.get_network(netid)
            fw_net = self.get_fw_by_netid(netid)
            if not net or not fw_net:
                self.delete_os_nwk_db(net_id, seg_netid_dict[netid],
                                      vlan_netid_dict[netid])
                return
        # Any VLANs allocated that's not in Network or FW DB, release it
        # For Virtual case, this list will be empty
        for netid in vlan_netid_dict:
            net = self.get_network(netid)
            fw_net = self.get_fw_by_netid(netid)
            if not net or not fw_net:
                self.delete_os_nwk_db(net_id, seg_netid_dict[netid],
                                      vlan_netid_dict[netid])
                return
        # Release all IP's from DB that has no NetID or SubnetID
        self.service_in_ip.release_subnet_no_netid()
        self.service_out_ip.release_subnet_no_netid()
        # It leaves out following possibilities not covered by above.
        # 1. Crash can happen just after creating FWID in DB (for init state)
        # 2. Crash can happen after 1 + IP address allocation
        # 3. Crash can happen after 2 + create OS network
        # IP address allocated will be freed as above.
        # Only OS network will remain for case 3.
        # Soln. TODO Check at beginning of state function if that network
        # exists and only then create it.
        # Also, create that FW DB entry only if that FWID didn't exist.

        # Delete all dummy networks created for dummy router from OS if it's
        # ID is not in NetDB
        # Delete all dummy routers and its associated networks/subnetfrom OS
        # if it's ID is not in FWDB
        fw_dict = self.get_all_fw_db()
        for fw_id in fw_dict:
            fw_data = fw_dict[fw_id]
            rtr_nwk = fw_id[0:4] + fw_const.DUMMY_SERVICE_NWK + (
                fw_id[len(fw_id) - 4:])
            net_list = self.os_helper.get_network_by_name(rtr_nwk)
            # Not sure of this TODO
            # There should be only one
            rtr_net = net_list[0]
            # Come back to finish this TODO
            # The router interface should be deleted first and then the network
            # Try using show_router
            for net in net_list:
                # Check for if it's there in NetDB
                net_db_item = self.get_network(net.get('id'))
                if not net_db_item:
                    self.os_helper.delete_network_all_subnets(net.get('id'))
                    return
            rtr_name = fw_id[0:4] + fw_const.DUMMY_SERVICE_RTR + (
                fw_id[len(fw_id) - 4:])
            rtr_list = self.os_helper.get_rtr_by_name(rtr_name)
            for rtr in rtr_list:
                fw_db_item = self.get_fw_by_rtrid(rtr.get('id'))
                if not fw_db_item:
                    ret = self.delete_os_dummy_rtr_nwk(rtr.get('id'),
                                                       rtr_net.get('id'))
                    return ret
        # TODO Read the Service NWK creation status in DCNM.
        # If it does not match with FW DB DCNM status, update it
        # Do the same for partition as well.
        # TODO go through the algo for delete SM as well.

    def prepare_fabric_fw_int(self, tenant_id, tenant_name, fw_id, fw_name,
                              is_fw_virt):
        if not self.auto_nwk_create:
            LOG.info("Auto network creation disabled")
            return False
        try:
            # More than 1 FW per tenant not supported TODO(padkrish)
            if tenant_id in self.service_attr and (
               self.service_attr[tenant_id].is_fabric_create()):
                LOG.error("Fabric already prepared for tenant %s, %s" %
                          (tenant_id, tenant_name))
                return False
            if tenant_id not in self.service_attr:
                self.create_serv_obj(tenant_id)
            self.service_attr[tenant_id].create_fw_db(fw_id, fw_name,
                                                      tenant_id)
            ret = self.run_create_sm(fw_id, fw_name, tenant_id, tenant_name,
                                     is_fw_virt)
            # Exceptions to be added for everything TODO
            # Also retry mechanism and state for which failed TODO
            # try:
            #    ret = self.create_os_services(tenant_id)
            # except Exception as e:
            #    LOG.error("Not able to create service resource in Openstack")
            # try:
            #    ret = self.create_dcnm_services(tenant_id, tenant_name)
            # except Exception as e:
            #    LOG.error("Not able to create service resource in DCNM")

            self.service_attr[tenant_id].set_fabric_create(True)
        except Exception as e:
            LOG.error("Exception raised in create fabric int %s" % e)
            return False
        return ret

    def prepare_fabric_fw(self, tenant_id, tenant_name, fw_id, fw_name,
                          is_fw_virt):
        try:
            with self.mutex_lock:
                ret = self.prepare_fabric_fw_int(tenant_id, tenant_name,
                                                 fw_id, fw_name, is_fw_virt)
        except Exception as e:
            LOG.error("Exception raised in create fabric %s" % e)
            return False
        return ret

    def delete_fabric_fw_int(self, tenant_id, tenant_name, fw_id, fw_name,
                             is_fw_virt):
        if not self.auto_nwk_create:
            LOG.info("Auto network creation disabled")
            return False
        try:
            if tenant_id not in self.service_attr:
                LOG.error("Fabric not prepared for tenant %s, %s" %
                          (tenant_id, tenant_name))
                return False
            if not self.service_attr[tenant_id].is_fabric_create():
                LOG.error("Fabric for tenant %s, %s already deleted" %
                          (tenant_id, tenant_name))
                return True
            ret = self.run_delete_sm(fw_id, fw_name, tenant_id, tenant_name,
                                     is_fw_virt)
            self.service_attr[tenant_id].set_fabric_create(False)
            self.service_attr[tenant_id].destroy_local_fw_db()
            del self.service_attr[tenant_id]
            # Equivalent of create_fw_db for delete TODO
        except Exception as e:
            LOG.error("Exception raised in delete fabric int %s" % e)
            return False
        return ret

    def delete_fabric_fw(self, tenant_id, tenant_name, fw_id, fw_name,
                         is_fw_virt):
        try:
            with self.mutex_lock:
                ret = self.delete_fabric_fw_int(tenant_id, tenant_name,
                                                fw_id, fw_name, is_fw_virt)
        except Exception as e:
            LOG.error("Exception raised in delete fabric %s" % e)
            return False
        return ret
