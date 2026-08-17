# SEL, C++23. No dependencies to fetch: SRELL is vendored under third_party/.
#
#   make          build everything into build/
#   make test     unit tests, then the conformance suite
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

BINS := $(addprefix $(BUILD)/,$(LINKED) $(WHITEBOX))

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

clean:
	rm -rf $(BUILD)
