#!/bin/bash

db=$1
lorc=$2
load=$3
run=$4
mode=$5

source_postfix="-source-1KB-4G"
properties_file="${db}.properties"

if [[ -z "$mode" ]]; then
    mode="test"
fi

# Check db parameter
if [[ "$db" != "rocksdb" && "$db" != "blobdb" ]]; then
    echo "Error: db must be 'rocksdb' or 'blobdb'"
    exit 1
fi

# Check and process load parameter
if [[ "$load" == "load" ]]; then
    load_flag="-load"
else
    load_flag=""
fi

# Check and process run parameter
if [[ "$run" == "run" ]]; then
    run_flag="-run"
else
    run_flag=""
fi

# Set properties file based on lorc parameter
if [[ "$lorc" == "lorc" ]]; then
    properties_file="${db}_lorc.properties"
else
    properties_file="${db}.properties"
fi

# Check if load or run is specified in any position
for arg in "$@"; do
    if [[ "$arg" == "load" ]]; then
        load_flag="-load"
    elif [[ "$arg" == "run" ]]; then
        run_flag="-run"
    elif [[ "$arg" == "build" || "$arg" == "test" || "$arg" == "profile" ]]; then
        mode="$arg"
    elif [[ "$arg" == "lorc" ]]; then
        properties_file="${db}_lorc.properties"
    fi
done

# Check that at least one of 'load' or 'run' is specified
if [[ -z "$load_flag" && -z "$run_flag" ]]; then
    echo "Error: At least one of 'load' or 'run' must be specified"
    exit 1
fi

# Check and process mode parameter
if [[ "$mode" == "build" ]]; then
    echo "Building YCSB..."
    sudo make clean
    sudo make BIND_ROCKSDB=1
    # exit
    exit 0
fi

# Prepare database directory
sudo rm -rf ./db/ycsb-$db
sudo cp -r ./db/ycsb-${db}${source_postfix} ./db/ycsb-$db

# Execute test
echo "Running YCSB with: db=$db, operations=$load_flag $run_flag"

if [[ "$mode" == "profile" ]]; then
    # Set perf output filename
    profile_filename="ycsb"

    # Use perf for performance sampling (requires root privileges or perf permissions)
    sudo perf record -F 99 --call-graph dwarf -g --delay 0 -o ./profile/data/${profile_filename}.data ./ycsb $load_flag $run_flag -db rocksdb -P workloads/workload_cust -P rocksdb/$properties_file -s

    # Generate flame graph (FlameGraph tool needs to be installed)
    sudo perf script -i ./profile/data/${profile_filename}.data | \
        stackcollapse-perf.pl | \
        flamegraph.pl > ./profile/${profile_filename}_linux_flamegraph.svg

    # Clean up intermediate files
    rm -f ./profile/data/${profile_filename}.data
elif [[ "$mode" == "test" ]]; then
    sudo ./ycsb $load_flag $run_flag -db rocksdb -P workloads/workload_cust -P rocksdb/$properties_file -s
fi