cmake_minimum_required(VERSION 3.13)
project({{NAME}} C)

add_executable({{BINARY}} main.c)
target_compile_options({{BINARY}} PRIVATE -O2 -Wall -Wextra)