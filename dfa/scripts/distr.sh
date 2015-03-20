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
# A simple script to detect the linux distribution.
# 'lsb-release' is checked first and if not present 'os-release' is checked.
# This should most likely get it. IF not python platform call is used.
# gcc version can also alternately be used to get the distribution.
#
# @author: Padmanabhan Krishnan, Cisco Systems, Inc.

get_distr_int () {
    if [ -f /etc/lsb-release ]
    then
        NAME=`cat /etc/lsb-release | grep DISTRIB_ID | awk -F "=" '{print $2}'`
        echo $NAME
    else
        if [ -f /etc/os-release ]
        then
            NAME=`cat /etc/os-release | grep -w ID | awk -F "=" '{print $2}'`
            echo $NAME
        else
            NAME=`python -mplatform | awk -F "with-" '{print $2}' | awk -F "-" '{print $1}'`
            if [ $? == 0 ]
            then
                echo $NAME
            else
                echo "Unknown"
            fi
        fi
    fi
}

name=$(get_distr_int)
if [ "$name" == Ubuntu ] || [ "$name" == "\"Ubuntu\"" ]
then
    echo "ubuntu"
fi
if [ "$name" == centos ] || [ "$name" == "\"centos\"" ]
then
    echo "centos"
fi
if [ "$name" == redhat ] || [ "$name" == "\"redhat\"" ]
then
    echo "redhat"
fi
