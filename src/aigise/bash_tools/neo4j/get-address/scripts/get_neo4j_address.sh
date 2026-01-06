#!/bin/bash

# get_neo4j_address.sh - Get IP address of container
# Usage: ./get_neo4j_address.sh

# Get first IP address
IP=$(hostname -I | awk '{print $1}')

if [ -z "$IP" ]; then
    echo '{"result": "", "error": "Failed to get IP address"}'
else
    echo "{\"result\": \"$IP\"}"
fi
