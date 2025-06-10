#!/bin/bash

db=$1
load=$2
run=$3
build=$4

source_postfix="-source-1KB-4G"

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

# Check if load or run is specified in any position
for arg in "$@"; do
    if [[ "$arg" == "load" ]]; then
        load_flag="-load"
    elif [[ "$arg" == "run" ]]; then
        run_flag="-run"
    elif [[ "$arg" == "build" ]]; then
        build="build"
    fi
done

# Check that at least one of 'load' or 'run' is specified
if [[ -z "$load_flag" && -z "$run_flag" ]]; then
    echo "Error: At least one of 'load' or 'run' must be specified"
    exit 1
fi

# Check and process build parameter
if [[ "$build" == "build" ]]; then
    echo "Building YCSB..."
    sudo make clean
    sudo make BIND_ROCKSDB=1
fi

# Prepare database directory
sudo rm -rf ./db/ycsb-$db
sudo cp -r ./db/ycsb-${db}${source_postfix} ./db/ycsb-$db

# Execute test
echo "Running YCSB with: db=$db, operations=$load_flag $run_flag"
sudo ./ycsb $load_flag $run_flag -db rocksdb -P workloads/workload_cust -P rocksdb/$db.properties -s