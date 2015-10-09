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


import eventlet
eventlet.monkey_patch()
import json
import os
import platform
import time
import sys

from dfa.agent import iptables_driver as iptd
from dfa.agent.vdp import dfa_vdp_mgr as vdpm
from dfa.common import dfa_logger as logging
from dfa.common import config
from dfa.common import constants
from dfa.common import rpc
from dfa.common import utils


LOG = logging.getLogger(__name__)

thishost = platform.node()


class RpcCallBacks(object):

    """RPC call back methods."""

    def __init__(self, vdpd, iptd):
        self._vdpd = vdpd
        self._iptd = iptd

    def test(self, context, args):
        LOG.debug("RX: context=%s, args=%s" % (context, args))
        resp = 'Reply from %s.' % thishost
        return resp

    def send_vm_info(self, context, msg):
        vm_info = eval(msg)
        LOG.debug('Received %(vm_info)s for %(instance)s' % (
            {'vm_info': vm_info, 'instance': vm_info.get('inst_name')}))
        # Call VDP/LLDPad API to send the info
        self._vdpd.vdp_vm_event(vm_info)

        # Enqueue the vm info for updating iptables.
        oui = vm_info.get('oui')
        if oui and oui.get('ip_addr') != '0.0.0.0' and self._iptd:
            rule_info = dict(mac=vm_info.get('vm_mac'), ip=oui.get('ip_addr'),
                             port=vm_info.get('port_uuid'),
                             status=vm_info.get('status'))
            self._iptd.enqueue_event(rule_info)

    def update_ip_rule(self, context, msg):
        rule_info = eval(msg)
        LOG.debug('RX Info : %s' % rule_info)
        # Update the iptables for this rule
        if self._iptd:
            self._iptd.enqueue_event(rule_info)

    def send_msg_to_agent(self, context, msg):
        msg_type = context.get('type')
        uplink = json.loads(msg)
        LOG.debug("Received %(context)s and %(msg)s" % (
            {'context': context, 'msg': uplink}))
        if msg_type == constants.UPLINK_NAME:
            LOG.debug("uplink is %(uplink)s" % uplink)
            self._vdpd.dfa_uplink_restart(uplink)


class DfaAgent(object):

    """DFA agent."""

    def __init__(self, host, rpc_qn):
        self._my_host = host
        self._qn = rpc_qn
        self._cfg = config.CiscoDFAConfig('neutron').cfg
        LOG.debug('Starting DFA Agent on %s' % self._my_host)

        # List of task in the agent
        self.agent_task_list = []

        # This flag indicates the agent started for the first time.
        self._need_uplink_info = True

        # Initialize iptables driver. This will be used to update the ip
        # rules in iptables, after launching an instance.

        if (self._cfg.dcnm.dcnm_dhcp.lower() == 'true'):
            self._iptd = iptd.IptablesDriver(self._cfg)
        else:
            self._iptd = None
            LOG.info("Using native dhcp, iptable driver is not needed")

        # Setup RPC client for sending heartbeat to controller
        self.setup_client_rpc()

        # Initialize VPD manager.
        # TODO read it from config.
        br_int = 'br-int'
        br_ext = 'br-ethd'
        root_helper = 'sudo'
        self._vdpm = vdpm.VdpMgr(br_int, br_ext, root_helper, self.clnt,
                                 thishost)
        self.pool = eventlet.GreenPool()
        self.setup_rpc()

    def setup_client_rpc(self):
        """Setup RPC client for dfa agent."""
        # Setup RPC client.
        url = self._cfg.dfa_rpc.transport_url % (
            {'ip': self._cfg.DEFAULT.rabbit_hosts})
        self.clnt = rpc.DfaRpcClient(url, constants.DFA_SERVER_QUEUE)

    def send_heartbeat(self):
        context = {}
        args = json.dumps(dict(when=time.ctime(), agent=thishost))
        msg = self.clnt.make_msg('heartbeat', context, msg=args)
        resp = self.clnt.cast(msg)
        LOG.debug("send_heartbeat: resp = %s" % resp)

    def request_uplink_info(self):
        context = {}
        msg = self.clnt.make_msg('request_uplink_info', context, agent=thishost)
        try:
            resp = self.clnt.call(msg)
            LOG.debug("request_uplink_info: resp = %s" % resp)
            self._need_uplink_info = resp
        except rpc.MessagingTimeout:
            LOG.error("RPC timeout: Request for uplink info failed.")

    def setup_rpc(self):
        """Setup RPC server for dfa agent."""

        endpoints = RpcCallBacks(self._vdpm, self._iptd)
        self.server = rpc.DfaRpcServer(self._qn, self._my_host, endpoints)

    def start_rpc(self):
        self.server.start()
        LOG.debug('starting RPC server on the agent.')
        self.server.wait()

    def stop_rpc(self):
        self.server.stop()

    def start_rpc_task(self):
        thrd = utils.EventProcessingThread('Agent_RPC_Server',
                                           self, 'start_rpc')
        thrd.start()
        return thrd

    def start_iptables_task(self):
        thrd = self._iptd.create_thread()
        thrd.start()
        return thrd

    def start_tasks(self):
        rpc_thrd = self.start_rpc_task()
        self.agent_task_list.append(rpc_thrd)
        if (self._iptd):
            ipt_thrd = self.start_iptables_task()
            self.agent_task_list.append(ipt_thrd)


def save_my_pid(cfg):

    mypid = os.getpid()

    pid_path = cfg.dfa_log.pid_dir
    pid_file = cfg.dfa_log.pid_agent_file
    if pid_path and pid_file:
        try:
            if not os.path.exists(pid_path):
                os.makedirs(pid_path)
        except OSError:
            pass
        else:
            pid_file_path = os.path.join(pid_path, pid_file)

        LOG.debug('dfa_agent pid=%s' % mypid)
        with open(pid_file_path, 'w') as fn:
            fn.write(str(mypid))


def main():

    # Setup logger
    cfg = config.CiscoDFAConfig().cfg
    logging.setup_logger('dfa_enabler', cfg)

    # Get pid of the process and save it.
    save_my_pid(cfg)

    # Create DFA agent object
    dfa_agent = DfaAgent(thishost, constants.DFA_AGENT_QUEUE)

    LOG.debug('Starting tasks in agent...')
    try:
        # Start all task in the agent.
        dfa_agent.start_tasks()

        # Endless loop
        while True:
            start = time.time()

            # Send heartbeat to controller, data includes:
            # - timestamp
            # - host name
            dfa_agent.send_heartbeat()

            # If the agent comes up for the fist time (could be after crash),
            # ask for the uplink info.
            if dfa_agent._need_uplink_info:
                dfa_agent.request_uplink_info()

            for trd in dfa_agent.agent_task_list:
                if not trd.am_i_active:
                    LOG.info("Thread %s is not active." % (trd.name))
                    # TODO should something be done?

            end = time.time()
            delta = end - start
            eventlet.sleep(constants.HB_INTERVAL - delta)
    except Exception as e:
        dfa_agent.stop_rpc()
        LOG.exception('Exception %s is received' % str(e))
        LOG.error('Exception %s is received' % str(e))
        sys.exit("ERROR: %s" % str(e))

if __name__ == '__main__':
    sys.exit(main())
