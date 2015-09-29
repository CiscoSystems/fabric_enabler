# Copyright 2015 Cisco Systems, Inc.
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

#
# Openstack driver for cisco ASA firewall
#

import base64
import json
import sys
import urllib2
from netaddr import *
from dfa.common import dfa_logger as logging

LOG = logging.getLogger(__name__)


class Asa5585():
    ''' ASA 5585 Driver '''

    def __init__(self, mgmt_ip, username, password):
        self.server = "https://" + mgmt_ip
        self.username = username
        self.password = password
        self.rule_tbl = {}

    def rest_send_cli(self, data):
        headers = {'Content-Type': 'application/json'}
        api_path = "/api/cli"    # param
        url = self.server + api_path

        req = urllib2.Request(url, json.dumps(data), headers)
        base64string = base64.encodestring('%s:%s' %
                                           (self.username,
                                            self.password)).replace('\n', '')
        req.add_header("Authorization", "Basic %s" % base64string)
        f = None
        try:
            f = urllib2.urlopen(req)
            status_code = f.getcode()
            LOG.debug("Status code is %d", status_code)

        except urllib2.HTTPError, err:
            LOG.debug("Error received from server. HTTP status code is %d",
                      err.code)
            try:
                json_error = json.loads(err.read())
                if json_error:
                    LOG.debug(json.dumps(json_error, sort_keys=True,
                                         indent=4, separators=(',', ': ')))
            except ValueError:
                pass
        finally:
            if f:
                f.close()
        return (status_code in range(200, 300))

    def setup(self, tenant, inside_vlan_arg, outside_vlan_arg,
              inside_ip, inside_mask, inside_gw,
              outside_ip, outside_mask, outside_gw,
              interface_in, interface_out):

        """ setup ASA context for an edge tenant pair """
        LOG.debug("asa_setup: %s %d %d %s %s %s %s",
                  tenant, inside_vlan_arg, outside_vlan_arg,
                  inside_ip, inside_mask, outside_ip, outside_mask)
        inside_vlan = str(inside_vlan_arg)
        outside_vlan = str(outside_vlan_arg)
        context = tenant
        cmds = ["conf t", "changeto system"]
        inside_int = interface_in + '.' + inside_vlan
        cmds.append("int " + inside_int)
        cmds.append("vlan " + inside_vlan)
        outside_int = interface_out + '.' + outside_vlan
        cmds.append("int " + outside_int)
        cmds.append("vlan " + outside_vlan)
        cmds.append("context " + context)
        cmds.append("allocate-interface " + inside_int)
        cmds.append("allocate-interface " + outside_int)
        cmds.append("config-url disk0:/" + context + ".cfg")
        cmds.append("changeto context " + context)
        cmds.append("int " + inside_int)
        cmds.append("nameif Inside")
        cmds.append("security-level 100")
        cmds.append("ip address " + inside_ip + " " + inside_mask)
        cmds.append("int " + outside_int)
        cmds.append("nameif Outside")
        cmds.append("security-level 0")
        cmds.append("ip address " + outside_ip + " " + outside_mask)

        cmds.append("router ospf 1")
        cmds.append("network " + inside_ip + " " + inside_mask + " area 0")
        cmds.append("network " + outside_ip + " " + outside_mask + " area 0")
        cmds.append("area 0")
        cmds.append("route Outside 0.0.0.0 0.0.0.0 " + outside_gw + " 1")
        cmds.append("end")

        data = {"commands": cmds}
        return self.rest_send_cli(data)

    def cleanup(self, tenant, inside_vlan_arg, outside_vlan_arg,
                inside_ip, inside_mask,
                outside_ip, outside_mask,
                interface_in, interface_out):
        """ cleanup ASA context for an edge tenant pair """
        LOG.debug("asa_cleanup: %s %d %d %s %s %s %s",
                  tenant, inside_vlan_arg, outside_vlan_arg,
                  inside_ip, inside_mask, outside_ip, outside_mask)
        inside_vlan = str(inside_vlan_arg)
        outside_vlan = str(outside_vlan_arg)
        context = tenant
        cmds = ["conf t", "changeto system"]
        cmds.append("no context " + context + " noconfirm")
        inside_int = interface_in + '.' + inside_vlan
        outside_int = interface_out + '.' + outside_vlan
        cmds.append("no interface " + inside_int)
        cmds.append("no interface " + outside_int)
        data = {"commands": cmds}
        return self.rest_send_cli(data)

    def get_quota(self):
        cmds = ["conf t", "changeto system"]
        cmds.append("show ver | grep Contexts")
        data = {"commands": cmds}
        headers = {'Content-Type': 'application/json'}
        api_path = "/api/cli"    # param
        url = self.server + api_path

        req = urllib2.Request(url, json.dumps(data), headers)
        base64string = base64.encodestring('%s:%s' %
                                           (self.username,
                                            self.password)).replace('\n', '')
        req.add_header("Authorization", "Basic %s" % base64string)
        max_ctx_count = 0
        f = None
        try:
            f = urllib2.urlopen(req)
            status_code = f.getcode()
            LOG.debug("Status code is %d", status_code)
            if status_code in range(200, 300):
                resp = json.loads(f.read())
                try:
                    max_ctx_count = int(resp.get('response')[-1].split()[3])
                except ValueError:
                    max_ctx_count = 0
                LOG.debug("Max Context Count is %d", max_ctx_count)

        except urllib2.HTTPError, err:
            LOG.debug("Error received from server. HTTP status code is %d",
                      err.code)
            try:
                json_error = json.loads(err.read())
                if json_error:
                    LOG.debug(json.dumps(json_error, sort_keys=True,
                                         indent=4, separators=(',', ': ')))
            except ValueError:
                pass
        finally:
            if f:
                f.close()
        return max_ctx_count

    def apply_policy(self, policy):
        """ apply a firewall policy """
        tenant_name = policy['tenant_name']
        fw_id = policy['fw_id']
        fw_name = policy['fw_name']
        LOG.debug("asa_apply_policy: tenant=%s fw_id=%s fw_name=%s",
                  tenant_name, fw_id, fw_name)
        cmds = ["conf t", "changeto context " + tenant_name]

        rule_dict = policy['rules']
        for rule_id in rule_dict:
            rule = rule_dict[rule_id]
            protocol = rule['protocol']
            name = rule['name']
            enabled = rule['enabled']
            dst_port = rule['destination_port']
            src_port = rule['source_port']

            if (rule['source_ip_address'] is not None):
                src_ip = IPNetwork(rule['source_ip_address'])
            else:
                src_ip = IPNetwork('0.0.0.0/0')

            if (rule['destination_ip_address'] is not None):
                dst_ip = IPNetwork(rule['destination_ip_address'])
            else:
                dst_ip = IPNetwork('0.0.0.0/0')

            if rule['action'] == 'allow':
                action = 'permit'
            else:
                action = 'deny'

            LOG.debug("rule[%s]: name=%s enabled=%s prot=%s dport=%s sport=%s \
                      dip=%s %s sip=%s %s action=%s",
                      rule_id, name, enabled, protocol, dst_port, src_port,
                      dst_ip.network, dst_ip.netmask,
                      src_ip.network, src_ip.netmask, action)

            acl = "access-list "
            acl = (acl + tenant_name + " extended " + action + " " +
                   protocol + " ")
            if (rule['source_ip_address'] is None):
                acl = acl + "any "
            else:
                acl = acl + str(src_ip.network) + " " + (
                    str(src_ip.netmask) + " ")
            if (src_port is not None):
                if (':' in src_port):
                    range = src_port.replace(':', ' ')
                    acl = acl + "range " + range + " "
                else:
                    acl = acl + "eq " + src_port + " "
            if (rule['destination_ip_address'] is None):
                acl = acl + "any "
            else:
                acl = acl + str(dst_ip.network) + " " + \
                    str(dst_ip.netmask) + " "
            if (dst_port is not None):
                if (':' in dst_port):
                    range = dst_port.replace(':', ' ')
                    acl = acl + "range " + range + " "
                else:
                    acl = acl + "eq " + dst_port + " "
                    if (enabled is False):
                        acl = acl + 'inactive'

            # remove the old ace for this rule
            if (rule_id in self.rule_tbl):
                cmds.append('no ' + self.rule_tbl[rule_id])

            self.rule_tbl[rule_id] = acl
            cmds.append(acl)
        cmds.append("access-group " + tenant_name + " global")

        LOG.debug(cmds)
        data = {"commands": cmds}
        return self.rest_send_cli(data)
