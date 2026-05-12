make BIND_TOPLINGDB=1

rm -rf db/ycsb-toplingdb

cp -r db/ycsb-toplingdb-source-24B-1KB-4GB-random db/ycsb-toplingdb

rm -f ./profile/data

perf record -F 99 --call-graph dwarf -g --delay 0 -o ./profile/data/toplingdb.data ./ycsb -run -db toplingdb -P workloads/workload_cust -P toplingdb/toplingdb.properties -s

perf script -i ./profile/data/toplingdb.data | \
    stackcollapse-perf.pl | \
    flamegraph.pl > ./profile/toplingdb.svg
