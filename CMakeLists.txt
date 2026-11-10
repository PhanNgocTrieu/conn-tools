cmake_minimum_required(VERSION 3.16)
project(connectivity-tools VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(CTT_BUILD_TESTS "Build unit tests" ON)
option(CTT_STATIC "Link ctt-agent statically (portable binary for the DUT)" OFF)

if(CTT_BUILD_TESTS)
  enable_testing()
endif()

add_subdirectory(agent)
