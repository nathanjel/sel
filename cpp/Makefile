# SEL, C++23. No dependencies to fetch: SRELL is vendored under third_party/.
#
#   make          build everything into build/
#   make test     unit tests, then the conformance suite
#   make asan     the same, plus the SQL suite and SQL unit, under the address,
#                 leak and undefined-behaviour sanitizers
#   make tsan     every thread-sanitizer probe: a shared Program, the SQL
#                 translator, the regex cache, registration beside compilation
#   make BUILD=build-usage build-usage/example-sql-star
#                 one database-backed example, which also needs libpq,
#                 libmariadb and libsqlite3 -- not part of `make` (DB_EXAMPLES)
#
# `make` and `make test` are what tools/check.sh builds and runs. `asan` and
# `tsan` are separate builds with their own objects (several minutes each);
# tools/check.sh says which of them it runs, and anything it does not run is a
# manual check to make before a release (docs/contributing.md).
#
# The drop-in is sel.hpp + sel.cpp + sel_ast.hpp + the four generated headers
# (sel_limits.hpp, sel_math_ops.hpp, sel_lexicon.hpp, sel_builtin_manifest.hpp) +
# third_party/srell/. Everything under bin/ and tests/ is this repository's
# harness, not part of what you copy. sel_ast.hpp and the generated headers are
# internal -- you include sel.hpp -- but sel.cpp needs them beside it.
#
# CMakeLists.txt builds the library (and, with SEL_BUILD_TOOLS, a subset of the
# harness for a consumer's own checks); this Makefile is the full harness.

CXX      ?= c++
CXXFLAGS ?= -std=c++23 -O2 -Wall -Wextra -Wpedantic -Wshadow
BUILD    := build
# Header dependencies are tracked by the compiler: every rule writes <target>.d
# beside its target, and the -include at the end of this file reads them, so an
# edited or regenerated header (sel_limits.hpp after spec/limits.json, say)
# rebuilds everything that includes it -- through sel.cpp too, for the tools
# that include the translation unit. The explicit prerequisites below are what
# a first build needs; the .d files are what keeps later ones honest.
DEPFLAGS  = -MMD -MP -MF $@.d

# Tools that use only the public interface, and link the library normally. This
# is also the check that sel.hpp is sufficient on its own.
LINKED := conformance batch e2e sel api
# The same, except that they also include sel_ast.hpp to walk the parse tree.
# Kept apart from LINKED so that list keeps meaning "sel.hpp alone is enough";
# this one means "a second translation unit can see the tree", which is what
# sel_ast.hpp exists for and what the SEL->SQL layer relies on.
INTERNAL := ast regex_verdict
# Whitebox tools, which include the translation unit to reach the decimal core
# and the other internals. They must not also link it.
WHITEBOX := check-decimal

# The SEL->SQL layer, one translation unit per stage. The dialect map's data
# (sel_sql_map_data.cpp) is generated from sql/dialects/*.json by
# tools/gen-sql-map.mjs and is entirely constexpr, so it contributes .rodata and
# no static constructor and --gc-sections drops all of it from a link that
# never asks for it.
SQL_SRCS := sel_sql.cpp sel_sql_binding.cpp sel_sql_emit.cpp sel_sql_map.cpp \
            sel_sql_map_data.cpp sel_sql_node.cpp sel_sql_stage1.cpp \
            sel_sql_translator.cpp sel_sql_hybrid.cpp
SQL_OBJS := $(addprefix $(BUILD)/,$(SQL_SRCS:.cpp=.o))
SQL_HDRS := sel_ast.hpp sel_sql.hpp sel_sql_emit.hpp sel_sql_map.hpp sel_sql_node.hpp \
            sel_sql_stage1.hpp sel_sql_translator.hpp
# Tools over that layer.
SQLTOOLS := sqlmap sqlfuzz sqlt sqlreplay sqlapi
SCALETOOLS := scale-bench

# The worked examples under ../examples/. They use the public interface only,
# exactly like LINKED, but they live outside this directory on purpose: they are
# the C++ member of a set whose whole promise is that every host prints the
# same thing, so they sit beside their siblings rather than under bin/.
# In the default target for the same reason `unit` is, below.
EXAMPLES := plain complex repl validation scripting
# The same, for an example that also uses the SEL->SQL layer and so needs
# $(SQL_OBJS) on the link line. A second list rather than a longer $(EXAMPLES),
# for the reason LINKED and INTERNAL are two lists: the plain example rule links
# $(BUILD)/sel.o and nothing else, which is what keeps "an example needs only
# sel.hpp and the evaluator" a checked claim instead of a comment. Folding sql
# into that rule would link the SQL objects into plain and complex too and
# quietly retire the check.
#
# memory-complex is here too: it plans for MariaDB and renders rows through
# ../examples/lib/db.hpp, but never connects, and render() is defined in that
# header -- so it links no database client, and saying so is this list's job.
SQL_EXAMPLES := sql dialect memory-complex
# The examples that talk to a real database (the categories with a LIVE marker).
# They link ../examples/lib/db.cpp and the three client libraries, which a
# development box is not expected to carry -- so they are NOT in the default
# target. tools/check-usage.sh builds them inside tools/usage.Dockerfile's image,
# into a directory of their own:
#
#   make BUILD=build-usage build-usage/example-sql-star
DB_EXAMPLES := sql-conditions sql-star sql-eav sql-3nf sql-flat sql-complex sql-functions
DB_LIBS     := -lpq -lmariadb -lsqlite3
EXAMPLE_BINS := $(addprefix $(BUILD)/example-,$(EXAMPLES)) \
                $(addprefix $(BUILD)/example-,$(SQL_EXAMPLES))

# `unit` is in the default target and not only in `test`, because it was not:
# `make` built everything else, tools/check.sh ran whatever build/unit happened
# to be lying there, and once tests/unit.cpp stopped compiling against sel.cpp a
# stale binary went on reporting 81 green checks for 27 commits.
BINS := $(addprefix $(BUILD)/,$(LINKED) $(INTERNAL) $(WHITEBOX) $(SQLTOOLS) $(SCALETOOLS) unit sqlunit) \
        $(EXAMPLE_BINS) $(BUILD)/metadata

.PHONY: all test clean
all: $(BINS)

$(BUILD)/metadata: ../tools/metadata/cpp.cpp sel.hpp $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -I. -o $@ $< $(BUILD)/sel.o

# What a build that includes or links the evaluator depends on, for the first
# build (later ones also read the .d files).
CORE_DEPS := sel.cpp sel.hpp sel_ast.hpp sel_limits.hpp sel_builtin_manifest.hpp sel_math_ops.hpp sel_lexicon.hpp

$(BUILD)/sel.o: $(CORE_DEPS) | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -c -o $@ sel.cpp

$(addprefix $(BUILD)/,$(LINKED) $(INTERNAL)): $(BUILD)/%: bin/%.cpp bin/read_file.hpp bin/show.hpp sel.hpp sel_ast.hpp $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $< $(BUILD)/sel.o

# `sel --version` prints the package version, read from the one place this
# directory states it (CMakeLists.txt's project() line, which
# tools/check-version.sh holds to the other manifests).
SEL_VERSION := $(shell grep -m1 -o 'sel-lang VERSION [0-9.]*' CMakeLists.txt | cut -d' ' -f3)
$(BUILD)/sel: private CXXFLAGS += -DSEL_VERSION='"$(SEL_VERSION)"'

$(addprefix $(BUILD)/,$(WHITEBOX)): $(BUILD)/%: bin/%.cpp bin/read_file.hpp $(CORE_DEPS) | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $<

$(addprefix $(BUILD)/example-,$(EXAMPLES)): $(BUILD)/example-%: ../examples/%/cpp.cpp sel.hpp $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $< $(BUILD)/sel.o

# sel_sql.hpp rather than $(SQL_HDRS): translating needs the public header
# alone, and listing the internal ones here would claim the examples reach past
# it as a matter of course. One does -- dialect registers a flavour, and the
# registry and the emitter are internal headers -- and it is still not missed,
# because $(SQL_OBJS) depend on all of them and this rule depends on those.
$(addprefix $(BUILD)/example-,$(SQL_EXAMPLES)): $(BUILD)/example-%: ../examples/%/cpp.cpp sel.hpp sel_sql.hpp $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $< $(SQL_OBJS) $(BUILD)/sel.o

# The one that also includes the database helper's header, for render().
$(BUILD)/example-memory-complex: ../examples/lib/db.hpp

# db.o is compiled on its own rather than on each example's line, so the six
# share one compile of the driver code -- and it lives in $(BUILD), so the
# image's build-usage/ never picks up an object from the host's compiler.
$(BUILD)/db.o: ../examples/lib/db.cpp ../examples/lib/db.hpp sel.hpp sel_sql.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -c -o $@ $<

$(addprefix $(BUILD)/example-,$(DB_EXAMPLES)): $(BUILD)/example-%: ../examples/%/cpp.cpp ../examples/lib/db.hpp sel.hpp sel_sql.hpp $(BUILD)/db.o $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $< $(BUILD)/db.o $(SQL_OBJS) $(BUILD)/sel.o $(DB_LIBS)

$(SQL_OBJS): $(BUILD)/%.o: %.cpp $(SQL_HDRS) sel.hpp sel_limits.hpp sel_builtin_manifest.hpp sel_lexicon.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -c -o $@ $<

$(addprefix $(BUILD)/,sqlmap sqlfuzz sqlapi): $(BUILD)/%: bin/%.cpp bin/read_file.hpp $(SQL_HDRS) sel.hpp $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $< $(SQL_OBJS) $(BUILD)/sel.o

# The SQL suite, plus the case table tools/gen-sql-cases.mjs writes. That table
# is source as far as this host is concerned -- it is committed, and a clone
# runs the suite without Node.
$(BUILD)/case_data.o: bin/case_data.cpp bin/case_data.hpp $(SQL_HDRS) sel.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -c -o $@ $<

$(BUILD)/map_replay.o: bin/map_replay.cpp bin/map_replay.hpp $(SQL_HDRS) sel.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -c -o $@ $<

$(BUILD)/sqlreplay: bin/sqlreplay.cpp bin/map_replay.hpp $(SQL_HDRS) sel.hpp $(BUILD)/map_replay.o $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $< $(BUILD)/map_replay.o $(SQL_OBJS) $(BUILD)/sel.o

$(BUILD)/sqlt: bin/sqlt.cpp bin/case_data.hpp $(SQL_HDRS) sel.hpp $(BUILD)/case_data.o $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $< $(BUILD)/case_data.o $(SQL_OBJS) $(BUILD)/sel.o

$(BUILD)/unit: tests/unit.cpp tests/harness.hpp $(CORE_DEPS) | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $<

$(BUILD)/sqlunit: tests/sql_unit.cpp $(SQL_HDRS) sel.hpp $(SQL_OBJS) $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $< $(SQL_OBJS) $(BUILD)/sel.o

# The scale runner includes sel.cpp directly because it also exercises the
# public evaluator and benchmark-only builtin registration in one translation
# unit; link only the SQL layer to avoid a duplicate evaluator definition.
$(BUILD)/scale-bench: ../tools/scale-test/sel_benchmarks.cpp $(CORE_DEPS) $(SQL_HDRS) $(SQL_OBJS) | $(BUILD)
	$(CXX) $(CXXFLAGS) $(DEPFLAGS) -o $@ $< $(SQL_OBJS)

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
	$(BUILD)/sqlunit
	cd .. && cpp/$(BUILD)/conformance

# Value is a reference-counted handle, and nothing behind it collects cycles:
# a value that contained itself would leak. It cannot, because every path that
# stores one value inside another clones first — but that is an invariant now
# rather than something the type guarantees, so it gets checked. LeakSanitizer
# reports a cycle as a leak, which is exactly the signal wanted here.
#
# The SQL layer and the planner run under the same sanitizers: the SQL case
# suite (stage 1, the translator, the planner over every committed case) and
# the SQL unit binary (executed plans, the shared physical tree). Until they
# did, a leak or an out-of-bounds in the translator was caught by nothing in
# the tree. The sanitized objects live under build/asan/ so the two SQL
# binaries share one compile of the core.
ASAN_FLAGS := -std=c++23 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer
ASAN_DIR   := $(BUILD)/asan
ASAN_SQL_OBJS := $(addprefix $(ASAN_DIR)/,$(SQL_SRCS:.cpp=.o))

$(ASAN_DIR):
	mkdir -p $(ASAN_DIR)

$(ASAN_DIR)/sel.o: $(CORE_DEPS) | $(ASAN_DIR)
	$(CXX) $(ASAN_FLAGS) $(DEPFLAGS) -c -o $@ $<

$(ASAN_SQL_OBJS): $(ASAN_DIR)/%.o: %.cpp $(SQL_HDRS) sel.hpp sel_limits.hpp sel_builtin_manifest.hpp sel_lexicon.hpp | $(ASAN_DIR)
	$(CXX) $(ASAN_FLAGS) $(DEPFLAGS) -c -o $@ $<

$(ASAN_DIR)/case_data.o: bin/case_data.cpp bin/case_data.hpp $(SQL_HDRS) sel.hpp | $(ASAN_DIR)
	$(CXX) $(ASAN_FLAGS) $(DEPFLAGS) -c -o $@ $<

# unit-asan includes the translation unit (whitebox), so it compiles sel.cpp
# itself; the other three link the shared sanitized object.
$(BUILD)/unit-asan: tests/unit.cpp tests/harness.hpp $(CORE_DEPS) | $(BUILD)
	$(CXX) $(ASAN_FLAGS) $(DEPFLAGS) -o $@ $<

$(BUILD)/conformance-asan: bin/conformance.cpp bin/read_file.hpp sel.hpp $(ASAN_DIR)/sel.o | $(BUILD)
	$(CXX) $(ASAN_FLAGS) $(DEPFLAGS) -o $@ $< $(ASAN_DIR)/sel.o

$(BUILD)/sqlunit-asan: tests/sql_unit.cpp $(SQL_HDRS) sel.hpp $(ASAN_SQL_OBJS) $(ASAN_DIR)/sel.o | $(BUILD)
	$(CXX) $(ASAN_FLAGS) $(DEPFLAGS) -o $@ $< $(ASAN_SQL_OBJS) $(ASAN_DIR)/sel.o

$(BUILD)/sqlt-asan: bin/sqlt.cpp bin/case_data.hpp $(SQL_HDRS) sel.hpp $(ASAN_DIR)/case_data.o $(ASAN_SQL_OBJS) $(ASAN_DIR)/sel.o | $(BUILD)
	$(CXX) $(ASAN_FLAGS) $(DEPFLAGS) -o $@ $< $(ASAN_DIR)/case_data.o $(ASAN_SQL_OBJS) $(ASAN_DIR)/sel.o

.PHONY: asan
# ASan checks the whole byte range a libc call may read, and for memmem that is the
# remaining haystack on every call: a linear search loop becomes quadratic under
# the sanitizer alone (REPLACE over 8M code points, conformance/31's cap cases:
# 25+ minutes instead of seconds; the -O2 build is linear). Every read the
# interpreter itself makes is still checked -- only memmem's up-front range check
# is off. The conformance files run side by side, ASAN_JOBS at a time, each with
# its own output; any failing file fails the target.
ASAN_JOBS ?= $(shell nproc 2>/dev/null || echo 4)
ASAN_ENV  := ASAN_OPTIONS=intercept_memmem=0$(if $(ASAN_OPTIONS),:$(ASAN_OPTIONS))

asan: $(BUILD)/unit-asan $(BUILD)/conformance-asan $(BUILD)/sqlunit-asan $(BUILD)/sqlt-asan
	$(ASAN_ENV) $(BUILD)/unit-asan
	cd .. && ls conformance/*.selt | $(ASAN_ENV) xargs -P $(ASAN_JOBS) -I{} sh -c \
	  'out=$$(cpp/$(BUILD)/conformance-asan {} 2>&1); rc=$$?; \
	   printf "%s: %s\n" {} "$$(printf "%s\n" "$$out" | tail -n 1)"; \
	   if [ $$rc -ne 0 ]; then printf "%s\n" "$$out" | grep -E "^(FAIL|SUITE)|ERROR: |SUMMARY: " | head -n 40; exit 1; fi'
	$(ASAN_ENV) $(BUILD)/sqlunit-asan
	cd .. && $(ASAN_ENV) cpp/$(BUILD)/sqlt-asan

# The thread sanitizer, for the thread promises sel.hpp and sel_sql.hpp make: a
# compiled Program shared between threads that each have their own context
# (program_race), translate() on several threads once the map is registered
# (sql_race), the bounded regex cache under eviction (regex_race), and
# register_function() beside compile() and run() (registry_race). It has its
# own object directory: TSan and ASan cannot share objects.
TSAN_FLAGS := -std=c++23 -O1 -g -fsanitize=thread -fno-omit-frame-pointer
TSAN_DIR   := $(BUILD)/tsan
TSAN_SRCS  := sel.cpp $(SQL_SRCS)
TSAN_OBJS  := $(addprefix $(TSAN_DIR)/,$(TSAN_SRCS:.cpp=.o))
TSAN_PROBES := program-race sql-race regex-race registry-race

$(TSAN_DIR):
	mkdir -p $(TSAN_DIR)

$(TSAN_OBJS): $(TSAN_DIR)/%.o: %.cpp $(CORE_DEPS) $(SQL_HDRS) | $(TSAN_DIR)
	$(CXX) $(TSAN_FLAGS) $(DEPFLAGS) -c -o $@ $<

$(addprefix $(BUILD)/,$(addsuffix -tsan,$(TSAN_PROBES))): $(BUILD)/%-tsan: $(TSAN_OBJS) | $(BUILD)
	$(CXX) $(TSAN_FLAGS) $(DEPFLAGS) -pthread -o $@ tests/$(subst -,_,$*).cpp $(TSAN_OBJS)

# Each probe's source, as a prerequisite of its binary (the pattern rule above
# cannot spell the hyphen-to-underscore mapping in a prerequisite).
$(BUILD)/program-race-tsan: tests/program_race.cpp
$(BUILD)/sql-race-tsan: tests/sql_race.cpp
$(BUILD)/regex-race-tsan: tests/regex_race.cpp
$(BUILD)/registry-race-tsan: tests/registry_race.cpp

# Every probe; any report or wrong answer fails the target.
.PHONY: tsan tsan-registry tsan-regex tsan-sql tsan-program
tsan: $(addprefix $(BUILD)/,$(addsuffix -tsan,$(TSAN_PROBES)))
	$(BUILD)/program-race-tsan
	$(BUILD)/sql-race-tsan
	$(BUILD)/regex-race-tsan
	$(BUILD)/registry-race-tsan

# One probe at a time, for iterating on one of them.
tsan-program: $(BUILD)/program-race-tsan
	$(BUILD)/program-race-tsan
tsan-sql: $(BUILD)/sql-race-tsan
	$(BUILD)/sql-race-tsan
tsan-regex: $(BUILD)/regex-race-tsan
	$(BUILD)/regex-race-tsan
tsan-registry: $(BUILD)/registry-race-tsan
	$(BUILD)/registry-race-tsan

clean:
	rm -rf $(BUILD)

# The compiler-written header dependencies (DEPFLAGS above).
-include $(wildcard $(BUILD)/*.d $(BUILD)/asan/*.d $(BUILD)/tsan/*.d)
