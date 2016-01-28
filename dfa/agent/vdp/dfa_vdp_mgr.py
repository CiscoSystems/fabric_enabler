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

import json
import Queue
import time

from dfa.common import config
from dfa.common import utils
from dfa.agent.vdp import ovs_vdp
from dfa.agent.topo_disc import topo_disc
from dfa.common import constants
from dfa.common import dfa_logger as logging
from dfa.common import rpc
from dfa.agent import detect_uplink as uplink_det
from dfa.agent.fi_evb_emul import evb_emulator

LOG = logging.getLogger(__name__)


class VdpMsgPriQue(object):

    '''VDP MEssage Queue'''

    def __init__(self):
        self._queue = Queue.PriorityQueue()

    def enqueue(self, priority, msg):
        msg_tupl = (priority, msg)
        self._queue.put(msg_tupl)

    def dequeue(self):
        msg_tupl = self._queue.get()
        return msg_tupl[0], msg_tupl[1]

    def dequeue_nonblock(self):
        msg_tupl = self._queue.get_nowait()
        return msg_tupl[0], msg_tupl[1]

    def is_not_empty(self):
        return not self._queue.empty()


class VdpQueMsg(object):

    '''Construct VDP Message'''

    def __init__(self, msg_type, port_uuid=None, vm_mac=None, oui=None,
                 net_uuid=None, segmentation_id=None, status=None,
                 vm_bulk_list=None, phy_uplink=None, br_int=None, br_ex=None,
                 root_helper=None):
        self.msg_dict = {}
        self.msg_type = msg_type
        if msg_type == constants.VM_MSG_TYPE:
            self.construct_vm_msg(port_uuid, vm_mac, net_uuid,
                                  segmentation_id, status, oui, phy_uplink)
        elif msg_type == constants.UPLINK_MSG_TYPE:
            self.construct_uplink_msg(status, phy_uplink, br_int, br_ex,
                                      root_helper)
        elif msg_type == constants.VM_BULK_SYNC_MSG_TYPE:
            self.construct_vm_bulk_sync_msg(vm_bulk_list, phy_uplink)

    def construct_vm_msg(self, port_uuid, vm_mac, net_uuid,
                         segmentation_id, status, oui, phy_uplink):
        self.msg_dict['port_uuid'] = port_uuid
        self.msg_dict['vm_mac'] = vm_mac
        self.msg_dict['net_uuid'] = net_uuid
        self.msg_dict['segmentation_id'] = segmentation_id
        self.msg_dict['status'] = status
        self.msg_dict['oui'] = oui
        self.msg_dict['phy_uplink'] = phy_uplink

    def construct_vm_bulk_sync_msg(self, vm_bulk_list, phy_uplink):
        self.msg_dict['phy_uplink'] = phy_uplink
        self.msg_dict['vm_bulk_list'] = vm_bulk_list

    def construct_uplink_msg(self, status, phy_uplink, br_int, br_ex,
                             root_helper):
        self.msg_dict['status'] = status
        self.msg_dict['phy_uplink'] = phy_uplink
        self.msg_dict['br_int'] = br_int
        self.msg_dict['br_ex'] = br_ex
        self.msg_dict['root_helper'] = root_helper

    def get_oui(self):
        return self.msg_dict['oui']

    def get_uplink(self):
        return self.msg_dict['phy_uplink']

    def get_mac(self):
        return self.msg_dict['vm_mac']

    def get_status(self):
        return self.msg_dict['status']

    def get_segmentation_id(self):
        return self.msg_dict['segmentation_id']

    def get_net_uuid(self):
        return self.msg_dict['net_uuid']

    def get_port_uuid(self):
        return self.msg_dict['port_uuid']

    def get_integ_br(self):
        return self.msg_dict['br_int']

    def get_ext_br(self):
        return self.msg_dict['br_ex']

    def get_root_helper(self):
        return self.msg_dict['root_helper']

    def set_uplink(self, uplink):
        self.msg_dict['phy_uplink'] = uplink


class VdpMgr(object):

    '''Responsible for Handling VM/Uplink requests'''

    def __init__(self, br_integ, br_ex, root_helper, rpc_client, host):
        self.br_integ = br_integ
        self.br_ex = br_ex
        self.root_helper = root_helper
        # Check for error?? fixme(padkrish)
        self.que = VdpMsgPriQue()
        self.err_que = VdpMsgPriQue()
        self.phy_uplink = None
        self.veth_intf = None
        self.veth_ovs_intf = None
        self.restart_uplink_called = False
        self.ovs_vdp_obj_dict = {}
        self.rpc_clnt = rpc_client
        self.host = host
        self.uplink_det_compl = False
        self.process_uplink_ongoing = False
        self.uplink_down_cnt = 0
        self.is_os_run = False
        self._cfg = config.CiscoDFAConfig().cfg
        self.static_uplink = False
        self.static_uplink_port = None
        self.static_uplink_first = True
        self.is_ucs_fi = False
        self.ucs_fi_evb_dmac = None
        self.evb_emulator = None
        self.read_static_uplink()
        self.start()
        self.topo_disc = topo_disc.TopoDisc(self.topo_disc_cb, root_helper)

    def read_static_uplink(self):
        ''' Read the static uplink from file, if given '''
        cnt = 0
        if self._cfg.general.node is None:
            return
        for node in self._cfg.general.node.split(','):
            if node.strip() == self.host:
                self.static_uplink = True
                self.static_uplink_port = self._cfg.general.node_uplink.\
                    split(',')[cnt].strip()
                if self._cfg.general.ucs_fi is not None and\
                        self._cfg.general.ucs_fi is True:
                    self.is_ucs_fi = True
                    self.ucs_fi_evb_dmac = \
                        self._cfg.general.ucs_fi_evb_dmac
                return
            cnt = cnt + 1

    def topo_disc_cb(self, intf, topo_disc_obj):
        return self.save_topo_disc_params(intf, topo_disc_obj)

    def update_vm_result(self, port_uuid, result, lvid=None,
                         vdp_vlan=None):
        context = {'agent': self.host}
        if lvid is None or vdp_vlan is None:
            args = json.dumps(dict(port_uuid=port_uuid, result=result))
        else:
            args = json.dumps(dict(port_uuid=port_uuid, local_vlan=lvid,
                                   vdp_vlan=vdp_vlan, result=result))
        msg = self.rpc_clnt.make_msg('update_vm_result', context, msg=args)
        try:
            resp = self.rpc_clnt.call(msg)
            return resp
        except rpc.MessagingTimeout:
            LOG.error("RPC timeout: Failed to update VM result on the server")

    def vdp_vlan_change_cb(self, port_uuid, lvid, vdp_vlan):
        '''
            Callback function for updating the VDP VLAN in DB,
            called by VDP
        '''
        LOG.info("Vlan change CB lvid %(lvid)s VDP %(vdp)s",
                 {'lvid': lvid, 'vdp': vdp_vlan})
        self.update_vm_result(port_uuid, constants.RESULT_SUCCESS,
                              lvid=lvid, vdp_vlan=vdp_vlan)

    def process_vm_event(self, msg, phy_uplink):
        LOG.info("In processing VM Event status %s for MAC %s UUID %s oui %s"
                 % (msg.get_status(), msg.get_mac(),
                    msg.get_port_uuid(), msg.get_oui()))
        time.sleep(10)
        if msg.get_status() == 'up':
            res_fail = constants.CREATE_FAIL
        else:
            res_fail = constants.DELETE_FAIL
        if (not self.uplink_det_compl or
                phy_uplink not in self.ovs_vdp_obj_dict):
            LOG.error("Uplink Port Event not received yet")
            self.update_vm_result(msg.get_port_uuid(), res_fail)
            return
        ovs_vdp_obj = self.ovs_vdp_obj_dict[phy_uplink]
        ret = ovs_vdp_obj.send_vdp_port_event(msg.get_port_uuid(),
                                              msg.get_mac(),
                                              msg.get_net_uuid(),
                                              msg.get_segmentation_id(),
                                              msg.get_status(),
                                              msg.get_oui())
        if not ret:
            LOG.error("Error in VDP port event, Err Queue enq")
            self.update_vm_result(msg.get_port_uuid(), res_fail)
        else:
            LOG.error("Success in VDP port event")
            lvid, vdp_vlan = ovs_vdp_obj.get_lvid_vdp_vlan(msg.get_net_uuid(),
                                                           msg.get_port_uuid())
            self.update_vm_result(msg.get_port_uuid(),
                                  constants.RESULT_SUCCESS,
                                  lvid=lvid, vdp_vlan=vdp_vlan)

    def process_bulk_vm_event(self, msg, phy_uplink):
        LOG.info("In processing Bulk VM Event status %s", msg)
        time.sleep(3)
        if (not self.uplink_det_compl or
                phy_uplink not in self.ovs_vdp_obj_dict):
            LOG.error("Uplink Port Event not received yet in bulk process")
            # This condition shouldn't be hit as only when uplink is obtained
            # save_uplink is called and that in turns calls this process_bulk.
            # But, wrong failure constant is passed below. Unless the message
            # is processed, it's not known if it's CREATE or DELETE fail.
            self.update_vm_result(msg.get_port_uuid(), constants.CREATE_FAIL)
            return
        ovs_vdp_obj = self.ovs_vdp_obj_dict[phy_uplink]
        for vm_dict in msg.msg_dict.get('vm_bulk_list'):
            if vm_dict['status'] == 'down':
                ovs_vdp_obj.pop_local_cache(vm_dict['port_uuid'],
                                            vm_dict['vm_mac'],
                                            vm_dict['net_uuid'],
                                            vm_dict['local_vlan'],
                                            vm_dict['vdp_vlan'],
                                            vm_dict['segmentation_id'])
            vm_msg = VdpQueMsg(constants.VM_MSG_TYPE,
                               port_uuid=vm_dict['port_uuid'],
                               vm_mac=vm_dict['vm_mac'],
                               net_uuid=vm_dict['net_uuid'],
                               segmentation_id=vm_dict['segmentation_id'],
                               status=vm_dict['status'],
                               oui=vm_dict['oui'],
                               phy_uplink=phy_uplink)
            self.process_vm_event(vm_msg, phy_uplink)

    def process_uplink_event(self, msg, phy_uplink):
        LOG.info("Received New uplink Msg %s for uplink %s" %
                 (msg.get_status(), phy_uplink))
        if msg.get_status() == 'up':
            ovs_exc_raised = False
            try:
                self.ovs_vdp_obj_dict[phy_uplink] = ovs_vdp.OVSNeutronVdp(
                    phy_uplink, msg.get_integ_br(), msg.get_ext_br(),
                    msg.get_root_helper(), self.vdp_vlan_change_cb,
                    fi_evb_dmac=self.ucs_fi_evb_dmac)
            except Exception as exc:
                LOG.error("OVS VDP Object creation failed %s" % str(exc))
                ovs_exc_raised = True
            if (ovs_exc_raised or not self.ovs_vdp_obj_dict[phy_uplink].
                    is_lldpad_setup_done()):
                # Is there a way to delete the object??
                LOG.error("UP Event Processing NOT Complete")
                self.err_que.enqueue(constants.Q_UPL_PRIO, msg)
            else:
                self.uplink_det_compl = True
                veth_intf = (self.ovs_vdp_obj_dict[self.phy_uplink].
                             get_lldp_local_bridge_port())
                LOG.info("UP Event Processing Complete Saving uplink %s and "
                         "veth %s" % (self.phy_uplink, veth_intf))
                self.save_uplink(uplink=self.phy_uplink, veth_intf=veth_intf)
                self.topo_disc.uncfg_intf(self.phy_uplink)
                self.topo_disc.cfg_intf(veth_intf)
        elif msg.get_status() == 'down':
            # Free the object fixme(padkrish)
            if phy_uplink in self.ovs_vdp_obj_dict:
                self.ovs_vdp_obj_dict[phy_uplink].clear_obj_params()
            else:
                ovs_vdp.delete_uplink_and_flows(self.root_helper, self.br_ex,
                                                phy_uplink, self.is_ucs_fi)
            self.save_uplink()
            self.topo_disc.uncfg_intf(self.veth_intf)
            self.topo_disc.cfg_intf(phy_uplink)

    def process_queue(self):
        LOG.info("Entered process_q")
        while True:
            prio, msg = self.que.dequeue()
            msg_type = msg.msg_type
            phy_uplink = msg.get_uplink()
            LOG.info("Msg dequeued type is %d" % msg_type)
            try:
                if msg_type == constants.VM_MSG_TYPE:
                    self.process_vm_event(msg, phy_uplink)
                elif msg_type == constants.VM_BULK_SYNC_MSG_TYPE:
                    self.process_bulk_vm_event(msg, phy_uplink)
                elif msg_type == constants.UPLINK_MSG_TYPE:
                    try:
                        self.process_uplink_event(msg, phy_uplink)
                    except Exception as eu:
                        LOG.exception("Exception caught in process_uplink %s "
                                      % str(eu))
                    self.process_uplink_ongoing = False
            except Exception as e:
                LOG.exception("Exception caught in process_q %s " % str(e))

    def process_err_queue(self):
        LOG.info("Entered Err process_q")
        try:
            while self.err_que.is_not_empty():
                prio, msg = self.err_que.dequeue_nonblock()
                msg_type = msg.msg_type
                LOG.info("Msg dequeued from err queue type is %d" % msg_type)
                if msg_type == constants.UPLINK_MSG_TYPE:
                    self.que.enqueue(constants.Q_UPL_PRIO, msg)
        except Exception as e:
            LOG.exception("Exception caught in proc_err_que %s " % str(e))

    def start(self):
        # Spawn the thread
        # Pass the Que as last argument so that in case of exception, the
        # daemon can exit gracefully. fixme(padkrish)
        thr_q = utils.EventProcessingThread("VDP_Mgr", self, 'process_queue')
        thr_q.start()
        task_err_proc = utils.PeriodicTask(constants.ERR_PROC_INTERVAL,
                                           self.process_err_queue)
        task_err_proc.run()
        task_uplink = utils.PeriodicTask(constants.UPLINK_DET_INTERVAL,
                                         self.vdp_uplink_proc_top)
        task_uplink.run()

    def is_openstack_running(self):
        '''
        Currently it just checks for the presence of both the bridges
        '''
        try:
            if (ovs_vdp.is_bridge_present(self.br_ex, self.root_helper) and
               ovs_vdp.is_bridge_present(self.br_integ, self.root_helper)):
                return True
            else:
                return False
        except Exception as e:
            LOG.error("Exception in is_openstack_running %s", str(e))
            return False

    def vdp_uplink_proc_top(self):
        try:
            self.vdp_uplink_proc()
        except Exception as e:
            LOG.error("VDP uplink proc exception %s" % e)

    def save_uplink(self, uplink="", veth_intf=""):
        context = {}
        args = json.dumps(dict(agent=self.host, uplink=uplink,
                               veth_intf=veth_intf))
        msg = self.rpc_clnt.make_msg('save_uplink', context, msg=args)
        try:
            resp = self.rpc_clnt.call(msg)
            return resp
        except rpc.MessagingTimeout:
            LOG.error("RPC timeout: Failed to save link name on the server")

    def save_topo_disc_params(self, intf, topo_disc_obj):
        context = {}
        args = json.dumps(
            dict(
                agent=self.host,
                intf=intf,
                remote_evb_cfgd=topo_disc_obj.remote_evb_cfgd,
                remote_evb_mode=topo_disc_obj.remote_evb_mode,
                remote_mgmt_addr=topo_disc_obj.remote_mgmt_addr,
                remote_system_desc=topo_disc_obj.remote_system_desc,
                remote_system_name=topo_disc_obj.remote_system_name,
                remote_port=topo_disc_obj.remote_port,
                remote_chassis_id_mac=topo_disc_obj.remote_chassis_id_mac,
                remote_port_id_mac=topo_disc_obj.remote_port_id_mac))
        msg = self.rpc_clnt.make_msg('save_topo_disc_params', context,
                                     msg=args)
        try:
            resp = self.rpc_clnt.call(msg)
            return resp
        except rpc.MessagingTimeout:
            LOG.error("RPC timeout: Failed to send topo disc on the server")

    def static_uplink_detect(self, veth):
        ''' Return the static uplink based on argument passed '''
        LOG.info("In static_uplink_detect")
        if self.static_uplink_first:
            self.static_uplink_first = False
            if self.phy_uplink is not None and (
               self.phy_uplink != self.static_uplink_port):
                return 'down'
            if self.is_ucs_fi is True:
                self.evb_emulator = evb_emulator.EvbEmulator(
                    self.root_helper,
                    self.static_uplink_port,
                    self.veth_intf,
                    self.veth_ovs_intf)

        if self.evb_emulator is not None:
            self.evb_emulator.emulate_on_interface(
                self.static_uplink_port,
                self.veth_intf,
                self.veth_ovs_intf)

        if veth is None:
            return self.static_uplink_port
        else:
            return 'normal'

    def vdp_uplink_proc(self):
        '''
        -> restart_uplink_called: should be called by agent initially to set
           the stored uplink and veth from DB
        -> process_uplink_ongoing: Will be set when uplink message is enqueue
           and reset when dequeued and processed completely
        -> uplink_det_compl: Will be set to True when a valid uplink is
           detected and object created. Will be reset when uplink is down
        -> phy_uplink: Is the uplink interface
        -> veth_intf : Signifies the veth interface
        '''
        LOG.info("In Periodic Uplink Task")
        if not self.is_os_run:
            if not self.is_openstack_running():
                LOG.info("Openstack is not running")
                return
            else:
                self.is_os_run = True
        if not self.restart_uplink_called or self.process_uplink_ongoing:
            LOG.info("Uplink before restart not refreshed yet..states %d %d" %
                     (self.restart_uplink_called, self.process_uplink_ongoing))
            return
        if self.phy_uplink is not None:
            if (self.uplink_det_compl and (
               self.phy_uplink not in self.ovs_vdp_obj_dict)):
                LOG.error("Not Initialized for phy %s" % self.phy_uplink)
                return
            if self.phy_uplink in self.ovs_vdp_obj_dict:
                self.veth_intf = (self.ovs_vdp_obj_dict[self.phy_uplink].
                                  get_lldp_local_bridge_port())
                self.veth_ovs_intf = (self.ovs_vdp_obj_dict[self.phy_uplink].
                                      get_lldp_ovs_bridge_port())
            # The below logic has a bug when agent is started
            # and openstack is not running fixme(padkrish)
            else:
                if self.veth_intf is None:
                    LOG.error("Incorrect state, Bug")
                    return
        if self.static_uplink:
            ret = self.static_uplink_detect(self.veth_intf)
        else:
            ret = uplink_det.detect_uplink(self.veth_intf,
                                           self.root_helper)
        if ret is 'down':
            if self.phy_uplink is None:
                LOG.error("Wrong status down")
                return
            # Call API to set the uplink as "" DOWN event
            self.uplink_down_cnt = self.uplink_down_cnt + 1
            if not self.static_uplink and (
               self.uplink_down_cnt < constants.UPLINK_DOWN_THRES):
                return
            self.process_uplink_ongoing = True
            upl_msg = VdpQueMsg(constants.UPLINK_MSG_TYPE,
                                status='down',
                                phy_uplink=self.phy_uplink,
                                br_int=self.br_integ, br_ex=self.br_ex,
                                root_helper=self.root_helper)
            self.que.enqueue(constants.Q_UPL_PRIO, upl_msg)
            self.phy_uplink = None
            self.veth_intf = None
            self.uplink_det_compl = False
            self.uplink_down_cnt = 0
        elif ret is None:
            if self.veth_intf is not None:
                LOG.error("Wrong status None")
                return
            # Call API to set the uplink as "" Uplink not discovered yet
            self.save_uplink()
        elif ret is 'normal':
            if self.veth_intf is None:
                LOG.error("Wrong status Normal")
                return
            # Uplink already discovered, nothing to be done here
            # Resetting it back, happens when uplink was down for a very short
            # time and no need to remove flows
            self.uplink_down_cnt = 0
            # Revisit this logic.
            # If uplink detection fails, it will be put in Error queue, which
            # will dequeue and put it back in the main queue
            # At the same time this periodic task will also hit this normal
            # state and will put the message in main queue. fixme(padkrish)
            # The below lines are put here because after restart when
            # eth/veth are passed to uplink script, it will return normal
            # But OVS object would not have been created for the first time,
            # so the below lines ensures it's done.
            if not self.uplink_det_compl:
                if self.phy_uplink is None:
                    LOG.error("Incorrect state, bug")
                    return
                self.process_uplink_ongoing = True
                upl_msg = VdpQueMsg(constants.UPLINK_MSG_TYPE,
                                    status='up',
                                    phy_uplink=self.phy_uplink,
                                    br_int=self.br_integ, br_ex=self.br_ex,
                                    root_helper=self.root_helper)
                self.que.enqueue(constants.Q_UPL_PRIO, upl_msg)
                # yield
                LOG.info("Enqueued Uplink Msg from normal")
        else:
            LOG.info("In Periodic Uplink Task uplink found %s" % ret)
            # Call API to set the uplink as ret
            self.save_uplink(uplink=ret, veth_intf=self.veth_intf)
            self.phy_uplink = ret
            self.process_uplink_ongoing = True
            upl_msg = VdpQueMsg(constants.UPLINK_MSG_TYPE,
                                status='up',
                                phy_uplink=self.phy_uplink,
                                br_int=self.br_integ, br_ex=self.br_ex,
                                root_helper=self.root_helper)
            self.que.enqueue(constants.Q_UPL_PRIO, upl_msg)
            # yield
            LOG.info("Enqueued Uplink Msg")

    def vdp_vm_event(self, vm_dict_list):
        if isinstance(vm_dict_list, list):
            vm_msg = VdpQueMsg(constants.VM_BULK_SYNC_MSG_TYPE,
                               vm_bulk_list=vm_dict_list,
                               phy_uplink=self.phy_uplink)
        else:
            vm_dict = vm_dict_list
            LOG.info("Obtained VM event Enqueueing Status %s MAC %s uuid %s "
                     "oui %s", vm_dict['status'], vm_dict['vm_mac'],
                     vm_dict['net_uuid'], vm_dict['oui'])
            vm_msg = VdpQueMsg(constants.VM_MSG_TYPE,
                               port_uuid=vm_dict['port_uuid'],
                               vm_mac=vm_dict['vm_mac'],
                               net_uuid=vm_dict['net_uuid'],
                               segmentation_id=vm_dict['segmentation_id'],
                               status=vm_dict['status'],
                               oui=vm_dict['oui'],
                               phy_uplink=self.phy_uplink)
        self.que.enqueue(constants.Q_VM_PRIO, vm_msg)

    def dfa_uplink_restart(self, uplink_dict):
        LOG.info("Obtained uplink after restart %s " % uplink_dict)
        # This shouldn't happen
        if self.phy_uplink is not None:
            LOG.error("Uplink detection already done %s" % self.phy_uplink)
            return
        uplink = uplink_dict.get('uplink')
        veth_intf = uplink_dict.get('veth_intf')
        # Logic is as follows:
        # If DB didn't have any uplink it means it's not yet detected or down
        # if DB has uplink and veth, then no need to scan all ports we can
        # start with this veth.
        # If uplink has been removed or modified during restart, then a
        # down will be returned by uplink detection code and it will be
        # removed then.
        # If DB has uplink, but no veth, it's an error condition and in
        # which case remove the uplink port from bridge and start fresh
        if uplink is None or len(uplink) == 0:
            LOG.info("uplink not discovered yet")
            self.restart_uplink_called = True
            return
        if veth_intf is not None and len(veth_intf) != 0:
            LOG.info("veth interface is already added, %s %s" % (uplink,
                                                                 veth_intf))
            self.phy_uplink = uplink
            self.veth_intf = veth_intf
            self.restart_uplink_called = True
            return
        LOG.info("Error case removing the uplink %s from bridge" % uplink)
        ovs_vdp.delete_uplink_and_flows(self.root_helper, self.br_ex, uplink,
                                        self.is_ucs_fi)
        self.restart_uplink_called = True
