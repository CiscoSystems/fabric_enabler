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

"""
This file provides a wrapper to novaclient API, for getting the instance's
information such as display_name.
"""

from keystoneclient.v2_0 import client as keyc
from novaclient import exceptions as nexc
from novaclient.v1_1 import client as nova_client

from dfa.common import config
from dfa.common import dfa_logger as logging


LOG = logging.getLogger(__name__)


class DFAInstanceAPI(object):

    """This class provides API to get information for a given instance."""

    def __init__(self):
        self._cfg = config.CiscoDFAConfig('nova').cfg
        self._tenant_name = self._cfg.keystone_authtoken.admin_tenant_name
        self._user_name = self._cfg.keystone_authtoken.admin_user
        self._admin_password = self._cfg.keystone_authtoken.admin_password
        self._timeout_respoonse = 10
        self._token = None
        self._project_id = None
        self._auth_url = None
        self._token_id = None
        self._token = None
        self._novaclnt = None
        self._url = self._cfg.keystone_authtoken.auth_uri
        if not self._url:
            proto = self._cfg.keystone_authtoken.auth_protocol
            auth_host = self._cfg.keystone_authtoken.auth_host
            auth_port = self._cfg.keystone_authtoken.auth_port
            self._url = '%(proto)s://%(host)s:%(port)s/v2.0' % (
                {'proto': proto if proto else 'http',
                 'host': auth_host if auth_host else 'localhost',
                 'port': auth_port if auth_port else '5000'})
        else:
            url_s = self._url.split('/')
            if len(url_s) == 4:
                url_s[-1] = 'v2.0'
                self._url = '/'.join(url_s)
            elif len(url_s) == 3:
                self._url += '/v2.0'
            else:
                LOG.error('Invalid auth_uri=%s value.', self._url)

        self._inst_info_cache = {}
        LOG.debug('DFAInstanceAPI: initialization done...')

    def _create_token(self):
        """Create new token for using novaclient API."""
        ks = keyc.Client(username=self._user_name,
                         password=self._admin_password,
                         tenant_name=self._tenant_name,
                         auth_url=self._url)
        result = ks.authenticate()
        if result:
            access = ks.auth_ref
            token = access.get('token')
            self._token_id = token['id']
            self._project_id = token['tenant'].get('id')
            service_catalog = access.get('serviceCatalog')
            for sc in service_catalog:
                if sc['type'] == "compute" and sc['name'] == 'nova':
                    endpoints = sc['endpoints']
                    for endp in endpoints:
                        self._auth_url = endp['adminURL']
            LOG.info('_create_token: token = %s' % token)

            # Create nova clinet.
            self._novaclnt = self._create_nova_client()

            return token

        else:
            # Failed request.
            LOG.error('Failed to send token create request.')
            return

    def _create_nova_client(self):
        """Creates nova client object."""
        try:
            clnt = nova_client.Client(self._user_name,
                                      self._token_id,
                                      self._project_id,
                                      self._auth_url,
                                      insecure=False,
                                      cacert=None)
            clnt.client.auth_token = self._token_id
            clnt.client.management_url = self._auth_url
            return clnt
        except nexc.Unauthorized:
            thismsg = ('Failed to get novaclient:Unauthorised '
                       'project_id=%s user=%s') % (self._project_id,
                                                   self._user_name)
            raise nexc.ClientException(thismsg)

        except nexc.AuthorizationFailure as err:
            raise nexc.ClientException("Failed to get novaclient %s" % (err))

    def _get_instances_for_project(self, project_id):
        """Return all instances for a given project.

        :project_id: UUID of project (tenant)
        """
        search_opts = {'marker': None,
                       'all_tenants': True,
                       'project_id': project_id}
        self._create_token()
        try:
            servers = self._novaclnt.servers.list(True, search_opts)
            LOG.debug('_get_instances_for_project: servers=%s' % servers)
            return servers
        except nexc.Unauthorized:
            emsg = (('Failed to get novaclient:Unauthorised '
                    'project_id=%(proj)s user=%(user)s') %
                    {'proj': self._project_id, 'user': self._user_name})
            LOG.exception(emsg)
            raise nexc.ClientException(emsg)
        except nexc.AuthorizationFailure as err:
            emsg = ("Failed to get novaclient %s")
            LOG.exception(emsg % err)
            raise nexc.ClientException(emsg % err)

    def get_instance_for_uuid(self, uuid, project_id):
        """Return instance name for given uuid of an instance and project.

        :uuid: Instance's UUID
        :project_id: UUID of project (tenant)
        """
        instance_name = self._inst_info_cache.get((uuid, project_id))
        if instance_name:
            return instance_name
        instances = self._get_instances_for_project(project_id)
        for inst in instances:
            if inst.id.replace('-', '') == uuid:
                LOG.debug('get_instance_for_uuid: name=%s' % inst.name)
                instance_name = inst.name
                self._inst_info_cache[(uuid, project_id)] = instance_name
                return instance_name
        return instance_name
