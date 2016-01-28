# Copyright 2014 Cisco Systems.
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
# @author: Viral Barot, Cisco Systems, Inc.

"""
Emulates evb capabilities for physical interface
"""
from dfa.agent.topo_disc import pub_lldp_api
from dfa.agent.vdp import vdp_constants
from dfa.common import dfa_logger as logging
from dfa.common import dfa_sys_lib
from threading import Thread
from threading import Lock
from threading import Event
import binascii
import socket
import struct
import time

LOG = logging.getLogger(__name__)

# Standard TLV types
TLV_TYPE = {"END_OF_LLDPDU":       b'\x00',
            "CHASSIS_ID":          b'\x01',
            "PORT_ID":             b'\x02',
            "TTL":                 b'\x03',
            "PORT_DESCRIPTION":    b'\x04',
            "SYSTEM_NAME":         b'\x05',
            "SYSTEM_DESCRIPTION":  b'\x06',
            "SYSTEM_CAPABILITIES": b'\x07',
            "MANAGEMENT_ADDRESS":  b'\x08',
            "CUSTOM":              b'\x7f'}


# Chaisis ID Subtypes
CHAISIS_ID_SUBTYPE = {"RESERVED":           b'\x00',
                      "CHASSIS_COMPOONENT": b'\x01',
                      "INTERFACE_ALIAS":    b'\x02',
                      "PORT_COMPONENT":     b'\x03',
                      "MAC_ADDRESS":        b'\x04',
                      "NETWORK_ADDRESS":    b'\x05',
                      "INTERFACE_NAME":     b'\x06',
                      "LOCALLY_ASSIGNED":   b'\x07'}

# Port ID Subtypes
PORT_ID_SUBTYPE = {"RESERVED":         b'\x00',
                   "INTERFACE_ALIAS":  b'\x01',
                   "PORT_COMPONENT":   b'\x02',
                   "MAC_ADDRESS":      b'\x03',
                   "NETWORK_ADDRESS":  b'\x04',
                   "INTERFACE_NAME":   b'\x05',
                   "AGENT_CIRCUIT_ID": b'\x06',
                   'LOCALLY_ASSIGNED': b'\x07'}

# Capabilities Subtypes
CAPABILITES_SUBTYPE = {"OTHER":               b'\x01',
                       "REPEATER":            b'\x02',
                       "MAC_BRIDGE":          b'\x04',
                       "WLAN_ACCESS_POINT":   b'\x08',
                       "ROUTER":              b'\x10',
                       "TELEPHONE":           b'\x20',
                       "DOCSIS_CABLE_DEVICE": b'\x40',
                       "STATION_ONLY":        b'\x80',
                       "C_VLAN":              b'\x01\x00',
                       "S_VLAN":              b'\x02\x00',
                       "TWO_PORT_MAC_RELAY":  b'\x04\x00'}

# Org Codes for custom TLVs
ORG_CODES = {"CISCO_SYSTEMS": b'\x00\x01\x42',
             "IEEE_802_1":    b'\x00\x80\xc2',
             "IEEE_802_3":    b'\x00\x12\x0f'}


class EvbEmulator(Thread):

    """The EVB emulator class."""

    def __init__(self, root_helper, uplink_interface,
                 virtual_local_interface,
                 virtual_ovs_interface,
                 frequency=30):

        Thread.__init__(self)

        # Static uplink interface
        self._uplink_interface = uplink_interface

        # Local veth
        self._virtual_local_interface = virtual_local_interface

        # OVS veth
        self._virtual_ovs_interface = virtual_ovs_interface

        # Lock to atomically upate the interface configs
        self._lock = Lock()

        # Time in seconds after which the next lldp packet will be sent
        self._frequency = frequency

        # Cached tlvs
        self._cached_emulated_tlv = None
        self._cached_real_tlv = None

        # Event to stop the thread
        self._stop = Event()

        # lldp helpper object
        self._lldp_helper = pub_lldp_api.LldpApi(root_helper)

    def run(self):
        while not self._stop.isSet():

            # delayed start
            time.sleep(self._frequency)

            self._lock.acquire()
            uplink_interface = self._uplink_interface
            virtual_local_interface = self._virtual_local_interface
            virtual_ovs_interface = self._virtual_ovs_interface
            self._lock.release()

            if dfa_sys_lib.is_intf_up(uplink_interface):

                LOG.info(
                    "Emulating EVB on %s , %s , %s",
                    uplink_interface,
                    virtual_local_interface,
                    virtual_ovs_interface)

                self._lldp_helper.enable_lldp(
                    virtual_local_interface,
                    is_ncb=False,
                    is_nb=True)
                real_tlv = self._lldp_helper.get_lldp_tlv(
                    virtual_local_interface, is_ncb=False, is_nb=True)
                if real_tlv is None:
                    LOG.error(
                        "Could not get tlv on %s, skipping evb emulation",
                        uplink_interface)
                    continue

                if not self._lldp_helper.get_remote_evb_cfgd(real_tlv):
                    if real_tlv != self._cached_real_tlv:
                        evb_enabled_tlv = \
                            self._generate_evb_capalble_lldp_pkt(real_tlv)
                        if evb_enabled_tlv is None:
                            LOG.error("Failed to generate EVB capable tlv")
                            continue
                        self._cached_real_tlv = real_tlv
                        self._cached_emulated_tlv = evb_enabled_tlv
                    self.send_raw_packet(
                        virtual_ovs_interface,
                        self._cached_emulated_tlv)

    def emulate_on_interface(self, uplink_interface,
                             virtual_local_interface,
                             virtual_ovs_interface):

        """Send on the given interface."""

        if uplink_interface is None or virtual_local_interface is None or \
                virtual_ovs_interface is None:
            LOG.error(
                "Invalid iterface uplink: %s , veth local: %s ,veth ovs %s",
                uplink_interface, virtual_local_interface,
                virtual_ovs_interface)
            return

        if dfa_sys_lib.is_intf_up(uplink_interface):

            LOG.info(
                "Emulating EVB on %s , %s , %s",
                uplink_interface,
                virtual_local_interface,
                virtual_ovs_interface)

            self._lldp_helper.enable_lldp(
                virtual_local_interface,
                is_ncb=False,
                is_nb=True)
            real_tlv = self._lldp_helper.get_lldp_tlv(
                virtual_local_interface, is_ncb=False, is_nb=True)
            if real_tlv is None:
                LOG.error(
                    "Could not get tlv on %s, skipping evb emulation",
                    uplink_interface)
                return

            if not self._lldp_helper.get_remote_evb_cfgd(real_tlv):
                evb_enabled_tlv = self._generate_evb_capalble_lldp_pkt(
                    real_tlv)
                if evb_enabled_tlv is None:
                    LOG.error("Failed to generate EVB capable tlv")
                self.send_raw_packet(virtual_ovs_interface, evb_enabled_tlv)

    def _generate_evb_capalble_lldp_pkt(self, tlv):

        """ Parse the given tlv to get the mandetory tlv
            and constructs a raw lldp ethernet frame """

        chassis_id_mac_str = self._lldp_helper.get_remote_chassis_id_mac(tlv)
        if chassis_id_mac_str is None:
            LOG.error("Failed to get chaisis id tlv")
            return None
        port_id_mac_str = self._lldp_helper.get_remote_port_id_mac(tlv)
        port_id_local_str = self._lldp_helper.get_remote_port_id_local(tlv)
        ncb_mac_str = vdp_constants.NCB_DMAC

        # Create chaissis id tlv
        chassis_id_tlv = Tlv(TLV_TYPE["CHASSIS_ID"])
        chassis_id_tlv.add_subtype(
            CHAISIS_ID_SUBTYPE["MAC_ADDRESS"],
            bytearray(chassis_id_mac_str.replace(':', '').decode('hex')))

        # Create port id tlv
        port_id_tlv = Tlv(TLV_TYPE["PORT_ID"])
        if port_id_mac_str is not None:
            port_id_tlv.add_subtype(
                PORT_ID_SUBTYPE["MAC_ADDRESS"],
                bytearray(port_id_mac_str.replace(':', '').decode('hex')))
        if port_id_local_str is not None:
            port_id_tlv.add_subtype(
                PORT_ID_SUBTYPE["LOCALLY_ASSIGNED"],
                port_id_local_str)
        if port_id_local_str is None and port_id_mac_str is None:
            LOG.error("Failed to get port-id tlv")
            return None

        # Create TTL tlv
        ttl_tlv = Tlv(TLV_TYPE["TTL"])
        ttl_tlv.add_data(bytearray(b'\x00\x5a'))

        # Create EVB tlv
        tlv_802_1 = Tlv(TLV_TYPE["CUSTOM"])

        # EVB TLV Set 802.1Qbg-D.2.13

        # +------------------------+--------+--------+--------+---+-----+---+--+-----+--+--+-----+
        # |   OUI - 00-80-C2       |   ST   |   BS   |   SS   | R | RTE | M |RL| RWD |X |RL| RKA |
        # +------------------------+--------+--------+--------+---+-----+---+--+-----+--+--+-----+

        # OUI  Org code                           (3 bytes) [0x0088c2]
        # ST   Subtype                            (1 byte)  [0x0d]
        # BS   EVB Bridge  Status                 (1 byte)
        # SS   EVB Station Status                 (1 byte)
        # R    Max Retries for ECP state machine  (3 bits)
        # RTE  Retransmission Exponent            (5 bits)
        # M    EVB Mode                           (2 bits)
        # RL   Remote or Local                    (1 bit)
        # RWD  Resource Wait Delay                (5 bits)
        # X    Reserved                           (2 bits)
        # RKA  Reinit Keep Alive                  (5 bits)

        evb_tlv_set = bytearray(b'\x0d\x04\x00\x6e\x54\x16')
        tlv_802_1.add_subtype(ORG_CODES["IEEE_802_1"], evb_tlv_set)

        end_tlv = Tlv(TLV_TYPE["END_OF_LLDPDU"])

        smac = bytearray(chassis_id_mac_str.replace(':', '').decode('hex'))
        dmac = bytearray(ncb_mac_str.replace(':', '').decode('hex'))
        ethertype = bytearray(b'\x88\xcc')

        return dmac + smac + ethertype + chassis_id_tlv.pack() \
            + port_id_tlv.pack() + ttl_tlv.pack() + tlv_802_1.pack() \
            + end_tlv.pack()

    def send_raw_packet(self, interface, data):

        """Send packet over raw socket."""

        LOG.info("Sending lldp evb packet")
        soc = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
        try:
            soc.bind((interface, 0))
            soc.send(data)
        except Exception as exc:
            LOG.error(
                "Error occured while sending data over socket %s",
                str(exc))
        soc.close()

    def stop(self):

        """Stop thread execution."""

        self._stop.set()

    def update_frequency(self, frequency):

        """Update frequency of thread execution."""

        self._frequency = frequency

    def update_interfaces(self, uplink_interface,
                          virtual_local_interface,
                          virtual_ovs_interface):

        """Update interfaces."""

        if uplink_interface is None or virtual_local_interface is None \
                or virtual_ovs_interface is None:
            LOG.error("Invalid iterface uplink: %s , veth local: %s ,\
                      veth ovs %s", uplink_interface, virtual_local_interface,
                      virtual_ovs_interface)
            return
        self._lock.acquire()
        self._uplink_interface = uplink_interface
        self._virtual_local_interface = virtual_local_interface
        self._virtual_ovs_interface = virtual_ovs_interface
        self._lock.release()


class Tlv(object):

    """ Tlv helper class to generate tlvs """

    def __init__(self, tlv_type):
        self._type = bytearray(tlv_type)
        self._data = bytearray()

    def add_subtype(self, tlv_subtype, subtype_data):

        """Add tlv data for specfic tlv subtype."""

        if (len(self._data) + 2 + len(subtype_data)) > 511:
            LOG.error("Tlv not added as it exceedes total byte size")
            return False
        self._data = self._data + bytearray(tlv_subtype) + subtype_data
        return True

    def add_data(self, data):

        """Add data to the tlv."""

        if (len(self._data) + len(data)) > 511:
            LOG.error("Tlv not added as it exceedes total byte size")
            return False
        self._data = self._data + data
        return True

    def pack(self):

        """Return the bytearry of the tlv."""

        length = len(self._data)
        tlv_header = struct.pack(
            '!H', (int(binascii.hexlify(self._type), 16) << 9) | length)
        return bytearray(tlv_header) + self._data
