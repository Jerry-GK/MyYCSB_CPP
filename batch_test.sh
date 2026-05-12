# ./retest_rocksdb_lorc.sh blobdb run random test > result/log/log-blobdb-mixed-zipfian-2.txt
# # mv profile/ycsb_linux_flamegraph.svg profile/ycsb-blobdb-mixed-zipfian.svg
# du -sh db/ycsb-blobdb >> result/log/size-blobdb-mixed-zipfian-2.txt

# ./retest_rocksdb_lorc.sh blobdb lorc run random test > result/log/log-blobdb-lorc-mixed-zipfian-2.txt
# # mv profile/ycsb_linux_flamegraph.svg profile/ycsb-blobdb-lorc-mixed-zipfian.svg
# du -sh db/ycsb-blobdb >> result/log/size-blobdb-lorc-mixed-zipfian-2.txt

# ./retest_rocksdb_lorc.sh rocksdb run random test > result/log/log-rocksdb-mixed-zipfian-2.txt
# # mv profile/ycsb_linux_flamegraph.svg profile/ycsb-rocksdb-mixed-zipfian.svg
# du -sh db/ycsb-rocksdb >> result/log/size-rocksdb-mixed-zipfian-2.txt

# ./retest_rocksdb_lorc.sh rocksdb lorc run random test > result/log/log-rocksdb-lorc-mixed-zipfian-2.txt
# # mv profile/ycsb_linux_flamegraph.svg profile/ycsb-rocksdb-lorc-mixed-zipfian.svg
# du -sh db/ycsb-rocksdb >> result/log/size-rocksdb-lorc-mixed-zipfian-2.txt

./retest_rocksdb_lorc.sh rocksdb lorc run random test > result/log/log-rocksdb-lorc-mixed-zipfian-allscan-nocomp-log-1.txt 

./retest_rocksdb_lorc.sh blobdb lorc run random test > result/log/log-blobdb-lorc-mixed-zipfian-allscan-nocomp-log-1.txt
