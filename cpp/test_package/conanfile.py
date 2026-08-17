"""Conan's own smoke test: `conan create` builds and runs this against the
package it just made.

It exists because the recipe shipped broken once. `exports_sources` cannot
reference a parent directory, so `../LICENSE` made `conan create` fail at the
export step — and nothing in tools/check.sh could have noticed, because Conan
was not installed when the recipe was written. This is the packaging equivalent
of the other harness layers: the check runs where the mistake can happen.
"""

from conan import ConanFile
from conan.tools.cmake import CMake, cmake_layout
from conan.tools.build import can_run
import os


class SelTestConan(ConanFile):
    settings = "os", "compiler", "build_type", "arch"
    generators = "CMakeDeps", "CMakeToolchain"

    def requirements(self):
        self.requires(self.tested_reference_str)

    def layout(self):
        cmake_layout(self)

    def build(self):
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def test(self):
        if can_run(self):
            self.run(os.path.join(self.cpp.build.bindir, "example"), env="conanrun")
