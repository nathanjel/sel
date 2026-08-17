cmake_minimum_required(VERSION 3.20)
project(sel_test_package CXX)

find_package(sel-lang REQUIRED CONFIG)

add_executable(example example.cpp)
target_link_libraries(example PRIVATE sel-lang::sel-lang)
