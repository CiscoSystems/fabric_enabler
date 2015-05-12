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
            return None
        return rout_id

    def delete_router(self, name, tenant_id, subnet_id, rout_id):
        try:
            body = {'subnet_id': subnet_id}
            intf = self.neutronclient.remove_interface_router(rout_id,
                                                              body=body)
            intf_dict = intf.get('id')
        except Exception as e:
            LOG.error("Failed to delete router interface %s" % name)
            return

        try:
            ret = self.neutronclient.delete_router(rout_id)
        except Exception as e:
            LOG.error("Failed to delete router %s %s" % (name, ret))
            return
