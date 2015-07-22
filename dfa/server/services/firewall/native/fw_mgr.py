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

import stevedore

from dfa.db import dfa_db_models as dfa_dbm
from dfa.common import dfa_logger as logging
from dfa.server.dfa_openstack_helper import DfaNeutronHelper as OsHelper
from dfa.server.services.firewall.native import fabric_setup_base as fabric
from dfa.server.services.firewall.native import fw_constants
from dfa.server.services.firewall.native.drivers import dev_mgr

LOG = logging.getLogger(__name__)


# Remove after DB is implemented
class FwTempDb(dfa_dbm.DfaDBMixin):

    '''
    This class maintains a mapping of rule, policies and FW and its associated
    tenant ID
    '''

    def __init__(self):
        self.rule_tenant_dict = {}
        self.policy_tenant_dict = {}
        self.fw_tenant_dict = {}

    def store_rule_tenant(self, rule_id, tenant_id):
        self.rule_tenant_dict[rule_id] = tenant_id

    def get_rule_tenant(self, rule_id):
        return self.rule_tenant_dict[rule_id]

    def store_policy_tenant(self, policy_id, tenant_id):
        self.policy_tenant_dict[policy_id] = tenant_id

    def get_policy_tenant(self, policy_id):
        return self.policy_tenant_dict[policy_id]

    def store_fw_tenant(self, fw_id, tenant_id):
        self.fw_tenant_dict[fw_id] = tenant_id

    def get_fw_tenant(self, fw_id):
        return self.fw_tenant_dict[fw_id]

    def del_fw_tenant(self, fw_id):
        del self.fw_tenant_dict[fw_id]

    def del_policy_tenant(self, pol_id):
        del self.policy_tenant_dict[pol_id]

    def del_rule_tenant(self, rule_id):
        del self.rule_tenant_dict[rule_id]


class FwMapAttr(object):

    '''Firewall Attributes. This is created per tenant'''

    def __init__(self, tenant_id):
        self.rules = {}
        self.policies = {}
        self.tenant_id = tenant_id
        self.rule_cnt = 0
        self.policy_cnt = 0
        self.active_pol_id = None
        self.fw_created = False
        self.fw_drvr_status = False
        self.fw_id = None

    def store_policy(self, pol_id, policy):
        if pol_id not in self.policies:
            self.policies[pol_id] = policy
            self.policy_cnt += 1

    def store_rule(self, rule_id, rule):
        if rule_id not in self.rules:
            self.rules[rule_id] = rule
            self.rule_cnt += 1

    def delete_rule(self, rule_id):
        if rule_id not in self.rules:
            LOG.error("No Rule id present for deleting %s" % rule_id)
            return
        del self.rules[rule_id]
        self.rule_cnt -= 1
        # No need to navigate through self.policies to delete rules since
        # if a rule is a part of a policy, Openstack would not allow to delete
        # that rule

    def is_rule_present(self, rule_id):
        if rule_id not in self.rules:
            return False
        else:
            return True

    def rule_update(self, rule_id, rule):
        if rule_id not in self.rules:
            LOG.error("Rule ID not present %s" % rule_id)
            return
        self.rules[rule_id].update(rule)

    def is_policy_present(self, pol_id):
        return pol_id in self.policies

    def is_fw_present(self, fw_id):
        if self.fw_id is None or self.fw_id != fw_id:
            return False
        else:
            return True

    def create_fw(self, proj_name, pol_id, fw_id, fw_name):
        self.tenant_name = proj_name
        self.fw_id = fw_id
        self.fw_name = fw_name
        self.fw_created = True
        self.active_pol_id = pol_id

    def delete_fw(self, fw_id):
        self.fw_id = None
        self.fw_name = None
        self.fw_created = False
        self.active_pol_id = None

    def delete_policy(self, pol_id):
        if pol_id not in self.policies:
            LOG.error("Invalid policy %s" % pol_id)
            return
        del self.policies[pol_id]
        self.policy_cnt -= 1

    def is_fw_complete(self):
        # This API returns the complete status of FW.
        # This returns True if a FW is created with a active policy that has
        # more than one rule associated with it and if a driver init is done
        # successfully.
        return self.fw_created and self.active_pol_id and (
            self.is_fw_drvr_created()) and (
            self.active_pol_id in self.policies) and (
            len(self.policies[self.active_pol_id]['rule_dict'])) > 0 and (
            self.one_rule_present(self.active_pol_id))

    def is_fw_drvr_create_needed(self):
        # This API returns True if a driver init needs to be performed
        # This returns True if a FW is created with a active policy that has
        # more than one rule associated with it and if a driver init is NOT
        # done.
        return self.fw_created and self.active_pol_id and (
            not self.is_fw_drvr_created()) and (
            self.active_pol_id in self.policies) and (
            len(self.policies[self.active_pol_id]['rule_dict'])) > 0 and (
            self.one_rule_present(self.active_pol_id))

    def one_rule_present(self, pol_id):
        pol_dict = self.policies[pol_id]
        for rule in pol_dict['rule_dict']:
            if self.is_rule_present(rule):
                return True
        return False

    def fw_drvr_created(self, status):
        # This stores the status of the driver init, this API assumes only one
        # FW driver
        self.fw_drvr_status = status

    def is_fw_drvr_created(self):
        # This returns the status of the driver, this API assumes only one FW
        # driver
        return self.fw_drvr_status

    def get_fw_dict(self):
        fw_dict = {}
        fw_dict['rules'] = {}
        fw_dict['tenant_name'] = self.tenant_name
        fw_dict['tenant_id'] = self.tenant_id
        fw_dict['fw_id'] = self.fw_id
        fw_dict['fw_name'] = self.fw_name
        fw_dict['firewall_policy_id'] = self.active_pol_id
        pol_dict = self.policies[self.active_pol_id]
        for rule in pol_dict['rule_dict']:
            fw_dict['rules'][rule] = self.rules[rule]
        return fw_dict


class FwMgr(dev_mgr.DeviceMgr):

    '''Firewall Native Manager'''

    def __init__(self, cfg):
        LOG.debug("Initializing Native FW Manager")
        super(FwMgr, self).__init__(cfg)
        self.events.update({
            'firewall_rule.create.end': self.fw_rule_create,
            'firewall_rule.delete.end': self.fw_rule_delete,
            'firewall_rule.update.end': self.fw_rule_update,
            'firewall_policy.create.end': self.fw_policy_create,
            'firewall_policy.delete.end': self.fw_policy_delete,
            'firewall.create.end': self.fw_create,
            'firewall.delete.end': self.fw_delete})
        self.fwid_attr = {}
        self.pid_dict = {}
        self.rules_id = {}
        self.fw_drvr_created = False
        self.fabric = fabric.FabricBase()
        self.temp_db = FwTempDb()
        self.os_helper = OsHelper()
        fw_dict = self.pop_local_cache()
        self.pop_local_sch_cache(fw_dict)

    def populate_cfg_dcnm(self, cfg, dcnm_obj):
        self.dcnm_obj = dcnm_obj
        self.fabric.store_dcnm(dcnm_obj)

    def _check_create_fw(self, tenant_id, drvr_name):
        if self.fwid_attr[tenant_id].is_fw_drvr_create_needed():
            fw_dict = self.fwid_attr[tenant_id].get_fw_dict()
            is_fw_virt = self.is_device_virtual()
            # What's the third argumennt really doing? TODO
            ret = self.add_fw_db(fw_dict.get('fw_id'), fw_dict,
                                 fw_constants.RESULT_FW_CREATE_INIT)
            if not ret:
                return
            ret = self.fabric.prepare_fabric_fw(tenant_id,
                                                fw_dict.get('tenant_name'),
                                                fw_dict.get('fw_id'),
                                                fw_dict.get('fw_name'),
                                                is_fw_virt)
            if not ret:
                return
            else:
                self.update_fw_db_final_result(fw_dict.get('fw_id'), (
                    fw_constants.RESULT_FW_CREATE_DONE))
            ret = self.create_fw_device(tenant_id, fw_dict.get('fw_id'),
                                        fw_dict)
            if ret:
                self.fwid_attr[tenant_id].fw_drvr_created(True)
                self.update_fw_db_dev_status(fw_dict.get('fw_id'), 'SUCCESS')

    def _check_delete_fw(self, tenant_id, drvr_name):
        fw_dict = self.fwid_attr[tenant_id].get_fw_dict()
        is_fw_virt = self.is_device_virtual()
        self.update_fw_db_final_result(fw_dict.get('fw_id'), (
            fw_constants.RESULT_FW_DELETE_INIT))
        ret = self.fabric.delete_fabric_fw(tenant_id,
                                           fw_dict.get('tenant_name'),
                                           fw_dict.get('fw_id'),
                                           fw_dict.get('fw_name'),
                                           is_fw_virt)
        if not ret:
            return
        self.update_fw_db_final_result(fw_dict.get('fw_id'), (
            fw_constants.RESULT_FW_DELETE_DONE))
        if self.fwid_attr[tenant_id].is_fw_drvr_created():
            ret = self.delete_fw_device(tenant_id, fw_dict.get('fw_id'),
                                        fw_dict)
            if ret:
                self.fwid_attr[tenant_id].fw_drvr_created(False)
                self.delete_fw(fw_dict.get('fw_id'))

    def _check_update_fw(self, tenant_id, drvr_name):
        if self.fwid_attr[tenant_id].is_fw_complete():
            fw_dict = self.fwid_attr[tenant_id].get_fw_dict()
            self.modify_fw_device(tenant_id, fw_dict)

    def _fw_create(self, drvr_name, data, cache):
        fw = data.get('firewall')
        tenant_id = fw.get('tenant_id')
        fw_name = fw.get('name')
        fw_id = fw.get('id')
        fw_pol_id = fw.get('firewall_policy_id')
        admin_state = fw.get('admin_state_up')
        if not admin_state:
            LOG.debug("Admin state disabled")
            return

        if tenant_id not in self.fwid_attr:
            self.fwid_attr[tenant_id] = FwMapAttr(tenant_id)
        tenant_obj = self.fwid_attr[tenant_id]
        name = dfa_dbm.DfaDBMixin.get_project_name(self, tenant_id)
        tenant_obj.create_fw(name, fw_pol_id, fw_id, fw_name)
        self.temp_db.store_fw_tenant(fw_id, tenant_id)
        if not cache:
            self._check_create_fw(tenant_id, drvr_name)
        if fw_pol_id is not None and not (
                tenant_obj.is_policy_present(fw_pol_id)):
            pol_data = self.os_helper.get_fw_policy(fw_pol_id)
            self.fw_policy_create(pol_data, cache=cache)

    def fw_create(self, data, fw_name=None, cache=False):
        LOG.debug("FW Debug")
        try:
            self._fw_create(fw_name, data, cache)
        except Exception as e:
            LOG.error("Exception in fw_create %s" % str(e))

    def _fw_delete(self, drvr_name, data):
        fw_id = data.get('firewall_id')
        tenant_id = self.temp_db.get_fw_tenant(fw_id)

        if tenant_id not in self.fwid_attr:
            LOG.error("Invalid tenant id for FW delete %s" % tenant_id)
            return
        tenant_obj = self.fwid_attr[tenant_id]
        self._check_delete_fw(tenant_id, drvr_name)
        tenant_obj.delete_fw(fw_id)
        self.temp_db.del_fw_tenant(fw_id)

    def fw_delete(self, data, fw_name=None):
        LOG.debug("FW Debug")
        try:
            self._fw_delete(fw_name, data)
        except Exception as e:
            LOG.error("Exception in fw_delete %s" % str(e))

    def _fw_rule_create(self, drvr_name, data, cache):
        tenant_id = data.get('firewall_rule').get('tenant_id')
        rule = {}
        fw_rule = data.get('firewall_rule')
        rule['protocol'] = fw_rule.get('protocol')
        rule['source_ip_address'] = fw_rule.get('source_ip_address')
        rule['destination_ip_address'] = fw_rule.get('destination_ip_address')
        rule['source_port'] = fw_rule.get('source_port')
        rule['destination_port'] = fw_rule.get('destination_port')
        rule['action'] = fw_rule.get('action')
        rule['enabled'] = fw_rule.get('enabled')
        rule['name'] = fw_rule.get('name')
        fw_pol_id = fw_rule.get('firewall_policy_id')
        rule_id = fw_rule.get('id')
        if tenant_id not in self.fwid_attr:
            self.fwid_attr[tenant_id] = FwMapAttr(tenant_id)
        self.fwid_attr[tenant_id].store_rule(rule_id, rule)
        if not cache:
            self._check_create_fw(tenant_id, drvr_name)
        self.temp_db.store_rule_tenant(rule_id, tenant_id)
        if fw_pol_id is not None and not (
                self.fwid_attr[tenant_id].is_policy_present(fw_pol_id)):
            pol_data = self.os_helper.get_fw_policy(fw_pol_id)
            self.fw_policy_create(pol_data, cache=cache)

    def fw_rule_create(self, data, fw_name=None, cache=False):
        LOG.debug("FW Rule Debug")
        try:
            self._fw_rule_create(fw_name, data, cache)
        except Exception as e:
            LOG.error("Exception in fw_rule_create %s" % str(e))

    def _fw_rule_delete(self, drvr_name, data):
        rule_id = data.get('firewall_rule_id')
        tenant_id = self.temp_db.get_rule_tenant(rule_id)

        if tenant_id not in self.fwid_attr:
            LOG.error("Invalid tenant id for FW delete %s" % tenant_id)
            return
        tenant_obj = self.fwid_attr[tenant_id]
        # Guess actual FW/policy need not be deleted if this is the active
        # rule, Openstack does not allow it to be deleted
        tenant_obj.delete_rule(rule_id)
        self.temp_db.del_rule_tenant(rule_id)

    def fw_rule_delete(self, data, fw_name=None):
        LOG.debug("FW Rule delete")
        try:
            self._fw_rule_delete(fw_name, data)
        except Exception as e:
            LOG.error("Exception in fw_rule_delete %s" % str(e))

    def _fw_rule_update(self, drvr_name, data):
        LOG.debug("FW Update %s" % data)
        tenant_id = data.get('firewall_rule').get('tenant_id')
        rule = {}
        fw_rule = data.get('firewall_rule')
        rule['protocol'] = fw_rule.get('protocol')
        rule['source_ip_address'] = fw_rule.get('source_ip_address')
        rule['destination_ip_address'] = fw_rule.get('destination_ip_address')
        rule['source_port'] = fw_rule.get('source_port')
        rule['destination_port'] = fw_rule.get('destination_port')
        rule['action'] = fw_rule.get('action')
        rule['enabled'] = fw_rule.get('enabled')
        rule['name'] = fw_rule.get('name')
        rule_id = fw_rule.get('id')
        if tenant_id not in self.fwid_attr or not (
           self.fwid_attr[tenant_id].is_rule_present(rule_id)):
            LOG.error("Incorrect update info for tenant %s" % tenant_id)
            return
        self.fwid_attr[tenant_id].rule_update(rule_id, rule)
        self._check_update_fw(tenant_id, drvr_name)

    def fw_rule_update(self, data, fw_name=None):
        LOG.debug("FW Update Debug")
        try:
            self._fw_rule_update(fw_name, data)
        except Exception as e:
            LOG.error("Exception in fw_rule_update %s" % str(e))

    def _fw_policy_delete(self, drvr_name, data):
        policy_id = data.get('firewall_policy_id')
        tenant_id = self.temp_db.get_policy_tenant(policy_id)

        if tenant_id not in self.fwid_attr:
            LOG.error("Invalid tenant id for FW delete %s" % tenant_id)
            return
        tenant_obj = self.fwid_attr[tenant_id]
        # Guess actual FW need not be deleted since if this is the active
        # policy, Openstack does not allow it to be deleted
        tenant_obj.delete_policy(policy_id)
        self.temp_db.del_policy_tenant(policy_id)

    def fw_policy_delete(self, data, fw_name=None):
        LOG.debug("FW Policy Debug")
        try:
            self._fw_policy_delete(fw_name, data)
        except Exception as e:
            LOG.error("Exception in fw_policy_delete %s" % str(e))

    def _fw_policy_create(self, drvr_name, data, cache):
        policy = {}
        fw_policy = data.get('firewall_policy')
        tenant_id = fw_policy.get('tenant_id')
        policy_id = fw_policy.get('id')
        policy_name = fw_policy.get('name')
        pol_rule_dict = fw_policy.get('firewall_rules')
        if tenant_id not in self.fwid_attr:
            self.fwid_attr[tenant_id] = FwMapAttr(tenant_id)
        policy['name'] = policy_name
        policy['rule_dict'] = pol_rule_dict
        self.fwid_attr[tenant_id].store_policy(policy_id, policy)
        if not cache:
            self._check_create_fw(tenant_id, drvr_name)
        self.temp_db.store_policy_tenant(policy_id, tenant_id)
        for rule in pol_rule_dict:
            rule_id = rule
            if not self.fwid_attr[tenant_id].is_rule_present(rule_id):
                rule_data = self.os_helper.get_fw_rule(rule_id)
                self.fw_rule_create(rule_data, cache=cache)

    def fw_policy_create(self, data, fw_name=None, cache=False):
        LOG.debug("FW Policy Debug")
        try:
            self._fw_policy_create(fw_name, data, cache)
        except Exception as e:
            LOG.error("Exception in fw_policy_create %s" % str(e))

    def convert_fwdb_event_msg(self, rule, tenant_id, rule_id, policy_id):
        fw_rule_data = {}
        rule['tenant_id'] = tenant_id
        rule['id'] = rule_id
        rule['firewall_policy_id'] = policy_id
        fw_rule_data['firewall_rule'] = rule
        return fw_rule_data

    def pop_local_cache(self):
        fw_dict = self.get_all_fw_db()
        for fw_id in fw_dict:
            fw_data = fw_dict.get(fw_id)
            tenant_id = fw_data.get('tenant_id')
            rule_dict = fw_data.get('rules').get('rules')
            policy_id = fw_data.get('rules').get('firewall_policy_id')
            for rule in rule_dict:
                fw_evt_data = self.convert_fwdb_event_msg(rule_dict.get(rule),
                                                          tenant_id, rule,
                                                          policy_id)
                self.fw_rule_create(fw_evt_data, cache=True)
            fw_data = self.os_helper.get_fw(fw_id)
            self.fw_create(fw_data, cache=True)
        return fw_dict
