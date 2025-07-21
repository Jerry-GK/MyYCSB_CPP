#
#  Makefile
#  YCSB-cpp
#
#  Copyright (c) 2020 Youngjae Lee <ls4154.lee@gmail.com>.
#  Copyright (c) 2014 Jinglei Ren <jinglei@ren.systems>.
#  Modifications Copyright 2023 Chengye YU <yuchengye2013 AT outlook.com>.
#


#---------------------build config-------------------------

ROCKSDB_LORC_BUILD_TYPE ?= release
TERARKDB_BUILD_TYPE ?= release
TOPLINGDB_BUILD_TYPE ?= release
LSBM_BUILD_TYPE ?= release

CUSTOM_ROCKSDB_LORC_PATH ?= /home/gjr/mylibs/lorcdb_${ROCKSDB_LORC_BUILD_TYPE}
CUSTOM_TERARKDB_PATH ?= /home/gjr/mylibs/terarkdb_${TERARKDB_BUILD_TYPE}
CUSTOM_TOPLINGDB_PATH ?= /home/gjr/mylibs/toplingdb_${TOPLINGDB_BUILD_TYPE}
CUSTOM_LSBM_PATH ?= /home/gjr/mylibs/lsbm_${LSBM_BUILD_TYPE}

# Database bindings
BIND_WIREDTIGER ?= 0
BIND_LEVELDB ?= 0
BIND_LSBM ?= 0
BIND_ROCKSDB ?= 0
BIND_ROCKSDB_LORC ?= 0
BIND_TERARKDB ?= 0
BIND_TOPLINGDB ?= 0
BIND_LMDB ?= 0
BIND_SQLITE ?= 0

# Extra options
DEBUG_BUILD ?=
EXTRA_CXXFLAGS ?=
EXTRA_LDFLAGS ?=

# HdrHistogram for tail latency report
BIND_HDRHISTOGRAM ?= 1
# Build and statically link library, submodule required
BUILD_HDRHISTOGRAM ?= 1

#----------------------------------------------------------

ifeq ($(DEBUG_BUILD), 1)
	CXXFLAGS += -g
else
	CXXFLAGS += -O2
	CPPFLAGS += -DNDEBUG
endif

ifeq ($(BIND_WIREDTIGER), 1)
	LDFLAGS += -lwiredtiger
	SOURCES += $(wildcard wiredtiger/*.cc)
endif

ifeq ($(BIND_LEVELDB), 1)
	LDFLAGS += -lleveldb
	SOURCES += $(wildcard leveldb/*.cc)
endif

ifeq ($(BIND_ROCKSDB), 1)
	LDFLAGS += -lrocksdb
	SOURCES += $(wildcard rocksdb/*.cc)
endif

ifeq ($(BIND_ROCKSDB_LORC), 1)
	CXXFLAGS += -I$(CUSTOM_ROCKSDB_LORC_PATH)/include
	LDFLAGS += -L$(CUSTOM_ROCKSDB_LORC_PATH)/lib -lrocksdb -Wl,-rpath,$(CUSTOM_ROCKSDB_LORC_PATH)/lib
    SOURCES += $(wildcard rocksdb_lorc/*.cc)
endif

ifeq ($(BIND_TERARKDB), 1)
	CXXFLAGS += -I$(CUSTOM_TERARKDB_PATH)/include
# LDFLAGS += -L$(CUSTOM_TERARKDB_PATH)/lib -lterarkdb -Wl,-rpath,$(CUSTOM_TERARKDB_PATH)/lib
    LDFLAGS += -L$(CUSTOM_TERARKDB_PATH)/lib \
               -Wl,-Bstatic \
               -lterarkdb -lbz2 -llz4 -lsnappy -lz -lzstd \
               -Wl,-Bdynamic \
               -pthread -lgomp -lrt -ldl -laio
	SOURCES += $(wildcard terarkdb/*.cc)
endif

ifeq ($(BIND_TOPLINGDB), 1)
	CXXFLAGS += -I$(CUSTOM_TOPLINGDB_PATH)/include
    LDFLAGS += -L$(CUSTOM_TOPLINGDB_PATH)/lib \
               -Wl,-Bstatic \
               -lrocksdb -lbz2 -llz4 -lsnappy -lz -lzstd \
               -Wl,-Bdynamic \
               -pthread -lgomp -lrt -ldl -laio
	SOURCES += $(wildcard toplingdb/*.cc)
endif

ifeq ($(BIND_LSBM), 1)
	CXXFLAGS += -I$(CUSTOM_LSBM_PATH)/include
	LDFLAGS += -L$(CUSTOM_LSBM_PATH)/lib -ldb_lsmcb -ldb_common -lport -ltable -lutil -Wl,-rpath,$(CUSTOM_LSBM_PATH)/lib
	SOURCES += $(wildcard lsbm/*.cc)
endif

ifeq ($(BIND_LMDB), 1)
	LDFLAGS += -llmdb
	SOURCES += $(wildcard lmdb/*.cc)
endif

ifeq ($(BIND_SQLITE), 1)
	LDFLAGS += -lsqlite3
	SOURCES += $(wildcard sqlite/*.cc)
endif

CXXFLAGS += -std=c++17 -Wall -pthread $(EXTRA_CXXFLAGS) -I./
LDFLAGS += $(EXTRA_LDFLAGS) -lpthread
SOURCES += $(wildcard core/*.cc)
OBJECTS += $(SOURCES:.cc=.o)
DEPS += $(SOURCES:.cc=.d)
EXEC = ycsb

HDRHISTOGRAM_DIR = HdrHistogram_c
HDRHISTOGRAM_LIB = $(HDRHISTOGRAM_DIR)/src/libhdr_histogram_static.a

ifeq ($(BIND_HDRHISTOGRAM), 1)
ifeq ($(BUILD_HDRHISTOGRAM), 1)
	CXXFLAGS += -I$(HDRHISTOGRAM_DIR)/include
	OBJECTS += $(HDRHISTOGRAM_LIB)
else
	LDFLAGS += -lhdr_histogram
endif
CPPFLAGS += -DHDRMEASUREMENT
endif

all: $(EXEC)

$(EXEC): $(OBJECTS)
	@$(CXX) $(CXXFLAGS) $^ $(LDFLAGS) -o $@
	@echo "  LD      " $@

.cc.o:
	@$(CXX) $(CXXFLAGS) $(CPPFLAGS) -c -o $@ $<
	@echo "  CC      " $@

%.d: %.cc
	@$(CXX) $(CXXFLAGS) $(CPPFLAGS) -MM -MT '$(<:.cc=.o)' -o $@ $<

$(HDRHISTOGRAM_DIR)/CMakeLists.txt:
	@echo "Download HdrHistogram_c"
	@git submodule update --init

$(HDRHISTOGRAM_DIR)/Makefile: $(HDRHISTOGRAM_DIR)/CMakeLists.txt
	@cmake -DCMAKE_BUILD_TYPE=Release -S $(HDRHISTOGRAM_DIR) -B $(HDRHISTOGRAM_DIR)


$(HDRHISTOGRAM_LIB): $(HDRHISTOGRAM_DIR)/Makefile
	@echo "Build HdrHistogram_c"
	@make -C $(HDRHISTOGRAM_DIR)

ifneq ($(MAKECMDGOALS),clean)
-include $(DEPS)
endif

clean:
	find . -name "*.[od]" -delete
	$(RM) $(EXEC)

.PHONY: clean
