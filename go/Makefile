# SEL Go implementation
#
#   make          build all binaries into build/, the worked examples included
#   make examples build only the worked examples (build/example-<category>)
#   make test     run unit tests and conformance suites
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

.PHONY: all examples test clean $(BINARIES)

all: $(TARGETS) $(EXAMPLES)

examples: $(EXAMPLES)

$(BUILD)/example-%: $(EXAMPLE_SOURCES)
	@mkdir -p $(BUILD)
	cd ../examples && $(GO) build -o $(abspath $(BUILD))/example-$* ./$*/go.go

$(BUILD)/conformance: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/conformance

$(BUILD)/batch: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/batch

$(BUILD)/e2e: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/e2e

$(BUILD)/sel: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/sel

$(BUILD)/api: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/api

$(BUILD)/check-decimal: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/check-decimal

$(BUILD)/sqlapi: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/sqlapi

$(BUILD)/sqlreplay: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/sqlreplay

$(BUILD)/sqlt: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/sqlt

$(BUILD)/sqlfuzz: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/sqlfuzz

$(BUILD)/mandelbrot: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/mandelbrot

$(BUILD)/scale-bench: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/scale-bench

$(BUILD)/hybridparity: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/hybridparity

$(BUILD)/regexverdict: $(shell find . -name '*.go' -not -path './build/*')
	@mkdir -p $(BUILD)
	$(GO) build -o $@ ./bin/regexverdict

test: all
	$(GO) test -race ./...
	./$(BUILD)/conformance ../conformance/*.selt
	./$(BUILD)/sqlt

clean:
	rm -rf $(BUILD)
