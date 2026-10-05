# SEL Go implementation
#
#   make          build all binaries into build/, the worked examples included
#   make examples build only the worked examples (build/example-<category>)
#   make test     run the unit tests, the conformance and SQL suites, and the examples
#   make clean    remove build/

GO ?= go
BUILD ?= build

BINARIES := conformance batch e2e sel api check-decimal sqlapi sqlreplay sqlt sqlfuzz mandelbrot scale-bench hybridparity regexverdict

TARGETS := $(addprefix $(BUILD)/,$(BINARIES))

# The worked examples (../examples/<category>/go.go) that need only the host:
# every category without a LIVE, REFERENCE or LIBRARY marker, as
# tools/check-examples.sh runs them. They are a module of their own
# (../examples/go.mod), built by file name because each directory also holds the
# other hosts' versions -- a cpp.cpp beside go.go would make Go take the
# directory for a cgo package. Each binary depends on every Go file there, so an
# edit to any example (a LIVE one included, which tools/check-usage.sh builds)
# rebuilds them and the freshness check in tools/impls.sh stays honest.
EXAMPLE_DIRS := $(shell for d in ../examples/*/; do \
	[ -f $$d/go.go ] && [ ! -f $$d/LIVE ] && [ ! -f $$d/REFERENCE ] && [ ! -f $$d/LIBRARY ] && basename $$d; done)
EXAMPLES := $(addprefix $(BUILD)/example-,$(EXAMPLE_DIRS))
EXAMPLE_SOURCES := $(shell find . ../examples -name '*.go' -not -path './build/*') ../examples/go.mod

SOURCES := $(shell find . -name '*.go' -not -path './build/*')

.PHONY: all examples test clean

all: $(TARGETS) $(EXAMPLES)

examples: $(EXAMPLES)

$(BUILD)/example-%: $(EXAMPLE_SOURCES)
	@mkdir -p $(BUILD)
	cd ../examples && $(GO) build -o $(abspath $(BUILD))/example-$* ./$*/go.go

# Every binary is built the same way from its directory under bin/.
$(TARGETS): $(BUILD)/%: $(SOURCES)
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/$*

# The examples print exactly their output.txt (as tools/check-examples.sh holds
# every host to), run from the repository root with its session.txt as stdin,
# as that script runs them.
test: all
	$(GO) test -race ./...
	./$(BUILD)/conformance ../conformance/*.selt
	./$(BUILD)/sqlt
	@for d in $(EXAMPLE_DIRS); do \
	  in=/dev/null; [ -f ../examples/$$d/session.txt ] && in=examples/$$d/session.txt; \
	  (cd .. && go/$(BUILD)/example-$$d < $$in | cmp -s - examples/$$d/output.txt) \
	    || { echo "FAIL example $$d: output differs from examples/$$d/output.txt"; exit 1; }; \
	done; echo "examples: $(words $(EXAMPLE_DIRS)) print their output.txt"

clean:
	rm -rf $(BUILD)
