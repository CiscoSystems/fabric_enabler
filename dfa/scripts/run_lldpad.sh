#!/bin/bash
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

generate_init () {
    DISTR=$1
    SRC_DIR=$2

    if [ "$DISTR" == ubuntu ]
    then
        logger -t SETUP_LLDPAD "Ubuntu distro"
        if [ ! -f /etc/init/lldpad.conf ]
        then
            logger -t SETUP_LLDPAD "Copying lldpad upstart"
            sudo cp $SRC_DIR/dfa/scripts/lldpad.conf /etc/init/
        fi
    else
        if [ "$DISTR" == redhat ] || [ "$DISTR" == centos ]
        then
            logger -t SETUP_LLDPAD "Redhat based distro"
            if [ ! -f /usr/lib/systemd/system/ ]
            then
                logger -t SETUP_LLDPAD "Copying lldpad systemd"
                sudo cp -n $SRC_DIR/dfa/scripts/lldpad.service \
                        /usr/lib/systemd/system/
            fi
            act_stat=`sudo systemctl status lldpad | grep Loaded | grep enabled`
            if [ $? != 0 ]
            then
                logger -t SETUP_LLDPAD "Enabling lldpad"
                sudo systemctl enable lldpad
            fi
        else
            logger -t SETUP_LLDPAD "Unknown Distribution $DISTR"
        fi
    fi
    sleep 5
}

get_status () {
    DISTR=$1

    if [ "$DISTR" == ubuntu ]
    then
        LLDP_STAT=`sudo status lldpad | grep -i stop`
        if [ "$LLDP_STAT" == "" ]
        then
            return 1
        else
            return 0
        fi
    else
        if [ "$DISTR" == redhat ] || [ "$DISTR" == centos ]
        then
            is_act=`sudo systemctl is-active lldpad`
            if [ "$is_act" != "active" ]
            then
                return 0
            else
                return 1
            fi
        else
            logger -t SETUP_LLDPAD "Unknown Distribution $DISTR"
            return -1
        fi
    fi
}

start_lldpad () {
    DISTR=$1

    if [ "$DISTR" == ubuntu ]
    then
        logger -t SETUP_LLDPAD "Starting Lldpad for Ubuntu"
        sudo start lldpad
    else
        if [ "$DISTR" == redhat ] || [ "$DISTR" == centos ]
        then
            logger -t SETUP_LLDPAD "Starting Lldpad for Redhat/Centos"
            sudo systemctl start lldpad
        else
            logger -t SETUP_LLDPAD "Not supported distro"
        fi
    fi
}

SRC_DIR=$1
if [ "$SRC_DIR  " == "" ]
then
    logger -t SETUP_LLDPAD "Empty SRC DIR"
    exit
fi
flag=0
logger -t SETUP_LLDPAD "Copying lldpad files"
NAME=`$SRC_DIR/dfa/scripts/distr.sh`
generate_init $NAME $SRC_DIR

LLDP_PATH=`which lldpad`
if [ $? == 0 ]
then
    get_status $NAME
    LLDP_STAT=$?

    if [ $LLDP_STAT -eq 1 ]
    then
        logger -t SETUP_LLDPAD "lldpad is already running"
    else
        if [ $LLDP_STAT -eq 0 ]
        then
            logger -t SETUP_LLDPAD "starting lldpad"
            start_lldpad $NAME
        else
            logger -t SETUP_LLDPAD "Could not start lldpad"
        fi
    fi
else
    logger  -t SETUP_LLDPAD "Install and start lldpad service"
fi
logger -t SETUP_LLDPAD "Done with lldpad"
