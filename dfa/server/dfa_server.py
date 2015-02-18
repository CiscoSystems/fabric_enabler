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


"""This is the DFA enabler server module which is respnsible for processing
neutron, keystone and DCNM events. Also interacting with DFA enabler agent
module for port events.
"""

import eventlet
eventlet.monkey_patch()
import json
import os
import paramiko
import platform
import Queue
import re
from six import moves
import sys
import time


from dfa.common import config
from dfa.common import constants
from dfa.common import dfa_exceptions as dexc
from dfa.common import dfa_logger as logging
from dfa.common import rpc
from dfa.common import utils
from dfa.db import dfa_db_models as dfa_dbm
from dfa.server import cisco_dfa_rest as cdr
from dfa.server import dfa_events_handler as deh
from dfa.server import dfa_fail_recovery as dfr
from dfa.server import dfa_instance_api as dfa_inst
from dfa.server import dfa_listen_dcnm as dfa_dcnm


LOG = logging.getLogger(__name__)


class RpcCallBacks(object):

    """RPC call back methods."""

    def __init__(self, obj):
        self.obj = obj

    def heartbeat(self, context, msg):
        """Process heartbeat message from agents on compute nodes."""

        args = json.loads(msg)
        when = args.get('when')
        agent = args.get('agent')
        # The configurations in here, only used once when creating entry
        # for an agent in DB for the first time.
        configurations = {'uplink': ''}
        LOG.debug('heartbeat received: %(time)s - %(agent)s', (
                  {'time': when, 'agent': agent}))

        if self.obj.neutron_event:
            self.obj.neutron_event.create_rpc_client(agent)
        # Other option is to add the event to the queue for processig it later.

        self.obj.update_agent_status(agent, when)

        # Update the agents database.
        agent_info = dict(timestamp=utils.utc_time(when), host=agent,
                          config=json.dumps(configurations))
        self.obj.update_agent_db(agent_info)

    def request_uplink_info(self, context, agent):
        """Process uplink message from an agent."""

        LOG.debug('request_uplink_info from %(agent)s', {'agent': agent})

        # Add the request into queue for processing.
        event_type = 'agent.request.uplink'
        payload = {'agent': agent}
        timestamp = time.ctime()
        data = (event_type, payload)
        pri = self.obj.PRI_LOW_START+1
        self.obj.pqueue.put((pri, timestamp, data))
        LOG.debug('Added request uplink info into queue.')

        return 0

    def request_vms_info(self, context, agent):
        """Process request for VM information from an agent."""

        LOG.debug('request_vms_info from %(agent)s', {'agent': agent})

        # Add the request into queue for processing.
        event_type = 'agent.request.vms'
        payload = {'agent': agent}
        timestamp = time.ctime()
        data = (event_type, payload)
        pri = self.obj.PRI_LOW_START+2
        self.obj.pqueue.put((pri, timestamp, data))
        LOG.debug('Added request VMs info into queue.')

        return 0

    def save_uplink(self, context, msg):
        args = json.loads(msg)
        agent = args.get('agent')
        uplink = args.get('uplink')
        veth_intf = args.get('veth_intf')
        configs = self.obj.get_agent_configurations(agent)
        if configs:
            # Update the agents database.
            new_config = json.loads(configs)
            new_config.update({'uplink': uplink, 'veth_intf': veth_intf})
            if self.obj.update_agent_configurations(agent,
                                                    json.dumps(new_config)):
                LOG.debug('Saved %(uplink)s %(veth)s for %(agent)s in DB.', (
                          {'uplink': uplink, 'veth': veth_intf,
                           'agent': agent}))
                if uplink:
                    # Request for VMs if uplink is detected.
                    self.request_vms_info(context, agent)
                return True
            else:
                return False

    def set_static_ip_address(self, context, msg):
        """Process request for setting rules in iptables.

        In cases that static ip address is assigned for a VM, it is needed
        to update the iptables rule for that address.
        """
        args = json.loads(msg)
        macaddr = args.get('mac')
        ipaddr = args.get('ip')
        LOG.debug('set_static_ip_address received: %(mac)s %(ip)s', (
                  {'mac': macaddr, 'ip': ipaddr}))

        # Add the request into queue for processing.
        event_type = 'cli.static_ip.set'
        payload = {'mac': macaddr, 'ip': ipaddr}
        timestamp = time.ctime()
        data = (event_type, payload)
        pri = self.obj.PRI_LOW_START
        self.obj.pqueue.put((pri, timestamp, data))
        LOG.debug('Added request to add static ip into queue.')

        return 0

    def update_vm_result(self, context, msg):
        """Update VM's result field in the DB.

        The result reflects the success of failure of operation when an
        agent processes the vm info.
        """
        args = json.loads(msg)
        agent = context.get('agent')
        port_id = args.get('port_uuid')
        result = args.get('result')
        LOG.debug('update_vm_result received from %(agent)s: '
                  '%(port_id)s %(result)s' % ({'agent': agent,
                                               'port_id': port_id,
                                               'result': result}))

        # Add the request into queue for processing.
        event_type = 'agent.vm_result.update'
        payload = {'port_id': port_id, 'result': result}
        timestamp = time.ctime()
        data = (event_type, payload)
        # TODO use value defined in constants
        pri = self.obj.PRI_LOW_START+10
        self.obj.pqueue.put((pri, timestamp, data))
        LOG.debug('Added request vm result update into queue.')

        return 0


class DfaServer(dfr.DfaFailureRecovery, dfa_dbm.DfaDBMixin):

    """Process keystone and neutron events.

    The supported events project and network create/delete/update.
    For each events, data will be provided and sent to DCNM.
    """

    def __init__(self, cfg):
        super(DfaServer, self).__init__(cfg)
        self._cfg = cfg
        self._host = platform.node()
        self.server = None
        self.events = {
            'identity.project.created': self.project_create_event,
            'identity.project.deleted': self.project_delete_event,
            'identity.project.updated': self.project_update_event,
            'subnet.create.end': self.subnet_create_event,
            'network.create.end': self.network_create_event,
            'network.delete.end': self.network_delete_event,
            'port.create.end': self.port_create_event,
            'port.update.end': self.port_update_event,
            'port.delete.end': self.port_delete_event,
            'dcnm.network.create': self.dcnm_network_create_event,
            'dcnm.network.delete': self.dcnm_network_delete_event,
            'server.failure.recovery': self.failure_recovery,
            'agent.request.vms': self.request_vms_info,
            'agent.request.uplink': self.request_uplink_info,
            'cli.static_ip.set': self.set_static_ip_address,
            'agent.vm_result.update': self.vm_result_update,
        }
        self.project_info_cache = {}
        self.network = {}
        self.subnet = {}
        self.port = {}
        self.dfa_threads = []
        self.agents_status_table = {}

        # Create segmentation id pool.
        seg_id_min = int(cfg.dcnm.segmentation_id_min)
        seg_id_max = int(cfg.dcnm.segmentation_id_max)
        self.segmentation_pool = set(moves.xrange(seg_id_min, seg_id_max + 1))

        # Create queue for exception returned by a thread.
        self._excpq = Queue.Queue()

        # Loading project and network info and updating the cache.
        self._load_project_info_cache()
        self._load_network_info()

        # Create priority queue for events.
        self.pqueue = Queue.PriorityQueue()
        self.PRI_HIGH_START = 10
        self.PRI_MEDIUM_START = 20
        self.PRI_LOW_START = 30

        self._gateway_mac = cfg.dcnm.gateway_mac
        dcnm_ip = cfg.dcnm.dcnm_ip
        dcnm_amqp_user = cfg.dcnm.dcnm_amqp_user
        dcnm_password = cfg.dcnm.dcnm_password
        self.dcnm_client = cdr.DFARESTClient(cfg)

        self.keystone_event = deh.EventsHandler('keystone', self.pqueue,
                                                self.PRI_HIGH_START,
                                                self.PRI_LOW_START)

        self.neutron_event = deh.EventsHandler('neutron', self.pqueue,
                                               self.PRI_MEDIUM_START,
                                               self.PRI_MEDIUM_START + 5)

        self.dcnm_event = dfa_dcnm.DCNMListener(name='dcnm', ip=dcnm_ip,
                                                user=dcnm_amqp_user,
                                                password=dcnm_password,
                                                pqueue=self.pqueue,
                                                c_pri=self.PRI_MEDIUM_START+1,
                                                d_pri=self.PRI_MEDIUM_START+6)

        self._inst_api = dfa_inst.DFAInstanceAPI()

        # RPC setup
        self.ser_q = constants.DFA_SERVER_QUEUE
        self._setup_rpc()

    @property
    def cfg(self):
        return self._cfg

    @property
    def neutronclient(self):
        return self.neutron_event.nclient

    def _load_project_info_cache(self):
        projs = self.get_all_projects()
        for proj in projs:
            self.project_info_cache[proj.id] = dict(name=proj.name,
                                                    dci_id=proj.dci_id)

        LOG.info('Project info cache: %s', self.project_info_cache)

    def get_project_name(self, tenant_id):
        proj = self.project_info_cache.get(tenant_id)
        return None if not proj else proj.get('name')

    def get_dci_id(self, tenant_id):
        proj = self.project_info_cache.get(tenant_id)
        return None if not proj else proj.get('dci_id')

    def _load_network_info(self):
        nets = self.get_all_networks()
        for net in nets:
            self.network[net.network_id] = {}
            self.network[net.network_id]['segmentation_id'] = (
                net.segmentation_id)
            self.network[net.network_id]['config_profile'] = (
                net.config_profile)
            self.network[net.network_id]['fwd_mod'] = net.fwd_mod
            self.network[net.network_id]['tenant_id'] = net.tenant_id
            self.network[net.network_id]['name'] = net.name
            self.network[net.network_id]['id'] = net.network_id

            # Remove the used segmentation id from the pool.
            if net.segmentation_id in self.segmentation_pool:
                self.segmentation_pool.remove(net.segmentation_id)

        LOG.info('Network info cache: %s', self.network)

    def _setup_rpc(self):
        """Setup RPC server for dfa server."""

        endpoints = RpcCallBacks(self)
        self.server = rpc.DfaRpcServer(self.ser_q, self._host, endpoints)

    def start_rpc(self):
        self.server.start()
        LOG.debug('starting RPC server on the dfa server.')
        self.server.wait()

    def stop_rpc(self):
        self.server.stop()

    def update_agent_status(self, agent, ts):
        self.agents_status_table[agent] = ts

    def update_project_info_cache(self, pid, dci_id=None,
                                  name=None, opcode='add',
                                  result=constants.RESULT_SUCCESS):
        if 'add' in opcode:
            self.project_info_cache[pid] = dict(name=name, dci_id=dci_id)
            self.add_project_db(pid, name, dci_id, result)
        if 'update' in opcode:
            self.project_info_cache[pid] = dict(name=name, dci_id=dci_id)
            self.update_project_entry(pid, dci_id, result)
        if 'delete' in opcode:
            if pid in self.project_info_cache:
                if result == constants.DELETE_FAIL:
                    # Update the database entry with failure in the result.
                    # The entry should be deleted later in periodic task.
                    self.update_project_entry(pid, dci_id, result)
                else:
                    self.project_info_cache.pop(pid)
                    self.del_project_db(pid)

    def _get_dci_id_and_proj_name(self, proj_name):
        # dci_id can be embedded in the partition name, name:dci_id:27
        dciid_key = ':dci_id:'
        try:
            dci_index = proj_name.index(dciid_key)
        except ValueError:
            # There is no dci_id in the project name
            return proj_name, None

        proj_fields = proj_name[dci_index+1:].split(':')
        if len(proj_fields) == 2:
            if (proj_fields[1].isdigit()
                    and proj_fields[0] == dciid_key[1:-1]):
                LOG.debug('project name %(proj)s DCI_ID %(dci_id)s.', (
                    {'proj': proj_name[0:dci_index],
                     'dci_id': proj_fields[1]}))
                return proj_name[0:dci_index], proj_fields[1]

    def project_create_event(self, proj_info):
        """Create project."""

        LOG.debug("Processing create %(proj)s event.", {'proj': proj_info})
        proj_id = proj_info.get('resource_info')
        try:
            proj = self.keystone_event._service.projects.get(proj_id)
        except:
            LOG.error("Failed to find project %s.", proj_id)
            return

        # In the project name, dci_id may be included. Check if this is the
        # case and extact the dci_id from the name, and provide dci_id when
        # creating the project.
        proj_name, dci_id = self._get_dci_id_and_proj_name(proj.name)
        # The default partition name is 'os' (i.e. openstack) which reflects
        # it is created by openstack.
        part_name = self.cfg.dcnm.default_partition_name
        if len(':'.join((proj_name, part_name))) > 32:
            LOG.error('Invalid project name length: %s. The lenght of org:part'
                      ' name is greater than 32' %
                      len(':'.join((proj_name, part_name))))
            return
        try:
            self.dcnm_client.create_project(proj_name, part_name, dci_id,
                                            proj.description)
        except dexc.DfaClientRequestFailed:
            # Failed to send create project in DCNM.
            # Save the info and mark it as failure and retry it later.
            self.update_project_info_cache(proj_id, name=proj_name,
                                           dci_id=dci_id,
                                           result=constants.CREATE_FAIL)
            LOG.error("Failed to create project %s on DCNM.", proj_name)
        else:
            self.update_project_info_cache(proj_id, name=proj_name,
                                           dci_id=dci_id)
            LOG.debug('project %(name)s %(dci)s %(desc)s', (
                {'name': proj_name, 'dci': dci_id, 'desc': proj.description}))

    def project_update_event(self, proj_info):
        """Process project update event.

        There could be change in project name. DCNM doesn't allow change in
        project (a.k.a tenant). This event may be received for the DCI update.
        If the change is for DCI, update the DCI portion of the project name
        and send the update event to the DCNM.
        """

        LOG.debug("Processing project_update_event %(proj)s.",
                  {'proj': proj_info})
        proj_id = proj_info.get('resource_info')
        try:
            proj = self.keystone_event._service.projects.get(proj_id)
        except:
            LOG.error("Failed to find project %s.", proj_id)
            return

        new_proj_name, new_dci_id, = self._get_dci_id_and_proj_name(proj.name)
        # Check if project name and dci_id are the same, there is no change.
        orig_proj_name = self.get_project_name(proj_id)
        orig_dci_id = self.get_dci_id(proj_id)
        if orig_proj_name == new_proj_name and new_dci_id == orig_dci_id:
            # This is an invalid update event.
            LOG.warning('Project update event for %(proj)s is received without'
                        ' changing in the project name: %(orig_proj)s'
                        'Ignoring the event.', (
                            {'proj': proj_id, 'orig_proj': orig_proj_name}))
            return

        if orig_proj_name != new_proj_name:
            # Project has new name and in DCNM the name of project cannot be
            # modified. It is an invalid update. Do not process the event.
            LOG.debug('Update request cannot be processed as name of project'
                      ' is changed: %(proj)s %(orig_name)s %(orig_dci)s to '
                      '%(new_name)s %(new_dci)s.', (
                          {'proj': proj_id, 'orig_name': orig_proj_name,
                           'orig_dci': orig_dci_id, 'new_name': new_proj_name,
                           'new_dci': new_dci_id}))
            return

        # Valid update request.
        LOG.debug('Changing project DCI id for %(proj)s from %(orig_dci)s to '
                  '%(new_dci)s.', ({'proj': proj_id,
                                    'orig_dci': orig_dci_id,
                                    'new_dci': new_dci_id}))

        try:
            self.dcnm_client.update_project(new_proj_name,
                                            self.cfg.dcnm.
                                            default_partition_name, new_dci_id)
        except dexc.DfaClientRequestFailed:
            # Failed to update project in DCNM.
            # Save the info and mark it as failure and retry it later.
            LOG.error("Failed to update project %s on DCNM.", new_proj_name)
            self.update_project_info_cache(proj_id, name=new_proj_name,
                                           dci_id=new_dci_id,
                                           opcode='update',
                                           result=constants.UPDATE_FAIL)
        else:
            self.update_project_info_cache(proj_id, name=new_proj_name,
                                           dci_id=new_dci_id,
                                           opcode='update')
            LOG.debug('Updated project %(proj)s %(name)s.', (
                {'proj': proj_id, 'name': proj.name}))

    def project_delete_event(self, proj_info):
        """Process project delete event."""

        LOG.debug("Processing project_delete_event...")
        proj_id = proj_info.get('resource_info')
        proj_name = self.get_project_name(proj_id)
        if proj_name:
            try:
                self.dcnm_client.delete_project(proj_name,
                                                self.cfg.dcnm.
                                                default_partition_name)
            except dexc.DfaClientRequestFailed:
                # Failed to delete project in DCNM.
                # Save the info and mark it as failure and retry it later.
                LOG.error("Failed to create project %s on DCNM.", proj_name)
                self.update_project_info_cache(proj_id, name=proj_name,
                                               opcode='delete',
                                               result=constants.DELETE_FAIL)
            else:
                self.update_project_info_cache(proj_id, opcode='delete')
                LOG.debug('Deleted project:%s', proj_name)

    def subnet_create_event(self, subnet_info):
        """Process subnet create event."""

        subnet = subnet_info.get('subnet')
        if subnet:
            self.create_subnet(subnet)
        else:
            # Check whether request is for subnets.
            subnets = subnet_info.get('subnets')
            if subnets:
                for subnet in subnets:
                    self.create_subnet(subnet)

    def create_subnet(self, snet):
        """Create subnet."""

        snet_id = snet.get('id')
        if snet_id not in self.subnet:
            self.subnet[snet_id] = {}
            self.subnet[snet_id].update(snet)

        net = self.network.get(self.subnet[snet_id].get('network_id'))

        # Check if the network is created by DCNM.
        query_net = self.get_network(net.get('id'))
        if query_net and query_net.source.lower() == 'dcnm':
            # The network is created by DCNM.
            # No need to process this event.
            LOG.info('create_subnet: network %(name)s was created by DCNM. '
                     'Ignoring processing the event.', (
                         {'name': query_net.name}))
            return

        tenant_name = self.get_project_name(snet['tenant_id'])
        subnet = utils.dict_to_obj(snet)
        dcnm_net = utils.dict_to_obj(net)
        if not tenant_name:
            LOG.error('Project %(tenant_id)s does not exist.', (
                      {'tenant_id': subnet.tenant_id}))
            self.update_network_db(dcnm_net.id, constants.CREATE_FAIL)
            return

        try:
            self.dcnm_client.create_network(tenant_name, dcnm_net, subnet)
        except dexc.DfaClientRequestFailed:
            emsg = 'Failed to create network %(net)s.'
            LOG.exception(emsg, {'net': dcnm_net.name})
            # Update network database with failure result.
            self.update_network_db(dcnm_net.id, constants.CREATE_FAIL)

    def _get_segmentation_id(self, segid):
        """Allocate segmentation id."""

        try:
            newseg = (segid, self.segmentation_pool.remove(segid)
                      if segid and segid in self.segmentation_pool else
                      self.segmentation_pool.pop())
            return newseg[0] if newseg[0] else newseg[1]
        except KeyError:
            LOG.exception('Error: Segmentation id pool is empty')
            return 0

    def network_create_event(self, network_info):
        """Process network create event.

        Save the network inforamtion in the database.
        """
        net = network_info['network']
        net_id = net['id']
        self.network[net_id] = {}
        self.network[net_id].update(net)

        net_name = net.get('name')
        tenant_id = net.get('tenant_id')

        # Extract segmentation_id from the network name
        try:
            net_ext_name = self.cfg.dcnm.dcnm_net_ext
            nobj = re.search(net_ext_name, net_name)
            seg_id = int((net_name[nobj.start(0)+len(net_ext_name)-1:]
                         if nobj else None))
        except (IndexError, TypeError, ValueError):
            seg_id = None

        # Check if network is already created.
        query_net = self.get_network_by_segid(seg_id) if seg_id else None
        if query_net:
            # The network is already created no need to process the event.
            if query_net.source.lower() == 'dcnm':
                # DCNM created the network. Only update network id in database.
                prev_id = query_net.network_id
                params = dict(columns=dict(network_id=net_id))
                self.update_network(prev_id, **params)

                # Update the network cache.
                prev_info = self.network.pop(prev_id)
                prev_info['id'] = net_id
                self.network[net_id] = prev_info

                # Update the network name. After extracting the segmentation_id
                # no need to keep it in the name. Removing it and update
                # the network.
                updated_net_name = net_name[:nobj.start(0)+len(net_ext_name)-1]
                try:
                    body = {'network': {'name': updated_net_name, }}
                    dcnm_net = self.neutronclient.update_network(
                        net_id, body=body).get('network')
                except Exception as e:  # TODO get the proper exception
                    LOG.exception('Failed to update network '
                                  '%(network)s. Reason %(err)s.' % (
                                      {'network': dcnm_net, 'err': str(e)}))
                    return

            LOG.info('network_create_event: network %(name)s was created '
                     'by %(source)s. Ignoring processing the event.' % (
                         {'name': net_name, 'source': 'dcnm'}))
            return

        # Check if project (i.e. tenant) exist.
        tenant_name = self.get_project_name(tenant_id)
        if not tenant_name:
            LOG.error('Failed to create network %(name)s. Project '
                      '%(tenant_id)s does not exist.' % (
                          {'name': net_name, 'tenant_id': tenant_id}))
            return

        pseg_id = self.network[net_id].get('provider:segmentation_id')
        seg_id = self._get_segmentation_id(pseg_id)
        self.network[net_id]['segmentation_id'] = seg_id
        try:
            cfgp, fwd_mod = self.dcnm_client.get_config_profile_for_network(
                net.get('name'))
            self.network[net_id]['config_profile'] = cfgp
            self.network[net_id]['fwd_mod'] = fwd_mod
            self.add_network_db(net_id, self.network[net_id],
                                'openstack',
                                constants.RESULT_SUCCESS)
            LOG.debug('network_create_event: network=%s', self.network)
        except dexc.DfaClientRequestFailed:
            # Fail to get config profile from DCNM.
            # Save the network info with failure result and send the request
            # to DCNM later.
            self.add_network_db(net_id, self.network[net_id], 'openstack',
                                constants.CREATE_FAIL)
            LOG.error('Failed to create network=%s.', self.network)

    def network_delete_event(self, network_info):
        """Process network delete event."""

        net_id = network_info['network_id']
        if net_id not in self.network:
            LOG.error('network_delete_event: net_id %s does not exist.',
                      net_id)
            return

        segid = self.network[net_id].get('segmentation_id')
        tenant_id = self.network[net_id].get('tenant_id')
        tenant_name = self.get_project_name(tenant_id)
        net = utils.dict_to_obj(self.network[net_id])
        if not tenant_name:
            LOG.error('Project %(tenant_id)s does not exist.', (
                      {'tenant_id': tenant_id}))
            self.update_network_db(net.id, constants.DELETE_FAIL)
            return

        try:
            self.dcnm_client.delete_network(tenant_name, net)
            # Put back the segmentation id into the pool.
            self.segmentation_pool.add(segid)

            # Remove entry from database and cache.
            self.delete_network_db(net_id)
            del self.network[net_id]
            snets = [(k) for k in self.subnet if self.subnet[k] == net_id]
            [self.subnet.pop(s) for s in snets]
        except dexc.DfaClientRequestFailed:
            emsg = ('Failed to create network %(net)s.')
            LOG.error(emsg, {'net': net.name})
            self.update_network_db(net_id, constants.DELETE_FAIL)

    def dcnm_network_create_event(self, network_info):
        """Process network create event from DCNM."""

        # 1. Add network info to database before sending request to
        # neutron to create the network.
        # Check if network is already created.
        pre_seg_id = network_info.get('segmentation_id')
        pre_project_name = network_info.get('project_name')
        pre_partition_name = network_info.get('partition_name')
        if not pre_seg_id or not pre_partition_name or not pre_project_name:
            LOG.error('Invalid network event: %s', (network_info))
            return

        # Check if partition name is the one that openstack created.
        if pre_partition_name != self.cfg.dcnm.default_partition_name:
            LOG.error('Failed to create network. Partition %(part)s is not '
                      '%(os_part)s which is created by openstack.', {
                          'part': pre_partition_name,
                          'os_part': self.cfg.dcnm.default_partition_name})
            return

        query_net = self.get_network_by_segid(pre_seg_id)
        if query_net:
            # The network is already created no need to process the event.
            LOG.info('dcnm_network_create_event: network %(name)s was '
                     'created. Ignoring processing the event.' % (
                         {'name': query_net.name}))
            return

        dcnm_net_info = self.dcnm_client.get_network(pre_project_name,
                                                     pre_seg_id)
        if not dcnm_net_info:
            LOG.info('No network details for %(org)s and %(segid)s' % (
                {'org': pre_project_name, 'segid': pre_seg_id}))
            return

        net_id = utils.get_uuid()
        pseg_id = dcnm_net_info.get('segmentId')
        seg_id = self._get_segmentation_id(pseg_id)
        cfgp = dcnm_net_info.get('profileName')
        net_name = dcnm_net_info.get('networkName')
        fwd_mod = self.dcnm_client.config_profile_fwding_mode_get(cfgp)
        tenant_name = dcnm_net_info.get('organizationName')
        tenant_id = self.get_project_id(tenant_name)

        # Get the subnet details.
        subnet = dcnm_net_info.get('dhcpScope')
        if not subnet:
            # The dhcpScope is not provided. Calculating the cidr based on
            # gateway ip and netmask.
            gw_addr = dcnm_net_info.get('gateway')
            net_mask = dcnm_net_info.get('netmaskLength')
            cidr = utils.make_cidr(gw_addr, net_mask)
            if not cidr:
                LOG.error('Failed to create network: '
                          'cidr is None for %(gw)s %(mask)s',
                          ({'gw': gw_addr, 'mask': net_mask}))
                return
            subnet = dict(gateway=gw_addr, subnet=cidr)

        # Check if parameters are provided.
        if not (net_name and tenant_id and seg_id and subnet):
            LOG.error('Invalid value: network %(name)s tenant_id '
                      '%(tenant_id)s segmentation_id %(seg_id)s '
                      'subnet %(subnet)s.' % ({'name': net_name,
                                               'tenant_id': tenant_id,
                                               'seg_id': seg_id,
                                               'subnet': subnet}))
            return

        # Update network cache and add the network to the database.
        net_ext_name = self.cfg.dcnm.dcnm_net_ext
        self.network[net_id] = dict(segmentation_id=seg_id,
                                    config_profile=cfgp,
                                    fwd_mod=fwd_mod,
                                    tenant_id=tenant_id,
                                    name=net_name + net_ext_name,
                                    id=net_id,
                                    source='DCNM')
        self.add_network_db(net_id, self.network[net_id], 'DCNM',
                            constants.RESULT_SUCCESS)

        # 2. Send network create request to neutron
        try:
            # With create_network (called below), the same request comes as
            # notification and it will be processed in the
            # create_network_event. The request should not be processed as it
            # is already processed here.
            # The only way to decide whether it is for a new network or not is
            # the segmentation_id (DCNM does not have uuid for network) which
            # is unique. For that reason it is needed to send segmentation_id
            # when creating network in openstack.
            # Moreover, we are using network_type=local and for that reason
            # provider:segmentation_id cannot be added as parameter when
            # creating network. One solution is to embed segmentation_id in the
            # network name. Then, when processing the notification, if the
            # request is from DCNM, the segmentation_id will be extracted from
            # network name. With that create_network_event can decide to
            # process or deny an event.
            updated_net_name = net_name + net_ext_name + str(seg_id)
            body = {'network': {'name': updated_net_name,
                                'tenant_id': tenant_id,
                                'admin_state_up': True}}
            dcnm_net = self.neutronclient.create_network(
                body=body).get('network')
            net_id = dcnm_net.get('id')

        except Exception as e:  # TODO get the proper exception
            # Failed to create network, do clean up.
            # Remove the entry from database and local cache.
            del self.network[net_id]
            self.delete_network_db(net_id)
            LOG.exception('dcnm_network_create_event: Failed to create '
                          '%(network)s. Reason %(err)s.' % (
                              {'network': body, 'err': str(e)}))
            return

        LOG.debug('dcnm_network_create_event: Created network %(network)s' % (
            body))

        # 3. Send subnet create request to neutron.
        pool = subnet.get('ipRange')
        allocation_pools = []
        if pool:
            iprange = ["{'start': '%s', 'end': '%s'}" % (
                p.split('-')[0], p.split('-')[1]) for p in pool.split(',')]
            [allocation_pools.append(eval(ip)) for ip in iprange]

        try:
            body = {'subnet': {'cidr': subnet.get('subnet'),
                               'gateway_ip': subnet.get('gateway'),
                               'ip_version': 4,
                               'network_id': net_id,
                               'tenant_id': tenant_id,
                               'enable_dhcp': False,
                               'allocation_pools': allocation_pools, }}
            # Send requenst to create subnet in neutron.
            LOG.debug('Creating subnet %(subnet)s for DCNM request.' % body)
            dcnm_subnet = self.neutronclient.create_subnet(
                body=body).get('subnet')
            subnet_id = dcnm_subnet.get('id')
            # Update subnet cache.
            self.subnet[subnet_id] = {}
            self.subnet[subnet_id].update(body.get('subnet'))
        except Exception as e:  # TODO get the proper exception
            # Failed to create network, do clean up if necessary.
            LOG.exception('Failed to create subnet %(subnet)s for DCNM '
                          'request. Error %(err)s' % (
                              {'subnet': body['subnet'], 'err': str(e)}))

        LOG.debug('dcnm_network_create_event: Created subnet %(subnet)s' % (
            body))

    def dcnm_network_delete_event(self, network_info):
        """Process network delete event from DCNM."""
        seg_id = network_info.get('segmentation_id')
        if not seg_id:
            LOG.error('Failed to delete network. Invalid network info %s.' %
                      network_info)
        query_net = self.get_network_by_segid(seg_id)
        if not query_net:
            LOG.info('dcnm_network_delete_event: network %(segid)s does not '
                     'exist.' % ({'segid': seg_id}))
            return
        # Send network delete request to neutron
        try:
            del_net = self.network.pop(query_net.network_id)
            self.neutronclient.delete_network(query_net.network_id)
            self.delete_network_db(query_net.network_id)
        except Exception as e:  # TODO get the proper exception
            # Failed to delete network.
            # Put back the entry to the local cache???
            self.network[query_net.network_id] = del_net
            LOG.exception('dcnm_network_delete_event: Failed to delete '
                          '%(network)s. Reason %(err)s.' % (
                              {'network': query_net.name, 'err': str(e)}))

    def _make_vm_info(self, port, status):
        port_id = port.get('id')
        device_id = port.get('device_id').replace('-', '')
        tenant_id = port.get('tenant_id')
        net_id = port.get('network_id')
        inst_name = self._inst_api.get_instance_for_uuid(device_id,
                                                         tenant_id)
        segid = (net_id in self.network and
                 self.network[net_id].get('segmentation_id')) or 0
        fwd_mod = (net_id in self.network and
                   self.network[net_id].get('fwd_mod')) or 'anycast-gateway'
        gw_mac = self._gateway_mac if fwd_mod == 'proxy-gateway' else None
        vm_mac = port.get('mac_address')

        vm_info = dict(status=status,
                       vm_mac=vm_mac,
                       segmentation_id=segid,
                       host=port.get('binding:host_id'),
                       port_uuid=port_id,
                       net_uuid=port.get('network_id'),
                       oui=dict(ip_addr='0.0.0.0',
                                vm_name=inst_name,
                                vm_uuid=device_id,
                                gw_mac=gw_mac,
                                fwd_mod=fwd_mod,
                                oui_id='cisco'))
        return vm_info

    def port_create_event(self, port_info):
        port = port_info.get('port')
        if not port:
            return

        vm_info = self._make_vm_info(port, 'up')
        port_id = port.get('id')
        self.port[port_id] = vm_info
        LOG.debug("port_create_event : %s" % vm_info)

        if (not port.get('binding:host_id') and
                port.get('binding:vif_type').lower() == 'unbound'):
            # A port is created without binding host, vif_type,...
            # Keep the info in the database.
            self.add_vms_db(vm_info, constants.RESULT_SUCCESS)

            LOG.debug('Port %s created with no binding host and vif_type.')
            return

        try:
            self.neutron_event.send_vm_info(str(vm_info.get('host')),
                                            str(vm_info))
        except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
            # Failed to send info to the agent. Keep the data in the
            # database as failure to send it later.
            self.add_vms_db(vm_info, constants.CREATE_FAIL)
            LOG.error('Failed to send VM info to agent.')
        else:
            self.add_vms_db(vm_info, constants.RESULT_SUCCESS)

    def port_update_event(self, port_info):
        port = port_info.get('port')
        if not port:
            return

        bhost_id = port.get('binding:host_id')
        if not bhost_id:
            return

        port_id = port.get('id')
        LOG.debug("port_update_event for %(port)s %(host)s." %
                  {'port': port_id, 'host': bhost_id})

        # Get the port info from DB and check if the instance is migrated.
        vm = self.get_vm(port_id)
        if not vm:
            LOG.error("port_update_event: port %s does not exist." % port_id)
            return

        if vm.host == bhost_id:
            # Port update is received without binding host change.
            LOG.info('Port %(port)s update event is received but host %(host)s'
                     ' is the same.' % {'port': port_id, 'host': vm.host})
            return

        vm_info = self._make_vm_info(port, 'up')
        self.port[port_id] = vm_info
        LOG.debug("port_update_event : %s" % vm_info)
        if not vm.host and bhost_id:
            # Port updated event received as a result of binding existing port
            # to a VM.
            try:
                self.neutron_event.send_vm_info(str(bhost_id),
                                                str(vm_info))
            except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
                # Failed to send info to the agent. Keep the data in the
                # database as failure to send it later.
                LOG.error('Failed to send VM info to agent %s.' % bhost_id)
                params = dict(columns=dict(host=bhost_id,
                                           instance_id=vm_info.get('oui').
                                           get('vm_uuid'),
                                           name=vm_info.get('oui').
                                           get('vm_name'),
                                           result=constants.CREATE_FAIL))
                self.update_vm_db(vm.port_id, **params)
            else:
                # Update the database with info.
                params = dict(columns=dict(host=bhost_id,
                                           instance_id=vm_info.get('oui').
                                           get('vm_uuid'),
                                           name=vm_info.get('oui').
                                           get('vm_name'),
                                           result=constants.RESULT_SUCCESS))
                self.update_vm_db(vm.port_id, **params)

        elif vm.host != bhost_id:
            # TODO support for live migration should be added here.
            LOG.debug("Binding host for port %(port)s changed"
                      "from %(host_p)s to %(host_n)s."
                      "This is live migration and currently "
                      "it is not supported." % (
                          {'port': port_id,
                           'host_p': vm.host,
                           'host_n': bhost_id}))

    def port_delete_event(self, port_info):
        port_id = port_info.get('port_id')
        if port_id is None:
            LOG.debug("port_delete_event : %s does not exist." % port_id)
            return

        vm = self.get_vm(port_id)
        if not vm:
            LOG.error("port_delete_event: port %s does not exist." % port_id)
            return
        vm_info = dict(status='down',
                       vm_mac=vm.mac,
                       segmentation_id=vm.segmentation_id,
                       host=vm.host,
                       port_uuid=vm.port_id,
                       net_uuid=vm.network_id,
                       oui=dict(ip_addr=vm.ip,
                                vm_name=vm.name,
                                vm_uuid=vm.instance_id,
                                gw_mac=vm.gw_mac,
                                fwd_mod=vm.fwd_mod,
                                oui_id='cisco'))
        LOG.debug("port_delete_event : %s" % vm_info)
        try:
            self.neutron_event.send_vm_info(str(vm_info.get('host')),
                                            str(vm_info))
        except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
            params = dict(columns=dict(result=constants.DELETE_FAIL))
            self.update_vm_db(vm.port_id, **params)
            LOG.error('Failed to send VM info to agent')
        else:
            self.delete_vm_db(vm.instance_id)
            LOG.info('Deleted VM %(vm)s from DB.', {'vm': vm.instance_id})

    def process_data(self, data):
        LOG.debug('process_data: event: %s, payload: %s' % (data[0], data[1]))
        if self.events.get(data[0]):
            try:
                self.events[data[0]](data[1])
            except Exception as exc:
                LOG.exception('Failed to process %s. Reason: %s' % (
                    data[0], str(exc)))
                raise exc

    def process_queue(self):
        LOG.debug('proess_queue ...')
        while True:
            time.sleep(constants.PROCESS_QUE_INTERVAL)
            while not self.pqueue.empty():
                try:
                    events = self.pqueue.get(block=False)
                except Queue.Empty:
                    pass
                except Exception as exc:
                    LOG.exception('ERROR %s:Failed to process queue', str(exc))

                pri = events[0]
                timestamp = events[1]
                data = events[2]
                LOG.debug('events: %s, pri: %s, timestamp: %s, data:%s' % (
                    events, pri, timestamp, data))
                self.process_data(data)

    def _get_ip_leases(self):
        if not self.cfg.dcnm.dcnm_dhcp_leases:
            LOG.debug('DHCP lease file is not defined.')
            return
        ssh_session = paramiko.SSHClient()
        ssh_session.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_session.connect(self.cfg.dcnm.dcnm_ip,
                            username=self.cfg.dcnm.dcnm_user,
                            password=self.cfg.dcnm.dcnm_password)
        try:
            ftp_session = ssh_session.open_sftp()
            dhcpd_leases = ftp_session.file(self.cfg.dcnm.dcnm_dhcp_leases)
            leases = dhcpd_leases.readlines()
            ftp_session.close()
            ssh_session.close()
            return leases
        except IOError:
            ftp_session.close()
            ssh_session.close()
            LOG.error('Cannot open %(file)s.',
                      {'file': self.cfg.dcnm.dcnm_dhcp_leases})

    def update_port_ip_address(self):
        """Find the ip address that assinged to a port via DHCP

        The port database will be updated with the ip address.
        """
        # TODO Move it to create port
        leases = None
        instances = self.get_vms()
        for vm in instances:
            if vm.ip != '0.0.0.0' or not vm.host:
                continue

            if not leases:
                # For the first time finding the leases file.
                leases = self._get_ip_leases()
                if not leases:
                    # File does not exist.
                    return

            LOG.info('Looking for IP address for %(mac)s.' % ({'mac': vm.mac}))
            for line in leases:
                if line.startswith('lease') and line.endswith('{\n'):
                    ip_addr = line.split()[1]
                if 'hardware ethernet' in line:
                    if vm.mac == line.replace(';', '').split()[2]:
                        LOG.info('Find IP address %(ip)s for %(mac)s' % (
                                 {'ip': ip_addr, 'mac': vm.mac}))
                        try:
                            rule_info = dict(ip=ip_addr, mac=vm.mac,
                                             port=vm.port_id,
                                             status='up')
                            self.neutron_event.update_ip_rule(str(vm.host),
                                                              str(rule_info))
                        except (rpc.MessagingTimeout, rpc.RPCException,
                                rpc.RemoteError):
                            LOG.error("RPC error: Failed to update rules.")
                        else:
                            params = dict(columns=dict(ip=ip_addr))
                            self.update_vm_db(vm.port_id, **params)

    def request_vms_info(self, payload):
        """Get the VMs from the database and send the info to the agent."""

        # This request is received from an agent when it runs for the first
        # time and uplink is detected.
        agent = payload.get('agent')
        LOG.debug('request_vms_info: Getting VMs info for %s', agent)
        req = dict(host=payload.get('agent'))
        instances = self.get_vms_for_this_req(**req)
        for vm in instances:
            vm_info = dict(status=vm.status,
                           vm_mac=vm.mac,
                           segmentation_id=vm.segmentation_id,
                           host=vm.host,
                           port_uuid=vm.port_id,
                           net_uuid=vm.network_id,
                           oui=dict(ip_addr=vm.ip,
                                    vm_name=vm.name,
                                    vm_uuid=vm.instance_id,
                                    gw_mac=vm.gw_mac,
                                    fwd_mod=vm.fwd_mod,
                                    oui_id='cisco'))
            try:
                self.neutron_event.send_vm_info(vm.host,
                                                str(vm_info))
            except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
                LOG.error('Failed to send VM info to agent.')

    def request_uplink_info(self, payload):
        """Get the uplink from the database and send the info to the agent."""

        # This request is received from an agent when it run for the first
        # Send the uplink name (physical port name that connectes compute
        #                          node and switch fabric),
        agent = payload.get('agent')
        config_res = self.get_agent_configurations(agent)
        LOG.debug('configurations on %(agent)s is %(cfg)s', (
            {'agent': agent, 'cfg': config_res}))
        try:
            self.neutron_event.send_msg_to_agent(agent,
                                                 constants.UPLINK_NAME,
                                                 config_res)
        except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
            LOG.error("RPC error: Failed to send uplink name to agent.")

    def set_static_ip_address(self, payload):
        """Set static ip address for a VM."""

        # This request is received from CLI for setting ip address of an
        # instance.
        macaddr = payload.get('mac')
        ipaddr = payload.get('ip')

        # Find the entry associated with the mac in the database.
        req = dict(mac=macaddr)
        instances = self.get_vms_for_this_req(**req)
        for vm in instances:
            LOG.info('Updating IP address: %(ip)s %(mac)s.' % (
                {'ip': ipaddr, 'mac': macaddr}))
            # Send request to update the rule.
            try:
                rule_info = dict(ip=ipaddr, mac=macaddr,
                                 port=vm.port_id,
                                 status='up')
                self.neutron_event.update_ip_rule(str(vm.host),
                                                  str(rule_info))
            except (rpc.MessagingTimeout, rpc.RPCException,
                    rpc.RemoteError):
                LOG.error("RPC error: Failed to update rules.")
            else:
                # Update the database.
                params = dict(columns=dict(ip=ipaddr))
                self.update_vm_db(vm.port_id, **params)

    def vm_result_update(self, payload):
        """Update the result field in VM database.

        This request comes from an agent that needs to update the result
        in VM database to success or failure to reflect the operation's result
        in the agent.
        """

        port_id = payload.get('port_id')
        result = payload.get('result')

        if port_id and result:
            # Update the VM's result field.
            params = dict(columns=dict(result=result))
            self.update_vm_db(port_id, **params)

    def create_threads(self):
        """Create threads on server."""

        # Create thread for neutron notifications.
        neutorn_thrd = utils.EventProcessingThread('Neutron_Event',
                                                   self.neutron_event,
                                                   'event_handler',
                                                   self._excpq)
        self.dfa_threads.append(neutorn_thrd)

        # Create thread for processing notification events.
        qp_thrd = utils.EventProcessingThread('Event_Queue', self,
                                              'process_queue', self._excpq)
        self.dfa_threads.append(qp_thrd)

        # Create thread for keystone notifications.
        keys_thrd = utils.EventProcessingThread('Keystone_Event',
                                                self.keystone_event,
                                                'event_handler', self._excpq)
        self.dfa_threads.append(keys_thrd)

        # Create thread to process RPC calls.
        hb_thrd = utils.EventProcessingThread('RPC_Server', self, 'start_rpc',
                                              self._excpq)
        self.dfa_threads.append(hb_thrd)

        # Create thread to listen to dcnm network create event
        dcnmL_thrd = utils.EventProcessingThread('DcnmListener',
                                                 self.dcnm_event,
                                                 'process_amqp_msgs',
                                                 self._excpq)
        self.dfa_threads.append(dcnmL_thrd)

        # Create periodic task to process failure cases in create/delete
        # networks and projects.
        fr_thrd = utils.PeriodicTask(interval=constants.FAIL_REC_INTERVAL,
                                     func=self.add_events,
                                     event_queue=self.pqueue,
                                     priority=self.PRI_LOW_START+10,
                                     excq=self._excpq)

        # Start all the threads.
        for t in self.dfa_threads:
            t.start()

        # Run the periodic tasks.
        fr_thrd.run()


def save_my_pid(cfg):

    mypid = os.getpid()
    pid_path = cfg.dfa_log.pid_dir
    pid_file = cfg.dfa_log.pid_server_file
    if pid_path and pid_file:
        try:
            if not os.path.exists(pid_path):
                os.makedirs(pid_path)
        except OSError:
            pass
        else:
            pid_file_path = os.path.join(pid_path, pid_file)

        LOG.debug('dfa_server pid=%s', mypid)
        with open(pid_file_path, 'w') as funcp:
            funcp.write(str(mypid))


def dfa_server():
    try:
        cfg = config.CiscoDFAConfig().cfg
        logging.setup_logger('dfa_enabler', cfg)
        dfa = DfaServer(cfg)
        save_my_pid(cfg)
        dfa.create_threads()
        LOG.debug('Done...')
        while True:
            time.sleep(constants.MAIN_INTERVAL)
            dfa.update_port_ip_address()
            for trd in dfa.dfa_threads:
                if not trd.am_i_active:
                    LOG.info("Thread %s is not active.", trd.name)
                try:
                    exc = trd._excq.get(block=False)
                except Queue.Empty:
                    pass
                else:
                    emsg = 'Exception occured in %s thread. %s' % (
                        trd.name, exc)
                    LOG.error(emsg)
                    raise Exception(emsg)
            # Check on dfa agents
            cur_time = time.time()
            for agent, time_s in dfa.agents_status_table.iteritems():
                last_seen = time.mktime(time.strptime(time_s))
                if abs(cur_time - last_seen -
                       constants.MAIN_INTERVAL) > constants.HB_INTERVAL:
                    LOG.error("Agent on %(host)s is not seen for %(sec)s. "
                              "Last seen was %(time)s.", (
                                  {'host': agent,
                                   'sec': abs(cur_time - last_seen),
                                   'time': time_s}))

    except Exception as exc:
        LOG.exception("ERROR: %s", exc)

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    sys.exit(dfa_server())
