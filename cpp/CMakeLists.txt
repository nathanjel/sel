# For projects that already use CMake. The library is two files and a vendored
# header; the Makefile beside this does the same job for everyone else.
#
#   add_subdirectory(path/to/sel/cpp)
#   target_link_libraries(myapp PRIVATE sel)

cmake_minimum_required(VERSION 3.20)
project(sel CXX)

add_library(sel sel.cpp)
target_compile_features(sel PUBLIC cxx_std_23)
# third_party/ is reached by a relative include from sel.cpp, so consumers need
# nothing on their include path beyond this directory.
target_include_directories(sel PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

# The harness. Not built by default: it is this repository's test rig, not part
# of what a consumer copies.
option(SEL_BUILD_TOOLS "Build the SEL CLI and test harness" OFF)

if(SEL_BUILD_TOOLS)
  foreach(tool sel conformance batch e2e)
    add_executable(sel-${tool} bin/${tool}.cpp)
    target_link_libraries(sel-${tool} PRIVATE sel)
  endforeach()

  # These include sel.cpp directly to reach the internals, so they must not also
  # link the library.
  add_executable(sel-check-decimal bin/check-decimal.cpp)
  target_compile_features(sel-check-decimal PRIVATE cxx_std_23)
  target_include_directories(sel-check-decimal PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

  add_executable(sel-unit tests/unit.cpp)
  target_compile_features(sel-unit PRIVATE cxx_std_23)
  target_include_directories(sel-unit PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

  enable_testing()
  add_test(NAME unit COMMAND sel-unit)
  add_test(NAME conformance COMMAND sel-conformance
           WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/..)
endif()
