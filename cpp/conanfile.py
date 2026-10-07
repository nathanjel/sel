"""Conan 2 recipe for SEL, the C++23 implementation.

    conan create cpp/ --build=missing

SRELL is vendored under third_party/ and compiled into the library rather than
being a Conan dependency: SEL pins an exact SRELL commit on purpose, because the
regex engine is what makes the C++ host agree with the JavaScript one, and a
resolver picking a different version would quietly change matching behaviour.
See cpp/third_party/srell/PINNED.md.
"""

from conan import ConanFile
from conan.errors import ConanInvalidConfiguration
from conan.tools.cmake import CMake, CMakeToolchain, cmake_layout
from conan.tools.files import copy
import os


class SelConan(ConanFile):
    name = "sel-lang"
    version = "0.10.2"
    license = "MIT"
    author = "Marcin Gałczyński"
    url = "https://github.com/nathanjel/sel"
    homepage = "https://github.com/nathanjel/sel"
    description = (
        "A small expression language for business rules: one rule gives the "
        "same answer in Python, JavaScript, PHP, C++23, Common Lisp, Rust and "
        "Go, or in your database as SQL. Exact decimal arithmetic, no "
        "floating point, no truthiness."
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
        # Every file the build includes: the generated headers were missing
        # until 0.9.1, and the package did not compile.
        # tools/check-cpp-package.sh builds from exactly this list.
        for pattern in ("CMakeLists.txt", "sel.hpp", "sel_ast.hpp", "sel_limits.hpp",
                        "sel_math_ops.hpp", "sel_lexicon.hpp", "sel_builtin_manifest.hpp", "sel.cpp",
                        "sel_sql*.hpp", "sel_sql*.cpp", "third_party/*"):
            copy(self, pattern, self.recipe_folder, self.export_sources_folder)
        copy(self, "LICENSE",
             os.path.join(self.recipe_folder, ".."), self.export_sources_folder)

    def config_options(self):
        if self.settings.os == "Windows":
            del self.options.fPIC

    def validate(self):
        # The decimal core needs __int128, the __builtin_* overflow checks and
        # GNU inline assembly: GCC and Clang (MinGW included), not MSVC.
        if str(self.settings.compiler) == "msvc":
            raise ConanInvalidConfiguration("sel-lang needs GCC or Clang; MSVC is not supported")

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
