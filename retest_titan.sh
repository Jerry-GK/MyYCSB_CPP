make BIND_TITAN=1

rm -rf db/ycsb-titan

cp -r db/ycsb-titan-source-24B-1KB-4GB-random db/ycsb-titan

rm -f ./profile/data

perf record -F 99 --call-graph dwarf -g --delay 0 -o ./profile/data/titan.data ./ycsb -run -db titan -P workloads/workload_cust -P titan/titan.properties -s

perf script -i ./profile/data/titan.data | \
    stackcollapse-perf.pl | \
    flamegraph.pl > ./profile/titan.svg
