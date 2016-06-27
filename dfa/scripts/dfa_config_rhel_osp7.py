# Copyright 2016 Cisco Systems, Inc.
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
# @author: Paolo Zarpellon, Cisco Systems, Inc.

import urlparse
import socket

import logging
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)

from oslo.config.cfg import ConfigParser

class EnhancedConfigParser(ConfigParser):
    '''
    Extends oslo_config's ConfigParser in order to write config
    files back after they've been modified
    '''

    def __init__(self, filename):
        self._filename = filename
        ConfigParser.__init__(self, filename, {})
        self.parse()

    def _write_section(self, fp, section, items):
        '''
        In file fp, write 'items' in 'section'
        '''
        s = None
        for k in items:
            for v in items[k]:
                if s is None:
                    fp.write("[%s]\n" % (section))
                    s = section
                fp.write("%s = %s\n" % (k, v))
        if s is not None:
            fp.write("\n")

    def write(self):
        '''
        Write config to filename
        '''
        with open(self._filename, "w+") as f:
            sections = self.sections
            if sections.has_key("DEFAULT"):
                self._write_section(f, "DEFAULT", sections["DEFAULT"])
            for section, items in self.sections.iteritems():
                if section == "DEFAULT":
                    continue
                self._write_section(f, section, items)

def update_config_files():
    '''
    Update config files
    '''

    # Set notification_driver to messaging in
    # /usr/share/neutron/neutron-dist.conf
    try:
        parser = EnhancedConfigParser("/usr/share/neutron/neutron-dist.conf")
        config = parser.sections
        if config.has_key("DEFAULT"):
            config["DEFAULT"]["notification_driver"] = ["messaging"]
            parser.write()
    except IOError as e:
        LOG.error("I/O error %s", e)

    # Delete keystone_authtoken from /usr/share/nova-dist.conf"
    try:
        parser = EnhancedConfigParser("/usr/share/nova/nova-dist.conf")
        config = parser.sections
        if config.has_key("keystone_authtoken"):
            del config["keystone_authtoken"]
            parser.write()
    except IOError as e:
        LOG.error("I/O error %s", e)

    # In /etc/neutron/neutron.conf:
    # - use auth_url instead of auth_uri in section keystone_authtoken
    # - collect rabbit_hosts from oslo_messaging_rabbit
    rabbit_hosts = None
    db_connection = None
    try:
        parser = EnhancedConfigParser("/etc/neutron/neutron.conf")
        config = parser.sections
        if config.has_key("keystone_authtoken"):
            if config["keystone_authtoken"].has_key("identity_uri"):
                config["keystone_authtoken"]["auth_url"] = \
                    config["keystone_authtoken"]["identity_uri"]
                parser.write()
            else:
                LOG.error("identity_uri config not found "
                          "in section keystone_authtoken")
        else:
            LOG.error("keystone_authtoken section not found")
        if config.has_key("oslo_messaging_rabbit"):
            if config["oslo_messaging_rabbit"].has_key("rabbit_hosts"):
                rabbit_hosts = \
                    config["oslo_messaging_rabbit"]["rabbit_hosts"][0]
        if config.has_key("database"):
            if config["database"].has_key("connection"):
                db_connection = config["database"]["connection"][0]
    except IOError as e:
        LOG.error("I/O error %s", e)

    if rabbit_hosts is None or db_connection is None:
        LOG.error("Could not find DB connection or rabbit_hosts: "
                  "haproxy.conf will not be processed")
        return

    # Add VIP for RabbitMQ in /etc/haproxy/haproxy.conf
    #
    vip = urlparse.urlparse(db_connection).hostname
    fname = "/etc/haproxy/haproxy.cfg"
    rabbitmq_present = False
    try:
        f = open(fname, "r+")
        while True:
            l = f.readline()
            if not l:
                break
            if "listen rabbitmq" in l:
                rabbitmq_present = True
                break
        if rabbitmq_present:
            LOG.warning('"listen rabbitmq" instance already '
                        'found in {}, no changes will be done'.format(fname))
        else:
            f.write("\nlisten rabbitmq")
            f.write("\n  option tcpka")
            f.write("\n  timeout client 0")
            f.write("\n  timeout server 0")

            f.write("\n  bind {}:5672 transparent".format(vip))
            c = 0
            for i in rabbit_hosts.split(","):
                try:
                    hostname = socket.gethostbyaddr(i)[2][1]
                except Exception as e:
                    hostname = "controller_{}".format(c)
                f.write(("\n  server {} {}:5672 check "
                         "fall 5 inter 2000 rise 2").format(hostname, i))
                c = c + 1

            f.write("\n")
        f.close()
    except IOError as e:
        LOG.error("I/O error %s", e)

if __name__ == "__main__":
    update_config_files()
