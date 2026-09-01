#!/usr/bin/env bash
set -e
echo "Starting..."
name = "world"            # bash does not allow spaces around =
echo "Hello $name!"
cd /does/not/exist         # intentional failure
echo "done"
