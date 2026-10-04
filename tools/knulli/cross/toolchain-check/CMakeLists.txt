cmake_minimum_required(VERSION 3.16)
project(check_toolchain LANGUAGES CXX)
find_package(Boost REQUIRED COMPONENTS locale program_options date_time)
add_custom_command(
    OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/host-generated.h"
    COMMAND /usr/bin/g++ -std=c++17 "${CMAKE_CURRENT_SOURCE_DIR}/host-generator.cpp"
            -o "${CMAKE_CURRENT_BINARY_DIR}/host-generator"
    COMMAND "${CMAKE_CURRENT_BINARY_DIR}/host-generator" "${CMAKE_CURRENT_BINARY_DIR}/host-generated.h"
    DEPENDS host-generator.cpp
    VERBATIM
)
add_executable(check-toolchain main.cpp "${CMAKE_CURRENT_BINARY_DIR}/host-generated.h")
target_include_directories(check-toolchain PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
target_link_libraries(check-toolchain PRIVATE Boost::locale Boost::program_options Boost::date_time)
