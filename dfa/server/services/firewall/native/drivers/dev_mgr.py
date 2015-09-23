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

from dfa.common import dfa_logger as logging
from dfa.server.services.firewall.native import fw_constants as fw_const
from dfa.server.services.firewall.native.drivers import dev_mgr_plug

LOG = logging.getLogger(__name__)


# Not sure of the exact name. But, this implements a case when all requests
# goto first device until it exhausts
class MaxSched(object):

    '''
    Max Sched. This scheduler will return the first firewall until it reaches
    its quota
    '''

    def __init__(self, obj_dict):
        ''' Initialization '''
        self.num_res = len(obj_dict)
        self.obj_dict = obj_dict
        self.res = dict()
        cnt = 0
        for ip in self.obj_dict:
            self.res[cnt] = dict()
            obj_elem_dict = self.obj_dict.get(ip)
            drvr_obj = obj_elem_dict.get('drvr_obj')
            self.res[cnt]['mgmt_ip'] = ip
            self.res[cnt]['quota'] = drvr_obj.get_max_quota()
            self.res[cnt]['obj_dict'] = obj_elem_dict
            self.res[cnt]['used'] = 0
            self.res[cnt]['fw_id'] = None
            cnt = cnt + 1

    def allocate_fw_dev(self, fw_id, new):
        '''
        Allocate the first Firewall device which has resources available
        '''
        for cnt in self.res:
            used = self.res.get(cnt).get('used')
            if used < self.res.get(cnt).get('quota'):
                if new:
                    self.res[cnt]['used'] = used + 1
                self.res[cnt]['fw_id'] = fw_id
                return self.res[cnt].get('obj_dict'), (
                    self.res[cnt].get('mgmt_ip'))
        return None, None

    def get_fw_dev_map(self, fw_id):
        ''' Return the object dict and mgmt ip for a firewall '''
        for cnt in self.res:
            if self.res.get(cnt).get('fw_id') == fw_id:
                return self.res[cnt].get('obj_dict'), (
                    self.res[cnt].get('mgmt_ip'))
        return None, None

    def deallocate_fw_dev(self, fw_id):
        ''' Release the firewall resource '''
        for cnt in self.res:
            if self.res.get(cnt).get('fw_id') == fw_id:
                self.res[cnt]['used'] = self.res[cnt]['used'] - 1


class DeviceMgr(object):

    '''Device Manager'''

    def __init__(self, cfg):
        ''' Initialization '''
        self.drvr_obj = {}
        self.mgmt_ip_list = cfg.firewall.fw_mgmt_ip
        self.mgmt_ip_list = self.mgmt_ip_list.strip('[').rstrip(']').split(',')
        self.user_list = cfg.firewall.username
        self.user_list = self.user_list.strip('[').rstrip(']').split(',')
        self.pwd_list = cfg.firewall.pwd
        self.pwd_list = self.pwd_list.strip('[').rstrip(']').split(',')
        self.interface_in_list = cfg.firewall.interface_in
        self.interface_in_list = self.interface_in.strip('[').rstrip(']').\
            split(',')
        self.interface_out_list = cfg.firewall.interface_out
        self.interface_out_list = self.interface_out.strip('[').rstrip(']').\
            split(',')
        self.obj_dict = dict()
        cnt = 0
        dev = cfg.firewall.device
        # Modify enabler_conf.ini in source path for IP list
        for ip in self.mgmt_ip_list:
            ip = ip.strip()
            obj = dev_mgr_plug.DeviceMgr(cfg, dev)
            self.obj_dict[ip] = dict()
            self.obj_dict[ip]['drvr_obj'] = obj.get_drvr_obj()
            self.obj_dict[ip]['dev_name'] = cfg.firewall.device.split(',')[cnt]
            cnt = cnt + 1
        self.drvr_initialize(cfg)
        if cfg.firewall.sched_policy == fw_const.SCHED_POLICY:
            self.sched_obj = MaxSched(self.obj_dict)

    def pop_local_sch_cache(self, fw_dict):
        ''' Populate the local cache from FW DB after restart '''
        for fw_id in fw_dict:
            fw_data = fw_dict.get(fw_id)
            mgmt_ip = fw_data.get('fw_mgmt_ip')
            dev_status = fw_data.get('device_provision_status')
            if dev_status is 'SUCCESS':
                new = True
            else:
                new = False
            if mgmt_ip is not None:
                drvr_dict, mgmt_ip = self.sched_obj.allocate_fw_dev(fw_id,
                                                                    new)
                if drvr_dict is None or mgmt_ip is None:
                    LOG.info("Pop cache for FW sch: drvr_dict or mgmt_ip is "
                             "None")

    def drvr_initialize(self, cfg):
        ''' Initialize the driver routines '''
        cnt = 0
        for ip in self.obj_dict:
            cfg_dict = {}
            drvr_obj = self.obj_dict.get(ip).get('drvr_obj')
            cfg_dict['mgmt_ip_addr'] = ip
            cfg_dict['user'] = self.user_list[cnt]
            cfg_dict['pwd'] = self.pwd_list[cnt]
            cfg_dict['interface_in'] = self.interface_in_list[cnt]
            cfg_dict['interface_out'] = self.interface_out_list[cnt]
            drvr_obj.initialize(cfg_dict)
            cnt = cnt + 1

    def pop_evnt_que(self, que_obj):
        '''
        Populates the event queue object for sending router events to
        event handler
        '''
        for ip in self.obj_dict:
            drvr_obj = self.obj_dict.get(ip).get('drvr_obj')
            drvr_obj.pop_evnt_que(que_obj)

    def pop_dcnm_obj(self, dcnm_obj):
        '''
        Populates the DCNM object
        '''
        for ip in self.obj_dict:
            drvr_obj = self.obj_dict.get(ip).get('drvr_obj')
            drvr_obj.pop_dcnm_obj(dcnm_obj)

    def is_device_virtual(self):
        ''' Returns if the device is physical or virtual '''
        for ip in self.obj_dict:
            drvr_obj = self.obj_dict.get(ip).get('drvr_obj')
            ret = drvr_obj.is_device_virtual()
            # No way to pin a device as of now, so return the first TODO
            return ret

    def create_fw_device(self, tenant_id, fw_id, data):
        ''' Creates the Firewall '''
        drvr_dict, mgmt_ip = self.sched_obj.allocate_fw_dev(fw_id, True)
        if drvr_dict is not None and mgmt_ip is not None:
            self.update_fw_db_mgmt_ip(fw_id, mgmt_ip)
            ret = drvr_dict.get('drvr_obj').create_fw(tenant_id, data)
            if not ret:
                self.sched_obj.deallocate_fw_dev(fw_id)
            return ret
        else:
            return False

    def delete_fw_device(self, tenant_id, fw_id, data):
        ''' Deletes the Firewall '''
        drvr_dict, mgmt_ip = self.sched_obj.get_fw_dev_map(fw_id)
        ret = drvr_dict.get('drvr_obj').delete_fw(tenant_id, data)
        # FW DB gets deleted, so no need to remove the MGMT IP
        if ret:
            self.sched_obj.deallocate_fw_dev(fw_id)
        return ret

    def modify_fw_device(self, tenant_id, fw_id, data):
        ''' Modifies the firewall cfg '''
        drvr_dict, mgmt_ip = self.sched_obj.get_fw_dev_map(fw_id)
        return drvr_dict.get('drvr_obj').modify_fw(tenant_id, data)

    def nwk_create_notif(self, tenant_id, tenant_name, cidr):
        '''
        Notification for Network create.
        Since FW ID not present, it's not possible to know which FW instance
        to call. So, calling everyone, each instance will figure out if it
        applies to them
        '''
        for ip in self.obj_dict:
            drvr_obj = self.obj_dict.get(ip).get('drvr_obj')
            ret = drvr_obj.nwk_create_notif(tenant_id, tenant_name, cidr)
            LOG.info("Drvr with IP %(ip)s return %(ret)s",
                     {'ip': ip, 'ret': ret})

    def nwk_delete_notif(self, tenant_id, tenant_name, net_id):
        '''
        Notification for Network delete.
        Since FW ID not present, it's not possible to know which FW instance
        to call. So, calling everyone, each instance will figure out if it
        applies to them
        '''
        for ip in self.obj_dict:
            drvr_obj = self.obj_dict.get(ip).get('drvr_obj')
            ret = drvr_obj.nwk_delete_notif(tenant_id, tenant_name, net_id)
            LOG.info("Drvr with IP %(ip)s return %(ret)s for nwk delete notif",
                     {'ip': ip, 'ret': ret})
