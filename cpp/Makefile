# SEL, C++23. No dependencies to fetch: SRELL is vendored under third_party/.
#
#   make          build everything into build/
#   make test     unit tests, then the conformance suite
#   make asan     the same, with the address and leak sanitizers
#
# The drop-in is sel.hpp + sel.cpp + sel_ast.hpp + third_party/srell/. Everything
# under bin/ and tests/ is this repository's harness, not part of what you copy.
# sel_ast.hpp is internal -- you include sel.hpp -- but sel.cpp needs it beside it.

CXX      ?= c++
CXXFLAGS ?= -std=c++23 -O2 -Wall -Wextra -Wpedantic
BUILD    := build

# Tools that use only the public interface, and link the library normally. This
# is also the check that sel.hpp is sufficient on its own.
LINKED := conformance batch e2e sel api
# The same, except that they also include sel_ast.hpp to walk the parse tree.
# Kept apart from LINKED so that list keeps meaning "sel.hpp alone is enough";
# this one means "a second translation unit can see the tree", which is what
# sel_ast.hpp exists for and what the SEL->SQL layer will need.
INTERNAL := ast
# Whitebox tools, which include the translation unit to reach the decimal core
# and the other internals. They must not also link it.
WHITEBOX := check-decimal

# The SEL->SQL layer. Three translation units: the error type, the dialect
# map's registry, and the map itself -- which is generated from
# sql/dialects/*.json by tools/gen-sql-map.mjs and is entirely constexpr, so it
# contributes .rodata and no static constructor and --gc-sections drops all of
# it from a link that never asks for it.
SQL_SRCS := sel_sql.cpp sel_sql_binding.cpp sel_sql_emit.cpp sel_sql_map.cpp \
            sel_sql_map_data.cpp sel_sql_node.cpp sel_sql_stage1.cpp \
            sel_sql_translator.cpp
SQL_OBJS := $(addprefix $(BUILD)/,$(SQL_SRCS:.cpp=.o))
SQL_HDRS := sel_sql.hpp sel_sql_emit.hpp sel_sql_map.hpp sel_sql_node.hpp \
            sel_sql_stage1.hpp sel_sql_translator.hpp
# Tools over that layer.
SQLTOOLS := sqlmap sqlfuzz sqlt sqlreplay

# `unit` is in the default target and not only in `test`, because it was not:
# `make` built everything else, tools/check.sh ran whatever build/unit happened
# to be lying there, and once tests/unit.cpp stopped compiling against sel.cpp a
# stale binary went on reporting 81 green checks for 27 commits.
BINS := $(addprefix $(BUILD)/,$(LINKED) $(INTERNAL) $(WHITEBOX) $(SQLTOOLS) unit)

.PHONY: all test clean
all: $(BINS)

$(BUILD)/sel.o: sel.cpp sel.hpp sel_ast.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c -o $@ sel.cpp

$(addprefix $(BUILD)/,$(LINKED) $(INTERNAL)): $(BUILD)/%: bin/%.cpp sel.hpp sel_ast.hpp $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $< $(BUILD)/sel.o

$(addprefix $(BUILD)/,$(WHITEBOX)): $(BUILD)/%: bin/%.cpp sel.cpp sel.hpp sel_ast.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $<

$(SQL_OBJS): $(BUILD)/%.o: %.cpp $(SQL_HDRS) sel.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(addprefix $(BUILD)/,sqlmap sqlfuzz): $(BUILD)/%: bin/%.cpp $(SQL_HDRS) sel.hpp $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $< $(SQL_OBJS) $(BUILD)/sel.o

# The SQL suite, plus the case table tools/gen-sql-cases.mjs writes. That table
# is source as far as this host is concerned -- it is committed, and a clone
# runs the suite without Node.
$(BUILD)/case_data.o: bin/case_data.cpp bin/case_data.hpp $(SQL_HDRS) sel.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(BUILD)/map_replay.o: bin/map_replay.cpp bin/map_replay.hpp $(SQL_HDRS) sel.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(BUILD)/sqlreplay: bin/sqlreplay.cpp bin/map_replay.hpp $(SQL_HDRS) sel.hpp $(BUILD)/map_replay.o $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $< $(BUILD)/map_replay.o $(SQL_OBJS) $(BUILD)/sel.o

$(BUILD)/sqlt: bin/sqlt.cpp bin/case_data.hpp $(SQL_HDRS) sel.hpp $(BUILD)/case_data.o $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $< $(BUILD)/case_data.o $(SQL_OBJS) $(BUILD)/sel.o

$(BUILD)/unit: tests/unit.cpp sel.cpp sel.hpp sel_ast.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $<

$(BUILD):
	mkdir -p $(BUILD)

# Run from the repository root, where conformance/ lives.
#
# Depends on everything, not just the two it runs. tools/impls.sh gates all of
# C++'s availability on build/conformance alone, so a tree where only `make test`
# had been run reported C++ as available and then failed in whichever lane
# reached build/api, build/batch, build/e2e or build/check-decimal first.
test: $(BINS)
	$(BUILD)/unit
	cd .. && cpp/$(BUILD)/conformance

# Value is a handle over a shared_ptr, and nothing behind that collects cycles:
# a value that contained itself would leak. It cannot, because every path that
# stores one value inside another clones first — but that is an invariant now
# rather than something the type guarantees, so it gets checked. LeakSanitizer
# reports a cycle as a leak, which is exactly the signal wanted here.
.PHONY: asan
asan: | $(BUILD)
	$(CXX) -std=c++23 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
	  -o $(BUILD)/unit-asan tests/unit.cpp
	$(CXX) -std=c++23 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
	  -o $(BUILD)/conformance-asan bin/conformance.cpp sel.cpp
	$(BUILD)/unit-asan
	cd .. && cpp/$(BUILD)/conformance-asan

clean:
	rm -rf $(BUILD)
