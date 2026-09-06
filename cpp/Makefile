# SEL, C++23. No dependencies to fetch: SRELL is vendored under third_party/.
#
#   make          build everything into build/
#   make test     unit tests, then the conformance suite
#   make asan     the same, with the address and leak sanitizers
#
# The drop-in is sel.hpp + sel.cpp + third_party/srell/. Everything under bin/
# and tests/ is this repository's harness, not part of what you copy.

CXX      ?= c++
CXXFLAGS ?= -std=c++23 -O2 -Wall -Wextra -Wpedantic
BUILD    := build

# Tools that use only the public interface, and link the library normally. This
# is also the check that sel.hpp is sufficient on its own.
LINKED := conformance batch e2e sel api
# Whitebox tools, which include the translation unit to reach the decimal core
# and the other internals. They must not also link it.
WHITEBOX := check-decimal

# `unit` is in the default target and not only in `test`, because it was not:
# `make` built everything else, tools/check.sh ran whatever build/unit happened
# to be lying there, and once tests/unit.cpp stopped compiling against sel.cpp a
# stale binary went on reporting 81 green checks for 27 commits.
BINS := $(addprefix $(BUILD)/,$(LINKED) $(WHITEBOX) unit)

.PHONY: all test clean
all: $(BINS)

$(BUILD)/sel.o: sel.cpp sel.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c -o $@ sel.cpp

$(addprefix $(BUILD)/,$(LINKED)): $(BUILD)/%: bin/%.cpp sel.hpp $(BUILD)/sel.o | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $< $(BUILD)/sel.o

$(addprefix $(BUILD)/,$(WHITEBOX)): $(BUILD)/%: bin/%.cpp sel.cpp sel.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $<

$(BUILD)/unit: tests/unit.cpp sel.cpp sel.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $<

$(BUILD):
	mkdir -p $(BUILD)

# Run from the repository root, where conformance/ lives.
test: $(BUILD)/unit $(BUILD)/conformance
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
