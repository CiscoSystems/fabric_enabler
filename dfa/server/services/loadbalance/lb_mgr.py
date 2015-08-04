import netaddr
from dfa.common import dfa_exceptions as dexc
from dfa.common import dfa_logger as logging
from dfa.common import utils

LOG = logging.getLogger(__name__)


class LbMgr(object):

    """LBaaS manager"""
    def __init__(self, cfg, server):
        self._base_ver = '1.0'
        self._cfg = cfg
        self._dfa_server = server
        self._lb_net_prefix = cfg.loadbalance.lb_svc_net_name_prefix
        self._lb_net = cfg.loadbalance.lb_svc_net
        self._lb_vrf_profile = cfg.loadbalance.lb_vrf_profile
        self._lb_service_net_profile = cfg.loadbalance.lb_svc_net_profile
        self._lb_vlan_min = int(cfg.loadbalance.lb_service_vlan_id_min)
        self._lb_vlan_max = int(cfg.loadbalance.lb_service_vlan_id_max)
        self._lb_net_obj = netaddr.IPNetwork(self._lb_net)
        self._lb_net_gw = str(netaddr.IPAddress(self._lb_net_obj.first + 1))

    @property
    def dfa_server(self):
        return self._dfa_server

    @property
    def cfg(self):
        return self._cfg

    def release_vlan(self, vlan_id):
        LOG.debug("released vlan %d", vlan_id)

    def get_vlan(self):
        vlan_id = 600
        LOG.debug("allocated vlan %d", vlan_id)
        return vlan_id

    def service_network_exists(self, tenant_id):
        allnet = self.dfa_server.network
        for net in allnet:
            if (allnet[net].get('tenant_id') == tenant_id and
                    allnet[net].get('name').startswith(self._lb_net_prefix)):
                LOG.debug("internal network exists for tenant %s" % tenant_id)
                return True
        return False

    def create_lbaas_service_network(self, tenant_id):
        vlan = self.get_vlan()
        if vlan == 0:
            LOG.error("No more vlan is available, LBaaS may stop working")
            return 0
        updated_net_name = self._lb_net_prefix+str(vlan)
        body = {'network': {'name': updated_net_name,
                            'tenant_id': tenant_id,
                            'admin_state_up': True}}
        lb_internal_net = (self.dfa_server.neutronclient.
                           create_network(body=body).get('network'))
        net_id = lb_internal_net.get('id')
        LOG.debug("created lbaas internal network on openstack %s, uuid is %s"
                  % (updated_net_name, net_id))
        try:
            subnet_body = {'subnet': {'cidr': self._lb_net,
                                      'ip_version': 4,
                                      'network_id': net_id,
                                      'gateway_ip': self._lb_net_gw,
                                      'tenant_id': tenant_id,
                                      'enable_dhcp': False}}
            lb_internal_subnet = (self.dfa_server.neutronclient.
                                  create_subnet(body=subnet_body).
                                  get('subnet'))

            LOG.debug("created lbaas internal subnet on openstack %s"
                      % lb_internal_subnet)
        except Exception as e:
            LOG.error('Fail to create  subnet %(subnet)s on openstack '
                      'request. Error %(err)s' % (
                          {'subnet': subnet_body['subnet'], 'err': str(e)}))
            self.dfa_server.neutronclient.delete_network(net_id)
            self.release_vlan(vlan)
            return 0
        return vlan

    def change_lbaas_vrf_profile(self, tenant_id):
        profile_name = self._lb_service_net_profile
        tenant_name = self.dfa_server.get_project_name(tenant_id)
        try:
            self.dfa_server.dcnm_client.update_project(
                    org_name=tenant_name,
                    part_name=None,
                    dci_id=0,
                    vrf_prof=profile_name)
        except dexc.DfaClientRequestFailed:
            LOG.error("Failed to update project %s on DCNM.", tenant_name)
        return

    def lb_create_net_dcnm(self, tenant_name, net_name, net, snet):
        net_name_list = net_name.split(self._lb_net_prefix)
        vlan_id = int(net_name_list[1])
        net["vlan_id"] = vlan_id
        net["config_profile"] = self._lb_service_net_profile
        subnet = utils.dict_to_obj(snet)
        dcnm_net = utils.dict_to_obj(net)
        self.dfa_server.dcnm_client.create_service_network(
                            tenant_name, dcnm_net, subnet, dhcp_range=False)

    def lb_delete_net(self, net_name):
        net_name_list = net_name.split(self._lb_net_prefix)
        vlan_id = int(net_name_list[1])
        self.release_vlan(vlan_id)

    def lb_is_internal_nwk(self, net_name):
        if net_name.startswith(self._lb_net_prefix):
            return True
        else:
            return False

    def pool_create_event(self, pool_info):
        function_name = "pool_create_event"
        LOG.info("entering %s, data is %s" % (function_name, pool_info))
        tenant_id = pool_info.get('tenant_id')
        if self.service_network_exists(tenant_id) is False:
            self.change_lbaas_vrf_profile(tenant_id)
            vlan_id = self.create_lbaas_service_network(tenant_id)
#            self.lb_service.prepareF5ForNetwork(vlan_id,
#                                            tenant_id, self._lb_net)
#        self.lb_service.processLbMessage(function_name, pool_info)

    def member_create_event(self, member_info):
        function_name = "member_create_event"
        LOG.info("entering %s, data is %s" % (function_name, member_info))
#        self.lb_service.processLbMessage(function_name, member_info)

    def vip_create_event(self, vip_info):
        function_name = "vip_create_event"
        LOG.info("entering %s, data is %s" % (function_name, vip_info))
#        self.lb_service.processLbMessage(function_name, vip_info)

    def hm_create_event(self, health_info):
        function_name = "hm_create_event"
        LOG.info("entering %s, data is %s" % (function_name, health_info))
#        self.lb_service.processLbMessage(function_name, health_info)

    def pool_update_event(self, pool_info):
        function_name = "pool_update_event"
        LOG.info("entering %s, data is %s" % (function_name, pool_info))
#        self.lb_service.processLbMessage(function_name, pool_info)

    def member_update_event(self, member_info):
        function_name = "member_update_event"
        LOG.info("entering %s, data is %s" % (function_name, member_info))
#        self.lb_service.processLbMessage(function_name, member_info)

    def vip_update_event(self, vip_info):
        function_name = "vip_updateevent"
        LOG.info("entering %s, data is %s" % (function_name, vip_info))
#        self.lb_service.processLbMessage(function_name, vip_info)

    def hm_update_event(self, health_info):
        function_name = "hm_update_event"
        LOG.info("entering %s, data is %s" % (function_name, health_info))
#        self.lb_service.processLbMessage(function_name, health_info)

    def pool_delete_event(self, pool_info):
        function_name = "pool_delete_event"
        LOG.info("entering %s, data is %s" % (function_name, pool_info))
#        self.lb_service.processLbMessage(function_name, pool_info)

    def member_delete_event(self, member_info):
        function_name = "member_delete_event"
        LOG.info("entering %s, data is %s" % (function_name, member_info))
#        self.lb_service.processLbMessage(function_name, member_info)

    def vip_delete_event(self, vip_info):
        function_name = "vip_delete_event"
        LOG.info("entering %s, data is %s" % (function_name, vip_info))
#        self.lb_service.processLbMessage(function_name, vip_info)

    def hm_delete_event(self, health_info):
        function_name = "hm_delete_event"
        LOG.info("entering %s, data is %s" % (function_name, health_info))
#        self.lb_service.processLbMessage(function_name, health_info)
