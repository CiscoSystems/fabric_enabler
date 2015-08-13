from neutron.plugins.common import constants
from neutron.db.loadbalancer import loadbalancer_db


class LBnoop():

    def __init__(self, plugin):
        self.plugin = plugin
        self.status = constants.ACTIVE

    def create_vip(self, context, vip):
        self.plugin.update_status(context, loadbalancer_db.Vip, vip["id"],
                                  self.status)

    def update_vip(self, context, old_vip, vip):
        self.plugin.update_status(context, loadbalancer_db.Vip, old_vip["id"],
                                  self.status)

    def delete_vip(self, context, vip):
        self.plugin._delete_db_vip(context, vip["id"])

    def create_pool(self, context, pool):
        self.plugin.update_status(context, loadbalancer_db.Pool,
                                  pool["id"], self.status)

    def update_pool(self, context, old_pool, pool):
        self.plugin.update_status(context, loadbalancer_db.Pool,
                                  old_pool["id"], self.status)

    def delete_pool(self, context, pool):
        self.plugin._delete_db_pool(context, pool["id"])

    def create_member(self, context, member):
        self.plugin.update_status(context, loadbalancer_db.Member,
                                  member["id"], self.status)

    def update_member(self, context, old_member, member):
        self.plugin.update_status(context, loadbalancer_db.Member,
                                  old_member["id"], self.status)

    def delete_member(self, context, member):
        self.plugin._delete_db_member(context, member["id"])

    def update_pool_health_monitor(self, context, old_hm, hm, pool_id):
        self.plugin.update_pool_health_monitor(context,
                                               old_hm['id'],
                                               pool_id,
                                               self.status, "")

    def create_pool_health_monitor(self, context, hm, pool_id):
        self.plugin.update_pool_health_monitor(context,
                                               hm['id'],
                                               pool_id,
                                               self.status, "")
        hm["cisco_added_pool_id"] = pool_id
        hm["cisco_added_hm_id"] = hm["id"]

    def delete_pool_health_monitor(self, context, hm, pool_id):
        self.plugin._delete_db_pool_health_monitor(
                    context, hm["id"],
                    pool_id)
        hm["cisco_added_pool_id"] = pool_id
        hm["cisco_added_hm_id"] = hm["id"]
