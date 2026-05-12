make BIND_LSBM=1

rm -rf db/ycsb-lsbm

cp -r db/ycsb-lsbm-source-24B-1KB-4GB-random db/ycsb-lsbm

rm -f ./profile/data

perf record -F 99 --call-graph dwarf -g --delay 0 -o ./profile/data/lsbm.data ./ycsb -run -db lsbm -P workloads/workload_cust -P lsbm/lsbm.properties -s

perf script -i ./profile/data/lsbm.data | \
    stackcollapse-perf.pl | \
    flamegraph.pl > ./profile/lsbm.svg
