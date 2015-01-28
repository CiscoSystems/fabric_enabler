import os
import shlex
from eventlet import greenthread
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

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.destroy()

def _subprocess_setup():
    # Python installs a SIGPIPE handler by default. This is usually not what
    # non-Python subprocesses expect.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

def subprocess_popen(args, stdin=None, stdout=None, stderr=None, shell=False,
                     env=None):
    return subprocess.Popen(args, shell=shell, stdin=stdin, stdout=stdout,
                            stderr=stderr, preexec_fn=_subprocess_setup,
                            close_fds=True, env=env)

def create_process(cmd, root_helper=None, addl_env=None):
    """Create a process object for the given command.

    The return value will be a tuple of the process object and the
    list of command arguments used to create it.
    """
    if root_helper:
        cmd = shlex.split(root_helper) + cmd
    cmd = map(str, cmd)

    LOG.info("Running command: %s", cmd)
    env = os.environ.copy()
    if addl_env:
        env.update(addl_env)

    obj = subprocess_popen(cmd, shell=False, stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env)
    return obj, cmd

def execute(cmd, root_helper=None, process_input=None, addl_env=None,
            check_exit_code=True, return_stderr=False, log_fail_as_error=True):
    try:
        obj, cmd = create_process(cmd, root_helper=root_helper,
                                  addl_env=addl_env)
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
            LOG.info(m)

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
