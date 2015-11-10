import F5Device as F5
import json
import time
import sys

def createFlow(f5Dev):
        context = "Rajesh_Partition"
        gateway_ip = "10.1.1.1"
        mask = "255.255.255.0"
        vlanid = 502
        selfIpAddress = f5Dev.allocateSelfIpAddress(gateway_ip, mask)
        print("Self IP Address is ", selfIpAddress)
        f5Dev.prepareF5ForNetwork(vlanid, context, gateway_ip, mask)
        jsMsg = json.loads('{"pool": {"status": "PENDING_CREATE", "lb_method": "ROUND_ROBIN", "protocol": "TCP", "description": "", "health_monitors": [], "members": [], "status_description": null, "id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15", "vip_id": null, "name": "waxu_pool3", "admin_state_up": true, "subnet_id": "0efe5f1c-22e3-4704-9713-558372214aa3", "tenant_id": "Rajesh_Partition", "health_monitors_status": [], "provider": "f5"}}')
        f5Dev.processLbMessage('pool_create_event', jsMsg)

        jsMsg = json.loads('{"admin_state_up":true, "tenant_id": "Rajesh_Partition", "pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15", "delay": 5, "max_retries": 4, "timeout": 5, "pools": [{"status": "PENDING_CREATE", "status_description": null, "pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15"}], "type": "PING", "id": "monitor_icmp"}')
        f5Dev.processLbMessage('monitor_attach_event', jsMsg)

        jsMsg = json.loads('{"member": {"status": "PENDING_CREATE", "protocol_port": 23, "weight": 1, "admin_state_up": true, "tenant_id": "Rajesh_Partition", "pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15", "address": "1.1.201.2", "status_description": null, "id": "64c8e650-9bf8-48ff-9b96-5330b3898e59"}}')
        f5Dev.processLbMessage('member_create_event', jsMsg)

        jsMsg = json.loads('{"vip": {"status": "PENDING_CREATE", "status_description": null, "protocol": "TCP", "description": "", "admin_state_up": true, "subnet_id": "9082ca27-7ed3-4b94-8f61-8e7f3ecbf106", "tenant_id": "Rajesh_Partition", "connection_limit": -1, "pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15", "session_persistence": null, "address": "101.101.101.152", "protocol_port": 23, "port_id": "8600c488-bd83-4f38-b004-fae6e42aa705", "id": "ae381dab-343e-4eb8-8c29-473bdf8dc678", "name": "vip_pool3"}}')
        f5Dev.processLbMessage('vip_create_event', jsMsg)

def deleteFlow(f5Dev):
        context = "Rajesh_Partition"
        jsMsg = json.loads('{"vip_id": "ae381dab-343e-4eb8-8c29-473bdf8dc678", "tenant_id":"Rajesh_Partition"}')
        f5Dev.processLbMessage('vip_delete_event', jsMsg)

        jsMsg = json.loads('{"member_id": "64c8e650-9bf8-48ff-9b96-5330b3898e59", "tenant_id":"Rajesh_Partition"}')
        f5Dev.processLbMessage('member_delete_event', jsMsg)

    
        jsMsg = json.loads('{"admin_state_up": true, "tenant_id": "Rajesh_Partition", "pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15" , "delay": 5, "max_retries": 4, "timeout": 5, "pools": [{"status": "PENDING_DELETE", "status_description": null, "pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15"}], "type": "PING", "id": "monitor_icmp"}')
        f5Dev.processLbMessage('monitor_detach_event', jsMsg)

        jsMsg = json.loads('{"pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15","tenant_id":"Rajesh_Partition"}')
        f5Dev.processLbMessage('pool_delete_event', jsMsg)

#        jsMsg = json.loads('{"tenant_id": "Rajesh_Partition", "health_monitor_id": "monitor_icmp"}')
#        f5Dev.processLbMessage('monitor_delete_event', jsMsg)

        f5Dev.cleanupF5Network(502, context)

def deletePool(f5Dev):
    jsMsg = json.loads('{"pool_id": "d091d1a1-2c81-4bfa-af04-a3eeee8b4f15","tenant_id":"Rajesh_Partition"}')
    f5Dev.processLbMessage('pool_delete_event', jsMsg)

def deleteMonitor(f5Dev):
    jsMsg = json.loads('{"tenant_id": "Rajesh_Partition", "health_monitor_id": "monitor_icmp"}'); 
    f5Dev.processLbMessage('monitor_delete_event', jsMsg)


def updateFlow(f5Dev):
    jsMsg = json.loads('{"old_hm": {"admin_state_up": true, "tenant_id": "7775cd6bef1b4e08952cedcd29b480ae", "delay": 5, "max_retries": 5, "timeout": 5, "pools": [{"status": "ACTIVE", "status_description": "", "pool_id": "60dc5aeb-3199-4df1-86c1-ca9b08c3bf44"}], "type": "PING", "id": "7a3069c2-dc18-4835-ab1d-8a90067d7964"}, "new_hm": {"admin_state_up": true, "tenant_id": "7775cd6bef1b4e08952cedcd29b480ae", "delay": 5, "max_retries": 4, "timeout": 5, "pools": [{"status": "ACTIVE", "status_description": "", "pool_id": "60dc5aeb-3199-4df1-86c1-ca9b08c3bf44"}], "type": "PING", "id": "7a3069c2-dc18-4835-ab1d-8a90067d7964"}}')


def main():
    f5Dev = F5.F5Device("172.28.10.180", "admin", "cisco123")
    
    if (sys.argv[1] == "mondel"):
        deleteMonitor(f5Dev)

    if (sys.argv[1] == "create"):
        print("Start Creation...")
        createFlow(f5Dev)

    if (sys.argv[1] == "update"):
        print("Start Modification")
        updateFlow(f5Dev)

    if (sys.argv[1] == "delete"):
        print("Start Deletion..")
        deleteFlow(f5Dev)

if __name__ == "__main__":
    main()
