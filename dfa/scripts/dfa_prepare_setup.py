#!/usr/bin/python
# Copyright 2014 Cisco Systems, Inc.
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
# @author: Nader Lahouti, Cisco Systems, Inc.



import os
import sys
import commands
import itertools
import optparse
import ConfigParser

CONF_TMP_FILE = '%s_conf.new'
NEUTRON = 'neutron'
KEYSTONE = 'keystone'

conf_file_list = [
'keystone.conf',
'keystone.conf.sample',
'neutron.conf'
]
default_path = '/opt/stack,/etc/neutron,/etc/keystone'
default_mysql_user = 'root'
default_mysql_passwd = 'cisco123'
dfa_cfg_file = '/etc/enabler_conf.ini'


def get_mysql_credentials(cfg_file):
    try:
        parser = ConfigParser.ConfigParser()
        cfg_fp = open(cfg_file)
        parser.readfp(cfg_fp)
        cfg_fp.close()
    except ConfigParser.NoOptionError:
        cfg_fp.close()
        print 'Failed to find mysql connections credentials.'
        sys.exit(1)
    except IOError:
        print 'ERROR: Cannot open %s.' % cfg_file
        sys.exit(1)

    value = parser.get('dfa_mysql', 'connection')

    try:
        start = value.index('://') + 3
        end = value.index('@')
        cred = value[start:end].split(':')
        return cred[0], cred[1]
    except ValueError:
        print 'Failed to find mysql connections credentials.'
        sys.exit(1)


def modify_conf(cfgfile, service_name, outfn):

    """Modify these lines in  config file for:
    1. /opt/stack/neutron/etc/neutron.conf
    2. /etc/neutron/neutron.conf
    3. /etc/keystone/keyston.conf
    4. /opt/stack/keystone/etc/keystone.conf.sample
    
    rpc_backend = rabbit
    notification_topics = cisco_dfa_neutron_notify
    notification_driver = messaging

    rpc_backend = rabbit
    notification_topics = cisco_dfa_keystone_notify
    notification_driver = messaging
    """
    fn = open(outfn , 'w')
    notify_val = 'cisco_dfa_%s_notify' % service_name
    notify_drvr = 'messaging'
    if cfgfile:
        with open(cfgfile, 'r') as cf:
            lines = cf.readlines()
            for line in lines:
                line = line.strip('\n')
                newline = line
                opt = line.partition('=')
                if opt[1] == '=':
                    if line.startswith('#rpc_backend'):
                        newline = 'rpc_backend = rabbit'
                    elif line.startswith('#notification_driver'):
                        newline = 'notification_driver = ' + notify_drvr
                    elif 'notification_topics' in line:
                        if opt[0].startswith('#notification_topics'):
                            newline = 'notification_topics = ' + notify_val
                        elif opt[0].startswith('notification_topics'):
                            if notify_val not in opt[2]:
                                newline = opt[0] + ' = ' + (
                                      (opt[2] + ',' + notify_val)
                                      if opt[2].strip(' ') else notify_val)

                fn.write(newline + '\n')

        fn.close()


def prepare_db():
    hostname = 'localhost'
    database = 'cisco_dfa'
    charset = 'utf8'

    (user, password) = get_mysql_credentials(dfa_cfg_file)

    # Modify max_connections, if it is not 2000
    logincmd = ("mysql -u%(user)s -p%(password)s -h%(host)s -e '" % (
                 {'user': user, 'password': password, 'host': hostname}))
    conn_cmd = 'show variables like "' + 'max_connections";' + "'"
    out = commands.getoutput(logincmd + conn_cmd)
    try:
        val = int(out.split('\n')[1].split('\t')[1])
    except:
        print 'Invalid value: Cannot get max_connections from DB.'
        sys.exit(0)

    if val < 2000:
        # Set max_connections to 2000 if it is not.
        logincmd = ("mysql -u%(user)s -p%(password)s -h%(host)s -e '" % (
                     {'user': user, 'password': password, 'host': hostname}))
        conn_cmd = 'set global max_connections = 2000' + "'"
        out = commands.getoutput(logincmd + conn_cmd)

    # Delete database if it exist.
    del_cmd = ('mysql -u%(user)s -p%(password)s -h%(host)s -e '
               '"DROP DATABASE IF EXISTS %(db)s;"' % (
               {'user': user, 'password': password, 'host': hostname,
                'db': database}))
    out = commands.getoutput(del_cmd)
    print out

    # Create database.
    creat_cmd = ('mysql -u%(user)s -p%(password)s -h%(host)s '
                 '-e "CREATE DATABASE %(db)s CHARACTER SET %(charset)s;"' % (
                 {'user': user, 'password': password, 'host': hostname, 
                  'db': database, 'charset': charset}))
    out = commands.getoutput(creat_cmd)
    print out
    

def find_conf_and_modify(os_path):

    # Search for the config files in the path
    for path in os_path.split(','):
        for p, d, f in os.walk(path):
            for fn in f:
                if fn in conf_file_list:
                    fname = os.path.realpath(os.path.join(p, fn))
                    service_name = NEUTRON if NEUTRON in fname else (
                                   KEYSTONE if KEYSTONE in fname else None)
                    modify_conf(fname, service_name, fname + '.modified')

                    # Keep the existing in .orig and copy the .modified 
                    # to the exisiting one.
                    cmd = 'cp %s %s.orig' % (fname, fname)
                    print cmd
                    commands.getoutput(cmd)
                    cmd = 'cp %s.modified %s' % (fname, fname)
                    print cmd
                    commands.getoutput(cmd)


def copy_init_conf_files(node):

    # TODO get the path from input arguments.
    # copy fabric_enabler_server.conf and fabric_enabler_agent.conf
    # to /etc/init.
    path = 'openstack_fabric_enabler/dfa/scripts/'
    if node == 'control':
        f = 'fabric_enabler_server.conf'
    else:
        f ='fabric_enabler_agent.conf'
        f2 = 'openstack_fabric_enabler/dfa/agent/detect_uplink.sh'
        cmd2 = 'sudo cp %s /usr/local/bin' % f2
        print cmd2
        commands.getoutput(cmd2)
    cmd = 'sudo cp %s /etc/init' % (path + f)
    print cmd
    commands.getoutput(cmd)


def copy_dfa_cfg():

    # TODO get the path from input arguments.
    path = 'openstack_fabric_enabler/'
    dfa_cfg = 'enabler_conf.ini'

    cmd = 'sudo cp %s /etc/' % (path + dfa_cfg)
    print cmd
    commands.getoutput(cmd)


usage = ('\n'
'python dfa_prepare_setup.py --dir-path filepath1[,filepath2,...]'
'[control | compute]\n')

if __name__ == '__main__':

    parser = optparse.OptionParser(usage=usage)

    parser.add_option('--dir-path',
                  type='string',
                  dest='dir_path',
                  default=default_path,
                  help='Path to neutron.conf and keystone.conf files')
    (options, args) = parser.parse_args()

    copy_dfa_cfg()
    node = 'compute'
    if 'control' in args:
        find_conf_and_modify(options.dir_path)
        prepare_db()
        node = 'control'

    copy_init_conf_files(node)
