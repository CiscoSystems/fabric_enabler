import os
import urllib
import json
import requests
import socket
import netaddr
import pexpect
import logging
import time
import sys

REQUEST_TIMEOUT = 30

PING_MONITOR_TYPE = 'PING'
HTTP_MONITOR_TYPE = 'HTTP'
HTTPS_MONITOR_TYPE = 'HTTPS'
TCP_MONITOR_TYPE = 'TCP'

SHARED_CONFIG_DEFAULT_TRAFFIC_GROUP = 'traffic-group-local-only'
SHARED_CONFIG_DEFAULT_FLOATING_TRAFFIC_GROUP = 'traffic-group-1'

class SystemException(Exception):
    pass

class RouteAddException(Exception):
    pass

class RouteDomainUpdateException(Exception):
    pass

class VLANCreateException(Exception):
    pass

class SelfIPCreateException(Exception):
    pass

class SelfIPDeleteException(Exception):
    pass

class PoolCreateException(Exception):
    pass

class PoolDeleteException(Exception):
    pass

class PoolUpdateException(Exception):
    pass

class PoolQueryException(Exception):
    pass

class MonitorCreateException(Exception):
    pass

class MonitorDeleteException(Exception):
    pass

class VirtualServerCreateException(Exception):
    pass

class VirtualServerUpdateException(Exception):
    pass

class VirtualServerDeleteException(Exception):
    pass

class MonitorUnknownException(Exception):
    pass

class F5Device(object):
    def __init__(self, f5IpAddr, username, password):
        self.deviceIp = f5IpAddr 
        self.username = username
        self.password = password
        self.session = self._getBigSession()
        self.url = 'https://%s/mgmt/tm' % f5IpAddr 
        self.network = Network(self)
        self.ltm = LTM(self)
        self.strict_route_isolation = False

    def logIt(self, level, prefix, msg):
        log_string = prefix + ': ' + msg
        log = logging.getLogger(__name__)
        out_hdlr = logging.StreamHandler(sys.stdout)
        out_hdlr.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        log.addHandler(out_hdlr)

        if level == 'debug':
            log.debug(log_string)
        elif level == 'error':
            log.error(log_string)
        elif level == 'crit':
            log.critical(log_string)
        else:
            log.info(log_string)

        log.removeHandler(out_hdlr)

    def error_log(self, prefix, msg):
        self.logIt('error', prefix, msg)


    def _getBigSession(self, timeout=REQUEST_TIMEOUT):
        session = requests.session()
        session.auth = (self.username, self.password)
        session.verify = False
        session.headers.update({'Content-Type': 'application/json'})
        socket.setdefaulttimeout(timeout)
        return session

    def folderExists(self, folder = None):
        if folder == None:
            return False
        folder = str(folder).replace('/', '')
        if folder == 'Common':
            return True
        request_url = self.url + '/sys/folder/'
        request_url += '~' + folder
        request_url += '?$select=name'
        print request_url
        response = self.session.get(request_url,
                                    timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code == 404:
            return False
        else:
            self.error_log('folder', response.text)
            raise exceptions.SystemException(response.text)
        return False


    def stripDomainId(self, ip_address):
        mask_index = ip_address.find('/')
        if mask_index > 0:
            return ip_address[:mask_index].split('%')[0] + \
                                     ip_address[mask_index:]
        else:
            return ip_address.split('%')[0]


    def stripToOnlyName(self, path):
        if isinstance(path, list):
            for i in range(len(path)):
                if path[i].find('~') > -1:
                    path[i] = path[i].replace('~', '/')
                if path[i].startswith('/Common'):
                    path[i] = path[i].replace('uuid_', '')
                else:
                    path[i] = \
                      os.path.basename(str(path[i])).replace('uuid_', '')
            return path
        else:
            if path.find('~') > -1:
                path = path.replace('~', '/')
            if path.startswith('/Common'):
                return str(path).replace('uuid_', '')
            else:
                return os.path.basename(str(path)).replace('uuid_', '')

    def createFolder(self, folder=None):
        if folder == None:
            return False
        folder = str(folder).replace('/', '')
        request_url = self.url + '/sys/folder/'
        payload = dict()
        payload['name'] = folder
        payload['subPath'] = '/'
        payload['fullPath'] = '/' + folder
        payload['hidden'] = False
        payload['inheritedDevicegroup'] = True
        payload['inheritedTrafficGroup'] = True

        response = self.session.post(request_url,
                                 data=json.dumps(payload),
                                 timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        else:
            self.error_log('folder', response.text)
            raise exceptions.SystemException(response.text)


    def deleteFolder(self, folder):
        if folder:
            # Before deleting the folder, change the iControl SOAP
            # active folder to '/' so that we do not delete the
            # active folder, which breaks the iControl session.
            # We also need to do a fake query and fake command
            # because changing your active folder, by itself, does
            # not do anything.
            folder = str(folder).replace('/', '')
            request_url = self.url + '/sys/folder/~' + folder
            response = self.session.delete(request_url, 
                                           timeout=REQUEST_TIMEOUT)
            if response.status_code < 400:
                return True
            elif response.status_code == 404:
                return True
            else:
                self.error_log('folder', response.text)
                raise exceptions.SystemException(response.text)
        return False

class Network(object):
    def __init__(self, bigip):
        self.bigip = bigip

    def routeAdd(self, name=None, dest_ip_address=None, dest_mask=None,
               gw_ip_address=None, folder='Common'):
        if dest_ip_address and dest_mask and gw_ip_address:
            folder = str(folder).replace('/', '')
            # self.bigip.system.set_rest_folder(folder)
            payload = dict()
            payload['name'] = name
            payload['partition'] = folder
            payload['gw'] = gw_ip_address
            payload['network'] = dest_ip_address + "/" + dest_mask
            request_url = self.bigip.url + '/net/route/'
            response = self.bigip.session.post(request_url,
                            data=json.dumps(payload),
                            timeout=REQUEST_TIMEOUT)
            if response.status_code < 400:
                return True
            elif response.status_code == 409:
                return True
            else:
                self.bigip.error_log('route', response.text)
                raise exceptions.RouteCreationException(response.text)
        return False

    def routeDel(self, name=None, folder='Common'):
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/net/route/'
        request_url += '~' + folder + '~' + name

        response = self.bigip.session.delete(request_url,
                               timeout=REQUEST_TIMEOUT)

        if response.status_code < 400:
            return True
        elif response.status_code == 404:
            return True
        else:
            self.bigip.error_log('route', response.text)
            raise exceptions.RouteDeleteException(response.text)
        return False


    def getRouteDomain(self, folder='Common'):
        folder = str(folder).replace('/', '')
        if folder == 'Common':
            return 0
        request_url = self.bigip.url + '/net/route-domain/'
        request_url += '~' + folder + '~' + folder
        request_url += '?$select=id'
        response = self.bigip.session.get(request_url,
                                  timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            response_obj = json.loads(response.text)
            if 'id' in response_obj:
                return int(response_obj['id'])
        elif response.status_code != 404:
            self.bigip.error_log('route-domain', response.text)
            raise exceptions.RouteQueryException(response.text)
        return 0

    def routeDomainPresent(self, folder='Common'):
        folder = str(folder).replace('/', '')
        if folder == 'Common':
            return True
        request_url = self.bigip.url + '/net/route-domain/'
        request_url += '~' + folder + '~' + folder
        request_url += '?$select=name'

        response = self.bigip.session.get(request_url,
                             timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code != 404:
            self.bigip.error_log('route', response.text)
            raise exceptions.RouteQueryException(response.text)
        return False


    def startOspf(self, rdId, network, netmask):
        username = "root"
        f5IpAddr = self.bigip.deviceIp
        print("Connecting to the F5 Device for Configuring OSPF")
        spawnid = pexpect.spawn("ssh "+username+"@"+f5IpAddr, timeout=20)
        matchOpt = spawnid.expect(["Password:.*", "yes/no"])
        if (matchOpt == 1):
            spawnid.sendline("yes")
            matchOpt = spawnid.expect("Password:.*")
        spawnid.sendline(self.bigip.password)
        spawnid.expect("config #.*")
        
        ospfRetryCount = 1
        while(ospfRetryCount < 60):
            spawnid.sendline("imish -r " + str(rdId))
            matchOpt = spawnid.expect([str(rdId) + "\]>.*", "Dynamic routing is not"])
            print(spawnid.before, spawnid.after)
            if (matchOpt == 1): 
                time.sleep(2)
                ospfRetryCount += 1
                continue
            else:
                break
            
        if (ospfRetryCount > 60):
            print("Error Starting IMISH, Dynamic routing may not be enabled on the F5 RD")
            spawnid.close()
            return False
        
        print("Started IMISH command line")
        
        spawnid.sendline("ena")
        spawnid.expect("\["+str(rdId)+"\]#")
        print spawnid.before
        ospfRetryCount = 1
        while(ospfRetryCount < 60):
            spawnid.sendline("show process")
            matchOpt = spawnid.expect(["ospfd.*\["+str(rdId)+"\]#", "\["+str(rdId)+"\]#"])
            if (matchOpt == 0):
                break
            else:
                ospfRetryCount += 1
        
        if (ospfRetryCount >= 60):
            print("OSPF Process is not started in the Routedomain " + str(rdId))
            spawnid.close()
            return False
        
        print("OSPF Process is running in the Routedomain " + str(rdId))
        
        spawnid.sendline("conf t")
        spawnid.expect("\["+str(rdId)+"\]\(config\)#.*")
        spawnid.sendline("router ospf")
        spawnid.expect("\["+str(rdId)+"\]\(config-router\)#.*")
        spawnid.sendline("redistribute kernel")
        spawnid.expect("\["+str(rdId)+"\]\(config-router\)#.*")
        spawnid.sendline("network " + network + " " +  netmask + " area 0 ")
        spawnid.expect("\["+str(rdId)+"\]\(config-router\)#.*")
        print("OSPF is now configured in the Routedomain " + str(rdId))
        spawnid.sendline("end")
        spawnid.expect("\["+str(rdId)+"\]#")
        spawnid.close()

    def _get_protocol_from_name(self, protocol):
        if (protocol == 'ospfv2'):
            return ['OSPFv2']
        if (protocol == 'BGP'):
            return ['BGP']
        return None

    def routeDomainAdd(self, folder='Common', protocol=None):
        folder = str(folder).replace('/', '')
        if (folder == 'Common'):
            return 0
        rid = self.getRouteDomain(folder)
        if (rid != 0):
            return rid
        payload = dict()
        payload['name'] = folder
        payload['partition'] = '/' + folder
        payload['id'] = self.getFreeRouteDomainId()
        if self.bigip.strict_route_isolation:
            payload['strict'] = 'enabled'
        else:
            payload['strict'] = 'disabled'
            payload['parent'] = '/Common/0'
        if (protocol != None):
            payload['routingProtocol'] = self._get_protocol_from_name(protocol)
        request_url = self.bigip.url + '/net/route-domain/'
        response = self.bigip.session.post(request_url,
                                data=json.dumps(payload),
                                timeout=REQUEST_TIMEOUT)

        if response.status_code < 400:
            pass
        elif response.status_code == 409:
            pass
        else:
            self.bigip.error_log('route-domain', response.text)
            raise exceptions.RouteAddException(response.text)

        rid = -1
        for retryCnt in range (0, 10):
            rid = self.getRouteDomain(folder)
            if (rid == 0):
                time.sleep(1)
            else:
                return rid 
            rid = -1
        return rid 

    def getFreeRouteDomainId(self):
        request_url = self.bigip.url + '/net/route-domain?$select=id'
        response = self.bigip.session.get(request_url,
                              timeout=REQUEST_TIMEOUT)
        all_identifiers = []
        if response.status_code < 400:
            response_obj = json.loads(response.text)
            if 'items' in response_obj:
                for route_domain in response_obj['items']:
                    all_identifiers.append(int(route_domain['id']))
                all_identifiers = sorted(all_identifiers)
                all_identifiers.remove(0)
        else:
            raise exceptions.RouteQueryException(response.text)

        lowest_available_index = 1
        for i in range(len(all_identifiers)):
            if all_identifiers[i] < lowest_available_index:
                if len(all_identifiers) > (i + 1):
                    if all_identifiers[i + 1] > lowest_available_index:
                        return lowest_available_index
                    else:
                        lowest_available_index = lowest_available_index + 1
            elif all_identifiers[i] == lowest_available_index:
                lowest_available_index = lowest_available_index + 1
        else:
            return lowest_available_index

    def deleteRouteDomain(self, folder='Common'):
        folder = str(folder).replace('/', '')
        if (folder == 'Common'):
            return True 
        request_url = self.bigip.url + '/net/route-domain/'
        request_url += '~' + folder + '~' + folder
        response = self.bigip.session.delete(request_url,
                              timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code != 404:
            self.bigip.error_log('route-domain', response.text)
            raise exceptions.RouteDeleteException(response.text)
        return False

    def getDomainVlanList(self, folder='Common'):
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + \
                      '/net/route-domain?$select=name,partition,vlans'
        if folder:
            request_filter = 'partition eq ' + folder
            request_url += '&$filter=' + request_filter
        response = self.bigip.session.get(request_url,
                                          timeout=REQUEST_TIMEOUT)

        if response.status_code < 400:
            response_obj = json.loads(response.text)
            if 'items' in response_obj:
                vlans = []
                folder = str(folder).replace('/', '')
                for route_domain in response_obj['items']:
                    if route_domain['name'] == folder:
                        if 'vlans' in route_domain:
                            for vlan in route_domain['vlans']:
                                vlans.append(vlan)
                return vlans
            return []
        else:
            if response.status_code != 404:
                self.bigip.error_log('route-domain', response.text)
                raise exceptions.RouteQueryException(response.text)
        return []


    def addVlanToDomain(self, name=None, folder='Common'):
        folder = str(folder).replace('/', '')
        existing_vlans = self.getDomainVlanList(folder)
        if not name in existing_vlans:
            existing_vlans.append(name)
            vlans = dict()
            vlans['vlans'] = existing_vlans
            request_url = self.bigip.url + '/net/route-domain/'
            request_url += '~' + folder + '~' + folder
            response = self.bigip.session.put(request_url,
                                   data=json.dumps(vlans),
                                   timeout=REQUEST_TIMEOUT)
            if response.status_code < 400:
                return True
            else:
                self.bigip.error_log('route-domain', response.text)
                raise exceptions.RouteDomainUpdateException(response.text)
        return False

    def createVlan(self, name=None, vlanid=None, interface=None,
               folder='Common', description=None):
        if name:
            folder = str(folder).replace('/', '')
            payload = dict()
            payload['name'] = name
            payload['partition'] = folder
            if vlanid:
                payload['tag'] = vlanid
                payload['interfaces'] = [{'name':interface, 'tagged':True}]
            else:
                payload['tag'] = 0
                payload['interfaces'] = [{'name':interface, 'untagged':True}]
            if description:
                payload['description'] = description
            request_url = self.bigip.url + '/net/vlan/'
            response = self.bigip.session.post(request_url,
                                  data=json.dumps(payload),
                                  timeout=REQUEST_TIMEOUT)
            if response.status_code < 400:
                if not folder == 'Common':
                    self.addVlanToDomain(name=name, folder=folder)
                return True
            elif response.status_code == 409:
                return True
            else:
                self.bigip.error_log('VLAN', response.text)
                raise exceptions.VLANCreateException(response.text)
        return False

    def deleteVlan(self, name=None, folder='Common'):
        if (name == None):
            return False
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/net/vlan/'
        request_url += '~' + folder + '~' + name
        response = self.bigip.session.delete(request_url,
                                                 timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code != 404:
            self.bigip.error_log('VLAN', response.text)
            raise exceptions.VLANDeleteException(response.text)
        else:
            return True

    def getVlans(self, folder='Common'):
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/net/vlan/'
        request_url += '?$select=name'
        if folder:
            request_filter = 'partition eq ' + folder
            request_url += '&$filter=' + request_filter
        response = self.bigip.session.get(request_url,
                                          timeout=REQUEST_TIMEOUT)
        return_list = []
        if response.status_code < 400:
            return_obj = json.loads(response.text)
            if 'items' in return_obj:
                for vlan in return_obj['items']:
                    return_list.append( \
                            self.bigip.stripToOnlyName(vlan['name']))
        elif response.status_code != 404:
            self.bigip.error_log('VLAN', response.text)
            raise exceptions.VLANLevelException(response.text)
        return return_list

    def createSelfIp(self, name=None, ip_address=None, netmask=None,
               vlan_name=None, floating=False, traffic_group=None,
               folder='Common'):
        if name: 
            folder = str(folder).replace('/', '')
            if not traffic_group:
                if floating:
                    traffic_group = \
                       SHARED_CONFIG_DEFAULT_FLOATING_TRAFFIC_GROUP
                else:
                    traffic_group = SHARED_CONFIG_DEFAULT_TRAFFIC_GROUP
            # self.bigip.system.set_rest_folder(folder)
            payload = dict()
            payload['name'] = name
            payload['partition'] = folder
            if not netmask:
                netmask = '32'
                payload['address'] = ip_address + '/' + str(netmask)
            else:
                net = netaddr.IPNetwork('1.1.1.1/' + str(netmask))
                payload['address'] = ip_address + '/' + str(net.prefixlen)
            if floating:
                payload['floating'] = 'enabled'
            else:
                payload['floating'] = 'disabled'
            payload['trafficGroup'] = traffic_group
            if not vlan_name.startswith('/Common'):
                payload['vlan'] = '/' + folder + '/' + vlan_name
            else:
                payload['vlan'] = vlan_name

            request_url = self.bigip.url + '/net/self/'
            response = self.bigip.session.post(request_url,
                                  data=json.dumps(payload),
                                  timeout=REQUEST_TIMEOUT)
            if response.status_code < 400:
                return True
            elif response.status_code == 409:
                return True
            elif response.status_code == 400 and \
                 response.text.find(
                "must be one of the vlans in the associated route domain") > 0:
                self.addVlanToDomain(name=vlan_name, folder=folder)
                self.bigip.error_log('self', 'bridge creation was halted before ' + \
                                  'it was added to route domain.' + \
                                  'attempting to add to route domain ' + \
                                  'and retrying SelfIP creation.')
                response = self.bigip.session.post(request_url,
                                  data=json.dumps(payload),
                                  timeout=REQUEST_TIMEOUT)
                if response.status_code < 400:
                    return True
                elif response.status_code == 409:
                    return True
                else:
                    self.bigip.error_log('self', response.text)
                    raise exceptions.SelfIPCreateException(response.text)
            else:
                self.bigip.error_log('self', response.text)
                raise exceptions.SelfIPCreationException(response.text)
        return False

    def deleteSelfIp(self, name=None, folder='Common'):
        if (name == None):
            return False

        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/net/self/'
        request_url += '~' + folder + '~' + name
        response = self.bigip.session.delete(request_url,
                               timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code != 404:
            self.bigip.error_log('self', response.text)
            raise exceptions.SelfIPDeleteException(response.text)
        else:
            return True

        return False


class LTM(object):
    def __init__(self, bigip):
        self.bigip = bigip
        self.monitor_type = {
             'ping': {'name': 'gateway-icmp',
                      'url': '/ltm/monitor/gateway-icmp'},
             'tcp': {'name': 'tcp',
                     'url': '/ltm/monitor/tcp'},
             'http': {'name': 'http',
                      'url': '/ltm/monitor/http'},
             'https': {'name': 'https',
                       'url': '/ltm/monitor/https'},
        }

    def getDeviceLbMethodName(self, lb_method):
        lb_method = str(lb_method).upper()

        if lb_method == 'LEAST_CONNECTIONS':
            return 'least-connections-member'
        elif lb_method == 'RATIO_LEAST_CONNECTIONS':
            return 'ratio-least-connections-member'
        elif lb_method == 'SOURCE_IP':
            return 'least-connections-node'
        elif lb_method == 'OBSERVED_MEMBER':
            return 'observed-member'
        elif lb_method == 'PREDICTIVE_MEMBER':
            return 'predictive-member'
        elif lb_method == 'RATIO':
            return 'ratio-member'
        else:
            return 'round-robin'


    def createPool(self, name=None, lb_method=None,
               description=None, folder='Common'):
        folder = str(folder).replace('/', '')
        if self.isPoolPresent(name=name, folder=folder):
            return True

        payload = dict()
        payload['name'] = name
        payload['partition'] = folder
        if description:
            payload['description'] = description
        payload['loadBalancingMode'] = \
                          self.getDeviceLbMethodName(lb_method)
        request_url = self.bigip.url + '/ltm/pool'
        response = self.bigip.session.post(request_url,
                              data=json.dumps(payload),
                              timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code == 409:
            return True
        else:
            self.bigip.error_log('pool', response.text)
            raise exceptions.PoolCreateException(response.text)

    def deletePool(self, name=None, folder='Common'):
        if (name == None):
            return False
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/ltm/pool/'
        request_url += '~' + folder + '~' + name
        node_addresses = []
        # get a list of node addresses before we delete
        response = self.bigip.session.get(request_url + '/members',
                                        timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return_obj = json.loads(response.text)
            if 'items' in return_obj:
                for member in return_obj['items']:
                    node_addresses.append(member['address'])
        else:
            self.bigip.error_log('members', response.text)
            raise PoolDeleteException(response.text)
        # delete the pool
        response = self.bigip.session.delete(request_url,
                                        timeout=REQUEST_TIMEOUT)
        if response.status_code < 400 or response.status_code == 404:
            for node_address in node_addresses:
                node_url = self.bigip.url + '/ltm/node/'
                node_url += '~' + folder + '~' + urllib.quote(node_address)
                node_res = self.bigip.session.delete(node_url,
                                          timeout=REQUEST_TIMEOUT)
                # we only care if this works.  Otherwise node is likely
                # in use by another pool
                if node_res.status_code < 400:
                    self._del_arp_and_fdb(node_address, folder)
                elif node_res.status_code == 400 and \
                     (node_res.text.find('is referenced') > 0):
                    # same node can be in multiple pools
                    pass
                else:
                    raise PoolDeleteException(node_res.text)
        return True

    def isPoolPresent(self, name=None, folder='Common'):
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/ltm/pool/'
        request_url += '~' + folder + '~' + name
        request_url += '?$select=name'
        response = self.bigip.session.get(request_url,
                             timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code != 404:
            raise exceptions.PoolQueryException(response.text)
        return False

    def getPartitionPools(self, folder='Common'):
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/ltm/pool'
        request_url += '?$select=name'
        request_url += '&$filter=partition eq ' + folder

        response = self.bigip.session.get(request_url,
                                    timeout=REQUEST_TIMEOUT)
        pool_names = []
        if response.status_code < 400:
            return_obj = json.loads(response.text)
            if 'items' in return_obj:
                for pool in return_obj['items']:
                    pool_names.append(
                                self.bigip.stripToOnlyName(pool['name']))
        elif response.status_code != 404:
            self.bigip.error_log('pool', response.text)
            raise exceptions.PoolQueryException(response.text)
        return pool_names

    def attachMonitor(self, name=None, monitor_name=None, folder='Common'):
        if (name == None) or (monitor_name == None):
            return False

        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/ltm/pool/'
        request_url += '~' + folder + '~' + name
        request_url += '?$select=monitor'
        response = self.bigip.session.get(request_url,
                             timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            response_obj = json.loads(response.text)
            if 'monitor' in response_obj:
                w_split = response_obj['monitor'].split()
                existing_monitors = []
                for w in w_split:
                    if w.startswith('/'):
                        existing_monitors.append(w)
                fp_monitor = '/' + folder + '/' + monitor_name
                monitor_string = ''
                if not fp_monitor in existing_monitors:
                    if response_obj['monitor'].startswith('min'):
                        min_count = w_split[1]
                        monitor_string = 'min ' + min_count + ' of { '
                        for monitor in existing_monitors:
                            monitor_string += monitor + ' '
                        monitor_string += fp_monitor + ' '
                        monitor_string += '}'
                    else:
                        for monitor in existing_monitors:
                            monitor_string += monitor + ' and '
                        monitor_string += fp_monitor
                    request_url = self.bigip.url + '/ltm/pool/'
                    request_url += '~' + folder + '~' + name
                    payload = dict()
                    payload['monitor'] = monitor_string
                    response = self.bigip.session.put(request_url,
                                        data=json.dumps(payload),
                                        timeout=REQUEST_TIMEOUT)
                    if response.status_code < 400:
                        return True
                    else:
                        self.bigip.error_log('pool', response.text)
                        raise exceptions.PoolUpdateException(response.text)
                else:
                    return True
            else:
                payload = dict()
                payload['monitor'] = monitor_name
                request_url = self.bigip.url + '/ltm/pool/'
                request_url += '~' + folder + '~' + name
                response = self.bigip.session.put(request_url,
                                        data=json.dumps(payload),
                                        timeout=REQUEST_TIMEOUT)
                if response.status_code < 400:
                    return True
                else:
                    self.bigip.error_log('pool', response.text)
                    raise exceptions.PoolUpdateException(response.text)
        else:
            self.bigip.error_log('pool', response.text)
            raise exceptions.PoolQueryException(response.text)
        return False

    def detachMonitor(self, name=None, monitor_name=None, folder='Common'):

        if (name == None) or (monitor_name == None):
            return False

        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/ltm/pool/'
        request_url += '~' + folder + '~' + name
        request_url += '?$select=monitor'
        response = self.bigip.session.get(request_url,
                                            timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            response_obj = json.loads(response.text)
            if 'monitor' in response_obj:
                w_split = response_obj['monitor'].split()
                existing_monitors = []
                for w in w_split:
                    if w.startswith('/'):
                        existing_monitors.append(w)
                fp_monitor = '/' + folder + '/' + monitor_name
                monitor_string = ''
                if fp_monitor in existing_monitors:
                    existing_monitors.remove(fp_monitor)
                    new_monitor_count = len(existing_monitors)
                    if new_monitor_count > 0:
                        if response_obj['monitor'].startswith('min'):
                            min_count = w_split[1]
                            if min_count > new_monitor_count:
                                min_count = new_monitor_count
                            monitor_string = 'min ' + min_count + ' of { '
                            for monitor in existing_monitors:
                                monitor_string += monitor + ' '
                            monitor_string += '}'
                        else:
                            for i in range(new_monitor_count):
                                if (i + 1) < new_monitor_count:
                                    monitor_string += \
                                            existing_monitors[i] + ' and '
                                else:
                                    monitor_string += \
                                            existing_monitors[i] + ' '
                    request_url = self.bigip.url + '/ltm/pool/'
                    request_url += '~' + folder + '~' + name
                    payload = dict()
                    payload['monitor'] = monitor_string
                    response = self.bigip.session.put(request_url,
                                        data=json.dumps(payload),
                                        timeout=REQUEST_TIMEOUT)
                    if response.status_code < 400:
                        return True
                    else:
                        self.bigip.error_log('pool', response.text)
                        raise exceptions.PoolUpdateException(response.text)
                else:
                    return True
        elif response.status_code != 404:
            self.bigip.error_log('pool', response.text)
            raise exceptions.PoolQueryException(response.text)
        return False



    def getPoolMembers(self, name=None, folder='Common'):
        if name:
            folder = str(folder).replace('/', '')
            request_url = self.bigip.url + '/ltm/pool/'
            request_url += '~' + folder + '~' + name
            request_url += '/members?$select=name'
            response = self.bigip.session.get(request_url,
                                      timeout=REQUEST_TIMEOUT)
            members = []
            if response.status_code < 400:
                return_obj = json.loads(response.text)
                if 'items' in return_obj:
                    for member in return_obj['items']:
                        name_parts = member['name'].split(":")
                        members.append(
                            {
                             'addr': \
                                self.bigip.stripDomainId(name_parts[0]),
                             'port': int(name_parts[1])
                            }
                        )
            elif response.status_code != 404:
                self.bigip.error_log('pool', response.text)
                raise exceptions.PoolQueryException(response.text)
            return members
        return None

    def createPoolMember(self, memberName, poolName, ip_address, port, folder):
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/ltm/pool/'
        request_url += '~' + folder + '~' + poolName 
        request_url += '/members'
        payload = dict()
        payload['name'] = memberName + ":" + str(port)
        payload['partition'] = folder
        payload['address'] = ip_address
        response = self.bigip.session.post(request_url,
                                    data=json.dumps(payload), timeout=60)
        if response.status_code < 400:
            return True
        elif response.status_code == 404:
            self.bigip.error_log('pool',
                      'tried to add member %s to non-existant pool %s.' %
                      (payload['name'], '/' + folder + '/' + name))
            raise exceptions.PoolUpdateException(response.text)
            return False
        elif response.status_code == 409:
            return True
        else:
            self.bigip.error_log('pool', response.text)

    def removePoolMember(self, poolName=None, memberName=None, port = None, folder='Common'):
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/ltm/pool/'
        request_url += '~' + folder + '~' + poolName
        request_url += '/members/'
        request_url += '~' + folder + '~'
        request_url += memberName + ":" + str(port)
        print("Removing Member ", request_url)
        response = self.bigip.session.delete(request_url, timeout=60)
        if response.status_code < 400 or response.status_code == 404:
            # delete nodes
            node_req = self.bigip.url + '/ltm/node/'
            node_req += '~' + folder + '~' + memberName
            response = self.bigip.session.delete(node_req, timeout=60)
            if response.status_code == 400 and \
             (response.text.find('is referenced') > 0):
                # Node address is part of multiple pools
                pass
            elif response.status_code > 399 and \
               (not response.status_code == 404):
                self.bigip.error_log('node', response.text)
                raise exceptions.PoolUpdateException(response.text)
        else:
            self.bigip.error_log('pool', response.text)
            raise exceptions.PoolUpdateException(response.text)

    def createMonitor(self, name=None, mon_type=None, interval=5, timeout=16,
               url_path=None, expect_codes = None,
               send_text=None, recv_text=None, folder='Common'):
        folder = str(folder).replace('/', '')
        mon_type = self._getMonitorRestType(mon_type)
        payload = dict()
        payload['name'] = name
        payload['partition'] = folder
        parent = mon_type.replace('-', '_')
        payload['defaultsFrom'] = '/Common/' + parent
        payload['timeout'] = timeout
        payload['interval'] = interval
        if send_text:
            payload['send'] = send_text
        if recv_text:
            payload['recv'] = recv_text
        request_url = self.bigip.url + '/ltm/monitor/' + mon_type
        response = self.bigip.session.post(request_url,
                              data=json.dumps(payload),
                              timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code == 409:
            return True
        else:
            self.bigip.error_log('monitor', response.text)
            raise exceptions.MonitorCreateException(response.text)
        return False


    def _getMonitorType(self, name, folder):
        for mtype in self.monitor_type:
            request_url = self.bigip.url + self.monitor_type[mtype]['url'] + "/"
            request_url += "~"+folder+"~"+name
            response = self.bigip.session.get(request_url,
                                              timeout=REQUEST_TIMEOUT)
            if (response.status_code < 400):
                js = json.loads(response.text)
                parentMonitor = js.get('defaultsFrom')
                if (parentMonitor != None):
                    return mtype
        return None
        
    def deleteMonitor(self, name=None, folder='Common'):
        if (name == None):
           return False
        mon_type = self._getMonitorType(name, folder)
        if (mon_type == None):
            print ("Monitor Not found")
            return False
        folder = str(folder).replace('/', '')
        mon_type = self._getMonitorRestType(mon_type)
        request_url = self.bigip.url + '/ltm/monitor/' + mon_type + '/'
        request_url += '~' + folder + '~' + name
        print (request_url)
        response = self.bigip.session.delete(request_url,
                                          timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code == 404:
            return True
        else:
            self.bigip.error_log('monitor', response.text)
            raise exceptions.MonitorDeleteException(response.text)
        return False

    def _getMonitorRestType(self, type_str):
        type_str = type_str.lower()
        if type_str in self.monitor_type:
            return self.monitor_type[type_str]['name']
        else:
            raise exceptions.MonitorUnknownException('Invalid Modnitor %s' % type_str)

    def createVirtualServer(self, name=None, ip_address=None, mask=None,
               port=None, protocol=None, vlan_name=None,
               traffic_group=None, use_snat=True,
               snat_pool=None, folder='Common'):

        if (name == None):
            return False

        folder = str(folder).replace('/', '')
        # self.bigip.system.set_rest_folder(folder)
        payload = dict()
        payload['name'] = name
        payload['partition'] = folder
        if str(ip_address).endswith('%0'):
            ip_address = ip_address[:-2]
        if not port:
            port = 0
        payload['destination'] = ip_address + ':' + str(port)
        payload['mask'] = mask
        if not protocol:
            protocol = 'tcp'
        else:
            protocol = self._get_server_protocol_name(protocol)
        payload['ipProtocol'] = protocol
        payload['vlansEnabled'] = True
        if vlan_name:
            payload['vlans'] = [vlan_name]
        if use_snat:
            payload['sourceAddressTranslation'] = dict()
            if snat_pool:
                payload['sourceAddressTranslation']['type'] = 'snat'
                payload['sourceAddressTranslation']['pool'] = snat_pool
            else:
                payload['sourceAddressTranslation']['type'] = 'automap'
        if not traffic_group:
            traffic_group = \
                  SHARED_CONFIG_DEFAULT_FLOATING_TRAFFIC_GROUP

        request_url = self.bigip.url + '/ltm/virtual/'
        response = self.bigip.session.post(request_url,
                                      data=json.dumps(payload),
                                      timeout=REQUEST_TIMEOUT)
        if response.status_code < 400 or response.status_code == 409:
            request_url = self.bigip.url + '/ltm/virtual-address/'
            request_url += '~' + folder + '~' + urllib.quote(ip_address)
            payload = dict()
            payload['trafficGroup'] = traffic_group
            response = self.bigip.session.put(request_url,
                                        data=json.dumps(payload),
                                        timeout=REQUEST_TIMEOUT)
            if response.status_code < 400:
                return True
            else:
                self.bigip.error_log('virtual-address', response.text)
                raise exceptions.VirtualServerCreateException(response.text)
        else:
            self.bigip.error_log('virtual', response.text)
            raise exceptions.VirtualServerException(response.text)
        return False

    def deleteVirtualServer(self, name=None, folder='Common'):
        if (name == None):
            return False
        folder = str(folder).replace('/', '')
        request_url = self.bigip.url + '/ltm/virtual/'
        request_url += '~' + folder + '~' + name
        response = self.bigip.session.delete(request_url,
                                      timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        elif response.status_code == 404:
            return True
        else:
            self.bigip.error_log('virtual', response.text)
            raise exceptions.VirtualServerDeleteException(response.text)
        return False

    def vipEnableAdvertise(self, partition, vipAddress):
        request_url = self.bigip.url + '/ltm/virtual-address/'
        request_url += '~' + partition + '~' + urllib.quote(vipAddress)
        payload = dict()
        payload['routeAdvertisement'] = "enabled"
        response = self.bigip.session.put(request_url,
                                         data=json.dumps(payload),
                                         timeout=60)
        if response.status_code < 400:
            return True
        else:
            self.bigip.error_log('virtual-address', response.text)
            raise exceptions.VirtualServerUpdateException(response.text)
        return False

    def setVirtualServerPool(self, name=None, pool_name=None, folder='Common'):
        if (name  == None) or (pool_name == None):
            return False
        folder = str(folder).replace('/', '')
        payload = dict()
        payload['pool'] = pool_name
        request_url = self.bigip.url + '/ltm/virtual/'
        request_url += '~' + folder + '~' + name
        response = self.bigip.session.put(request_url,
                                          json.dumps(payload),
                                          timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            return True
        else:
            self.bigip.error_log('virtual', response.text)
            raise exceptions.VirtualServerUpdateException(response.text)
        return False


    def _get_server_protocol_name(self, protocol):
        if str(protocol).lower() == 'tcp':
            return 'tcp'
        elif str(protocol).lower() == 'udp':
            return 'udp'
        elif str(protocol).lower() == 'http':
            return 'tcp'
        elif str(protocol).lower() == 'https':
            return 'tcp'
        elif str(protocol).lower() == 'dns':
            return 'udp'
        elif str(protocol).lower() == 'dnstcp':
            return 'tcp'
        elif str(protocol).lower() == 'sctp':
            return 'sctp'
        else:
            return 'tcp'


