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
# @author: Nader Lahouti, Cisco Systems, Inc.


from __future__ import print_function

import ConfigParser
import optparse
import os
import platform
import re
import shlex
import subprocess as subp
import sys


NEUTRON = 'neutron'
KEYSTONE = 'keystone'

conf_file_list = [
    'keystone.conf',
    'neutron.conf'
]
default_path = '/etc/neutron,/etc/keystone'
dfa_cfg_file = 'enabler_conf.ini'
mysqlcnf = '.my.cnf'

dfa_neutron_option_list = [
    {'section': 'DEFAULT',
     'option': 'rpc_backend',
     'value': 'rabbit',
     'is_list': False},
    {'section': 'DEFAULT',
     'option': 'notification_driver',
     'value': 'messaging',
     'is_list': False},
    {'section': 'DEFAULT',
     'option': 'notification_topics',
     'value': 'cisco_dfa_neutron_notify',
     'is_list': True},
]
dfa_keystone_option_list = [
    {'section': 'DEFAULT',
     'option': 'rpc_backend',
     'value': 'rabbit',
     'is_list': False},
    {'section': 'DEFAULT',
     'option': 'notification_driver',
     'value': 'messaging',
     'is_list': False},
    {'section': 'DEFAULT',
     'option': 'notification_topics',
     'value': 'cisco_dfa_keystone_notify',
     'is_list': True},
]

service_options = {
    'neutron': dfa_neutron_option_list,
    'keystone': dfa_keystone_option_list,
}


dist_data = {
    'ubuntu': {'init_dir': '/etc/init/',
               'server_conf': 'fabric-enabler-server.conf',
               'agent_conf': 'fabric-enabler-agent.conf'},
    'centos': {'init_dir': '/usr/lib/systemd/system/',
               'server_conf': 'fabric-enabler-server.service',
               'agent_conf': 'fabric-enabler-agent.service'},
    'redhat': {'init_dir': '/usr/lib/systemd/system/',
               'server_conf': 'fabric-enabler-server.service',
               'agent_conf': 'fabric-enabler-agent.service',
               'host': '%'},
}


def get_cmd_output(cmd, check_result=True):
    output = None
    final_cmd = shlex.split(cmd)
    try:
        output = subp.check_output(final_cmd)
    except subp.CalledProcessError as exc:
        if check_result:
            print("Error running %s: error: %s, output: %s" % (
                cmd, exc.returncode, exc.output))
            sys.exit(0)
    except Exception as exc:
        print("Exception %s running command %s" % (cmd, exc))
        sys.exit(0)

    return output


def get_db_credentials(cfg_file):
    """Get the credentials and database name from options in config file."""

    cfgfile = (os.path.dirname(os.path.dirname(os.path.abspath(__file__))) +
               '/../' + cfg_file)
    try:
        parser = ConfigParser.ConfigParser()
        cfg_fp = open(cfgfile)
        parser.readfp(cfg_fp)
        cfg_fp.close()
    except ConfigParser.NoOptionError:
        cfg_fp.close()
        print('Failed to find mysql connections credentials.')
        sys.exit(1)
    except IOError:
        print('ERROR: Cannot open %s.', cfg_file)
        sys.exit(1)

    try:
        value = parser.get('dfa_mysql', 'connection')

        # Find location of pattern in connection parameter as shown below:
        # http://username:password@host/databasename?characterset=encoding'
        sobj = re.search(r"(://).*(@).*(/).*(\?)", value)

        # The list parameter contains:
        # indices[0], is the index of '://'
        # indices[1], is the index of '@'
        # indices[2], is the index of '/'
        # indices[3], is the index of '?'
        indices = [sobj.start(1), sobj.start(2), sobj.start(3), sobj.start(4)]

        # Get the credentials
        cred = value[indices[0] + 3:indices[1]].split(':')

        # Get the host name
        host = value[indices[1] + 1:indices[2]]
        dist = platform.dist()[0].lower()
        if dist in dist_data:
            host = (
                dist_data[dist].get('host')
                if dist_data[dist].get('host') else host)

        # Get the database name
        db_name = value[indices[2] + 1:indices[3]]

        # Get the character encoding
        charset = value[indices[3] + 1:].split('=')[1]

        return cred[0], cred[1], host, db_name, charset
    except ConfigParser.NoOptionError as e:
        print('ERROR: %s file error: %s' % (cfgfile, e))
        sys.exit(1)
    except (ValueError, IndexError, AttributeError):
        print('ERROR: failed to find mysql connections credentials.')
        sys.exit(1)


def modify_conf(cfgfile, service_name, outfn):
    """Modify config file neutron and keystone to include enabler options."""

    if not cfgfile or not outfn:
        print('ERROR: There is no config file.')
        sys.exit(0)

    options = service_options[service_name]
    with open(cfgfile, 'r') as cf:
        lines = cf.readlines()

    for opt in options:
        op = opt.get('option')
        res = [line for line in lines if re.match("$op\s*=", line)]
        if len(res) > 1:
            print('ERROR: There are more than one %s option.' % res)
            sys.exit(0)
        if res:
            (op, sep, val) = (res[0].strip('\n').replace(' ', '').
                              partition('='))
            new_val = None
            if opt.get('is_list'):
                # Value for this option can contain list of values.
                # Append the value if it does not exist.
                if not any(opt.get('value') == value for value in
                           val.split(',')):
                    new_val = ','.join((val, opt.get('value')))
            else:
                if val != opt.get('value'):
                    new_val = opt.get('value')
            if new_val:
                opt_idx = lines.index(res[0])
                # The setting is different, replace it with new one.
                lines.pop(opt_idx)
                lines.insert(opt_idx, '='.join((opt.get('option'),
                             new_val + '\n')))
        else:
            # Option does not exist. Add the option.
            try:
                sec_idx = lines.index('[' + opt.get('section') + ']\n')
                lines.insert(sec_idx + 1, '='.join(
                    (opt.get('option'), opt.get('value') + '\n')))
            except ValueError:
                print('Invalid %s section name.' % opt.get('section'))
                sys.exit(0)

    with open(outfn, 'w') as fwp:
        all_lines = ''
        for line in lines:
            all_lines += line

        fwp.write(all_lines)


def prepare_db(mysql_user, mysql_pass, mysql_host):

    (user, password, host, db, charset) = get_db_credentials(dfa_cfg_file)

    # Modify max_connections, if it is not 2000
    mysql_cmd = ('mysql -u%s -h%s ') % (mysql_user, mysql_host)
    if mysql_pass is not None:
        mysql_cmd = mysql_cmd + '-p%s ' % (mysql_pass)
    get_var_cmd = (mysql_cmd +
                   '-e "show variables like \'max_connections\';"')
    out = get_cmd_output(get_var_cmd)
    try:
        val = int(out.split('\n')[1].split('\t')[1])
    except Exception:
        print('Invalid value: Cannot get max_connections from DB.')
        sys.exit(0)

    print('max_connections for mysql = %s' % val)
    if val < 2000:
        # Set max_connections to 2000 if it is not.
        set_conn_cmd = (mysql_cmd +
                        '-e "set global max_connections = 2000;"')
        out = get_cmd_output(set_conn_cmd)
        print(out)

    # Create database if it not existed.
    create_cmd = (mysql_cmd +
                  '-e "CREATE DATABASE IF NOT EXISTS %(db)s '
                  'CHARACTER SET %(charset)s;"' % (
                      {'user': user, 'password': password, 'host': host,
                       'db': db, 'charset': charset}))
    out = get_cmd_output(create_cmd)
    print(out)

    # Create user for enabler if it does not exist.
    check_user_cmd = (mysql_cmd + '-e '
                      '"SELECT EXISTS(SELECT DISTINCT user FROM mysql.user'
                      ' WHERE user=\'%s\' AND host=\'%s\')as user;"' % (
                          user, host))
    out = get_cmd_output(check_user_cmd)
    if int(out.split()[1]) == 0:
        # User does not exist. Create new one.
        create_user_cmd = (mysql_cmd + '-e '
                           '"CREATE USER \'%(user)s\'@\'%(host)s\''
                           'IDENTIFIED BY \'%(pwd)s\';"' % {
                               'user': user, 'host': host, 'pwd': password})
        out = get_cmd_output(create_user_cmd)
        if 'ERROR' in out:
            print('Failed to create %(user)s in MySQL.\n%(reason)s') % (
                {'user': user, 'reason': out})
            sys.exit(0)

    # Grant permission to the user.
    grant_perm_cmd = (mysql_cmd + "-e "
                      "\"GRANT  ALL PRIVILEGES ON *.* TO "
                      "'%(user)s'@'%(host)s';\"") % (
                          {'user': user, 'host': host})
    out = get_cmd_output(grant_perm_cmd)
    if 'ERROR' in out:
        print('Failed to grant permission to %(user)s.\n%(reason)s') % (
            {'user': user, 'reason': out})
        sys.exit(0)


def find_conf_and_modify(os_path, root_helper):

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
                    cmd = root_helper + 'cp %s %s.orig' % (fname, fname)
                    print(cmd)
                    get_cmd_output(cmd)
                    cmd = root_helper + 'cp %s.modified %s' % (fname, fname)
                    print(cmd)
                    get_cmd_output(cmd)


def copy_init_conf_files(node, root_helper):

    # Copy fabric-enabler-server and fabric-enabler-agent
    # to init directory based on Linux distribution.
    path = (os.path.dirname(os.path.dirname(os.path.abspath(__file__))) +
            '/scripts/')
    dist = platform.dist()[0].lower()
    if dist not in dist_data:
        print('This %s Linux distribution is not supported.') % dist
        sys.exit(1)

    init_dir = dist_data[dist].get('init_dir')
    conf_files = []
    if node == 'control':
        conf_files.append(dist_data[dist].get('server_conf'))
        conf_files.append(dist_data[dist].get('agent_conf'))
    if node == 'compute':
        conf_files.append(dist_data[dist].get('agent_conf'))

    for conf_fn in conf_files:
        cmd = root_helper + 'cp %s %s' % ((path + conf_fn), init_dir)
        print(cmd)
        get_cmd_output(cmd)
        if dist == 'centos' or dist == 'redhat':
            cmd3 = root_helper + 'systemctl enable %s' % conf_fn
            print(cmd3)
            get_cmd_output(cmd3)


def get_mysql_cred():

    mysql_user = None
    mysql_password = None
    mysql_host = None
    mysqlconf = os.path.join(os.path.expanduser('~'), mysqlcnf)
    if os.path.exists(mysqlconf) is True:
        config = ConfigParser.ConfigParser()
        config.read(mysqlconf)
        try:
            user = config.get("client", "user")
            mysql_user = user[1:-1] if (user[0] == user[-1] == '"' or
                                        user[0] == user[-1] == "'") else user
            password = config.get("client", "password")
            mysql_password = password[1:-1] if (
                password[0] == password[-1] == '"' or
                password[0] == password[-1] == "'") else password
            mysql_host = 'localhost'
        except:
            print('Cannot find %s' % mysqlconf)

    return mysql_user, mysql_password, mysql_host

def do_rhel_osp7_customization(root_helper):
    # Invoke script to do config file customization
    path = (os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + '/scripts/')
    for f in  ["/etc/neutron/neutron.conf",
               "/usr/share/neutron/neutron-dist.conf",
               "/usr/share/nova/nova-dist.conf",
               "/etc/haproxy/haproxy.cfg"]:
        cmd = "%s cp %s %s.bak" % (root_helper, f, f)
        print(cmd)
        get_cmd_output(cmd)
    cmd = "%s python %s/%s" % (root_helper, path,
                               "dfa_config_rhel_osp7.py")
    get_cmd_output(cmd)
    
    # Restart HA proxy
    cmd = "%s systemctl restart haproxy" % (root_helper)
    get_cmd_output(cmd)

def upgrade_database(root_helper):
    pass

usage = ('\n'
         'python dfa_prepare_setup.py --dir-path filepath1[,filepath2,...]'
         '--node-function [control | compute]\n')

if __name__ == '__main__':

    root_helper = ''
    if os.geteuid() != 0:
        # This is not root
        root_helper = 'sudo '

    mysqluser, mysqlpass, mysqlhost = get_mysql_cred()

    parser = optparse.OptionParser(usage=usage)

    parser.add_option('--dir-path',
                      type='string', dest='dir_path', default=default_path,
                      help='Path to neutron.conf and keystone.conf files')
    parser.add_option('--node-function',
                      type='string', dest='node_function', default='control',
                      help='Choose the node runs as controller or compute.')
    parser.add_option('--mysql-user',
                      type='string', dest='mysql_user', default=mysqluser,
                      help='MySQL user name (only for a controller node.')
    parser.add_option('--mysql-host',
                      type='string', dest='mysql_host', default=mysqlhost,
                      help='MySQL host name or IP address'
                      '(only for a controller node.')
    parser.add_option('--mysql-password',
                      type='string', dest='mysql_pass', default=mysqlpass,
                      help='MySQL password (only for a controller node.')
    parser.add_option('--vendor-os-release',
                      type='string', dest='vendor_os_release', default=None,
                      help='Vendor specific OS release, e.g. rhel-osp7')
    parser.add_option('--upgrade', default=None,
                      help='Set to True if upgrading an existing installation')
    (options, args) = parser.parse_args()

    node = options.node_function.lower()
    upgrade = True if options.upgrade is not None \
              and options.upgrade.lower() == 'true' else False

    if upgrade:
        if node == 'control':
            upgrade_database(root_helper)
    else:
        if node == 'control':
            if options.mysql_host is None or options.mysql_user is None:
                mysqlconf = os.path.join(os.path.expanduser('~'), mysqlcnf)
                print("Cannot find %s" % mysqlconf)
                print("MySQL credentials must be provided when setting up "
                      "a controller node.\nUse --help for more help.")
                sys.exit(1)

            find_conf_and_modify(options.dir_path.lower(), root_helper)
            prepare_db(options.mysql_user, options.mysql_pass, options.mysql_host)
        if node == 'ha-control':
            find_conf_and_modify(options.dir_path.lower(), root_helper)
            node = 'control'

        if (node == 'control' or node == 'ha-control') \
           and options.vendor_os_release == "rhel-osp7":
            dist = platform.dist()[0].lower()
            if dist == 'redhat':
                do_rhel_osp7_customization(root_helper)
            else:
                print("WARNING: no RedHat system, customization skipped.")

        copy_init_conf_files(node, root_helper)
