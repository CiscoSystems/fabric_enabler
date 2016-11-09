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
# @author: Padmanabhan Krishnan, Cisco Systems, Inc.

"""Common Routines used by DFA enabler"""

import os
import shlex
from eventlet import greenthread
import netifaces
import signal
from dfa.common import constants as q_const
from eventlet.green import subprocess
from dfa.common import dfa_logger as logging

LOG = logging.getLogger(__name__)

# Default timeout for ovs-vsctl command
DEFAULT_OVS_VSCTL_TIMEOUT = 10


class InvalidInput():
    message = ("Invalid input for operation: %(error_message)s.")


def is_valid_vlan_tag(vlan):
    return q_const.MIN_VLAN_TAG <= vlan <= q_const.MAX_VLAN_TAG


def get_bridges(root_helper):
    args = ["ovs-vsctl", "--timeout=%d" % DEFAULT_OVS_VSCTL_TIMEOUT, "list-br"]
    try:
        return execute(args, root_helper=root_helper).strip().split("\n")
    except Exception as e:
        LOG.error("Unable to retrieve bridges. Exception: %s", e)


def is_patch(root_helper, port):
    args = ["ovs-vsctl", "--timeout=%d" % DEFAULT_OVS_VSCTL_TIMEOUT, "get",
            "Interface", port, "type"]
    try:
        output = execute(args, root_helper=root_helper).strip().split("\n")
    except Exception as e:
        LOG.error("Unable to retrieve Interface type")
        return False
    if 'patch' in output:
        return True
    else:
        return False


def get_peer(root_helper, port):
    args = ["ovs-vsctl", "--timeout=%d" % DEFAULT_OVS_VSCTL_TIMEOUT, "get",
            "Interface", port, "options"]
    try:
        output = execute(args, root_helper=root_helper).strip().split("\n")
        output1 = output[0].split("=")[1].strip('}')
    except Exception as e:
        LOG.error("Unable to retrieve Peer")
        return None
    return output1


def get_bridge_name_for_port_name_glob(root_helper, port_name):
    try:
        args = ["ovs-vsctl", "--timeout=%d" % DEFAULT_OVS_VSCTL_TIMEOUT,
                "port-to-br", port_name]
        output = execute(args, root_helper=root_helper)
        return output
    except RuntimeError as e:
        LOG.error("Error Running vsctl for getting bridge name for portname")
        return False


def port_exists_glob(root_helper, port_name):
    output = get_bridge_name_for_port_name_glob(root_helper, port_name)
    port_exists = bool(output)
    if port_exists:
        return output.strip(), port_exists
    else:
        return output, port_exists


def delete_port_glob(root_helper, br_ex, port_name):
    try:
        args = ["ovs-vsctl", "--timeout=%d" % DEFAULT_OVS_VSCTL_TIMEOUT, "--",
                "--if-exists", "del-port", br_ex, port_name]
        execute(args, root_helper=root_helper)
    except RuntimeError as e:
        LOG.error("Error Running vsctl for port delete")


class BaseOVS(object):

    def __init__(self, root_helper):
        self.root_helper = root_helper
        self.vsctl_timeout = DEFAULT_OVS_VSCTL_TIMEOUT

    def run_vsctl(self, args, check_error=False):
        full_args = ["ovs-vsctl", "--timeout=%d" % self.vsctl_timeout] + args
        try:
            return execute(full_args, root_helper=self.root_helper)
        except Exception as e:
            LOG.error("Unable to execute %(cmd)s. "
                      "Exception: %(exception)s",
                      {'cmd': full_args, 'exception': e})

    def add_bridge(self, bridge_name):
        self.run_vsctl(["--", "--may-exist", "add-br", bridge_name])
        return OVSBridge(bridge_name, self.root_helper)

    def delete_bridge(self, bridge_name):
        self.run_vsctl(["--", "--if-exists", "del-br", bridge_name])

    def bridge_exists(self, bridge_name):
        try:
            self.run_vsctl(['br-exists', bridge_name], check_error=True)
        except RuntimeError as e:
            return False
        return True

    def get_bridge_name_for_port_name(self, port_name):
        try:
            return self.run_vsctl(['port-to-br', port_name], check_error=True)
        except RuntimeError as e:
            LOG.error("Error Running vsctl")
            return False

    def port_exists(self, port_name):
        return bool(self.get_bridge_name_for_port_name(port_name))


class OVSBridge(BaseOVS):

    def __init__(self, br_name, root_helper):
        super(OVSBridge, self).__init__(root_helper)
        self.br_name = br_name

    def set_secure_mode(self):
        self.run_vsctl(['--', 'set-fail-mode', self.br_name, 'secure'],
                       check_error=True)

    def create(self):
        self.add_bridge(self.br_name)

    def destroy(self):
        self.delete_bridge(self.br_name)

    def add_port(self, port_name):
        self.run_vsctl(["--", "--may-exist", "add-port", self.br_name,
                        port_name])
        return self.get_port_ofport(port_name)

    def delete_port(self, port_name):
        self.run_vsctl(["--", "--if-exists", "del-port", self.br_name,
                        port_name])

    def set_db_attribute(self, table_name, record, column, value):
        args = ["set", table_name, record, "%s=%s" % (column, value)]
        self.run_vsctl(args)

    def clear_db_attribute(self, table_name, record, column):
        args = ["clear", table_name, record, column]
        self.run_vsctl(args)

    def run_ofctl(self, cmd, args, process_input=None):
        full_args = ["ovs-ofctl", cmd, self.br_name] + args
        try:
            return execute(full_args, root_helper=self.root_helper,
                           process_input=process_input)
        except Exception as e:
            LOG.error("Unable to execute %(cmd)s. Exception: %(exception)s",
                      {'cmd': full_args, 'exception': e})

    def remove_all_flows(self):
        self.run_ofctl("del-flows", [])

    def get_port_ofport(self, port_name):
        ofport = self.db_get_val("Interface", port_name, "ofport")
        # This can return a non-integer string, like '[]' so ensure a
        # common failure case
        try:
            int(ofport)
            return ofport
        except (ValueError, TypeError):
            return q_const.INVALID_OFPORT

    def get_port_vlan_tag(self, port_name):
        vlan_tag = self.db_get_val("port", port_name, "tag")
        # This can return a non-integer string, like '[]' so ensure a
        # common failure case
        try:
            int(vlan_tag)
            return vlan_tag
        except (ValueError, TypeError):
            return q_const.INVALID_VLAN

    def get_ofport_name(self, iface_uuid):
        ext_str = "external_ids:iface-id=" + iface_uuid
        try:
            output = self.run_vsctl(["--columns=name", "find", "Interface",
                                     ext_str])
            return output.split()[2].strip('\"')
        except Exception:
            LOG.error("Unable to retrieve ofport name on %(iface-id)s",
                      {'iface-id': iface_uuid})
            return None

    def do_action_flows(self, action, kwargs_list):
        flow_strs = [_build_flow_expr_str(kw, action) for kw in kwargs_list]
        self.run_ofctl('%s-flows' % action, ['-'], '\n'.join(flow_strs))

    def add_flow(self, **kwargs):
        self.do_action_flows('add', [kwargs])

    def delete_flows(self, **kwargs):
        self.do_action_flows('del', [kwargs])

    def db_get_val(self, table, record, column, check_error=False):
        output = self.run_vsctl(["get", table, record, column], check_error)
        if output:
            return output.rstrip("\n\r")

    def get_port_name_list(self):
        res = self.run_vsctl(["list-ports", self.br_name], check_error=True)
        if res:
            return res.strip().split("\n")
        return []

    def dump_flows_for(self, **kwargs):
        retval = None
        flow_str = ",".join(
            "=".join([key, str(val)]) for key, val in kwargs.items())

        flows = self.run_ofctl("dump-flows", [flow_str])
        if flows:
            retval = '\n'.join(item for item in flows.splitlines()
                               if 'NXST' not in item)
        return retval

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.destroy()


class SubProcessBase(object):
    def __init__(self, root_helper=None, namespace=None,
                 log_fail_as_error=True):
        self.root_helper = root_helper
        self.namespace = namespace
        self.log_fail_as_error = log_fail_as_error

    def _as_root(self, options, command, args, use_root_namespace=False):
        namespace = self.namespace if not use_root_namespace else None

        return self._execute(options, command, args, self.root_helper,
                             namespace=namespace,
                             log_fail_as_error=self.log_fail_as_error)

    @classmethod
    def _execute(cls, options, command, args, root_helper=None,
                 namespace=None, log_fail_as_error=True):
        opt_list = ['-%s' % o for o in options]
        if namespace:
            ip_cmd = ['ip', 'netns', 'exec', namespace, 'ip']
        else:
            ip_cmd = ['ip']
        return execute(ip_cmd + opt_list + [command] + list(args),
                       root_helper=root_helper,
                       log_fail_as_error=log_fail_as_error)

    def _run(self, options, command, args):
        if self.namespace:
            return self._as_root(options, command, args)
        else:
            return self._execute(options, command, args,
                                 log_fail_as_error=self.log_fail_as_error)

    def set_log_fail_as_error(self, fail_with_error):
        self.log_fail_as_error = fail_with_error


class IPDevice(SubProcessBase):
    def __init__(self, name, root_helper=None, namespace=None):
        super(IPDevice, self).__init__(root_helper=root_helper,
                                       namespace=namespace)
        self.name = name
        self.link = IpLinkCommand(self)

    def __eq__(self, other):
        return (other is not None and self.name == other.name
                and self.namespace == other.namespace)

    def __str__(self):
        return self.name


class IpCommandBase(object):
    COMMAND = ''

    def __init__(self, parent):
        self._parent = parent

    def _run(self, *args, **kwargs):
        return self._parent._run(kwargs.get('options', []), self.COMMAND, args)

    def _as_root(self, *args, **kwargs):
        return self._parent._as_root(kwargs.get('options', []),
                                     self.COMMAND,
                                     args,
                                     kwargs.get('use_root_namespace', False))


class IpDeviceCommandBase(IpCommandBase):
    @property
    def name(self):
        return self._parent.name


class IpLinkCommand(IpDeviceCommandBase):
    COMMAND = 'link'

    def set_up(self):
        self._as_root('set', self.name, 'up')

    @property
    def address(self):
        return self.attributes.get('link/ether')

    @property
    def attributes(self):
        return self._parse_line(self._run('show', self.name, options='o'))

    def _parse_line(self, value):
        if not value:
            return {}

        device_name, settings = value.replace("\\", '').split('>', 1)
        tokens = settings.split()
        keys = tokens[::2]
        values = [int(v) if v.isdigit() else v for v in tokens[1::2]]

        retval = dict(zip(keys, values))
        return retval


class IPWrapper(SubProcessBase):
    def __init__(self, root_helper=None, namespace=None):
        super(IPWrapper, self).__init__(root_helper=root_helper,
                                        namespace=namespace)

    def device(self, name):
        return IPDevice(name, self.root_helper, self.namespace)

    def add_veth(self, name1, name2, namespace2=None):
        args = ['add', name1, 'type', 'veth', 'peer', 'name', name2]

        if namespace2 is None:
            namespace2 = self.namespace

        self._as_root('', 'link', tuple(args))

        return (IPDevice(name1, self.root_helper, self.namespace),
                IPDevice(name2, self.root_helper, namespace2))


def device_exists(device_name, root_helper=None, namespace=None):
    """Return True if the device exists in the namespace."""
    try:
        dev = IPDevice(device_name, root_helper, namespace=namespace)
        dev.set_log_fail_as_error(False)
        address = dev.link.address
    except RuntimeError:
        return False
    return bool(address)


def _subprocess_setup():
    # Python installs a SIGPIPE handler by default. This is usually not what
    # non-Python subprocesses expect.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def subprocess_popen(args, stdin=None, stdout=None, stderr=None, shell=False,
                     env=None):
    return subprocess.Popen(args, shell=shell, stdin=stdin, stdout=stdout,
                            stderr=stderr, preexec_fn=_subprocess_setup,
                            close_fds=True, env=env)


def create_process(cmd, root_helper=None, addl_env=None, log_output=True):
    """Create a process object for the given command.

    The return value will be a tuple of the process object and the
    list of command arguments used to create it.
    """
    if root_helper:
        cmd = shlex.split(root_helper) + cmd
    cmd = map(str, cmd)

    log_output and LOG.info("Running command: %s", cmd)
    env = os.environ.copy()
    if addl_env:
        env.update(addl_env)

    obj = subprocess_popen(cmd, shell=False, stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env)
    return obj, cmd


def execute(cmd, root_helper=None, process_input=None, addl_env=None,
            check_exit_code=True, return_stderr=False, log_fail_as_error=True,
            log_output=True):
    try:
        obj, cmd = create_process(cmd, root_helper=root_helper,
                                  addl_env=addl_env, log_output=log_output)
        _stdout, _stderr = (process_input and
                            obj.communicate(process_input) or
                            obj.communicate())
        obj.stdin.close()
        m = _("\nCommand: %(cmd)s\nExit code: %(code)s\nStdout: %(stdout)r\n"
              "Stderr: %(stderr)r") % {'cmd': cmd, 'code': obj.returncode,
                                       'stdout': _stdout, 'stderr': _stderr}

        if obj.returncode and log_fail_as_error:
            LOG.error(m)
        else:
            log_output and LOG.info(m)

        if obj.returncode and check_exit_code:
            raise RuntimeError(m)
    finally:
        # NOTE(termie): this appears to be necessary to let the subprocess
        #               call clean something up in between calls, without
        #               it two execute calls in a row hangs the second one
        greenthread.sleep(0)

    return return_stderr and (_stdout, _stderr) or _stdout


def _build_flow_expr_str(flow_dict, cmd):
    flow_expr_arr = []
    actions = None

    if cmd == 'add':
        flow_expr_arr.append("hard_timeout=%s" %
                             flow_dict.pop('hard_timeout', '0'))
        flow_expr_arr.append("idle_timeout=%s" %
                             flow_dict.pop('idle_timeout', '0'))
        flow_expr_arr.append("priority=%s" %
                             flow_dict.pop('priority', '1'))
    elif 'priority' in flow_dict:
        msg = _("Cannot match priority on flow deletion or modification")
        raise InvalidInput(error_message=msg)

    if cmd != 'del':
        if "actions" not in flow_dict:
            msg = _("Must specify one or more actions on flow addition"
                    " or modification")
            raise InvalidInput(error_message=msg)
        actions = "actions=%s" % flow_dict.pop('actions')

    for key, value in flow_dict.iteritems():
        if key == 'proto':
            flow_expr_arr.append(value)
        else:
            flow_expr_arr.append("%s=%s" % (key, str(value)))

    if actions:
        flow_expr_arr.append(actions)

    return ','.join(flow_expr_arr)


def get_all_run_phy_intf():
    intf_list = []
    base_dir = '/sys/class/net'
    dir_exist = os.path.exists(base_dir)
    if not dir_exist:
        LOG.error("Unable to get interface list :Base dir %s does not exist",
                  base_dir)
        return intf_list
    dir_cont = os.listdir(base_dir)
    for subdir in dir_cont:
        dev_dir = base_dir + '/' + subdir + '/' + 'device'
        dev_exist = os.path.exists(dev_dir)
        if dev_exist:
            oper_state = is_intf_up(subdir)
            if oper_state is True:
                    intf_list.append(subdir)
        else:
            LOG.info("Dev dir %s does not exist, not physical intf", dev_dir)
    return intf_list


def get_intf_ipv4_addr(intf):
    """Retrieves the IPV4 address associated with an interface. """

    try:
        interface_addrs = netifaces.ifaddresses(intf)
        if netifaces.AF_INET in interface_addrs:
            return interface_addrs[netifaces.AF_INET][0]['addr']
    except Exception as e:
        LOG.error("Unable to get IP address for %(intf)s, exception %(exc)s",
                  {'intf': intf, 'exc': str(e)})


def is_phy_intf_ipv4_cfgd(intf):
    """Checks if the interface has IP address configured. """

    ipv4_addr = get_intf_ipv4_addr(intf)
    if ipv4_addr:
        return True
    return False


def is_intf_up(intf):

    """Function to check if a interface is up."""

    intf_path = '/sys/class/net' + '/' + intf
    intf_exist = os.path.exists(intf_path)
    if not intf_exist:
        LOG.error(
            "Unable to get interface %s : Interface dir %s does not exist",
            intif,
            intf_path)
        return False
    try:
        oper_file = intf_path + '/' + 'operstate'
        with open(oper_file, 'r') as fd:
            oper_state = fd.read().strip('\n')
            if oper_state == 'up':
                return True
    except Exception as e:
        LOG.error("Exception in reading %s", str(e))

    return False


def get_bond_intf(intf):
    bond_dir = '/proc/net/bonding/'
    dir_exist = os.path.exists(bond_dir)
    if not dir_exist:
        return
    base_dir = '/sys/class/net'
    for subdir in os.listdir(bond_dir):
        file_name = '/'.join((base_dir, subdir, 'bonding', 'slaves'))
        file_exist = os.path.exists(file_name)
        if file_exist:
            with open(file_name, 'r') as fd:
                slave_val = fd.read().strip('\n')
                if intf in slave_val:
                    return subdir


def is_intf_bond(intf):
    bond_dir = '/proc/net/bonding/'
    dir_exist = os.path.exists(bond_dir)
    if not dir_exist or not intf:
        return False
    bond_file = '/'.join((bond_dir, intf))
    return os.path.exists(bond_file)


def get_member_ports(intf):
    if not is_intf_bond(intf):
        return
    base_dir = '/sys/class/net'
    file_name = '/'.join((base_dir, intf, 'bonding', 'slaves'))
    file_exist = os.path.exists(file_name)
    if file_exist:
        with open(file_name, 'r') as fd:
            slave_val = fd.read().strip('\n')
            return slave_val


def get_dmi_info(id_type):

    """Get DMI info for given type"""

    if id_type is None:
        return None
    dmi_id_path = '/sys/class/dmi/id/' + id_type
    file_exists = os.path.exists(dmi_id_path)
    if file_exists:
        try:
            with open(dmi_id_path, 'r') as fd:
                dmi_val = fd.read().strip('\n')
                return dmi_val
        except Exception as e:
            LOG.error("Exception %s while reading file %s",
                      str(e), dmi_id_path)
            return None
    else:
        return None


def is_cisco_ucs_b_series():

    """Check if current system is Cisco UCS Blade server"""

    vendor = get_dmi_info('sys_vendor')
    product = get_dmi_info('product_name')

    if vendor is not None and product is not None \
            and vendor.lower().startswith('cisco') \
            and product.lower().startswith('ucsb'):
        return True
    else:
        return False
