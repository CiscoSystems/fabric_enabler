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

""" This file contains the public API's for interacting with LLDPAD """

from dfa.common import dfa_logger as logging
from dfa.common import dfa_sys_lib as utils

LOG = logging.getLogger(__name__)


class LldpApi(object):

    """ LLDP API Class """

    def __init__(self, root_helper):
        self.root_helper = root_helper

    def enable_lldp(self, port_name, is_ncb=True, is_nb=False):
        '''Function to enable LLDP on the interface.'''
        reply = None
        if is_ncb:
            reply = self.run_lldptool(["-L", "-i", port_name, "-g", "ncb",
                                       "adminStatus=rxtx"])
        else:
            if is_nb:
                reply = self.run_lldptool(["-L", "-i", port_name, "-g", "nb",
                                           "adminStatus=rxtx"])
            else:
                LOG.error("Both NCB and NB are not selected to enable LLDP")
                return False
        if reply is None:
            return False
        exp_str = "adminstatus=rxtx"
        if exp_str in reply.replace(" ", "").lower():
            return True
        else:
            return False

    def get_lldp_tlv(self, port_name, is_ncb=True, is_nb=False):
        '''Function to Query LLDP TLV on the interface.'''
        reply = None
        if is_ncb:
            reply = self.run_lldptool(["get-tlv", "-n", "-i", port_name,
                                       "-g", "ncb"])
        else:
            if is_nb:
                reply = self.run_lldptool(["get-tlv", "-n", "-i", port_name,
                                           "-g", "nb"])
            else:
                LOG.error("Both NCB and NB are not selected to query LLDP")
        return reply

    def run_lldptool(self, args):
        '''Function for invoking the lldptool utility'''
        full_args = ['lldptool'] + args
        try:
            return utils.execute(full_args, root_helper=self.root_helper)
        except Exception as exc:
            LOG.error("Unable to execute %(cmd)s. "
                      "Exception: %(exception)s",
                      {'cmd': full_args, 'exception': str(exc)})

    def get_remote_evb_cfgd(self, tlv_data):
        ''' Returns IF EVB TLV is present in the TLV '''
        if tlv_data is None:
            return False
        evb_mode_set = tlv_data.split("EVB Configuration TLV")
        if len(evb_mode_set) < 2:
            return False
        next_tlv_list = evb_mode_set[1].split('TLV')[0]
        mode_val_set = next_tlv_list.split("mode:")
        if len(mode_val_set) < 2:
            return False
        return True

    def get_remote_evb_mode(self, tlv_data):
        ''' Returns the EVB mode in the TLV '''
        if tlv_data is None:
            return None
        evb_mode_set = tlv_data.split("EVB Configuration TLV")
        if len(evb_mode_set) < 2:
            return None
        next_tlv_list = evb_mode_set[1].split('TLV')[0]
        mode_val_set = next_tlv_list.split("mode:")
        if len(mode_val_set) < 2:
            return None
        mode_val = mode_val_set[1].split()[0].strip()
        return mode_val

    def get_remote_mgmt_addr(self, tlv_data):
        ''' Returns Remote Mgmt Addr from the TLV '''
        if tlv_data is None:
            return None
        mgmt_addr_set = tlv_data.split("Management Address TLV")
        if len(mgmt_addr_set) < 2:
            return None
        next_tlv_list = mgmt_addr_set[1].split('TLV')[0]
        mgmt_val_set = next_tlv_list.split('IPv4:')
        if len(mgmt_val_set) < 2:
            return None
        addr_fam = 'IPv4:'
        addr = mgmt_val_set[1].split('\n')[0].strip()
        return addr_fam + addr

    def get_remote_sys_desc(self, tlv_data):
        ''' Returns Remote Sys Desc from the TLV '''
        if tlv_data is None:
            return None
        sys_desc_set = tlv_data.split("System Description TLV")
        if len(sys_desc_set) < 2:
            return None
        next_tlv_list = sys_desc_set[1].split('TLV')[0]
        sys_val_set = next_tlv_list.split('\n')
        if len(sys_val_set) < 2:
            return None
        return sys_val_set[1].strip()

    def get_remote_sys_name(self, tlv_data):
        ''' Returns Remote Sys Name from the TLV '''
        if tlv_data is None:
            return None
        sys_name_set = tlv_data.split("System Name TLV")
        if len(sys_name_set) < 2:
            return None
        next_tlv_list = sys_name_set[1].split('TLV')[0]
        sys_val_set = next_tlv_list.split('\n')
        if len(sys_val_set) < 2:
            return None
        return sys_val_set[1].strip()

    def get_remote_port(self, tlv_data):
        ''' Returns Remote Port from the TLV '''
        if tlv_data is None:
            return None
        port_set = tlv_data.split("Port Description TLV")
        if len(port_set) < 2:
            return None
        next_tlv_list = port_set[1].split('TLV')[0]
        port_val_set = next_tlv_list.split('\n')
        if len(port_val_set) < 2:
            return None
        return port_val_set[1].strip()

    def get_remote_chassis_id_mac(self, tlv_data):
        ''' Returns Remote Chassis ID MAC from the TLV '''
        if tlv_data is None:
            return None
        chassis_mac_set = tlv_data.split("Chassis ID TLV")
        if len(chassis_mac_set) < 2:
            return None
        next_tlv_list = chassis_mac_set[1].split('TLV')[0]
        chassis_val_set = next_tlv_list.split('MAC:')
        if len(chassis_val_set) < 2:
            return None
        mac = chassis_val_set[1].split('\n')
        return mac[0].strip()

    def get_remote_port_id_mac(self, tlv_data):
        ''' Returns Remote Port ID MAC from the TLV '''
        if tlv_data is None:
            return None
        port_mac_set = tlv_data.split("Port ID TLV")
        if len(port_mac_set) < 2:
            return None
        next_tlv_list = port_mac_set[1].split('TLV')[0]
        mac_list = next_tlv_list.split('MAC:')
        if len(mac_list) < 2:
            return None
        mac = mac_list[1].split('\n')
        return mac[0].strip()

    def get_remote_port_id_local(self, tlv_data):
        ''' Returns Remote Port ID Local from the TLV '''
        if tlv_data is None:
            return None
        port_local_set = tlv_data.split("Port ID TLV")
        if len(port_local_set) < 2:
            return None
        next_tlv_list = port_local_set[1].split('TLV')[0]
        local_list = next_tlv_list.split('Local:')
        if len(local_list) < 2:
            return None
        local = local_list[1].split('\n')
        return local[0].strip()
