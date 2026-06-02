#!/bin/bash

# Preserve PYTHONPATH for python-server
# supervisord consumes PYTHONPATH, so we use a separate variable to store it
if [ -n "${SRV_PYTHONPATH}" ]; then
    export PYTHONPATH="${SRV_PYTHONPATH}${PYTHONPATH:+:${PYTHONPATH}}"
fi

echo "Starting python-server with PYTHONPATH=${PYTHONPATH}"

# Execute python-server with all arguments
exec /usr/local/bin/python-server "$@"