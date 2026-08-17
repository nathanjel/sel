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
    version = "0.1.3"
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
    # The library compiles as C++23 via target_compile_features in CMakeLists,
    # so it does not gate on the consumer's cppstd setting — a stock
    # `conan profile detect` produces gnu20, and refusing to build on that would
    # make the package unusable out of the box.
    settings = "os", "compiler", "build_type", "arch"
    options = {"fPIC": [True, False]}
    default_options = {"fPIC": True}

    def export_sources(self):
        # A method rather than the exports_sources attribute, because the licence
        # lives at the repository root and the attribute cannot reference a
        # parent directory ("copy() it is not possible to use relative patterns
        # starting with '..'"). It lands at the root of the source folder, which
        # is why CMakeLists.txt looks for it in both places.
        for pattern in ("CMakeLists.txt", "sel.hpp", "sel.cpp", "third_party/*"):
            copy(self, pattern, self.recipe_folder, self.export_sources_folder)
        copy(self, "LICENSE",
             os.path.join(self.recipe_folder, ".."), self.export_sources_folder)

    def config_options(self):
        if self.settings.os == "Windows":
            del self.options.fPIC

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
        copy(self, "LICENSE", src=self.source_folder,
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
