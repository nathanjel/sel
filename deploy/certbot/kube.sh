#!/usr/bin/env bash
# kubectl on this box's microk8s, whose socket belongs to the microk8s group.
exec sg microk8s -c "/snap/bin/microk8s kubectl $*"
