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


import sqlalchemy as sa
import sqlalchemy.orm.exc as orm_exc

from oslo.db import exception as db_exc
from six import moves

from dfa.common import dfa_logger as logging
import dfa_db_api as db

LOG = logging.getLogger(__name__)

DB_MAX_RETRIES = 10


class DfaSegmentatationId(db.Base):
    """Represents DFA segmentation ID."""

    __tablename__ = 'segmentation_id'

    segmentation_id = sa.Column(sa.Integer, nullable=False, primary_key=True,
                                autoincrement=False)
    allocated = sa.Column(sa.Boolean, nullable=False, default=False)


class DfaSegmentTypeDriver(object):

    def __init__(self, segid_min, segid_max):
        self.seg_id_ranges = []
        self.seg_id_ranges.append((segid_min, segid_max))
        self._seg_id_allocations()

    def _allocate_specified_segment(self, session, seg_id):
        """Allocate specified segment.

        If segment exists, then try to allocate it and return db object
        If segment does not exists, then try to create it and return db object
        If allocation/creation failed, then return None
        """
        try:
            with session.begin(subtransactions=True):
                alloc = (session.query(DfaSegmentatationId).filter_by(
                    segmentation_id=seg_id).first())
                if alloc:
                    if alloc.allocated:
                        # Segment already allocated
                        return
                    else:
                        # Segment not allocated
                        count = (session.query(DfaSegmentatationId).
                                 filter_by(allocated=False,
                                           segmentation_id=seg_id).update(
                                               {"allocated": True}))
                        if count:
                            return alloc

                # Segment to create or already allocated
                alloc = DfaSegmentatationId(segmentation_id=seg_id,
                                            allocated=True)
                session.add(alloc)

        except db_exc.DBDuplicateEntry:
            # Segment already allocated (insert failure)
            alloc = None

        return alloc

    def _allocate_segment(self, session):
        """Allocate segment from pool.

        Return allocated db object or None.
        """

        with session.begin(subtransactions=True):
            select = (session.query(DfaSegmentatationId).filter_by(
                allocated=False))

            # Selected segment can be allocated before update by someone else,
            # We retry until update success or DB_MAX_RETRIES retries
            for attempt in range(1, DB_MAX_RETRIES + 1):
                alloc = select.first()
                if not alloc:
                    # No resource available
                    return

                count = (session.query(DfaSegmentatationId).
                         filter_by(segmentation_id=alloc.segmentation_id,
                         allocated=False).update({"allocated": True}))
                if count:
                    return alloc

        LOG.error('ERROR: Failed to allocate segment.')

    def _reserve_provider_segment(self, session, seg_id=None):

        if seg_id is None:
            alloc = self._allocate_segment(session)
            if not alloc:
                LOG.error('ERROR: No segment is available')
                return
        else:
            alloc = self._allocate_specified_segment(session, seg_id)
            if not alloc:
                LOG.error('ERROR: Segmentation_id %s is in use.' % seg_id)
                return

        return alloc.segmentation_id

    def release_segmentation_id(self, seg_id):

        inside = any(lo <= seg_id <= hi for lo, hi in self.seg_id_ranges)
        session = db.get_session()
        with session.begin(subtransactions=True):
            query = session.query(DfaSegmentatationId).filter_by(
                segmentation_id=seg_id)
            if inside:
                count = query.update({"allocated": False})
                if count:
                    LOG.debug("Releasing segmentation id %s to pool" % seg_id)
            else:
                count = query.delete()
                if count:
                    LOG.debug("Releasing segmentation_id %s outside pool" % (
                        seg_id))

        if not count:
            LOG.debug("segmentation_id %s not found" % seg_id)

    def _seg_id_allocations(self):

        seg_ids = set()
        for seg_id_range in self.seg_id_ranges:
            seg_min, seg_max = seg_id_range
            seg_ids |= set(moves.xrange(seg_min, seg_max + 1))

        session = db.get_session()
        with session.begin(subtransactions=True):
            allocs = (session.query(DfaSegmentatationId).all())
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
                alloc = DfaSegmentatationId(segmentation_id=seg_id)
                session.add(alloc)

    def get_segid_allocation(self, session, seg_id):
        return (session.query(DfaSegmentatationId).filter_by(
            segmentation_id=seg_id).first())

    def allocate_segmentation_id(self, seg_id=None):
        session = db.get_session()
        return self._reserve_provider_segment(session, seg_id=seg_id)


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
    source = sa.Column(sa.String(16))
    result = sa.Column(sa.String(16))


class DfaTenants(db.Base):
    """Represents DFA tenants."""

    __tablename__ = 'tenants'

    id = sa.Column(sa.String(36), primary_key=True)
    name = sa.Column(sa.String(255), primary_key=True)
    dci_id = sa.Column(sa.Integer)
    result = sa.Column(sa.String(16))


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
    result = sa.Column(sa.String(16))


class DfaAgentsDb(db.Base):
    """Represents DFA agent."""

    __tablename__ = 'agents'

    host = sa.Column(sa.String(255), primary_key=True)
    created = sa.Column(sa.DateTime)
    heartbeat = sa.Column(sa.DateTime)
    configurations = sa.Column(sa.String(4095))


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
            return ent and ent.name
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
                             source=source,
                             result=result)
            session.add(net)

    def delete_network_db(self, net_id):
        session = db.get_session()
        with session.begin(subtransactions=True):
            net = session.query(DfaNetwork).filter_by(
                network_id=net_id).first()
            session.delete(net)

    def get_all_networks(self):
        session = db.get_session()
        with session.begin(subtransactions=True):
            nets = session.query(DfaNetwork).all()
        return nets

    def get_network(self, net_id):
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

    def delete_vm_db(self, vm_uuid):
        session = db.get_session()
        with session.begin(subtransactions=True):
            vm = session.query(DfaVmInfo).filter_by(
                instance_id=vm_uuid).first()
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
