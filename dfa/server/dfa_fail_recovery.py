# Copyright 2014 Cisco Systems, Inc.
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
# @author: Nader Lahouti, Cisco Systems, Inc.

import six
import time

from dfa.common import constants
from dfa.common import dfa_exceptions as dexc
from dfa.common import dfa_logger as logging
from dfa.common import utils

LOG = logging.getLogger(__name__)


class DfaFailureRecovery(object):

    """Failure recovery class."""

    def __init__(self, cfg):
        super(DfaFailureRecovery, self).__init__(cfg)
        self._cfg = cfg

    @property
    def cfg(self):
        return self._cfg

    def add_events(self, **kwargs):
        """Add failure event into the queue."""

        event_q = kwargs.get('event_queue')
        pri = kwargs.get('priority')
        if not event_q or not pri:
            return

        try:
            event_type = 'server.failure.recovery'
            payload = {}
            timestamp = time.ctime()
            data = (event_type, payload)
            event_q.put((pri, timestamp, data))
            LOG.debug('Added failure recovery event to the queue.')
        except Exception as exc:
            LOG.exception('Error: %s for event %s' % (str(exc), event_type))
            raise exc

    def _failure_vms_migration(self, vm, vm_info):
        """Processes failure recovery for VM migration case."""

        vmr = eval(vm.result)
        params = None

        # Go through the result field of this instance. There are two cases
        # which needs to be covered:
        # 1. Processing the info in 'src' field - which means the VM was
        #    migrated from the hosts saved in the result. If there is failure,
        #    it is because of failing in delete request. So, try to send
        #    delete request to agents on those hosts.
        # 2. Processing the info in the 'dst' field - which means the VM was
        #    migrated to the hosts saved in the result. If there is a failure,
        #    it is because of creation failure. Then send create request to the
        #    agent on that host.
        for to_host, to_val in six.iteritems(vmr.get(constants.MIG_TO)):
            to_res = to_val.get('res')
            if to_res == constants.CREATE_FAIL:
                try:
                    vm_info.update({'host': to_host, 'status': 'up'})
                    self.neutron_event.send_vm_info(str(to_host), str(vm_info))
                    vmr.get(constants.MIG_TO).get(to_host).update(
                        {'res': constants.RESULT_SUCCESS})
                    params = dict(columns=dict(result=str(vmr)))
                    to_res = constants.RESULT_SUCCESS
                except Exception as e:
                    # Failed to send info to the agent. Keep the data in the
                    # database as failure to send it later.
                    to_res = constants.CREATE_FAIL
                    reason = ('Failed to send create VM info to agent %s'
                              'Reason %s' % (to_host, str(e)))
                    LOG.error(reason)
                    self.update_reason_in_port_result(vm_info.port_id, reason)

        res_list = []
        for from_host, from_val in six.iteritems(vmr.get(constants.MIG_FROM)):
            if from_val.get('res') == constants.DELETE_FAIL:
                if self.is_agent_alive(from_host):
                    try:
                        vm_info.update({'host': from_host,
                                        'status': 'down'})
                        self.neutron_event.send_vm_info(str(from_host),
                                                        str(vm_info))
                        vmr.get(constants.MIG_FROM).get(from_host).update(
                            {'res': constants.DELETE_PENDING})
                        res_list.append(False)
                        params = dict(columns=dict(result=str(vmr)))
                    except Exception as e:
                        # Failed to send info to agent. Keep the data in the
                        # database as failure to send it later.
                        reason = ('Failed to send create VM info to agent %s'
                                  'Reason %s' % (from_host, str(e)))
                        LOG.error(reason)
                        res_list.append(False)
                        self.update_reason_in_port_result(vm_info.port_id,
                                                          reason)
                else:
                    # The compute node or agent is not responsive, mark the
                    # result as success and consider the delete is completed.
                    if from_val.get('res') in constants.DELETE_LIST:
                        vmr.get(constants.MIG_FROM).get(from_host).update(
                            {'res': constants.RESULT_SUCCESS})
                        res_list.append(True)
                        params = dict(columns=dict(result=str(vmr)))
                        LOG.debug('Agent %(agent)s is not responsive. '
                                  'Setting status to success. %(result)s',
                                  {'agent': from_host, 'result': str(vmr)})

        if all(res_list):
            # The delete on source host was successfull. Now VM exist on the
            # destination host. Mark result field to success and status to 'up'
            params = dict(columns=dict(status='up',
                                       result=to_res))

        if params:
            self.update_vm_db(vm.port_id, **params)
            LOG.info('Processing migration %(port)s %(params)s.',
                     {'port': vm.port_id, 'params': params})

    def _failure_vms(self, vm, vm_info):
        if vm.result == constants.CREATE_FAIL:
            try:
                self.neutron_event.send_vm_info(str(vm.host), str(vm_info))
            except Exception as e:
                # Failed to send info to the agent. Keep the data in the
                # database as failure to send it later.
                reason = ('Failed to send create VM info to agent %s'
                          'Reason %s' % (vm.host, str(e)))
                LOG.error(reason)
                self.update_reason_in_port_result(vm.port_id, reason)
            else:
                params = dict(columns=dict(
                    result=constants.RESULT_SUCCESS))
                self.update_vm_db(vm.port_id, **params)
                LOG.info('Created VM %(vm)s.', {'vm': vm.name})

        if vm.result == constants.DELETE_FAIL:
            try:
                self.neutron_event.send_vm_info(str(vm.host), str(vm_info))
            except Exception as e:
                reason = ('Failed to send delete VM info to agent %s'
                          'Reason %s' % (vm.host, str(e)))
                LOG.error(reason)
                self.update_reason_in_port_result(vm.port_id, reason)
            else:
                # Do not delete the vm from database, as it may not be
                # deleted in the agent side. Keep it in the database till
                # agent sends success on deleting the VM, then delete it
                # from database.
                # Updating the result to delete pending state.
                params = dict(columns=dict(
                    status='down', result=constants.DELETE_PENDING))
                self.update_vm_db(vm.port_id, **params)
                LOG.info('VM %(vm)s is in delete pending state.',
                         {'vm': vm.port_id})

    def failure_recovery(self, fail_info):
        """Failure recovery task.

        In case of failure in projects, network and VM create/delete, this
        task goes through all failure cases and try the request.
        """
        # Read failed entries from project database and send request
        # (create/delete - depends on failure type) to DCNM

        # 1. Try failure recovery for create project.
        LOG.info("Started failure_recovery.")
        projs = self.get_fialed_projects_entries(constants.CREATE_FAIL)
        for proj in projs:
            LOG.debug("Failure recovery for project %(name)s." % (
                {'name': proj.name}))
            # Try to create the project in DCNM
            try:
                self.dcnm_client.create_project(self.cfg.dcnm.orchestrator_id,
                                                proj.name,
                                                self.cfg.dcnm.
                                                default_partition_name,
                                                proj.dci_id)
            except dexc.DfaClientRequestFailed as e:
                LOG.error("failure_recovery: Failed to create %(proj)s on "
                          "DCNM : %(reason)s" % (
                              {'proj': proj.name, 'reason': str(e)}))
                self.update_project_info_cache(proj.id,
                                               dci_id=proj.dci_id,
                                               name=proj.name,
                                               opcode='update',
                                               reason=e.args[0])
            else:
                # Request is sent successfully, update the database.
                self.update_project_info_cache(proj.id, dci_id=proj.dci_id,
                                               name=proj.name,
                                               opcode='update')
                LOG.debug('Success on failure recovery for '
                          'project %(name)s', {'name': proj.name})

        # 1.1 Try failure recovery for update project.
        projs = self.get_fialed_projects_entries(constants.UPDATE_FAIL)
        for proj in projs:
            LOG.debug("Failure recovery for project %(name)s.",  (
                {'name': proj.name}))
            # This was failure of updating DCI id of the project in DCNM.
            try:
                self.dcnm_client.update_project(proj.name,
                                                self.cfg.dcnm.
                                                default_partition_name,
                                                dci_id=proj.dci_id)
            except dexc.DfaClientRequestFailed as exc:
                LOG.error("failure_recovery: Failed to update %(proj)s on "
                          "DCNM : %(reason)s",
                          {'proj': proj.name, 'reason': str(exc)})
                self.update_project_info_cache(proj.id,
                                               dci_id=proj.dci_id,
                                               name=proj.name,
                                               opcode='update',
                                               reason=e.args[0])
            else:
                # Request is sent successfully, update the database.
                self.update_project_info_cache(proj.id,
                                               dci_id=proj.dci_id,
                                               name=proj.name,
                                               opcode='update')
                LOG.debug('Success on failure recovery update for '
                          'project %(name)s', {'name': proj.name})

        # 2. Try failure recovery for create network.
        nets = self.get_all_networks()
        for net in nets:
            if (net.result == constants.CREATE_FAIL
                    and net.source.lower() == 'openstack'):
                net_id = net.network_id
                try:
                    subnets = self.neutron_event.nclient.list_subnets(
                        network_id=net_id).get('subnets')
                except dexc.ConnectionFailed:
                    LOG.exception('Failed to get subnets list.')
                    continue

                for subnet in subnets:
                    tenant_name = self.get_project_name(subnet['tenant_id'])
                    snet = utils.Dict2Obj(subnet)
                    try:
                        # Check if config_profile is not NULL.
                        if not net.config_profile:
                            cfgp, fwd_mod = (
                                self.dcnm_client.
                                get_config_profile_for_network(net.name))
                            net.config_profile = cfgp
                            net.fwd_mod = fwd_mod
                        if (self._lbMgr and
                                self._lbMgr.lb_is_internal_nwk(net.name)):
                            net_in_dict = self.network.get(net_id)
                            self._lbMgr.lb_create_net_dcnm(tenant_name,
                                                           net.name,
                                                           net_in_dict,
                                                           subnet)
                        else:
                            self.dcnm_client.create_network(tenant_name,
                                                            net, snet, None,
                                                            self.dcnm_dhcp)
                    except dexc.DfaClientRequestFailed as exc:
                        # Still is failure, only log the error.
                        LOG.error('Failed to create network %(net)s.',
                                  {'net': net.name})
                        self.network[net_id].update({'reason': exc.args[0]})
                    else:
                        # Request is sent to DCNM, update the database
                        params = dict(
                            columns=dict(config_profile=net.config_profile,
                                         fwd_mod=net.fwd_mod,
                                         result=constants.RESULT_SUCCESS))
                        self.update_network(net_id, **params)
                        self.network[net_id].update({'reason': 'SUCCESS'})
                        LOG.debug("Success on failure recovery to create "
                                  "%(net)s", {'net': net.name})

        # 3. Try Failure recovery for VM create and delete.
        instances = self.get_vms()
        for vm in instances:
            if constants.IP_DHCP_WAIT in vm.ip:
                ipaddr = vm.ip.replace(constants.IP_DHCP_WAIT, '')
            else:
                ipaddr = vm.ip
            vm_info = dict(status=vm.status,
                           vm_mac=vm.mac,
                           segmentation_id=vm.segmentation_id,
                           host=vm.host,
                           port_uuid=vm.port_id,
                           net_uuid=vm.network_id,
                           oui=dict(ip_addr=ipaddr,
                                    vm_name=vm.name,
                                    vm_uuid=vm.instance_id,
                                    gw_mac=vm.gw_mac,
                                    fwd_mod=vm.fwd_mod,
                                    oui_id='cisco'))
            if vm.status == constants.MIGRATE:
                self._failure_vms_migration(vm, vm_info)
            else:
                self._failure_vms(vm, vm_info)

        # 4. Try failure recovery for delete network.
        for net in nets:
            if (net.result == constants.DELETE_FAIL
                    and net.source.lower() == 'openstack'):
                net_id = net.network_id
                segid = net.segmentation_id
                tenant_name = self.get_project_name(net.tenant_id)
                try:
                    self.dcnm_client.delete_network(tenant_name, net)
                except dexc.DfaClientRequestFailed as exc:
                    # Still is failure, only log the error.
                    LOG.error('Failed to delete network %(net)s.',
                              {'net': net.name})
                    self.network[net_id].update({'reason': exc.args[0]})
                else:
                    # Request is sent to DCNM, delete the entry
                    # from database and return the segmentation id to the
                    # pool.
                    self.delete_network_db(net_id)
                    del self.network[net_id]
                    self.seg_drvr.release_segmentation_id(segid)
                    LOG.debug("Success on failure recovery to deleted "
                              "%(net)s", {'net': net.name})

        # 5. Try failure recovery for delete project.
        projs = self.get_fialed_projects_entries(constants.DELETE_FAIL)
        for proj in projs:
            LOG.debug("Failure recovery for project %(name)s.", (
                {'name': proj.name}))
            # Try to delete the project in DCNM
            try:
                self.dcnm_client.delete_project(proj.name,
                                                self.cfg.dcnm.
                                                default_partition_name)
            except dexc.DfaClientRequestFailed as e:
                # Failed to delete project in DCNM.
                # Save the info and mark it as failure and retry it later.
                LOG.error("Failure recovery is failed to delete "
                          " %(project)s on DCNM : %(reason)s",
                          {'project': proj.name, 'reason': str(e)})
                self.update_project_info_cache(proj.id, opcode='delete',
                                               reason=e.args[0])
            else:
                # Delete was successful, now update the database.
                self.update_project_info_cache(proj.id, opcode='delete')
                LOG.debug("Success on failure recovery to deleted "
                          "%(project)s", {'project': proj.name})

        # 6. Do failure recovery for Firewall service
        self.fw_retry_failures()

        # 6. DHCP port consistency check for HA.
        if self.need_dhcp_check():
            nets = self.get_all_networks()
            for net in nets:
                net_id = net.network_id
                LOG.info("dhcp consistency check for net id %s" % net_id)
                self.correct_dhcp_ports(net_id)
            self.decrement_dhcp_check()
        LOG.info("Finished failure_recovery.")
