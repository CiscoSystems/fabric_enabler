from neutron.plugins.common import constants
try:
    from neutron.db.loadbalancer import loadbalancer_db
except ImportError:
    from neutron_lbaas.db.loadbalancer import loadbalancer_db
from neutron.common import rpc as n_rpc


class LBEnabler():

    def __init__(self, plugin):
        self.plugin = plugin
        self.status = constants.ACTIVE
        self._notifier = n_rpc.get_notifier('network')

    def create_vip(self, context, vip):
        self.plugin.update_status(context, loadbalancer_db.Vip, vip["id"],
                                  self.status)

    def update_vip(self, context, old_vip, vip):
        self.plugin.update_status(context, loadbalancer_db.Vip, old_vip["id"],
                                  self.status)

    def delete_vip(self, context, vip):
        self.plugin._delete_db_vip(context, vip["id"])
        self._notifier.info(context, "enabler_vip_delete", vip)

    def create_pool(self, context, pool):
        self.plugin.update_status(context, loadbalancer_db.Pool,
                                  pool["id"], self.status)

    def update_pool(self, context, old_pool, pool):
        self.plugin.update_status(context, loadbalancer_db.Pool,
                                  old_pool["id"], self.status)

    def delete_pool(self, context, pool):
        self.plugin._delete_db_pool(context, pool["id"])
        self._notifier.info(context, "enabler_pool_delete", pool)

    def create_member(self, context, member):
        self.plugin.update_status(context, loadbalancer_db.Member,
                                  member["id"], self.status)

    def update_member(self, context, old_member, member):
        self.plugin.update_status(context, loadbalancer_db.Member,
                                  old_member["id"], self.status)

    def delete_member(self, context, member):
        self.plugin._delete_db_member(context, member["id"])
        self._notifier.info(context, "enabler_member_delete", member)

    def update_pool_health_monitor(self, context, old_hm, hm, pool_id):
        body = {"pool_id": pool_id, "old_hm": old_hm, "new_hm":  hm}
        self._notifier.info(context, "enabler_pool_hm_update", body)

    def create_pool_health_monitor(self, context, hm, pool_id):
        self.plugin.update_pool_health_monitor(context,
                                               hm['id'],
                                               pool_id,
                                               self.status, "")
        hm["pool_id"] = pool_id
        self._notifier.info(context, "enabler_pool_hm_create", hm)

    def delete_pool_health_monitor(self, context, hm, pool_id):
        self.plugin._delete_db_pool_health_monitor(
                    context, hm["id"],
                    pool_id)
        hm["pool_id"] = pool_id
        self._notifier.info(context, "enabler_pool_hm_delete", hm)
