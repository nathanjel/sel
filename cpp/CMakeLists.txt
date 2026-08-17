# SEL — C++23.
#
# Three ways to consume this, in increasing order of ceremony:
#
#   1. Copy sel.hpp, sel.cpp and third_party/srell/ into your project and
#      compile sel.cpp. Nothing here is needed. This remains the primary story.
#   2. add_subdirectory(path/to/sel/cpp) and link `sel-lang::sel-lang`.
#   3. Install it (or take it from Conan or vcpkg) and find_package(sel-lang).
#
# SRELL is vendored and compiled *into* the library; sel.hpp does not include it,
# so consumers need nothing on their include path but sel.hpp itself.

cmake_minimum_required(VERSION 3.20)
project(sel-lang VERSION 0.1.4 LANGUAGES CXX
        DESCRIPTION "A small expression language that evaluates identically on every host"
        HOMEPAGE_URL "https://github.com/nathanjel/sel")

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

# SRELL is vendored by default. It is available in vcpkg at exactly the pinned
# release (2026.05) and not in Conan Center at all, so the copy is what makes the
# package build the same way everywhere — and what keeps "copy two files and
# compile" true. Turn this on to link a package manager's copy instead; it must
# be the same release, and tools/check.sh is what tells you it is.
option(SEL_USE_SYSTEM_SRELL "Use an external SRELL instead of the vendored one" OFF)

add_library(sel-lang sel.cpp)
# The package is `sel-lang` everywhere it is published, so the exported target
# matches. `sel::sel` is kept as a shorter alias for in-tree use.
add_library(sel-lang::sel-lang ALIAS sel-lang)
add_library(sel::sel ALIAS sel-lang)

target_compile_features(sel-lang PUBLIC cxx_std_23)

if(SEL_USE_SYSTEM_SRELL)
  find_package(srell CONFIG REQUIRED)
  target_compile_definitions(sel-lang PRIVATE SEL_SYSTEM_SRELL)
  target_link_libraries(sel-lang PRIVATE srell::srell)
endif()
set_target_properties(sel-lang PROPERTIES
  VERSION ${PROJECT_VERSION}
  SOVERSION ${PROJECT_VERSION_MAJOR}
  PUBLIC_HEADER sel.hpp)

# The build tree needs this directory so sel.cpp can reach third_party/srell/.
# Consumers get only the installed include directory.
target_include_directories(sel-lang
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)

# --- install ------------------------------------------------------------------

install(TARGETS sel-lang
        EXPORT sel-langTargets
        LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
        ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
        PUBLIC_HEADER DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

install(EXPORT sel-langTargets
        FILE sel-langTargets.cmake
        NAMESPACE sel-lang::
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/sel-lang)

write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/sel-langConfigVersion.cmake"
  VERSION ${PROJECT_VERSION}
  COMPATIBILITY SameMajorVersion)

file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/sel-langConfig.cmake"
"include(\"\${CMAKE_CURRENT_LIST_DIR}/sel-langTargets.cmake\")
")

install(FILES
  "${CMAKE_CURRENT_BINARY_DIR}/sel-langConfig.cmake"
  "${CMAKE_CURRENT_BINARY_DIR}/sel-langConfigVersion.cmake"
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/sel-lang)

# At the repository root normally; at this directory's root when a package
# manager has exported just cpp/ plus the licence.
foreach(candidate "${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE" "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE")
  if(EXISTS "${candidate}")
    install(FILES "${candidate}" DESTINATION ${CMAKE_INSTALL_DATAROOTDIR}/licenses/sel-lang)
    break()
  endif()
endforeach()

# --- harness ------------------------------------------------------------------
#
# Not built by default: this is the repository's test rig, not part of what a
# consumer installs.

option(SEL_BUILD_TOOLS "Build the SEL CLI and test harness" OFF)

if(SEL_BUILD_TOOLS)
  foreach(tool sel conformance batch e2e api)
    add_executable(sel-${tool} bin/${tool}.cpp)
    target_link_libraries(sel-${tool} PRIVATE sel-lang)
  endforeach()

  # These include sel.cpp directly to reach the internals, so they must not also
  # link the library.
  foreach(whitebox check-decimal)
    add_executable(sel-${whitebox} bin/${whitebox}.cpp)
    target_compile_features(sel-${whitebox} PRIVATE cxx_std_23)
    target_include_directories(sel-${whitebox} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
  endforeach()

  add_executable(sel-unit tests/unit.cpp)
  target_compile_features(sel-unit PRIVATE cxx_std_23)
  target_include_directories(sel-unit PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

  enable_testing()
  add_test(NAME unit COMMAND sel-unit)
  add_test(NAME conformance COMMAND sel-conformance
           WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/..)
endif()
