# SEL Go implementation
#
#   make          build all binaries into build/
#   make test     run unit tests and conformance suites
#   make clean    remove build/

GO ?= go
BUILD ?= build

BINARIES := conformance batch e2e sel api check-decimal sqlapi sqlreplay sqlt sqlfuzz mandelbrot scale-bench

TARGETS := $(addprefix $(BUILD)/,$(BINARIES))

.PHONY: all test clean $(BINARIES)

all: $(TARGETS)

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

test: all
	$(GO) test ./...
	./$(BUILD)/conformance ../conformance/*.selt
	./$(BUILD)/sqlt

clean:
	rm -rf $(BUILD)
