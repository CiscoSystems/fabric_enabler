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

import json
import netaddr
import sqlalchemy as sa
import sqlalchemy.orm.exc as orm_exc

from oslo.db import exception as db_exc
from six import moves

from dfa.common import constants as const
from dfa.common import dfa_logger as logging
import dfa_db_api as db

LOG = logging.getLogger(__name__)

DB_MAX_RETRIES = 10
RULE_LEN = 4096


class DfaSegmentationId(db.Base):
    """Represents DFA segmentation ID."""

    __tablename__ = 'segmentation_id'

    segmentation_id = sa.Column(sa.Integer, nullable=False, primary_key=True,
                                autoincrement=False)
    network_id = sa.Column(sa.String(36))
    allocated = sa.Column(sa.Boolean, nullable=False, default=False)
    source = sa.Column(sa.String(16))


class DfaVlanId(db.Base):
    """Represents DFA VLAN ID."""

    __tablename__ = 'vlan_id'

    segmentation_id = sa.Column(sa.Integer, nullable=False, primary_key=True,
                                autoincrement=False)
    network_id = sa.Column(sa.String(36))
    allocated = sa.Column(sa.Boolean, nullable=False, default=False)
    source = sa.Column(sa.String(16))


class DfaInServiceSubnet(db.Base):
    """Represents DFA Service Subnet."""

    __tablename__ = 'in_service_subnet'

    subnet_address = sa.Column(sa.String(20), nullable=False, primary_key=True,
                               autoincrement=False)
    network_id = sa.Column(sa.String(36))
    subnet_id = sa.Column(sa.String(36))
    allocated = sa.Column(sa.Boolean, nullable=False, default=False)


class DfaOutServiceSubnet(db.Base):
    """Represents DFA Service Subnet."""

    __tablename__ = 'out_service_subnet'

    subnet_address = sa.Column(sa.String(20), nullable=False, primary_key=True,
                               autoincrement=False)
    network_id = sa.Column(sa.String(36))
    subnet_id = sa.Column(sa.String(36))
    allocated = sa.Column(sa.Boolean, nullable=False, default=False)


class DfaResource(object):

    def is_res_init_done(self, num_init):
        if num_init > 0:
            return True
        else:
            return False


class DfaSegment(DfaResource):
    dfa_segment_init = 0

    def get_model(cls):
        return DfaSegmentationId

    @classmethod
    def init_done(cls):
        cls.dfa_segment_init = cls.dfa_segment_init + 1

    def is_init_done(cls):
        return cls.is_res_init_done(cls.dfa_segment_init)


class DfaVlan(DfaResource):
    dfa_vlan_init = 0

    def get_model(cls):
        return DfaVlanId

    @classmethod
    def init_done(cls):
        cls.dfa_vlan_init = cls.dfa_vlan_init + 1

    def is_init_done(cls):
        return cls.is_res_init_done(cls.dfa_vlan_init)


class DfaSegmentTypeDriver(object):

    # Tested for both Segment and VLAN
    def __init__(self, segid_min, segid_max, res_name, cfg):
        # Have a check here to ensure a crazy init is not called TODO(padkrish)
        db.configure_db(cfg)
        self.seg_id_ranges = []
        self.seg_id_ranges.append((segid_min, segid_max))
        if res_name is const.RES_SEGMENT:
            self.model_obj = DfaSegment()
        if res_name is const.RES_VLAN:
            self.model_obj = DfaVlan()
        self.model = self.model_obj.get_model()
        if not self.model_obj.is_init_done():
            self._seg_id_allocations()
            self.model_obj.init_done()

    def _allocate_specified_segment(self, session, seg_id, source):
        """Allocate specified segment.

        If segment exists, then try to allocate it and return db object
        If segment does not exists, then try to create it and return db object
        If allocation/creation failed, then return None
        """
        try:
            with session.begin(subtransactions=True):
                alloc = (session.query(self.model).filter_by(
                    segmentation_id=seg_id).first())
                if alloc:
                    if alloc.allocated:
                        # Segment already allocated
                        return
                    else:
                        # Segment not allocated
                        count = (session.query(self.model).
                                 filter_by(allocated=False,
                                           segmentation_id=seg_id).update(
                                               {"allocated": True}))
                        if count:
                            return alloc

                # Segment to create or already allocated
                alloc = self.model(segmentation_id=seg_id,
                                   allocated=True, source=source)
                session.add(alloc)

        except db_exc.DBDuplicateEntry:
            # Segment already allocated (insert failure)
            alloc = None

        return alloc

    def _allocate_segment(self, session, net_id, source):
        """Allocate segment from pool.

        Return allocated db object or None.
        """

        with session.begin(subtransactions=True):
            select = (session.query(self.model).filter_by(
                allocated=False))

            # Selected segment can be allocated before update by someone else,
            # We retry until update success or DB_MAX_RETRIES retries
            for attempt in range(1, DB_MAX_RETRIES + 1):
                alloc = select.first()
                if not alloc:
                    # No resource available
                    return

                count = (session.query(self.model).
                         filter_by(segmentation_id=alloc.segmentation_id,
                         allocated=False).update({"allocated": True,
                                                  "network_id": net_id,
                                                  "source": source}))
                if count:
                    return alloc

        LOG.error('ERROR: Failed to allocate segment.')

    def _reserve_provider_segment(self, session, net_id=None, seg_id=None,
                                  source=None):

        if seg_id is None:
            alloc = self._allocate_segment(session, net_id, source)
            if not alloc:
                LOG.error('ERROR: No segment is available')
                return
        else:
            # TODO net_id not passed here
            alloc = self._allocate_specified_segment(session, seg_id, source)
            if not alloc:
                LOG.error('ERROR: Segmentation_id %s is in use.' % seg_id)
                return

        return alloc.segmentation_id

    def release_segmentation_id(self, seg_id):

        inside = any(lo <= seg_id <= hi for lo, hi in self.seg_id_ranges)
        session = db.get_session()
        with session.begin(subtransactions=True):
            query = session.query(self.model).filter_by(
                segmentation_id=seg_id)
            if inside:
                count = query.update({"allocated": False, "network_id": None,
                                      "source": None})
                if count:
                    LOG.debug("Releasing segmentation id %s to pool" % seg_id)
            else:
                count = query.delete()
                if count:
                    LOG.debug("Releasing segmentation_id %s outside pool" % (
                        seg_id))

        if not count:
            LOG.debug("segmentation_id %s not found" % seg_id)

    # Tested for both Segment and VLAN
    def _seg_id_allocations(self):

        seg_ids = set()
        for seg_id_range in self.seg_id_ranges:
            seg_min, seg_max = seg_id_range
            seg_ids |= set(moves.xrange(seg_min, seg_max + 1))

        session = db.get_session()
        with session.begin(subtransactions=True):
            allocs = (session.query(self.model).all())
            for alloc in allocs:
                try:
                    seg_ids.remove(alloc.segmentation_id)
                except KeyError:
                    # it's not allocatable, so check if its allocated
                    if not alloc.allocated:
                        # it's not, so remove it from table
                        LOG.debug("Removing seg_id %s from pool" %
                                  alloc.segmentation_id)
                        session.delete(alloc)

            for seg_id in sorted(seg_ids):
                alloc = self.model(segmentation_id=seg_id)
                session.add(alloc)

    def get_segid_allocation(self, session, seg_id):
        return (session.query(self.model).filter_by(
            segmentation_id=seg_id).first())

    def allocate_segmentation_id(self, net_id, seg_id=None, source=None):
        session = db.get_session()
        return self._reserve_provider_segment(session, net_id, seg_id=seg_id,
                                              source=source)

    # Tested for clean case
    def get_all_seg_netid(self):
        session = db.get_session()
        netid_dict = {}
        allocs = (session.query(self.model).all())
        for alloc in allocs:
            if alloc.network_id is not None:
                netid_dict[alloc.network_id] = alloc.segmentation_id
        return netid_dict

    def get_seg_netid_src(self, source):
        session = db.get_session()
        netid_dict = {}
        allocs = (session.query(self.model).filter_by(source=source).all())
        for alloc in allocs:
            if alloc.network_id is not None:
                netid_dict[alloc.network_id] = alloc.segmentation_id
        return netid_dict


class DfaNetwork(db.Base):
    """Represents DFA network."""

    __tablename__ = 'networks'

    network_id = sa.Column(sa.String(36), primary_key=True)
    name = sa.Column(sa.String(255))
    config_profile = sa.Column(sa.String(255))
    segmentation_id = sa.Column(sa.Integer)
    tenant_id = sa.Column(sa.String(36))
    fwd_mod = sa.Column(sa.String(16))
    vlan = sa.Column(sa.Integer)
    mob_domain = sa.Column(sa.String(16))
    source = sa.Column(sa.String(16))
    result = sa.Column(sa.String(255))


class DfaTenants(db.Base):
    """Represents DFA tenants."""

    __tablename__ = 'tenants'

    id = sa.Column(sa.String(36), primary_key=True)
    name = sa.Column(sa.String(255), primary_key=True)
    dci_id = sa.Column(sa.Integer)
    result = sa.Column(sa.String(255))


class DfaVmInfo(db.Base):
    """Represents VM info."""

    __tablename__ = 'instances'

    port_id = sa.Column(sa.String(36), primary_key=True)
    name = sa.Column(sa.String(255))
    mac = sa.Column(sa.String(17))
    status = sa.Column(sa.String(8))
    network_id = sa.Column(sa.String(36))
    instance_id = sa.Column(sa.String(36))
    ip = sa.Column(sa.String(16))
    segmentation_id = sa.Column(sa.Integer)
    fwd_mod = sa.Column(sa.String(16))
    gw_mac = sa.Column(sa.String(17))
    host = sa.Column(sa.String(255))
    vdp_vlan = sa.Column(sa.Integer)
    local_vlan = sa.Column(sa.Integer)
    result = sa.Column(sa.String(255))


class DfaAgentsDb(db.Base):
    """Represents DFA agent."""

    __tablename__ = 'agents'

    host = sa.Column(sa.String(255), primary_key=True)
    created = sa.Column(sa.DateTime)
    heartbeat = sa.Column(sa.DateTime)
    configurations = sa.Column(sa.String(4095))


class DfaFwInfo(db.Base):
    """Represents Firewall info."""

    __tablename__ = 'firewall'

    fw_id = sa.Column(sa.String(36), primary_key=True)
    name = sa.Column(sa.String(255))
    tenant_id = sa.Column(sa.String(36))
    in_network_id = sa.Column(sa.String(36))
    in_service_node_ip = sa.Column(sa.String(16))
    out_network_id = sa.Column(sa.String(36))
    out_service_node_ip = sa.Column(sa.String(16))
    router_id = sa.Column(sa.String(36))
    router_net_id = sa.Column(sa.String(36))
    router_subnet_id = sa.Column(sa.String(36))
    fw_mgmt_ip = sa.Column(sa.String(16))
    openstack_provision_status = sa.Column(sa.String(34))
    dcnm_provision_status = sa.Column(sa.String(38))
    device_provision_status = sa.Column(sa.String(30))
    rules = sa.Column(sa.String(RULE_LEN))
    result = sa.Column(sa.String(32))


class DfaLbaaSMapping(db.Base):
    """Represnets tenant to LBaaS box mapping for multiple LBaaS support"""

    __tablename__ = 'lbaas_tenant_box_mapping'
    tenant_id = sa.Column(sa.String(36), sa.ForeignKey("tenants.id"),
                          primary_key=True)
    ip_address = sa.Column(sa.String(64))


class DfaDBMixin(object):

    """Database API."""

    def __init__(self, cfg):
        # Configure database.
        super(DfaDBMixin, self).__init__(cfg)
        db.configure_db(cfg)

    def add_project_db(self, pid, name, dci_id, result):
        proj = DfaTenants(id=pid, name=name, dci_id=dci_id, result=result)
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.add(proj)

    def del_project_db(self, pid):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                ent = session.query(DfaTenants).filter_by(id=pid).one()
                session.delete(ent)
        except orm_exc.NoResultFound:
            LOG.info('Project %(id)s does not exist' % ({'id': pid}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for project %(id)s.' % (
                {'id': pid}))

    def get_project_name(self, pid):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                ent = session.query(DfaTenants).filter_by(id=pid).one()
            # Check with Nader if it's ok to make this change
            # return ent and ent.name
            return ent.name
        except orm_exc.NoResultFound:
            LOG.info('Project %(id)s does not exist' % ({'id': pid}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for project %(id)s.' % (
                {'id': pid}))

    def get_project_id(self, name):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                ent = session.query(DfaTenants).filter_by(name=name).one()
            return ent and ent.id
        except orm_exc.NoResultFound:
            LOG.info('Project %(name)s does not exist' % ({'name': name}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for project %(name)s.' % (
                {'name': name}))

    def get_all_projects(self):
        session = db.get_session()
        with session.begin(subtransactions=True):
            projs = session.query(DfaTenants).all()
        return projs

    def update_project_entry(self, pid, dci_id, result):
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.query(DfaTenants).filter_by(id=pid).update(
                {'result': result, 'dci_id': dci_id})

    def add_network_db(self, net_id, net_data, source, result):
        session = db.get_session()
        with session.begin(subtransactions=True):
            net = DfaNetwork(network_id=net_id,
                             name=net_data.get('name'),
                             config_profile=net_data.get('config_profile'),
                             segmentation_id=net_data.get('segmentation_id'),
                             tenant_id=net_data.get('tenant_id'),
                             fwd_mod=net_data.get('fwd_mod'),
                             vlan=net_data.get('vlan'),
                             mob_domain=net_data.get('mob_domain_name'),
                             source=source,
                             result=result)
            session.add(net)

    def delete_network_db(self, net_id):
        session = db.get_session()
        with session.begin(subtransactions=True):
            net = session.query(DfaNetwork).filter_by(
                network_id=net_id).first()
            if net is not None:
                session.delete(net)

    def get_all_networks(self):
        session = db.get_session()
        with session.begin(subtransactions=True):
            nets = session.query(DfaNetwork).all()
        return nets

    def get_network(self, net_id):
        net = None
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                net = session.query(DfaNetwork).filter_by(
                    network_id=net_id).one()
            return net
        except orm_exc.NoResultFound:
            LOG.info('Network %(id)s does not exist' % ({'id': net_id}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for network %(id)s.' % (
                {'id': net_id}))
        return net

    def get_network_by_name(self, name):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                net = session.query(DfaNetwork).filter_by(name=name).all()
            return net
        except orm_exc.NoResultFound:
            LOG.info('Network %(name)s does not exist' % ({'name': name}))

    def get_network_by_segid(self, segid):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                net = session.query(DfaNetwork).filter_by(
                    segmentation_id=segid).one()
            return net
        except orm_exc.NoResultFound:
            LOG.info('Network %(segid)s does not exist' % ({'segid': segid}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for seg-id %(id)s.' % (
                {'id': segid}))

    def update_network_db(self, net_id, result):
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.query(DfaNetwork).filter_by(
                network_id=net_id).update({"result": result})

    def update_network(self, net_id, **params):
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.query(DfaNetwork).filter_by(
                network_id=net_id).update(params.get('columns'))

    def add_vms_db(self, vm_data, result):
        session = db.get_session()
        with session.begin(subtransactions=True):
            vm = DfaVmInfo(instance_id=vm_data['oui'].get('vm_uuid'),
                           name=vm_data['oui'].get('vm_name'),
                           status=vm_data.get('status'),
                           network_id=vm_data.get('net_uuid'),
                           port_id=vm_data.get('port_uuid'),
                           ip=vm_data['oui'].get('ip_addr'),
                           mac=vm_data.get('vm_mac'),
                           segmentation_id=vm_data.get('segmentation_id'),
                           fwd_mod=vm_data['oui'].get('fwd_mod'),
                           gw_mac=vm_data['oui'].get('gw_mac'),
                           host=vm_data.get('host'),
                           result=result)
            session.add(vm)

    def delete_vm_db(self, port_id):
        session = db.get_session()
        with session.begin(subtransactions=True):
            vm = session.query(DfaVmInfo).filter_by(
                port_id=port_id).first()
            session.delete(vm)

    def update_vm_db(self, vm_port_id, **params):
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.query(DfaVmInfo).filter_by(
                port_id=vm_port_id).update(params.get('columns'))

    def get_vm(self, port_id):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                port = session.query(DfaVmInfo).filter_by(
                    port_id=port_id).one()
            return port
        except orm_exc.NoResultFound:
            LOG.info('Port %(id)s does not exist' % ({'id': port_id}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for Port %(id)s.' % (
                {'id': port_id}))

    def get_vms(self):
        session = db.get_session()
        with session.begin(subtransactions=True):
            vms = session.query(DfaVmInfo).all()
        return vms

    def get_vms_for_this_req(self, **req):
        session = db.get_session()
        with session.begin(subtransactions=True):
            vms = session.query(DfaVmInfo).filter_by(**req).all()
        return vms

    def get_fialed_projects_entries(self, fail_res):
        session = db.get_session()
        with session.begin(subtransactions=True):
            ent = session.query(DfaTenants).filter_by(result=fail_res).all()
        return ent

    def update_agent_db(self, agent_info):
        session = db.get_session()
        host = agent_info.get('host')
        with session.begin(subtransactions=True):
            try:
                # Check if entry exists.
                session.query(DfaAgentsDb).filter_by(host=host).one()

                # Entry exist, only update the heartbeat and configurations.
                session.query(DfaAgentsDb).filter_by(host=host).update(
                    {'heartbeat': agent_info.get('timestamp')})
            except orm_exc.NoResultFound:
                LOG.info('Creating new entry for agent on %(host)s.' % (
                    {'host': host}))
                agent = DfaAgentsDb(host=host,
                                    created=agent_info.get('timestamp'),
                                    heartbeat=agent_info.get('timestamp'),
                                    configurations=agent_info.get('config'))
                session.add(agent)
            except orm_exc.MultipleResultsFound:
                LOG.error('More than one enty found for agent %(host)s.' % (
                    {'host': host}))

    def get_agent_configurations(self, host):
        session = db.get_session()
        with session.begin(subtransactions=True):
            try:
                ent = session.query(DfaAgentsDb).filter_by(host=host).one()
                return ent.configurations
            except orm_exc.NoResultFound:
                LOG.info('Agent %(host)s does not exist.' % ({'host': host}))
            except orm_exc.MultipleResultsFound:
                LOG.error('More than one enty found for agent %(host)s.' % (
                    {'host': host}))

    def update_agent_configurations(self, host, configs):
        session = db.get_session()
        with session.begin(subtransactions=True):
            # Update the configurations.
            return session.query(DfaAgentsDb).filter_by(host=host).update(
                {'configurations': configs})

    def get_str_dict(self, fw_data):
        fw_dict = {}
        fw_dict['firewall_policy_id'] = fw_data.get('firewall_policy_id')
        fw_dict['rules'] = fw_data.get('rules')
        str_dic = json.dumps(fw_dict)
        return str_dic

    def add_fw_db(self, fw_id, fw_data, result=None):
        session = db.get_session()
        rule_str = self.get_str_dict(fw_data)
        if len(rule_str) > RULE_LEN:
            return False
        with session.begin(subtransactions=True):
            fw = DfaFwInfo(fw_id=fw_id,
                           name=fw_data.get('name'),
                           tenant_id=fw_data.get('tenant_id'),
                           in_network_id=fw_data.get('in_network_id'),
                           in_service_node_ip=fw_data.get('in_service_ip'),
                           out_network_id=fw_data.get('out_network_id'),
                           out_service_node_ip=fw_data.get('out_service_ip'),
                           router_id=fw_data.get('router_id'),
                           router_net_id=fw_data.get('router_net_id'),
                           router_subnet_id=fw_data.get('router_subnet_id'),
                           openstack_provision_status=fw_data.get('os_status'),
                           dcnm_provision_status=fw_data.get('dcnm_status'),
                           device_provision_status=fw_data.get('dev_status'),
                           rules=rule_str, result=result)
            session.add(fw)
        return True

    def get_fw_rule_by_id(self, fw_id):
        session = db.get_session()
        rule_dict = {}
        try:
            with session.begin(subtransactions=True):
                fw = session.query(DfaFwInfo).filter_by(fw_id=fw_id).one()
                rule_str = fw.rules
                rule_dict = json.loads(rule_str)
        except orm_exc.NoResultFound:
            LOG.info('FWID %(fwid)s does not exist' % ({'fw_id': fw_id}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for fw-id %(id)s.' % (
                {'id': fw_id}))
        return rule_dict

    def update_fw_db(self, fw_id, fw_data):
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.query(DfaFwInfo).filter_by(fw_id=fw_id).update(
                {'name': fw_data.get('name'),
                 'in_network_id': fw_data.get('in_network_id'),
                 'in_service_node_ip': fw_data.get('in_service_ip'),
                 'out_network_id': fw_data.get('out_network_id'),
                 'out_service_node_ip': fw_data.get('out_service_ip'),
                 'router_id': fw_data.get('router_id'),
                 'router_net_id': fw_data.get('router_net_id'),
                 'router_subnet_id': fw_data.get('router_subnet_id')})

    def update_fw_db_result(self, fw_id, fw_data):
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.query(DfaFwInfo).filter_by(fw_id=fw_id).update(
                {'openstack_provision_status': fw_data.get('os_status'),
                 'dcnm_provision_status': fw_data.get('dcnm_status')})

    # Pass
    def update_fw_db_final_result(self, fw_id, result):
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.query(DfaFwInfo).filter_by(fw_id=fw_id).update(
                {'result': result})

    def append_state_final_result(self, fw_id, cur_res, state):
        final_res = cur_res + '(' + str(state) + ')'
        self.update_fw_db_final_result(fw_id, final_res)

    # Pass
    def update_fw_db_dev_status(self, fw_id, status):
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.query(DfaFwInfo).filter_by(fw_id=fw_id).update(
                {'device_provision_status': status})

    def update_fw_db_mgmt_ip(self, fw_id, mgmt_ip):
        session = db.get_session()
        with session.begin(subtransactions=True):
            session.query(DfaFwInfo).filter_by(fw_id=fw_id).update(
                {'fw_mgmt_ip': mgmt_ip})

    def conv_db_dict(self, alloc):
        fw_dict = dict()
        fw_dict['tenant_id'] = alloc.tenant_id
        fw_dict['in_network_id'] = alloc.in_network_id
        fw_dict['in_service_node_ip'] = alloc.in_service_node_ip
        fw_dict['out_network_id'] = alloc.out_network_id
        fw_dict['out_service_node_ip'] = alloc.out_service_node_ip
        fw_dict['router_id'] = alloc.router_id
        fw_dict['router_net_id'] = alloc.router_net_id
        fw_dict['router_subnet_id'] = alloc.router_subnet_id
        fw_dict['os_status'] = alloc.openstack_provision_status
        fw_dict['dcnm_status'] = alloc.dcnm_provision_status
        fw_dict['device_status'] = alloc.device_provision_status
        fw_dict['name'] = alloc.name
        fw_dict['fw_mgmt_ip'] = alloc.fw_mgmt_ip
        fw_dict['result'] = alloc.result
        fw_dict['fw_id'] = alloc.fw_id
        rule_str = alloc.rules
        rule_dict = json.loads(rule_str)
        fw_dict['rules'] = rule_dict
        return fw_dict

    # Tested with 1 FW
    def get_all_fw_db(self):
        session = db.get_session()
        allocs = (session.query(DfaFwInfo).all())
        fw_ret_dict = dict()
        for alloc in allocs:
            fw_id = alloc.fw_id
            fw_dict = self.conv_db_dict(alloc)
            fw_ret_dict[fw_id] = fw_dict
        return fw_ret_dict

    def get_fw_by_netid(self, netid):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                fw = session.query(DfaFwInfo).filter(
                    (DfaFwInfo.in_network_id == netid) |
                    (DfaFwInfo.out_network_id == netid)).one()
            return fw
        except orm_exc.NoResultFound:
            LOG.info('FW %(netid)s does not exist', ({'netid': netid}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for netid-id %(id)s.', (
                {'id': netid}))
        return None

    def get_fw_by_tenant_id(self, tenant_id):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                fw = session.query(DfaFwInfo).filter(
                    (DfaFwInfo.tenant_id == tenant_id)).one()
                fw_dict = self.conv_db_dict(fw)
            return fw_dict
        except orm_exc.NoResultFound:
            LOG.info('FW %s does not exist' % tenant_id)
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for tenant-id %(id)s.' % (
                {'id': tenant_id}))

    def get_fw_by_rtr_netid(self, netid):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                net = session.query(DfaFwInfo).filter_by(
                    (router_net_id == netid)).one()
            return net
        except orm_exc.NoResultFound:
            LOG.info('Network %(segid)s does not exist' % ({'netid': netid}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for netid-id %(id)s.' % (
                {'id': netid}))

    # Tested
    def get_fw_by_rtrid(self, rtrid):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                rtr = session.query(DfaFwInfo).filter_by(router_id=rtrid)
        except orm_exc.NoResultFound:
            LOG.info('rtr %(rtrid)s does not exist' % ({'rtrid': rtrid}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for rtrid-id %(id)s.' % (
                {'id': rtrid}))
        return rtr

    def delete_fw(self, fw_id):
        session = db.get_session()
        with session.begin(subtransactions=True):
            fw = session.query(DfaFwInfo).filter_by(fw_id=fw_id).first()
            session.delete(fw)

    def get_fw(self, fw_id):
        session = db.get_session()
        fw = None
        try:
            with session.begin(subtransactions=True):
                fw = session.query(DfaFwInfo).filter_by(fw_id=fw_id).first()
                fw_dict = self.conv_db_dict(fw)
        except orm_exc.NoResultFound:
            LOG.info('fw %(fwid)s does not exist' % ({'fw_id': fw_id}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for fwid-id %(id)s.' % (
                {'id': fw_id}))
        return fw, fw_dict

    def clear_fw_entry_by_netid(self, net_id):
        session = db.get_session()
        with session.begin(subtransactions=True):
            fw = session.query(DfaFwInfo).filter_by(in_network_id=net_id).\
                update({'in_network_id': '', 'in_service_node_ip': ''})
            # We don't need to do the below if above succeeds, TODO
            fw = session.query(DfaFwInfo).filter_by(out_network_id=net_id).\
                update({'out_network_id': '',
                        'out_service_node_ip': ''})


class DfaInSubnet(DfaResource):
    dfa_in_subnet_init = 0

    def get_model(cls):
        return DfaInServiceSubnet

    @classmethod
    def init_done(cls):
        cls.dfa_in_subnet_init = cls.dfa_in_subnet_init + 1

    def is_init_done(cls):
        return cls.is_res_init_done(cls.dfa_in_subnet_init)


class DfaOutSubnet(DfaResource):
    dfa_out_subnet_init = 0

    def get_model(cls):
        return DfaOutServiceSubnet

    @classmethod
    def init_done(cls):
        cls.dfa_out_subnet_init = cls.dfa_out_subnet_init + 1

    def is_init_done(cls):
        return cls.is_res_init_done(cls.dfa_out_subnet_init)


class DfasubnetDriver(object):

    # Tested
    def __init__(self, subnet_min_str, subnet_max_str, res_name):
        # Have a check here to ensure a crazy init is not called TODO(padkrish)
        self.subnet_ranges = []
        self.subnet_min = int(netaddr.IPAddress(subnet_min_str.split('/')[0]))
        self.subnet_max = int(netaddr.IPAddress(subnet_max_str.split('/')[0]))
        self.mask = int(subnet_max_str.split('/')[1])
        self.subnet_ranges.append((self.subnet_min, self.subnet_max))
        step = 1 << (32 - self.mask)
        self.step = step
        if res_name is const.RES_IN_SUBNET:
            self.model_obj = DfaInSubnet()
        if res_name is const.RES_OUT_SUBNET:
            self.model_obj = DfaOutSubnet()
        self.model = self.model_obj.get_model()
        if not self.model_obj.is_init_done():
            self._subnet_id_allocations()
            self.model_obj.init_done()

    # Tested
    def _subnet_id_allocations(self):

        subnet_ids = sorted(set(moves.xrange(self.subnet_min, self.subnet_max,
                                             self.step)))
        # seg_ids = set()
        # for subnet_range in self.subnet_ranges:
        #    subnet_min, subnet_max = subnet_range
        #    subnet_ids |= set(moves.xrange(subnet_min, subnet_max, self.step))

        session = db.get_session()
        with session.begin(subtransactions=True):
            allocs = (session.query(self.model).all())
            for alloc in allocs:
                try:
                    ip = int(netaddr.IPAddress(alloc.subnet_address))
                    subnet_ids.remove(ip)
                except KeyError:
                    # it's not allocatable, so check if its allocated
                    if not alloc.allocated:
                        # it's not, so remove it from table
                        LOG.debug("Removing subnet %s from pool" %
                                  alloc.subnet_address)
                        session.delete(alloc)

            for subnet_id in subnet_ids:
                subnet_add = str(netaddr.IPAddress(subnet_id))
                alloc = self.model(subnet_address=subnet_add)
                session.add(alloc)

    # Tested
    def allocate_subnet(self, subnet_lst, net_id=None):
        """Allocate subnet from pool.

        Return allocated db object or None.
        """

        session = db.get_session()
        query_str = None
        for sub in subnet_lst:
            sub_que = (self.model.subnet_address != sub)
            if query_str is not None:
                query_str = query_str & sub_que
            else:
                query_str = sub_que
        with session.begin(subtransactions=True):
            select = (session.query(self.model).filter(
                (self.model.allocated == 0) & query_str))

            # Selected segment can be allocated before update by someone else,
            # We retry until update success or DB_MAX_RETRIES retries
            for attempt in range(1, DB_MAX_RETRIES + 1):
                alloc = select.first()
                if not alloc:
                    # No resource available
                    return
                count = (session.query(self.model).
                         filter_by(subnet_address=alloc.subnet_address,
                         allocated=False).update({"allocated": True,
                                                  "network_id": net_id}))
                if count:
                    return alloc.subnet_address

        LOG.error('ERROR: Failed to allocate segment.')
        return None

    def update_subnet(self, subnet, net_id, subnet_id):
        session = db.get_session()
        with session.begin(subtransactions=True):
            query = session.query(self.model).filter_by(
                subnet_address=subnet).update({"network_id": net_id,
                                               "subnet_id": subnet_id})

    # Tested with a negative case
    def release_subnet(self, subnet_address):

        subnet_addr_int = int(netaddr.IPAddress(subnet_address))
        inside = any(lo <= subnet_addr_int <= hi for lo, hi in
                     self.subnet_ranges)
        session = db.get_session()
        with session.begin(subtransactions=True):
            query = session.query(self.model).filter_by(
                subnet_address=subnet_address)
            if inside:
                count = query.update({"allocated": False, "network_id": None,
                                      "subnet_id": None})
                if count:
                    LOG.debug("Releasing subnet id %s to pool" %
                              subnet_address)
            else:
                count = query.delete()
                if count:
                    LOG.debug("Releasing subnet %s outside pool" % (
                        subnet_address))

        if not count:
            LOG.debug("subnet %s not found" % subnet_address)

    def release_subnet_by_netid(self, netid):

        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                allocs = (session.query(self.model).filter_by(allocated=True,
                          network_id=netid).update({"allocated": False}))
        except orm_exc.NoResultFound:
            LOG.info('Network %(netid)s does not exist' % ({'netid': netid}))

    def release_subnet_no_netid(self):

        net = ''
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                allocs = (session.query(self.model).filter_by(allocated=True,
                                                              network_id=net).
                          update({"allocated": False}))
        except orm_exc.NoResultFound:
            LOG.info('Query failed in release subnet no netid')

    # Tested
    def get_subnet_by_netid(self, netid):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                net = session.query(self.model).filter_by(allocated=True,
                                                          network_id=netid).\
                    one()
            return net.subnet_address
        except orm_exc.NoResultFound:
            LOG.info('Network %(netid)s does not exist', ({'netid': netid}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for netid-id %(id)s.', (
                {'id': netid}))
        return None

    def get_subnet(self, sub):
        session = db.get_session()
        try:
            with session.begin(subtransactions=True):
                net = session.query(self.model).filter_by(allocated=True,
                                                          subnet_address=sub).\
                    one()
            return net
        except orm_exc.NoResultFound:
            LOG.info('subnet %(sub)s does not exist', ({'sub': sub}))
        except orm_exc.MultipleResultsFound:
            LOG.error('More than one enty found for sub %(sub)s.', (
                {'sub': sub}))
        return None


class DfaLBaaSMappingDriver(object):
    def __init__(self, cfg):
        self.model = DfaLbaaSMapping
        db.configure_db(cfg)

    def get_all_lbaas_mapping(self):
        session = db.get_session()
        with session.begin(subtransactions=True):
            allocs = (session.query(self.model).all())
        return allocs

    def add_lbaas_mapping(self, tid, ip):
        session = db.get_session()
        with session.begin(subtransactions=True):
            lbaas_mapping = self.model(tenant_id=tid,
                                       ip_address=ip)
            session.add(lbaas_mapping)

    def delete_lbaas_mapping(self, tid):
        session = db.get_session()
        with session.begin(subtransactions=True):
            row = session.query(self.model).filter_by(
                tenant_id=tid).first()
            session.delete(row)
