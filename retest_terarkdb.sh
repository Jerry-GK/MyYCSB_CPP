sudo make BIND_TERARKDB=1

sudo rm -rf db/ycsb-terarkdb

sudo cp -r db/ycsb-terarkdb-source-24B-1KB-4GB-random db/ycsb-terarkdb

sudo rm -f ./profile/data

sudo perf record -F 99 --call-graph dwarf -g --delay 0 -o ./profile/data/terarkdb.data ./ycsb -run -db terarkdb -P workloads/workload_cust -P terarkdb/terarkdb.properties -s

sudo perf script -i ./profile/data/terarkdb.data | \
    stackcollapse-perf.pl | \
    flamegraph.pl > ./profile/terarkdb.svg