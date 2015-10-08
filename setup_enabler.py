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


import argparse
import os
import re
import subprocess
import sys
import platform
import time
import ConfigParser
import cisco_scp
import paramiko


startup_cmds = {
    'ubuntu': {
        'stop_agent': 'stop fabric-enabler-agent',
        'start_agent': 'start fabric-enabler-agent',
        'stop_keystone': 'service apache2 stop',
        'start_keystone': 'service apache2 start',
        'stop_server': 'stop fabric-enabler-server',
        'start_server': 'start fabric-enabler-server',
        'get_pty': False,
    },
    'redhat': {
        'stop_agent': 'systemctl stop fabric-enabler-agent',
        'start_agent': 'systemctl start fabric-enabler-agent',
        'stop_keystone': 'systemctl stop openstack-keystone',
        'start_keystone': 'systemctl start openstack-keystone',
        'stop_server': 'systemctl stop fabric-enabler-server',
        'start_server': 'systemctl start fabric-enabler-server',
        'stop_neutron_server': 'systemctl stop neutron-server',
        'start_neutron_server': 'systemctl start neutron-server',
        'get_pty': True,
    },
    'centos': {
        'stop_agent': 'systemctl stop fabric-enabler-agent',
        'start_agent': 'systemctl start fabric-enabler-agent',
        'stop_keystone': 'systemctl stop httpd',
        'start_keystone': 'systemctl start httpd',
        'stop_server': 'systemctl stop fabric-enabler-server',
        'start_server': 'systemctl start fabric-enabler-server',
        'stop_neutron_server': 'systemctl stop neutron-server',
        'start_neutron_server': 'systemctl start neutron-server',
        'get_pty': True,
    }
}


class NexusFabricEnablerInstaller(object):
    """Represents Fabric Enabler Installation."""

    def __init__(self, mysql_user, mysql_passwd, mysql_host):
        self.root_helper = '' if os.geteuid() == 0 else 'sudo '
        self.src_dir = 'openstack_fabric_enabler'
        self.ssh_client_log = '%s/paramiko.log' % self.src_dir
        self.uplink_file = "uplink"
        self.script_dir = '%s/dfa/scripts' % self.src_dir
        self.rm_uplink = '%s rm -f /tmp/uplink*' % self.root_helper
        self.cp_uplink = 'cp %s/%s /tmp' % (self.src_dir, self.uplink_file)
        self.run_dfa_prep_on_control = (
            '%s python %s/dfa_prepare_setup.py --node-function=control '
            '%s %s %s' % (
                self.root_helper, self.script_dir,
                '--mysql-user=' + mysql_user if mysql_user else '',
                '--mysql-password=' + mysql_passwd if mysql_passwd else '',
                '--mysql-host=' + mysql_host if mysql_host else ''))
        self.run_dfa_prep_on_compute = ('%s python %s/dfa_prepare_setup.py '
                                        '--node-function=compute' % (
                                            self.root_helper, self.script_dir))
        self.add_req_txt = 'touch %s/requirements.txt' % self.src_dir
        sudo_cmd = (self.root_helper + '-E ') if self.root_helper else ''
        self.install_pkg = ("cd  %s;"
                            "python setup.py build;python setup.py bdist_egg;"
                            "%spython setup.py install" % (
                                self.src_dir, sudo_cmd))
        self.distr_name = platform.dist()[0].lower()
        self.run_lldpad = '%s %s/run_lldpad.sh %s' % (
            self.root_helper, self.script_dir, self.src_dir)
        self.neutron_restart_procs = [
            'neutron-server']

    def restart_neutron_processes(self):
        print('    Restarting Neutron Processes   ')
        if (os.path.isfile('/etc/init/neutron-server.conf') or
           os.path.isfile('/usr/lib/systemd/system/neutron-server.service')):
            if self.distr_name == 'redhat' or self.distr_name == 'centos':
                self.run_cmd_line(self.stop_neutron_server,
                                  check_result=False)
                time.sleep(10)
                self.run_cmd_line(self.start_neutron_server,
                                  check_result=False)
        else:
            reg_exes = {}
            for proc in self.neutron_restart_procs:
                reg_exes[proc] = re.compile(
                    "^(?P<uid>\S+)\s+(?P<pid>\d+)\s+(?P<ppid>\d+)."
                    "*python(?P<cmd>.*%s.*)" % proc)
            ps_output, rc = self.run_cmd_line('ps -ef')
            for line in ps_output.splitlines():
                for proc, reg_ex in reg_exes.items():
                    result = reg_ex.search(line)
                    if result:
                        print 'Restarting ', proc
                        # Kill the process
                        kill_cmd = ''.join((self.root_helper,
                                            ('kill -9 %d' % (
                                                int(result.group('pid'))))))
                        self.run_cmd_line(kill_cmd)
                        cmd = result.group('cmd') + ' > %s/%s 2>&1 &' % (
                            self.src_dir, 'enabler_neutron_svc.log')
                        print cmd
                        os.system(cmd)
            print 'Neutron processes: '
            ps_output, rc = self.run_cmd_line('ps -ef')
            for line in ps_output.splitlines():
                for proc, reg_ex in reg_exes.items():
                    result = reg_ex.search(line)
                    if result:
                        print line

    def run_cmd_line(self, cmd_str, stderr=None, shell=False,
                     echo_cmd=True, check_result=True):
        if echo_cmd:
            print cmd_str
        if shell:
            cmd_args = cmd_str
        else:
            cmd_args = cmd_str.split()
        output = None
        returncode = 0
        try:
            output = subprocess.check_output(cmd_args, shell=shell,
                                             stderr=stderr)
        except subprocess.CalledProcessError as e:
            if check_result:
                print e
                sys.exit(e.returncode)
            else:
                returncode = e.returncode
        return output, returncode

    def find_computes(self):
        """Returns commpute nodes in the setup."""

        compute_list = []
        nova_manage = ''.join((self.root_helper, "nova-manage service list"))
        output, returncode = self.run_cmd_line(nova_manage)
        output_list = output.split('\n')
        for ent in output_list:
            novac = ent.split()
            if len(novac) != 0 and novac[0] == 'nova-compute':
                compute_list.append(novac[1])
        return compute_list

    def parse_config(self):
        """Parses enabler config file.

        It returns compute nodes credentails and also list of compute nodes
        and uplink interfaces, if they are defined.
        """

        compute_name_list = None
        compute_uplink_list = None
        configfile = '/etc/saf/enabler_conf.ini'
        if os.path.exists(configfile) is False:
            print "Config file %s is missing\n" % configfile
            sys.exit(1)

        config = ConfigParser.ConfigParser()
        config.read(configfile)
        try:
            compute_names = config.get("compute", "node")
            if compute_names:
                compute_name_list = compute_names.split(',')
            compute_uplinks = config.get("compute", "node_uplink")
            if compute_uplinks:
                compute_uplink_list = compute_uplinks.split(',')
        except:
            pass
        return (config.get("general", "compute_user"),
                config.get("general", "compute_passwd"),
                compute_name_list, compute_uplink_list)

    def create_sshClient(self, host, user, passwd):
        try:
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            paramiko.util.log_to_file(self.ssh_client_log)
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, username=user, password=passwd)
            return client
        except:
            print("Filed to create SSH client for %s %s" % (host, user))

    def copy_dir(self, compute_host, compute_uplink, compute_user,
                 compute_passwd):
        """Copy source files into compute nodes for installation."""

        print("Copying dir " + self.src_dir + " to " + compute_host)
        client = self.create_sshClient(compute_host,  compute_user,
                                       compute_passwd)
        if client is None:
            print("Failed to copy source files.")
            return

        scp_client = cisco_scp.cisco_SCPClient(client.get_transport())
        scp_client.put(self.src_dir, recursive=True)
        client.close()

    def invoke_scripts(self, compute_host, compute_uplink, compute_user,
                       compute_passwd):
        """Installs enabler package on compute node."""

        script_list = [self.rm_uplink, self.cp_uplink,
                       self.run_dfa_prep_on_compute,
                       self.add_req_txt, self.install_pkg,
                       self.stop_agent, self.start_agent, self.run_lldpad]

        client = self.create_sshClient(compute_host, compute_user,
                                       compute_passwd)
        if client is None:
            return

        for script in script_list:
            print("invoking script " + script + " on " + compute_host)
            ssh_stdin, ssh_stdout, ssh_stderr = client.exec_command(
                script, get_pty=self.get_pty)
            ssh_stdin.write(compute_passwd)
            ssh_stdin.write('\n')
            for line in ssh_stdout:
                print(line)
            for line in ssh_stderr:
                print(line)
        client.close()

    def generate_uplink_file(self, compute_uplink):

        uplink_file_str = self.src_dir + '/' + self.uplink_file

        if "auto" in compute_uplink.lower():
            if os.path.isfile(uplink_file_str):
                os.remove(uplink_file_str)
        else:
            filep = open(uplink_file_str, "w")
            filep.write(compute_uplink)
            filep.close()

    def setup_control(self):
        """Install enabler package on control node."""

        output, returncode = self.run_cmd_line(self.run_dfa_prep_on_control)
        print output
        output, returncode = self.run_cmd_line(self.install_pkg, shell=True)
        print output

    def setup_compute(self, input_compute_name, input_compute_uplink):

        compute_user, compute_passwd, compute_list, compute_uplinks = (
            self.parse_config())
        if input_compute_name is not None:
            compute_list = []
            compute_list.append(input_compute_name)
        if input_compute_uplink is not None:
            compute_uplinks = []
            compute_uplinks.append(input_compute_uplink)
        if compute_user is not None:
            if compute_list is None:
                print ("The user did not specify compute list ,"
                       "will auto detect.\n")
                compute_list = self.find_computes()

        if compute_uplinks is None:
            compute_uplinks = ['auto']

        while (len(compute_uplinks) < len(compute_list)):
            print("Will use the last uplink ports for the rest of "
                  "compute nodes")
            compute_uplinks.append(compute_uplinks[-1])
        print('Compute User: %s' % compute_user)
        print('Compute nodes: %s' % compute_list)
        print('Uplinks : %s' % compute_uplinks)
        for compute_host, compute_uplink in zip(compute_list, compute_uplinks):
            self.generate_uplink_file(compute_uplink)
            self.copy_dir(compute_host, compute_uplink, compute_user,
                          compute_passwd)
            self.invoke_scripts(compute_host, compute_uplink,
                                compute_user, compute_passwd)

    @property
    def stop_neutron_server(self):
        if startup_cmds[self.distr_name]:
            return ''.join((self.root_helper,
                            startup_cmds[self.distr_name].get(
                                'stop_neutron_server')))

    @property
    def start_neutron_server(self):
        if startup_cmds[self.distr_name]:
            return ''.join((self.root_helper,
                            startup_cmds[self.distr_name].get(
                                'start_neutron_server')))

    @property
    def stop_keystone(self):
        if startup_cmds[self.distr_name]:
            return ''.join((self.root_helper,
                            startup_cmds[self.distr_name].get(
                                'stop_keystone')))

    @property
    def start_keystone(self):
        if startup_cmds[self.distr_name]:
            return ''.join((self.root_helper,
                            startup_cmds[self.distr_name].get(
                                'start_keystone')))

    @property
    def stop_server(self):
        if startup_cmds[self.distr_name]:
            return ''.join((self.root_helper,
                            startup_cmds[self.distr_name].get('stop_server')))

    @property
    def start_server(self):
        if startup_cmds[self.distr_name]:
            return ''.join((self.root_helper,
                            startup_cmds[self.distr_name].get('start_server')))

    @property
    def stop_agent(self):
        if startup_cmds[self.distr_name]:
            return ''.join((self.root_helper,
                            startup_cmds[self.distr_name].get('stop_agent')))

    @property
    def start_agent(self):
        if startup_cmds[self.distr_name]:
            return ''.join((self.root_helper,
                            startup_cmds[self.distr_name].get('start_agent')))

    @property
    def get_pty(self):
        if startup_cmds[self.distr_name]:
            return startup_cmds[self.distr_name].get('get_pty')

    def restart_keystone_process(self):
        self.run_cmd_line(self.stop_keystone, check_result=False)
        time.sleep(5)
        self.run_cmd_line(self.start_keystone, check_result=False)

    def restart_fabric_enabler_server(self):
        self.run_cmd_line(self.stop_server, check_result=False)
        time.sleep(5)
        self.run_cmd_line(self.start_server)

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("--compute-name", help="compute name or ip")
    parser.add_argument("--uplink", help="compute uplink to leaf switch")
    parser.add_argument("--mysql-user",
                        help="MySQL user name (only for control node)")
    parser.add_argument("--mysql-password",
                        help="MySQL passsword (only for control node)")
    parser.add_argument("--mysql-host",
                        help="MySQL Host name or IP address "
                        "(only for control node)")

    args = parser.parse_args()
    input_compute_name = args.compute_name
    input_uplink = args.uplink
    if input_uplink is None:
        input_uplink_converted = "auto"

    if input_compute_name is None:
        print("This script will setup openstack fabric enabler on control "
              "and compute nodes.")
    else:
        print("This script will setup openstack fabric enabler on compute "
              "node %s with uplink %s" % (
                  input_compute_name, input_uplink_converted))

    user_answer = raw_input("Would you like to continue(y/n)? ").lower()
    if user_answer.startswith('n'):
        sys.exit(1)

    os.chdir("../")

    fabric_inst = NexusFabricEnablerInstaller(args.mysql_user,
                                              args.mysql_password,
                                              args.mysql_host)

    if input_compute_name is None:
        # If no compute node is specified, enabler is installed on controller
        # node and then compute node.
        fabric_inst.setup_control()
        print "restarting keystone"
        fabric_inst.restart_keystone_process()
        time.sleep(10)
        fabric_inst.restart_neutron_processes()
        time.sleep(10)
        fabric_inst.restart_fabric_enabler_server()

    # Setup compute node.
    fabric_inst.setup_compute(input_compute_name, input_uplink)
