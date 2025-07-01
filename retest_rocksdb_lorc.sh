#!/bin/bash

db=$1
# lorc=$2
# load=$3
# run=$4
# distribution=$5
# mode=$6

BASE_DB=rocksdb_lorc

if [[ -z "$mode" ]]; then
    mode="test"
fi

# Check db parameter
if [[ "$db" != "rocksdb" && "$db" != "blobdb" ]]; then
    echo "Error: db must be 'rocksdb' or 'blobdb'"
    exit 1
fi

lorc=""
load_flag=""
run_flag=""

# Check if load or run is specified in any position
for arg in "${@:2}"; do
    if [[ "$arg" == "load" ]]; then
        load_flag="-load"
    elif [[ "$arg" == "run" ]]; then
        run_flag="-run"
    elif [[ "$arg" == "build" || "$arg" == "test" || "$arg" == "profile" || "$arg" == "debug" ]]; then
        mode="$arg"
    elif [[ "$arg" == "lorc" ]]; then
        lorc="lorc"
    elif [[ "$arg" == "ordered" || "$arg" == "random" || "$arg" == "hashed" ]]; then
        distribution="$arg"
    else
        echo "Error: Invalid argument '$arg'."
        exit 1
    fi
done

# Set properties file based on lorc parameter
if [[ "$lorc" == "lorc" ]]; then
    properties_file="${db}_lorc.properties"
else
    properties_file="${db}.properties"
fi

source_postfix="-source-24B-1KB-4GB-${distribution}"

# Check that at least one of 'load' or 'run' is specified
if [[ -z "$load_flag" && -z "$run_flag" ]]; then
    echo "Error: At least one of 'load' or 'run' must be specified"
    exit 1
fi

if [[ -z "$distribution" ]]; then
    echo "Error: Distribution must be specified as 'ordered', 'random', or 'hashed'"
    exit 1
fi

# Check and process mode parameter
if [[ "$mode" == "build" ]]; then
    echo "Building YCSB..."
    sudo make clean
    sudo make BIND_ROCKSDB_LORC=1
    # exit
    exit 0
fi

# Prepare database directory
sudo rm -rf ./db/ycsb-$db
if [[ "$load_flag" == "" ]]; then
    echo "Copying existing database ./db/ycsb-${db}${source_postfix}"
    sudo cp -r ./db/ycsb-${db}${source_postfix} ./db/ycsb-$db
fi

# Execute test
echo "Running YCSB with: db=$db, distribution=$distribution, lorc=$lorc, operations=$load_flag $run_flag"

if [[ "$mode" == "profile" ]]; then
    # Set perf output filename
    profile_filename="ycsb"

    # Use perf for performance sampling (requires root privileges or perf permissions)
    sudo perf record -F 99 --call-graph dwarf -g --delay 0 -o ./profile/data/${profile_filename}.data ./ycsb $load_flag $run_flag -db $BASE_DB -P workloads/workload_cust -P $BASE_DB/$properties_file -s

    # Generate flame graph (FlameGraph tool needs to be installed)
    sudo perf script -i ./profile/data/${profile_filename}.data | \
        stackcollapse-perf.pl | \
        flamegraph.pl > ./profile/${profile_filename}_linux_flamegraph.svg

    # Clean up intermediate files
    rm -f ./profile/data/${profile_filename}.data
elif [[ "$mode" == "test" ]]; then
    sudo ./ycsb $load_flag $run_flag -db $BASE_DB -P workloads/workload_cust -P $BASE_DB/$properties_file -s
elif [[ "$mode" == "debug" ]]; then
    echo "Starting GDB debug session..."
    sudo gdb --args ./ycsb $load_flag $run_flag -db $BASE_DB -P workloads/workload_cust -P $BASE_DB/$properties_file -s
fi