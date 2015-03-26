#!/bin/sh

SRC_DIR=TODO(Who calls this??)
NAME=`$SRC_DIR/dfa/scripts/distr.sh`
if [ "$DISTR" == ubuntu ]
then
    sudo start fabric_enabler_agent
else
    if [ "$DISTR" == redhat ] || [ "$DISTR" == centos ]
    then
        sudo systemctl start fabric_enabler_agent
    fi
fi
