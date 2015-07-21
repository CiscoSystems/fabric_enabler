from dfa.common import dfa_logger as logging
from dfa.server import dfa_events_handler as deh

LOG = logging.getLogger(__name__)


class DfaNeutronHelper(object):

    ''' Helper Routines for Neutron'''

    def __init__(self):
        self.neutron_help = deh.EventsHandler('neutron', None, 20, 25)

    @property
    def neutronclient(self):
        return self.neutron_help.nclient

    def create_network(self, name, tenant_id, subnet):

        try:
            body = {'network': {'name': name, 'tenant_id': tenant_id,
                                'admin_state_up': True}}
            netw = self.neutronclient.create_network(body=body)
            net_dict = netw.get('network')
            net_id = net_dict.get('id')
        except Exception as e:
            LOG.error("Failed to create network %s" % name)
            return None, None

        try:
            body = {'subnet': {'cidr': subnet,
                               'ip_version': 4,
                               'network_id': net_id,
                               'tenant_id': tenant_id,
                               'enable_dhcp': False}}
            subnet_ret = self.neutronclient.create_subnet(body=body)
            subnet_dict = subnet_ret.get('subnet')
            subnet_id = subnet_dict.get('id')
        except Exception as e:
            LOG.error("Failed to create subnet %s" % subnet)
            try:
                net_ret = self.neutronclient.delete_network(net_id)
            except Exception as e:
                LOG.error("Failed to delete network %s" % net_id)
            return None, None
        return net_id, subnet_id

    def delete_network(self, name, tenant_id, subnet_id, net_id):

        try:
            subnet_ret = self.neutronclient.delete_subnet(subnet_id)
        except Exception as e:
            LOG.error("Failed to create subnet %s ret %s" % (subnet_id,
                                                             subnet_ret))
            return

        try:
            net_ret = self.neutronclient.delete_network(net_id)
        except Exception as e:
            LOG.error("Failed to delete network %s ret %s" % (name, net_ret))

    # Pass
    def delete_network_all_subnets(self, net_id):
        try:
            body = {'network_id': net_id}
            subnet_list = self.neutronclient.list_subnets(body=body)
            subnet_list = subnet_list.get('subnets')
            for subnet in subnet_list:
                if subnet.get('network_id') == net_id:
                    subnet_id = subnet.get('id')
                    subnet_ret = self.neutronclient.delete_subnet(subnet_id)
        except Exception as e:
            LOG.error("Failed to create subnet for net %s" % net_id)
            return False
        try:
            net_ret = self.neutronclient.delete_network(net_id)
        except Exception as e:
            LOG.error("Failed to delete network %s ret %s" % (name, net_ret))
        return True

    def is_subnet_present(self, subnet_addr):
        try:
            body = {}
            subnet_list = self.neutronclient.list_subnets(body=body)
            subnet_dat = subnet_list.get('subnets')
            for sub in subnet_dat:
                if sub.get('cidr') == subnet_addr:
                    return True
            return False
        except Exception as e:
            LOG.error("Failed to list subnet ret %s" % subnet_ret)
            return False

    def delete_network_subname(self, sub_name):
        try:
            body = {}
            net_list = self.neutronclient.list_networks(body=body)
            for net in net_list:
                if net.get('name').find(sub_name) != -1:
                    self.delete_all_subnets(net.get('net_id'))
            return net_list
        except Exception as e:
            LOG.error("Failed to get network by name")
            return None

    # Tested
    def get_network_by_name(self, nwk_name):
        ret_net_lst = []
        try:
            body = {}
            net_list = self.neutronclient.list_networks(body=body)
            net_list = net_list.get('networks')
            for net in net_list:
                if net.get('name') == nwk_name:
                    ret_net_lst.append(net)
        except Exception as e:
            LOG.error("Failed to get network by name")
        return ret_net_lst

    # Tested
    def get_rtr_by_name(self, rtr_name):
        upd_rtr_list = []
        try:
            rtr_list = self.neutronclient.list_routers()
            rtr_list = rtr_list.get('routers')
            for rtr in rtr_list:
                if rtr_name == rtr['name']:
                    upd_rtr_list.append(rtr)
        except Exception as e:
            LOG.error("Failed to get router by name")
        return upd_rtr_list

    def create_router(self, name, tenant_id, subnet_id):
        try:
            body = {'router': {'name': name, 'tenant_id': tenant_id,
                               'admin_state_up': True}}
            routw = self.neutronclient.create_router(body=body)
            rout_dict = routw.get('router')
            rout_id = rout_dict.get('id')
        except Exception as e:
            LOG.error("Failed to create router %s" % name)
            return None

        try:
            body = {'subnet_id': subnet_id}
            intf = self.neutronclient.add_interface_router(rout_id, body=body)
            intf_dict = intf.get('port_id')
        except Exception as e:
            LOG.error("Failed to create router interface %s" % name)
            try:
                ret = self.neutronclient.delete_router(rout_id)
            except Exception as e:
                LOG.error("Failed to delete router %s" % name)
            return None
        return rout_id

    # Passed
    def delete_router(self, name, tenant_id, rout_id, subnet_id):
        try:
            body = {'subnet_id': subnet_id}
            intf = self.neutronclient.remove_interface_router(rout_id,
                                                              body=body)
            intf_dict = intf.get('id')
        except Exception as e:
            LOG.error("Failed to delete router interface %s" % name)
            return False

        try:
            ret = self.neutronclient.delete_router(rout_id)
        except Exception as e:
            LOG.error("Failed to delete router %s %s" % (name, ret))
            return False
        return True

    def get_router_intf(self, router_id):
        try:
            body = {}
            rout = self.neutronclient.show_router(router_id, body=body)
        except Exception as e:
            LOG.error("Failed to show router interface %s" % name)
            return
        # Complete TODO

    def get_fw(self, fw_id):
        fw = None
        try:
            fw = self.neutronclient.show_firewall(fw_id)
        except Exception as e:
            LOG.error("Failed to get firewall list")
        return fw

    # Tested
    def get_fw_rule(self, rule_id):
        rule = None
        try:
            rule = self.neutronclient.show_firewall_rule(rule_id)
        except Exception as e:
            LOG.error("Failed to get firewall rule")
        return rule

    # Tested
    def get_fw_policy(self, policy_id):
        policy = None
        try:
            policy = self.neutronclient.show_firewall_policy(policy_id)
        except Exception as e:
            LOG.error("Failed to get firewall policy")
        return policy
