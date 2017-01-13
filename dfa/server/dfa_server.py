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


"""This is the DFA enabler server module which is responsible for processing
neutron, keystone and DCNM events. Also interacting with DFA enabler agent
module for port events.
"""

import datetime
import eventlet
eventlet.monkey_patch()
import json
import os
import paramiko
import platform
import pytz
import Queue
import re
import six
import sys
import time
import importlib

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
from dfa.server.services.firewall.native import fabric_setup_base as FP
from dfa.server.services.firewall.native import fw_mgr as fw_native

LOG = logging.getLogger(__name__)
# In order to support multiple openstacks on one dcnm
# We need to add some suffix to some reserved openstack
# projects which are created at installation time
reserved_project_name = ["admin", "demo", "alt_demo"]
not_create_project_name = ["invisible_to_admin", "service"]


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
        # Other option is to add the event to the queue for processing it later

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
        pri = self.obj.PRI_LOW_START + 1
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
        pri = self.obj.PRI_LOW_START + 2
        self.obj.pqueue.put((pri, timestamp, data))
        LOG.debug('Added request VMs info into queue.')

        return 0

    def save_uplink(self, context, msg):
        args = json.loads(msg)
        agent = args.get('agent')
        uplink = args.get('uplink')
        veth_intf = args.get('veth_intf')
        memb_port_list = args.get('memb_port_list')
        fail_reason = args.get('fail_reason')
        if agent not in self.obj.agents_status_table:
            self.obj.agents_status_table[agent] = {'fail_reason': fail_reason}
        else:
            self.obj.agents_status_table[agent].update(
                {'fail_reason': fail_reason})
        configs = self.obj.get_agent_configurations(agent)
        if configs:
            # Update the agents database.
            new_config = json.loads(configs)
            new_config.update({'uplink': uplink, 'veth_intf': veth_intf,
                               'memb_ports': memb_port_list})
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

    def is_mand_arg_present(self, intf_dict):
        """Check if mndatory parameters is present for LLDPAD.

        Just checking for 2 parameters.
        """
        if intf_dict.get('remote_port_id_mac') is None and (
           intf_dict.get('remote_system_name') is None):
            return False
        else:
            return True

    def _save_topo_disc_params(self, context, msg):
        args = json.loads(msg)
        agent = args.get('host')
        protocol_interface = args.get('protocol_interface')
        mand_arg = self.is_mand_arg_present(args)
        params = dict(host=agent, protocol_interface=protocol_interface)
        configs = self.obj.topology_db.query_topology_db(
            dict_convert=True, **params)
        if len(configs) != 0:
            # Update the topology database.
            new_config = configs[0]
            if mand_arg:
                new_config.update(args)
                new_config.update({'heartbeat':
                                   utils.utc_time(args.get('heartbeat'))})
                params = dict(columns=new_config)
                self.obj.topology_db.add_update_topology_db(**params)
                LOG.debug('Updated topo discovery %s in DB.', new_config)
            else:
                params = dict(host=agent,
                              protocol_interface=protocol_interface)
                self.obj.topology_db.delete_topology_entry(**params)
                LOG.debug('Deleted topo discovery %s in DB.', new_config)
        # Config not yet created
        else:
            if mand_arg:
                args.update({'created': utils.utc_time(
                    args.get('heartbeat'))})
                args.update({'heartbeat': utils.utc_time(
                    args.get('heartbeat'))})
                params = dict(columns=args)
                self.obj.topology_db.add_update_topology_db(**params)
                LOG.debug('Added topo discovery %s in DB.', args)
        return True

    def save_topo_disc_params(self, context, msg):
        try:
            return self._save_topo_disc_params(context, msg)
        except Exception as exc:
            LOG.error("Exception in RPC save_topo_disc_params %s", str(exc))

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
        payload = json.loads(msg)
        agent = context.get('agent')
        payload.update({'agent': agent})
        LOG.debug('update_vm_result received from %(agent)s: %(payload)s',
                  {'agent': agent, 'payload': payload})

        # Add the request into queue for processing.
        event_type = 'agent.vm_result.update'
        timestamp = time.ctime()
        data = (event_type, payload)
        # TODO use value defined in constants
        pri = self.obj.PRI_LOW_START + 10
        self.obj.pqueue.put((pri, timestamp, data))
        LOG.debug('Added request vm result update into queue.')

        return 0

    def cli_get_networks(self, context, msg):
        """Process request to get Network Details. """

        payload = json.loads(msg)
        nets = self.obj.get_network_by_filters(payload)

        data = []
        for net in nets:
            tenant_name = self.obj.get_project_name(net.tenant_id)
            data.append(dict(name=net.name, net_id=net.network_id,
                             seg=net.segmentation_id, vlan=net.vlan,
                             md=net.mob_domain, cfgp=net.config_profile,
                             tenant_id=net.tenant_id, result=net.result,
                             tenant_name=tenant_name, source=net.source,
                             reason=(self.obj.network.get(
                                 net.network_id)).get('reason')))
        return data

    def cli_get_instances(self, context, msg):
        """Process request to get Instance Details. """

        payload = json.loads(msg)
        vms = self.obj.get_vms_by_filters(payload)

        data = []
        for v in vms:
            reason = None
            if v.port_id in self.obj.port_result:
                reason = self.obj.port_result[
                    v.port_id].get('fail_reason')

            net = self.obj.get_network(v.network_id)
            if not net:
                continue
            tenant_name = self.obj.get_project_name(net.tenant_id)
            data.append(dict(id=v.instance_id, ip=v.ip, name=v.name,
                             mac=v.mac, net_id=v.network_id, host=v.host,
                             local=v.local_vlan, seg=v.segmentation_id,
                             vdp=v.vdp_vlan, port=v.port_id, result=v.result,
                             net_name=net.name, tenant_name=tenant_name,
                             reason=reason))
        return data

    def cli_get_projects(self, context, msg):
        """Process request to get Project Details. """

        payload = json.loads(msg)
        name = payload.get('name')
        tenant_id = payload.get('tenant_id')

        projs = self.obj.get_project_by_filters(name, tenant_id)

        return [(p.id, p.name, p.dci_id, p.result,
                 self.obj.project_info_cache[p.id].get('reason'))
                for p in projs]

    def get_fabric_summary(self, context, msg):
        """Process request to get DCNM and Fabric Details. """

        summary = []

        lan = self.obj.dcnm_client.default_lan_settings()
        dcnm_version = self.obj.dcnm_client.get_version()
        enabler_version = constants.VERSION
        dcnm_ip = self.obj.cfg.dcnm.dcnm_ip
        switch_type = lan.get('deviceType')
        fabric_type = lan.get('fabricEncapsulationMode')
        fabric_id = lan.get('id')
        seg_id = self.obj.cfg.dcnm.segmentation_id_min + '-' + \
            self.obj.cfg.dcnm.segmentation_id_max

        summary.append({'key': "Fabric Enabler Version",
                        'value': enabler_version})
        summary.append({'key': "DCNM Version", 'value': dcnm_version})
        summary.append({'key': "DCNM IP", 'value': dcnm_ip})
        summary.append({'key': "Switch Type", 'value': switch_type})
        summary.append({'key': "Fabric Type", 'value': fabric_type})
        summary.append({'key': "Fabric ID", 'value': '2'})
        summary.append({'key': "Segment ID Range", 'value': seg_id})

        return summary

    def get_per_config_profile_detail(self, context, msg):
        """Process request to get per config profiles details from DCNM. """

        profile = msg.get('profile')
        ftype = msg.get('ftype')

        cfg = self.obj.dcnm_client._config_profile_get_detail(profile, ftype)
        if not cfg:
            LOG.debug("Unable to extract config Profile")
            return False
        return cfg

    def get_config_profiles_detail(self, context, msg):
        """Process request to get all config profiles from DCNM. """

        try:
            cfplist = self.obj.dcnm_client._config_profile_list()
            if not cfplist:
                LOG.debug("Unable to extract config Profile")
                return False

            lan = self.obj.dcnm_client.default_lan_settings()
            if not lan:
                LOG.debug("Unable to extract LAN settings")
                return False

            switch_type = lan.get('deviceType')
            fabric_type = lan.get('fabricEncapsulationMode')
            if fabric_type == 'fabricpath':
                fwding_mode = 'FPVLAN'
            elif fabric_type == 'vxlan' and switch_type == 'n6k':
                fwding_mode = 'IPVLAN'
            else:
                fwding_mode = 'IPBD'

            result = [p for p in cfplist
                      if (p.get('profileType') == fwding_mode)]
            if not result:
                LOG.debug("Unable to extract config Profile")
                return False

            return result
        except dexc.DfaClientRequestFailed as exc:
            raise Exception(exc.args[0])

    def associate_dci_id_to_project(self, context, msg):
        """Proccess request to Associated DCI ID to Project. """

        tenant_id = msg.get('tenant_id')
        tenant_name = msg.get('tenant_name')
        dci_id = msg.get('dci_id')
        tenant_name = self.obj.get_project_name(tenant_id)
        if not tenant_name:
            LOG.error('Failed to associate %(dci_id)s because Project '
                      '%(tenant_id)s does not exist.' % (
                          {'dci_id': dci_id, 'tenant_id': tenant_id}))
            return
        self.obj.update_project_entry(pid=tenant_id,
                                      dci_id=dci_id,
                                      result=constants.RESULT_SUCCESS)
        try:
            self.obj.dcnm_client.update_project(tenant_name,
                                                self.obj.cfg.dcnm.
                                                default_partition_name,
                                                dci_id=dci_id)
        except dexc.DfaClientRequestFailed:
            LOG.error("Failed to update project %s on DCNM.", tenant_name)

    def get_all_networks_for_tenant(self, context, msg):
        """Process request to get all the networks for particular tenant. """

        tenant_id = msg.get('tenant_id')
        tenant_name = self.obj.get_project_name(tenant_id)
        if not tenant_name:
            return False

        net = self.obj.get_network_by_filters(msg)
        netList = []
        for row in net:
            gui_result = row.result
            if row.result == 'CREATE:FAIL' or row.result == 'DELETE:FAIL':
                gui_result = (row.result).replace(":", "_")
            netList.append(dict(network_id=row.network_id,
                                network_name=row.name,
                                config_profile=row.config_profile,
                                seg_id=row.segmentation_id,
                                mob_domain=row.mob_domain, vlan_id=row.vlan,
                                result=gui_result,
                                reason=(self.obj.network.get(
                                    row.network_id)).get('reason')))
        return netList

    def get_instance_by_tenant_id(self, context, msg):
        """Process request to get all the instances for particular tenant. """

        project_id = msg.get('tenant_id')
        tenant_name = self.obj.get_project_name(project_id)
        if not tenant_name:
            return False

        vmlist = []
        vms = self.obj.get_vms_per_tenant(project_id)
        for row in vms:
            gui_result = row[0].result
            if (row[0].result == 'CREATE:FAIL' or
                    row[0].result == 'DELETE:FAIL'):
                gui_result = (row[0].result).replace(":", "_")

            reason = None
            if row[0].port_id in self.obj.port_result:
                reason = self.obj.port_result[
                    row[0].port_id].get('fail_reason')

            vmlist.append(dict(port_id=row[0].port_id, name=row[0].name,
                               network_name=row[1].name,
                               instance_id=row[0].instance_id,
                               mac=row[0].mac, ip=row[0].ip,
                               seg_id=row[0].segmentation_id,
                               host=row[0].host, vdp_vlan=row[0].vdp_vlan,
                               local_vlan=row[0].local_vlan,
                               result=gui_result,
                               reason=reason))
        return vmlist

    def get_project_detail(self, context, msg):
        """Process request to get project details from Enabler and DCNM. """

        tenant_id = msg.get('tenant_id')
        project = (self.obj.get_project_by_filters(None, tenant_id))[0]
        if not project:
            return False

        part = self.obj.cfg.dcnm.default_partition_name
        seg = self.obj.dcnm_client.get_partition_segmentId(project.name, part)
        project_list = []
        project_list.append(dict(project_id=tenant_id,
                                 project_name=project.name,
                                 dci_id=project.dci_id, seg_id=seg,
                                 result=project.result,
                                 reason=self.obj.project_info_cache[
                                     tenant_id].get('reason')))
        return project_list

    def get_agents_details(self, context, msg):
        """Process request to get all agents details from DB. """

        agents = self.obj.get_agents_by_filters(None)
        if not agents:
            return False
        agent_list = []
        for agent in agents:
            heartbeat = pytz.timezone('US/Pacific').localize(agent.heartbeat)
            tz_info = heartbeat.tzinfo
            time_diff = datetime.datetime.now(tz_info) - heartbeat
            timestamp = datetime.timedelta(seconds=constants.HB_INTERVAL)
            active = 'Active' if time_diff < timestamp else 'Not Active'
            agent_list.append(dict(host=agent.host, created=agent.created,
                                   heartbeat=agent.heartbeat,
                                   agent_status=active,
                                   config=agent.configurations))
        return agent_list

    def get_agent_details_per_host(self, context, msg):
        """Process request to get details for particular agent. """

        host = msg.get('host')
        agent = self.obj.get_agents_by_filters(host)
        if not agent:
            return False
        agent_list = []
        agent_list.append(dict(host=agent.host, created=agent.created,
                               heartbeat=agent.heartbeat,
                               config=agent.configurations))
        return agent_list

    def associate_profile_with_network(self, context, msg):
        """Process request to associate profile with particular network. """

        event_type = 'associate.network.profile'
        if self.obj.pqueue is None:
            raise Exception("DFA Queue is not initialized!")

        payload = (event_type, msg)
        self.obj.pqueue.put(((self.obj.PRI_MEDIUM_START + 1),
                             time.ctime(), payload))
        return True


class DfaServer(dfr.DfaFailureRecovery, dfa_dbm.DfaDBMixin,
                fw_native.FwMgr):

    """Process keystone and neutron events.

    The supported events project and network create/delete/update.
    For each events, data will be provided and sent to DCNM.
    """

    def __init__(self, cfg):
        self.events = {}
        super(DfaServer, self).__init__(cfg)
        self.fw_api = FP.FabricApi()
        self._cfg = cfg
        self._host = platform.node()
        self.server = None
        self.events.update({
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
            'service.vnic.create': self.service_vnic_create,
            'service.vnic.delete': self.service_vnic_delete,
            'associate.network.profile': self.associate_network_profile,
        })
        self.project_info_cache = {}
        self.network = {}
        self.subnet = {}
        self.port = {}
        self.port_result = {}
        self.dfa_threads = []
        self.agents_status_table = {}

        # Create segmentation id pool.
        seg_id_min = int(cfg.dcnm.segmentation_id_min)
        seg_id_max = int(cfg.dcnm.segmentation_id_max)
        seg_reuse_timeout = int(cfg.dcnm.segmentation_reuse_timeout)
        self.seg_drvr = dfa_dbm.DfaSegmentTypeDriver(
            seg_id_min, seg_id_max, constants.RES_SEGMENT,
            cfg, reuse_timeout=seg_reuse_timeout)

        self.topology_db = dfa_dbm.TopologyDiscoveryDb(cfg)
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

        # Create DCNM client
        self._gateway_mac = cfg.dcnm.gateway_mac
        dcnm_ip = cfg.dcnm.dcnm_ip
        dcnm_amqp_user = cfg.dcnm.dcnm_amqp_user
        dcnm_password = cfg.dcnm.dcnm_password
        self.dcnm_dhcp = cfg.dcnm.dcnm_dhcp
        self.dcnm_client = cdr.DFARESTClient(cfg)

        # Register segmentation id pool with DCNM
        orch_id = cfg.dcnm.orchestrator_id
        try:
            segid_range = self.dcnm_client.get_segmentid_range(orch_id)
            if segid_range is None:
                self.dcnm_client.set_segmentid_range(orch_id, seg_id_min,
                                                     seg_id_max)
            else:
                conf_min = int(segid_range["segmentIdRanges"].split("-")[0])
                conf_max = int(segid_range["segmentIdRanges"].split("-")[1])
                if conf_min != seg_id_min or conf_max != seg_id_max:
                    self.dcnm_client.update_segmentid_range(orch_id,
                                                            seg_id_min,
                                                            seg_id_max)
        except dexc.DfaClientRequestFailed as exc:
            LOG.critical(("Segment ID range could not be created/updated" +
                         " on DCNM: %s") % (exc))
            raise SystemExit(exc)

        self.populate_cfg_dcnm(cfg, self.dcnm_client)
        self.populate_event_queue(cfg, self.pqueue)

        self.keystone_event = deh.EventsHandler('keystone', self.pqueue,
                                                self.PRI_HIGH_START,
                                                self.PRI_LOW_START)

        self.neutron_event = deh.EventsHandler('neutron', self.pqueue,
                                               self.PRI_MEDIUM_START,
                                               self.PRI_MEDIUM_START + 5)

        if cfg.dcnm.dcnm_net_create:
            self.dcnm_event = dfa_dcnm.DCNMListener(
                name='dcnm',
                ip=dcnm_ip,
                user=dcnm_amqp_user,
                password=dcnm_password,
                pqueue=self.pqueue,
                c_pri=self.PRI_MEDIUM_START + 1,
                d_pri=self.PRI_MEDIUM_START + 6)

        self._inst_api = dfa_inst.DFAInstanceAPI()

        # RPC setup
        self.ser_q = constants.DFA_SERVER_QUEUE
        self._setup_rpc()
        self._lbMgr = None
        if (cfg.loadbalance.lb_enabled and not cfg.loadbalance.lb_native):
            LOG.debug("Vendor LBaaS is enabled")
            lbaas_module = importlib.import_module(
                'dfa.server.services.loadbalance.lb_mgr')
            self._lbMgr = lbaas_module.LbMgr(cfg, self)
            self.events.update({
                'pool.create.end': self._lbMgr.pool_create_event,
                'pool.update.end': self._lbMgr.pool_update_event,
                'enabler_pool_delete': self._lbMgr.pool_delete_event,
                'member.create.end': self._lbMgr.member_create_event,
                'member.update.end': self._lbMgr.member_update_event,
                'enabler_member_delete': self._lbMgr.member_delete_event,
                'vip.create.end': self._lbMgr.vip_create_event,
                'vip.update.end': self._lbMgr.vip_update_event,
                'enabler_vip_delete': self._lbMgr.vip_delete_event,
                'enabler_pool_hm_create': self._lbMgr.pool_hm_create_event,
                'enabler_pool_hm_update': self._lbMgr.pool_hm_update_event,
                'enabler_pool_hm_delete': self._lbMgr.pool_hm_delete_event,
            })
        elif (cfg.loadbalance.lb_enabled and cfg.loadbalance.lb_native):
            LOG.debug("Native LBaaS is enabled")
            self.events.update({
                'vip.create.end': self.vip_create_event,
                'vip.delete.end': self.vip_delete_event,
                'listener.create.end': self.listener_create_event,
                'listener.delete.end': self.listener_delete_event,
                'loadbalancer.delete.end': self.loadbalancer_delete_event,
            })
        if self.dcnm_dhcp is False:
            self.turn_on_dhcp_check()
            self.events.update({
                'dhcp_agent.network.remove': self.dhcp_agent_network_remove,
                'dhcp_agent.network.add': self.dhcp_agent_network_add,
            })
            LOG.info("Using internal DHCP")
        else:
            self.dhcp_consist_check = 0
            LOG.info("Using DCNM DHCP")
        self.sync_projects()
        self.sync_networks()

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

    def is_agent_alive(self, thisagent):
        """Check if a given agent on a compute node still up."""

        # Check if it is reachable and agent sends heartbeat
        return (utils.is_host_alive(thisagent) and
                self.agents_status_table[thisagent].get('fail_count') <
                constants.AGENT_TIMEOUT_THR)

    def get_project_name(self, tenant_id):
        proj = self.project_info_cache.get(tenant_id)
        return None if not proj else proj.get('name')

    def get_dci_id(self, tenant_id):
        proj = self.project_info_cache.get(tenant_id)
        return None if not proj else proj.get('dci_id')

    def _load_network_info(self):
        nets = self.get_all_networks()
        for net in nets:
            if self.fw_api.is_network_source_fw(net, net.name):
                continue
            self.network[net.network_id] = {}
            self.network[net.network_id]['segmentation_id'] = (
                net.segmentation_id)
            self.network[net.network_id]['config_profile'] = (
                net.config_profile)
            self.network[net.network_id]['fwd_mod'] = net.fwd_mod
            self.network[net.network_id]['tenant_id'] = net.tenant_id
            self.network[net.network_id]['name'] = net.name
            self.network[net.network_id]['id'] = net.network_id
            self.network[net.network_id]['vlan'] = net.vlan
            def_part = self._cfg.dcnm.default_partition_name
            self.network[net.network_id]['partition'] = def_part

        LOG.info('Network info cache: %s', self.network)

    def _setup_rpc(self):
        """Setup RPC server for dfa server."""

        endpoints = RpcCallBacks(self)
        self.server = rpc.DfaRpcServer(self.ser_q, self._host,
                                       self.cfg.dfa_rpc.transport_url,
                                       endpoints,
                                       exchange=constants.DFA_EXCHANGE)

    def start_rpc(self):
        self.server.start()
        LOG.debug('starting RPC server on the dfa server.')
        self.server.wait()

    def stop_rpc(self):
        self.server.stop()

    def update_agent_status(self, agent, ts):
        if agent not in self.agents_status_table:
            self.agents_status_table[agent] = dict(timestamp=ts, fail_count=0)
        else:
            self.agents_status_table[agent].update({'timestamp': ts,
                                                    'fail_count': 0})

    def update_reason_in_port_result(self, port_id, reason):
        # Update Failure reason in port_result cache i.e RPC failed
        if port_id in self.port_result:
            self.port_result[port_id].update({'failure_reason': reason})
        else:
            self.port_result[port_id] = {'failure_reason': reason}

    def update_project_info_cache(self, pid, dci_id=None,
                                  name=None, opcode='add',
                                  result=constants.RESULT_SUCCESS,
                                  reason=constants.RESULT_SUCCESS):
        if 'add' in opcode:
            self.project_info_cache[pid] = dict(name=name, dci_id=dci_id,
                                                reason=reason)
            self.add_project_db(pid, name, dci_id, result)
        if 'update' in opcode:
            self.project_info_cache[pid] = dict(name=name, dci_id=dci_id,
                                                reason=reason)
            self.update_project_entry(pid, dci_id, result)
        if 'delete' in opcode:
            if pid in self.project_info_cache:
                if result == constants.DELETE_FAIL:
                    # Update the database entry with failure in the result.
                    # The entry should be deleted later in periodic task.
                    self.project_info_cache[pid] = dict(reason=reason)
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

        proj_fields = proj_name[dci_index + 1:].split(':')
        if len(proj_fields) == 2:
            if (proj_fields[1].isdigit()
                    and proj_fields[0] == dciid_key[1:-1]):
                LOG.debug('project name %(proj)s DCI_ID %(dci_id)s.', (
                    {'proj': proj_name[0:dci_index],
                     'dci_id': proj_fields[1]}))
                return proj_name[0:dci_index], proj_fields[1]

    def project_create_func(self, proj_id, proj=None):
        """ Create project given project uuid"""

        if self.get_project_name(proj_id):
            LOG.info("project %s exists, returning", proj_id)
            return

        if not proj:
            try:
                proj = self.keystone_event._service.projects.get(proj_id)
            except:
                LOG.error("Failed to find project %s.", proj_id)
                return
        # In the project name, dci_id may be included. Check if this is the
        # case and extract the dci_id from the name, and provide dci_id when
        # creating the project.
        proj_name, dci_id = self._get_dci_id_and_proj_name(proj.name)
        if proj_name in reserved_project_name:
            proj_name = "_".join((proj_name, self.cfg.dcnm.orchestrator_id))
        # The default partition name is 'os' (i.e. openstack) which reflects
        # it is created by openstack.
        part_name = self.cfg.dcnm.default_partition_name
        if len(':'.join((proj_name, part_name))) > 32:
            LOG.error('Invalid project name length: %s. The length of org:part'
                      ' name is greater than 32' %
                      len(':'.join((proj_name, part_name))))
            return
        try:
            self.dcnm_client.create_project(self.cfg.dcnm.orchestrator_id,
                                            proj_name, part_name, dci_id,
                                            proj.description)
        except dexc.DfaClientRequestFailed as exc:
            # Failed to send create project in DCNM.
            # Save the info and mark it as failure and retry it later.
            self.update_project_info_cache(proj_id, name=proj_name,
                                           dci_id=dci_id,
                                           result=constants.CREATE_FAIL,
                                           reason=exc.args[0])
            LOG.error("Failed to create project %s on DCNM.", proj_name)
        else:
            self.update_project_info_cache(proj_id, name=proj_name,
                                           dci_id=dci_id)
            LOG.debug('project %(name)s %(dci)s %(desc)s', (
                {'name': proj_name, 'dci': dci_id, 'desc': proj.description}))
        self.project_create_notif(proj_id, proj_name)

    def project_create_event(self, proj_info):
        """Create project."""

        LOG.debug("Processing create %(proj)s event.", {'proj': proj_info})
        proj_id = proj_info.get('resource_info')

        self.project_create_func(proj_id)

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

        new_proj_name, new_dci_id = self._get_dci_id_and_proj_name(proj.name)
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
                  '%(new_dci)s.', {'proj': proj_id,
                                   'orig_dci': orig_dci_id,
                                   'new_dci': new_dci_id})

        try:
            self.dcnm_client.update_project(new_proj_name,
                                            self.cfg.dcnm.
                                            default_partition_name,
                                            dci_id=new_dci_id)
        except dexc.DfaClientRequestFailed as exc:
            # Failed to update project in DCNM.
            # Save the info and mark it as failure and retry it later.
            LOG.error("Failed to update project %s on DCNM.", new_proj_name)
            self.update_project_info_cache(proj_id, name=new_proj_name,
                                           dci_id=new_dci_id,
                                           opcode='update',
                                           result=constants.UPDATE_FAIL,
                                           reason=exc.args[0])
        else:
            self.update_project_info_cache(proj_id, name=new_proj_name,
                                           dci_id=new_dci_id,
                                           opcode='update')
            LOG.debug('Updated project %(proj)s %(name)s.',
                      {'proj': proj_id, 'name': proj.name})

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
            except dexc.DfaClientRequestFailed as exc:
                # Failed to delete project in DCNM.
                # Save the info and mark it as failure and retry it later.
                LOG.error("Failed to create project %s on DCNM.", proj_name)
                self.update_project_info_cache(proj_id, name=proj_name,
                                               opcode='delete',
                                               result=constants.DELETE_FAIL,
                                               reason=exc.args[0])
            else:
                self.update_project_info_cache(proj_id, opcode='delete')
                LOG.debug('Deleted project:%s', proj_name)
            self.project_delete_notif(proj_id, proj_name)

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
        # This checks if the source of the subnet creation is FW,
        # If yes, this event is ignored.
        if self.fw_api.is_subnet_source_fw(snet.get('tenant_id'),
                                           snet.get('cidr')):
            LOG.info("Service subnet %s, returning", snet.get('cidr'))
            return
        if snet_id not in self.subnet:
            self.subnet[snet_id] = {}
            self.subnet[snet_id].update(snet)

        net = self.network.get(self.subnet[snet_id].get('network_id'))
        if not net:
            LOG.error(('Network %(network_id)s does not exist.'),
                      {'network_id': self.subnet[snet_id].get('network_id')})
            return

        # Check if the network is created by DCNM.
        query_net = self.get_network(net.get('id'))
        if query_net.result != constants.SUBNET_PENDING:
            LOG.info("Subnet exists, returning")
            return
        if query_net and query_net.source.lower() == 'dcnm':
            # The network is created by DCNM.
            # No need to process this event.
            LOG.info('create_subnet: network %(name)s was created by DCNM. '
                     'Ignoring processing the event.', (
                         {'name': query_net.name}))
            return

        tenant_name = self.get_project_name(snet['tenant_id'])
        subnet = utils.Dict2Obj(snet)
        dcnm_net = utils.Dict2Obj(net)
        if not tenant_name:
            LOG.error('Project %(tenant_id)s does not exist.', (
                      {'tenant_id': subnet.tenant_id}))
            self.update_network_db(dcnm_net.id, constants.CREATE_FAIL)
            return

        try:
            if self._lbMgr and self._lbMgr.lb_is_internal_nwk(query_net.name):
                self._lbMgr.lb_create_net_dcnm(tenant_name, query_net.name,
                                               net,
                                               snet)
            else:
                part = net['partition']
                self.dcnm_client.create_network(tenant_name, dcnm_net, subnet,
                                                part, self.dcnm_dhcp)
            self.update_network_db(net.get('id'), constants.RESULT_SUCCESS)
            self.network[net.get('id')].update({'reason': 'SUCCESS'})
        except dexc.DfaClientRequestFailed as exc:
            emsg = 'Failed to create network %(net)s.'
            LOG.exception(emsg, {'net': dcnm_net.name})
            self.network[net.get('id')].update({'reason': exc.args[0]})
            # Update network database with failure result.
            self.update_network_db(dcnm_net.id, constants.CREATE_FAIL)
        # Notification to services like FW about creation of Subnet Event
        # Currently, doesn't work for network created in DCNM, place the below
        # lines before the above DCNM check. fixme
        part = net.get('name').partition('::')[2]
        if part:
            # Network in another partition, skip
            return
        self.nwk_sub_create_notif(snet.get('tenant_id'), tenant_name,
                                  snet.get('cidr'))

    def _get_segmentation_id(self, netid, segid, source):
        """Allocate segmentation id."""

        return self.seg_drvr.allocate_segmentation_id(netid, seg_id=segid,
                                                      source=source)

    def associate_network_profile(self, network):
        """Process associate network profile event.

        Associate config profle with network:
        Check if network is already created,
        no: then create a network in db with the associated network profile,
        yes:
        if network is already created with same network profile as of profile
        in event
        return True
        else if subnet event for this network is already proccessed
        then send update network to DCNM with the new network profile
        """

        netid = network['id']
        cfg_p = network['cfgp']
        tenant_id = network['tenant_id']

        net = self.get_network(netid)
        if not net and tenant_id:
            # Insert network into db and return
            network_info = {'network': {'id': network['id'],
                                        'name': network['name'],
                                        'tenant_id': network['tenant_id'],
                                        'config_profile': network['cfgp']}}
            self.network_create_event(network_info)

            return True

        # network present in db, check if network.config_profile==cfg_p in db
        if net.config_profile == cfg_p:
            return True

        # else update db with new cfg_profile
        params = dict(columns=dict(config_profile=cfg_p))
        self.update_network(netid, **params)
        self.network[netid]['config_profile'] = cfg_p

        # check if subnet is present in network
        snet = self.neutronclient.list_subnets(network_id=netid).get('subnets')

        # if present: send update network with config_profile to dcnm
        # else return
        if snet[0]:
            if not tenant_id:
                tenant_id = net.tenant_id
            tenant_name = self.get_project_name(tenant_id)
            subnet = utils.Dict2Obj(snet[0])
            dcnm_net = utils.Dict2Obj(self.network[netid])
            part = self.cfg.dcnm.default_partition_name
            try:
                self.dcnm_client.update_network(tenant_name, dcnm_net, subnet,
                                                part, self.dcnm_dhcp)
            except dexc.DfaClientRequestFailed as exc:
                emsg = 'Failed to update network %(net)s.'
                LOG.exception(emsg, {'net': dcnm_net.name})

        return

    def network_create_func(self, net):
        """ Create network in database and dcnm
        :param net: network dictionary
        """
        net_id = net['id']
        net_name = net.get('name')
        nwk_db_elem = self.get_network(net_id)
        # Check if the source of network creation is FW and if yes, skip
        # this event.
        # Check if there's a way to read the DB from service class TODO
        if self.fw_api.is_network_source_fw(nwk_db_elem, net_name):
            LOG.info("Service network %s, returning", net_name)
            return
        if nwk_db_elem:
            return
        if not nwk_db_elem:
            self.network[net_id] = {}
            self.network[net_id].update(net)

        net_name = net.get('name')
        tenant_id = net.get('tenant_id')

        # Extract segmentation_id from the network name
        net_ext_name = self.cfg.dcnm.dcnm_net_ext
        nobj = re.search(net_ext_name, net_name)
        try:
            seg_id = int((net_name[nobj.start(0) + len(net_ext_name) - 1:]
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
                updated_net_name = (
                    net_name[:nobj.start(0) + len(net_ext_name) - 1])
                try:
                    body = {'network': {'name': updated_net_name, }}
                    dcnm_net = self.neutronclient.update_network(
                        net_id, body=body).get('network')
                    LOG.debug('Updated network %(network)s', dcnm_net)
                except:
                    LOG.exception('Failed to update network '
                                  '%(network)s.',
                                  {'network': updated_net_name})
                    return

            LOG.info('network_create_event: network %(name)s was created '
                     'by %(source)s. Ignoring processing the event.' % (
                         {'name': net_name, 'source': 'dcnm'}))
            return
        if nwk_db_elem:
            LOG.debug("Network %s exists, not processing" % net_name)
            return

        # Check if project (i.e. tenant) exist.
        tenant_name = self.get_project_name(tenant_id)
        if not tenant_name:
            LOG.error('Failed to create network %(name)s. Project '
                      '%(tenant_id)s does not exist.' % (
                          {'name': net_name, 'tenant_id': tenant_id}))
            return

        pseg_id = self.network[net_id].get('provider:segmentation_id')
        seg_id = self._get_segmentation_id(net_id, pseg_id, 'openstack')
        self.network[net_id]['segmentation_id'] = seg_id
        try:
            cfgp, fwd_mod = self.dcnm_client.get_config_profile_for_network(
                net.get('name'))
            if 'config_profile' in net and net['config_profile']:
                cfgp = net['config_profile']
            part = net.get('name').partition('::')[2]
            if part:
                self.network[net_id]['partition'] = part
            else:
                def_part = self._cfg.dcnm.default_partition_name
                self.network[net_id]['partition'] = def_part
            self.network[net_id]['config_profile'] = cfgp
            self.network[net_id]['fwd_mod'] = fwd_mod
            if self._lbMgr and self._lbMgr.lb_is_internal_nwk(net_name):
                vlan = self._lbMgr.lb_get_vlan_from_name(net_name)
                self.network[net_id]['vlan'] = vlan
            self.add_network_db(net_id, self.network[net_id],
                                'openstack',
                                constants.SUBNET_PENDING)
            LOG.debug('network_create_event: network=%s', self.network)
            self.network[net_id].update({'reason': 'SUCCESS'})
        except dexc.DfaClientRequestFailed as exc:
            # Fail to get config profile from DCNM.
            # Save the network info with failure result and send the request
            # to DCNM later.
            self.add_network_db(net_id, self.network[net_id], 'openstack',
                                constants.CREATE_FAIL)
            self.network[net_id].update({'reason': exc.args[0]})
            LOG.error('Failed to create network=%s.', self.network)

    def network_create_event(self, network_info):
        """Process network create event.

        Save the network information in the database.
        """
        net = network_info['network']
        self.network_create_func(net)

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
        net = utils.Dict2Obj(self.network[net_id])
        name = self.network[net_id].get('name')
        if not tenant_name:
            LOG.error('Project %(tenant_id)s does not exist.', (
                      {'tenant_id': tenant_id}))
            self.update_network_db(net.id, constants.DELETE_FAIL)
            return

        try:
            part = self.network[net_id].get('partition')
            if self._lbMgr and self._lbMgr.lb_is_internal_nwk(net.name):
                self.dcnm_client.delete_service_network(tenant_name, net)
            else:
                self.dcnm_client.delete_network(tenant_name, net,
                                                part_name=part)
            # Put back the segmentation id into the pool.
            self.seg_drvr.release_segmentation_id(segid)

            # Remove entry from database and cache.
            self.delete_network_db(net_id)
            del self.network[net_id]
            snets = [k for k in self.subnet if (
                self.subnet[k].get('network_id') == net_id)]
            [self.subnet.pop(s) for s in snets]
        except dexc.DfaClientRequestFailed as exc:
            emsg = ('Failed to create network %(net)s.')
            LOG.error(emsg, {'net': net.name})
            self.network[net.get('id')].update({'reason': exc.args[0]})
            self.update_network_db(net_id, constants.DELETE_FAIL)
        if self._lbMgr and self._lbMgr.lb_is_internal_nwk(net.name):
            self._lbMgr.lb_delete_net(net.name, tenant_id)
        # deleting all related VMs
        instances = self.get_vms()
        instances_related = [(k) for k in instances
                             if k.network_id == net_id]
        for vm in instances_related:
            LOG.info("deleting vm %s because network is deleted" % vm.name)
            self.delete_vm_function(vm.port_id, vm)
        # Notification to services like FW about deletion of Nwk Event,
        # Since deletion of subnet event is not processed currently.
        # Currently, doesn't work for network created in DCNM, place the below
        # lines before the above DCNM check. fixme
        part = name.partition('::')[2]
        if part:
            # Network in another partition, skip
            return
        self.nwk_del_notif(tenant_id, tenant_name, net_id)

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
        seg_id = self._get_segmentation_id(net_id, pseg_id, 'DCNM')
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

        except:
            # Failed to create network, do clean up.
            # Remove the entry from database and local cache.
            del self.network[net_id]
            self.delete_network_db(net_id)
            LOG.exception('dcnm_network_create_event: Failed to create '
                          '%(network)s.', {'network': body})
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
        if self.dcnm_dhcp:
            enable_dhcp = False
        else:
            enable_dhcp = True
        try:
            body = {'subnet': {'cidr': subnet.get('subnet'),
                               'gateway_ip': subnet.get('gateway'),
                               'ip_version': 4,
                               'network_id': net_id,
                               'tenant_id': tenant_id,
                               'enable_dhcp': enable_dhcp,
                               'allocation_pools': allocation_pools, }}
            if self.dcnm_dhcp is False:
                body.get('subnet').pop('allocation_pools', None)
            # Send request to create subnet in neutron.
            LOG.debug('Creating subnet %(subnet)s for DCNM request.' % body)
            dcnm_subnet = self.neutronclient.create_subnet(
                body=body).get('subnet')
            subnet_id = dcnm_subnet.get('id')
            # Update subnet cache.
            self.subnet[subnet_id] = {}
            self.subnet[subnet_id].update(body.get('subnet'))
        except:
            # Failed to create network, do clean up if necessary.
            LOG.exception('Failed to create subnet %(subnet)s for DCNM '
                          'request.', {'subnet': body['subnet']})

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
        if self.fw_api.is_network_source_fw(query_net, query_net.name):
            LOG.info("Service network %s, returning", query_net.name)
            return
        # Send network delete request to neutron
        del_net = None
        try:
            del_net = self.network.pop(query_net.network_id)
            self.neutronclient.delete_network(query_net.network_id)
            self.delete_network_db(query_net.network_id)
        except:
            # Failed to delete network.
            # Put back the entry to the local cache???
            if del_net:
                self.network[query_net.network_id] = del_net
            LOG.exception('dcnm_network_delete_event: Failed to delete '
                          '%(network)s.', {'network': query_net.name})

    def _make_vm_info(self, port, status, vm_prefix=None):
        port_id = port.get('id')
        device_id = port.get('device_id').replace('-', '')
        tenant_id = port.get('tenant_id')
        net_id = port.get('network_id')
        inst_ip = '0.0.0.0'
        segid = (net_id in self.network and
                 self.network[net_id].get('segmentation_id')) or 0
        if not vm_prefix:
            inst_name = self._inst_api.get_instance_for_uuid(device_id,
                                                             tenant_id)
            # handle the case port is created and bound to ovs
            # but vm is not launched on the port. i.e. octavia hm port
            if not inst_name:
                inst_name = port.get('name')

        fwd_mod = (net_id in self.network and
                   self.network[net_id].get('fwd_mod')) or 'anycast-gateway'
        gw_mac = self._gateway_mac if fwd_mod == 'proxy-gateway' else None
        vm_mac = port.get('mac_address')
        if self.dcnm_dhcp is False:
            fixed_ip = port.get('fixed_ips')
            inst_ip = fixed_ip[0].get('ip_address')
            if vm_prefix:
                inst_name = (vm_prefix + str(segid)
                             + '_' + inst_ip.split(".")[3])

        vm_info = dict(status=status,
                       vm_mac=vm_mac,
                       segmentation_id=segid,
                       host=port.get('binding:host_id'),
                       port_uuid=port_id,
                       net_uuid=port.get('network_id'),
                       oui=dict(ip_addr=inst_ip,
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
            vm_info['oui']["ip_addr"] += constants.IP_DHCP_WAIT
            self.add_vms_db(vm_info, constants.RESULT_SUCCESS)

            LOG.debug('Port %s created with no binding host and vif_type.')
            return

        try:
            self.neutron_event.send_vm_info(str(vm_info.get('host')),
                                            str(vm_info))
        except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
            # Failed to send info to the agent. Keep the data in the
            # database as failure to send it later.
            if self.dcnm_dhcp is False:
                vm_info['oui']["ip_addr"] += constants.IP_DHCP_WAIT
            self.add_vms_db(vm_info, constants.CREATE_FAIL)
            LOG.error('Failed to send VM info to agent.')
            # Update Failure reason in port_result cache i.e RPC failed
            reason = ('Failed to send VM info to agent %s.' %
                      str(vm_info.get('host')))
            self.update_reason_in_port_result(port_id, reason)
        else:
            # if using native DHCP , append a W at the end of ip address
            # to indicate that the dhcp port needs to be queried
            if self.dcnm_dhcp is False:
                vm_info['oui']["ip_addr"] += constants.IP_DHCP_WAIT
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

                # Update Failure reason in port_result cache i.e RPC failed
                reason = 'Failed to send VM info to agent %s.' % bhost_id
                self.update_reason_in_port_result(port_id, reason)
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
            LOG.debug("Binding host for port %(port)s changed "
                      "from %(host_p)s to %(host_n)s." %
                      ({'port': port_id,
                        'host_p': vm.host,
                        'host_n': bhost_id}))
            # This is port migration case and the event process as follows:
            # 1. Send port 'up' event to the agent that VM is migrating to by
            #    calling _migrate_to. The result of this operation needs to be
            #    recorded.
            # 2. Send port 'down' event to the agent that VM is migrating from
            #    by calling _migrate_from. Same as (1), the result of this
            #    operation needs to be saved.
            #
            # The result of the two events will be saved in the result field in
            # the instance's database with the following format:
            # result['src'] - This is a dictionary which contains results of
            #                 all the host name that the port is migrating
            #                 from. The following data will be save:
            #                 key = host name,
            #                 value = result of step 2, vdp_vlan
            # result['dst'] - This dictionary contains the result of step 1
            #                 event.
            to_result = self._migrate_to(vm, bhost_id)
            from_result = self._migrate_from(vm, bhost_id)
            src_mig = {}
            dst_mig = {}
            src_mig[vm.host] = dict(res=from_result, vlan=vm.vdp_vlan)
            dst_mig[bhost_id] = dict(res=to_result)
            if vm.status == constants.MIGRATE:
                # This VM is already in migrating. Update the result by adding
                # new host name and result to the src of migration.
                vm_result = eval(vm.result)
                if vm_result.get(constants.MIG_FROM).get(bhost_id):
                    # If migrating back to the original host, and if migration
                    # still in process for this host, remove it from the list.
                    vm_result.get(constants.MIG_FROM).pop(bhost_id)
                vm_result[constants.MIG_FROM].update(src_mig)
                vm_result[constants.MIG_TO] = dst_mig

                result_field = str(vm_result)
            else:
                # The current status is not migration. Add the result of two
                # events (i.e. up and down events) to the result filed.
                result_field = str(dict(result=constants.MIGRATE,
                                        src=src_mig,
                                        dst=dst_mig))
            params = dict(columns=dict(status=constants.MIGRATE,
                                       host=bhost_id,
                                       result=result_field))
            self.update_vm_db(vm.port_id, **params)
            LOG.debug("Migration: updating VM DB with %s.", params)

    def _migrate_from(self, vm, new_host):
        if constants.IP_DHCP_WAIT in vm.ip:
            ipaddr = vm.ip.replace(constants.IP_DHCP_WAIT, '')
        else:
            ipaddr = vm.ip
        # Send VM 'down' event to agent that migrated VM resided.
        vm_info = dict(status='down',
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
        try:
            self.neutron_event.send_vm_info(str(vm.host), str(vm_info))
            LOG.debug("Sent 'down' event for %(port)s to %(to)s.",
                      {'port': vm.port_id, 'to': vm.host})
        except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
            LOG.error('Failed to send VM info to agent %s.' % vm.host)
            # Update Failure reason in port_result cache i.e RPC failed
            reason = 'Failed to send VM info to agent %s.' % vm.host
            self.update_reason_in_port_result(vm.port_id, reason)
            return constants.DELETE_FAIL
        else:
            return constants.DELETE_PENDING

    def _migrate_to(self, vm, to_host):
        if constants.IP_DHCP_WAIT in vm.ip:
            ipaddr = vm.ip.replace(constants.IP_DHCP_WAIT, '')
        else:
            ipaddr = vm.ip
        # Send VM 'up' event to agent that VM migrated to.
        vm_info = dict(status='up',
                       vm_mac=vm.mac,
                       segmentation_id=vm.segmentation_id,
                       host=to_host,
                       port_uuid=vm.port_id,
                       net_uuid=vm.network_id,
                       oui=dict(ip_addr=ipaddr,
                                vm_name=vm.name,
                                vm_uuid=vm.instance_id,
                                gw_mac=vm.gw_mac,
                                fwd_mod=vm.fwd_mod,
                                oui_id='cisco'))
        try:
            self.neutron_event.send_vm_info(str(to_host), str(vm_info))
            LOG.debug("Sent 'up' event for %(port)s to %(to)s.",
                      {'port': vm.port_id, 'to': to_host})
        except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
            LOG.error('Failed to send VM info to agent %s.' % to_host)
            # Update Failure reason in port_result cache i.e RPC failed
            reason = 'Failed to send VM info to agent %s.' % to_host
            self.update_reason_in_port_result(vm.port_id, reason)
            return constants.CREATE_FAIL
        else:
            return constants.RESULT_SUCCESS

    def port_delete_event(self, port_info):
        port_id = port_info.get('port_id')
        if port_id is None:
            LOG.debug("port_delete_event : %s does not exist." % port_id)
            return
        self.delete_vm_function(port_id)

    def delete_vm_function(self, port_id, vm=None):
        if not vm:
            vm = self.get_vm(port_id)
            if not vm:
                LOG.error("delete_vm_function: port %s does not exist." %
                          port_id)
                return
        if not vm.host:
            LOG.debug("host is empty, delete db right away")
            self.delete_vm_db(vm.port_id)
            if vm.port_id in self.port:
                del self.port[vm.port_id]
            return
        if constants.IP_DHCP_WAIT in vm.ip:
            ipaddr = vm.ip.replace(constants.IP_DHCP_WAIT, '')
        else:
            ipaddr = vm.ip
        vm_info = dict(status='down',
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
        LOG.debug("deleting port : %s" % vm_info)

        if self.send_vm_info(vm_info):
            params = dict(columns=dict(status='down',
                                       result=constants.DELETE_PENDING))
            LOG.info('VM %(vm)s is in delete pending state.',
                     {'vm': vm.port_id})
        else:
            params = dict(columns=dict(status='down',
                                       result=constants.DELETE_FAIL))
            # Update Failure reason in port_result cache i.e RPC failed
            reason = ('Failed to send VM info to agent %s.' %
                      str(vm.host))
            self.update_reason_in_port_result(vm.port_id, reason)
        self.update_vm_db(vm.port_id, **params)

    def service_vnic_create(self, vnic_info_arg):
        LOG.info("Service vnic create %s", vnic_info_arg)
        vnic_info = vnic_info_arg.get('service')
        vm_info = dict(status=vnic_info.get('status'),
                       vm_mac=vnic_info.get('mac'),
                       segmentation_id=vnic_info.get('segid'),
                       host=vnic_info.get('host'),
                       port_uuid=vnic_info.get('port_id'),
                       net_uuid=vnic_info.get('network_id'),
                       oui=dict(ip_addr=vnic_info.get('vm_ip'),
                                vm_name=vnic_info.get('vm_name'),
                                vm_uuid=vnic_info.get('vm_uuid'),
                                gw_mac=vnic_info.get('gw_mac'),
                                fwd_mod=vnic_info.get('fwd_mod'),
                                oui_id='cisco'))
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

    def service_vnic_delete(self, vnic_info_arg):
        LOG.info("Service vnic delete %s", vnic_info_arg)
        vnic_info = vnic_info_arg.get('service')
        vm_info = dict(status=vnic_info.get('status'),
                       vm_mac=vnic_info.get('mac'),
                       segmentation_id=vnic_info.get('segid'),
                       host=vnic_info.get('host'),
                       port_uuid=vnic_info.get('port_id'),
                       net_uuid=vnic_info.get('network_id'),
                       oui=dict(ip_addr=vnic_info.get('vm_ip'),
                                vm_name=vnic_info.get('vm_name'),
                                vm_uuid=vnic_info.get('vm_uuid'),
                                gw_mac=vnic_info.get('gw_mac'),
                                fwd_mod=vnic_info.get('fwd_mod'),
                                oui_id='cisco'))
        try:
            self.neutron_event.send_vm_info(str(vm_info.get('host')),
                                            str(vm_info))
        except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
            # Failed to send info to the agent. Keep the data in the
            # database as failure to send it later.
            params = dict(columns=dict(result=constants.DELETE_FAIL))
            self.update_vm_db(vnic_info.get('port_id'), **params)
            LOG.error('Failed to send VM info to agent')
        else:
            self.delete_vm_db(vnic_info.get('port_id'))

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
        LOG.debug('process_queue ...')
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
        try:
            ssh_session = paramiko.SSHClient()
            ssh_session.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_session.connect(self.cfg.dcnm.dcnm_ip,
                                username=self.cfg.dcnm.dcnm_user,
                                password=self.cfg.dcnm.dcnm_password)
        except:
            LOG.exception('Failed to establish connection with DCNM.')
            if ssh_session:
                ssh_session.close()
            return

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
        """Find the ip address that assigned to a port via DHCP

        The port database will be updated with the ip address.
        """
        # TODO Move it to create port
        leases = None
        req = dict(ip='0.0.0.0')
        instances = self.get_vms_for_this_req(**req)
        if instances is None:
            return

        for vm in instances:
            if not leases:
                # For the first time finding the leases file.
                leases = self._get_ip_leases()
                if not leases:
                    # File does not exist.
                    return

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

                            # Send update to the agent.
                            vm_info = dict(status=vm.status, vm_mac=vm.mac,
                                           segmentation_id=vm.segmentation_id,
                                           host=vm.host, port_uuid=vm.port_id,
                                           net_uuid=vm.network_id,
                                           oui=dict(ip_addr=ip_addr,
                                                    vm_name=vm.name,
                                                    vm_uuid=vm.instance_id,
                                                    gw_mac=vm.gw_mac,
                                                    fwd_mod=vm.fwd_mod,
                                                    oui_id='cisco'))
                            try:
                                self.neutron_event.send_vm_info(vm.host,
                                                                str(vm_info))
                            except (rpc.MessagingTimeout, rpc.RPCException,
                                    rpc.RemoteError):
                                LOG.error(('Failed to send VM info to agent.'))

    def turn_on_dhcp_check(self):
        self.dhcp_consist_check = constants.DHCP_PORT_CHECK

    def decrement_dhcp_check(self):
        self.dhcp_consist_check = self.dhcp_consist_check - 1

    def need_dhcp_check(self):
        if self.dhcp_consist_check > 0:
            return True
        else:
            return False

    def send_vm_info(self, vm_info):
        """ Send vm info to the compute host.
        it will return True/False
        """
        agent_host = vm_info.get('host')
        if not agent_host:
            LOG.info("agent host is empty, not sending vm info")
            return True
        ip = vm_info['oui']['ip_addr']
        if constants.IP_DHCP_WAIT in ip:
            ipaddr = ip.replace(constants.IP_DHCP_WAIT, '')
            vm_info['oui']['ip_addr'] = ipaddr
        try:
            self.neutron_event.send_vm_info(agent_host,
                                            str(vm_info))
        except (rpc.MessagingTimeout, rpc.RPCException,
                rpc.RemoteError):
            # Failed to send info to the agent. Keep the data in the
            # database as failure to send it later.
            LOG.error('Failed to send VM info to agent %s', agent_host)
            return False
        else:
            return True

    def add_dhcp_port(self, p):
        port_id = p['id']
        if self.get_vm(port_id):
            LOG.info("dhcp port %s has already been  added" %
                     port_id)
            return
        d_id = p["device_id"]
        l = constants.DID_LEN
        p["device_id"] = d_id[:l] if len(d_id) > l else d_id

        vm_info = self._make_vm_info(p, 'up', constants.DHCP_PREFIX)
        LOG.debug("add_dhcp_ports : %s" % vm_info)
        self.port[port_id] = vm_info
        if self.send_vm_info(vm_info):
            self.add_vms_db(vm_info, constants.RESULT_SUCCESS)
        else:
            self.add_vms_db(vm_info, constants.CREATE_FAIL)
        return

    def correct_dhcp_ports(self, net_id):

        search_opts = {'network_id': net_id,
                       'device_owner': 'network:dhcp'}

        data = self.neutronclient.list_ports(**search_opts)
        dhcp_ports = data.get('ports', [])
        add = False
        remove = False
        for p in dhcp_ports:
            port_id = p['id']
            status = p['status']
            ip_host = p.get('binding:host_id')
            if not self.neutron_event._clients.get(ip_host):
                LOG.info("Agent on %s is not active" % ip_host)
                LOG.info("Ignore DHCP port %s" % port_id)
                continue
            if status == 'ACTIVE':
                add = True
                self.add_dhcp_port(p)
            else:
                self.delete_vm_function(port_id)
                remove = True
                LOG.info("port %s, is deleted due to dhcp HA remove"
                         % port_id)
        return (add, remove)

    def check_dhcp_ports(self):
        instances = self.get_vms()
        if instances is None:
            return
        network_processed = []
        wait_dhcp_instances = [(k) for k in instances
                               if constants.IP_DHCP_WAIT in k.ip]
        for vm in wait_dhcp_instances:
            net_id = vm.network_id
            if net_id in network_processed:
                LOG.info("net_id %s has been queried for dhcp port"
                         % net_id)
                self.strip_wait_dhcp(vm)
                continue
            add, remove = self.correct_dhcp_ports(net_id)

            if add is True:
                self.strip_wait_dhcp(vm)
                network_processed.append(net_id)

    def strip_wait_dhcp(self, vm):
        LOG.info("updating port %s ip address" % vm.port_id)
        ip = vm.ip.replace(constants.IP_DHCP_WAIT, '')
        params = dict(columns=dict(ip=ip))
        self.update_vm_db(vm.port_id, **params)

    def request_vms_info(self, payload):
        """Get the VMs from the database and send the info to the agent."""

        # This request is received from an agent when it runs for the first
        # time and uplink is detected.
        agent = payload.get('agent')
        LOG.debug('request_vms_info: Getting VMs info for %s', agent)
        # Collect all instances that exist on this agent.
        req = dict(host=agent)
        instances = self.get_vms_for_this_req(**req)
        vm_info = []
        for vm in instances:
            if constants.IP_DHCP_WAIT in vm.ip:
                ipaddr = vm.ip.replace(constants.IP_DHCP_WAIT, '')
            else:
                ipaddr = vm.ip

            # Check whether the VM is migrating on this agent. If so, send
            # event to the agent with 'up' status.
            vm_status = 'up' if vm.status == constants.MIGRATE else vm.status
            vm_info.append(dict(status=vm_status,
                           vm_mac=vm.mac,
                           segmentation_id=vm.segmentation_id,
                           host=vm.host,
                           port_uuid=vm.port_id,
                           net_uuid=vm.network_id,
                           vdp_vlan=vm.vdp_vlan,
                           local_vlan=vm.local_vlan,
                           oui=dict(ip_addr=ipaddr,
                                    vm_name=vm.name,
                                    vm_uuid=vm.instance_id,
                                    gw_mac=vm.gw_mac,
                                    fwd_mod=vm.fwd_mod,
                                    oui_id='cisco')))

        # There could be cases that migration is in progress on the same agent.
        # To include this case, lookup for those instances that are in
        # migration process and append them to the list.
        req = dict(status=constants.MIGRATE)
        mig_insts = self.get_vms_for_this_req(**req)
        for vm in mig_insts:
            vmr = eval(vm.result)
            for from_host, from_val in six.iteritems(
                    vmr.get(constants.MIG_FROM)):
                if from_host == agent:
                    # It means the 'down' event should be sent to the agent,
                    # since this is the host that VM is migrating from.
                    vm_info.append(dict(status='down',
                                   vm_mac=vm.mac,
                                   segmentation_id=vm.segmentation_id,
                                   host=agent,
                                   port_uuid=vm.port_id,
                                   net_uuid=vm.network_id,
                                   vdp_vlan=from_val.get('vlan'),
                                   local_vlan=vm.local_vlan,
                                   oui=dict(ip_addr=ipaddr,
                                            vm_name=vm.name,
                                            vm_uuid=vm.instance_id,
                                            gw_mac=vm.gw_mac,
                                            fwd_mod=vm.fwd_mod,
                                            oui_id='cisco')))

        try:
            self.neutron_event.send_vm_info(agent, str(vm_info))
        except (rpc.MessagingTimeout, rpc.RPCException, rpc.RemoteError):
            LOG.error('Failed to send VM info to agent.')

    def request_uplink_info(self, payload):
        """Get the uplink from the database and send the info to the agent."""

        # This request is received from an agent when it run for the first
        # Send the uplink name (physical port name that connects compute
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

                # Send update to the agent.
                vm_info = dict(status=vm.status, vm_mac=vm.mac,
                               segmentation_id=vm.segmentation_id,
                               host=vm.host, port_uuid=vm.port_id,
                               net_uuid=vm.network_id,
                               oui=dict(ip_addr=ipaddr,
                                        vm_name=vm.name,
                                        vm_uuid=vm.instance_id,
                                        gw_mac=vm.gw_mac,
                                        fwd_mod=vm.fwd_mod,
                                        oui_id='cisco'))
                try:
                    self.neutron_event.send_vm_info(vm.host,
                                                    str(vm_info))
                except (rpc.MessagingTimeout, rpc.RPCException,
                        rpc.RemoteError):
                    LOG.error('Failed to send VM info to agent.')

    def _update_migration_result(self, vm, agent, result):
        # This is migration case. Only update the result field
        # based on the agent.
        vmr = eval(vm.result)
        res = vmr.get(constants.MIG_FROM).get(agent).update(
            {'res': result}) if (vmr.get(constants.MIG_FROM).
                                 get(agent)) else (
                vmr.get(constants.MIG_TO).get(agent).update(
                    {'res': result}) if (vmr.get(constants.MIG_TO).
                                         get(agent)) else True)

        if res:
            LOG.info("No agent %s found in the result to update.",
                     agent)
            return

        # Check if the result from agents that VM is migrated
        # from is success. If so, make the status to 'up' as it means
        # migration from the agent is done.
        res_list = []
        for from_host, from_val in six.iteritems(vmr.get(constants.MIG_FROM)):
            res_list.append(from_val.get('res') == constants.RESULT_SUCCESS)

        if all(res_list):
            # All the results are success
            to_res = vmr.get(constants.MIG_TO).values()[0].get('res')
            params = dict(columns=dict(status='up', result=to_res))
        else:
            # Still in migration process. So update the latest
            # result in the port's database.
            params = dict(columns=dict(result=str(vmr)))

        LOG.debug("_update_migration_result: port_id: %(pid)s params: %(pr)s",
                  {'pid': vm.port_id, 'pr': params})
        self.update_vm_db(vm.port_id, **params)

    def vm_result_update(self, payload):
        """Update the result field in VM database.

        This request comes from an agent that needs to update the result
        in VM database to success or failure to reflect the operation's result
        in the agent.
        """

        port_id = payload.get('port_uuid')
        result = payload.get('result')
        agent = payload.get('agent')

        if port_id and result:
            # Get the entry for this port_id
            req = dict(port_id=port_id)
            vms = self.get_vms_for_this_req(**req)
            if vms is None:
                LOG.error("There is no instance for port_id %s", port_id)
                return
            # Check whether the result is for success on delete.

            for vm in vms:
                if vm.status == constants.MIGRATE:
                    self._update_migration_result(vm, agent, result)
                    return

                if (result == constants.RESULT_SUCCESS and
                        vm.result in constants.DELETE_LIST):

                    # Now delete the VM from the database.
                    self.delete_vm_db(vm.port_id)
                    LOG.info('Deleted VM %(vm)s from DB.', {'vm': vm.name})
                    if vm.port_id in self.port:
                        del self.port[vm.port_id]
                    if vm.port_id in self.port_result:
                        del self.port_result[vm.port_id]
                    return
                res = constants.RESULTS_MAP.get((result, vm.result))
                final_res = res if res else vm.result

                # Update the VM's result field.
                if payload.get('vdp_vlan') is None or (
                   payload.get('local_vlan') is None):
                    params = dict(columns=dict(result=final_res))
                else:
                    params = dict(columns=dict(
                                  vdp_vlan=payload.get('vdp_vlan'),
                                  local_vlan=payload.get('local_vlan'),
                                  result=final_res))
                LOG.debug("vm_result_update: port_id: %(pid)s, params: %(pr)s",
                          {'pid': port_id, 'pr': params})
                self.update_vm_db(port_id, **params)
            if port_id in self.port_result:
                self.port_result[port_id].update(
                    {'local_vlan': payload.get('local_vlan'),
                     'vdp_vlan': payload.get('vdp_vlan'),
                     'result': result,
                     'fail_reason': payload.get('fail_reason')})
            else:
                self.port_result[port_id] = {
                    'local_vlan': payload.get('local_vlan'),
                    'vdp_vlan': payload.get('vdp_vlan'),
                    'result': result,
                    'fail_reason': payload.get('fail_reason')}

    def dhcp_agent_network_add(self, dhcp_net_info):
        """Process dhcp agent net add event.
        """
        self.turn_on_dhcp_check()

    def dhcp_agent_network_remove(self, dhcp_net_info):
        """Process dhcp agent net remove event.
        """
        self.turn_on_dhcp_check()

    def add_lbaas_port(self, port_id, lb_id):
        """Give port id, get port info and send vm info to agent,
        lb_id will be the vip id for v1 and lbaas_id for v2
        """
        port_info = self.neutronclient.show_port(port_id)
        port = port_info.get('port')
        if not port:
            LOG.error("Can not retrieve port info for port %s" % port_id)
            return
        LOG.debug("lbaas add port, %s", port)
        if not port['binding:host_id']:
            LOG.info("No host bind for lbaas port, octavia case")
            return
        port["device_id"] = lb_id

        vm_info = self._make_vm_info(port, 'up', constants.LBAAS_PREFIX)
        self.port[port_id] = vm_info
        if self.send_vm_info(vm_info):
            self.add_vms_db(vm_info, constants.RESULT_SUCCESS)
        else:
            self.add_vms_db(vm_info, constants.CREATE_FAIL)
        return

    def delete_lbaas_port(self, lb_id):
        """Given lbaas id , send vm down event and delete db
        """
        lb_id = lb_id.replace('-', '')
        req = dict(instance_id=lb_id)
        instances = self.get_vms_for_this_req(**req)
        for vm in instances:
            LOG.info("deleting lbaas vm %s " % vm.name)
            self.delete_vm_function(vm.port_id, vm)

    def vip_create_event(self, vip_info):
        """Process vip create event
        """
        vip_data = vip_info.get('vip')
        port_id = vip_data.get('port_id')
        vip_id = vip_data.get('id')
        self.add_lbaas_port(port_id, vip_id)

    def vip_delete_event(self, vip_info):
        """Process vip delete event
        """
        vip_id = vip_info.get('vip_id')
        self.delete_lbaas_port(vip_id)

    def listener_create_event(self, listener_info):
        """ Process listener create event
        This is lbaas v2
        vif will be plugged into ovs when first
        listener is created and unpluged from ovs
        when last listener is deleted
        """
        listener_data = listener_info.get('listener')
        lb_list = listener_data.get('loadbalancers')
        for lb in lb_list:
            lb_id = lb.get('id')
            req = dict(instance_id=(lb_id.replace('-', '')))
            instances = self.get_vms_for_this_req(**req)
            if not instances:
                lb_info = self.neutronclient.show_loadbalancer(lb_id)
                if lb_info:
                    port_id = lb_info["loadbalancer"]["vip_port_id"]
                    self.add_lbaas_port(port_id, lb_id)
            else:
                LOG.info("lbaas port for lb %s already added" % lb_id)

    def listener_delete_event(self, listener_info):
        """ Process listener delete event
        This is lbaas v2
        vif will be plugged into ovs when first
        listener is created and unpluged from ovs
        when last listener is created.
        as the data only contains listener id, we will
        scan all loadbalancers from db and delete the vdp
        if there admin state is down in that loadbalancer
        """
        lb_list = self.neutronclient.list_loadbalancers()
        for lb in lb_list.get('loadbalancers'):
            if not lb.get("listeners"):
                lb_id = lb.get('id')
                LOG.info("Deleting lb %s port" % lb_id)
                self.delete_lbaas_port(lb_id)

    def loadbalancer_delete_event(self, lb_info):
        """ Process loadbalancer delete event
        This is lbaas v2
        """
        lb_id = lb_info.get('loadbalancer_id')
        self.delete_lbaas_port(lb_id)

    def sync_projects(self):
        """Sync projects
        This function will retrieve project from keystone
        and populate them  dfa database and dcnm
        """
        p = self.keystone_event._service.projects.list()
        for proj in p:
            if proj.name in not_create_project_name:
                continue
            LOG.info("Syncing project %s" % proj.name)
            self.project_create_func(proj.id, proj=proj)

    def sync_networks(self):
        """sync networkss
        It will Retrieve networks from neutron and populate
        them in dfa database and dcnm
        """
        nets = self.neutronclient.list_networks()
        for net in nets.get("networks"):
            LOG.info("Syncing network %s", net["id"])
            self.network_create_func(net)
        subnets = self.neutronclient.list_subnets()
        for subnet in subnets.get("subnets"):
            LOG.info("Syncing subnet %s", subnet["id"])
            self.create_subnet(subnet)

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
        if self.cfg.dcnm.dcnm_net_create:
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
                                     priority=self.PRI_LOW_START + 10,
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
            LOG.error(('Fail to create %s'), pid_path)
            return

        pid_file_path = os.path.join(pid_path, pid_file)

        LOG.debug('dfa_server pid=%s', mypid)
        with open(pid_file_path, 'w') as funcp:
            funcp.write(str(mypid))


def dfa_server():
    try:
        cfg = config.CiscoDFAConfig().cfg
        logging.setup_logger('server', cfg)
        dfa = DfaServer(cfg)
        save_my_pid(cfg)
        dfa.create_threads()
        while True:
            time.sleep(constants.MAIN_INTERVAL)
            try:
                if dfa.dcnm_dhcp:
                    dfa.update_port_ip_address()
                else:
                    dfa.check_dhcp_ports()
            except Exception as exc:
                LOG.error("Exception %s occured " % exc.__class__)

            for trd in dfa.dfa_threads:
                if not trd.am_i_active:
                    LOG.info("Thread %s is not active.", trd.name)
                    if not trd.restart_threshold_exceeded:
                        LOG.info("Thread %s is restarted.", trd.name)
                        trd.run()
                    else:
                        LOG.error('ERROR: Thread %(name)s restarted '
                                  'for %(times)s times. Aborting..', (
                                      {'name': trd.name,
                                       'times': trd.restart_count}))
                        raise SystemExit('ERROR: Thread %(name)s restarted '
                                         'for %(times)s times.', (
                                             {'name': trd.name,
                                              'times': trd.restart_count}))
                try:
                    exc = trd._excq.get(block=False)
                except Queue.Empty:
                    pass
                else:
                    trd_name = eval(exc).get('name')
                    exc_tb = eval(exc).get('tb')
                    emsg = 'Exception occurred in %s thread. %s' % (
                        trd_name, exc_tb)
                    LOG.error(emsg)
            # Check on dfa agents
            cur_time = time.time()
            for agent, ent in six.iteritems(dfa.agents_status_table):
                time_s = ent.get('timestamp')
                last_seen = time.mktime(time.strptime(time_s))
                if abs(cur_time - last_seen -
                       constants.MAIN_INTERVAL) > constants.HB_INTERVAL:
                    LOG.error("Agent on %(host)s is not seen for %(sec)s. "
                              "Last seen was %(time)s.", (
                                  {'host': agent,
                                   'sec': abs(cur_time - last_seen),
                                   'time': time_s}))
                    # Add failure count
                    ent.update({'fail_count': ent.get('fail_count') + 1})
                else:
                    ent.update({'fail_count': 0})

    except Exception as exc:
        LOG.exception("ERROR: %s", exc)

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    sys.exit(dfa_server())
