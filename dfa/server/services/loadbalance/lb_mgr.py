import sys
import netaddr
from dfa.common import dfa_exceptions as dexc
from dfa.common import dfa_logger as logging
from dfa.common import utils
from dfa.db import dfa_db_models as dfa_dbm
from dfa.common import constants as const

LOG = logging.getLogger(__name__)


def lb_import_class(import_str):
    """Returns a class from a string including module and class."""
    mod_str, _sep, class_str = import_str.rpartition('.')
    __import__(mod_str)
    try:
        return getattr(sys.modules[mod_str], class_str)
    except AttributeError:
        errorStr = 'Class %s cannot be found (%s)' % class_str
        LOG.error(errStr)
        raise ImportError(errorStr)


def lb_import_object(import_str, *args, **kwargs):
    """Import a class and return an instance of it."""
    return lb_import_class(import_str)(*args, **kwargs)


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
        self.serv_vlan_min = int(cfg.dcnm.vlan_id_min)
        self.serv_vlan_max = int(cfg.dcnm.vlan_id_max)
        self._lb_net_obj = netaddr.IPNetwork(self._lb_net)
        self._lb_net_gw = str(netaddr.IPAddress(self._lb_net_obj.first + 1))
        self._lb_net_mask = str(netaddr.IPAddress(self._lb_net_obj.netmask))
        self._vlan_db = dfa_dbm.DfaSegmentTypeDriver(self.serv_vlan_min,
                                                     self.serv_vlan_max,
                                                     const.RES_VLAN,
                                                     cfg)
        self._load_mapping_db()
        self._driver_obj = {}
        # creating driving objects, one for each box
        self._box_ip_list = cfg.loadbalance.lb_mgmt_ip.strip().split(',')
        lb_user_name = cfg.loadbalance.lb_user_name
        lb_user_password = cfg.loadbalance.lb_user_password
        lb_f5_interface = cfg.loadbalance.lb_f5_interface
        for box in self._box_ip_list:
            LOG.info("Creating driver obj for ip %s" % box)
            self._driver_obj[box] = lb_import_object(cfg.loadbalance.lb_driver,
                                                     box,
                                                     lb_user_name,
                                                     lb_user_password,
                                                     lb_f5_interface)

    @property
    def dfa_server(self):
        return self._dfa_server

    @property
    def cfg(self):
        return self._cfg

    def _load_mapping_db(self):
        self._mapping_db = dfa_dbm.DfaLBaaSMappingDriver(self._cfg)
        all_mappings = self._mapping_db.get_all_lbaas_mapping()
        self.mapping_dict = {}
        for alloc in all_mappings:
            self.mapping_dict[alloc.tenant_id] = alloc.ip_address
        LOG.info("mapping_dic = %s " % self.mapping_dict)

    def add_mapping(self, tenant_id, box_ip):
        self.mapping_dict[tenant_id] = box_ip
        self._mapping_db.add_lbaas_mapping(tenant_id, box_ip)

    def delete_mapping(self, tenant_id):
        self.mapping_dict.pop(tenant_id)
        self._mapping_db.delete_lbaas_mapping(tenant_id)

    def release_vlan(self, vlan_id):
        self._vlan_db.release_segmentation_id(vlan_id)
        LOG.debug("released vlan %d", vlan_id)

    def get_vlan(self):
        vlan_id = self._vlan_db.allocate_segmentation_id(0)
        LOG.debug("allocated vlan %d", vlan_id)
        return vlan_id

    def service_network_exists(self, tenant_id):
        if tenant_id in self.mapping_dict.keys():
            return self.mapping_dict.get(tenant_id)
        else:
            return None

    def get_driver_obj_from_ip(self, ip_addr):
        if ip_addr in self._driver_obj.keys():
            return self._driver_obj.get(ip_addr)
        else:
            return None

    def get_driver_obj_from_tenant(self, tenant_id):
        ip = self.service_network_exists(tenant_id)
        if ip:
            return self.get_driver_obj_from_ip(ip)
        else:
            return None

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
        profile_name = self._lb_vrf_profile
        tenant_name = self.dfa_server.get_project_name(tenant_id)
        f = self.dfa_server.dcnm_client.get_partition_vrfProf
        current_prof = f(tenant_name)

        if current_prof == profile_name:
            LOG.info("vrf profile for %s is the right one" % tenant_name)
            return
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

    def lb_delete_net(self, net_name, tenant_id):
        net_name_list = net_name.split(self._lb_net_prefix)
        vlan_id = int(net_name_list[1])
        self.release_vlan(vlan_id)
        lb_service = self.get_driver_obj_from_tenant(tenant_id)
        if lb_service:
            LOG.info("calling cleanupF5Netwwork with vlan %s, tenant %s" %
                     (vlan_id, tenant_id))
            lb_service.cleanupF5Network(vlan_id, tenant_id)
            self.delete_mapping(tenant_id)
        else:
            LOG.error("Can not find driver obj for tenant_id %s" % tenant_id)

    def lb_is_internal_nwk(self, net_name):
        if net_name.startswith(self._lb_net_prefix):
            return True
        else:
            return False

    def call_driver(self, function_name, info):
        tenant_id = self.get_tenant_id(info)
        if tenant_id is None:
            LOG.error("tenant_id for this event is None")
            return
        lb_service = self.get_driver_obj_from_tenant(tenant_id)
        if lb_service:
            lb_service.processLbMessage(function_name, info)
        else:
            LOG.error("Can not find driver obj for tenant_id %s, ignoring %s" %
                      (tenant_id, function_name))

    def pool_create_event(self, pool_info):
        function_name = "pool_create_event"
        LOG.info("entering %s, data is %s" % (function_name, pool_info))
        pool = pool_info.get("pool")
        tenant_id = pool.get('tenant_id')
        box_ip = self.service_network_exists(tenant_id)
        if box_ip is None:
            self.change_lbaas_vrf_profile(tenant_id)
            vlan_id = self.create_lbaas_service_network(tenant_id)
            box_ip = self._box_ip_list.pop(0)
            self._box_ip_list = self._box_ip_list + [box_ip]
            self.add_mapping(tenant_id, box_ip)
            lb_service = self.get_driver_obj_from_ip(box_ip)
            LOG.info("calling prepareF5network with vlan_id %d and ip is %s"
                     % (vlan_id, box_ip))
            lb_service.prepareF5ForNetwork(vlan_id,
                                           tenant_id,
                                           self._lb_net_gw,
                                           self._lb_net_mask)

        self.call_driver(function_name, pool_info)

    def get_tenant_id(self, event):
        tenant_id = "tenant_id"
        if tenant_id in event:
            return event.get(tenant_id)
        else:
            for key, item in event.iteritems():
                if isinstance(item, dict):
                    if tenant_id in item:
                        return item.get(tenant_id)
        return None

    def member_create_event(self, member_info):
        function_name = "member_create_event"
        LOG.info("entering %s, data is %s" % (function_name, member_info))
        self.call_driver(function_name, member_info)

    def vip_create_event(self, vip_info):
        function_name = "vip_create_event"
        LOG.info("entering %s, data is %s" % (function_name, vip_info))
        self.call_driver(function_name, vip_info)

    def pool_hm_create_event(self, health_info):
        function_name = "pool_hm_create_event"
        LOG.info("entering %s, data is %s" % (function_name, health_info))
        self.call_driver(function_name, health_info)

    def pool_update_event(self, pool_info):
        function_name = "pool_update_event"
        LOG.info("entering %s, data is %s" % (function_name, pool_info))
        self.call_driver(function_name, pool_info)

    def member_update_event(self, member_info):
        function_name = "member_update_event"
        LOG.info("entering %s, data is %s" % (function_name, member_info))
        self.call_driver(function_name, member_info)

    def vip_update_event(self, vip_info):
        function_name = "vip_update_event"
        LOG.info("entering %s, data is %s" % (function_name, vip_info))
        self.call_driver(function_name, vip_info)

    def pool_hm_update_event(self, health_info):
        function_name = "pool_hm_update_event"
        LOG.info("entering %s, data is %s" % (function_name, health_info))
        self.call_driver(function_name, health_info)

    def pool_delete_event(self, pool_info):
        function_name = "pool_delete_event"
        pool_info["pool_id"] = pool_info["id"]
        LOG.info("entering %s, data is %s" % (function_name, pool_info))
        self.call_driver(function_name, pool_info)

    def member_delete_event(self, member_info):
        function_name = "member_delete_event"
        member_info["member_id"] = member_info["id"]
        LOG.info("entering %s, data is %s" % (function_name, member_info))
        self.call_driver(function_name, member_info)

    def vip_delete_event(self, vip_info):
        function_name = "vip_delete_event"
        vip_info["vip_id"] = vip_info["id"]
        LOG.info("entering %s, data is %s" % (function_name, vip_info))
        self.call_driver(function_name, vip_info)

    def pool_hm_delete_event(self, health_info):
        function_name = "pool_hm_delete_event"
        LOG.info("entering %s, data is %s" % (function_name, health_info))
        self.call_driver(function_name, health_info)
