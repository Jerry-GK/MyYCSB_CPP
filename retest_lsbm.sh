sudo make BIND_LSBM=1

sudo rm -rf db/ycsb-lsbm

sudo cp -r db/ycsb-lsbm-source-24B-1KB-4GB-random db/ycsb-lsbm

sudo rm -f ./profile/data

sudo perf record -F 99 --call-graph dwarf -g --delay 0 -o ./profile/data/lsbm.data ./ycsb -run -db lsbm -P workloads/workload_cust -P lsbm/lsbm.properties -s

sudo perf script -i ./profile/data/lsbm.data | \
    stackcollapse-perf.pl | \
    flamegraph.pl > ./profile/lsbm.svg