sudo make BIND_TOPLINGDB=1

sudo rm -rf db/ycsb-toplingdb

sudo cp -r db/ycsb-toplingdb-source-24B-1KB-4GB-random db/ycsb-toplingdb

sudo rm -f ./profile/data

sudo perf record -F 99 --call-graph dwarf -g --delay 0 -o ./profile/data/toplingdb.data ./ycsb -run -db toplingdb -P workloads/workload_cust -P toplingdb/toplingdb.properties -s

sudo perf script -i ./profile/data/toplingdb.data | \
    stackcollapse-perf.pl | \
    flamegraph.pl > ./profile/toplingdb.svg