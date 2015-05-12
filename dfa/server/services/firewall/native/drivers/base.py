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

import abc
import six

from dfa.common import dfa_logger as logging

LOG = logging.getLogger(__name__)


@six.add_metaclass(abc.ABCMeta)
class BaseDrvr(object):

    '''Base Driver class for FW driver classes.'''

    # def __init__(self):
    #    Pass

    @abc.abstractmethod
    def initialize(self):
        '''Initialize method'''
        Pass

    @abc.abstractmethod
    def get_name(self):
        '''Return the name of the driver service'''
        Pass

    @abc.abstractmethod
    def store_dcnm_obj(self):
        '''Store the DCNM obj'''
        Pass

    @abc.abstractmethod
    def create_fw(self, tenant_id, data):
        '''Create the Firewall'''
        Pass

    @abc.abstractmethod
    def delete_fw(self, tenant_id, data):
        '''Create the Firewall'''
        Pass

    @abc.abstractmethod
    def modify_fw(self, tenant_id, data):
        '''Create the Firewall'''
        Pass
