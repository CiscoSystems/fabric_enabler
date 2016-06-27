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
import errno

startup_cmds = {
    'ubuntu': {
        'stop_agent': 'stop fabric-enabler-agent',
        'start_agent': 'start fabric-enabler-agent',
        'stop_keystone': 'service apache2 stop',
        'start_keystone': 'service apache2 start',
        'stop_server': 'stop fabric-enabler-server',
        'start_server': 'start fabric-enabler-server',
        'stop_neutron_server': 'stop neutron-server',
        'start_neutron_server': 'start neutron-server',
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
        self.mysql_user = mysql_user
        self.mysql_password = mysql_passwd
        self.mysql_host = mysql_host
        self.http_proxy = None
        self.https_proxy = None
        self.vendor_os_rel = None
        self.root_helper = '' if os.geteuid() == 0 else 'sudo '
        self.src_dir = os.path.basename(
            os.path.dirname(os.path.realpath(__file__)))
        self.ssh_client_log = '%s/paramiko.log' % self.src_dir
        self.uplink_file = "uplink"
        self.script_dir = '%s/dfa/scripts' % self.src_dir
        self.rm_uplink = '%s rm -f /tmp/uplink*' % self.root_helper
        self.cp_uplink = '[[ -e %s/%s ]] && cp %s/%s /tmp' % (
            self.src_dir, self.uplink_file,
            self.src_dir, self.uplink_file)
        self.run_dfa_prep_on_control = (
            '%s python %s/dfa_prepare_setup.py --node-function=control '
            '%s %s %s' % (
                self.root_helper, self.script_dir,
                '--mysql-user=' + mysql_user if mysql_user else '',
                '--mysql-password=' + mysql_passwd if mysql_passwd else '',
                '--mysql-host=' + mysql_host if mysql_host else ''))
        self.run_dfa_prep_on_hacontrol = (
            '%s python %s/dfa_prepare_setup.py --node-function=ha-control '
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
                            "python setup.py build;%spython setup.py bdist_egg;"
                            "%spython setup.py install" % (
                                self.src_dir, sudo_cmd, sudo_cmd))
        self.distr_name = platform.dist()[0].lower()
        self.run_lldpad = '%s %s/run_lldpad.sh %s' % (
            self.root_helper, self.script_dir, self.src_dir)
        self.cleanup = "cd %s ; %s rm -rf %s %s %s" % (
            self.src_dir,
            self.root_helper,
            "openstack_fabric_enabler.egg-info",
            "build",
            "dist")
        self.neutron_restart_procs = [
            'neutron-server']

    def restart_neutron_processes(self):
        print('    Restarting Neutron Processes   ')
        if (os.path.isfile('/etc/init/neutron-server.conf') or
           os.path.isfile('/usr/lib/systemd/system/neutron-server.service')):
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
                print e.output
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

    def create_sshClient(self, host, user, passwd=None):
        try:
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            paramiko.util.log_to_file(self.ssh_client_log)
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, username=user, password=passwd)
            return client
        except:
            print("Filed to create SSH client for %s %s" % (host, user))

    def copy_dir(self, target_host, target_user, target_password=None):
        """Copy source files into compute nodes for installation."""

        print("Copying dir " + self.src_dir + " to " + target_host)
        client = self.create_sshClient(target_host, target_user,
                                       target_password)
        if client is None:
            print("Failed to copy source files.")
            return

        scp_client = cisco_scp.cisco_SCPClient(client.get_transport())
        scp_client.set_verbose(False)
        scp_client.put(self.src_dir, recursive=True)
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

    def setup_control(self, hamode):
        """Install enabler package on control node."""

        output, returncode = self.run_cmd_line(
            self.run_dfa_prep_on_hacontrol if hamode else
            self.run_dfa_prep_on_control)
        print output
        output, returncode = self.run_cmd_line(self.install_pkg, shell=True)
        print output
        output, returncode = self.run_cmd_line(self.run_lldpad, shell=True, check_result=False)
        print output
        output, returncode = self.run_cmd_line(self.cleanup, shell=True, check_result=False)
        print output

        if self.vendor_os_rel == 'rhel-osp7':
            self.rhel_osp7_setup(hamode)
        else:
            print "restarting keystone"
            self.restart_keystone_process()
            time.sleep(10)
            self.restart_neutron_processes()
            time.sleep(10)
            if hamode is False:
                self.restart_fabric_enabler_server()
            self.restart_fabric_enabler_agent()

    def install_remote(self, command, host, user, password=None):
        """Invoke installation script on remote node."""

        print("Invoking installation on %s, please wait..." % (host))
        c = self.create_sshClient(host, user, password)
        if c is None:
            print "Could not connect to remote host %s" % (host)
            return
        c.get_transport().open_session().set_combine_stderr(True)
        ssh_stdin, ssh_stdout, ssh_stderr = c.exec_command(command,
                                                           get_pty=True)
        for i in ssh_stdout.readlines():
            print "(%s) %s" % (host, i.encode('utf-8')),
        c.close()

    def setup_control_remote(self, control_name, control_user,
                             control_password=None, ha_mode=False):
        """Invoke installation on remote control node."""

        self.copy_dir(control_name, control_user, control_password)
        cmd = "cd %s; yes | " % (self.src_dir)
        if self.http_proxy is not None:
            cmd += "http_proxy=%s " % (self.http_proxy)
        if self.https_proxy is not None:
            cmd += "https_proxy=%s " % (self.https_proxy)
        cmd += "python setup_enabler.py "
        if self.mysql_user is not None:
            cmd += "--mysql-user=%s " % (self.mysql_user)
        if self.mysql_password is not None:
            cmd += "--mysql-password=\"%s\" " % (self.mysql_password)
        if self.mysql_host is not None:
            cmd += "--mysql-host=%s " % (self.mysql_host)
        if self.vendor_os_rel is not None:
            cmd += "--vendor-os-release=%s " % (self.vendor_os_rel)
        cmd += "--controller-only=True "
        if ha_mode:
            cmd += "--ha-mode=True"
        else:
            cmd += "--ha-mode=False"
        self.install_remote(cmd, control_name, control_user, control_password)

    def setup_compute_remote(self, compute_name, compute_uplink,
                             compute_user, compute_password=None):
        """Invoke installation on remote compute node"""

        self.copy_dir(compute_name, compute_user, compute_password)
        cmd = "cd %s; yes | " % (self.src_dir)
        if self.http_proxy is not None:
            cmd += "http_proxy=%s " % (self.http_proxy)
        if self.https_proxy is not None:
            cmd += "https_proxy=%s " % (self.https_proxy)
        cmd += "python setup_enabler.py --compute-local=True "
        if compute_uplink is not None:
            cmd += "--uplink=%s" % (compute_uplink)
        if self.vendor_os_rel is not None:
            cmd += "--vendor-os-release=%s " % (self.vendor_os_rel)
        self.install_remote(cmd, compute_name, compute_user, compute_password)

    def setup_compute_local(self, input_compute_uplink):
        """Install Enabler on  local compute node"""

        script_list = [self.rm_uplink, self.cp_uplink,
                       self.run_dfa_prep_on_compute,
                       self.add_req_txt, self.install_pkg,
                       self.stop_agent, self.start_agent,
                       self.run_lldpad, self.cleanup]

        if input_compute_uplink is None:
            input_compute_uplink = 'auto'

        self.generate_uplink_file(input_compute_uplink)
        for script in script_list:
            self.run_cmd_line(script, shell=True, check_result=False)

    def setup_compute(self, input_compute_name, input_compute_uplink):
        """Install Enabler on computes in enabler_conf.ini or
        provided as input"""

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
            self.setup_compute_remote(compute_host, compute_uplink,
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

    def restart_fabric_enabler_agent(self):
        self.run_cmd_line(self.stop_agent, check_result=False)
        time.sleep(5)
        self.run_cmd_line(self.start_agent)

    def set_http_proxy(self, http_proxy):
        self.http_proxy = http_proxy

    def set_https_proxy(self, https_proxy):
        self.https_proxy = https_proxy

    def set_vendor_os_release(self, vendor_os_release):
        if vendor_os_release is None:
            return

        # Save value...
        self.vendor_os_rel = vendor_os_release

        # ...and modify commands run locally
        o = " --vendor-os-release=%s" % (vendor_os_release)
        self.run_dfa_prep_on_control += o
        self.run_dfa_prep_on_hacontrol += o
        self.run_dfa_prep_on_compute += o

    def rhel_osp7_setup(self, hamode):
        # Restart keystone/neutron
        print("Restarting keystone and neutron")
        cmds = ["pcs resource restart openstack-keystone",
                "pcs resource restart neutron-server"]
        for c in cmds:
            cmd = "%s %s" % (self.root_helper, c)
            o, rc = self.run_cmd_line(cmd, check_result=False)
            print(o)

        # Setup Pacemaker/Start resources
        pcs_resources = {
            'fabric-enabler-server':
            ["pcs resource create fabric-enabler-server systemd:fabric-enabler-server",
             "pcs resource meta fabric-enabler-server migration-threshold=1",
             "pcs constraint order start galera-master then start fabric-enabler-server",
             "pcs constraint order start rabbitmq-clone then start fabric-enabler-server",
             "pcs resource enable fabric-enabler-server"],
                'fabric-enabler-agent':
            ["pcs resource create fabric-enabler-agent systemd:fabric-enabler-agent --clone  interleave=true",
             "pcs constraint order start rabbitmq-clone then start fabric-enabler-agent-clone",
             "pcs resource enable fabric-enabler-agent"],
            'lldpad':
            ["pcs resource create lldpad systemd:lldpad --clone interleave=true",
             "pcs resource enable lldpad"]
        }
        if not hamode:
            print("Setting up and starting Pacemaker resources")
            for resource in pcs_resources:
                cmd = "%s pcs resource show %s 2>/dev/null" % \
                      (self.root_helper, resource)
                o, rc = self.run_cmd_line(cmd, check_result=False, shell=True)
                if o is None:
                    for c in pcs_resources[resource]:
                        cmd = "%s %s" % (self.root_helper, c)
                        o, rc = self.run_cmd_line(cmd, check_result=False)
                        print(o)
                else:
                    print(o)
        else:
            for resource in pcs_resources:
                cmd = "%s pcs resource cleanup %s" % \
                      (self.root_helper, resource)
                o, rc = self.run_cmd_line(cmd, check_result=False)
                print(o)

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("--ha-mode", default=None,
                        help="Set this value to True, if installing ONLY on "
                        "a controller node in an HA setup.")
    parser.add_argument("--compute-name", default=None,
                        help="Set this value to thecontrol name or ip to "
                        "install the Enabler on a remote compute node.")
    parser.add_argument("--control-name", default=None,
                        help="Set this value to the control name or ip to "
                        "install the Enabler on a remote control node.")
    parser.add_argument("--remote-user", default=None,
                        help="Remote user for ssh access.")
    parser.add_argument("--remote-password", default=None,
                        help="Remote password for ssh access.")
    parser.add_argument("--http-proxy", default=None,
                        help="HTTP proxy URL.")
    parser.add_argument("--https-proxy", default=None,
                        help="HTTPS proxy URL.")
    parser.add_argument("--compute-local", default=False,
                        help="Set this value to True, if installing ONLY on "
                        "a local compute node.")
    parser.add_argument("--controller-only", default=False,
                        help="Set this value to True, if installing only "
                        "on the controller.")
    parser.add_argument("--uplink", help="compute uplink to leaf switch")
    parser.add_argument("--mysql-user",
                        help="MySQL user name (only for control node)")
    parser.add_argument("--mysql-password",
                        help="MySQL passsword (only for control node)")
    parser.add_argument("--mysql-host",
                        help="MySQL Host name or IP address "
                        "(only for control node)")
    parser.add_argument("--vendor-os-release", default=None,
                        help="Vendor specific OS release, e.g. rhel-osp7.")

    args = parser.parse_args()
    input_compute_name = args.compute_name
    input_uplink = args.uplink
    hamode = True if args.ha_mode is not None \
             and args.ha_mode.lower() == 'true' else False
    local_compute = True if args.compute_local and \
                    args.compute_local.lower() == 'true' else False
    controller_only = True if args.controller_only and \
                      args.controller_only.lower() == 'true' or \
                      args.control_name is not None else False

    install_control = False if args.compute_local or \
                      args.compute_name is not None else True
    control_node = "n/a" if not install_control else \
                   args.control_name if args.control_name is not None else \
                   "remote" if args.vendor_os_release == 'rhel-osp7' and \
                   not args.controller_only \
                   else "local"

    if args.vendor_os_release == 'rhel-osp7' and \
       not local_compute and not controller_only \
       and args.control_name is None \
       and args.compute_name is None:
        if args.ha_mode is not None:
            print("!!! WARNING: --ha-mode will be ignored.")
            print("!!!         Installer will take care of proper HA config.")
        control_ha_mode = "auto"
        compute_nodes = "as per 'nova list' output"
    else:
        control_ha_mode = "n/a" if not install_control else args.ha_mode
        compute_nodes = "n/a" if controller_only \
                        else "local" if args.compute_local \
                        else args.compute_name \
                        if args.compute_name is not None \
                        else "as per enabler_conf.ini"

    print("This script will install the Openstack Fabric Enabler as follows:")
    print(" - install on control node: %s" % \
          ("yes" if install_control else "no"))
    print(" - control node(s): %s" % (control_node))
    print(" - control HA mode: %s" % (control_ha_mode))
    print(" - install on compute nodes: %s" %
        ("no" if controller_only else "yes"))
    print(" - compute node(s): %s" % (compute_nodes))
    print(" - uplink: %s" % ("auto" if input_uplink is None else input_uplink))
    try: 
        user_answer = raw_input("Would you like to continue(y/n)? ").lower()
        if user_answer.startswith('n'):
            sys.exit(1)
    except KeyboardInterrupt:
        print
        sys.exit(1)

    fabric_inst = NexusFabricEnablerInstaller(args.mysql_user,
                                              args.mysql_password,
                                              args.mysql_host)

    fabric_inst.set_http_proxy(args.http_proxy)
    fabric_inst.set_https_proxy(args.https_proxy)
    fabric_inst.set_vendor_os_release(args.vendor_os_release)

    # RHEL-OSP7 specific behavior
    if args.vendor_os_release == 'rhel-osp7':
        root_helper = '' if os.geteuid() == 0 else 'sudo '

        if args.remote_user is None:
            args.remote_user = 'heat-admin'

        extra_rpms_dir = "./extra-rpms"
        pkgs = ["lldpad.x86_64",
                "libconfig.x86_64"]

        if local_compute or (args.control_name is None and controller_only):
            # Install RPMs in extra_rpms_dir
            cmd = "%s rpm -ihv %s/*" % (root_helper, extra_rpms_dir)
            o, rc = fabric_inst .run_cmd_line(cmd, shell=True,
                                              check_result=False)
            if o is not None:
                print(o)
        else:
            # Get extra RPMs
            try:
                os.mkdir(extra_rpms_dir)
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise exc
            os.chdir(extra_rpms_dir)
            cmd = "%s yumdownloader %s" % (root_helper, " ".join(pkgs))
            o, rc = fabric_inst.run_cmd_line(cmd, check_result=True)
            if o is not None:
                print(o)
            os.chdir("../")

        if not local_compute and not controller_only \
           and args.control_name is None \
           and args.compute_name is None:
            # Install Fabric Enabler on controllers and computes
            os.chdir("../")
            first_controller = True
            cmd = "nova list | grep ctlplane= "
            o, rc = fabric_inst.run_cmd_line(cmd, shell=True,
                                             check_result=False)
            if o is None:
                print 'NOTICE: the script could not retrieve overcloud information'
                print '        This could be due to stackrc not being sourced'
                print '        or overcloud not being deployed.'
                print '        Please make sure overcloud is deployed and stackrc'
                print '        is sourced before running this command. Thank you.'
                sys.exit(1)
            print(o)
            for l in o.splitlines():
                node_type = None
                node_ip = None
                s = l.split('|')
                if 'compute' in s[2]:
                    node_type = 'compute'
                elif 'controller' in s[2]:
                    node_type = 'controller'
                node_ip = s[6].split('=')[1]
                print 'Installing Fabric Enabler on', node_type, node_ip
                if node_type == 'controller':
                    fabric_inst.setup_control_remote(node_ip,
                                                     args.remote_user,
                                                     args.remote_password,
                                                     not first_controller)
                    first_controller = False
                elif node_type == 'compute':
                    fabric_inst.setup_compute_remote(node_ip, input_uplink,
                                                     args.remote_user,
                                                     args.remote_password)
                else:
                    print('WARNING: unknown node type for', node_ip)
            # Done!
            sys.exit(0)
    elif args.vendor_os_release is not None:
        print 'ERROR: Vendor OS release %s is not supported' % (args.vendor_os_release)
        print '       Supported vendor OS releases are:'
        print '         - rhel-osp7'
        sys.exit(1)

    os.chdir("../")

    if local_compute:
        # Compute-only enabler installation
        fabric_inst.setup_compute_local(input_uplink)
        sys.exit(0)

    if input_compute_name is None:
        # Enabler installation on control node
        if args.control_name is None:
            fabric_inst.setup_control(hamode)
        else:
            fabric_inst.setup_control_remote(args.control_name,
                                             args.remote_user,
                                             args.remote_password,
                                             hamode)

    # Setup compute node.
    if not hamode and not controller_only:
        if args.remote_user is not None:
            fabric_inst.setup_compute_remote(input_compute_name,
                                             input_uplink,
                                             args.remote_user,
                                             args.remote_password)
        else:
            fabric_inst.setup_compute(input_compute_name, input_uplink)
