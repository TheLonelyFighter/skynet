#!/bin/bash

# get the path to the current directory
MY_PATH=`dirname "$0"`
MY_PATH=`( cd "$MY_PATH" && pwd )`
cd $MY_PATH

./singularity.sh exec "source ~/.bashrc && code ~/summer-school-2022/mrim_task/mrim_planner/ > /dev/null 2>&1 &"
