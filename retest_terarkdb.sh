make BIND_TERARKDB=1

rm -rf db/ycsb-terarkdb

cp -r db/ycsb-terarkdb-source-24B-1KB-4GB-random db/ycsb-terarkdb

rm -f ./profile/data

perf record -F 99 --call-graph dwarf -g --delay 0 -o ./profile/data/terarkdb.data ./ycsb -run -db terarkdb -P workloads/workload_cust -P terarkdb/terarkdb.properties -s

perf script -i ./profile/data/terarkdb.data | \
    stackcollapse-perf.pl | \
    flamegraph.pl > ./profile/terarkdb.svg
