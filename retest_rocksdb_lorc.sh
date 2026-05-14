#!/bin/bash

db=$1
# lorc=$2
# load=$3
# run=$4
# distribution=$5
# mode=$6

BASE_DB=rocksdb_lorc
workload_file="workloads/workload_cust_20GB"

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
    elif [[ "$arg" == "build" || "$arg" == "test" || "$arg" == "profile" || "$arg" == "debug" || "$arg" == "dry-run" ]]; then
        mode="$arg"
    elif [[ "$arg" == "lorc" ]]; then
        lorc="lorc"
    elif [[ "$arg" == "ordered" || "$arg" == "random" || "$arg" == "hashed" ]]; then
        distribution="$arg"
    elif [[ "$arg" == workloads/* ]]; then
        workload_file="$arg"
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

# Check that at least one of 'load' or 'run' is specified
if [[ -z "$load_flag" && -z "$run_flag" ]]; then
    echo "Error: At least one of 'load' or 'run' must be specified"
    exit 1
fi

if [[ -z "$distribution" ]]; then
    echo "Error: Distribution must be specified as 'ordered', 'random', or 'hashed'"
    exit 1
fi

if [[ ! -f "$workload_file" ]]; then
    echo "Error: workload file does not exist: $workload_file"
    exit 1
fi

get_property() {
    local file="$1"
    local key="$2"
    awk -F= -v key="$key" '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        {
            k=$1
            v=$0
            sub(/^[^=]*=/, "", v)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", v)
            if (k == key) value=v
        }
        END { if (value != "") print value }
    ' "$file"
}

prop_or_default() {
    local file="$1"
    local key="$2"
    local default="$3"
    local value
    value=$(get_property "$file" "$key")
    if [[ -n "$value" ]]; then
        echo "$value"
    else
        echo "$default"
    fi
}

is_zero_prop() {
    local value="$1"
    awk -v v="$value" 'BEGIN { exit !((v + 0) == 0) }'
}

recordcount=$(prop_or_default "$workload_file" "recordcount" "0")
operationcount=$(prop_or_default "$workload_file" "operationcount" "0")
hot_data_ratio=$(prop_or_default "$workload_file" "hot_data_ratio" "1")
warmup_ratio=$(prop_or_default "$workload_file" "warmup_ratio" "0")
minscanlength=$(prop_or_default "$workload_file" "minscanlength" "1")
maxscanlength=$(prop_or_default "$workload_file" "maxscanlength" "$minscanlength")
readproportion=$(prop_or_default "$workload_file" "readproportion" "0")
scanproportion=$(prop_or_default "$workload_file" "scanproportion" "0")
updateproportion=$(prop_or_default "$workload_file" "updateproportion" "0")
insertproportion=$(prop_or_default "$workload_file" "insertproportion" "0")

case "$recordcount" in
    4000000)
        source_postfix="source-24B-1KB-4GB"
        ;;
    20000000)
        source_postfix="source-24B-1KB-20GB"
        ;;
    *)
        source_postfix="${SOURCE_POSTFIX:-source-24B-1KB-20GB}"
        ;;
esac

read_only_workload=false
if is_zero_prop "$updateproportion" && is_zero_prop "$insertproportion"; then
    read_only_workload=true
fi

avgscanlength=$(awk -v min="$minscanlength" -v max="$maxscanlength" 'BEGIN { print (min + max) / 2.0 }')
hot_records=$(awk -v records="$recordcount" -v ratio="$hot_data_ratio" 'BEGIN { print records * ratio }')
warmup_ops=$(awk -v ops="$operationcount" -v ratio="$warmup_ratio" 'BEGIN { print ops * ratio }')
warmup_hot_coverage=$(awk -v warmup="$warmup_ops" -v scan="$avgscanlength" -v hot="$hot_records" 'BEGIN { if (hot == 0) print 0; else print warmup * scan / hot }')
coverage_ok=$(awk -v coverage="$warmup_hot_coverage" 'BEGIN { if (coverage >= 1.0) print "yes"; else print "no" }')

source_db_path="./db/ycsb-${source_postfix}/ycsb-${db}-${source_postfix}-${distribution}"
work_db_path="./db/ycsb-$db"
db_path="$work_db_path"
copy_db=true
extra_props=()

if [[ -n "${ROCKSDB_USE_DIRECT_READS:-}" ]]; then
    extra_props+=("-p" "rocksdb.use_direct_reads=${ROCKSDB_USE_DIRECT_READS}")
fi

if [[ "$run_flag" != "" && "$load_flag" == "" && "$read_only_workload" == "true" ]]; then
    copy_db=false
    db_path="$source_db_path"
    extra_props+=("-p" "rocksdb.dbname=$db_path")
    extra_props+=("-p" "rocksdb.disable_auto_compactions=true")
    extra_props+=("-p" "rocksdb.create_if_missing=false")
    extra_props+=("-p" "rocksdb.destroy=false")
    extra_props+=("-p" "rocksdb.read_only=true")
fi

echo "Experiment config:"
echo "  workload=$workload_file"
echo "  db=$db"
echo "  distribution=$distribution"
echo "  lorc=$lorc"
echo "  read_only_workload=$read_only_workload"
echo "  copy_db=$copy_db"
echo "  source_postfix=$source_postfix"
echo "  db_path=$db_path"
if [[ -n "${ROCKSDB_USE_DIRECT_READS:-}" ]]; then
    echo "  use_direct_reads=$ROCKSDB_USE_DIRECT_READS"
fi
echo "  operationcount=$operationcount"
echo "  warmup_ratio=$warmup_ratio"
echo "  hot_data_ratio=$hot_data_ratio"
echo "  scan_length=[$minscanlength,$maxscanlength]"
echo "  warmup_hot_coverage=${warmup_hot_coverage}x"
if [[ "$coverage_ok" != "yes" ]]; then
    echo "Warning: warmup does not cover the full configured hot range once on average."
fi

if [[ "$mode" == "dry-run" ]]; then
    printf "Command:"
    printf " %q" ./ycsb "$load_flag" "$run_flag" -db "$BASE_DB" -P "$workload_file" -P "$BASE_DB/$properties_file" "${extra_props[@]}" -s
    printf "\n"
    exit 0
fi

# Check and process mode parameter
if [[ "$mode" == "build" ]]; then
    echo "Building YCSB..."
    make clean
    make BIND_ROCKSDB_LORC=1
    # exit
    exit 0
fi

# Prepare database directory
if [[ "$copy_db" == "true" ]]; then
    rm -rf "$work_db_path"
    if [[ "$load_flag" == "" ]]; then
        echo "Copying existing database $source_db_path"
        cp -r "$source_db_path" "$work_db_path"
    fi
else
    if [[ ! -d "$source_db_path" ]]; then
        echo "Error: source database does not exist: $source_db_path"
        exit 1
    fi
    echo "Using source database directly for read-only workload."
fi

# Execute test
echo "Running YCSB with: db=$db, distribution=$distribution, lorc=$lorc, operations=$load_flag $run_flag"

if [[ "$mode" == "profile" ]]; then
    # Set perf output filename
    profile_filename="ycsb"

    # Use perf for performance sampling (requires root privileges or perf permissions)
    perf record -F 99 --call-graph dwarf -g --delay 40000 -o ./profile/data/${profile_filename}.data ./ycsb $load_flag $run_flag -db $BASE_DB -P "$workload_file" -P $BASE_DB/$properties_file "${extra_props[@]}" -s

    # Generate flame graph (FlameGraph tool needs to be installed)
    perf script -i ./profile/data/${profile_filename}.data | \
        stackcollapse-perf.pl | \
        flamegraph.pl > ./profile/${profile_filename}_linux_flamegraph.svg

    # Clean up intermediate files
    rm -f ./profile/data/${profile_filename}.data
elif [[ "$mode" == "test" ]]; then
    ./ycsb $load_flag $run_flag -db $BASE_DB -P "$workload_file" -P $BASE_DB/$properties_file "${extra_props[@]}" -s
elif [[ "$mode" == "debug" ]]; then
    echo "Starting GDB debug session..."
    gdb --args ./ycsb $load_flag $run_flag -db $BASE_DB -P "$workload_file" -P $BASE_DB/$properties_file "${extra_props[@]}" -s
fi
