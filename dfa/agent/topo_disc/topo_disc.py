# Copyright 2014 Cisco Systems.
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

"""This file contains the implementation of Topology Discovery of servers and
their associated leaf switches using Open source implementation of LLDP.
www.open-lldp.org
"""

from dfa.agent.topo_disc import pub_lldp_api as pub_lldp
from dfa.agent.topo_disc import topo_disc_constants as constants
from dfa.common import utils
from dfa.common import dfa_logger as logging
from dfa.common import dfa_sys_lib as sys_utils

LOG = logging.getLogger(__name__)


class TopoIntfAttr(object):

    ''' Class that stores the interface attributes'''

    def __init__(self, intf):
        ''' Class Init '''
        self.init_params(intf)

    def init_params(self, intf):
        ''' Initializing parameters '''
        self.lldp_cfgd = False
        self.local_intf = intf
        self.remote_evb_cfgd = False
        self.remote_evb_mode = None
        self.remote_mgmt_addr = None
        self.remote_system_desc = None
        self.remote_system_name = None
        self.remote_port = None
        self.remote_chassis_id_mac = None
        self.remote_port_id_mac = None
        self.local_evb_cfgd = False
        self.local_evb_mode = None
        self.local_mgmt_address = None
        self.local_system_desc = None
        self.local_system_name = None
        self.local_port = None
        self.local_chassis_id_mac = None
        self.local_port_id_mac = None
        self.db_retry_status = False

    def update_lldp_status(self, status):
        ''' Update the LLDP cfg status '''
        self.lldp_cfgd = status

    def get_lldp_status(self):
        ''' Retrieve the LLDP cfg status '''
        return self.lldp_cfgd

    def get_db_retry_status(self):
        '''
        This retrieves the number of times RPC was retried with the server
        '''
        return self.db_retry_status

    def store_db_retry_status(self, status):
        '''
        This stores the number of times RPC was retried with the server
        '''
        self.db_retry_status = status

    def remote_evb_mode_uneq_store(self, remote_evb_mode):
        '''
        This stores the EVB mode retrieved, if it is not the same as stored.
        '''
        if remote_evb_mode != self.remote_evb_mode:
            self.remote_evb_mode = remote_evb_mode
            return True
        return False

    def remote_evb_cfgd_uneq_store(self, remote_evb_cfgd):
        '''
        This stores the EVB cfg retrieved, if it is not the same as stored.
        '''
        if remote_evb_cfgd != self.remote_evb_cfgd:
            self.remote_evb_cfgd = remote_evb_cfgd
            return True
        return False

    def remote_mgmt_addr_uneq_store(self, remote_mgmt_addr):
        '''
        This stores the Mgmt address retrieved, if it is not the same as
        stored.
        '''
        if remote_mgmt_addr != self.remote_mgmt_addr:
            self.remote_mgmt_addr = remote_mgmt_addr
            return True
        return False

    def remote_sys_desc_uneq_store(self, remote_system_desc):
        '''
        This stores the Sys Desc retrieved, if it is not the same as stored.
        '''
        if remote_system_desc != self.remote_system_desc:
            self.remote_system_desc = remote_system_desc
            return True
        return False

    def remote_sys_name_uneq_store(self, remote_system_name):
        '''
        This stores the Sys Name retrieved, if it is not the same as stored.
        '''
        if remote_system_name != self.remote_system_name:
            self.remote_system_name = remote_system_name
            return True
        return False

    def remote_port_uneq_store(self, remote_port):
        '''
        This stores the remote port retrieved, if it is not the same as stored.
        '''
        if remote_port != self.remote_port:
            self.remote_port = remote_port
            return True
        return False

    def remote_chassis_id_mac_uneq_store(self, remote_chassis_id_mac):
        '''
        This stores the Chassis ID retrieved, if it is not the same as stored.
        '''
        if remote_chassis_id_mac != self.remote_chassis_id_mac:
            self.remote_chassis_id_mac = remote_chassis_id_mac
            return True
        return False

    def remote_port_id_mac_uneq_store(self, remote_port_id_mac):
        '''
        This stores the Port ID MAC retrieved, if it is not the same as stored.
        '''
        if remote_port_id_mac != self.remote_port_id_mac:
            self.remote_port_id_mac = remote_port_id_mac
            return True
        return False


class TopoDiscPubApi(object):
    topo_intf_obj_dict = {}

    @classmethod
    def store_obj(cls, intf, obj):
        ''' Stores the topo object '''
        cls.topo_intf_obj_dict[intf] = obj

    @classmethod
    def get_lldp_status(cls, intf):
        ''' Retrieves the LLDP status '''
        if intf not in cls.topo_intf_obj_dict:
            LOG.error("Interface %s not configured at all", intf)
            return False
        intf_obj = cls.topo_intf_obj_dict.get(intf)
        return intf_obj.get_lldp_status()


class TopoDisc(TopoDiscPubApi):

    '''
    Topology Discovery Top level class, just needs to be instantiated
    once
    '''

    def __init__(self, cb, root_helper, intf_list=None, all_intf=True):
        '''
        Constructor
        cb => Callback in case any of the interface TLV changes.
        intf_list => List of interfaces to be LLDP enabled and monitored.
        all_intf => Boolean that signifies if all physical interfaces are to
        be monitored. intf_list will be None, if this variable is True.
        '''
        self.pub_lldp = pub_lldp.LldpApi(root_helper)
        if not all_intf:
            self.intf_list = intf_list
        else:
            self.intf_list = sys_utils.get_all_run_phy_intf()
        self.cb = cb
        self.intf_attr = {}
        self.cfg_lldp(self.intf_list)
        per_task = utils.PeriodicTask(constants.PERIODIC_TASK_INTERVAL,
                                      self.period_disc_task)
        per_task.run()

    def cfg_intf(self, intf):
        ''' Called by application to add an interface to the list '''
        self.intf_list.append(intf)
        self.cfg_lldp_intf(intf)

    def uncfg_intf(self, intf):
        ''' Called by application to remove an interface to the list '''
        pass
        # self.intf_list.remove(intf)
        # Can't remove interface since DB in server may appear stale
        # I can just remove the interface DB, but need to retry that till
        # it succeeds, so it has to be in periodic loop
        # So, currently leaving it as is, since LLDP frames won't be obtained
        # over the bridge, the periodic handler will automatically remove the
        # DB for this interface from server
        # Do i need to uncfg LLDP, object need not be removed

    def create_attr_obj(self, intf):
        ''' Creates the local interface attribute object and stores it '''
        self.intf_attr[intf] = TopoIntfAttr(intf)
        self.store_obj(intf, self.intf_attr[intf])

    def get_attr_obj(self, intf):
        ''' Retrieve the interface object '''
        return self.intf_attr[intf]

    def cmp_store_tlv_params(self, intf, tlv_data):
        ''' Compares the received TLV with stored TLV. Store the new TLV'''
        flag = False
        attr_obj = self.get_attr_obj(intf)
        remote_evb_mode = self.pub_lldp.get_remote_evb_mode(tlv_data)
        if attr_obj.remote_evb_mode_uneq_store(remote_evb_mode):
            flag = True
        remote_evb_cfgd = self.pub_lldp.get_remote_evb_cfgd(tlv_data)
        if attr_obj.remote_evb_cfgd_uneq_store(remote_evb_cfgd):
            flag = True
        remote_mgmt_addr = self.pub_lldp.get_remote_mgmt_addr(tlv_data)
        if attr_obj.remote_mgmt_addr_uneq_store(remote_mgmt_addr):
            flag = True
        remote_sys_desc = self.pub_lldp.get_remote_sys_desc(tlv_data)
        if attr_obj.remote_sys_desc_uneq_store(remote_sys_desc):
            flag = True
        remote_sys_name = self.pub_lldp.get_remote_sys_name(tlv_data)
        if attr_obj.remote_sys_name_uneq_store(remote_sys_name):
            flag = True
        remote_port = self.pub_lldp.get_remote_port(tlv_data)
        if attr_obj.remote_port_uneq_store(remote_port):
            flag = True
        remote_chassis_id_mac = self.pub_lldp.\
            get_remote_chassis_id_mac(tlv_data)
        if attr_obj.remote_chassis_id_mac_uneq_store(remote_chassis_id_mac):
            flag = True
        remote_port_id_mac = self.pub_lldp.get_remote_port_id_mac(tlv_data)
        if attr_obj.remote_port_id_mac_uneq_store(remote_port_id_mac):
            flag = True
        return flag

    def cfg_lldp_intf(self, intf):
        ''' Cfg LLDP on interface and create object '''
        self.create_attr_obj(intf)
        ret = self.pub_lldp.enable_lldp(intf)
        attr_obj = self.get_attr_obj(intf)
        attr_obj.update_lldp_status(ret)

    def cfg_lldp(self, intf_list):
        ''' This routine configures LLDP on all the interfaces '''
        for intf in intf_list:
            self.cfg_lldp_intf(intf)

    def period_disc_task(self):
        ''' Periodic task that checks the interface TLV attributes '''
        try:
            self._periodic_task_int()
        except Exception as exc:
            LOG.error("Exception caught in periodic task %s", str(exc))

    def _periodic_task_int(self):
        ''' Internal periodic task routine '''
        for intf in self.intf_list:
            attr_obj = self.get_attr_obj(intf)
            status = attr_obj.get_lldp_status()
            if not status:
                ret = self.pub_lldp.enable_lldp(intf)
                attr_obj.update_lldp_status(ret)
                continue
            tlv_data = self.pub_lldp.get_lldp_tlv(intf)
            # This should take care of storing the information of interest
            if self.cmp_store_tlv_params(intf, tlv_data) or (
               attr_obj.get_db_retry_status()):
                # Passing the interface attribute object to CB
                ret = self.cb(intf, attr_obj)
                status = not ret
                attr_obj.store_db_retry_status(status)
