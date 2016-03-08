import F5BigIp
import urllib
import socket
import struct
import pexpect
import json
import time
from dfa.common import dfa_logger as logging

LOG = logging.getLogger(__name__)

class F5Device(object):
    def __init__(self, f5IpAddr, username, password, fabricIf):
        self.username = username
        self.password = password
        self.f5IpAddr = f5IpAddr
        self.fabricIf = fabricIf
        self.initF5Connection(f5IpAddr, username, password)

    def initF5Connection(self, f5hostname, username, password):
        self.big = F5BigIp.F5Device(f5hostname, username, password)
        """
        TODO ENFORCE VERSIONS HERE and DISCONNECT if Not SUPPORTED VERSION
        """

    def getSubnet(self, gateway_ip, mask):
        lmask = struct.unpack('!L', socket.inet_aton(mask))[0]
        lsip = struct.unpack('!L', socket.inet_aton(gateway_ip))[0]
        lsmask = lsip & lmask
        sNet = socket.inet_ntoa(struct.pack("!L", lsmask))
        return sNet

    def allocateSelfIpAddress(self, gateway_ip, mask):
        lmask = struct.unpack('!L', socket.inet_aton(mask))[0]
        lsip = struct.unpack('!L', socket.inet_aton(gateway_ip))[0]
        
        shifter = 0
        mask_len = 0
        while ((lmask & (0x1 << shifter)) == 0):
            shifter = shifter + 1
        mask_len = shifter
        lfree_ip = (lsip & lmask) + ((1 << mask_len) - 2)
        free_ip = socket.inet_ntoa(struct.pack("!L", lfree_ip))
        return free_ip


    def cleanupF5Network(self, vlanid, context):
        context = 'uuid_' + context
        big = self.big
        vlanName = 'uuid_vlan' + str(vlanid)
        big.network.routeDel("defaultRoute_" + str(vlanid), context)
        big.network.deleteSelfIp('selfIp'+str(vlanid), folder=context)
        big.network.deleteVlan(vlanName, context)
        big.network.deleteRouteDomain(context) 
        big.deleteFolder(context) 

    def prepareF5ForNetwork(self, vlanid, context, gateway_ip,second_gw, mask):
        context = 'uuid_' + context
        big = self.big
        vlanName = 'uuid_vlan' + str(vlanid) 

        try:
            if ((big.folderExists(context) == False) and  \
                (big.createFolder(context) == False)):
                print("Error Creating Partition on F5")
                return False
        except F5BigIp.SystemException as fexc:
            print("Error Creating Partition ", fexc.message)
            return False

        try:
            rid = big.network.routeDomainAdd(context, 'ospfv2');
            if (rid <= 0):
                print("Error Creating Partition and domain on F5")
                return False
        except F5BigIp.RouteAddException as rexc:
            print("Error Adding a Route Domain", rexc.message)
            big.deleteFolder(context)
            return False 

        if (rid == 0):
            return False

        try:
            if (big.network.createVlan(vlanName, vlanid, self.fabricIf,  \
                              context, "Vlan for Tenant" + context) == False):
                return False
        except F5BigIp.VLANCreateException as vexc:
            print("Error Creaing Vlan ", vlanName, vexc.message)
            big.delete_folder_and_domain(context, big)
            return False
        
        selfIpAddres = self.allocateSelfIpAddress(gateway_ip, mask)
        selfIpAddres = selfIpAddres + "%" + str(rid)
        print("Self IP address is ", selfIpAddres)

        big.network.createSelfIp('selfIp'+str(vlanid), selfIpAddres, mask, vlanName, \
                            folder=context)

        big.network.routeAdd("defaultRoute_" + str(vlanid), "0.0.0.0%" + str(rid), 
                           "0.0.0.0", gateway_ip + "%" + str(rid), context)

        ospfNet = self.getSubnet(gateway_ip, mask)
        big.network.startOspf(rid, ospfNet, mask)
        print("Called Apply F5 Netowrk config")
        return True

    """
    POOOL CREATION MESSAGE
    {"pool": {"status": "pending_create", "lb_method": "round_robin", "protocol": "tcp", "description": "", "health_monitors": [], "members": [], "status_description": null, "id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15", "vip_id": null, "name": "waxu_pool3", "admin_state_up": true, "subnet_id": "0efe5f1c-22e3-4704-9713-558372214aa3", "tenant_id": "b8560134d1c24305a06274214d7cf481", "health_monitors_status": [], "provider": "f5"}} 
    """
    def createPool(self, jsMsg):

        pool = jsMsg.get('pool')

        tenant_id = pool.get('tenant_id')
        partition = "uuid_" + tenant_id
        poolName = "uuid_" + pool.get('id')
        lbMethod = pool.get('lb_method')
        description=pool.get('description')
        self.big.ltm.createPool(poolName, lbMethod, description, partition)
        members = pool.get('members')
        """
            TBD decide on Members addition, during pool creation
        """ 
        for member in members:
            print("Member is ", member)

    """
    {"pool_id": "80064138-7dd2-4fc2-a4ff-92035c672a27"}
    """
    def deletePool(self, pool):
        poolName = 'uuid_' + pool.get('pool_id')
        partition = 'uuid_' + pool.get('tenant_id')
        
        """ 
            Cleanup all the members in the pool
        """
        self.memberDelete(pool)

        self.big.ltm.deletePool(poolName, partition)
        
    """
    {"member": {"status": "PENDING_CREATE", "protocol_port": 23, "weight": 1, "admin_state_up": true, "tenant_id": "b8560134d1c24305a06274214d7cf481", "pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15", "address": "1.1.201.2", "status_description": null, "id": "64c8e650-9bf8-48ff-9b96-5330b3898e59"}}
    """
    def memberCreate(self, jsMsg):
        big = self.big
        member = jsMsg.get('member')
        tenant_id = member.get('tenant_id')
        partition = 'uuid_' + tenant_id
        member_id = 'uuid_' +  member.get('id')
        pool_id = 'uuid_' + member.get('pool_id')

        address = member.get('address')
        rid = big.network.getRouteDomain(partition)
        address  = address + "%" + str(rid)

        port = member.get('protocol_port')
        big.ltm.createPoolMember(member_id, pool_id, address, port, partition)



    """
    {"member_id": "7cec8346-be32-4856-ae46-e5fb74e83318"}
    """
    def memberDelete(self, jsMsg):

        memberName = jsMsg.get('member_id')
        if (memberName):
            memberName = "uuid_" + memberName
        else:
            memberName = "*"
        big = self.big

        memberPort = None
        partition = "uuid_" + jsMsg.get('tenant_id')

        """
            Check if the Pool Name was passed in the Message 
        """
        poolName = jsMsg.get('pool_id')
        if (poolName != None):
            poolName = "uuid_" + poolName

        """
            Find all the Pools in the partition and identify which Pool has this Member
            UUID. 
            Issue a Delete using this pool.
        """
        print("Looking for Pools in the partition ", partition)
        poolList = big.ltm.getPartitionPools(partition)

        for pool in poolList:

            print("Found the Pool ", pool)
            pool_id = "uuid_" + pool
            if (poolName != None) and (poolName != pool_id):
                continue
                
            memberList = big.ltm.getPoolMembers(pool_id, partition)
            if (memberList == None):
                return False

            for member in memberList:
                print("Delete Member ", member['addr'], "Port ", member['port'])
                if (member['addr'] == memberName):
                    big.ltm.removePoolMember(pool_id, member['addr'], str(member['port']), partition)
                    return True
                if (memberName == "*"):
                    big.ltm.removePoolMember(pool_id, member['addr'], str(member['port']), partition)

        """
            Did not find the Specified Member in any of the Pools in the partition 
        """
        if (memberName != "*"):
            print("Did not find the Member ", memberName, 
                  " in any of the Pools in the Partition ", partition)
            return False
        else:
            return True
                    


    """
    {"vip": {"status": "PENDING_CREATE", "status_description": null, "protocol": "TCP", "description": "", "admin_state_up": true, "subnet_id": "9082ca27-7ed3-4b94-8f61-8e7f3ecbf106", "tenant_id": "b8560134d1c24305a06274214d7cf481", "connection_limit": -1, "pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15", "session_persistence": null, "address": "101.101.101.152", "protocol_port": 23, "port_id": "8600c488-bd83-4f38-b004-fae6e42aa705", "id": "ae381dab-343e-4eb8-8c29-473bdf8dc678", "name": "vip_pool3"}}    
    """
    def vipCreate(self, jsMsg):

        big = self.big
        vipMsg = jsMsg.get('vip')

        vipName = "uuid_" + vipMsg.get('id')
        tenant_id = vipMsg.get('tenant_id')
        partition = "uuid_" + tenant_id
        rid = self.big.network.getRouteDomain(partition)
        vlanList = big.network.getVlans(partition)
        if (len(vlanList) == 0):
            print("Cannot Create a VIP in partition ", partition, "Vlan List is Empty")
            return False

        vlanName = vlanList[0]
        vlanName = "uuid_" + vlanName
        vlanName = "/" + partition + "/" + vlanName

        vipAddress = vipMsg.get('address') + "%" + str(rid)
        poolName = 'uuid_' +  vipMsg.get('pool_id')

        big.ltm.createVirtualServer(vipName, vipAddress, "255.255.255.255",
                            vipMsg.get('protocol_port'), vipMsg.get('protocol'),
                            vlan_name = vlanName, 
                            use_snat=True, folder=partition)
        big.ltm.setVirtualServerPool(vipName, poolName, folder=partition) 
        big.ltm.vipEnableAdvertise(partition, vipAddress)
        return True

    """
    {"vip_id": "da18a14e-f497-405f-a92b-0abe1c351bc5"}
    """
    def vipDelete(self, jsMsg):
        vip_id = "uuid_" + jsMsg.get('vip_id')
        tenant_id = jsMsg.get('tenant_id')
        partition = "uuid_" + tenant_id
        self.big.ltm.deleteVirtualServer(vip_id, partition)

    """
    HTTPS :  u'health_monitor': {u'admin_state_up': True, u'tenant_id': u'e8cd656c845246bf8074d1e920077dc2', u'delay': 1, u'expected_codes': u'200', u'max_retries': 1, u'http_method': u'GET', u'timeout': 1, u'pools': [], u'url_path': u'//1.1.1.1', u'type': u'HTTPS', u'id': u'0ec0a5f0-0ab8-449f-955d-8576f2bbe33b?}
    HTTP u'health_monitor': {u'admin_state_up': True, u'tenant_id': u'e8cd656c845246bf8074d1e920077dc2', u'delay': 2, u'expected_codes': u'200', u'max_retries': 2, u'http_method': u'GET', u'timeout': 2, u'pools': [], u'url_path': u'/1.1.1.1', u'type': u'HTTP', u'id': u'834e636a-63be-4727-9071-528738ddce5f'}
    TCP:  u'health_monitor': {u'admin_state_up': True, u'tenant_id': u'e8cd656c845246bf8074d1e920077dc2', u'delay': 1, u'max_retries': 1, u'timeout': 1, u'pools': [], u'type': u'TCP', u'id': u'521e777f-85cc-4079-950a-3bbf3400fef6'}
    PING  u'health_monitor': {u'admin_state_up': True, u'tenant_id': u'e8cd656c845246bf8074d1e920077dc2', u'delay': 12, u'max_retries': 2, u'timeout': 1, u'pools': [], u'type': u'PING', u'id': u'77801f6a-42e2-47da-abe6-cec313c05287'}
    """
    def monitorAttach(self, jsMsg):
        big = self.big
        tenant_id = "uuid_" + jsMsg.get('tenant_id')
        monitor_name = "uuid_" + jsMsg.get('id')
        monitor_type = jsMsg.get('type')
        interval = jsMsg.get('delay')
        timeout = jsMsg.get('timeout')
        pools = jsMsg.get('pools')
        url_path = None
        expected_codes = None
        http_method = None
        send_string = None
        
        if ((monitor_type == 'HTTP') or \
            (monitor_type == 'HTTPS')):
            url_path = jsMsg.get('url_path')
            expected_codes = jsMsg.get('expected_Codes')
            http_method = jsMsg.get('http_method')
        if (http_method == 'GET'):
            send_string = 'GET /\r\n'
            
        big.ltm.createMonitor(monitor_name, monitor_type, 
                              interval, timeout, url_path, send_text=send_string, 
                              folder=tenant_id)

        for pool in pools:
            poolName = "uuid_" + pool['pool_id']
            if (pool['status'] == 'PENDING_CREATE'):
                self.big.ltm.attachMonitor(poolName, monitor_name, tenant_id)

    def monitorDetach(self, jsMsg):
        big = self.big
        tenant_id = "uuid_" + jsMsg.get('tenant_id')
        monitor_name = "uuid_" + jsMsg.get('id')
        monitor_type = jsMsg.get('type')
        interval = jsMsg.get('delay')
        timeout = jsMsg.get('timeout')
        pools = jsMsg.get('pools')
        url_path = None
        expected_codes = None
        http_method = None
        send_string = None
        
        if ((monitor_type == 'HTTP') or (monitor_type == 'HTTPS')):
            url_path = jsMsg.get('url_path')
            expected_codes = jsMsg.get('expected_Codes')
            http_method = jsMsg.get('http_method')

        if (http_method == 'GET'):
            send_string = 'GET /\r\n'

        for pool in pools:
            poolName = "uuid_" + pool['pool_id']
            print("Detaching Monitor from Pool", monitor_name, poolName)
            if (pool['status'] == 'PENDING_DELETE'):
                big.ltm.detachMonitor(poolName, monitor_name, tenant_id)

        """
            Check if after removing this Monitor from the Pool if th Monitor
            is in further use. If Not delete the monitor as well
        """
        print("Check to see if Monitor can be deleted");
        if (big.ltm.isMonitorInUse(tenant_id, monitor_name)):
            print("Monitor cannot be deleted: Still in use")
            return
        else:
            print("Monitor can be deleted")
            big.ltm.deleteMonitor(monitor_name, tenant_id);

    """
    {'tenant_id': u'14ed59c459c74299b7287029bffdfe76', u'health_monitor_id': u'85a29bc2-436c-431e-aade-24a8d3b4e4fe'}
    """
    def monitorDelete(self, jsMsg):
        big = self.big
        tenant_id = "uuid_" + jsMsg.get('tenant_id')
        monitor_name = "uuid_" + jsMsg.get('health_monitor_id')
        big.ltm.deleteMonitor(monitor_name, tenant_id)

    """
    {'tenant_id': u'578ec47638654a3da65106cf52d69325', u'health_monitor': {u'admin_state_up': True, u'tenant_id': u'578ec47638654a3da65106cf52d69325', u'delay': 3, u'max_retries': 5, u'timeout': 3, u'pools': [{u'status': u'PENDING_DELETE', u'status_description': None, u'pool_id': u'31830a61-725d-47a3-8440-23f858f4a507'}], u'type': u'PING', u'id': u'a6b7f0ee-dfb0-4edd-963a-669cff45b127'}}
    """
    def monitorUpdate(self, jsMsg):
        big = self.big
        tenant_id =  "uuid_" + jsMsg.get('tenant_id')
        monMsg = jsMsg.get('health_monitor')
        monitor_name = monMsg.get('id')
        big.ltm.updateMonitor(monitor_name, tenant_id)
    

    def processLbMessage(self, event_type, message):
        lbEventList = { 'pool_create_event':self.createPool, 
                        'pool_delete_event':self.deletePool,
                        'member_create_event':self.memberCreate,
                        'member_delete_event':self.memberDelete,
                        'vip_create_event':self.vipCreate,
                        'vip_delete_event':self.vipDelete,
                        'pool_hm_create_event': self.monitorAttach,
                        'pool_hm_delete_event': self.monitorDetach,
                      }
        if (event_type in lbEventList.keys()):
            LOG.info("Processing Event type %s message %s", event_type, message)
            lbEventList[event_type](message)
        else:
            print("Unkown event ", event_type)
        


