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
#
# Service Constants

AUTO_NWK_CREATE = True
DEVICE = 'phy_asa'
VLAN_ID_MIN = 2550
VLAN_ID_MAX = 2650
MOB_DOMAIN_NAME = 'md0'
HOST_PROF = 'serviceNetworkUniversalDynamicRoutingESProfile'
HOST_FWD_MODE = 'proxy-gateway'
PART_PROF = 'vrf-common-universal-external-dynamic-ES'
EXT_PROF = 'externalNetworkUniversalDynamicRoutingESProfile'
EXT_FWD_MODE = 'anycast-gateway'
IN_IP_START = '100.100.2.0/24'
IN_IP_END = '100.100.20.0/24'
OUT_IP_START = '200.200.2.0/24'
OUT_IP_END = '200.200.20.0/24'
DUMMY_IP_SUBNET = '9.9.9.0/24'

IN_SERVICE_SUBNET = 'FwServiceInSub'
IN_SERVICE_NWK = 'FwServiceInNwk'
SERV_PART_NAME = 'FW_SERV_PART'
OUT_SERVICE_SUBNET = 'FwServiceOutSub'
OUT_SERVICE_NWK = 'FwServiceOutNwk'
DUMMY_SERVICE_RTR = 'DUMMY_SRVC_RTR'
DUMMY_SERVICE_NWK = 'DUMMY_SRVC_NWK'
