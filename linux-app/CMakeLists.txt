# ctt-agent (C++): Linux x86_64 and aarch64 boards. Built by scripts/build.sh.
cmake_minimum_required(VERSION 3.16)
project(connectivity-tools VERSION 3.0.0 LANGUAGES CXX)

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

# ---------------------------------------------------------------- ctt-agent
find_package(Threads REQUIRED)

configure_file(include/ctt/version.hpp.in ${CMAKE_CURRENT_BINARY_DIR}/gen/ctt/version.hpp @ONLY)

add_library(ctt_core STATIC
  src/crc32.cpp
  src/ctp.cpp
  src/pattern.cpp
  src/framing.cpp
  src/json.cpp
  src/output.cpp
  src/metrics.cpp
  src/options.cpp
  src/session_common.cpp
  src/server.cpp
  src/client.cpp
  src/i2c.cpp
  src/i2c_cmd.cpp
  src/storage_cmd.cpp
  src/pipe_cmd.cpp
  src/dbus.cpp
  src/transports/fd_stream.cpp
  src/transports/tcp.cpp
  src/transports/net_common.cpp
  src/transports/udp.cpp
  src/transports/usb.cpp
  src/transports/usb_desc.cpp
  src/transports/bt_common.cpp
  src/transports/packet_stream.cpp
  src/transports/rfcomm.cpp
  src/transports/l2cap.cpp
  src/transports/gatt.cpp
  src/transports/unix.cpp
  src/transports/registry.cpp
)
target_include_directories(ctt_core
  PUBLIC include ${CMAKE_CURRENT_BINARY_DIR}/gen
  PRIVATE src/transports)
target_compile_options(ctt_core PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(ctt_core PUBLIC Threads::Threads)

add_executable(ctt-agent src/main.cpp)
target_link_libraries(ctt-agent PRIVATE ctt_core)
target_compile_options(ctt-agent PRIVATE -Wall -Wextra -Wpedantic)
if(CTT_STATIC)
  target_link_options(ctt-agent PRIVATE -static)
endif()
install(TARGETS ctt-agent RUNTIME DESTINATION bin)
install(PROGRAMS ${CMAKE_CURRENT_SOURCE_DIR}/scripts/dut-usb-gadget.sh DESTINATION bin RENAME ctt-usb-gadget)

if(CTT_BUILD_TESTS)
  set(CTT_AGENT_TESTS ${CMAKE_CURRENT_SOURCE_DIR}/tests)
  add_executable(ctt_unit_tests ${CTT_AGENT_TESTS}/unit_tests.cpp)
  target_link_libraries(ctt_unit_tests PRIVATE ctt_core)
  target_include_directories(ctt_unit_tests PRIVATE src/transports)
  add_test(NAME unit COMMAND ctt_unit_tests)
  add_test(NAME loopback
           COMMAND ${CTT_AGENT_TESTS}/loopback_test.sh $<TARGET_FILE:ctt-agent>)
endif()

# Python tests of window-app/ (use the ctt-agent built here)
if(CTT_BUILD_TESTS AND NOT CMAKE_CROSSCOMPILING)
  find_package(Python3 COMPONENTS Interpreter)
  if(Python3_Interpreter_FOUND)
    add_test(NAME python
             COMMAND ${Python3_EXECUTABLE} -m unittest discover -s window-app/tests
             WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}/..)
    set_tests_properties(python PROPERTIES
             ENVIRONMENT "CTT_AGENT=$<TARGET_FILE:ctt-agent>;CTT_BOARD_PROFILE=none" TIMEOUT 600)
  endif()
endif()
