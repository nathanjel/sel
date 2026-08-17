"""Conan 2 recipe for SEL, the C++23 implementation.

    conan create cpp/ --build=missing

SRELL is vendored under third_party/ and compiled into the library rather than
being a Conan dependency: SEL pins an exact SRELL commit on purpose, because the
regex engine is what makes the C++ host agree with the JavaScript one, and a
resolver picking a different version would quietly change matching behaviour.
See cpp/third_party/srell/PINNED.md.
"""

from conan import ConanFile
from conan.tools.cmake import CMake, CMakeToolchain, cmake_layout
from conan.tools.files import copy
import os


class SelConan(ConanFile):
    name = "sel-lang"
    version = "0.1.0"
    license = "MIT"
    author = "Marcin Gałczyński"
    url = "https://github.com/nathanjel/sel"
    homepage = "https://github.com/nathanjel/sel"
    description = (
        "A small expression language for validation rules that evaluate "
        "identically on PHP, JavaScript, C++ and Common Lisp"
    )
    topics = ("expression-language", "validation", "rules", "decimal", "interpreter")

    package_type = "static-library"
    settings = "os", "compiler", "build_type", "arch"
    options = {"fPIC": [True, False]}
    default_options = {"fPIC": True}

    exports_sources = "CMakeLists.txt", "sel.hpp", "sel.cpp", "third_party/*", "../LICENSE"

    def config_options(self):
        if self.settings.os == "Windows":
            del self.options.fPIC

    def validate(self):
        # The implementation is C++23; there is no fallback path.
        from conan.tools.build import check_min_cppstd
        check_min_cppstd(self, 23)

    def layout(self):
        cmake_layout(self)

    def generate(self):
        tc = CMakeToolchain(self)
        # The harness is this repository's test rig, not part of the package.
        tc.cache_variables["SEL_BUILD_TOOLS"] = False
        tc.generate()

    def build(self):
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def package(self):
        cmake = CMake(self)
        cmake.install()
        copy(self, "LICENSE",
             src=os.path.join(self.source_folder, ".."),
             dst=os.path.join(self.package_folder, "licenses"))
        copy(self, "LICENSE.txt",
             src=os.path.join(self.source_folder, "third_party", "srell"),
             dst=os.path.join(self.package_folder, "licenses", "srell"))

    def package_info(self):
        self.cpp_info.libs = ["sel-lang"]
        # Match the names the installed CMake package exports, so
        # find_package(sel-lang) and Conan's generated config agree.
        self.cpp_info.set_property("cmake_file_name", "sel-lang")
        self.cpp_info.set_property("cmake_target_name", "sel-lang::sel-lang")
