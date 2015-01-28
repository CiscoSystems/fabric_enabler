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


from dfa.common import dfa_sys_lib as dsl
from dfa.common import dfa_logger as logging
import Queue
import time
from dfa.common import utils

LOG = logging.getLogger(__name__)


class IpMacPort(object):
    """This class keeps host rule information."""

    def __init__(self, ip, mac, port):
       self.ip = ip
       self.mac = mac
       self.port = port
       self.chain = 'neutron-openvswi-s' + port[:10]

class IptablesDriver(object):
    """This class provides API to update iptables rule."""

    def __init__(self, cfg):
        self._root_helper = cfg.sys.root_helper

        # List that contains VM info: ip, mac and port.
        self.rule_info = []

        # Queue to keep messages from server
        self._iptq = Queue.Queue()

    def update_rule_entry(self, rule_info):
        """Update the rule_info list."""

        if rule_info.get('status') == 'up':
            self.add_rule_entry(rule_info)
        if rule_info.get('status') == 'down':
            self.remove_rule_entry(rule_info)

    def add_rule_entry(self, rule_info):
        """Add host data object to the rule_info list."""

        new_rule = IpMacPort(rule_info.get('ip'), rule_info.get('mac'),
                             rule_info.get('port'))
        LOG.debug('Added rule info %s to the list' % rule_info)
        self.rule_info.append(new_rule)

    def remove_rule_entry(self, rule_info):
        """Remove host data object from rule_info list."""

        temp_list = list(self.rule_info)
        for rule in temp_list:
            if (rule.ip == rule_info.get('ip') and
                rule.mac == rule_info.get('mac') and
                rule.port == rule_info.get('port')):
                LOG.debug('Removed rule info %s from the list' % rule_info)
                self.rule_info.remove(rule)

    def _find_chain_name(self, mac):
        """Find a rule associated with a given mac."""

        ipt_cmd = ['iptables', '-t', 'filter', '-S']
        cmdo = dsl.execute(ipt_cmd, root_helper=self._root_helper)
        for o in cmdo.split('\n'):
            if mac in o.lower():
                chain = o.split()[1]
                LOG.info('Find %(chain)s for %(mac)s.' % ( {'chain': chain,
                                                            'mac': mac}))
                return chain

    def _find_rule_no(self, mac):
        """Find rule number associated with a given mac."""

        ipt_cmd = ['iptables', '-L', '--line-numbers']
        cmdo = dsl.execute(ipt_cmd, self._root_helper)
        for o in cmdo.split('\n'):
            if mac in o.lower():
                rule_no = o.split()[0]
                LOG.info('Found rule %(rule)s for %(mac)s.' % ({'rule': rule_no,
                                                               'mac': mac}))
                return rule_no

    def update_ip_rule(self, ip, mac):
        """Update a rule associated with given ip and mac."""

        rule_no = self._find_rule_no(mac)
        chain = self._find_chain_name(mac)
        if not rule_no or not chain:
            LOG.error('Failed to update ip rule for %(ip)s %(mac)s' % (
                                    {'ip': ip, 'mac': mac}))
            return

        update_cmd = ['iptables', '-R', '%s' % chain, '%s' % rule_no,
                      '-s', '%s/32' % ip, '-m', 'mac', '--mac-source',
                      '%s' % mac, '-j', 'RETURN']
        LOG.info('Execute command: %s' % (update_cmd))
        dsl.execute(update_cmd, self._root_helper)

    def enqueue_event(self, event):
        """Enqueue the given event.

        The event contains host data (ip, mac, port) which will be used to
        update the spoofing rule for the host in the iptables.
        """

        LOG.debug('Enqueue iptable event %s.' % event)
        if event.get('status') == 'up':
            for rule in self.rule_info:
                if (rule.mac == event.get('mac') and
                    rule.ip == event.get('ip') and
                    rule.port == event.get('port')):
                    # Entry already exist in the list.
                    return
        self._iptq.put(event)

    def create_thread(self):
        """Create a task to process event for updating iptables."""

        ipt_thrd = utils.EventProcessingThread('iptables', self,
                                               'process_rule_info')
        return ipt_thrd

    def updtate_iptables(self):
        """Update iptables based on information in the rule_info."""

        LOG.info('Starting update_iptables...')
        # Read the iptables
        iptables_cmds = ['iptables-save', '-c']
        all_rules = dsl.execute(iptables_cmds, root_helper=self._root_helper)

        LOG.debug('iptables rules: %s' % all_rules)
        # For each rule in rule_info update the rule if necessary.
        new_rules = []
        for line in all_rules.split('\n'):
            new_line = line
            for rule in self.rule_info:
                if rule.mac in line.lower() and rule.chain in line.lower():
                    newl = line.split()
                    newl[4] = rule.ip + '/32'
                    new_line = ' '.join(newl)
                    LOG.info('Modified %s. New rule is %s' % (line, new_line))
            new_rules.append(new_line)

        if new_rules:
            # Updated all the rules. Now commit the new rules.
            LOG.info('Applying new rules...')
            iptables_cmds = ['iptables-restore', '-c']
            dsl.execute(iptables_cmds, process_input='\n'.join(new_rules),
                                              root_helper=self._root_helper)

    def process_rule_info(self):
        """Task responsible for processing event queue."""

        new_event = False
        while True:
            try:
                event = self._iptq.get(block=False)
                LOG.debug('Dequeue event: %s.' % (event))
                self.update_rule_entry(event)
                new_event = True
            except Queue.Empty:
                if new_event:
                    LOG.info('Queue is empty now start updating iptables...')
                    self.updtate_iptables()
                    new_event = False
                time.sleep(1)
            except Exception:
                LOG.exception('ERROR: failed to process queue')
