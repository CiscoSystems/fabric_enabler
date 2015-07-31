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
# @author: Padmanabhan Krishnan, Cisco Systems, Inc.

import stevedore
from dfa.common import dfa_logger as logging

LOG = logging.getLogger(__name__)


class DeviceMgr(stevedore.named.NamedExtensionManager):

    '''Device Manager'''

    def __init__(self, cfg, dev_name):
        super(DeviceMgr, self).__init__('services.firewall.native.drivers',
                                        # cfg.firewall.device,
                                        dev_name, invoke_on_load=True)

    def get_drvr_obj(self):
        # Is there a cleaner way other than this Loop TODO
        for ext in self:
            return ext.obj
