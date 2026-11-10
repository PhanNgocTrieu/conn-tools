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
  src/transports/fd_stream.cpp
  src/transports/tcp.cpp
  src/transports/bt_common.cpp
  src/transports/packet_stream.cpp
  src/transports/rfcomm.cpp
  src/transports/l2cap.cpp
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

if(CTT_BUILD_TESTS)
  add_executable(ctt_unit_tests tests/unit_tests.cpp)
  target_link_libraries(ctt_unit_tests PRIVATE ctt_core)
  target_include_directories(ctt_unit_tests PRIVATE src/transports)
  add_test(NAME unit COMMAND ctt_unit_tests)
  add_test(NAME loopback
           COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/tests/loopback_test.sh $<TARGET_FILE:ctt-agent>)
endif()
